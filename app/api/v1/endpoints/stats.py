from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require_admin
from app.schemas.stats import StatsRead
from app.services import stats

admin_router = APIRouter(prefix="/admin/stats", tags=["admin"], dependencies=[Depends(require_admin)])


@admin_router.get("", response_model=StatsRead)
async def get_stats(db: DbSession, days: int = Query(default=28, ge=1, le=365)) -> dict:
    """Panel de datos: aportes de pasajeros (marcas y grabaciones) por línea y por zona."""
    return await stats.summary(db, days)
