from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SightingIn(BaseModel):
    """Pasajero: "pasó un colectivo de esta línea" donde está esperando."""

    line_code: str = Field(min_length=1, max_length=20, examples=["233025"])
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class FrequencyRead(BaseModel):
    line_code: str
    # Minutos entre pasadas (mediana). null si aún no hay marcas suficientes en este lugar.
    headway_min: float | None
    samples: int  # intervalos con que se calculó
    # "hour": misma franja horaria (tipo de día y ±1 h) · "all": todas las horas
    scope: Literal["hour", "all"] | None
    sightings: int  # marcas en este lugar en las últimas 4 semanas
    last_seen_at: datetime | None


class SightingRead(BaseModel):
    id: int
    line_code: str
    seen_at: datetime
    frequency: FrequencyRead  # ya contando esta marca
