"""Estado en tiempo real de la flota, 100 % en Redis.

El POST del conductor nunca escribe en PostgreSQL: cada ping hace UN round-trip a Redis
(pipeline) que actualiza:

  vehicles:geo            GEO set (ZSET) con la última posición de cada vehículo -> GEOSEARCH
  vehicle:{id}:state      HASH con el último estado, con TTL => "activo" = la llave existe
  vehicle:{id}:offroute   contador de pings consecutivos fuera de ruta (histéresis anti-ruido GPS)
  stream:locations        Redis Stream; el worker `location_persister` lo vacía a PostGIS en lotes
"""

import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.models import Vehicle
from app.schemas.location import LocationUpdateIn, NearbyVehicle
from app.services.route_validator import RouteCheck

GEO_KEY = "vehicles:geo"
STREAM_KEY = "stream:locations"
STREAM_MAXLEN = 200_000  # colchón si el worker se cae (~1 h para 500 vehículos)


def state_key(vehicle_id: str) -> str:
    return f"vehicle:{vehicle_id}:state"


def strikes_key(vehicle_id: str) -> str:
    return f"vehicle:{vehicle_id}:offroute"


def _encode(value: object) -> str | int | float:
    # Redis no acepta None ni bool.
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    return value  # type: ignore[return-value]


def _decode_bool(value: str) -> bool | None:
    return None if value == "" else value == "1"


async def record_location(
    redis: Redis,
    vehicle: Vehicle,
    payload: LocationUpdateIn,
    check: RouteCheck | None,
    *,
    ttl_s: int,
    live: bool,
) -> int:
    """Registra un ping. Devuelve cuántos pings consecutivos lleva fuera de ruta.

    `live=False` (ping atrasado, p. ej. reenviado al recuperar señal): solo va al histórico,
    para no "teletransportar" el auto en el mapa del pasajero a una posición vieja.
    """
    vid = str(vehicle.id)
    on_route = None if check is None else check.on_route
    history_entry = {
        "vehicle_id": vid,
        "lat": payload.lat,
        "lon": payload.lon,
        "recorded_at": payload.timestamp.isoformat(),
        "available_seats": payload.available_seats,
        "speed_kmh": payload.speed_kmh,
        "heading": payload.heading,
        "on_route": on_route,
        "distance_m": None if check is None else check.distance_m,
    }

    async with redis.pipeline(transaction=False) as pipe:
        pipe.xadd(STREAM_KEY, {k: _encode(v) for k, v in history_entry.items()},
                  maxlen=STREAM_MAXLEN, approximate=True)
        if live:
            pipe.geoadd(GEO_KEY, (payload.lon, payload.lat, vid))
            state = {
                **history_entry,
                "plate": vehicle.plate,
                "line_code": vehicle.line_code,
                "max_capacity": vehicle.max_capacity,
            }
            pipe.hset(state_key(vid), mapping={k: _encode(v) for k, v in state.items()})
            pipe.expire(state_key(vid), ttl_s)
            if on_route is False:
                pipe.incr(strikes_key(vid))
                pipe.expire(strikes_key(vid), ttl_s)
            else:
                pipe.delete(strikes_key(vid))
        results = await pipe.execute()

    return int(results[-2]) if live and on_route is False else 0


async def find_nearby(redis: Redis, lat: float, lon: float, radius_m: int, limit: int) -> list[NearbyVehicle]:
    """Vehículos activos en `radius_m`, ordenados por distancia.

    Equivalente PostGIS (si el estado viviera en una tabla `vehicle_positions`):
        SELECT ... WHERE ST_DWithin(geom::geography, ST_MakePoint(:lon,:lat)::geography, :radius)
    Redis GEOSEARCH lo resuelve en memoria (geohash sobre un ZSET), ideal para miles de lecturas/s.
    """
    hits = await redis.geosearch(
        GEO_KEY, longitude=lon, latitude=lat, radius=radius_m, unit="m",
        sort="ASC", count=limit, withdist=True,
    )
    if not hits:
        return []

    async with redis.pipeline(transaction=False) as pipe:
        for member, _ in hits:
            pipe.hgetall(state_key(member))
        states = await pipe.execute()

    vehicles: list[NearbyVehicle] = []
    expired: list[str] = []
    for (member, distance), state in zip(hits, states, strict=True):
        if not state:  # el HASH expiró => el conductor dejó de transmitir
            expired.append(member)
            continue
        vehicles.append(
            NearbyVehicle(
                vehicle_id=uuid.UUID(member),
                plate=state["plate"],
                line_code=state["line_code"],
                lat=float(state["lat"]),
                lon=float(state["lon"]),
                distance_m=round(float(distance), 1),
                available_seats=int(state["available_seats"]),
                max_capacity=int(state["max_capacity"]),
                heading=float(state["heading"]) if state["heading"] else None,
                on_route=_decode_bool(state["on_route"]),
                last_seen=datetime.fromisoformat(state["recorded_at"]).astimezone(UTC),
            )
        )

    # Limpieza perezosa: los miembros del GEO set no expiran solos.
    if expired:
        await redis.zrem(GEO_KEY, *expired)
    return vehicles


async def fleet(redis: Redis) -> list[dict]:
    """Todos los vehículos transmitiendo ahora (para el mapa de la flota en /admin).

    Además del estado de cada uno, cuántos pings seguidos lleva fuera de ruta (0 = en ruta).
    """
    members = await redis.zrange(GEO_KEY, 0, -1)
    if not members:
        return []
    async with redis.pipeline(transaction=False) as pipe:
        for member in members:
            pipe.hgetall(state_key(member))
            pipe.get(strikes_key(member))
        results = await pipe.execute()

    vehicles, expired = [], []
    for member, state, strikes in zip(members, results[0::2], results[1::2], strict=True):
        if not state:
            expired.append(member)
            continue
        vehicles.append({
            "vehicle_id": member,
            "plate": state["plate"],
            "line_code": state["line_code"],
            "lat": float(state["lat"]),
            "lon": float(state["lon"]),
            "available_seats": int(state["available_seats"]),
            "max_capacity": int(state["max_capacity"]),
            "speed_kmh": float(state["speed_kmh"]) if state.get("speed_kmh") else None,
            "heading": float(state["heading"]) if state.get("heading") else None,
            "on_route": _decode_bool(state.get("on_route", "")),
            "distance_to_route_m": float(state["distance_m"]) if state.get("distance_m") else None,
            "off_route_strikes": int(strikes or 0),
            "last_seen": datetime.fromisoformat(state["recorded_at"]).astimezone(UTC),
        })
    if expired:
        await redis.zrem(GEO_KEY, *expired)
    return sorted(vehicles, key=lambda v: (v["line_code"], v["plate"]))
