import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Sighting(Base):
    """Un pasajero marcó "pasó un colectivo" de una línea donde está esperando.

    Con las marcas de muchos pasajeros en un mismo lugar se estima cada cuánto pasa cada línea
    (ver app/services/frequency.py). No dice nada del auto: solo la línea, el lugar y la hora.
    """

    __tablename__ = "sightings"
    __table_args__ = (Index("ix_sightings_line_seen", "line_code", "seen_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    line_code: Mapped[str] = mapped_column(String(20), ForeignKey("lines.code", ondelete="CASCADE"))
    contributor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contributors.id", ondelete="CASCADE"), index=True)
    geom: Mapped[WKBElement] = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    # Hora del servidor: la del teléfono puede estar corrida y aquí importan los minutos.
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
