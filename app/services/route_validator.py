"""Validación "¿el colectivo sigue dentro de su trazado legal?".

Dos implementaciones equivalentes:

1. `RouteValidator` (Shapely, en memoria) -> la usa el endpoint de alta frecuencia.
   Los trazados de una comuna pesan pocos KB, así que cada worker de uvicorn los mantiene
   proyectados a metros y responde en microsegundos sin ir a la base de datos.

2. `distance_to_route_postgis` (PostGIS) -> fuente de verdad para batch/auditoría,
   o para cuando los trazados no quepan en memoria (escala nacional).

Qué cuenta como "en ruta": trazado + vías alternativas + bucles, en ambos sentidos. Los
trazados nocturnos solo cuentan dentro del horario nocturno (`night_hours`).
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import shapely
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Route, RouteKind

SANTIAGO_TZ = ZoneInfo("America/Santiago")


@dataclass(frozen=True, slots=True)
class RouteCheck:
    on_route: bool
    distance_m: float


class RouteValidator:
    def __init__(
        self, buffer_m: float, metric_crs: str = "EPSG:32719", night_hours: tuple[int, int] = (22, 6)
    ) -> None:
        self.buffer_m = buffer_m
        self.night_hours = night_hours  # [inicio, fin) en hora de Santiago; puede cruzar medianoche
        # GeoJSON viene en grados (EPSG:4326). Medir distancias en grados es incorrecto
        # (1° de longitud ≠ 1° de latitud), así que proyectamos a UTM 19S, donde 1 unidad = 1 metro.
        # always_xy=True => orden (lon, lat), igual que GeoJSON.
        self._to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
        self._day: dict[str, BaseGeometry] = {}
        self._night: dict[str, BaseGeometry] = {}

    def load(self, routes: Iterable[tuple[str, BaseGeometry, RouteKind]]) -> None:
        """Recibe (line_code, geometría WGS84, tipo) y reemplaza atómicamente el caché.

        Todos los tramos de una línea (ida, regreso, alternativas, bucles) se unen en un solo
        corredor: para validar "desvío" basta con estar cerca de cualquiera de ellos.
        """
        day: dict[str, list[BaseGeometry]] = defaultdict(list)
        night: dict[str, list[BaseGeometry]] = defaultdict(list)
        for line_code, geom, kind in routes:
            projected = shapely.transform(geom, self._to_metric.transform, interleaved=False)
            night[line_code].append(projected)
            if kind != RouteKind.NOCTURNO:
                day[line_code].append(projected)
        self._day = {line: unary_union(geoms) for line, geoms in day.items()}
        self._night = {line: unary_union(geoms) for line, geoms in night.items()}

    def has_line(self, line_code: str) -> bool:
        return line_code in self._night

    def is_night(self, at: datetime) -> bool:
        hour = at.astimezone(SANTIAGO_TZ).hour
        start, end = self.night_hours
        return start <= hour or hour < end if start > end else start <= hour < end

    def check(self, line_code: str, lat: float, lon: float, at: datetime | None = None) -> RouteCheck | None:
        """None si la línea no tiene trazado cargado (no se puede afirmar nada).

        `at` = hora del ping; sin ella se usa el corredor diurno.
        """
        night = at is not None and self.is_night(at)
        # Una línea con solo servicio nocturno no tiene corredor diurno: usamos el nocturno
        # (validar el horario de operación es otra regla, no un desvío de ruta).
        route = (self._night if night else self._day).get(line_code)
        if route is None:
            route = self._night.get(line_code)
        if route is None:
            return None
        point = Point(self._to_metric.transform(lon, lat))
        # distancia(punto, línea) <= buffer  ≡  punto ∈ line.buffer(buffer_m),
        # pero además nos entrega los metros de desvío para la alerta y la analítica.
        distance = route.distance(point)
        return RouteCheck(on_route=distance <= self.buffer_m, distance_m=round(distance, 1))


async def load_corridors(session: AsyncSession, validator: RouteValidator) -> int:
    """Carga los trazados activos desde PostGIS al validador en memoria."""
    rows = await session.execute(
        select(Route.line_code, func.ST_AsGeoJSON(Route.geom), Route.kind).where(Route.is_active.is_(True))
    )
    routes = [(line_code, shape(json.loads(geojson)), kind) for line_code, geojson, kind in rows]
    validator.load(routes)
    return len(routes)


# ST_Distance sobre `geography` devuelve metros directamente (cálculo sobre el elipsoide),
# sin tener que elegir un CRS proyectado. ST_DWithin usaría el índice GIST si solo
# necesitáramos el booleano.
_POSTGIS_DISTANCE_SQL = text(
    """
    SELECT MIN(ST_Distance(r.geom::geography,
                           ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography)) AS distance_m
    FROM routes r
    WHERE r.line_code = :line_code AND r.is_active
      AND (r.kind <> 'nocturno' OR :night)
    """
)


async def distance_to_route_postgis(
    session: AsyncSession, line_code: str, lat: float, lon: float, buffer_m: float, *, night: bool = False
) -> RouteCheck | None:
    distance = (
        await session.execute(
            _POSTGIS_DISTANCE_SQL, {"line_code": line_code, "lat": lat, "lon": lon, "night": night}
        )
    ).scalar_one()
    if distance is None:
        return None
    return RouteCheck(on_route=distance <= buffer_m, distance_m=round(distance, 1))
