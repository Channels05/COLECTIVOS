"""Flujo conductor -> pasajero contra un Redis falso (sin PostgreSQL)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from app.api.deps import get_authenticated_vehicle
from app.main import app
from app.models import RouteKind, Vehicle
from app.services.route_validator import RouteValidator

VEHICLE = Vehicle(
    id=uuid.uuid4(), plate="BCDF12", line_code="6045", max_capacity=4, is_active=True,
    created_at=datetime.now(UTC),
)
ROUTE_LON = -70.5985


@pytest.fixture
def client():
    # Sin `with TestClient(...)` no corre el lifespan: inyectamos el estado a mano.
    app.state.redis = FakeAsyncRedis(decode_responses=True)
    validator = RouteValidator(buffer_m=40)
    validator.load([("6045", LineString([(ROUTE_LON, -33.50), (ROUTE_LON, -33.54)]), RouteKind.TRAZADO)])
    app.state.route_validator = validator
    app.dependency_overrides[get_authenticated_vehicle] = lambda: VEHICLE
    yield TestClient(app)
    app.dependency_overrides.clear()


def ping(client, lon=ROUTE_LON, seats=2, age_s=0):
    return client.post(
        f"/api/v1/vehicles/{VEHICLE.id}/location",
        headers={"X-Device-Token": "test"},
        json={
            "lat": -33.52,
            "lon": lon,
            "timestamp": (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat(),
            "available_seats": seats,
            "heading": 180,
        },
    )


def test_driver_gets_own_vehicle(client):
    resp = client.get(f"/api/v1/vehicles/{VEHICLE.id}", headers={"X-Device-Token": "test"})
    assert resp.status_code == 200
    assert resp.json()["max_capacity"] == 4


def test_nearby_is_not_captured_by_vehicle_id_route(client):
    assert client.get("/api/v1/vehicles/nearby", params={"lat": -33.52, "lon": ROUTE_LON}).status_code == 200


def test_driver_ping_then_passenger_sees_vehicle(client):
    assert ping(client).json()["route_status"] == "on_route"

    resp = client.get("/api/v1/vehicles/nearby", params={"lat": -33.525, "lon": -70.60})
    body = resp.json()
    assert resp.status_code == 200
    assert body["count"] == 1
    vehicle = body["vehicles"][0]
    assert vehicle["plate"] == "BCDF12" and vehicle["available_seats"] == 2
    assert 500 < vehicle["distance_m"] < 600


def test_far_passenger_does_not_see_vehicle(client):
    ping(client)
    resp = client.get("/api/v1/vehicles/nearby", params={"lat": -33.45, "lon": -70.60})  # ~8 km al norte
    assert resp.json()["count"] == 0


def test_off_route_needs_consecutive_strikes(client):
    off_lon = ROUTE_LON + 0.003  # ≈ 280 m
    statuses = [ping(client, lon=off_lon).json()["route_status"] for _ in range(3)]
    assert statuses == ["deviating", "deviating", "off_route"]
    assert ping(client).json()["route_status"] == "on_route"  # al volver se reinicia el contador


def test_seats_above_capacity_rejected(client):
    assert ping(client, seats=5).status_code == 422


def test_outside_service_area_rejected(client):
    resp = client.post(
        f"/api/v1/vehicles/{VEHICLE.id}/location",
        headers={"X-Device-Token": "test"},
        json={"lat": 0, "lon": 0, "timestamp": datetime.now(UTC).isoformat(), "available_seats": 1},
    )
    assert resp.status_code == 422


def test_stale_ping_goes_only_to_history(client):
    assert ping(client, age_s=120).json()["accepted_live"] is False
    assert client.get("/api/v1/vehicles/nearby", params={"lat": -33.52, "lon": ROUTE_LON}).json()["count"] == 0


def test_websocket_sigue_a_los_cercanos(client):
    ping(client)
    with client.websocket_connect("/api/v1/vehicles/nearby/live") as ws:
        ws.send_json({"lat": -33.525, "lon": -70.60})
        first = ws.receive_json()
        assert first["count"] == 1 and first["vehicles"][0]["plate"] == "BCDF12"
        # Mover el mapa lejos: llega de inmediato la lista nueva, vacía.
        ws.send_json({"lat": -33.45, "lon": -70.60})
        assert ws.receive_json()["count"] == 0
        # Un mensaje inválido no corta la conexión.
        ws.send_json({"lat": 200})
        assert "error" in ws.receive_json()


def test_distancia_por_el_recorrido_si_el_pasajero_esta_adelante(client):
    # El tramo está dibujado de norte a sur y el auto (en -33,52) va al sur (rumbo 180).
    ping(client)
    params = {"lat": -33.53, "lon": ROUTE_LON, "user_lon": ROUTE_LON}
    ahead = client.get("/api/v1/vehicles/nearby", params={**params, "user_lat": -33.53}).json()
    assert 1_080 <= ahead["vehicles"][0]["route_distance_m"] <= 1_140  # ~1,1 km más al sur
    behind = client.get("/api/v1/vehicles/nearby", params={**params, "user_lat": -33.51}).json()
    assert behind["vehicles"][0]["route_distance_m"] is None  # ya pasó
    no_user = client.get("/api/v1/vehicles/nearby", params={"lat": -33.53, "lon": ROUTE_LON}).json()
    assert no_user["vehicles"][0]["route_distance_m"] is None
