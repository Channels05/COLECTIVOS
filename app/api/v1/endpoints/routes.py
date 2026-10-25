import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import DbSession, require_admin
from app.models import Route
from app.schemas.route import RouteFeature, RouteFeatureCollection
from app.services.route_validator import load_corridors

router = APIRouter(prefix="/routes", tags=["rutas"])


@router.get("/{line_code}", response_model=RouteFeatureCollection)
async def get_line_routes(line_code: str, db: DbSession) -> RouteFeatureCollection:
    """Trazados (ida/regreso) de una línea en GeoJSON, para dibujarlos en el mapa del pasajero."""
    # ST_SimplifyPreserveTopology (~1e-5° ≈ 1 m) reduce vértices sin deformar el trazado: menos datos al móvil.
    geojson = func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(Route.geom, 0.00001), 6)
    rows = await db.execute(
        select(
            Route.id, Route.direction, Route.kind, Route.name, Route.source, Route.source_date, geojson.label("geometry")
        )
        .where(Route.line_code == line_code, Route.is_active.is_(True))
        .order_by(Route.kind, Route.direction, Route.id)
    )
    features = [
        RouteFeature(
            geometry=json.loads(r.geometry),
            properties={
                "id": r.id,
                "line_code": line_code,
                "direction": r.direction,
                "kind": r.kind,
                "name": r.name,
                # gore_rm / osm / recorded: la app distingue el trazado oficial del grabado por conductores.
                "source": r.source,
                # Para que la app pueda avisar si el dato está desactualizado (o cuándo se aprobó).
                "source_date": r.source_date.isoformat() if r.source_date else None,
            },
        )
        for r in rows
    ]
    if not features:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"La línea {line_code} no tiene trazados activos")
    return RouteFeatureCollection(features=features)


@router.post("/reload", dependencies=[Depends(require_admin)])
async def reload_route_cache(request: Request, db: DbSession) -> dict[str, int]:
    """Recarga los trazados en memoria tras importar nuevos GeoJSON.
    Ojo: con varios workers de uvicorn, cada uno tiene su caché (usar pub/sub de Redis para propagar)."""
    loaded = await load_corridors(db, request.app.state.route_validator)
    return {"routes_loaded": loaded}
