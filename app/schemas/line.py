import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import LineSource, LineStatus


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str | None
    description: str | None
    commune: str | None
    source: LineSource
    status: LineStatus


class LineHitRead(LineRead):
    has_route: bool  # tiene trazado activo (oficial o grabado)
    distance_m: float | None  # distancia del trazado al pasajero, si buscó por ubicación


class LineProposal(BaseModel):
    """Pasajero: una línea que no aparece en la búsqueda."""

    name: str = Field(min_length=2, max_length=120, examples=["Metro Bellavista – Av. La Florida"])
    description: str | None = Field(default=None, max_length=300, examples=["Sale de Metro Bellavista por Vicuña Mackenna"])
    commune: str | None = Field(default=None, max_length=60, examples=["La Florida"])


class LineReview(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    # Al aprobar, el administrador puede corregir lo que escribió el pasajero.
    name: str | None = Field(default=None, max_length=120)
    commune: str | None = Field(default=None, max_length=60)


class LineEdit(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    commune: str | None = Field(default=None, max_length=60)

    @field_validator("name", "commune", mode="before")
    @classmethod
    def limpiar_texto(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        value = value.strip()
        return value or None


class LineAdminRead(LineRead):
    created_at: datetime
    created_by: uuid.UUID | None
    review_note: str | None
    recordings: int  # grabaciones asociadas (de cualquier estado)


class ContributorCreated(BaseModel):
    id: uuid.UUID
    token: str = Field(description="Se entrega una sola vez; la app lo guarda en el almacenamiento seguro.")
