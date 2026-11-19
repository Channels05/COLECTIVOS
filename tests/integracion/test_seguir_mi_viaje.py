import asyncio
from datetime import UTC, datetime

from app.services import tracking


def test_compartir_viaje_y_detener_transmision(client, admin, linea, patente, patente_factory):
    registrado = client.post(
        "/api/v1/vehicles",
        headers=admin,
        json={"plate": patente, "line_code": linea, "max_capacity": 4},
    )
    assert registrado.status_code == 201
    vehicle = registrado.json()

    ubicado = client.post(
        f"/api/v1/vehicles/{vehicle['id']}/location",
        headers={"X-Device-Token": vehicle["device_token"]},
        json={
            "lat": -33.52,
            "lon": -70.60,
            "timestamp": datetime.now(UTC).isoformat(),
            "available_seats": 2,
            "heading": 180,
        },
    )
    assert ubicado.status_code in (200, 202)

    creado = client.post("/api/v1/shares", json={"vehicle_id": vehicle["id"]})
    assert creado.status_code == 201
    assert "/seguir/" in creado.json()["url"]

    enlace = creado.json()["id"]
    lectura = client.get(f"/api/v1/shares/{enlace}")
    assert lectura.status_code == 200
    assert lectura.json()["active"] is True
    assert lectura.json()["plate"] == patente
    assert lectura.json()["available_seats"] == 2

    pagina = client.get(f"/seguir/{enlace}")
    assert pagina.status_code == 200
    assert "Seguir el viaje" in pagina.text

    redis = client.app.state.redis
    asyncio.run(redis.delete(tracking.state_key(vehicle["id"])))
    inactivo = client.get(f"/api/v1/shares/{enlace}")
    assert inactivo.status_code == 200
    assert inactivo.json()["active"] is False
    assert inactivo.json()["lat"] == -33.52

    assert client.get("/api/v1/shares/enlace-inventado").status_code == 404

    otro = client.post(
        "/api/v1/vehicles",
        headers=admin,
        json={"plate": patente_factory(), "line_code": linea, "max_capacity": 4},
    )
    assert otro.status_code == 201
    assert client.post(
        "/api/v1/shares",
        json={"vehicle_id": otro.json()["id"]},
    ).status_code == 404
