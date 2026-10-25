"""Simula un conductor recorriendo el trazado de su línea. Sirve para probar el flujo completo
y ver un auto moviéndose en la app del pasajero sin salir a la calle.

    python -m scripts.simulate_driver --vehicle-id <id> --token <token> --api http://127.0.0.1:8000

Opciones útiles: --interval 1 (más rápido), --pings 60, --detour-at 10 --detour-pings 4
(saca el auto 150 m de la ruta para probar la alerta de desvío).
"""

import argparse
import math
import random
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
from pyproj import Transformer
from shapely.geometry import LineString, shape
from shapely.ops import linemerge

from app.core.config import get_settings

_to_metric = Transformer.from_crs("EPSG:4326", get_settings().metric_crs, always_xy=True)
_to_wgs84 = Transformer.from_crs(get_settings().metric_crs, "EPSG:4326", always_xy=True)


def _main_line(geometry: dict) -> LineString:
    """Une los ways de la relación en una línea continua; si quedan huecos, usa el tramo más largo."""
    geom = shape(geometry)
    merged = linemerge(geom) if geom.geom_type == "MultiLineString" else geom
    line = max(merged.geoms, key=lambda g: g.length) if merged.geom_type == "MultiLineString" else merged
    return LineString([_to_metric.transform(x, y) for x, y in line.coords])


def walk(
    geometry: dict, step_m: float, detour: range, detour_m: float, start_m: float = 0.0
) -> Iterator[tuple[float, float, float]]:
    """Genera (lat, lon, rumbo) cada `step_m` metros: recorre el trazado hasta el terminal y
    vuelve por el mismo camino (ida y vuelta), como un colectivo real, sin "teletransportarse"."""
    line = _main_line(geometry)
    lap = 2 * line.length
    i, travelled = 0, start_m % lap
    while True:
        returning = travelled > line.length
        distance = lap - travelled if returning else travelled
        p = line.interpolate(distance)
        ahead = line.interpolate(max(0.0, distance - 5) if returning else min(distance + 5, line.length))
        heading = math.degrees(math.atan2(ahead.x - p.x, ahead.y - p.y)) % 360  # x=este, y=norte
        x = p.x + (detour_m if i in detour else 0.0)
        lon, lat = _to_wgs84.transform(x, p.y)
        # round(359.97, 1) daría 360.0, que la API rechaza (heading < 360).
        yield lat, lon, round(heading, 1) % 360
        i += 1
        travelled = (travelled + step_m) % lap


def simulate(
    api: str,
    vehicle_id: str,
    token: str,
    *,
    pings: int,
    interval_s: float,
    speed_kmh: float = 30,
    detour_at: int | None = None,
    detour_pings: int = 4,
    detour_m: float = 150,
    start_m: float = 0.0,
    direction: int = 0,
    seed: int | None = None,
    verbose: bool = True,
    stop_on_error: bool = True,
) -> list[dict]:
    """Envía `pings` ubicaciones recorriendo el trazado. Con stop_on_error=False (flotas largas),
    un ping rechazado o un corte de red se informa y el auto sigue, en vez de detenerse."""
    rng = random.Random(seed)
    headers = {"X-Device-Token": token}
    with httpx.Client(base_url=api, headers=headers, timeout=10) as client:
        vehicle = client.get(f"/api/v1/vehicles/{vehicle_id}").raise_for_status().json()
        routes = client.get(f"/api/v1/routes/{vehicle['line_code']}").raise_for_status().json()

        # El paso simula la velocidad real del auto (a 30 km/h y 5 s => ~42 m por ping).
        step_m = speed_kmh / 3.6 * 5
        detour = range(detour_at, detour_at + detour_pings) if detour_at is not None else range(0)
        seats = vehicle["max_capacity"]
        acks = []
        # Recorremos el trazado principal (no vías alternativas, bucles ni nocturnos).
        main = [f for f in routes["features"] if f["properties"].get("kind") == "trazado"] or routes["features"]
        geometry = main[direction % len(main)]["geometry"]
        for i, (lat, lon, heading) in zip(range(pings), walk(geometry, step_m, detour, detour_m, start_m)):
            if i and i % 6 == 0:  # cada ~30 s sube o baja alguien
                seats = max(0, min(vehicle["max_capacity"], seats + rng.choice([-1, 1])))
            payload = {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "timestamp": datetime.now(UTC).isoformat(),
                "available_seats": seats,
                "speed_kmh": speed_kmh,
                "heading": heading,
            }
            try:
                ack = client.post(f"/api/v1/vehicles/{vehicle_id}/location", json=payload).raise_for_status().json()
            except httpx.HTTPError as exc:
                if stop_on_error:
                    raise
                detail = exc.response.text[:120] if isinstance(exc, httpx.HTTPStatusError) else str(exc)
                print(f"[{vehicle['plate']} línea {vehicle['line_code']}] ping rechazado: {detail}")
                time.sleep(interval_s)
                continue
            acks.append({**payload, **ack})
            if verbose:
                dist = ack["distance_to_route_m"]
                print(f"#{i:<3} {lat:.5f},{lon:.5f}  cupos={seats}  -> {ack['route_status']}"
                      + (f" ({dist} m)" if dist is not None else ""))
            time.sleep(interval_s)
    return acks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--vehicle-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--pings", type=int, default=120)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--speed-kmh", type=float, default=30)
    parser.add_argument("--detour-at", type=int)
    parser.add_argument("--detour-pings", type=int, default=4)
    parser.add_argument("--start-m", type=float, default=0.0, help="metros recorridos al partir")
    parser.add_argument("--direction", type=int, default=0, help="0 = primer trazado principal (ida), 1 = segundo")
    args = parser.parse_args()
    simulate(
        args.api, args.vehicle_id, args.token,
        pings=args.pings, interval_s=args.interval, speed_kmh=args.speed_kmh,
        detour_at=args.detour_at, detour_pings=args.detour_pings,
        start_m=args.start_m, direction=args.direction,
    )
