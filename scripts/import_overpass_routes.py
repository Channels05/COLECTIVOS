"""Importa a `routes` un GeoJSON exportado desde Overpass Turbo (o `fetch_overpass_routes`).

    python -m scripts.import_overpass_routes data/overpass/ejemplo_bus_e13.geojson

Idempotente: upsert por (source="osm", source_id="relation/<id>").
Luego llamar POST /api/v1/routes/reload (o reiniciar la API) para refrescar el caché.
"""

import argparse
import asyncio
import json
from pathlib import Path

from shapely.geometry import shape

from app.models import RouteDirection, RouteKind, RouteSource
from scripts.route_import import ewkt, to_multilinestring, upsert_routes

_RETURN_WORDS = ("regreso", "vuelta", "retorno")


def _tags(feature: dict) -> dict:
    # Overpass Turbo exporta los tags planos en `properties` (formato nuevo)
    # o anidados en `properties.tags` (formato antiguo de osmtogeojson).
    props = feature.get("properties", {})
    return props.get("tags", props)


def _osm_relation_id(feature: dict) -> str | None:
    raw = str(feature.get("id") or feature.get("properties", {}).get("@id", ""))  # "relation/123"
    return raw if raw.startswith("relation/") else None


def _direction(tags: dict) -> RouteDirection:
    # OSM no tiene un tag estándar ida/regreso: heurística por nombre. Revisar a mano en el piloto.
    name = (tags.get("name") or "").lower()
    return RouteDirection.REGRESO if any(w in name for w in _RETURN_WORDS) else RouteDirection.IDA


def parse_features(geojson: dict) -> list[dict]:
    rows = []
    for feature in geojson["features"]:
        geom = to_multilinestring(shape(feature["geometry"]))
        source_id = _osm_relation_id(feature)
        if geom is None or source_id is None:
            continue  # paraderos (Point) u otros elementos del export

        tags = _tags(feature)
        line_code = tags.get("ref")
        if not line_code:
            print(f"[skip] relación sin `ref`: {tags.get('name')}")
            continue

        rows.append({
            "source_id": source_id,
            "source_date": None,
            "line_code": str(line_code),
            "direction": _direction(tags),
            "kind": RouteKind.TRAZADO,
            "name": tags.get("name"),
            "commune": "La Florida",
            "geom": ewkt(geom),
            "source_attrs": tags,
            "is_active": True,
        })
    _split_pairs_by_direction(rows)
    return rows


def _osm_number(row: dict) -> int:
    return int(row["source_id"].split("/")[1])


def _split_pairs_by_direction(rows: list[dict]) -> None:
    """Si una línea trae exactamente 2 relaciones y el nombre no permitió distinguirlas
    (ej. "A - B" / "B - A"), la de menor id OSM queda como ida y la otra como regreso."""
    by_line: dict[str, list[dict]] = {}
    for row in rows:
        by_line.setdefault(row["line_code"], []).append(row)
    for pair in by_line.values():
        if len(pair) == 2 and pair[0]["direction"] == pair[1]["direction"]:
            pair.sort(key=_osm_number)
            pair[0]["direction"], pair[1]["direction"] = RouteDirection.IDA, RouteDirection.REGRESO


async def main(path: Path) -> None:
    rows = parse_features(json.loads(path.read_text(encoding="utf-8")))
    if not rows:
        print("No se encontraron rutas en el archivo")
        return
    upserted, deactivated = await upsert_routes(rows, RouteSource.OSM)
    print(f"Importadas/actualizadas {upserted} rutas; desactivadas {deactivated}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("geojson", type=Path)
    asyncio.run(main(parser.parse_args().geojson))
