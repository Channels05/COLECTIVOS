"""¿Qué colectivo me lleva? Líneas que pasan cerca del origen y del destino.

Busca en los trazados activos (oficiales y grabados, sin los nocturnos) las líneas con un tramo
a menos de `walk_m` de ambos puntos y estima el viaje por ese tramo. Los trazados del GORE no
siempre están dibujados en el sentido del recorrido, así que no se usa el orden a lo largo del
tramo para descartar: como cada línea tiene ida y regreso por las mismas calles (o paralelas), si
pasa cerca de ambos puntos sirve en uno de sus sentidos. La app avisa que hay que mirar el letrero.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WALK_M_DEFAULT = 500
MIN_TRIP_M = 300  # más cerca que esto conviene caminar
MAX_OPTIONS = 8
RIDE_SPEED_M_PER_MIN = 20_000 / 60  # 20 km/h, igual que la llegada estimada de la app
WALK_SPEED_M_PER_MIN = 75  # ~4,5 km/h

# Por cada tramo (pieza) de cada trazado que pasa cerca de ambos puntos: cuánto hay que caminar a
# él desde cada punto, dónde subir y bajar, y cuánto se viaja por el tramo entre ambos.
_PLAN = text("""
    WITH o AS (SELECT ST_SetSRID(ST_MakePoint(:olon, :olat), 4326) AS p),
         d AS (SELECT ST_SetSRID(ST_MakePoint(:dlon, :dlat), 4326) AS p),
    candidates AS (
        SELECT r.line_code, r.direction::text AS direction, r.source::text AS source,
               (ST_Dump(ST_LineMerge(r.geom))).geom AS g
        FROM routes r, o, d
        WHERE r.is_active AND r.kind::text <> 'nocturno'
          AND ST_DWithin(r.geom, o.p, :deg) AND ST_DWithin(r.geom, d.p, :deg)
    ), near AS (
        SELECT c.*, ST_Distance(c.g::geography, o.p::geography) AS walk_o,
               ST_Distance(c.g::geography, d.p::geography) AS walk_d,
               ST_LineLocatePoint(c.g, o.p) AS fo, ST_LineLocatePoint(c.g, d.p) AS fd
        FROM candidates c, o, d
        WHERE ST_DWithin(c.g::geography, o.p::geography, :walk) AND ST_DWithin(c.g::geography, d.p::geography, :walk)
    )
    SELECT n.line_code, n.direction, n.source, n.walk_o, n.walk_d,
           abs(n.fd - n.fo) * ST_Length(n.g::geography) AS ride_m,
           ST_Y(ST_LineInterpolatePoint(n.g, n.fo)) AS board_lat, ST_X(ST_LineInterpolatePoint(n.g, n.fo)) AS board_lon,
           ST_Y(ST_LineInterpolatePoint(n.g, n.fd)) AS alight_lat, ST_X(ST_LineInterpolatePoint(n.g, n.fd)) AS alight_lon,
           l.name, l.description, l.commune
    FROM near n LEFT JOIN lines l ON l.code = n.line_code
    WHERE l.status IS NULL OR l.status::text = 'active'
""")

_STRAIGHT = text("""
    SELECT ST_Distance(ST_SetSRID(ST_MakePoint(:olon, :olat), 4326)::geography,
                       ST_SetSRID(ST_MakePoint(:dlon, :dlat), 4326)::geography)
""")


@dataclass
class TripOption:
    line_code: str
    name: str | None
    description: str | None
    commune: str | None
    direction: str
    walk_to_board_m: int
    board: tuple[float, float]
    walk_from_alight_m: int
    alight: tuple[float, float]
    ride_m: int
    total_min: int  # caminar + viajar (sin la espera)


async def plan(
    db: AsyncSession, origin: tuple[float, float], destination: tuple[float, float], walk_m: int = WALK_M_DEFAULT
) -> tuple[float, list[TripOption]]:
    """(distancia en línea recta, opciones de la mejor a la peor). Puntos como (lat, lon)."""
    params = {"olat": origin[0], "olon": origin[1], "dlat": destination[0], "dlon": destination[1]}
    straight = float(await db.scalar(_STRAIGHT, params))
    if straight < MIN_TRIP_M:
        return straight, []
    rows = await db.execute(_PLAN, {**params, "walk": walk_m, "deg": walk_m / 90_000})
    best: dict[str, TripOption] = {}
    for r in rows:
        # El tramo tiene que llevar de un punto al otro: si el viaje por él es mucho más corto que
        # la distancia entre ambos, es un tramo que solo los roza (p. ej. cruza una avenida).
        if r.ride_m < straight * 0.6:
            continue
        walk = r.walk_o + r.walk_d
        option = TripOption(
            line_code=r.line_code, name=r.name, description=r.description, commune=r.commune, direction=r.direction,
            walk_to_board_m=round(r.walk_o), board=(r.board_lat, r.board_lon),
            walk_from_alight_m=round(r.walk_d), alight=(r.alight_lat, r.alight_lon), ride_m=round(r.ride_m),
            total_min=round(walk / WALK_SPEED_M_PER_MIN + r.ride_m / RIDE_SPEED_M_PER_MIN),
        )
        current = best.get(r.line_code)
        if current is None or (option.total_min, walk) < (current.total_min, current.walk_to_board_m + current.walk_from_alight_m):
            best[r.line_code] = option
    options = sorted(best.values(), key=lambda o: (o.total_min, o.walk_to_board_m + o.walk_from_alight_m))
    return straight, options[:MAX_OPTIONS]
