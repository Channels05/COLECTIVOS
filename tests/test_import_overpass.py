from app.models import RouteDirection
from scripts.fetch_overpass_routes import to_feature_collection
from scripts.import_overpass_routes import parse_features


def _relation(rel_id: int, name: str, ref: str = "E13") -> dict:
    way = {"type": "way", "role": "", "geometry": [{"lat": -33.52, "lon": -70.59}, {"lat": -33.53, "lon": -70.59}]}
    stop = {"type": "node", "role": "stop", "lat": -33.52, "lon": -70.59}
    return {"type": "relation", "id": rel_id, "tags": {"ref": ref, "name": name, "route": "bus"}, "members": [way, stop]}


def test_overpass_api_json_becomes_turbo_style_geojson():
    collection = to_feature_collection({"elements": [_relation(10, "A - B")]})
    feature = collection["features"][0]
    assert feature["id"] == "relation/10"
    assert feature["properties"]["ref"] == "E13"
    assert feature["geometry"]["type"] == "MultiLineString"
    assert len(feature["geometry"]["coordinates"]) == 1  # el paradero (node) no es parte del trazado


def test_pair_without_direction_hint_is_split_into_ida_and_regreso():
    collection = to_feature_collection({"elements": [_relation(20, "B - A"), _relation(10, "A - B")]})
    rows = {row["source_id"]: row["direction"] for row in parse_features(collection)}
    assert rows == {"relation/10": RouteDirection.IDA, "relation/20": RouteDirection.REGRESO}


def test_direction_from_name_is_respected():
    collection = to_feature_collection({"elements": [_relation(10, "Línea 1 Ida"), _relation(20, "Línea 1 Regreso")]})
    rows = {row["source_id"]: row["direction"] for row in parse_features(collection)}
    assert rows == {"relation/10": RouteDirection.IDA, "relation/20": RouteDirection.REGRESO}
