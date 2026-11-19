"""Aportes de pasajeros: grabar el recorrido del colectivo en que van, proponer líneas que faltan y
marcar cuándo pasa un colectivo (para estimar la frecuencia de cada línea).

Los recorridos y las líneas quedan pendientes hasta que un administrador los aprueba en /admin.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import AuthenticatedContributor, DbSession, RedisClient, require_admin
from app.core.security import generate_device_token, hash_token
from app.models import Contributor, Line, LineStatus, RouteRecording
from app.schemas.line import (
    ContributorCreated,
    LineAdminRead,
    LineEdit,
    LineHitRead,
    LineProposal,
    LineRead,
    LineReview,
)
from app.schemas.recording import ContributionStart, PointBatch, PointBatchAck, RecordingRead
from app.schemas.sighting import FrequencyRead, SightingIn, SightingRead
from app.services import frequency, lines, recordings
from app.services.errors import ServiceError

public_router = APIRouter(tags=["pasajero"])
contributor_router = APIRouter(prefix="/contributions", tags=["pasajero"])
admin_router = APIRouter(prefix="/admin/lines", tags=["admin"], dependencies=[Depends(require_admin)])

# Altas de aportantes por IP y hora: un teléfono se registra una vez; esto frena scripts.
MAX_CONTRIBUTORS_PER_IP_HOUR = 10


def _http(exc: ServiceError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


# --- Público ------------------------------------------------------------------------------------


@public_router.post("/contributors", response_model=ContributorCreated, status_code=status.HTTP_201_CREATED)
async def register_contributor(request: Request, db: DbSession, redis: RedisClient) -> ContributorCreated:
    """La app del pasajero se registra (anónima) la primera vez que quiere aportar."""
    key = f"ratelimit:contributors:{request.client.host if request.client else 'unknown'}"
    attempts = await redis.incr(key)
    if attempts == 1:
        await redis.expire(key, 3600)
    if attempts > MAX_CONTRIBUTORS_PER_IP_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados registros desde esta red. Intenta más tarde.")
    token = generate_device_token()
    contributor_id = uuid.uuid4()
    db.add(Contributor(id=contributor_id, token_hash=hash_token(token)))
    await db.commit()
    return ContributorCreated(id=contributor_id, token=token)


@public_router.get("/lines", response_model=list[LineHitRead])
async def search_lines(
    db: DbSession,
    q: str | None = Query(default=None, max_length=60, description="Código, letrero o descripción"),
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    radius_m: float = Query(default=300, ge=50, le=2_000),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[dict]:
    """Líneas activas. Con lat/lon: las que pasan cerca, la más cercana primero."""
    hits = await lines.search(db, q, lat, lon, radius_m, limit)
    return [
        {
            **LineRead.model_validate(hit.line).model_dump(),
            "has_route": hit.has_route,
            "distance_m": round(hit.distance_m) if hit.distance_m is not None else None,
        }
        for hit in hits
    ]


@public_router.get("/lines/{code}", response_model=LineRead)
async def get_line(code: str, db: DbSession) -> Line:
    """Una línea del catálogo: la app muestra su letrero o las calles por donde pasa."""
    line = await db.get(Line, code)
    if line is None or line.status == LineStatus.REJECTED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea no encontrada.")
    return line


def _frequency(line_code: str, headway: frequency.Headway) -> FrequencyRead:
    return FrequencyRead(
        line_code=line_code,
        headway_min=headway.minutes,
        samples=headway.samples,
        scope=headway.scope,
        sightings=headway.sightings,
        last_seen_at=headway.last_seen_at,
    )


@public_router.get("/frequencies", response_model=list[FrequencyRead])
async def frequencies_near(
    db: DbSession,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    line_code: str | None = Query(default=None, max_length=20),
) -> list[FrequencyRead]:
    """Cada cuánto pasa cada línea cerca de (lat, lon), según las marcas de pasajeros.

    Solo aparecen las líneas con marcas en el lugar; la de pasada más reciente primero."""
    headways = await frequency.near(db, lat, lon, line_code)
    return [
        _frequency(code, h)
        for code, h in sorted(headways.items(), key=lambda kv: kv[1].last_seen_at, reverse=True)
    ]


# --- Pasajero aportante (X-Contributor-Id + X-Contributor-Token) --------------------------------


@contributor_router.post("/sightings", response_model=SightingRead, status_code=status.HTTP_201_CREATED)
async def report_sighting(body: SightingIn, contributor: AuthenticatedContributor, db: DbSession) -> SightingRead:
    """"Pasó un colectivo": responde con la frecuencia de la línea en ese lugar, ya contando esta marca."""
    try:
        sighting = await frequency.report(db, contributor, body.line_code, body.lat, body.lon, body.accuracy_m)
    except ServiceError as exc:
        raise _http(exc) from None
    headway = (await frequency.near(db, body.lat, body.lon, body.line_code)).get(body.line_code)
    return SightingRead(
        id=sighting.id,
        line_code=sighting.line_code,
        seen_at=sighting.seen_at,
        frequency=_frequency(body.line_code, headway or frequency.estimate([sighting.seen_at], sighting.seen_at)),
    )


@contributor_router.post("/lines", response_model=LineRead, status_code=status.HTTP_201_CREATED)
async def propose_line(body: LineProposal, contributor: AuthenticatedContributor, db: DbSession) -> Line:
    """Agrega una línea que no aparece en la búsqueda. Queda en revisión, pero se puede grabar ya."""
    try:
        return await lines.propose(db, contributor, body.name, body.description, body.commune)
    except ServiceError as exc:
        raise _http(exc) from None


@contributor_router.post("/recordings", response_model=RecordingRead, status_code=status.HTTP_201_CREATED)
async def start_contribution(
    body: ContributionStart, contributor: AuthenticatedContributor, db: DbSession
) -> RouteRecording:
    try:
        return await recordings.start_contribution(
            db, contributor, body.line_code, body.direction, body.comment, body.sign_text
        )
    except ServiceError as exc:
        raise _http(exc) from None


@contributor_router.get("/recordings", response_model=list[RecordingRead])
async def my_contributions(contributor: AuthenticatedContributor, db: DbSession) -> list[RouteRecording]:
    """Mis últimas grabaciones y su estado (en revisión, aprobada, rechazada)."""
    return await recordings.contributions_of(db, contributor)


@contributor_router.get("/recordings/active", response_model=RecordingRead)
async def active_contribution(contributor: AuthenticatedContributor, db: DbSession) -> RouteRecording:
    recording = await recordings.active_recording(db, contributor)
    if recording is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay una grabación en curso.")
    return recording


@contributor_router.post("/recordings/{recording_id}/points", response_model=PointBatchAck)
async def upload_points(
    recording_id: int, body: PointBatch, contributor: AuthenticatedContributor, db: DbSession
) -> PointBatchAck:
    """Lote de puntos GPS. Reintentar el mismo lote es seguro (no duplica)."""
    points = [(p.lat, p.lon, p.recorded_at, p.accuracy_m) for p in body.points]
    try:
        total = await recordings.add_points(db, contributor, recording_id, points)
    except ServiceError as exc:
        raise _http(exc) from None
    return PointBatchAck(total_points=total)


@contributor_router.post("/recordings/{recording_id}/finish", response_model=RecordingRead)
async def finish_contribution(recording_id: int, contributor: AuthenticatedContributor, db: DbSession) -> RouteRecording:
    try:
        return await recordings.finish(db, contributor, recording_id)
    except ServiceError as exc:
        raise _http(exc) from None


@contributor_router.post("/recordings/{recording_id}/cancel", response_model=RecordingRead)
async def cancel_contribution(recording_id: int, contributor: AuthenticatedContributor, db: DbSession) -> RouteRecording:
    try:
        return await recordings.cancel(db, contributor, recording_id)
    except ServiceError as exc:
        raise _http(exc) from None


# --- Administrador: líneas propuestas -----------------------------------------------------------

_RECORDINGS = (
    select(func.count()).where(RouteRecording.line_code == Line.code).correlate(Line).scalar_subquery()
)


def _admin_line(line: Line, count: int) -> dict:
    return {
        **LineRead.model_validate(line).model_dump(),
        "created_at": line.created_at,
        "created_by": line.created_by,
        "review_note": line.review_note,
        "recordings": count,
    }


@admin_router.get("", response_model=list[LineAdminRead])
async def list_lines(db: DbSession, status_filter: LineStatus = LineStatus.PENDING_REVIEW) -> list[dict]:
    """Líneas por estado (por defecto, las propuestas por pasajeros que esperan revisión)."""
    rows = await db.execute(
        select(Line, _RECORDINGS.label("recordings"))
        .where(Line.status == status_filter)
        .order_by(Line.created_at.desc())
        .limit(200)
    )
    return [_admin_line(line, count) for line, count in rows]


@admin_router.post("/{code}/approve", response_model=LineRead)
async def approve_line(code: str, body: LineReview, db: DbSession) -> Line:
    try:
        return await lines.approve(db, code, body.note, body.name, body.commune)
    except ServiceError as exc:
        raise _http(exc) from None


@admin_router.patch("/{code}", response_model=LineRead)
async def edit_line(code: str, body: LineEdit, db: DbSession) -> Line:
    """Corrige el letrero y la comuna de cualquier línea; solo cambia los campos que vienen en el cuerpo."""
    line = await db.get(Line, code)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea no encontrada.")

    if "name" in body.model_fields_set:
        line.name = body.name
    if "commune" in body.model_fields_set:
        line.commune = body.commune

    await db.commit()
    return line


@admin_router.post("/{code}/reject", response_model=LineRead)
async def reject_line(code: str, body: LineReview, db: DbSession) -> Line:
    """Rechaza la línea y sus grabaciones pendientes (p. ej. "duplicada de 233010")."""
    try:
        return await lines.reject(db, code, body.note)
    except ServiceError as exc:
        raise _http(exc) from None

