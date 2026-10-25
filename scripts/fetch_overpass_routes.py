"""Ejecuta una consulta Overpass QL contra la API pública y guarda un GeoJSON con el mismo
formato que exporta Overpass Turbo (el que espera `import_overpass_routes`).

    python -m scripts.fetch_overpass_routes data/overpass/colectivos_la_florida.overpassql \
        data/overpass/colectivos_la_florida.geojson

La consulta debe terminar en `out geom;` para que cada way traiga sus coordenadas.
"""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Instancias públicas equivalentes: si la principal está saturada se prueba la siguiente.
OVERPASS_MIRRORS = (OVERPASS_URL, "https://overpass.kumi.systems/api/interpreter")
# La API responde 406 a requests sin un User-Agent identificable.
USER_AGENT = "colectivos-mvp/0.1 (piloto La Florida)"
# Roles de ways que forman el trazado; se excluyen paraderos ("platform", "stop").
TRACK_ROLES = {"", "forward", "backward"}
# Respuestas típicas de una instancia saturada; vale la pena reintentar.
RETRYABLE_STATUS = {429, 502, 503, 504}
ATTEMPTS_PER_MIRROR = 3


def run_query(query: str) -> dict:
    """Ejecuta la consulta con reintentos (espera creciente) y servidores de respaldo."""
    body = urllib.parse.urlencode({"data": query}).encode()
    last_error: Exception | None = None
    for url in OVERPASS_MIRRORS:
        for attempt in range(1, ATTEMPTS_PER_MIRROR + 1):
            request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS:
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            print(f"[overpass] {url} falló ({last_error}); intento {attempt}/{ATTEMPTS_PER_MIRROR}")
            time.sleep(10 * attempt)
    raise RuntimeError(f"Overpass no respondió en ningún servidor: {last_error}")


def relation_to_feature(relation: dict) -> dict | None:
    lines = [
        [(point["lon"], point["lat"]) for point in member["geometry"]]
        for member in relation.get("members", [])
        if member["type"] == "way" and member.get("role", "") in TRACK_ROLES and len(member.get("geometry") or []) >= 2
    ]
    if not lines:
        return None
    osm_id = f"relation/{relation['id']}"
    return {
        "type": "Feature",
        "id": osm_id,
        "properties": {"@id": osm_id, **relation.get("tags", {})},
        "geometry": {"type": "MultiLineString", "coordinates": lines},
    }


def to_feature_collection(osm: dict) -> dict:
    relations = (e for e in osm["elements"] if e["type"] == "relation")
    features = [f for f in map(relation_to_feature, relations) if f is not None]
    return {"type": "FeatureCollection", "features": features}


def main(query_path: Path, out_path: Path) -> None:
    collection = to_feature_collection(run_query(query_path.read_text(encoding="utf-8")))
    out_path.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    print(f"{len(collection['features'])} rutas -> {out_path}")
    for feature in collection["features"]:
        props = feature["properties"]
        print(f"  {props['@id']}  ref={props.get('ref')}  {props.get('name')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.query, args.output)
