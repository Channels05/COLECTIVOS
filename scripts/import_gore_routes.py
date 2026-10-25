"""Importa a `routes` el GeoJSON generado por `fetch_gore_routes`.

    python -m scripts.import_gore_routes data/gore/colectivos_rm.geojson

Formato de la capa GORE (un feature = un tramo):
  comuna     (agregada por fetch_gore_routes) comuna donde el recorrido hace más metros
  nombre     "233010_I" / "233010_R"  -> código de línea + sentido (I = ida, R = regreso)
  recorrido  TRAZADOS | VIAS ALTERNATIVAS | BUCLE | TRAZADOS NOCTURNOS
  tipo       "TAXIS COLECTIVOS URBANO 200423" -> fecha de corte del registro (20-04-2023)
"""

import argparse
import asyncio
import json
import re
from datetime import date
from pathlib import Path

from shapely.geometry import shape

from app.models import RouteDirection, RouteKind, RouteSource
from scripts.route_import import ewkt, to_multilinestring, upsert_routes

# Acepta las variantes que trae el dato:
#   urbanos  "233010_I", "230007 I", "235012 R"
#   rurales  "20061_ITINERARIO_1_I"  (una línea rural tiene de 1 a 9 itinerarios: todos quedan
#            en el mismo corredor de la línea; el itinerario se conserva en `name`)
_NAME_RE = re.compile(r"^\s*(?P<line>\d+)(?:_ITINERARIO_(?P<itinerary>\d+))?\s*[_ ]?\s*(?P<dir>[IR])\s*$")
_DATE_RE = re.compile(r"(?P<d>\d{2})(?P<m>\d{2})(?P<y>\d{2})\s*$")
_DIRECTIONS = {"I": RouteDirection.IDA, "R": RouteDirection.REGRESO}
_KINDS = {
    "TRAZADOS": RouteKind.TRAZADO,
    "VIAS ALTERNATIVAS": RouteKind.VIA_ALTERNATIVA,
    "BUCLE": RouteKind.BUCLE,
    "TRAZADOS NOCTURNOS": RouteKind.NOCTURNO,
}


def parse_cutoff_date(tipo: str | None) -> date | None:
    match = _DATE_RE.search(tipo or "")
    if not match:
        return None
    return date(2000 + int(match["y"]), int(match["m"]), int(match["d"]))


def parse_features(geojson: dict) -> tuple[list[dict], list[str]]:
    """Devuelve (filas, advertencias). Los tramos con nombre ilegible se omiten y se reportan."""
    # Comuna por recorrido (fetch_gore_routes la calcula); si no viene, la del archivo.
    default_commune = geojson.get("metadata", {}).get("commune") or "Región Metropolitana"
    rows, warnings = [], []
    for feature in geojson["features"]:
        props = feature["properties"]
        name_match = _NAME_RE.match(props.get("nombre") or "")
        geom = to_multilinestring(shape(feature["geometry"]))
        if name_match is None or geom is None:
            warnings.append(f"objectid {props.get('objectid')}: nombre {props.get('nombre')!r} sin línea/sentido")
            continue
        kind = _KINDS.get((props.get("recorrido") or "").strip())
        if kind is None:
            # Los rurales nunca traen `recorrido`: son su trazado, no vale la pena avisar.
            if "RURAL" not in (props.get("tipo") or ""):
                warnings.append(f"objectid {props['objectid']}: recorrido {props.get('recorrido')!r} -> se asume trazado")
            kind = RouteKind.TRAZADO

        rows.append({
            "source_id": str(props["objectid"]),
            "source_date": parse_cutoff_date(props.get("tipo")),
            "line_code": name_match["line"],
            "direction": _DIRECTIONS[name_match["dir"]],
            "kind": kind,
            "name": props["nombre"].strip(),
            "commune": props.get("comuna") or default_commune,
            "geom": ewkt(geom),
            "source_attrs": props,
            "is_active": True,
        })
    return rows, warnings


async def main(path: Path) -> None:
    rows, warnings = parse_features(json.loads(path.read_text(encoding="utf-8")))
    for warning in warnings:
        print(f"[aviso] {warning}")
    upserted, deactivated = await upsert_routes(rows, RouteSource.GORE_RM)
    dates = sorted({str(r["source_date"]) for r in rows})
    print(f"Importados/actualizados {upserted} tramos de {len({r['line_code'] for r in rows})} líneas "
          f"(corte de datos: {', '.join(dates)}); desactivados {deactivated}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("geojson", type=Path)
    asyncio.run(main(parser.parse_args().geojson))
