from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbSession, RedisClient, require_admin
from app.core.config import get_settings
from app.services import tracking

admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


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


@admin_router.get("/fleet", response_model=list[FleetVehicle])
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


class HourOccupancy(BaseModel):
    hour: int  # 0-23, hora de Santiago
    occupancy: float  # 0 = vacío, 1 = lleno (promedio de los pings)
    samples: int


class LineOccupancy(BaseModel):
    line_code: str
    vehicles: int
    samples: int
    hours: list[HourOccupancy]


# Ocupación = 1 - cupos libres / asientos, promediada por línea y hora del día (hora de Santiago),
# con el historial de posiciones de los conductores (se guarda LOCATION_RETENTION_DAYS días).
_OCCUPANCY = text("""
    SELECT v.line_code,
           extract(hour FROM lu.recorded_at AT TIME ZONE 'America/Santiago')::int AS hour,
           avg(1 - lu.available_seats::float / greatest(v.max_capacity, 1)) AS occupancy,
           count(*) AS samples,
           count(DISTINCT lu.vehicle_id) AS vehicles
    FROM location_updates lu JOIN vehicles v ON v.id = lu.vehicle_id
    WHERE lu.recorded_at > now() - make_interval(days => :days)
    GROUP BY v.line_code, hour
    ORDER BY v.line_code, hour
""")


@admin_router.get("/occupancy", response_model=list[LineOccupancy])
async def get_occupancy(db: DbSession, days: int = Query(default=30, ge=1, le=90)) -> list[dict]:
    """A qué horas van más llenos los autos de cada línea (para "Ocupación" en /admin)."""
    lines: dict[str, dict] = {}
    for row in await db.execute(_OCCUPANCY, {"days": days}):
        line = lines.setdefault(row.line_code, {"line_code": row.line_code, "vehicles": 0, "samples": 0, "hours": []})
        line["hours"].append({"hour": row.hour, "occupancy": round(row.occupancy, 3), "samples": row.samples})
        line["samples"] += row.samples
        line["vehicles"] = max(line["vehicles"], row.vehicles)
    return sorted(lines.values(), key=lambda l: -l["samples"])
