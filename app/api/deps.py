import hmac
import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import verify_token
from app.db.session import get_db
from app.models import Contributor, Vehicle
from app.services.route_validator import RouteValidator

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_route_validator(request: Request) -> RouteValidator:
    return request.app.state.route_validator


RedisClient = Annotated[Redis, Depends(get_redis)]
Validator = Annotated[RouteValidator, Depends(get_route_validator)]


async def get_authenticated_vehicle(
    vehicle_id: uuid.UUID,
    db: DbSession,
    x_device_token: Annotated[str, Header()],
) -> Vehicle:
    # Lookup por PK (~1 ms). Con cientos de autos a 1 req/5 s es irrelevante; si crece,
    # cachear (id -> hash, línea, capacidad) en Redis o en memoria con TTL corto.
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None or not vehicle.is_active or not verify_token(x_device_token, vehicle.device_token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Vehículo no autorizado")
    return vehicle


AuthenticatedVehicle = Annotated[Vehicle, Depends(get_authenticated_vehicle)]


async def get_contributor(
    db: DbSession,
    x_contributor_id: Annotated[uuid.UUID, Header()],
    x_contributor_token: Annotated[str, Header()],
) -> Contributor:
    """Pasajero que aporta recorridos (anónimo: id + token generados por POST /contributors)."""
    contributor = await db.get(Contributor, x_contributor_id)
    if contributor is None or not verify_token(x_contributor_token, contributor.token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Aportante no reconocido")
    if contributor.is_blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Este teléfono no puede enviar aportes")
    return contributor


AuthenticatedContributor = Annotated[Contributor, Depends(get_contributor)]


def require_admin(x_admin_key: Annotated[str, Header()]) -> None:
    if not hmac.compare_digest(x_admin_key, get_settings().admin_api_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Se requiere clave de administrador")
