"""Seguir mi viaje: el pasajero que va en un colectivo con la app de conductor encendida comparte
un enlace para que otra persona vea por dónde va el auto (como en las apps de taxi).

El enlace muestra la posición del colectivo (no la del teléfono del pasajero) y vence a las 3 horas.
Vive solo en Redis: no se guarda en la base.
"""

import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import DbSession, RedisClient
from app.models import Line
from app.services import tracking

router = APIRouter(tags=["pasajero"])
page_router = APIRouter(include_in_schema=False)

SHARE_TTL = timedelta(hours=3)
MAX_SHARES_PER_IP_HOUR = 20
PUBLIC_BASE = "https://colectivos-api-06ka.onrender.com"
SHARE_PAGE = Path(__file__).resolve().parents[3] / "share_ui" / "seguir.html"


def _key(share_id: str) -> str:
    return f"share:{share_id}"


class ShareIn(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=64)


class ShareCreated(BaseModel):
    id: str
    url: str
    expires_at: datetime


class ShareRead(BaseModel):
    active: bool  # False = el auto dejó de transmitir (terminó el turno o perdió señal)
    line_code: str
    line_description: str | None
    plate: str | None
    lat: float | None
    lon: float | None
    heading: float | None
    available_seats: int | None
    last_seen: datetime | None
    expires_at: datetime


async def _live_vehicle(redis: RedisClient, vehicle_id: str) -> dict | None:
    state = await redis.hgetall(tracking.state_key(vehicle_id))
    return state or None


@router.post("/shares", response_model=ShareCreated, status_code=status.HTTP_201_CREATED)
async def create_share(body: ShareIn, request: Request, redis: RedisClient) -> dict:
    """Crea el enlace para seguir al colectivo en que va el pasajero (tiene que estar transmitiendo)."""
    key = f"ratelimit:shares:{request.client.host if request.client else 'unknown'}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    if count > MAX_SHARES_PER_IP_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados enlaces. Intenta más tarde.")
    state = await _live_vehicle(redis, body.vehicle_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ese colectivo ya no está transmitiendo.")
    share_id = secrets.token_urlsafe(9)
    expires_at = datetime.now(UTC) + SHARE_TTL
    await redis.set(_key(share_id), json.dumps({"vehicle_id": body.vehicle_id, "line_code": state["line_code"],
                                                "expires_at": expires_at.isoformat()}),
                    ex=int(SHARE_TTL.total_seconds()))
    return {"id": share_id, "url": f"{PUBLIC_BASE}/seguir/{share_id}", "expires_at": expires_at}


@router.get("/shares/{share_id}", response_model=ShareRead)
async def read_share(share_id: str, redis: RedisClient, db: DbSession) -> dict:
    raw = await redis.get(_key(share_id))
    if raw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El enlace venció o no existe.")
    share = json.loads(raw)
    line = await db.get(Line, share["line_code"])
    base = {
        "line_code": share["line_code"],
        "line_description": (line.name or line.description) if line else None,
        "expires_at": share["expires_at"],
    }
    state = await _live_vehicle(redis, share["vehicle_id"])
    if state is None:
        # Dejó de transmitir: se muestra la última posición que vio quien sigue el viaje.
        last = share.get("last") or {}
        return {**base, "active": False, "plate": last.get("plate"), "lat": last.get("lat"), "lon": last.get("lon"),
                "heading": None, "available_seats": None, "last_seen": last.get("last_seen")}
    now = {
        "plate": state.get("plate"),
        "lat": float(state["lat"]),
        "lon": float(state["lon"]),
        "last_seen": datetime.fromisoformat(state["recorded_at"]).astimezone(UTC).isoformat(),
    }
    await redis.set(_key(share_id), json.dumps({**share, "last": now}), keepttl=True)
    return {
        **base,
        **now,
        "active": True,
        "heading": float(state["heading"]) if state.get("heading") else None,
        "available_seats": int(state["available_seats"]),
    }


@page_router.get("/seguir/{share_id}")
async def share_page(share_id: str) -> FileResponse:
    """La página que abre quien recibe el enlace (no necesita la app)."""
    return FileResponse(SHARE_PAGE, headers={"Cache-Control": "no-cache"})
