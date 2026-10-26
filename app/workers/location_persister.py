"""Vacía `stream:locations` (Redis) a la tabla `location_updates` (PostGIS) en lotes.

    python -m app.workers.location_persister

Entrega "al menos una vez": si el worker cae antes del XACK, al reiniciar re-procesa
sus pendientes; el índice único (vehicle_id, recorded_at) + ON CONFLICT lo hace idempotente.
Se pueden levantar varias réplicas: el consumer group reparte los mensajes.
"""

import asyncio
import logging
import socket
import time
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import LocationUpdate
from app.services.tracking import STREAM_KEY

logger = logging.getLogger("location_persister")

GROUP = "persisters"
CONSUMER = socket.gethostname()
BATCH_SIZE = 1000
BLOCK_MS = 5000  # espera máxima de XREADGROUP cuando no hay mensajes nuevos
MAX_BACKOFF_S = 30.0
PRUNE_EVERY_S = 3600  # a lo más una limpieza del historial por hora


def _opt_float(value: str) -> float | None:
    return float(value) if value else None


def _to_row(fields: dict[str, str]) -> dict:
    return {
        "vehicle_id": fields["vehicle_id"],
        "recorded_at": datetime.fromisoformat(fields["recorded_at"]),
        # GeoAlchemy2 convierte EWKT con ST_GeomFromEWKT. Ojo: WKT es "lon lat".
        "geom": f"SRID=4326;POINT({fields['lon']} {fields['lat']})",
        "available_seats": int(fields["available_seats"]),
        "speed_kmh": _opt_float(fields["speed_kmh"]),
        "heading": _opt_float(fields["heading"]),
        "on_route": None if fields["on_route"] == "" else fields["on_route"] == "1",
        "distance_to_route_m": _opt_float(fields["distance_m"]),
    }


async def _ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def prune_old_locations(session: AsyncSession, retention_days: int) -> int:
    """Borra el historial de posiciones más viejo que `retention_days` (0 = no borra nada).

    Las grabaciones de recorridos no dependen de esto: al terminar guardan su propio trazado.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = await session.execute(delete(LocationUpdate).where(LocationUpdate.recorded_at < cutoff))
    return result.rowcount


async def run() -> None:
    # El timeout del socket debe superar el bloqueo de XREADGROUP; si no, una espera
    # normal sin mensajes se confunde con un Redis caído (redis-py >= 8 usa 5 s por defecto).
    redis = Redis.from_url(
        get_settings().redis_url, decode_responses=True, socket_timeout=BLOCK_MS / 1000 + 5
    )
    await _ensure_group(redis)
    insert_stmt = insert(LocationUpdate).on_conflict_do_nothing(index_elements=["vehicle_id", "recorded_at"])

    stream_id = "0"  # "0" = primero mis pendientes de una caída anterior; luego ">" = mensajes nuevos
    backoff_s = 1.0
    retention_days = get_settings().location_retention_days
    last_prune = 0.0
    while True:
        try:
            response = await redis.xreadgroup(
                GROUP, CONSUMER, {STREAM_KEY: stream_id}, count=BATCH_SIZE, block=BLOCK_MS
            )
            messages = response[0][1] if response else []
            if not messages:
                stream_id = ">"
                continue

            rows = [_to_row(fields) for _, fields in messages]
            async with SessionLocal() as session, session.begin():
                await session.execute(insert_stmt, rows)
            await redis.xack(STREAM_KEY, GROUP, *[msg_id for msg_id, _ in messages])
            logger.info("Persistidos %d pings", len(rows))
            # Se limpia justo después de escribir: la base ya está despierta (en Neon, despertarla
            # solo para limpiar gastaría horas de cómputo del plan gratis).
            if time.monotonic() - last_prune > PRUNE_EVERY_S:
                last_prune = time.monotonic()
                async with SessionLocal() as session, session.begin():
                    pruned = await prune_old_locations(session, retention_days)
                if pruned:
                    logger.info("Historial: borrados %d pings de más de %d días", pruned, retention_days)
            backoff_s = 1.0
        except (RedisError, SQLAlchemyError, OSError) as exc:
            # Un corte breve de Redis/PostgreSQL (pausa de Docker, PC suspendido) no debe matar al
            # worker. Lo leído y no confirmado (sin XACK) queda pendiente y se re-procesa con "0".
            logger.warning("Fallo transitorio (%s: %s); reintento en %.0f s", type(exc).__name__, exc, backoff_s)
            await asyncio.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, MAX_BACKOFF_S)
            stream_id = "0"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
