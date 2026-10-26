import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, require_admin
from app.core.security import generate_device_token, hash_token
from app.models import LineSource, Vehicle
from app.schemas.vehicle import VehicleCreate, VehicleCreated, VehicleRead
from app.services.lines import ensure_line

router = APIRouter(prefix="/vehicles", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("", response_model=VehicleCreated, status_code=status.HTTP_201_CREATED)
async def register_vehicle(body: VehicleCreate, db: DbSession) -> VehicleCreated:
    token = generate_device_token()
    vehicle = Vehicle(**body.model_dump(), device_token_hash=hash_token(token))
    # Una línea sin trazado (ni en el catálogo) se agrega: así sus conductores pueden grabarla.
    await ensure_line(db, body.line_code, LineSource.ADMIN)
    db.add(vehicle)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"La patente {body.plate} ya está registrada") from None
    await db.refresh(vehicle)
    return VehicleCreated(**VehicleRead.model_validate(vehicle).model_dump(), device_token=token)


@router.get("", response_model=list[VehicleRead])
async def list_vehicles(db: DbSession) -> list[Vehicle]:
    """Vehículos registrados (para la página /admin)."""
    return list(await db.scalars(select(Vehicle).order_by(Vehicle.line_code, Vehicle.plate)))


@router.post("/{vehicle_id}/token", response_model=VehicleCreated)
async def rotate_token(vehicle_id: uuid.UUID, db: DbSession) -> VehicleCreated:
    """Token nuevo (y QR nuevo) para un vehículo: el anterior deja de funcionar.

    El token solo se guarda como hash, así que no se puede volver a mostrar: si el conductor
    perdió el QR o cambió de teléfono, se genera otro.
    """
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehículo no encontrado.")
    token = generate_device_token()
    vehicle.device_token_hash = hash_token(token)
    await db.commit()
    await db.refresh(vehicle)
    return VehicleCreated(**VehicleRead.model_validate(vehicle).model_dump(), device_token=token)
