"""Ciclo de vida de una grabación de recorrido.

    conductor: start -> (pings normales del turno) -> finish | cancel
    pasajero:  start_contribution -> add_points (lotes) -> finish | cancel
    administrador: pending_review -> approve (suma una Route "recorded") | reject
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import (
    Contributor,
    Line,
    LineStatus,
    LocationUpdate,
    RecordingPoint,
    RecordingStatus,
    Route,
    RouteDirection,
    RouteKind,
    RouteRecording,
    RouteSource,
    Vehicle,
)
from app.services import lines
from app.services.errors import ServiceError as RecordingError
from app.services.map_matching import schedule_map_matching
from app.services.route_inference import NotEnoughPoints, build_proposal

# Quien graba: el vehículo del conductor o el pasajero (aportante anónimo).
Author = Vehicle | Contributor

# Puntos de pasajero menos precisos que esto (GPS en un bolsillo, dentro del auto) se descartan.
MAX_POINT_ACCURACY_M = 50.0
# Al sumar una grabación a lo ya grabado de la línea, lo que pasa a menos de esto de un tramo
# aprobado se da por cubierto (el ruido del GPS separa dos pasadas por la misma calle ~10-20 m).
MERGE_OVERLAP_M = 25.0
# Restos más cortos que esto tras quitar lo cubierto son ruido (cruces de calle, bordes).
MERGE_MIN_PIECE_M = 50.0
# ≈ 8 h a un punto cada 5 s: más que esto no es un recorrido sino un teléfono que quedó grabando.
MAX_POINTS_PER_RECORDING = 6_000


def _owned_by(author: Author):
    if isinstance(author, Vehicle):
        return RouteRecording.vehicle_id == author.id
    return RouteRecording.contributor_id == author.id


async def active_recording(db: AsyncSession, author: Author) -> RouteRecording | None:
    return await db.scalar(
        select(RouteRecording).where(_owned_by(author), RouteRecording.status == RecordingStatus.RECORDING)
    )


async def _create(db: AsyncSession, recording: RouteRecording) -> RouteRecording:
    db.add(recording)
    try:
        await db.commit()
    except IntegrityError:  # índice único parcial: ya hay una grabación activa
        await db.rollback()
        raise RecordingError("Ya hay una grabación en curso. Termínala o cancélala antes de empezar otra.") from None
    await db.refresh(recording)
    return recording


async def start(db: AsyncSession, vehicle: Vehicle, direction: RouteDirection) -> RouteRecording:
    """Conductor: la línea es la de su vehículo."""
    return await _create(db, RouteRecording(vehicle_id=vehicle.id, line_code=vehicle.line_code, direction=direction))


async def start_contribution(
    db: AsyncSession,
    contributor: Contributor,
    line_code: str,
    direction: RouteDirection | None,
    comment: str | None,
    sign_text: str | None = None,
) -> RouteRecording:
    """Pasajero: elige la línea en la que va (o una que acaba de proponer)."""
    line = await lines.usable_by(db, contributor, line_code)
    return await _create(
        db,
        RouteRecording(
            contributor_id=contributor.id,
            line_code=line.code,
            direction=direction,
            comment=(comment or "").strip() or None,
            sign_text=(sign_text or "").strip() or None,
        ),
    )


async def _own_recording(db: AsyncSession, author: Author, recording_id: int) -> RouteRecording:
    recording = await db.get(RouteRecording, recording_id)
    if recording is None:
        raise RecordingError("Grabación no encontrada.", 404)
    owner_id = recording.vehicle_id if isinstance(author, Vehicle) else recording.contributor_id
    if owner_id != author.id:
        raise RecordingError("Grabación no encontrada.", 404)
    if recording.status != RecordingStatus.RECORDING:
        raise RecordingError("La grabación ya terminó.")
    return recording


async def add_points(
    db: AsyncSession,
    contributor: Contributor,
    recording_id: int,
    points: Sequence[tuple[float, float, datetime, float | None]],
) -> int:
    """Guarda un lote de puntos (lat, lon, recorded_at, accuracy_m) y devuelve el total acumulado.

    Idempotente: reenviar el mismo lote no duplica (único por recording_id + recorded_at).
    """
    recording = await _own_recording(db, contributor, recording_id)
    settings = get_settings()
    min_lon, min_lat, max_lon, max_lat = settings.service_bbox
    now = datetime.now(UTC)
    earliest = recording.started_at - timedelta(minutes=1)
    latest = now + timedelta(seconds=settings.max_clock_skew_s)
    # Se ignoran (sin fallar el lote) los puntos fuera del área de servicio o de la ventana de tiempo.
    rows = [
        {
            "recording_id": recording.id,
            "recorded_at": t,
            "geom": f"SRID=4326;POINT({lon} {lat})",
            "accuracy_m": accuracy,
        }
        for lat, lon, t, accuracy in points
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat and earliest <= t <= latest
    ]
    count = await db.scalar(select(func.count()).where(RecordingPoint.recording_id == recording.id))
    if count + len(rows) > MAX_POINTS_PER_RECORDING:
        raise RecordingError("La grabación es demasiado larga. Termínala y empieza otra.", 422)
    if rows:
        await db.execute(insert(RecordingPoint).values(rows).on_conflict_do_nothing())
        await db.commit()
    return await db.scalar(select(func.count()).where(RecordingPoint.recording_id == recording.id))


async def _points(db: AsyncSession, recording: RouteRecording) -> list[tuple[float, float, datetime]]:
    if recording.vehicle_id is not None:
        query = (
            select(func.ST_Y(LocationUpdate.geom), func.ST_X(LocationUpdate.geom), LocationUpdate.recorded_at)
            .where(
                LocationUpdate.vehicle_id == recording.vehicle_id,
                LocationUpdate.recorded_at.between(recording.started_at, recording.finished_at),
            )
            .order_by(LocationUpdate.recorded_at)
        )
    else:
        query = (
            select(func.ST_Y(RecordingPoint.geom), func.ST_X(RecordingPoint.geom), RecordingPoint.recorded_at)
            .where(
                RecordingPoint.recording_id == recording.id,
                func.coalesce(RecordingPoint.accuracy_m, 0) <= MAX_POINT_ACCURACY_M,
            )
            .order_by(RecordingPoint.recorded_at)
        )
    return [(lat, lon, t) for lat, lon, t in await db.execute(query)]


async def finish(db: AsyncSession, author: Author, recording_id: int) -> RouteRecording:
    """Cierra la grabación, arma el trazado propuesto con sus puntos y lo deja en revisión."""
    recording = await _own_recording(db, author, recording_id)
    recording.finished_at = datetime.now(UTC)
    points = await _points(db, recording)
    try:
        proposal = build_proposal(points, get_settings().metric_crs)
    except NotEnoughPoints as exc:
        # No se puede proponer nada: se cancela para que pueda volver a grabar.
        recording.status = RecordingStatus.CANCELLED
        recording.review_note = str(exc)
        await db.commit()
        hint = (
            "Asegúrate de tener el turno iniciado mientras grabas."
            if isinstance(author, Vehicle)
            else "Graba desde que subes hasta que bajas, con la app abierta."
        )
        raise RecordingError(f"{exc} {hint}", 422) from None

    recording.raw_geom = f"SRID=4326;{proposal.raw.wkt}"
    recording.proposed_geom = f"SRID=4326;{proposal.proposed.wkt}"
    recording.stats = {**proposal.stats, "warnings": proposal.warnings}
    recording.status = RecordingStatus.PENDING_REVIEW
    await db.commit()
    await db.refresh(recording)
    # Pegar el trazado a las calles (OSRM) en segundo plano; si no se puede, queda el suavizado.
    schedule_map_matching(recording.id)
    return recording


async def cancel(db: AsyncSession, author: Author, recording_id: int) -> RouteRecording:
    recording = await _own_recording(db, author, recording_id)
    recording.status = RecordingStatus.CANCELLED
    recording.finished_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(recording)
    return recording


async def contributions_of(db: AsyncSession, contributor: Contributor, limit: int = 20) -> list[RouteRecording]:
    """Últimas grabaciones del pasajero, para que vea si se aprobaron."""
    rows = await db.scalars(
        select(RouteRecording)
        .where(RouteRecording.contributor_id == contributor.id)
        .order_by(RouteRecording.started_at.desc())
        .limit(limit)
    )
    return list(rows)


async def _pending(db: AsyncSession, recording_id: int) -> RouteRecording:
    recording = await db.get(RouteRecording, recording_id)
    if recording is None:
        raise RecordingError("Grabación no encontrada.", 404)
    if recording.status != RecordingStatus.PENDING_REVIEW:
        raise RecordingError(f"Solo se revisan grabaciones pendientes (esta está '{recording.status}').")
    return recording


async def approve(
    db: AsyncSession,
    recording_id: int,
    note: str | None,
    direction: RouteDirection | None = None,
    line_name: str | None = None,
    replace_previous: bool = False,
) -> RouteRecording:
    """Suma el trazado propuesto a lo ya grabado de la línea en ese sentido.

    Varias grabaciones parciales (cada pasajero graba el tramo que viaja) arman el recorrido: solo
    se agrega lo que aún no estaba cubierto, como una Route nueva por grabación. Con
    `replace_previous` se desactiva lo grabado antes (para corregir un trazado malo).

    Si el pasajero no indicó el sentido, lo elige el administrador (`direction`). Si la línea era
    una propuesta pendiente, se aprueba junto con su recorrido.
    """
    recording = await _pending(db, recording_id)
    direction = direction or recording.direction
    if direction is None:
        raise RecordingError("Indica si el recorrido es de ida o de regreso.", 422)
    recording.direction = direction

    line = await db.get(Line, recording.line_code)
    if line is not None and line.status == LineStatus.REJECTED:
        raise RecordingError("La línea de esta grabación fue rechazada.")
    if line is not None and line.status == LineStatus.PENDING_REVIEW:
        lines.activate(line, "Aprobada junto con su recorrido grabado")
    if line is not None and (line_name or "").strip():
        line.name = line_name.strip()

    if replace_previous:
        await db.execute(
            update(Route)
            .where(
                Route.source == RouteSource.RECORDED,
                Route.line_code == recording.line_code,
                Route.direction == direction,
                Route.is_active.is_(True),
            )
            .values(is_active=False)
        )
    geom_ewkt, added_m, total_m = (await db.execute(_NEW_PART_SQL, {
        "recording_id": recording.id, "line_code": recording.line_code, "direction": direction.value,
        "overlap_m": MERGE_OVERLAP_M, "min_piece_m": MERGE_MIN_PIECE_M,
    })).one()
    recording.stats = {
        **(recording.stats or {}),
        "merge": {"added_m": round(added_m), "covered_m": round(max(total_m - added_m, 0)), "replaced": replace_previous},
    }
    recording.status = RecordingStatus.APPROVED
    recording.review_note = note
    recording.reviewed_at = datetime.now(UTC)
    if geom_ewkt is None:
        # Todo lo grabado ya estaba cubierto: confirma el trazado pero no agrega nada.
        await db.commit()
        await db.refresh(recording)
        return recording

    # Comuna: la de los trazados oficiales de la línea si existen, si no la del catálogo.
    commune = await db.scalar(
        select(Route.commune).where(Route.line_code == recording.line_code, Route.source != RouteSource.RECORDED).limit(1)
    )
    commune = commune or (line.commune if line else None)
    label = line.name if line and line.name else recording.line_code
    route = Route(
        source=RouteSource.RECORDED,
        source_id=f"recording/{recording.id}",
        source_date=date.today(),
        line_code=recording.line_code,
        direction=direction,
        kind=RouteKind.TRAZADO,
        name=f"{label} {direction} (grabado)"[:200],
        commune=commune or "Sin asignar",
        geom=geom_ewkt,
        source_attrs={
            "recording_id": recording.id,
            "recorded_by": "driver" if recording.vehicle_id else "passenger",
            "stats": recording.stats,
            "note": note,
        },
        is_active=True,
    )
    db.add(route)
    await db.flush()
    recording.route_id = route.id
    await db.commit()
    await db.refresh(recording)
    return recording


# Parte del trazado propuesto que no cubren las rutas grabadas activas de la línea en ese sentido
# (todo, si no hay ninguna), sin los restos cortos. Devuelve (EWKT o NULL, metros nuevos, metros totales).
_NEW_PART_SQL = text("""
    WITH proposed AS (
        SELECT proposed_geom AS g FROM route_recordings WHERE id = :recording_id
    ), covered AS (
        SELECT ST_Buffer(ST_Union(geom)::geography, :overlap_m)::geometry AS g
        FROM routes
        WHERE source::text = 'recorded' AND line_code = :line_code
          AND direction::text = :direction AND is_active
    ), pieces AS (
        SELECT (ST_Dump(CASE WHEN c.g IS NULL THEN p.g ELSE ST_Difference(p.g, c.g) END)).geom AS g
        FROM proposed p, covered c
    ), kept AS (
        SELECT g FROM pieces WHERE ST_Dimension(g) = 1 AND ST_Length(g::geography) >= :min_piece_m
    )
    SELECT (SELECT ST_AsEWKT(ST_Multi(ST_Collect(g))) FROM kept),
           (SELECT COALESCE(SUM(ST_Length(g::geography)), 0) FROM kept),
           (SELECT ST_Length(g::geography) FROM proposed)
""")


async def reject(db: AsyncSession, recording_id: int, note: str | None) -> RouteRecording:
    recording = await _pending(db, recording_id)
    recording.status = RecordingStatus.REJECTED
    recording.review_note = note
    recording.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(recording)
    return recording
