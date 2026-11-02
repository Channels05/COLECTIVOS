import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from redis.asyncio import Redis

from app.api.v1.endpoints import shares
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.services.route_validator import RouteValidator, load_corridors
from app.workers import location_persister

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.route_validator = RouteValidator(settings.route_buffer_m, settings.metric_crs, settings.night_hours)
    async with SessionLocal() as session:
        loaded = await load_corridors(session, app.state.route_validator)
    logger.info("Trazados cargados en memoria: %d", loaded)
    worker = asyncio.create_task(location_persister.run()) if settings.run_worker_in_process else None
    yield
    if worker:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
    await app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api/v1")
app.include_router(shares.page_router)  # /seguir/{id}: la página del enlace compartido


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


ADMIN_PAGE = Path(__file__).parent / "admin_ui" / "index.html"


@app.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    """Página de revisión de recorridos grabados. Pública: los datos se piden con X-Admin-Key."""
    # no-cache: el navegador revalida siempre, así una versión nueva de la página se ve al recargar.
    return FileResponse(ADMIN_PAGE, headers={"Cache-Control": "no-cache"})
