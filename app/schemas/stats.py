from datetime import datetime

from pydantic import BaseModel


class StatsTotals(BaseModel):
    contributors: int  # pasajeros registrados (anónimos), desde siempre
    active_contributors: int  # los que marcaron o grabaron en el período
    sightings: int  # marcas "pasó un colectivo" en el período
    sightings_7d: int
    recordings: dict[str, int]  # grabaciones del período por estado (pending_review, approved, …)
    lines_active: int
    lines_pending: int  # propuestas por pasajeros, sin revisar
    lines_with_recorded_route: int
    recorded_km: float  # largo total de los recorridos grabados y aprobados (activos)


class LineStats(BaseModel):
    line_code: str
    name: str | None
    description: str | None
    commune: str | None
    sightings: int
    recordings_approved: int
    recordings_pending: int
    recorded_km: float
    last_activity: datetime | None


class ZoneStats(BaseModel):
    lat: float  # centro de la celda de ~1 km
    lon: float
    sightings: int
    recordings: int  # grabaciones que empezaron en la celda
    lines: int  # líneas distintas con marcas o grabaciones
    commune: str | None  # la comuna más común de esas líneas


class StatsRead(BaseModel):
    days: int
    totals: StatsTotals
    lines: list[LineStats]
    zones: list[ZoneStats]
