import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LocationUpdate(Base):
    """Histórico de pings GPS. NO se escribe en el request del conductor: llega en lotes
    desde Redis Streams vía `app.workers.location_persister` (ver services/tracking.py).

    Es la tabla de mayor volumen (1 fila / 5 s / vehículo): en producción, particionar por mes
    (pg_partman) y exportar a Parquet para DuckDB.
    """

    __tablename__ = "location_updates"
    __table_args__ = (
        # Único => el worker puede reintentar con ON CONFLICT DO NOTHING (idempotente).
        # También es el índice para "trayectoria del vehículo X entre t1 y t2".
        Index("ux_location_vehicle_time", "vehicle_id", "recorded_at", unique=True),
        # BRIN: diminuto y muy eficiente para filtros por rango de tiempo en datos append-only.
        Index("ix_location_recorded_brin", "recorded_at", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # hora del GPS (teléfono)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Sin índice GIST a propósito: encarece cada insert y las queries espaciales sobre el
    # histórico se hacen en batch (DuckDB / PostGIS offline).
    geom: Mapped[WKBElement] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    available_seats: Mapped[int] = mapped_column(SmallInteger)
    speed_kmh: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    on_route: Mapped[bool | None] = mapped_column(Boolean)  # None = la línea no tenía trazado cargado
    distance_to_route_m: Mapped[float | None] = mapped_column(Float)
