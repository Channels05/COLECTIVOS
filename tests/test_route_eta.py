"""Distancia por el recorrido de la línea (llegada estimada del colectivo al pasajero)."""

from shapely.geometry import LineString

from app.models import RouteKind
from app.services.route_validator import RouteValidator

LON = -70.5985
# Tramo de ida hacia el norte (de lat -33.54 a -33.50, ~4,4 km), dibujado en el sentido del recorrido.
NORTH = LineString([(LON, -33.54), (LON, -33.50)])


def validator() -> RouteValidator:
    v = RouteValidator(buffer_m=50)
    v.load([("6045", NORTH, RouteKind.TRAZADO)])
    return v


def test_auto_que_viene_por_el_tramo():
    d = validator().distance_along_route("6045", (-33.53, LON), 0.0, (-33.52, LON))
    assert d is not None and 1_080 <= d <= 1_140  # ~1,1 km entre -33,53 y -33,52


def test_auto_en_sentido_contrario_no_cuenta():
    assert validator().distance_along_route("6045", (-33.53, LON), 180.0, (-33.52, LON)) is None


def test_pasajero_que_ya_quedo_atras():
    assert validator().distance_along_route("6045", (-33.51, LON), 0.0, (-33.52, LON)) is None


def test_sin_rumbo_o_lejos_del_trazado():
    v = validator()
    assert v.distance_along_route("6045", (-33.53, LON), None, (-33.52, LON)) is None
    assert v.distance_along_route("6045", (-33.53, LON + 0.01), 0.0, (-33.52, LON)) is None  # auto a ~900 m
    assert v.distance_along_route("6045", (-33.53, LON), 0.0, (-33.52, LON + 0.01)) is None  # pasajero lejos


def test_linea_sin_trazado():
    assert validator().distance_along_route("999", (-33.53, LON), 0.0, (-33.52, LON)) is None
