"""Errores de la app: la app los reporta (anónimos) y el administrador los ve agrupados en /admin."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Text, delete, func, select
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by

from app.api.deps import DbSession, RedisClient, require_admin
from app.models import ClientError
from app.schemas.client_error import ClientErrorBatch, ErrorGroupRead

public_router = APIRouter(tags=["pasajero"])
admin_router = APIRouter(prefix="/admin/errors", tags=["admin"], dependencies=[Depends(require_admin)])

# Un teléfono con un error en bucle no debe llenar la base: tope por IP y hora.
MAX_ERRORS_PER_IP_HOUR = 60
RETENTION_DAYS = 90
STACK_MAX_CHARS = 8_000


def _latest(column):
    """El valor del reporte más reciente del grupo: (array_agg(col ORDER BY created_at DESC))[1]."""
    return func.array_agg(aggregate_order_by(column, ClientError.created_at.desc()), type_=ARRAY(Text))[1]


def _cut(text: str | None, size: int) -> str | None:
    return text[:size] if text else text


@public_router.post("/client-errors", status_code=status.HTTP_202_ACCEPTED)
async def report_errors(body: ClientErrorBatch, request: Request, db: DbSession, redis: RedisClient) -> dict:
    """La app envía sus errores de JavaScript (los que cerraron la app llegan al volver a abrirla)."""
    key = f"ratelimit:client-errors:{request.client.host if request.client else 'unknown'}"
    count = await redis.incrby(key, len(body.errors))
    if count == len(body.errors):
        await redis.expire(key, 3600)
    if count > MAX_ERRORS_PER_IP_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados reportes de error.")
    db.add_all(
        ClientError(
            occurred_at=e.occurred_at,
            message=e.message[:500],
            stack=_cut(e.stack, STACK_MAX_CHARS),
            fatal=e.fatal,
            source=e.source,
            screen=_cut(e.screen, 100),
            app_version=e.app_version,
            update_id=e.update_id,
            platform=e.platform,
            os_version=e.os_version,
            device=e.device,
        )
        for e in body.errors
    )
    # Los errores son pocos: la limpieza de los viejos va aquí mismo (usa el índice por fecha).
    await db.execute(delete(ClientError).where(ClientError.created_at < datetime.now(UTC) - timedelta(days=RETENTION_DAYS)))
    await db.commit()
    return {"received": len(body.errors)}


@admin_router.get("", response_model=list[ErrorGroupRead])
async def error_groups(db: DbSession, days: int = Query(default=30, ge=1, le=RETENTION_DAYS)) -> list[dict]:
    """Errores del período agrupados por mensaje; el más reciente primero."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await db.execute(
        select(
            ClientError.message,
            func.count().label("count"),
            func.count().filter(ClientError.fatal.is_(True)).label("fatal"),
            func.min(ClientError.created_at).label("first_seen"),
            func.max(ClientError.created_at).label("last_seen"),
            func.array_remove(func.array_agg(func.distinct(ClientError.app_version)), None).label("app_versions"),
            func.array_remove(func.array_agg(func.distinct(ClientError.device)), None).label("devices"),
            func.array_remove(func.array_agg(func.distinct(ClientError.screen)), None).label("screens"),
            _latest(ClientError.stack).label("last_stack"),
            _latest(ClientError.source).label("last_source"),
            _latest(ClientError.update_id).label("last_update_id"),
        )
        .where(ClientError.created_at > since)
        .group_by(ClientError.message)
        .order_by(func.max(ClientError.created_at).desc())
        .limit(100)
    )
    return [dict(r._mapping) for r in rows]
