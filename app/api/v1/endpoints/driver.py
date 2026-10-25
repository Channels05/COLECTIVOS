from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from app.api.deps import AuthenticatedVehicle, RedisClient, Validator
from app.core.config import get_settings
from app.models import Vehicle
from app.schemas.location import LocationAck, LocationUpdateIn, RouteStatus
from app.schemas.vehicle import VehicleRead
from app.services import tracking

router = APIRouter(prefix="/vehicles", tags=["conductor"])


@router.get("/{vehicle_id}", response_model=VehicleRead)
async def get_own_vehicle(vehicle: AuthenticatedVehicle) -> Vehicle:
    """La app del conductor lo usa al iniciar sesión: valida el token y obtiene patente/línea/capacidad."""
    return vehicle


@router.post("/{vehicle_id}/location", response_model=LocationAck)
async def report_location(
    payload: LocationUpdateIn,
    vehicle: AuthenticatedVehicle,
    redis: RedisClient,
    validator: Validator,
) -> LocationAck:
    """Ping de la app del conductor (cada ~5 s): ubicación + cupos disponibles.

    Camino caliente: validación Pydantic -> lookup del vehículo -> chequeo Shapely en
    memoria -> 1 pipeline a Redis. Sin escrituras síncronas en PostgreSQL.
    """
    settings = get_settings()
    if payload.available_seats > vehicle.max_capacity:
        raise HTTPException(
            422,
            f"available_seats ({payload.available_seats}) supera la capacidad ({vehicle.max_capacity})",
        )

    live = (datetime.now(UTC) - payload.timestamp).total_seconds() <= settings.max_live_age_s
    check = validator.check(vehicle.line_code, payload.lat, payload.lon, at=payload.timestamp)
    strikes = await tracking.record_location(
        redis, vehicle, payload, check, ttl_s=settings.vehicle_ttl_s, live=live
    )

    if check is None:
        route_status = RouteStatus.NO_ROUTE
    elif check.on_route:
        route_status = RouteStatus.ON_ROUTE
    elif strikes >= settings.off_route_strikes:
        # Aquí se engancharía la notificación (fiscalizador / dashboard de la ONG).
        route_status = RouteStatus.OFF_ROUTE
    else:
        route_status = RouteStatus.DEVIATING

    return LocationAck(
        accepted_live=live,
        route_status=route_status,
        distance_to_route_m=None if check is None else check.distance_m,
    )
