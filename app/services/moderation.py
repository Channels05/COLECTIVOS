"""Moderación de aportes: ver qué mandó un pasajero (anónimo) y bloquearlo si manda basura.

Bloquear impide que siga aportando (la API le responde 403). Con `purge`, además se deshacen sus
aportes: grabaciones pendientes rechazadas, sus tramos ya aprobados desactivados, sus marcas de
paso borradas y sus líneas propuestas sin revisar rechazadas.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contributor, Line, LineStatus, RecordingStatus, Route, RouteRecording, RouteSource, Sighting
from app.services.errors import ServiceError

PURGE_NOTE = "Aportante bloqueado por el administrador"


async def profile(db: AsyncSession, contributor_id: uuid.UUID) -> dict:
    contributor = await db.get(Contributor, contributor_id)
    if contributor is None:
        raise ServiceError("Aportante no encontrado.", 404)
    by_status = dict(
        (status.value if hasattr(status, "value") else status, n)
        for status, n in await db.execute(
            select(RouteRecording.status, func.count())
            .where(RouteRecording.contributor_id == contributor_id)
            .group_by(RouteRecording.status)
        )
    )
    sightings, last_sighting = (
        await db.execute(
            select(func.count(), func.max(Sighting.seen_at)).where(Sighting.contributor_id == contributor_id)
        )
    ).one()
    lines_proposed = await db.scalar(select(func.count()).where(Line.created_by == contributor_id))
    return {
        "id": contributor.id,
        "created_at": contributor.created_at,
        "is_blocked": contributor.is_blocked,
        "recordings": by_status,
        "sightings": sightings,
        "last_sighting_at": last_sighting,
        "lines_proposed": lines_proposed or 0,
    }


async def set_blocked(db: AsyncSession, contributor_id: uuid.UUID, blocked: bool, purge: bool = False) -> dict:
    contributor = await db.get(Contributor, contributor_id)
    if contributor is None:
        raise ServiceError("Aportante no encontrado.", 404)
    contributor.is_blocked = blocked
    result = {"pending_rejected": 0, "routes_deactivated": 0, "sightings_deleted": 0, "lines_rejected": 0}
    if blocked and purge:
        now = datetime.now(UTC)
        pending = await db.execute(
            update(RouteRecording)
            .where(
                RouteRecording.contributor_id == contributor_id,
                RouteRecording.status.in_([RecordingStatus.PENDING_REVIEW, RecordingStatus.RECORDING]),
            )
            .values(status=RecordingStatus.REJECTED, review_note=PURGE_NOTE, reviewed_at=now)
        )
        approved_routes = select(RouteRecording.route_id).where(
            RouteRecording.contributor_id == contributor_id, RouteRecording.route_id.is_not(None)
        )
        routes = await db.execute(
            update(Route)
            .where(Route.source == RouteSource.RECORDED, Route.id.in_(approved_routes), Route.is_active.is_(True))
            .values(is_active=False)
        )
        sightings = await db.execute(delete(Sighting).where(Sighting.contributor_id == contributor_id))
        lines = await db.execute(
            update(Line)
            .where(Line.created_by == contributor_id, Line.status == LineStatus.PENDING_REVIEW)
            .values(status=LineStatus.REJECTED, review_note=PURGE_NOTE, reviewed_at=now)
        )
        result = {
            "pending_rejected": pending.rowcount,
            "routes_deactivated": routes.rowcount,
            "sightings_deleted": sightings.rowcount,
            "lines_rejected": lines.rowcount,
        }
    await db.commit()
    return result
