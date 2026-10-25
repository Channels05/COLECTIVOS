from datetime import date

from app.models import RouteDirection, RouteKind
from scripts.import_gore_routes import parse_cutoff_date, parse_features

LINE = {"type": "LineString", "coordinates": [[-70.59, -33.52], [-70.59, -33.53]]}


def _feature(objectid: int, nombre: str, recorrido: str | None = "TRAZADOS") -> dict:
    return {
        "type": "Feature",
        "geometry": LINE,
        "properties": {
            "objectid": objectid,
            "nombre": nombre,
            "recorrido": recorrido,
            "tipo": "TAXIS COLECTIVOS URBANO 200423",
        },
    }


def _parse(*features: dict):
    return parse_features({"type": "FeatureCollection", "metadata": {"commune": "La Florida"}, "features": list(features)})


def test_cutoff_date_comes_from_tipo():
    assert parse_cutoff_date("TAXIS COLECTIVOS URBANO 200423") == date(2023, 4, 20)
    assert parse_cutoff_date("TAXIS COLECTIVOS RURALES AL 200423") == date(2023, 4, 20)
    assert parse_cutoff_date(None) is None


def test_name_gives_line_and_direction_including_malformed_variants():
    rows, warnings = _parse(_feature(1, "233010_I"), _feature(2, "233010_R"), _feature(3, "230007 I"))
    assert [(r["line_code"], r["direction"]) for r in rows] == [
        ("233010", RouteDirection.IDA),
        ("233010", RouteDirection.REGRESO),
        ("230007", RouteDirection.IDA),
    ]
    assert warnings == []


def test_rural_itinerary_names_are_parsed_into_the_line_without_warnings():
    rural = _feature(7, "20061_ITINERARIO_3_R", recorrido=None)
    rural["properties"]["tipo"] = "TAXIS COLECTIVOS RURALES AL 200423"
    rows, warnings = _parse(rural)
    assert (rows[0]["line_code"], rows[0]["direction"], rows[0]["kind"]) == ("20061", RouteDirection.REGRESO, RouteKind.TRAZADO)
    assert rows[0]["name"] == "20061_ITINERARIO_3_R"
    assert warnings == []


def test_unreadable_name_is_skipped_with_warning():
    rows, warnings = _parse(_feature(9, "236038 _"))
    assert rows == [] and "236038 _" in warnings[0]


def test_recorrido_maps_to_route_kind():
    rows, _ = _parse(
        _feature(1, "233021_I", "TRAZADOS"),
        _feature(2, "233021_I", "VIAS ALTERNATIVAS"),
        _feature(3, "233010_I", "BUCLE"),
        _feature(4, "233023_I", "TRAZADOS NOCTURNOS"),
    )
    assert [r["kind"] for r in rows] == [RouteKind.TRAZADO, RouteKind.VIA_ALTERNATIVA, RouteKind.BUCLE, RouteKind.NOCTURNO]
    assert {r["source_id"] for r in rows} == {"1", "2", "3", "4"}  # objectid: distingue tramos de un mismo nombre
    assert all(r["source_date"] == date(2023, 4, 20) and r["geom"].startswith("SRID=4326;MULTILINESTRING") for r in rows)


def test_commune_comes_from_each_feature_or_falls_back_to_file():
    with_commune = _feature(1, "234003_I")
    with_commune["properties"]["comuna"] = "Puente Alto"
    rows, _ = parse_features({"type": "FeatureCollection", "metadata": {"commune": None},
                              "features": [with_commune, _feature(2, "234003_R")]})
    assert [r["commune"] for r in rows] == ["Puente Alto", "Región Metropolitana"]


def test_missing_recorrido_defaults_to_trazado_with_warning():
    rows, warnings = _parse(_feature(5, "233001_I", None))
    assert rows[0]["kind"] == RouteKind.TRAZADO and warnings
