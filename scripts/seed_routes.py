"""Carga los trazados oficiales (GORE RM) si la base no tiene ninguno.

    python -m scripts.seed_routes

Lo corre el contenedor al arrancar: en una base nueva (p. ej. recién creada en la nube) deja las
410 líneas listas sin pasos manuales; si ya hay trazados no hace nada.
"""

import asyncio
from pathlib import Path

from sqlalchemy import func, select

from app.db.session import SessionLocal, engine
from app.models import Route, RouteSource
from scripts import import_gore_routes

GEOJSON = Path(__file__).resolve().parent.parent / "data" / "gore" / "colectivos_rm.geojson"


async def has_official_routes() -> bool:
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).where(Route.source == RouteSource.GORE_RM))
    await engine.dispose()
    return count > 0


async def main() -> None:
    if await has_official_routes():
        print("La base ya tiene trazados oficiales: no se importa nada.")
        return
    print(f"Base sin trazados: importando {GEOJSON.name}…")
    await import_gore_routes.main(GEOJSON)


if __name__ == "__main__":
    asyncio.run(main())
