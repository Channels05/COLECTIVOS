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

from app.services import metro

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


# --- Colectivo + Metro ----------------------------------------------------------------------------

STATION_WALK_M = 300  # del paradero del colectivo a la estación
METRO_WALK_M = 800  # caminando a una estación desde el origen o hasta el destino
MIN_COLECTIVO_RIDE_M = 700  # menos que esto, mejor caminar a la estación
MAX_COMBOS = 3

# Por cada tramo de colectivo que pasa cerca de `p` y cada estación cerca de ese tramo: cuánto se
# camina de `p` al tramo, del tramo a la estación y cuánto se viaja entre ambos puntos del tramo.
_TO_STATIONS = text("""
    WITH p AS (SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g),
    st AS (
        SELECT s.key, ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326) AS g
        FROM unnest(CAST(:keys AS int[]), CAST(:lons AS float8[]), CAST(:lats AS float8[])) AS s(key, lon, lat)
    ), candidates AS (
        SELECT r.line_code, (ST_Dump(ST_LineMerge(r.geom))).geom AS g
        FROM routes r, p
        WHERE r.is_active AND r.kind::text <> 'nocturno' AND ST_DWithin(r.geom, p.g, :deg)
    ), near AS (
        SELECT c.*, ST_Distance(c.g::geography, p.g::geography) AS walk_p, ST_LineLocatePoint(c.g, p.g) AS fp
        FROM candidates c, p
        WHERE ST_DWithin(c.g::geography, p.g::geography, :walk)
    ), legs AS (
        SELECT n.line_code, n.walk_p, st.key AS station, ST_Distance(n.g::geography, st.g::geography) AS walk_s,
               abs(ST_LineLocatePoint(n.g, st.g) - n.fp) * ST_Length(n.g::geography) AS ride_m,
               ST_LineInterpolatePoint(n.g, n.fp) AS at_p,
               ST_LineInterpolatePoint(n.g, ST_LineLocatePoint(n.g, st.g)) AS at_s
        FROM near n JOIN st ON ST_DWithin(n.g::geography, st.g::geography, :station_walk)
    )
    SELECT g.line_code, g.walk_p, g.station, g.walk_s, g.ride_m,
           ST_Y(g.at_p) AS p_lat, ST_X(g.at_p) AS p_lon, ST_Y(g.at_s) AS s_lat, ST_X(g.at_s) AS s_lon,
           l.name, l.description
    FROM legs g LEFT JOIN lines l ON l.code = g.line_code
    WHERE (l.status IS NULL OR l.status::text = 'active') AND g.ride_m >= :min_ride
""")


@dataclass
class ColectivoLeg:
    line_code: str
    name: str | None
    description: str | None
    walk_point_m: float  # entre el punto del pasajero (origen o destino) y el tramo
    walk_station_m: float  # entre el tramo y la estación
    ride_m: float
    at_point: tuple[float, float]  # dónde sube (o baja) cerca del pasajero
    at_station: tuple[float, float]  # dónde baja (o sube) cerca de la estación

    @property
    def minutes(self) -> float:
        return (self.walk_point_m + self.walk_station_m) / WALK_SPEED_M_PER_MIN + self.ride_m / RIDE_SPEED_M_PER_MIN


async def _colectivo_to_stations(
    db: AsyncSession, point: tuple[float, float], net: metro.Network
) -> dict[int, list[ColectivoLeg]]:
    """{estación: tramos de colectivo entre `point` y esa estación}, el mejor por línea."""
    stations = net.stations
    rows = await db.execute(_TO_STATIONS, {
        "lat": point[0], "lon": point[1], "walk": WALK_M_DEFAULT, "deg": WALK_M_DEFAULT / 90_000,
        "station_walk": STATION_WALK_M, "min_ride": MIN_COLECTIVO_RIDE_M,
        "keys": [s.key for s in stations], "lons": [s.lon for s in stations], "lats": [s.lat for s in stations],
    })
    best: dict[tuple[int, str], ColectivoLeg] = {}
    for r in rows:
        station = stations[r.station]
        # Como en el viaje directo: el tramo tiene que acercar de verdad a la estación.
        if r.ride_m < 0.6 * metro.distance_m(point[0], point[1], station.lat, station.lon):
            continue
        leg = ColectivoLeg(
            r.line_code, r.name, r.description, r.walk_p, r.walk_s, r.ride_m, (r.p_lat, r.p_lon), (r.s_lat, r.s_lon)
        )
        key = (r.station, r.line_code)
        if key not in best or leg.minutes < best[key].minutes:
            best[key] = leg
    by_station: dict[int, list[ColectivoLeg]] = {}
    for (station, _), leg in best.items():
        by_station.setdefault(station, []).append(leg)
    return by_station


def _point(lat: float, lon: float) -> dict:
    return {"lat": lat, "lon": lon}


def _metro_steps(net: metro.Network, path: list[int]) -> list[dict]:
    return [
        {
            "kind": "metro", "ref": leg.ref, "line_name": leg.line_name, "color": leg.color,
            "from_station": leg.start.name, "to_station": leg.end.name, "stops": leg.stops,
            "start": _point(leg.start.lat, leg.start.lon), "end": _point(leg.end.lat, leg.end.lon),
            "path": [_point(s.lat, s.lon) for s in leg.stations],
        }
        for leg in net.legs(path)
    ]


def _colectivo_step(leg: ColectivoLeg, board: tuple[float, float], alight: tuple[float, float]) -> dict:
    return {
        "kind": "colectivo", "line_code": leg.line_code, "name": leg.name, "description": leg.description,
        "ride_m": round(leg.ride_m), "board": _point(*board), "alight": _point(*alight),
    }


def _walk(meters: float, to: str) -> dict:
    return {"kind": "walk", "meters": round(meters), "to": to}


def _metro_only(
    net: metro.Network, near_origin: list[tuple[metro.Station, float]], near_destination: list[tuple[metro.Station, float]]
) -> dict | None:
    """El mejor viaje solo en Metro, caminando a la estación de entrada y desde la de salida."""
    exits = {s.key: d for s, d in near_destination}
    best: tuple[float, dict] | None = None
    for entry, walk_in in near_origin:
        for exit_key, (metro_min, path) in net.ride(entry.key, set(exits)).items():
            if not net.legs(path):  # solo cambia de andén en la misma estación
                continue
            total = (walk_in + exits[exit_key]) / WALK_SPEED_M_PER_MIN + metro.ACCESS_MIN + metro_min
            if best is None or total < best[0]:
                best = (total, {"total_min": round(total), "steps": [
                    _walk(walk_in, f"Metro {entry.name}"),
                    *_metro_steps(net, path),
                    _walk(exits[exit_key], "tu destino"),
                ]})
    return best[1] if best else None


async def combos(
    db: AsyncSession, origin: tuple[float, float], destination: tuple[float, float], beat_min: float | None
) -> list[dict]:
    """Viajes "colectivo + Metro" o "Metro + colectivo" más rápidos que `beat_min` (el mejor viaje
    solo en colectivo; None si no hay). Cada uno: {total_min, steps}."""
    net = metro.network()
    if net is None:
        return []
    near_origin = net.near(*origin, METRO_WALK_M)
    near_destination = net.near(*destination, METRO_WALK_M)
    # Si se puede ir caminando a la estación en ambos extremos, el colectivo no aporta: solo Metro.
    if near_origin and near_destination:
        metro_only = _metro_only(net, near_origin, near_destination)
        if metro_only and (beat_min is None or metro_only["total_min"] < beat_min):
            return [metro_only]
        return []
    found: list[tuple[float, str, dict]] = []  # (minutos, línea de colectivo, viaje)

    if near_destination:  # colectivo hasta una estación y Metro hasta el destino
        exits = {s.key: d for s, d in near_destination}
        for station_key, legs in (await _colectivo_to_stations(db, origin, net)).items():
            entry = net.stations[station_key]
            for exit_key, (metro_min, path) in net.ride(station_key, set(exits)).items():
                for leg in legs:
                    total = leg.minutes + metro.ACCESS_MIN + metro_min + exits[exit_key] / WALK_SPEED_M_PER_MIN
                    found.append((total, leg.line_code, {"total_min": round(total), "steps": [
                        _walk(leg.walk_point_m, "el colectivo"),
                        _colectivo_step(leg, leg.at_point, leg.at_station),
                        _walk(leg.walk_station_m, f"Metro {entry.name}"),
                        *_metro_steps(net, path),
                        _walk(exits[exit_key], "tu destino"),
                    ]}))

    if near_origin:  # Metro hasta una estación y colectivo hasta el destino
        legs_by_station = await _colectivo_to_stations(db, destination, net)
        for station, walk_in in near_origin:
            for exit_key, (metro_min, path) in net.ride(station.key, set(legs_by_station)).items():
                for leg in legs_by_station[exit_key]:
                    total = walk_in / WALK_SPEED_M_PER_MIN + metro.ACCESS_MIN + metro_min + leg.minutes
                    found.append((total, leg.line_code, {"total_min": round(total), "steps": [
                        _walk(walk_in, f"Metro {station.name}"),
                        *_metro_steps(net, path),
                        _walk(leg.walk_station_m, "el colectivo"),
                        _colectivo_step(leg, leg.at_station, leg.at_point),
                        _walk(leg.walk_point_m, "tu destino"),
                    ]}))

    result, seen = [], set()
    for total, key, combo in sorted(found, key=lambda f: f[0]):
        if beat_min is not None and total >= beat_min:
            break
        if key in seen:
            continue
        seen.add(key)
        result.append(combo)
        if len(result) == MAX_COMBOS:
            break
    return result


# --- Dos colectivos (con transbordo) ---------------------------------------------------------------

TRANSFER_M = 150  # caminando entre el tramo de una línea y el de la otra
TRANSFER_WAIT_MIN = 5.0  # esperar el segundo colectivo
MIN_LEG_RIDE_M = 500
MAX_DETOUR = 2.5
MAX_TRANSFERS = 3

# Pares (línea que pasa cerca del origen, línea que pasa cerca del destino) cuyos tramos se acercan
# a menos de TRANSFER_M: el transbordo es el punto donde están más cerca.
_TRANSFERS = text("""
    WITH o AS (SELECT ST_SetSRID(ST_MakePoint(:olon, :olat), 4326) AS p),
         d AS (SELECT ST_SetSRID(ST_MakePoint(:dlon, :dlat), 4326) AS p),
    a AS (
        SELECT r.line_code, (ST_Dump(ST_LineMerge(r.geom))).geom AS g
        FROM routes r, o WHERE r.is_active AND r.kind::text <> 'nocturno' AND ST_DWithin(r.geom, o.p, :deg)
    ), an AS (
        SELECT a.*, ST_Distance(a.g::geography, o.p::geography) AS walk_o, ST_LineLocatePoint(a.g, o.p) AS fo
        FROM a, o WHERE ST_DWithin(a.g::geography, o.p::geography, :walk)
    ), b AS (
        SELECT r.line_code, (ST_Dump(ST_LineMerge(r.geom))).geom AS g
        FROM routes r, d WHERE r.is_active AND r.kind::text <> 'nocturno' AND ST_DWithin(r.geom, d.p, :deg)
    ), bn AS (
        SELECT b.*, ST_Distance(b.g::geography, d.p::geography) AS walk_d, ST_LineLocatePoint(b.g, d.p) AS fd
        FROM b, d WHERE ST_DWithin(b.g::geography, d.p::geography, :walk)
    ), pairs AS (
        SELECT an.line_code AS l1, bn.line_code AS l2, an.walk_o, bn.walk_d, an.g AS g1, bn.g AS g2, an.fo, bn.fd,
               ST_ShortestLine(an.g, bn.g) AS sl
        FROM an JOIN bn ON an.line_code <> bn.line_code AND ST_DWithin(an.g, bn.g, :tdeg)
    ), legs AS (
        SELECT p.*, ST_Length(p.sl::geography) AS walk_t,
               ST_LineLocatePoint(p.g1, ST_StartPoint(p.sl)) AS f1t, ST_LineLocatePoint(p.g2, ST_EndPoint(p.sl)) AS f2t
        FROM pairs p
    )
    SELECT g.l1, g.l2, g.walk_o, g.walk_d, g.walk_t,
           abs(g.f1t - g.fo) * ST_Length(g.g1::geography) AS ride1,
           abs(g.fd - g.f2t) * ST_Length(g.g2::geography) AS ride2,
           ST_Y(ST_LineInterpolatePoint(g.g1, g.fo)) AS b1_lat, ST_X(ST_LineInterpolatePoint(g.g1, g.fo)) AS b1_lon,
           ST_Y(ST_StartPoint(g.sl)) AS a1_lat, ST_X(ST_StartPoint(g.sl)) AS a1_lon,
           ST_Y(ST_EndPoint(g.sl)) AS b2_lat, ST_X(ST_EndPoint(g.sl)) AS b2_lon,
           ST_Y(ST_LineInterpolatePoint(g.g2, g.fd)) AS a2_lat, ST_X(ST_LineInterpolatePoint(g.g2, g.fd)) AS a2_lon,
           l1.name AS name1, l1.description AS desc1, l2.name AS name2, l2.description AS desc2
    FROM legs g
    LEFT JOIN lines l1 ON l1.code = g.l1
    LEFT JOIN lines l2 ON l2.code = g.l2
    WHERE (l1.status IS NULL OR l1.status::text = 'active') AND (l2.status IS NULL OR l2.status::text = 'active')
""")


async def transfers(db: AsyncSession, origin: tuple[float, float], destination: tuple[float, float]) -> list[dict]:
    """Viajes con dos colectivos y un transbordo caminando, para cuando ninguno lleva directo.

    Mismo formato que `combos` ({total_min, steps}). Como en el viaje directo, cada colectivo tiene
    que acercar de verdad: su tramo, al menos el 60 % de la distancia en línea recta que cubre.
    """
    rows = await db.execute(_TRANSFERS, {
        "olat": origin[0], "olon": origin[1], "dlat": destination[0], "dlon": destination[1],
        "walk": WALK_M_DEFAULT, "deg": WALK_M_DEFAULT / 90_000, "tdeg": TRANSFER_M / 90_000,
    })
    straight = metro.distance_m(*origin, *destination)
    best: dict[tuple[str, str], tuple[float, dict]] = {}
    for r in rows:
        transfer = (r.a1_lat, r.a1_lon)
        to_transfer = metro.distance_m(*origin, *transfer)
        from_transfer = metro.distance_m(*transfer, *destination)
        if (r.walk_t > TRANSFER_M or r.ride1 < MIN_LEG_RIDE_M or r.ride2 < MIN_LEG_RIDE_M
                or r.ride1 < 0.6 * to_transfer or r.ride2 < 0.6 * from_transfer
                # Demasiada vuelta: dos colectivos que suman más de 2,5 veces la distancia directa.
                or r.ride1 + r.ride2 > max(MAX_DETOUR * straight, straight + 3_000)):
            continue
        walk = r.walk_o + r.walk_t + r.walk_d
        total = walk / WALK_SPEED_M_PER_MIN + (r.ride1 + r.ride2) / RIDE_SPEED_M_PER_MIN + TRANSFER_WAIT_MIN
        key = (r.l1, r.l2)
        if key in best and best[key][0] <= total:
            continue
        best[key] = (total, {"total_min": round(total), "steps": [
            _walk(r.walk_o, "el colectivo"),
            {"kind": "colectivo", "line_code": r.l1, "name": r.name1, "description": r.desc1, "ride_m": round(r.ride1),
             "board": _point(r.b1_lat, r.b1_lon), "alight": _point(r.a1_lat, r.a1_lon)},
            _walk(r.walk_t, f"el colectivo {r.l2}"),
            {"kind": "colectivo", "line_code": r.l2, "name": r.name2, "description": r.desc2, "ride_m": round(r.ride2),
             "board": _point(r.b2_lat, r.b2_lon), "alight": _point(r.a2_lat, r.a2_lon)},
            _walk(r.walk_d, "tu destino"),
        ]})
    # Variedad: no repetir la primera línea (si no, salen tres opciones casi iguales).
    result, first_lines = [], set()
    for (l1, _), (_, trip) in sorted(best.items(), key=lambda kv: kv[1][0]):
        if l1 in first_lines:
            continue
        first_lines.add(l1)
        result.append(trip)
        if len(result) == MAX_TRANSFERS:
            break
    return result
