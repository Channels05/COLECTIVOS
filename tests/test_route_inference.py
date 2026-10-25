import random
from datetime import UTC, datetime, timedelta

import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import LineString

from app.services.route_inference import NotEnoughPoints, build_proposal

# Recorrido "real": 3 km en L (norte-sur por Vicuña Mackenna y luego al oriente), La Florida.
TRUE_ROUTE = LineString([(-70.5985, -33.500), (-70.5985, -33.520), (-70.5800, -33.520)])
T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
_to_m = Transformer.from_crs(4326, 32719, always_xy=True).transform
_to_wgs = Transformer.from_crs(32719, 4326, always_xy=True).transform
TRUE_M = shapely.transform(TRUE_ROUTE, _to_m, interleaved=False)


def drive(step_m=40.0, every_s=5, noise_m=6.0, seed=1):
    """Pings cada 5 s a ~29 km/h sobre TRUE_ROUTE, con ruido GPS gaussiano."""
    rng = random.Random(seed)
    points, d, t = [], 0.0, T0
    while d <= TRUE_M.length:
        p = TRUE_M.interpolate(d)
        lon, lat = _to_wgs(p.x + rng.gauss(0, noise_m), p.y + rng.gauss(0, noise_m))
        points.append((lat, lon, t))
        d += step_m
        t += timedelta(seconds=every_s)
    return points


def max_deviation_m(line_wgs: LineString) -> float:
    line_m = shapely.transform(line_wgs, _to_m, interleaved=False)
    return max(TRUE_M.distance(shapely.Point(c)) for c in line_m.coords)


def test_clean_drive_reproduces_the_route():
    proposal = build_proposal(drive())
    assert max_deviation_m(proposal.proposed) < 20  # ruido de 6 m: el trazado queda pegado a la calle
    assert abs(proposal.stats["length_m"] - TRUE_M.length) / TRUE_M.length < 0.1
    assert proposal.stats["vertices"] < proposal.stats["used_points"]  # simplificó el zigzag
    assert proposal.warnings == []


def test_dense_noisy_pings_do_not_inflate_the_length():
    # 1 ping cada 12 m con 5 m de ruido: el zigzag inflaría el largo ~15 % si no se simplificara.
    proposal = build_proposal(drive(step_m=12, every_s=1, noise_m=5))
    assert abs(proposal.stats["length_m"] - TRUE_M.length) / TRUE_M.length < 0.07


def test_gps_jumps_are_dropped():
    points = drive()
    lat, lon, t = points[30]
    points.insert(31, (lat + 0.01, lon + 0.01, t + timedelta(seconds=1)))  # salto de ~1,4 km en 1 s
    proposal = build_proposal(points)
    assert proposal.stats["outliers_dropped"] == 1
    assert max_deviation_m(proposal.proposed) < 20


def test_bad_first_fix_does_not_discard_the_good_points_after_it():
    points = drive()
    lat, lon, t = points[0]
    points[0] = (lat + 0.01, lon, t)  # el primer fix rebotó ~1,1 km
    proposal = build_proposal(points)
    assert proposal.stats["outliers_dropped"] == 1
    assert proposal.stats["used_points"] >= len(points) - 3
    assert max_deviation_m(proposal.proposed) < 20


def test_stopped_car_points_are_dropped():
    points = drive()
    lat, lon, t = points[10]
    stop = [(lat, lon, t + timedelta(seconds=5 * k)) for k in range(1, 13)]  # 1 minuto en un semáforo
    later = [(la, lo, ti + timedelta(seconds=60)) for la, lo, ti in points[11:]]
    proposal = build_proposal(points[:11] + stop + later)
    assert proposal.stats["stationary_dropped"] >= 12


def test_signal_loss_is_flagged():
    points = drive()
    proposal = build_proposal(points[:20] + points[35:])  # 15 pings (~600 m) sin señal
    assert proposal.stats["gaps_over_threshold"] == 1
    assert any("sin señal" in w for w in proposal.warnings)


def test_short_or_tiny_recordings_warn_or_fail():
    proposal = build_proposal(drive()[:8])
    assert any("Pocos puntos" in w for w in proposal.warnings)
    assert any("muy corto" in w for w in proposal.warnings)
    with pytest.raises(NotEnoughPoints):
        build_proposal(drive()[:1])
    lat, lon, t = drive()[0]
    with pytest.raises(NotEnoughPoints):  # 10 pings sin moverse
        build_proposal([(lat, lon, t + timedelta(seconds=5 * k)) for k in range(10)])
