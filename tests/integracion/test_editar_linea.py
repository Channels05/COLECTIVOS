def test_editar_linea_oficial(client, admin, linea):
    cambiado = client.patch(
        f"/api/v1/admin/lines/{linea}",
        headers=admin,
        json={"name": "  Metro Prueba  "},
    )
    assert cambiado.status_code == 200
    assert cambiado.json()["name"] == "Metro Prueba"

    borrada = client.patch(
        f"/api/v1/admin/lines/{linea}",
        headers=admin,
        json={"commune": "   "},
    )
    assert borrada.status_code == 200
    assert borrada.json()["commune"] is None
    assert borrada.json()["name"] == "Metro Prueba"

    assert client.patch(
        f"/api/v1/admin/lines/{linea}",
        headers=admin,
        json={"name": 123},
    ).status_code == 422
    assert client.patch(
        "/api/v1/admin/lines/T999999",
        headers=admin,
        json={"name": "Inexistente"},
    ).status_code == 404
