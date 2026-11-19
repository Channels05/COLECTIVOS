from datetime import UTC, datetime


def test_inscripcion_aprobacion_uso_y_rechazo(client, admin, linea, patente, patente_factory):
    solicitud = client.post(
        "/api/v1/driver-applications",
        json={"plate": f"{patente[:2].lower()}-{patente[2:].lower()}", "line_code": linea, "max_capacity": 4},
    )
    assert solicitud.status_code == 201
    body = solicitud.json()
    assert body["plate"] == patente
    assert body["status"] == "pending"
    assert len(body["token"]) >= 32
    application_id = body["id"]
    token = body["token"]

    assert client.post(
        "/api/v1/driver-applications",
        json={"plate": "XX", "line_code": linea},
    ).status_code == 422

    consulta = client.get(
        f"/api/v1/driver-applications/{application_id}",
        headers={"X-Application-Token": token},
    )
    assert consulta.status_code == 200
    assert consulta.json()["status"] == "pending"
    assert client.get(
        f"/api/v1/driver-applications/{application_id}",
        headers={"X-Application-Token": "token-inventado"},
    ).status_code == 404

    lista = client.get("/api/v1/admin/driver-applications", headers=admin)
    assert lista.status_code == 200
    assert any(item["id"] == application_id for item in lista.json())

    aprobada = client.post(
        f"/api/v1/admin/driver-applications/{application_id}/approve",
        headers=admin,
        json={"max_capacity": 3},
    )
    assert aprobada.status_code == 200
    assert aprobada.json()["status"] == "approved"
    assert aprobada.json()["vehicle_id"] is not None
    assert client.post(
        f"/api/v1/admin/driver-applications/{application_id}/approve",
        headers=admin,
        json={"max_capacity": 3},
    ).status_code == 409

    vehicle_id = aprobada.json()["vehicle_id"]
    vehiculo = client.get(
        f"/api/v1/vehicles/{vehicle_id}",
        headers={"X-Device-Token": token},
    )
    assert vehiculo.status_code == 200
    assert vehiculo.json()["max_capacity"] == 3

    ubicacion = client.post(
        f"/api/v1/vehicles/{vehicle_id}/location",
        headers={"X-Device-Token": token},
        json={
            "lat": -33.52,
            "lon": -70.60,
            "timestamp": datetime.now(UTC).isoformat(),
            "available_seats": 2,
            "heading": 180,
        },
    )
    assert ubicacion.status_code in (200, 202)

    assert client.post(
        "/api/v1/driver-applications",
        json={"plate": patente, "line_code": linea, "max_capacity": 3},
    ).status_code == 409

    otra_patente = patente_factory()
    rechazada = client.post(
        "/api/v1/driver-applications",
        json={"plate": otra_patente, "line_code": linea, "max_capacity": 4, "note": "Solicitud nueva"},
    )
    assert rechazada.status_code == 201
    rechazada_id = rechazada.json()["id"]
    respuesta = client.post(
        f"/api/v1/admin/driver-applications/{rechazada_id}/reject",
        headers=admin,
        json={"note": "Patente no corresponde"},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "rejected"
    assert respuesta.json()["review_note"] == "Patente no corresponde"
