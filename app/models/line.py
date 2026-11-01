import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Sequence, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.route import _pg_enum


class LineSource(StrEnum):
    OFFICIAL = "official"  # viene de un registro importado (GORE RM, OSM)
    ADMIN = "admin"  # la creó un administrador (p. ej. al registrar un vehículo de una línea sin trazado)
    CONTRIBUTED = "contributed"  # la propuso un pasajero desde la app


class LineStatus(StrEnum):
    ACTIVE = "active"
    PENDING_REVIEW = "pending_review"  # propuesta por un pasajero, esperando al administrador
    REJECTED = "rejected"  # duplicada, inexistente o basura


# Códigos de las líneas propuestas por pasajeros: "P00001", "P00002"... No chocan con los del
# registro (numéricos, ej. "233010").
contributed_line_seq = Sequence("contributed_line_seq", metadata=Base.metadata)


class Line(Base):
    """Catálogo de líneas de colectivos. `routes.line_code` y `vehicles.line_code` apuntan a `code`.

    (Sin FK por ahora: los importadores cargan rutas de líneas que aún no están aquí; el
    importador y el registro de vehículos crean la línea si falta.)
    """

    __tablename__ = "lines"
    __table_args__ = (Index("ix_lines_status", "status"),)

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    # Lo que dice el letrero del auto (ej. "Metro Bellavista – Av. La Florida"). El registro
    # oficial no lo trae: lo completan pasajeros o administradores.
    name: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(300))  # desde dónde hasta dónde, referencias
    commune: Mapped[str | None] = mapped_column(String(60))
    source: Mapped[LineSource] = mapped_column(_pg_enum(LineSource, "line_source"))
    status: Mapped[LineStatus] = mapped_column(_pg_enum(LineStatus, "line_status"), default=LineStatus.ACTIVE)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contributors.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Tarifa (pesos) y horario: los informan pasajeros y los confirma el administrador.
    fare_day: Mapped[int | None] = mapped_column(Integer)
    fare_night: Mapped[int | None] = mapped_column(Integer)
    first_departure: Mapped[str | None] = mapped_column(String(5))  # "06:00"
    last_departure: Mapped[str | None] = mapped_column(String(5))  # "23:30"
    info_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
