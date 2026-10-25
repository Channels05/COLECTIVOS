"""Piezas comunes a los importadores de trazados (OSM, GORE RM)."""

from shapely.geometry import MultiLineString
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.db.session import SessionLocal, engine
from app.models import Line, LineSource, LineStatus, Route, RouteSource

_UPDATABLE = ("source_date", "line_code", "direction", "kind", "name", "commune", "geom", "source_attrs", "is_active")


def to_multilinestring(geom: BaseGeometry) -> MultiLineString | None:
    """Normaliza a MULTILINESTRING (el tipo de la columna). None si no es una línea (ej. paraderos)."""
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "LineString":
        return MultiLineString([geom])
    if geom.geom_type == "MultiLineString":
        return geom
    return None


def ewkt(geom: BaseGeometry) -> str:
    return f"SRID=4326;{geom.wkt}"


async def upsert_routes(rows: list[dict], source: RouteSource) -> tuple[int, int]:
    """Inserta/actualiza por (source, source_id) y desactiva los tramos que desaparecieron.

    La desactivación se limita a las líneas presentes en `rows`: importar solo La Florida no
    apaga las líneas de otras comunas. Devuelve (upserts, desactivados).
    """
    if not rows:
        return 0, 0
    stmt = insert(Route).values([{**row, "source": source} for row in rows])
    stmt = stmt.on_conflict_do_update(
        constraint="uq_routes_source", set_={c: stmt.excluded[c] for c in _UPDATABLE}
    )
    deactivate = (
        update(Route)
        .where(
            Route.source == source,
            Route.line_code.in_({r["line_code"] for r in rows}),
            Route.source_id.not_in([r["source_id"] for r in rows]),
            Route.is_active.is_(True),
        )
        .values(is_active=False)
    )
    # Catálogo de líneas: las nuevas entran como oficiales (las existentes no se tocan).
    communes: dict[str, str] = {}
    for row in rows:
        communes.setdefault(row["line_code"], row.get("commune"))
    new_lines = (
        insert(Line)
        .values([
            {"code": code, "commune": commune, "source": LineSource.OFFICIAL, "status": LineStatus.ACTIVE}
            for code, commune in communes.items()
        ])
        .on_conflict_do_nothing(index_elements=[Line.code])
    )
    async with SessionLocal() as session, session.begin():
        await session.execute(new_lines)
        await session.execute(stmt)
        deactivated = (await session.execute(deactivate)).rowcount
    await engine.dispose()
    return len(rows), deactivated
