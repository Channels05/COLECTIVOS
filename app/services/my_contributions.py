import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Line,
    LineInfoReport,
    LineStatus,
    RecordingStatus,
    ReportStatus,
    RouteRecording,
    RouteReport,
    Sighting,
)


async def summary(db: AsyncSession, contributor_id: uuid.UUID) -> dict:
    """Calcula cuántos aportes hizo el pasajero y cuáles fueron aplicados."""
    sightings = await db.scalar(
        select(func.count()).where(Sighting.contributor_id == contributor_id)
    )
    sighting_lines = await db.scalar(
        select(func.count(func.distinct(Sighting.line_code))).where(
            Sighting.contributor_id == contributor_id
        )
    )
    recordings_approved = await db.scalar(
        select(func.count()).where(
            RouteRecording.contributor_id == contributor_id,
            RouteRecording.status == RecordingStatus.APPROVED,
        )
    )
    recordings_pending = await db.scalar(
        select(func.count()).where(
            RouteRecording.contributor_id == contributor_id,
            RouteRecording.status == RecordingStatus.PENDING_REVIEW,
        )
    )
    lines_approved = await db.scalar(
        select(func.count()).where(
            Line.created_by == contributor_id,
            Line.status == LineStatus.ACTIVE,
        )
    )
    line_reports_sent = await db.scalar(
        select(func.count()).where(LineInfoReport.contributor_id == contributor_id)
    )
    route_reports_sent = await db.scalar(
        select(func.count()).where(RouteReport.contributor_id == contributor_id)
    )
    line_reports_applied = await db.scalar(
        select(func.count()).where(
            LineInfoReport.contributor_id == contributor_id,
            LineInfoReport.status == ReportStatus.APPLIED,
        )
    )
    route_reports_applied = await db.scalar(
        select(func.count()).where(
            RouteReport.contributor_id == contributor_id,
            RouteReport.status == ReportStatus.APPLIED,
        )
    )
    return {
        "sightings": sightings or 0,
        "sighting_lines": sighting_lines or 0,
        "recordings_approved": recordings_approved or 0,
        "recordings_pending": recordings_pending or 0,
        "lines_approved": lines_approved or 0,
        "reports_sent": (line_reports_sent or 0) + (route_reports_sent or 0),
        "reports_applied": (line_reports_applied or 0) + (route_reports_applied or 0),
    }
