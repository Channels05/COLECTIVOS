import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.route import _pg_enum


class ReportStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"  # el administrador copió los datos a la línea
    DISCARDED = "discarded"


class LineInfoReport(Base):
    """Un pasajero informa la tarifa o el horario de una línea (lo que paga, a qué hora pasa el
    primer y el último auto). Queda pendiente hasta que el administrador lo aplica en /admin."""

    __tablename__ = "line_info_reports"
    __table_args__ = (Index("ix_line_info_reports_status", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    line_code: Mapped[str] = mapped_column(String(20), ForeignKey("lines.code", ondelete="CASCADE"))
    contributor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contributors.id", ondelete="CASCADE"), index=True)
    fare_day: Mapped[int | None] = mapped_column(Integer)
    fare_night: Mapped[int | None] = mapped_column(Integer)
    first_departure: Mapped[str | None] = mapped_column(String(5))
    last_departure: Mapped[str | None] = mapped_column(String(5))
    note: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[ReportStatus] = mapped_column(_pg_enum(ReportStatus, "report_status"), default=ReportStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LineNotice(Base):
    """Aviso del administrador a los pasajeros ("Desvío por feria los domingos en Av. X"), de una
    línea o de todas (line_code vacío). Se muestra mientras está vigente."""

    __tablename__ = "line_notices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    line_code: Mapped[str | None] = mapped_column(String(20), ForeignKey("lines.code", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(String(280))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
