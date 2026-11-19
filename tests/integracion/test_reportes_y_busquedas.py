def test_reportes_resolucion_y_descarte(client, admin, aportante, linea):
    creado = client.post(
        "/api/v1/contributions/route-reports",
        headers=aportante,
        json={
            "line_code": linea,
            "kind": "route_changed",
            "message": "Ya no pasa por esta esquina",
            "lat": -33.52,
            "lon": -70.60,
        },
    )
    assert creado.status_code == 201
    assert creado.json()["status"] == "pending"
    report_id = creado.json()["id"]

    assert client.post(
        "/api/v1/contributions/route-reports",
        headers=aportante,
        json={"line_code": linea, "kind": "invalido"},
    ).status_code == 422
    assert client.post(
        "/api/v1/contributions/route-reports",
        headers=aportante,
        json={"line_code": "T999999", "kind": "sign"},
    ).status_code == 404

    pendientes = client.get("/api/v1/admin/route-reports", headers=admin)
    assert pendientes.status_code == 200
    reporte = next((item for item in pendientes.json() if item["id"] == report_id), None)
    assert reporte is not None
    assert reporte["lat"] == -33.52
    assert reporte["lon"] == -70.60

    resuelto = client.post(
        f"/api/v1/admin/route-reports/{report_id}/resolve",
        headers=admin,
    )
    assert resuelto.status_code == 200
    assert resuelto.json()["status"] == "applied"
    assert client.post(
        f"/api/v1/admin/route-reports/{report_id}/resolve",
        headers=admin,
    ).status_code == 409

    otro = client.post(
        "/api/v1/contributions/route-reports",
        headers=aportante,
        json={"line_code": linea, "kind": "other", "message": "Información duplicada"},
    )
    assert otro.status_code == 201
    descartado = client.post(
        f"/api/v1/admin/route-reports/{otro.json()['id']}/discard",
        headers=admin,
    )
    assert descartado.status_code == 200
    assert descartado.json()["status"] == "discarded"


def test_lugar_y_viaje_sin_resultado(client, admin, monkeypatch):
    from app.services import places

    async def sin_resultados(query: str) -> list[dict]:
        return []

    monkeypatch.setattr(places, "_nominatim", sin_resultados)
    lugar = client.get("/api/v1/places/search", params={"q": "Calle Que No Existe 123"})
    assert lugar.status_code == 200
    assert lugar.json() == []

    errores = client.get(
        "/api/v1/admin/search-misses",
        headers=admin,
        params={"days": 1},
    )
    assert errores.status_code == 200
    assert any(
        item["query"] == "calle que no existe 123"
        for item in errores.json()["places"]
    )

    viaje = client.get(
        "/api/v1/trips/plan",
        params={
            "from_lat": -33.30,
            "from_lon": -70.40,
            "to_lat": -33.33,
            "to_lon": -70.43,
        },
    )
    assert viaje.status_code == 200
    assert viaje.json()["options"] == []
    assert viaje.json()["combos"] == []

    errores = client.get(
        "/api/v1/admin/search-misses",
        headers=admin,
        params={"days": 1},
    )
    assert errores.status_code == 200
    assert any(
        item["to_lat"] == -33.33 and item["to_lon"] == -70.43
        for item in errores.json()["trips"]
    )
