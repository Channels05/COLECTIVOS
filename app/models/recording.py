import uuid
from datetime import datetime
from enum import StrEnum

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.route import RouteDirection, _pg_enum


class RecordingStatus(StrEnum):
    RECORDING = "recording"  # grabando (ventana de tiempo abierta / recibiendo puntos)
    PENDING_REVIEW = "pending_review"  # terminada, con trazado propuesto, esperando al administrador
    APPROVED = "approved"  # aprobada: generó una Route con source="recorded"
    REJECTED = "rejected"  # descartada por el administrador
    CANCELLED = "cancelled"  # la canceló quien grababa (o no tenía puntos suficientes)


class RouteRecording(Base):
    """Una pasada por el recorrido de una línea (ida o regreso) para proponer su trazado.

    La graba un conductor O un pasajero (`vehicle_id` / `contributor_id`, exactamente uno):
    - Conductor: no hay canal de datos aparte; la grabación es una ventana de tiempo
      [started_at, finished_at] sobre los pings de su turno (tabla location_updates).
    - Pasajero: no envía pings de vehículo; la app sube sus puntos GPS en lotes a recording_points.
    """

    __tablename__ = "route_recordings"
    __table_args__ = (
        # Un vehículo graba a lo más un recorrido a la vez.
        Index(
            "ux_recording_active_per_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("status = 'recording'"),
        ),
        Index(
            "ux_recording_active_per_contributor",
            "contributor_id",
            unique=True,
            postgresql_where=text("status = 'recording'"),
        ),
        Index("ix_recording_status", "status"),
        CheckConstraint("(vehicle_id IS NULL) <> (contributor_id IS NULL)", name="ck_recording_one_author"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    contributor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contributors.id", ondelete="CASCADE"))
    line_code: Mapped[str] = mapped_column(String(20), index=True)
    # El pasajero puede no saber si va de ida o de regreso: lo decide el administrador al aprobar.
    direction: Mapped[RouteDirection | None] = mapped_column(_pg_enum(RouteDirection, "route_direction"))
    # Comentario de quien grabó (p. ej. "va hacia Metro Bellavista"): ayuda a elegir el sentido.
    comment: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[RecordingStatus] = mapped_column(
        _pg_enum(RecordingStatus, "recording_status"), default=RecordingStatus.RECORDING
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Puntos tal como llegaron (para que el administrador vea qué se grabó) y trazado propuesto.
    raw_geom: Mapped[WKBElement | None] = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=False))
    proposed_geom: Mapped[WKBElement | None] = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=False))
    # Métricas de calidad y advertencias del algoritmo (ver services/route_inference.py).
    stats: Mapped[dict | None] = mapped_column(JSONB)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id", ondelete="SET NULL"))


class RecordingPoint(Base):
    """Punto GPS de una grabación hecha por un pasajero (las del conductor usan location_updates)."""

    __tablename__ = "recording_points"
    # Reenviar un lote (la app reintenta si no recibió respuesta) no duplica puntos.
    __table_args__ = (UniqueConstraint("recording_id", "recorded_at", name="uq_recording_point_time"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("route_recordings.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    geom: Mapped[WKBElement] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    accuracy_m: Mapped[float | None] = mapped_column(Float)  # precisión que informa el teléfono
