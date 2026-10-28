import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.core.config import get_settings


class LocationUpdateIn(BaseModel):
    """Payload que la app del conductor envía cada ~5 s."""

    lat: float = Field(ge=-90, le=90, examples=[-33.5225])
    lon: float = Field(ge=-180, le=180, examples=[-70.5985])
    timestamp: AwareDatetime = Field(description="Hora del fix GPS, con zona horaria (ISO 8601).")
    available_seats: int = Field(ge=0, le=6, examples=[2])
    speed_kmh: float | None = Field(default=None, ge=0, le=200)
    heading: float | None = Field(default=None, ge=0, lt=360, description="Rumbo en grados (0 = norte).")

    @field_validator("timestamp")
    @classmethod
    def not_in_future(cls, v: datetime) -> datetime:
        if v > datetime.now(UTC) + timedelta(seconds=get_settings().max_clock_skew_s):
            raise ValueError("timestamp en el futuro: revisar el reloj del dispositivo")
        return v

    @model_validator(mode="after")
    def inside_service_area(self) -> "LocationUpdateIn":
        min_lon, min_lat, max_lon, max_lat = get_settings().service_bbox
        if not (min_lon <= self.lon <= max_lon and min_lat <= self.lat <= max_lat):
            raise ValueError("ubicación fuera del área de servicio")
        return self


class RouteStatus(StrEnum):
    ON_ROUTE = "on_route"
    DEVIATING = "deviating"  # fuera del corredor, pero aún no suma los strikes para alertar
    OFF_ROUTE = "off_route"  # desvío confirmado
    NO_ROUTE = "no_route"  # la línea no tiene trazado cargado: no se puede validar


class LocationAck(BaseModel):
    accepted_live: bool = Field(description="False si el ping era antiguo y solo se guardó en el histórico.")
    route_status: RouteStatus
    distance_to_route_m: float | None


class NearbyVehicle(BaseModel):
    vehicle_id: uuid.UUID
    plate: str
    line_code: str
    lat: float
    lon: float
    distance_m: float
    available_seats: int
    max_capacity: int
    heading: float | None
    on_route: bool | None
    last_seen: datetime
    # Metros por el recorrido de la línea hasta el pasajero (si mandó su ubicación y el auto viene
    # hacia él por el trazado); la app calcula la llegada con esto en vez de la línea recta.
    route_distance_m: float | None = None


class NearbyResponse(BaseModel):
    radius_m: int
    count: int
    vehicles: list[NearbyVehicle]
