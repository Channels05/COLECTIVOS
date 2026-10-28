from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import RedisClient, require_admin
from app.core.config import get_settings
from app.services import tracking

admin_router = APIRouter(prefix="/admin/fleet", tags=["admin"], dependencies=[Depends(require_admin)])


class FleetVehicle(BaseModel):
    vehicle_id: str
    plate: str
    line_code: str
    lat: float
    lon: float
    available_seats: int
    max_capacity: int
    speed_kmh: float | None
    heading: float | None
    on_route: bool | None  # None = la línea no tiene trazado
    distance_to_route_m: float | None
    off_route_strikes: int
    route_status: str  # on_route | deviating | off_route | no_route (igual que el ack del conductor)
    last_seen: datetime


@admin_router.get("", response_model=list[FleetVehicle])
async def get_fleet(redis: RedisClient) -> list[dict]:
    """Todos los vehículos transmitiendo ahora, con su estado de ruta (mapa de la flota en /admin)."""
    strikes_needed = get_settings().off_route_strikes
    vehicles = await tracking.fleet(redis)
    for v in vehicles:
        v["route_status"] = (
            "no_route" if v["on_route"] is None
            else "on_route" if v["on_route"]
            else "off_route" if v["off_route_strikes"] >= strikes_needed
            else "deviating"
        )
    return vehicles
