import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, SmallInteger, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (CheckConstraint("max_capacity BETWEEN 1 AND 6", name="ck_vehicles_capacity"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plate: Mapped[str] = mapped_column(String(6), unique=True)  # patente normalizada: "BCDF12" / "AB1234"
    # Código de la línea (ej. "6045"). Debe calzar con Route.line_code; en una iteración
    # posterior conviene normalizarlo a una tabla `lines` con FK.
    line_code: Mapped[str] = mapped_column(String(20), index=True)
    max_capacity: Mapped[int] = mapped_column(SmallInteger, default=4)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    device_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
