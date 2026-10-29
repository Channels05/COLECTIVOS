"""Inscripción de conductores desde la app: el conductor la pide y el administrador la aprueba.

El teléfono genera un token aleatorio y lo manda al pedir la inscripción (el servidor guarda solo
su hash). Al aprobarla, el vehículo se crea con ese hash: el teléfono ya tiene su token y queda
vinculado sin QR. Mientras espera, consulta el estado con el mismo token.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.deps import DbSession, RedisClient, require_admin
from app.core.security import hash_token, verify_token
from app.models import ApplicationStatus, DriverApplication, LineSource, Vehicle
from app.schemas.vehicle import PLATE_PATTERN
from app.services.lines import ensure_line

public_router = APIRouter(prefix="/driver-applications", tags=["conductor"])
admin_router = APIRouter(prefix="/admin/driver-applications", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_APPLICATIONS_PER_IP_HOUR = 5


class ApplicationIn(BaseModel):
    plate: str = Field(pattern=PLATE_PATTERN, examples=["BCDF12"])
    line_code: str = Field(min_length=1, max_length=20)
    max_capacity: int = Field(default=4, ge=1, le=6)
    note: str | None = Field(default=None, max_length=200)
    # Lo genera el teléfono (aleatorio); será el token del dispositivo si se aprueba.
    token: str = Field(min_length=32, max_length=200)

    @field_validator("plate", mode="before")
    @classmethod
    def normalize_plate(cls, v: str) -> str:
        return "".join(ch for ch in str(v).upper() if ch.isalnum())

    @field_validator("line_code", mode="before")
    @classmethod
    def strip_line(cls, v: str) -> str:
        return str(v).strip().upper()


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plate: str
    line_code: str
    max_capacity: int
    note: str | None
    status: Literal["pending", "approved", "rejected"]
    review_note: str | None
    vehicle_id: uuid.UUID | None
    created_at: datetime
    reviewed_at: datetime | None


class ApplicationReview(BaseModel):
    note: str | None = Field(default=None, max_length=300)
    # Al aprobar, el administrador puede corregir lo que escribió el conductor.
    line_code: str | None = Field(default=None, max_length=20)
    max_capacity: int | None = Field(default=None, ge=1, le=6)


async def _plate_taken(db: DbSession, plate: str) -> bool:
    return await db.scalar(select(Vehicle.id).where(Vehicle.plate == plate)) is not None


@public_router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
async def apply(body: ApplicationIn, request: Request, db: DbSession, redis: RedisClient) -> DriverApplication:
    """El conductor pide que se registre su auto. Queda pendiente hasta que lo apruebe el administrador."""
    key = f"ratelimit:driver-applications:{request.client.host if request.client else 'unknown'}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    if count > MAX_APPLICATIONS_PER_IP_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiadas solicitudes. Intenta más tarde.")
    if await _plate_taken(db, body.plate):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"La patente {body.plate} ya está registrada. Pide el código QR a la administración.",
        )
    application = DriverApplication(
        plate=body.plate, line_code=body.line_code, max_capacity=body.max_capacity,
        note=(body.note or "").strip() or None, token_hash=hash_token(body.token),
    )
    db.add(application)
    await db.commit()
    await db.refresh(application)
    return application


@public_router.get("/{application_id}", response_model=ApplicationRead)
async def application_status(
    application_id: uuid.UUID, db: DbSession, x_application_token: Annotated[str, Header()]
) -> DriverApplication:
    """El teléfono que la pidió consulta si ya la aprobaron (con el mismo token)."""
    application = await db.get(DriverApplication, application_id)
    if application is None or not verify_token(x_application_token, application.token_hash):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitud no encontrada.")
    return application


@admin_router.get("", response_model=list[ApplicationRead])
async def list_applications(db: DbSession, status_filter: str = "pending") -> list[DriverApplication]:
    query = select(DriverApplication).order_by(DriverApplication.created_at.desc()).limit(100)
    if status_filter != "all":
        query = query.where(DriverApplication.status == ApplicationStatus(status_filter))
    return list(await db.scalars(query))


async def _pending(db: DbSession, application_id: uuid.UUID) -> DriverApplication:
    application = await db.get(DriverApplication, application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitud no encontrada.")
    if application.status != ApplicationStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "La solicitud ya fue revisada.")
    return application


@admin_router.post("/{application_id}/approve", response_model=ApplicationRead)
async def approve(application_id: uuid.UUID, body: ApplicationReview, db: DbSession) -> DriverApplication:
    """Crea el vehículo con el token del teléfono que pidió la inscripción."""
    application = await _pending(db, application_id)
    if await _plate_taken(db, application.plate):
        raise HTTPException(status.HTTP_409_CONFLICT, f"La patente {application.plate} ya está registrada.")
    line_code = (body.line_code or application.line_code).strip().upper()
    vehicle = Vehicle(
        plate=application.plate, line_code=line_code, max_capacity=body.max_capacity or application.max_capacity,
        device_token_hash=application.token_hash,
    )
    await ensure_line(db, line_code, LineSource.ADMIN)
    db.add(vehicle)
    await db.flush()
    application.status = ApplicationStatus.APPROVED
    application.vehicle_id = vehicle.id
    application.line_code = line_code
    application.max_capacity = vehicle.max_capacity
    application.review_note = body.note
    application.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(application)
    return application


@admin_router.post("/{application_id}/reject", response_model=ApplicationRead)
async def reject(application_id: uuid.UUID, body: ApplicationReview, db: DbSession) -> DriverApplication:
    application = await _pending(db, application_id)
    application.status = ApplicationStatus.REJECTED
    application.review_note = body.note
    application.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(application)
    return application
