from datetime import datetime

from pydantic import BaseModel, Field


class ClientErrorIn(BaseModel):
    """La app reporta un error de JavaScript. Los textos largos se recortan en vez de rechazarse."""

    message: str = Field(min_length=1, max_length=5_000)
    stack: str | None = Field(default=None, max_length=50_000)
    fatal: bool = False
    source: str | None = Field(default=None, max_length=40)
    screen: str | None = Field(default=None, max_length=300)
    occurred_at: datetime | None = None
    app_version: str | None = Field(default=None, max_length=20)
    update_id: str | None = Field(default=None, max_length=64)
    platform: str | None = Field(default=None, max_length=20)
    os_version: str | None = Field(default=None, max_length=20)
    device: str | None = Field(default=None, max_length=80)


class ClientErrorBatch(BaseModel):
    errors: list[ClientErrorIn] = Field(min_length=1, max_length=20)


class ErrorGroupRead(BaseModel):
    """Errores con el mismo mensaje, del más reciente al más viejo."""

    message: str
    count: int
    fatal: int  # cuántos cerraron la app
    first_seen: datetime
    last_seen: datetime
    app_versions: list[str]
    devices: list[str]
    screens: list[str]
    last_stack: str | None
    last_source: str | None
    last_update_id: str | None
