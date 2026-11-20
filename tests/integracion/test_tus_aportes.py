def test_resumen_de_aportes(client, admin, aportante, linea):
    inicial = client.get("/api/v1/contributions/me", headers=aportante)
    assert inicial.status_code == 200
    assert inicial.json() == {
        "sightings": 0,
        "sighting_lines": 0,
        "recordings_approved": 0,
        "recordings_pending": 0,
        "lines_approved": 0,
        "reports_sent": 0,
        "reports_applied": 0,
    }

    primer = client.post(
        "/api/v1/contributions/line-info",
        headers=aportante,
        json={"line_code": linea, "fare_day": 900},
    )
    segundo = client.post(
        "/api/v1/contributions/line-info",
        headers=aportante,
        json={"line_code": linea, "fare_night": 1100},
    )
    assert primer.status_code == 201
    assert segundo.status_code == 201

    aplicado = client.post(
        f"/api/v1/admin/line-info/{primer.json()['id']}/apply",
        headers=admin,
        json={},
    )
    assert aplicado.status_code == 200

    tarifas = client.get("/api/v1/contributions/me", headers=aportante)
    assert tarifas.status_code == 200
    assert tarifas.json()["reports_sent"] == 2
    assert tarifas.json()["reports_applied"] == 1

    recorrido = client.post(
        "/api/v1/contributions/route-reports",
        headers=aportante,
        json={"line_code": linea, "kind": "other", "message": "Le falta señal"},
    )
    assert recorrido.status_code == 201

    con_reporte = client.get("/api/v1/contributions/me", headers=aportante)
    assert con_reporte.status_code == 200
    assert con_reporte.json()["reports_sent"] == 3
    assert con_reporte.json()["reports_applied"] == 1

    assert client.get("/api/v1/contributions/me").status_code != 200
