from fastapi import APIRouter

from app.api.v1.endpoints import client_errors, contributions, contributors_admin, driver, fleet, passenger, recordings, routes, stats, vehicles

api_router = APIRouter()
# `passenger` antes que `driver`/`vehicles` para que /vehicles/nearby no choque con rutas paramétricas futuras.
api_router.include_router(passenger.router)
api_router.include_router(driver.router)
api_router.include_router(recordings.driver_router)
api_router.include_router(vehicles.router)
api_router.include_router(routes.router)
api_router.include_router(recordings.admin_router)
api_router.include_router(contributions.public_router)
api_router.include_router(contributions.contributor_router)
api_router.include_router(contributions.admin_router)
api_router.include_router(stats.admin_router)
api_router.include_router(client_errors.public_router)
api_router.include_router(client_errors.admin_router)
api_router.include_router(contributors_admin.admin_router)
api_router.include_router(fleet.admin_router)
