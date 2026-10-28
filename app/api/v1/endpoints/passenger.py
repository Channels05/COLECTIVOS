import asyncio
import contextlib
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from redis.asyncio import Redis

from app.api.deps import RedisClient, Validator
from app.schemas.location import NearbyResponse
from app.services import tracking
from app.services.route_validator import RouteValidator

router = APIRouter(prefix="/vehicles", tags=["pasajero"])

# WebSocket: cada cuánto se revisa Redis y, si nada cambió, cada cuánto se reenvía igual (para
# que la app sepa que el dato está al día y el proxy no corte la conexión por inactividad).
WS_TICK_S = 2.0
WS_HEARTBEAT_S = 10.0


class NearbyQuery(BaseModel):
    """Lo mismo que los parámetros de GET /vehicles/nearby (también es el mensaje del WebSocket)."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=2000, ge=100, le=5000)
    line_code: str | None = Field(default=None, max_length=20)
    min_seats: int = Field(default=0, ge=0, le=6)
    limit: int = Field(default=50, ge=1, le=200)
    user_lat: float | None = Field(default=None, ge=-90, le=90)
    user_lon: float | None = Field(default=None, ge=-180, le=180)


async def _nearby(redis: Redis, validator: RouteValidator, q: NearbyQuery) -> NearbyResponse:
    vehicles = await tracking.find_nearby(redis, q.lat, q.lon, q.radius_m, q.limit)
    vehicles = [
        v for v in vehicles
        if v.available_seats >= q.min_seats and (q.line_code is None or v.line_code == q.line_code)
    ]
    # Con la ubicación del pasajero (que puede no ser el centro de búsqueda del mapa): distancia
    # por el recorrido de cada línea, para una llegada estimada más precisa.
    if q.user_lat is not None and q.user_lon is not None:
        for v in vehicles:
            v.route_distance_m = validator.distance_along_route(v.line_code, (v.lat, v.lon), v.heading, (q.user_lat, q.user_lon))
    return NearbyResponse(radius_m=q.radius_m, count=len(vehicles), vehicles=vehicles)


@router.get("/nearby", response_model=NearbyResponse)
async def nearby_vehicles(
    redis: RedisClient,
    validator: Validator,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    radius_m: Annotated[int, Query(ge=100, le=5000)] = 2000,
    line_code: Annotated[str | None, Query(max_length=20)] = None,
    min_seats: Annotated[int, Query(ge=0, le=6)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    user_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    user_lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> NearbyResponse:
    """Colectivos activos alrededor del pasajero, del más cercano al más lejano.

    Para seguirlos en vivo conviene el WebSocket /vehicles/nearby/live (sin una consulta por
    actualización); este GET queda para una consulta suelta o si el WebSocket no conecta.
    """
    query = NearbyQuery(
        lat=lat, lon=lon, radius_m=radius_m, line_code=line_code, min_seats=min_seats, limit=limit,
        user_lat=user_lat, user_lon=user_lon,
    )
    return await _nearby(redis, validator, query)


@router.websocket("/nearby/live")
async def nearby_live(websocket: WebSocket) -> None:
    """Colectivos cercanos en vivo.

    La app manda un JSON con los mismos campos que GET /vehicles/nearby (y otro cada vez que
    mueve el mapa o cambia un filtro). El servidor responde con un NearbyResponse cada vez que
    algo cambia (revisa cada 2 s) y, si nada cambia, igual cada 10 s.
    """
    await websocket.accept()
    redis: Redis = websocket.app.state.redis
    validator: RouteValidator = websocket.app.state.route_validator
    query: NearbyQuery | None = None
    changed = asyncio.Event()

    async def receive() -> None:
        nonlocal query
        while True:
            try:
                query = NearbyQuery.model_validate_json(await websocket.receive_text())
                changed.set()
            except ValidationError as exc:
                await websocket.send_json({"error": exc.errors(include_url=False, include_context=False)[:3]})

    reader = asyncio.create_task(receive())
    last_payload: str | None = None
    last_sent_at = 0.0
    loop = asyncio.get_running_loop()
    try:
        while not reader.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), WS_TICK_S)
            fresh = changed.is_set()
            changed.clear()
            if query is None:
                continue
            payload = (await _nearby(redis, validator, query)).model_dump_json()
            if fresh or payload != last_payload or loop.time() - last_sent_at >= WS_HEARTBEAT_S:
                await websocket.send_text(payload)
                last_payload, last_sent_at = payload, loop.time()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await reader
