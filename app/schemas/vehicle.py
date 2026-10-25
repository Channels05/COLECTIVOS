import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Formatos chilenos: antiguo "AB1234" y actual "BCDF12".
PLATE_PATTERN = r"^([A-Z]{2}\d{4}|[A-Z]{4}\d{2})$"


class VehicleCreate(BaseModel):
    plate: str = Field(pattern=PLATE_PATTERN, examples=["BCDF12"])
    line_code: str = Field(min_length=1, max_length=20, examples=["6045"])
    max_capacity: int = Field(default=4, ge=1, le=6)

    @field_validator("plate", mode="before")
    @classmethod
    def normalize_plate(cls, v: str) -> str:
        # "bc-df·12" -> "BCDF12"
        return "".join(ch for ch in str(v).upper() if ch.isalnum())


class VehicleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plate: str
    line_code: str
    max_capacity: int
    is_active: bool
    created_at: datetime


class VehicleCreated(VehicleRead):
    device_token: str = Field(description="Se muestra solo una vez. Configurarlo en la app del conductor.")
