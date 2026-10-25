from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import RedisClient
from app.schemas.location import NearbyResponse
from app.services import tracking

router = APIRouter(prefix="/vehicles", tags=["pasajero"])


@router.get("/nearby", response_model=NearbyResponse)
async def nearby_vehicles(
    redis: RedisClient,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    radius_m: Annotated[int, Query(ge=100, le=5000)] = 2000,
    line_code: Annotated[str | None, Query(max_length=20)] = None,
    min_seats: Annotated[int, Query(ge=0, le=6)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NearbyResponse:
    """Colectivos activos alrededor del pasajero, del más cercano al más lejano.

    La app del pasajero puede hacer polling cada 5-10 s; si crece, migrar a WebSocket/SSE
    suscrito por geohash.
    """
    vehicles = await tracking.find_nearby(redis, lat, lon, radius_m, limit)
    vehicles = [
        v for v in vehicles
        if v.available_seats >= min_seats and (line_code is None or v.line_code == line_code)
    ]
    return NearbyResponse(radius_m=radius_m, count=len(vehicles), vehicles=vehicles)
