"""Pasajero: ¿qué colectivo me lleva? y buscar un lugar por nombre."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.api.deps import DbSession, RedisClient
from app.services import places, trip_planner

router = APIRouter(tags=["pasajero"])

# Búsquedas de lugares por IP y minuto: protege el límite de Nominatim (1 por segundo, compartido).
MAX_PLACE_SEARCHES_PER_MINUTE = 20


class Point(BaseModel):
    lat: float
    lon: float


class TripOptionRead(BaseModel):
    line_code: str
    name: str | None
    description: str | None
    commune: str | None
    direction: str  # ida | regreso: el sentido del trazado que calzó (mirar el letrero igual)
    walk_to_board_m: int
    board: Point
    walk_from_alight_m: int
    alight: Point
    ride_m: int
    total_min: int


class TripStep(BaseModel):
    """Un paso de un viaje combinado. Según `kind`, vienen unos campos u otros."""

    kind: Literal["walk", "colectivo", "metro"]
    # walk
    meters: int | None = None
    to: str | None = None
    # colectivo
    line_code: str | None = None
    name: str | None = None
    description: str | None = None
    ride_m: int | None = None
    board: Point | None = None
    alight: Point | None = None
    # metro
    ref: str | None = None  # "L5"
    line_name: str | None = None
    color: str | None = None
    from_station: str | None = None
    to_station: str | None = None
    stops: int | None = None
    start: Point | None = None
    end: Point | None = None


class ComboRead(BaseModel):
    total_min: int
    steps: list[TripStep]


class TripPlanRead(BaseModel):
    straight_m: int  # distancia en línea recta entre origen y destino
    options: list[TripOptionRead]
    # Colectivo + Metro (o al revés), solo si es más rápido que el mejor viaje en un colectivo.
    combos: list[ComboRead] = []


class PlaceRead(BaseModel):
    name: str
    detail: str | None
    lat: float
    lon: float


@router.get("/trips/plan", response_model=TripPlanRead)
async def plan_trip(
    db: DbSession,
    from_lat: Annotated[float, Query(ge=-90, le=90)],
    from_lon: Annotated[float, Query(ge=-180, le=180)],
    to_lat: Annotated[float, Query(ge=-90, le=90)],
    to_lon: Annotated[float, Query(ge=-180, le=180)],
    walk_m: Annotated[int, Query(ge=100, le=1000)] = trip_planner.WALK_M_DEFAULT,
) -> dict:
    """Líneas que pasan cerca del origen y del destino, la más rápida primero (caminar + viajar)."""
    origin, destination = (from_lat, from_lon), (to_lat, to_lon)
    straight, options = await trip_planner.plan(db, origin, destination, walk_m)
    combos = []
    if straight >= trip_planner.MIN_TRIP_M:
        combos = await trip_planner.combos(db, origin, destination, options[0].total_min if options else None)
    return {
        "combos": combos,
        "straight_m": round(straight),
        "options": [
            {**o.__dict__, "board": {"lat": o.board[0], "lon": o.board[1]}, "alight": {"lat": o.alight[0], "lon": o.alight[1]}}
            for o in options
        ],
    }


@router.get("/places/search", response_model=list[PlaceRead])
async def search_places(
    request: Request, redis: RedisClient, q: Annotated[str, Query(min_length=3, max_length=80)]
) -> list[dict]:
    """Lugares del Gran Santiago con ese nombre o dirección (OpenStreetMap)."""
    key = f"ratelimit:places:{request.client.host if request.client else 'unknown'}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > MAX_PLACE_SEARCHES_PER_MINUTE:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiadas búsquedas. Espera un minuto.")
    try:
        return await places.search(redis, q)
    except places.PlacesUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "El buscador de lugares no responde. Intenta de nuevo.") from None
