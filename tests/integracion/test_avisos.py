def test_avisos_de_linea_y_generales(client, admin, linea, linea_factory):
    otra = linea_factory()

    aviso_linea = client.post(
        "/api/v1/admin/notices",
        headers=admin,
        json={"line_code": linea.lower(), "message": "Desvío por feria", "days": 7},
    )
    assert aviso_linea.status_code == 201
    assert aviso_linea.json()["line_code"] == linea
    aviso_general = client.post(
        "/api/v1/admin/notices",
        headers=admin,
        json={"message": "Aviso general para todas las líneas", "days": 7},
    )
    assert aviso_general.status_code == 201
    assert aviso_general.json()["line_code"] is None

    inexistente = client.post(
        "/api/v1/admin/notices",
        headers=admin,
        json={"line_code": "T999999", "message": "Aviso inexistente"},
    )
    assert inexistente.status_code == 404

    ficha = client.get(f"/api/v1/lines/{linea}/details")
    assert ficha.status_code == 200
    mensajes = {aviso["message"] for aviso in ficha.json()["notices"]}
    assert "Desvío por feria" in mensajes
    assert "Aviso general para todas las líneas" in mensajes

    ficha_otra = client.get(f"/api/v1/lines/{otra}/details")
    assert ficha_otra.status_code == 200
    mensajes_otra = {aviso["message"] for aviso in ficha_otra.json()["notices"]}
    assert "Desvío por feria" not in mensajes_otra
    assert "Aviso general para todas las líneas" in mensajes_otra

    todos = client.get("/api/v1/notices")
    assert todos.status_code == 200
    assert len(todos.json()) >= 2

    borrado = client.delete(
        f"/api/v1/admin/notices/{aviso_linea.json()['id']}",
        headers=admin,
    )
    assert borrado.status_code == 204
    assert client.delete(
        f"/api/v1/admin/notices/{aviso_general.json()['id']}",
        headers=admin,
    ).status_code == 204

    assert client.post(
        "/api/v1/admin/notices",
        json={"message": "Sin clave"},
    ).status_code in (403, 422)
