from datetime import datetime

from shapely.geometry import LineString

from app.models import RouteKind
from app.services.route_validator import SANTIAGO_TZ, RouteValidator

# Tramo sintético norte-sur sobre Av. Vicuña Mackenna (La Florida), lon fija.
VICUNA_MACKENNA = LineString([(-70.5985, -33.500), (-70.5985, -33.540)])
# Calle paralela ~280 m al oriente, usada como vía alternativa / trazado nocturno.
PARALLEL = LineString([(-70.5955, -33.500), (-70.5955, -33.540)])
LAT = -33.52
# A esta latitud 0.0001° de longitud ≈ 9.3 m.

NOON = datetime(2026, 10, 24, 12, 0, tzinfo=SANTIAGO_TZ)
MIDNIGHT = datetime(2026, 10, 24, 23, 30, tzinfo=SANTIAGO_TZ)


def make_validator(*extra: tuple[LineString, RouteKind]) -> RouteValidator:
    validator = RouteValidator(buffer_m=40, night_hours=(22, 6))
    validator.load([("6045", VICUNA_MACKENNA, RouteKind.TRAZADO), *(("6045", g, k) for g, k in extra)])
    return validator


def test_point_on_the_line_is_on_route():
    check = make_validator().check("6045", LAT, -70.5985)
    assert check is not None and check.on_route and check.distance_m < 1


def test_gps_noise_within_buffer_is_on_route():
    check = make_validator().check("6045", LAT, -70.5985 + 0.0003)  # ≈ 28 m
    assert check.on_route
    assert 25 < check.distance_m < 31


def test_parallel_street_is_off_route():
    check = make_validator().check("6045", LAT, -70.5985 + 0.003)  # ≈ 280 m
    assert not check.on_route
    assert 270 < check.distance_m < 290


def test_unknown_line_returns_none():
    assert make_validator().check("9999", LAT, -70.5985) is None


def test_authorized_alternative_road_is_on_route():
    validator = make_validator((PARALLEL, RouteKind.VIA_ALTERNATIVA))
    assert validator.check("6045", LAT, -70.5955, at=NOON).on_route


def test_night_route_only_valid_at_night():
    validator = make_validator((PARALLEL, RouteKind.NOCTURNO))
    assert not validator.check("6045", LAT, -70.5955, at=NOON).on_route
    assert validator.check("6045", LAT, -70.5955, at=MIDNIGHT).on_route


def test_night_window_crosses_midnight():
    validator = make_validator()
    assert validator.is_night(datetime(2026, 10, 25, 2, 0, tzinfo=SANTIAGO_TZ))
    assert not validator.is_night(datetime(2026, 10, 25, 6, 0, tzinfo=SANTIAGO_TZ))


def test_line_with_only_night_service_still_validates_by_day():
    validator = RouteValidator(buffer_m=40)
    validator.load([("231006", PARALLEL, RouteKind.NOCTURNO)])
    assert validator.check("231006", LAT, -70.5955, at=NOON).on_route
