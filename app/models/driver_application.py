import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, SmallInteger, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.route import _pg_enum


class ApplicationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DriverApplication(Base):
    """Un conductor pide desde la app que se registre su auto.

    El teléfono genera su propio secreto y manda solo el hash: al aprobar, el vehículo se crea con
    ese mismo hash como token del dispositivo. Así el servidor nunca guarda el token en claro ni
    hace falta entregarle un QR al conductor.
    """

    __tablename__ = "driver_applications"
    __table_args__ = (Index("ix_driver_applications_status", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plate: Mapped[str] = mapped_column(String(6))
    line_code: Mapped[str] = mapped_column(String(20))
    max_capacity: Mapped[int] = mapped_column(SmallInteger, default=4)
    note: Mapped[str | None] = mapped_column(String(200))  # p. ej. el letrero o cómo contactarlo
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[ApplicationStatus] = mapped_column(
        _pg_enum(ApplicationStatus, "application_status"), default=ApplicationStatus.PENDING
    )
    review_note: Mapped[str | None] = mapped_column(Text)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
