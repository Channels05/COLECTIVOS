from app.services.places import fallback_query


def test_quita_la_palabra_de_transporte_del_comienzo():
    assert fallback_query("Metro Bellavista de La Florida") == "Bellavista de La Florida"
    assert fallback_query("estación  Plaza de Puente Alto") == "Plaza de Puente Alto"


def test_sin_palabra_de_transporte_no_reintenta():
    assert fallback_query("Mall Plaza Vespucio") is None
    assert fallback_query("Metro") is None
