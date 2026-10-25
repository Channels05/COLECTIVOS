"""Trazado propuesto a partir de los pings GPS de una grabación.

Pasos (en metros, proyectando a UTM 19S):
1. Descarta picos del GPS (rebote en edificios, fix de antena): un punto al que habría que llegar
   a más de `max_speed_kmh` desde su vecino anterior Y salir igual de rápido hacia el siguiente.
   Mirar ambos vecinos evita que un primer punto malo haga descartar todos los buenos que siguen.
2. Descarta puntos casi repetidos (< `min_step_m`): auto detenido en semáforo o paradero.
3. Suaviza con un promedio móvil centrado (3 puntos, o 5 si los pings vienen muy juntos) para
   bajar el ruido GPS; los extremos no se mueven.
4. Une los puntos en una línea y la simplifica (Douglas-Peucker, `simplify_m`) para quitar el
   zigzag restante sin deformar el recorrido.
5. Calcula métricas y advertencias para el administrador (pocos puntos, recorrido corto,
   tramos sin señal).

Con pings reales (cada 5 s, ~40 m, ruido de 6 m) el suavizado deja el largo a <1 % del real
y la desviación máxima en ~12 m (sin suavizar: +2 % y 17 m; con ruido de 12 m: +10 % y 34 m).

No ajusta a las calles (map matching): el administrador revisa el resultado sobre el mapa.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point


@dataclass(frozen=True)
class InferenceParams:
    max_speed_kmh: float = 110.0
    min_step_m: float = 5.0
    simplify_m: float = 8.0
    gap_warn_m: float = 300.0  # tramo sin puntos más largo que esto => probable pérdida de señal
    min_points: int = 20
    min_length_m: float = 500.0
    dense_step_m: float = 20.0  # paso mediano bajo esto => ventana de suavizado de 5 en vez de 3


@dataclass
class Proposal:
    raw: LineString  # todos los puntos recibidos, en orden (WGS84)
    proposed: LineString  # trazado limpio y simplificado (WGS84)
    stats: dict
    warnings: list[str] = field(default_factory=list)


class NotEnoughPoints(ValueError):
    """La grabación no tiene puntos suficientes para proponer un trazado."""


def build_proposal(
    points: Sequence[tuple[float, float, datetime]],
    metric_crs: str = "EPSG:32719",
    params: InferenceParams = InferenceParams(),
) -> Proposal:
    """`points` = (lat, lon, recorded_at) en orden temporal."""
    if len(points) < 2:
        raise NotEnoughPoints(f"La grabación tiene {len(points)} punto(s); se necesitan al menos 2.")

    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
    metric = [(Point(to_metric.transform(lon, lat)), t) for lat, lon, t in points]

    despiked = _drop_spikes(metric, params.max_speed_kmh)
    outliers = len(metric) - len(despiked)
    kept: list[tuple[Point, datetime]] = [despiked[0]]
    stationary = 0
    for p, t in despiked[1:]:
        if p.distance(kept[-1][0]) < params.min_step_m:
            stationary += 1
            continue
        kept.append((p, t))

    if len(kept) < 2:
        raise NotEnoughPoints("Tras limpiar el GPS quedaron menos de 2 puntos: el auto no se movió.")

    steps = [kept[i][0].distance(kept[i + 1][0]) for i in range(len(kept) - 1)]
    window = 5 if median(steps) < params.dense_step_m else 3
    smoothed = _moving_average([(p.x, p.y) for p, _ in kept], window)
    simplified_m = LineString(smoothed).simplify(params.simplify_m, preserve_topology=False)
    gaps = [s for s in steps if s > params.gap_warn_m]
    duration_s = (kept[-1][1] - kept[0][1]).total_seconds()

    stats = {
        "raw_points": len(points),
        "used_points": len(kept),
        "outliers_dropped": outliers,
        "stationary_dropped": stationary,
        # Largo del trazado propuesto: el de la línea cruda incluye el zigzag del ruido GPS
        # (con pings densos lo infla ~15 %).
        "length_m": round(simplified_m.length),
        "duration_min": round(duration_s / 60, 1),
        "vertices": len(simplified_m.coords),
        "max_gap_m": round(max(steps)),
        "gaps_over_threshold": len(gaps),
    }
    warnings = []
    if len(kept) < params.min_points:
        warnings.append(f"Pocos puntos útiles ({len(kept)}): el trazado puede ser impreciso.")
    if simplified_m.length < params.min_length_m:
        warnings.append(f"Recorrido muy corto ({round(simplified_m.length)} m): ¿se grabó completo?")
    if gaps:
        warnings.append(
            f"{len(gaps)} tramo(s) de más de {round(params.gap_warn_m)} m sin señal GPS "
            f"(el mayor: {round(max(gaps))} m). Revisa esos tramos en el mapa."
        )

    def to_wgs(line: LineString) -> LineString:
        return shapely.transform(line, to_wgs84.transform, interleaved=False)

    raw = LineString([(lon, lat) for lat, lon, _ in points])
    return Proposal(raw=raw, proposed=to_wgs(simplified_m), stats=stats, warnings=warnings)


def _too_fast(a: tuple[Point, datetime], b: tuple[Point, datetime], max_speed_kmh: float) -> bool:
    distance = a[0].distance(b[0])
    elapsed = abs((b[1] - a[1]).total_seconds())
    if elapsed == 0:
        return distance > 50  # mismo instante y lejos: uno de los dos es un rebote
    return distance / elapsed * 3.6 > max_speed_kmh


def _drop_spikes(points: list[tuple[Point, datetime]], max_speed_kmh: float) -> list[tuple[Point, datetime]]:
    """Quita picos: puntos imposibles respecto a ambos vecinos (o a su único vecino, en los extremos).

    Primero los interiores (repitiendo hasta que no quede ninguno) y después los extremos, para que
    un pico en la posición 1 no haga parecer malo al punto 0.
    """
    pts = list(points)
    changed = True
    while changed and len(pts) > 2:
        changed = False
        for i in range(1, len(pts) - 1):
            if _too_fast(pts[i - 1], pts[i], max_speed_kmh) and _too_fast(pts[i], pts[i + 1], max_speed_kmh):
                del pts[i]
                changed = True
                break
    if len(pts) > 2 and _too_fast(pts[0], pts[1], max_speed_kmh):
        del pts[0]
    if len(pts) > 2 and _too_fast(pts[-2], pts[-1], max_speed_kmh):
        del pts[-1]
    return pts


def _moving_average(coords: list[tuple[float, float]], window: int) -> list[tuple[float, float]]:
    """Promedio móvil centrado; los primeros y últimos puntos quedan fijos (inicio y fin reales)."""
    half = window // 2
    if len(coords) <= window:
        return coords
    out = coords[:half]
    for i in range(half, len(coords) - half):
        chunk = coords[i - half : i + half + 1]
        out.append((sum(x for x, _ in chunk) / window, sum(y for _, y in chunk) / window))
    return out + coords[-half:]
