"""Descarga los trazados de taxis colectivos del geoportal del GORE RM.

    # Toda la Región Metropolitana (cada recorrido queda con su comuna principal)
    python -m scripts.fetch_gore_routes --output data/gore/colectivos_rm.geojson

    # Solo una comuna
    python -m scripts.fetch_gore_routes --commune "La Florida" \
        --output data/gore/colectivos_la_florida.geojson

Fuente: capa "MOV_rutas_taxis_colectivos" (ArcGIS Feature Service). Los límites comunales se
obtienen de OSM. A cada recorrido se le agrega en `properties`:
  comuna   la comuna donde recorre más metros (la "principal")
  comunas  todas las comunas que cruza con al menos --min-inside-m metros
Con --commune, solo entran los recorridos con al menos --min-inside-m metros dentro de ella
(así se excluyen líneas que solo rozan el borde).
"""

import argparse
import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import shapely
from pyproj import Transformer
from shapely.geometry import LineString, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from app.core.config import get_settings
from scripts.fetch_overpass_routes import USER_AGENT, run_query

PORTAL = "https://geo.gobiernosantiago.cl"
ITEM_ID = "596ce3f0a30d464a93785d0f6367d138"
LAYER_URL = f"{PORTAL}/server/rest/services/Hosted/MOV_rutas_taxis_colectivos/FeatureServer/0"
PAGE_SIZE = 1000  # la capa permite hasta 2000 por request

_to_metric = Transformer.from_crs("EPSG:4326", get_settings().metric_crs, always_xy=True).transform


def _metric(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(geom, _to_metric, interleaved=False)


def _get_json(url: str, params: dict) -> dict:
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def _relation_polygon(relation: dict) -> BaseGeometry:
    outer = [
        LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
        for m in relation["members"]
        if m["type"] == "way" and m.get("role") == "outer"
    ]
    return unary_union(list(polygonize(unary_union(outer))))


def commune_polygons() -> dict[str, BaseGeometry]:
    """Límites de las comunas de la Región Metropolitana (admin_level=8) desde OSM, en WGS84."""
    osm = run_query(
        '[out:json][timeout:180];'
        'area["name"="Región Metropolitana de Santiago"]["admin_level"="4"]->.rm;'
        'rel(area.rm)["boundary"="administrative"]["admin_level"="8"];out geom;'
    )
    return {rel["tags"]["name"]: _relation_polygon(rel) for rel in osm["elements"] if "name" in rel.get("tags", {})}


def fetch_features(bbox: tuple[float, float, float, float] | None) -> list[dict]:
    """Features de la capa (todas, o las que intersectan el bbox), paginando. Geometría en WGS84."""
    params = {
        "where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson",
        "orderByFields": "objectid", "resultRecordCount": PAGE_SIZE,
    }
    if bbox:
        params |= {
            "geometry": ",".join(map(str, bbox)), "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        }
    features, offset = [], 0
    while True:
        page = _get_json(f"{LAYER_URL}/query", params | {"resultOffset": offset})
        features += page["features"]
        if not page.get("properties", {}).get("exceededTransferLimit"):
            return features
        offset += PAGE_SIZE


def assign_communes(features: list[dict], communes: dict[str, BaseGeometry], min_inside_m: float) -> None:
    """Agrega `comuna` (principal, por metros recorridos) y `comunas` a cada feature."""
    names = list(communes)
    polygons = [_metric(communes[n]) for n in names]
    tree = STRtree(polygons)
    for feature in features:
        line = _metric(shape(feature["geometry"]))
        lengths = {names[i]: line.intersection(polygons[i]).length for i in tree.query(line)}
        crossed = sorted((n for n, length in lengths.items() if length >= min_inside_m), key=lambda n: -lengths[n])
        feature["properties"]["comuna"] = max(lengths, key=lengths.get) if lengths else None
        feature["properties"]["comunas"] = crossed


def main(commune: str | None, output: Path, min_inside_m: float) -> None:
    item = _get_json(f"{PORTAL}/portal/sharing/rest/content/items/{ITEM_ID}", {"f": "json"})
    modified = datetime.fromtimestamp(item["modified"] / 1000, UTC).date()

    communes = commune_polygons()
    if commune:
        if commune not in communes:
            raise SystemExit(f"No se encontró la comuna {commune!r} en OSM")
        target = _metric(communes[commune])
        candidates = fetch_features(communes[commune].bounds)
        selected = [
            f for f in candidates if _metric(shape(f["geometry"])).intersection(target).length >= min_inside_m
        ]
    else:
        candidates = selected = fetch_features(None)
    assign_communes(selected, communes, min_inside_m)

    collection = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "gore_rm",
            "item_id": ITEM_ID,
            "layer_url": LAYER_URL,
            "item_modified": modified.isoformat(),
            "commune": commune,  # None = toda la región
            "min_inside_m": min_inside_m,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        "features": selected,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    lines = {f["properties"]["nombre"].strip().split("_")[0].split(" ")[0] for f in selected}
    by_commune: dict[str, int] = {}
    for f in selected:
        by_commune[f["properties"]["comuna"] or "(fuera de la RM)"] = by_commune.get(f["properties"]["comuna"] or "(fuera de la RM)", 0) + 1
    print(f"Capa modificada por última vez: {modified}")
    print(f"{len(selected)} recorridos ({len(lines)} líneas) de {len(candidates)} descargados -> {output}")
    print(f"Comunas principales: {len(by_commune)}; top 5: {sorted(by_commune.items(), key=lambda kv: -kv[1])[:5]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commune", help="filtrar a una comuna (por defecto: toda la Región Metropolitana)")
    parser.add_argument("--output", type=Path, default=Path("data/gore/colectivos_rm.geojson"))
    parser.add_argument("--min-inside-m", type=float, default=500)
    args = parser.parse_args()
    main(args.commune, args.output, args.min_inside_m)
