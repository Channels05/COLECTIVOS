"""Catálogo de líneas: búsqueda para el pasajero, propuestas de líneas nuevas y su revisión."""

from dataclasses import dataclass
from datetime import UTC, datetime

from geoalchemy2 import Geography
from sqlalchemy import cast, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Contributor,
    Line,
    LineSource,
    LineStatus,
    RecordingStatus,
    Route,
    RouteRecording,
    contributed_line_seq,
)
from app.services.errors import ServiceError

# Un pasajero no puede tener más de esto en revisión a la vez (freno simple contra spam).
MAX_PENDING_LINES_PER_CONTRIBUTOR = 3

_HAS_ROUTE = exists().where(Route.line_code == Line.code, Route.is_active.is_(True)).correlate(Line)


@dataclass
class LineHit:
    line: Line
    has_route: bool
    distance_m: float | None


async def ensure_line(db: AsyncSession, code: str, source: LineSource, commune: str | None = None) -> None:
    """Crea la línea en el catálogo si no existe (al registrar un vehículo o importar trazados)."""
    await db.execute(
        insert(Line)
        .values(code=code, source=source, status=LineStatus.ACTIVE, commune=commune)
        .on_conflict_do_nothing(index_elements=[Line.code])
    )


async def search(
    db: AsyncSession,
    q: str | None,
    lat: float | None,
    lon: float | None,
    radius_m: float,
    limit: int,
) -> list[LineHit]:
    """Líneas activas que calzan con `q` (código, letrero o descripción).

    Con lat/lon, solo las que tienen un trazado a menos de `radius_m` y ordenadas por cercanía:
    es la pregunta "¿en qué colectivo vas?" del pasajero que está subiendo a uno.
    """
    query = select(Line, _HAS_ROUTE.label("has_route")).where(Line.status == LineStatus.ACTIVE)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(Line.code.ilike(pattern), Line.name.ilike(pattern), Line.description.ilike(pattern))
        )

    if lat is None or lon is None:
        rows = await db.execute(query.order_by(Line.code).limit(limit))
        return [LineHit(line, has_route, None) for line, has_route in rows]

    here = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
    # Primer filtro en grados con el índice GIST de routes.geom (0,01° ≈ 1,1 km de latitud),
    # distancia exacta en metros con geography.
    nearest = (
        select(
            Route.line_code,
            func.min(func.ST_Distance(cast(Route.geom, Geography), cast(here, Geography))).label("distance_m"),
        )
        .where(Route.is_active.is_(True), func.ST_DWithin(Route.geom, here, radius_m / 90_000))
        .group_by(Route.line_code)
        .subquery()
    )
    rows = await db.execute(
        query.add_columns(nearest.c.distance_m)
        .join(nearest, nearest.c.line_code == Line.code)
        .where(nearest.c.distance_m <= radius_m)
        .order_by(nearest.c.distance_m)
        .limit(limit)
    )
    return [LineHit(line, has_route, distance) for line, has_route, distance in rows]


async def propose(
    db: AsyncSession, contributor: Contributor, name: str, description: str | None, commune: str | None
) -> Line:
    """Un pasajero agrega una línea que no está en el catálogo. Queda pendiente de revisión,
    pero quien la propuso puede grabar su recorrido de inmediato."""
    pending = await db.scalar(
        select(func.count()).where(Line.created_by == contributor.id, Line.status == LineStatus.PENDING_REVIEW)
    )
    if pending >= MAX_PENDING_LINES_PER_CONTRIBUTOR:
        raise ServiceError(
            f"Ya tienes {pending} líneas esperando revisión. Podrás agregar otra cuando un administrador las revise.",
            429,
        )
    number = await db.scalar(select(contributed_line_seq.next_value()))
    line = Line(
        code=f"P{number:05d}",
        name=name.strip(),
        description=(description or "").strip() or None,
        commune=(commune or "").strip() or None,
        source=LineSource.CONTRIBUTED,
        status=LineStatus.PENDING_REVIEW,
        created_by=contributor.id,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


async def usable_by(db: AsyncSession, contributor: Contributor, code: str) -> Line:
    """La línea que un pasajero quiere grabar: activa, o pendiente si la propuso él mismo."""
    line = await db.get(Line, code)
    if line is None or line.status == LineStatus.REJECTED:
        raise ServiceError(f"La línea {code} no existe.", 404)
    if line.status == LineStatus.PENDING_REVIEW and line.created_by != contributor.id:
        raise ServiceError(f"La línea {code} todavía está en revisión.", 409)
    return line


async def _pending(db: AsyncSession, code: str) -> Line:
    line = await db.get(Line, code)
    if line is None:
        raise ServiceError("Línea no encontrada.", 404)
    if line.status != LineStatus.PENDING_REVIEW:
        raise ServiceError(f"Solo se revisan líneas pendientes (esta está '{line.status}').")
    return line


def activate(line: Line, note: str | None) -> None:
    line.status = LineStatus.ACTIVE
    line.review_note = note
    line.reviewed_at = datetime.now(UTC)


async def approve(db: AsyncSession, code: str, note: str | None, name: str | None, commune: str | None) -> Line:
    """Acepta la línea propuesta (el administrador puede corregir el letrero y la comuna)."""
    line = await _pending(db, code)
    if name:
        line.name = name.strip()
    if commune:
        line.commune = commune.strip()
    activate(line, note)
    await db.commit()
    await db.refresh(line)
    return line


async def reject(db: AsyncSession, code: str, note: str | None) -> Line:
    """Descarta la línea (duplicada, inexistente) junto con sus grabaciones aún no aprobadas."""
    line = await _pending(db, code)
    now = datetime.now(UTC)
    line.status = LineStatus.REJECTED
    line.review_note = note
    line.reviewed_at = now
    await db.execute(
        update(RouteRecording)
        .where(
            RouteRecording.line_code == code,
            RouteRecording.status.in_([RecordingStatus.RECORDING, RecordingStatus.PENDING_REVIEW]),
        )
        .values(
            status=RecordingStatus.REJECTED,
            review_note=f"Línea rechazada: {note}" if note else "Línea rechazada",
            reviewed_at=now,
            finished_at=func.coalesce(RouteRecording.finished_at, now),
        )
    )
    await db.commit()
    await db.refresh(line)
    return line
