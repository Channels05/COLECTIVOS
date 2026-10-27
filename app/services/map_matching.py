"""Ajuste del trazado grabado a las calles (map matching) con OSRM.

El trazado propuesto (app/services/route_inference.py) sigue los puntos GPS suavizados: queda
cerca de la calle pero con el ruido del GPS (esquinas cortadas, zigzag). OSRM, con los datos de
OpenStreetMap, lo "pega" a la red vial.

Por defecto usa el servidor de demostración de OSRM: uso no comercial, máx. 1 consulta por segundo
y solo ~10 puntos por consulta. Por eso el trazado se muestrea (un punto cada 50 m o más) y se
ajusta por tramos de 10 puntos, uno por segundo, en segundo plano después de terminar la
grabación (puede tardar ~15 s). Si un tramo falla o el resultado no se parece al trazado (largo
muy distinto, se aleja demasiado), se conserva el trazado sin ajustar. En /admin se ve si se
ajustó y por qué no.
"""

import asyncio
import logging

import httpx
import shapely
from geoalchemy2.shape import to_shape
from pyproj import Transformer
from shapely.geometry import LineString, shape

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import RecordingStatus, RouteRecording

logger = logging.getLogger(__name__)

POINTS_PER_REQUEST = 10  # límite del servidor de demostración (uno propio acepta 100)
MAX_REQUESTS = 12  # ≈ 15 s con 1 consulta por segundo
MIN_STEP_M = 50.0
SEARCH_RADIUS_M = 30  # cuánto puede moverse cada punto para caer en una calle
MAX_LENGTH_CHANGE = 0.25  # el ajustado no puede ser más de 25 % más largo o corto
MAX_DEVIATION_M = 60.0  # ni alejarse más que esto del trazado original
REQUEST_GAP_S = 1.05
TIMEOUT_S = 10.0

# Tareas en curso: asyncio solo guarda referencias débiles, sin esto podrían recolectarse.
_running: set[asyncio.Task] = set()


class MatchFailed(Exception):
    """El ajuste no se aplica; el mensaje explica por qué (se muestra en /admin)."""


async def snap_to_streets(line: LineString, metric_crs: str) -> tuple[LineString, dict]:
    """Devuelve el trazado ajustado (WGS84) y su información, o lanza MatchFailed."""
    base_url = get_settings().map_matching_url
    if not base_url:
        raise MatchFailed("desactivado")
    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform
    to_wgs84 = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True).transform
    metric_line = shapely.transform(line, to_metric, interleaved=False)
    length = metric_line.length
    if length < 2 * MIN_STEP_M:
        raise MatchFailed("recorrido muy corto")

    # Tramos de POINTS_PER_REQUEST puntos que comparten el punto de unión.
    max_points = MAX_REQUESTS * (POINTS_PER_REQUEST - 1) + 1
    n = min(max_points, max(2, int(length // MIN_STEP_M) + 1))
    samples = [to_wgs84(*metric_line.interpolate(i * length / (n - 1)).coords[0]) for i in range(n)]
    chunks = [samples[i:i + POINTS_PER_REQUEST] for i in range(0, n - 1, POINTS_PER_REQUEST - 1)]

    coords: list[tuple[float, float]] = []
    confidences: list[float] = []
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers={"User-Agent": "colectivos-api"}) as client:
        for i, chunk in enumerate(chunks):
            if len(chunk) < 2:
                break
            if i:
                await asyncio.sleep(REQUEST_GAP_S)
            url = f"{base_url.rstrip('/')}/match/v1/driving/" + ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in chunk)
            params = {"geometries": "geojson", "overview": "full", "radiuses": ";".join([str(SEARCH_RADIUS_M)] * len(chunk))}
            try:
                body = (await client.get(url, params=params)).json()
            except (httpx.HTTPError, ValueError) as exc:
                raise MatchFailed(f"OSRM no respondió ({type(exc).__name__})") from None
            if body.get("code") != "Ok" or not body.get("matchings"):
                raise MatchFailed(f"OSRM: {body.get('code', 'sin respuesta')} en el tramo {i + 1}")
            if len(body["matchings"]) > 1:
                # Hubo puntos que no calzan con ninguna calle: mejor no inventar uniones.
                raise MatchFailed(f"el tramo {i + 1} no calza con una calle continua")
            part = body["matchings"][0]
            confidences.append(float(part.get("confidence", 0)))
            part_coords = [tuple(c) for c in part["geometry"]["coordinates"]]
            coords.extend(part_coords[1:] if coords else part_coords)

    matched = LineString(coords)
    matched_metric = shapely.transform(matched, to_metric, interleaved=False)
    change = abs(matched_metric.length - length) / length
    deviation = matched_metric.hausdorff_distance(metric_line)
    info = {
        "requests": len(confidences),
        "confidence": round(min(confidences), 2),
        "length_change": round(change, 3),
        "max_deviation_m": round(deviation),
    }
    if change > MAX_LENGTH_CHANGE:
        raise MatchFailed(f"el largo cambia {round(change * 100)} %")
    if deviation > MAX_DEVIATION_M:
        raise MatchFailed(f"se aleja {round(deviation)} m del trazado")
    return matched, info


async def _match_recording(recording_id: int) -> None:
    async with SessionLocal() as db:
        recording = await db.get(RouteRecording, recording_id)
        if recording is None or recording.proposed_geom is None:
            return
        line = to_shape(recording.proposed_geom)
        try:
            matched, info = await snap_to_streets(line, get_settings().metric_crs)
            result = {**info, "applied": True}
        except MatchFailed as exc:
            matched, result = None, {"applied": False, "reason": str(exc)}
        # Releer: mientras se ajustaba, el administrador pudo revisarla.
        await db.refresh(recording)
        if recording.status != RecordingStatus.PENDING_REVIEW:
            return
        if matched is not None:
            recording.proposed_geom = f"SRID=4326;{matched.wkt}"
        recording.stats = {**(recording.stats or {}), "map_matching": result}
        await db.commit()
        logger.info("Grabación %s: ajuste a calles %s", recording_id, result)


def schedule_map_matching(recording_id: int) -> None:
    """Ajusta el trazado de la grabación en segundo plano (no demora la respuesta a la app)."""
    if not get_settings().map_matching_url:
        return
    task = asyncio.create_task(_match_recording(recording_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
    task.add_done_callback(lambda t: t.exception() and logger.error("Ajuste a calles falló: %r", t.exception()))
