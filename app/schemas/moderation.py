import uuid
from datetime import datetime

from pydantic import BaseModel


class ContributorProfile(BaseModel):
    id: uuid.UUID
    created_at: datetime
    is_blocked: bool
    recordings: dict[str, int]  # por estado: pending_review, approved, rejected, …
    sightings: int
    last_sighting_at: datetime | None
    lines_proposed: int


class BlockRequest(BaseModel):
    # Además de bloquear, deshacer sus aportes (grabaciones, tramos aprobados, marcas, líneas).
    purge: bool = False


class BlockResult(BaseModel):
    pending_rejected: int
    routes_deactivated: int
    sightings_deleted: int
    lines_rejected: int
