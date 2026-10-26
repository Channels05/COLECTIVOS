"""Grabación de recorridos: el conductor graba su ida/regreso y un administrador aprueba el trazado.

Las grabaciones de pasajeros entran por endpoints/contributions.py y se revisan aquí mismo.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import exists, func, select

from app.api.deps import AuthenticatedVehicle, DbSession, require_admin
from app.models import Line, RecordingStatus, Route, RouteRecording, RouteSource, Vehicle
from app.schemas.recording import (
    RecordingApproval,
    RecordingDetail,
    RecordingRead,
    RecordingReview,
    RecordingStart,
    RecordingSummary,
)
from app.services import recordings
from app.services.route_validator import load_corridors

driver_router = APIRouter(prefix="/vehicles", tags=["conductor"])
admin_router = APIRouter(prefix="/admin/recordings", tags=["admin"], dependencies=[Depends(require_admin)])


def _http(exc: recordings.RecordingError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


# --- Conductor (autenticado con su token de dispositivo) ---------------------------------------


@driver_router.post("/{vehicle_id}/recordings", response_model=RecordingRead, status_code=status.HTTP_201_CREATED)
async def start_recording(body: RecordingStart, vehicle: AuthenticatedVehicle, db: DbSession) -> RouteRecording:
    """Empieza a grabar la ida o el regreso. Los pings del turno desde ahora forman el recorrido."""
    try:
        return await recordings.start(db, vehicle, body.direction)
    except recordings.RecordingError as exc:
        raise _http(exc) from None


@driver_router.get("/{vehicle_id}/recordings/active", response_model=RecordingRead)
async def get_active_recording(vehicle: AuthenticatedVehicle, db: DbSession) -> RouteRecording:
    """La grabación en curso (p. ej. para retomarla si la app se reinició). 404 si no hay."""
    recording = await recordings.active_recording(db, vehicle)
    if recording is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay una grabación en curso.")
    return recording


@driver_router.post("/{vehicle_id}/recordings/{recording_id}/finish", response_model=RecordingRead)
async def finish_recording(recording_id: int, vehicle: AuthenticatedVehicle, db: DbSession) -> RouteRecording:
    """Termina la grabación y arma el trazado propuesto; queda pendiente de revisión."""
    try:
        return await recordings.finish(db, vehicle, recording_id)
    except recordings.RecordingError as exc:
        raise _http(exc) from None


@driver_router.post("/{vehicle_id}/recordings/{recording_id}/cancel", response_model=RecordingRead)
async def cancel_recording(recording_id: int, vehicle: AuthenticatedVehicle, db: DbSession) -> RouteRecording:
    try:
        return await recordings.cancel(db, vehicle, recording_id)
    except recordings.RecordingError as exc:
        raise _http(exc) from None


# --- Administrador (X-Admin-Key) ----------------------------------------------------------------

_HAS_OFFICIAL = (
    exists()
    .where(Route.line_code == RouteRecording.line_code, Route.source != RouteSource.RECORDED, Route.is_active)
    .correlate(RouteRecording)
)


def _summary_query():
    # Outer joins: las grabaciones de pasajeros no tienen vehículo.
    return (
        select(RouteRecording, Vehicle.plate, Line.name, Line.status, _HAS_OFFICIAL.label("has_official"))
        .outerjoin(Vehicle, Vehicle.id == RouteRecording.vehicle_id)
        .outerjoin(Line, Line.code == RouteRecording.line_code)
    )


def _summary(recording: RouteRecording, plate, line_name, line_status, has_official: bool, **extra) -> dict:
    return {
        **RecordingRead.model_validate(recording).model_dump(exclude={"warnings"}),
        "recorded_by": "driver" if recording.vehicle_id else "passenger",
        "plate": plate,
        "line_name": line_name,
        "line_status": line_status,
        "line_has_official_route": has_official,
        **extra,
    }


@admin_router.get("", response_model=list[RecordingSummary])
async def list_recordings(db: DbSession, status_filter: RecordingStatus | None = None) -> list[dict]:
    """Grabaciones, las más recientes primero. `status_filter=pending_review` para la bandeja."""
    query = _summary_query().order_by(RouteRecording.started_at.desc()).limit(200)
    if status_filter:
        query = query.where(RouteRecording.status == status_filter)
    rows = await db.execute(query)
    return [_summary(*row) for row in rows]


@admin_router.get("/{recording_id}", response_model=RecordingDetail)
async def get_recording(recording_id: int, db: DbSession) -> dict:
    row = (
        await db.execute(
            _summary_query()
            .add_columns(func.ST_AsGeoJSON(RouteRecording.raw_geom), func.ST_AsGeoJSON(RouteRecording.proposed_geom))
            .where(RouteRecording.id == recording_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Grabación no encontrada.")
    *summary, raw, proposed = row
    return _summary(
        *summary,
        raw=json.loads(raw) if raw else None,
        proposed=json.loads(proposed) if proposed else None,
    )


@admin_router.post("/{recording_id}/approve", response_model=RecordingRead)
async def approve_recording(
    recording_id: int, body: RecordingApproval, request: Request, db: DbSession
) -> RouteRecording:
    """Aprueba: crea el trazado de la línea y recarga la validación de desvíos de inmediato.

    `direction` es obligatorio si quien grabó (un pasajero) no indicó el sentido."""
    try:
        recording = await recordings.approve(db, recording_id, body.note, body.direction, body.line_name)
    except recordings.RecordingError as exc:
        raise _http(exc) from None
    # Con varios workers de uvicorn, los demás lo verán al reiniciar o con POST /routes/reload.
    await load_corridors(db, request.app.state.route_validator)
    return recording


@admin_router.post("/{recording_id}/reject", response_model=RecordingRead)
async def reject_recording(recording_id: int, body: RecordingReview, db: DbSession) -> RouteRecording:
    try:
        return await recordings.reject(db, recording_id, body.note)
    except recordings.RecordingError as exc:
        raise _http(exc) from None
