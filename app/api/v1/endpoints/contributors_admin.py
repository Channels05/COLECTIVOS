"""Administrador: ver lo que aportó un pasajero anónimo y bloquearlo (o desbloquearlo)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import DbSession, require_admin
from app.schemas.moderation import BlockRequest, BlockResult, ContributorProfile
from app.services import moderation
from app.services.errors import ServiceError
from app.services.route_validator import load_corridors

admin_router = APIRouter(prefix="/admin/contributors", tags=["admin"], dependencies=[Depends(require_admin)])


@admin_router.get("/{contributor_id}", response_model=ContributorProfile)
async def get_contributor(contributor_id: uuid.UUID, db: DbSession) -> dict:
    try:
        return await moderation.profile(db, contributor_id)
    except ServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


@admin_router.post("/{contributor_id}/block", response_model=BlockResult)
async def block_contributor(contributor_id: uuid.UUID, body: BlockRequest, request: Request, db: DbSession) -> dict:
    """Bloquea al aportante; con `purge` deshace también sus aportes."""
    try:
        result = await moderation.set_blocked(db, contributor_id, True, body.purge)
    except ServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    if result["routes_deactivated"]:
        # Sus tramos aprobados dejan de contar para la validación de desvíos de inmediato.
        await load_corridors(db, request.app.state.route_validator)
    return result


@admin_router.post("/{contributor_id}/unblock", response_model=BlockResult)
async def unblock_contributor(contributor_id: uuid.UUID, db: DbSession) -> dict:
    """Le permite volver a aportar (no restaura lo que se borró al bloquearlo)."""
    try:
        return await moderation.set_blocked(db, contributor_id, False)
    except ServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
