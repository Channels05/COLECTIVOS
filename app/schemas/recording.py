from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models import LineStatus, RecordingStatus, RouteDirection


class RecordingStart(BaseModel):
    direction: RouteDirection


class ContributionStart(BaseModel):
    """Pasajero: en qué línea va y, si lo sabe, el sentido."""

    line_code: str = Field(min_length=1, max_length=20)
    direction: RouteDirection | None = None
    comment: str | None = Field(default=None, max_length=300, examples=["Va hacia Metro Bellavista"])
    sign_text: str | None = Field(default=None, max_length=120, examples=["Metro Bellavista – Av. La Florida"])


class PointIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    recorded_at: datetime
    accuracy_m: float | None = Field(default=None, ge=0)


class PointBatch(BaseModel):
    # La app sube cada ~30 s; 500 cubre varios minutos sin conexión.
    points: list[PointIn] = Field(min_length=1, max_length=500)


class PointBatchAck(BaseModel):
    total_points: int


class RecordingReview(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class RecordingApproval(RecordingReview):
    # Obligatorio si quien grabó (un pasajero) no indicó el sentido.
    direction: RouteDirection | None = None
    # Lo que dice el letrero (p. ej. el que anotó el pasajero): queda como nombre de la línea.
    line_name: str | None = Field(default=None, max_length=120)
    # Desactiva lo grabado antes en ese sentido (para corregir un trazado malo) en vez de sumarse.
    replace_previous: bool = False


class RecordingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_code: str
    direction: RouteDirection | None
    comment: str | None = None
    sign_text: str | None = None
    status: RecordingStatus
    started_at: datetime
    finished_at: datetime | None
    stats: dict[str, Any] | None
    review_note: str | None
    route_id: int | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warnings(self) -> list[str]:
        return list((self.stats or {}).get("warnings", []))


class RecordingSummary(RecordingRead):
    """Para la lista del administrador: quién grabó (patente del auto o pasajero) y la línea."""

    recorded_by: Literal["driver", "passenger"]
    plate: str | None
    line_name: str | None
    line_status: LineStatus | None
    line_has_official_route: bool


class RecordingDetail(RecordingSummary):
    """Geometrías en GeoJSON (WGS84) para dibujar en el mapa de revisión."""

    raw: dict[str, Any] | None
    proposed: dict[str, Any] | None
