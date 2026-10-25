from datetime import date, datetime
from enum import StrEnum

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import Boolean, Date, DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class RouteDirection(StrEnum):
    IDA = "ida"
    REGRESO = "regreso"


class RouteKind(StrEnum):
    """Tipo de trazado autorizado (campo `recorrido` del registro del MTT / GORE RM)."""

    TRAZADO = "trazado"  # recorrido principal
    VIA_ALTERNATIVA = "via_alternativa"  # desvío autorizado: NO es salirse de la ruta
    BUCLE = "bucle"  # vuelta en un terminal
    NOCTURNO = "nocturno"  # trazado válido solo en horario nocturno


class RouteSource(StrEnum):
    OSM = "osm"  # relación de OpenStreetMap (Overpass)
    GORE_RM = "gore_rm"  # geoportal del Gobierno Regional Metropolitano
    RECORDED = "recorded"  # grabado con el GPS de un conductor y aprobado por un administrador


class Route(Base):
    """Un tramo de trazado legal de una línea: (línea, sentido, tipo).

    No guardamos el GeoJSON crudo: la geometría va a una columna PostGIS (indexable, consultable
    con ST_*) y los atributos originales de la fuente a `source_attrs` (JSONB) para trazabilidad.
    """

    __tablename__ = "routes"
    # Upsert idempotente por fuente: re-importar la misma fuente actualiza en vez de duplicar.
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_routes_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[RouteSource] = mapped_column(_pg_enum(RouteSource, "route_source"))
    source_id: Mapped[str] = mapped_column(String(100))  # "relation/123" (OSM) / objectid (GORE)
    # Fecha de corte de los datos de la fuente (el registro oficial cambia por resolución).
    source_date: Mapped[date | None] = mapped_column(Date)
    line_code: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[RouteDirection] = mapped_column(_pg_enum(RouteDirection, "route_direction"))
    kind: Mapped[RouteKind] = mapped_column(_pg_enum(RouteKind, "route_kind"), default=RouteKind.TRAZADO)
    name: Mapped[str | None] = mapped_column(String(200))
    commune: Mapped[str] = mapped_column(String(60), default="La Florida")
    # MULTILINESTRING porque un trazado suele venir en varios tramos.
    # SRID 4326 = lon/lat WGS84, el mismo de GeoJSON. spatial_index=True crea el índice GIST.
    geom: Mapped[WKBElement] = mapped_column(Geometry("MULTILINESTRING", srid=4326, spatial_index=True))
    source_attrs: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
