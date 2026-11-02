import uuid
from datetime import datetime
from enum import StrEnum

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.line_info import ReportStatus
from app.models.route import _pg_enum


class RouteReportKind(StrEnum):
    ROUTE_CHANGED = "route_changed"  # "ya no pasa por aquí" / cambió el recorrido
    NOT_RUNNING = "not_running"  # la línea ya no existe o no pasa a ciertas horas
    SIGN = "sign"  # el letrero dice otra cosa
    OTHER = "other"


class RouteReport(Base):
    """Un pasajero avisa que algo de una línea está mal (los trazados oficiales son de 2023).

    Queda pendiente hasta que el administrador lo revisa en /admin (resuelto o descartado).
    """

    __tablename__ = "route_reports"
    __table_args__ = (Index("ix_route_reports_status", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    line_code: Mapped[str] = mapped_column(String(20), ForeignKey("lines.code", ondelete="CASCADE"))
    contributor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contributors.id", ondelete="CASCADE"), index=True)
    kind: Mapped[RouteReportKind] = mapped_column(_pg_enum(RouteReportKind, "route_report_kind"))
    message: Mapped[str | None] = mapped_column(String(300))
    # Dónde estaba el pasajero al reportar (opcional): ayuda a ubicar el tramo que cambió.
    geom: Mapped[WKBElement | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    status: Mapped[ReportStatus] = mapped_column(_pg_enum(ReportStatus, "report_status"), default=ReportStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchMiss(Base):
    """Una búsqueda que no encontró nada: un lugar que OpenStreetMap no conoce o un viaje sin
    colectivo que sirva. Muestra en /admin dónde faltan líneas o recorridos.

    Es anónima: sin aportante ni IP, y los puntos se guardan redondeados a ~100 m.
    """

    __tablename__ = "search_misses"
    __table_args__ = (Index("ix_search_misses_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # "place" | "trip"
    query: Mapped[str | None] = mapped_column(String(80))
    origin: Mapped[WKBElement | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    destination: Mapped[WKBElement | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
