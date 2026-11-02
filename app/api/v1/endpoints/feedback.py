"""Para mantener los datos al día: avisos de pasajeros ("este recorrido cambió") y las búsquedas
que no encontraron nada (lugares que OpenStreetMap no conoce, viajes sin colectivo que sirva).
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthenticatedContributor, DbSession, require_admin
from app.models import Line, LineStatus, ReportStatus, RouteReport, RouteReportKind, SearchMiss

contributor_router = APIRouter(prefix="/contributions", tags=["pasajero"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_REPORTS_PER_DAY = 10
# Los puntos de las búsquedas fallidas se guardan redondeados: ~100 m alcanza para ver la zona.
MISS_DECIMALS = 3


class RouteReportIn(BaseModel):
    line_code: str = Field(min_length=1, max_length=20)
    kind: RouteReportKind
    message: str | None = Field(default=None, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class RouteReportRead(BaseModel):
    id: int
    line_code: str
    kind: RouteReportKind
    message: str | None
    status: ReportStatus
    created_at: datetime


class RouteReportAdmin(RouteReportRead):
    line_description: str | None
    contributor_id: str
    lat: float | None
    lon: float | None


class PlaceMiss(BaseModel):
    query: str
    count: int
    last_at: datetime


class TripMiss(BaseModel):
    to_lat: float
    to_lon: float
    count: int
    last_at: datetime
    # Algunos orígenes de esas búsquedas (redondeados), para ver desde dónde querían llegar.
    origins: list[dict[str, float]]


class SearchMisses(BaseModel):
    places: list[PlaceMiss]
    trips: list[TripMiss]


async def log_miss(
    db: AsyncSession,
    kind: Literal["place", "trip"],
    query: str | None = None,
    origin: tuple[float, float] | None = None,
    destination: tuple[float, float] | None = None,
) -> None:
    """Guarda una búsqueda sin resultado, anónima. Nunca hace fallar la respuesta al pasajero."""

    def point(p: tuple[float, float] | None) -> str | None:
        return f"SRID=4326;POINT({round(p[1], MISS_DECIMALS)} {round(p[0], MISS_DECIMALS)})" if p else None

    try:
        db.add(SearchMiss(kind=kind, query=(query or "").strip().lower()[:80] or None,
                          origin=point(origin), destination=point(destination)))
        await db.commit()
    except Exception:  # noqa: BLE001 — es solo estadística
        await db.rollback()


# --- Pasajero aportante -------------------------------------------------------------------------


@contributor_router.post("/route-reports", response_model=RouteReportRead, status_code=status.HTTP_201_CREATED)
async def report_route(body: RouteReportIn, contributor: AuthenticatedContributor, db: DbSession) -> RouteReport:
    """"Esta línea ya no pasa por aquí", "el letrero dice otra cosa": lo revisa el administrador."""
    line = await db.get(Line, body.line_code)
    if line is None or line.status == LineStatus.REJECTED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea no encontrada.")
    recent = await db.scalar(
        select(func.count()).select_from(RouteReport).where(
            RouteReport.contributor_id == contributor.id,
            RouteReport.created_at > datetime.now(UTC) - timedelta(days=1),
        )
    )
    if recent >= MAX_REPORTS_PER_DAY:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Ya enviaste muchos avisos hoy. Gracias, sigue mañana.")
    has_point = body.lat is not None and body.lon is not None
    report = RouteReport(
        line_code=body.line_code, contributor_id=contributor.id, kind=body.kind,
        message=(body.message or "").strip() or None,
        geom=f"SRID=4326;POINT({body.lon} {body.lat})" if has_point else None,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


# --- Administrador ------------------------------------------------------------------------------

_REPORTS = text("""
    SELECT r.id, r.line_code, r.kind::text AS kind, r.message, r.status::text AS status, r.created_at,
           r.contributor_id::text AS contributor_id, ST_Y(r.geom) AS lat, ST_X(r.geom) AS lon,
           coalesce(l.name, l.description) AS line_description
    FROM route_reports r LEFT JOIN lines l ON l.code = r.line_code
    WHERE r.status::text = :status
    ORDER BY r.created_at DESC
    LIMIT 200
""")


@admin_router.get("/route-reports", response_model=list[RouteReportAdmin])
async def list_route_reports(db: DbSession, status_filter: ReportStatus = ReportStatus.PENDING) -> list[dict]:
    return [dict(r._mapping) for r in await db.execute(_REPORTS, {"status": status_filter.value})]


async def _review(db: AsyncSession, report_id: int, new_status: ReportStatus) -> RouteReport:
    report = await db.get(RouteReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aviso no encontrado.")
    if report.status != ReportStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese aviso ya fue revisado.")
    report.status = new_status
    report.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(report)
    return report


@admin_router.post("/route-reports/{report_id}/resolve", response_model=RouteReportRead)
async def resolve_route_report(report_id: int, db: DbSession) -> RouteReport:
    """Revisado y corregido (p. ej. se grabó el recorrido nuevo o se cambió el letrero)."""
    return await _review(db, report_id, ReportStatus.APPLIED)


@admin_router.post("/route-reports/{report_id}/discard", response_model=RouteReportRead)
async def discard_route_report(report_id: int, db: DbSession) -> RouteReport:
    return await _review(db, report_id, ReportStatus.DISCARDED)


_PLACE_MISSES = text("""
    SELECT query, count(*) AS count, max(created_at) AS last_at
    FROM search_misses
    WHERE kind = 'place' AND query IS NOT NULL AND created_at > now() - make_interval(days => :days)
    GROUP BY query ORDER BY count DESC, last_at DESC LIMIT 100
""")

# Viajes sin resultado agrupados por destino (celdas de ~1 km), con algunos orígenes de ejemplo.
_TRIP_MISSES = text("""
    SELECT round(ST_Y(destination)::numeric, 2)::float AS to_lat, round(ST_X(destination)::numeric, 2)::float AS to_lon,
           count(*) AS count, max(created_at) AS last_at,
           (array_agg(json_build_object('lat', ST_Y(origin), 'lon', ST_X(origin)) ORDER BY created_at DESC))[1:5] AS origins
    FROM search_misses
    WHERE kind = 'trip' AND destination IS NOT NULL AND created_at > now() - make_interval(days => :days)
    GROUP BY 1, 2 ORDER BY count DESC, last_at DESC LIMIT 100
""")


@admin_router.get("/search-misses", response_model=SearchMisses)
async def search_misses(db: DbSession, days: int = Query(default=30, ge=1, le=365)) -> dict:
    """Lo que la gente buscó y no encontró: dónde faltan líneas, recorridos o nombres de lugares."""
    places = [dict(r._mapping) for r in await db.execute(_PLACE_MISSES, {"days": days})]
    trips = [
        {**dict(r._mapping), "origins": [o for o in r.origins if o["lat"] is not None]}
        for r in await db.execute(_TRIP_MISSES, {"days": days})
    ]
    return {"places": places, "trips": trips}
