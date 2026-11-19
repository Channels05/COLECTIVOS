def test_ficha_y_reportes_de_tarifa(client, admin, aportante, linea, linea_factory):
    ficha = client.get(f"/api/v1/lines/{linea}/details")
    assert ficha.status_code == 200
    assert ficha.json()["live_vehicles"] == 0
    assert client.get("/api/v1/lines/T999999/details").status_code == 404

    assert client.post(
        "/api/v1/contributions/line-info",
        headers=aportante,
        json={"line_code": linea},
    ).status_code == 422
    assert client.post(
        "/api/v1/contributions/line-info",
        headers=aportante,
        json={"line_code": linea, "first_departure": "25:00"},
    ).status_code == 422

    creado = client.post(
        "/api/v1/contributions/line-info",
        headers=aportante,
        json={
            "line_code": linea,
            "fare_day": 900,
            "fare_night": 1100,
            "first_departure": "06:15",
            "last_departure": "23:30",
        },
    )
    assert creado.status_code == 201
    report_id = creado.json()["id"]
    assert creado.json()["status"] == "pending"

    pendientes = client.get("/api/v1/admin/line-info", headers=admin)
    assert pendientes.status_code == 200
    aporte = next((item for item in pendientes.json() if item["id"] == report_id), None)
    assert aporte is not None
    assert "current" in aporte

    aplicado = client.post(
        f"/api/v1/admin/line-info/{report_id}/apply",
        headers=admin,
        json={"fare_night": 950},
    )
    assert aplicado.status_code == 200
    assert aplicado.json()["status"] == "applied"
    assert client.post(
        f"/api/v1/admin/line-info/{report_id}/apply",
        headers=admin,
        json={"fare_night": 950},
    ).status_code == 409

    ficha = client.get(f"/api/v1/lines/{linea}/details")
    assert ficha.status_code == 200
    assert ficha.json()["fare_day"] == 900
    assert ficha.json()["fare_night"] == 950
    assert ficha.json()["first_departure"] == "06:15"

    otro = client.post(
        "/api/v1/contributions/line-info",
        headers=aportante,
        json={"line_code": linea, "fare_day": 7777},
    )
    assert otro.status_code == 201
    otro_id = otro.json()["id"]
    descartado = client.post(
        f"/api/v1/admin/line-info/{otro_id}/discard",
        headers=admin,
    )
    assert descartado.status_code == 200
    assert descartado.json()["status"] == "discarded"
    assert client.get(f"/api/v1/lines/{linea}/details").json()["fare_day"] == 900

    linea_b = linea_factory()
    linea_c = linea_factory()
    importado = client.post(
        "/api/v1/admin/line-info/import",
        headers=admin,
        json={"csv": (
            f"línea,día,noche,primero,último\n"
            f"{linea_b},900,1100,06:00,23:00\n"
            f"{linea_c},1000,,07:00,\n"
            "T999998,900,1100,06:00,23:00\n"
            f"{linea_b},50000,,, \n"
            f"{linea_c},800,900,25:00,23:00\n"
        )},
    )
    assert importado.status_code == 200
    assert importado.json()["updated"] == 2
    assert len(importado.json()["errors"]) == 3

    ficha_b = client.get(f"/api/v1/lines/{linea_b}/details").json()
    ficha_c = client.get(f"/api/v1/lines/{linea_c}/details").json()
    assert ficha_b["fare_day"] == 900
    assert ficha_b["fare_night"] == 1100
    assert ficha_c["fare_day"] == 1000
    assert ficha_c["first_departure"] == "07:00"

    segunda_importacion = client.post(
        "/api/v1/admin/line-info/import",
        headers=admin,
        json={"csv": f"{linea_b},950,,,"},
    )
    assert segunda_importacion.status_code == 200
    assert segunda_importacion.json()["updated"] == 1
    assert segunda_importacion.json()["errors"] == []

    ficha_b = client.get(f"/api/v1/lines/{linea_b}/details").json()
    assert ficha_b["fare_day"] == 950
    assert ficha_b["fare_night"] == 1100
    assert ficha_b["first_departure"] == "06:00"
