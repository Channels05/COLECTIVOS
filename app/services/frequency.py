"""Frecuencia de paso: cada cuánto pasa un colectivo de cada línea, según las marcas de pasajeros.

Un pasajero que espera marca "pasó un colectivo" cada vez que pasa uno de su línea. Con las marcas
de un mismo lugar (a menos de `NEAR_M`) se estima el intervalo entre pasadas:

1. Junta las marcas a menos de `SAME_PASS_S` entre sí: son el mismo auto marcado por dos personas
   (o dos veces por la misma).
2. Calcula el tiempo entre pasadas seguidas del mismo día. Los intervalos de más de `MAX_GAP_MIN`
   no cuentan: significan que nadie estaba mirando, no que el colectivo tardó tanto.
3. Usa la mediana (un auto que nadie marcó no la mueve tanto como al promedio). Primero con las
   marcas de la misma franja (mismo tipo de día y ±1 h de la hora actual); si no alcanzan, con
   todas las horas.

Es una estimación: si los pasajeros no marcan todos los autos, el intervalo sale más largo.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Contributor, Line, LineStatus, Sighting
from app.services.errors import ServiceError

SANTIAGO_TZ = ZoneInfo("America/Santiago")

NEAR_M = 400.0  # marcas a menos de esto cuentan como el mismo lugar
LOOKBACK_DAYS = 28
SAME_PASS_S = 90
MIN_GAP_MIN = 1.0
MAX_GAP_MIN = 45.0
MIN_SAMPLES = 3  # intervalos necesarios para dar una cifra
HOUR_WINDOW = 1  # franja: la hora actual ± esto
# Freno contra toques repetidos y scripts.
REPEAT_COOLDOWN_S = 60
MAX_PER_HOUR = 60
MAX_ACCURACY_M = 150.0


@dataclass(frozen=True)
class Headway:
    minutes: float | None  # mediana entre pasadas; None si aún no hay datos suficientes
    samples: int  # intervalos usados
    scope: str | None  # "hour": misma franja horaria · "all": todas las horas
    sightings: int  # marcas en el lugar (últimos LOOKBACK_DAYS días)
    last_seen_at: datetime | None


def _day_type(t: datetime) -> str:
    return "weekday" if t.weekday() < 5 else "weekend"


def _passes(times: Sequence[datetime]) -> list[datetime]:
    """Hora local de cada pasada, juntando las marcas del mismo auto."""
    passes: list[datetime] = []
    for t in sorted(times):
        local = t.astimezone(SANTIAGO_TZ)
        if passes and (local - passes[-1]).total_seconds() < SAME_PASS_S:
            continue
        passes.append(local)
    return passes


def _gaps(passes: Sequence[datetime]) -> list[float]:
    gaps = []
    for a, b in zip(passes, passes[1:], strict=False):
        minutes = (b - a).total_seconds() / 60
        if a.date() == b.date() and MIN_GAP_MIN <= minutes <= MAX_GAP_MIN:
            gaps.append(minutes)
    return gaps


def estimate(times: Sequence[datetime], now: datetime) -> Headway:
    """Intervalo entre pasadas a partir de las horas de las marcas de un lugar (con zona horaria)."""
    if not times:
        return Headway(None, 0, None, 0, None)
    passes = _passes(times)
    local_now = now.astimezone(SANTIAGO_TZ)
    in_window = [
        p for p in passes if _day_type(p) == _day_type(local_now) and abs(p.hour - local_now.hour) <= HOUR_WINDOW
    ]
    minutes, scope, samples = None, None, 0
    for candidate_scope, subset in (("hour", in_window), ("all", passes)):
        gaps = _gaps(subset)
        samples = max(samples, len(gaps))
        if len(gaps) >= MIN_SAMPLES:
            minutes, scope, samples = round(median(gaps), 1), candidate_scope, len(gaps)
            break
    return Headway(minutes, samples, scope, len(times), max(times))


async def report(
    db: AsyncSession, contributor: Contributor, line_code: str, lat: float, lon: float, accuracy_m: float | None
) -> Sighting:
    """Guarda que pasó un colectivo de `line_code` donde está el pasajero."""
    min_lon, min_lat, max_lon, max_lat = get_settings().service_bbox
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        raise ServiceError("Estás fuera de la zona donde funciona la app.", 422)
    if accuracy_m is not None and accuracy_m > MAX_ACCURACY_M:
        raise ServiceError(
            f"Tu ubicación es imprecisa (±{round(accuracy_m)} m). Espera unos segundos e intenta de nuevo.", 422
        )
    line = await db.get(Line, line_code)
    if line is None or line.status == LineStatus.REJECTED:
        raise ServiceError("Línea no encontrada.", 404)

    now = datetime.now(UTC)
    mine = select(func.count()).where(Sighting.contributor_id == contributor.id)
    if await db.scalar(
        mine.where(Sighting.line_code == line_code, Sighting.seen_at > now - timedelta(seconds=REPEAT_COOLDOWN_S))
    ):
        raise ServiceError("Ya anotaste ese colectivo hace un momento.", 429)
    if (await db.scalar(mine.where(Sighting.seen_at > now - timedelta(hours=1))) or 0) >= MAX_PER_HOUR:
        raise ServiceError("Llegaste al máximo de marcas por hora. Intenta más tarde.", 429)

    sighting = Sighting(
        line_code=line_code, contributor_id=contributor.id, geom=f"SRID=4326;POINT({lon} {lat})", accuracy_m=accuracy_m
    )
    db.add(sighting)
    await db.commit()
    await db.refresh(sighting)
    return sighting


async def near(db: AsyncSession, lat: float, lon: float, line_code: str | None = None) -> dict[str, Headway]:
    """Frecuencia de cada línea con marcas cerca de (lat, lon)."""
    here = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    query = (
        select(Sighting.line_code, Sighting.seen_at)
        .join(Contributor, Contributor.id == Sighting.contributor_id)
        .where(
            Sighting.seen_at > since,
            Contributor.is_blocked.is_(False),
            # Primer filtro en grados con el índice GIST (0,01° ≈ 1,1 km), luego la distancia exacta.
            func.ST_DWithin(Sighting.geom, here, NEAR_M / 90_000),
            func.ST_DWithin(cast(Sighting.geom, Geography), cast(here, Geography), NEAR_M),
        )
    )
    if line_code:
        query = query.where(Sighting.line_code == line_code)
    by_line: dict[str, list[datetime]] = {}
    for code, seen_at in await db.execute(query):
        by_line.setdefault(code, []).append(seen_at)
    now = datetime.now(UTC)
    return {code: estimate(times, now) for code, times in by_line.items()}
