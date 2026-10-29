"""Buscar un lugar por nombre ("Metro Bellavista", "Mall Florida Center") con Nominatim.

Nominatim (el buscador de OpenStreetMap) es gratis con condiciones: máximo 1 consulta por
segundo, un User-Agent que identifique a la app y no usarlo para autocompletar letra a letra.
Por eso la app busca al apretar "Buscar" y todo pasa por el servidor: un solo User-Agent, una
consulta a la vez y las respuestas guardadas un día en Redis.
"""

import asyncio
import json
import time

import httpx
from redis.asyncio import Redis

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "colectivos-app/1.0 (https://channels05.github.io/colectivos-descarga/)"
# Gran Santiago: los resultados se limitan a esta caja (oeste, norte, este, sur).
VIEWBOX = "-71.05,-33.20,-70.40,-33.80"
CACHE_S = 24 * 3600
MAX_RESULTS = 6

_lock = asyncio.Lock()
_last_request = 0.0


class PlacesUnavailable(Exception):
    pass


def _label(item: dict) -> tuple[str, str]:
    """(nombre corto, dirección) a partir de la respuesta de Nominatim."""
    address = item.get("address") or {}
    name = item.get("name") or address.get("road") or item.get("display_name", "").split(",")[0]
    parts = [
        " ".join(p for p in (address.get("road"), address.get("house_number")) if p),
        address.get("suburb") or address.get("neighbourhood"),
        address.get("city") or address.get("town") or address.get("municipality"),
    ]
    detail = ", ".join(p for p in parts if p and p != name)
    return name, detail


async def search(redis: Redis, query: str) -> list[dict]:
    key = f"places:{query.strip().lower()}"
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)

    global _last_request
    async with _lock:
        wait = 1.05 - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}) as client:
                response = await client.get(NOMINATIM_URL, params={
                    "q": query, "format": "jsonv2", "addressdetails": 1, "countrycodes": "cl",
                    "viewbox": VIEWBOX, "bounded": 1, "limit": MAX_RESULTS, "accept-language": "es",
                })
            response.raise_for_status()
            items = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlacesUnavailable(str(exc)) from None

    results = []
    for item in items:
        name, detail = _label(item)
        results.append({"name": name, "detail": detail or None, "lat": float(item["lat"]), "lon": float(item["lon"])})
    # Sin resultados se guarda poco rato: puede ser un error de tipeo que se corrige al tiro.
    await redis.set(key, json.dumps(results), ex=CACHE_S if results else 300)
    return results
