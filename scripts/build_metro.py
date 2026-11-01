"""Líneas de Metro (y el Tren Nos) con sus estaciones en orden, para combinar con colectivos.

    pip install osmium   # solo para este script, no lo usa el servidor
    python -m scripts.build_metro

Lee las relaciones `route=subway`/`train` del mismo extracto de OpenStreetMap que usa
`build_line_streets` (data/osm/Santiago.osm.pbf). De cada línea toma un sentido ("Línea 5:
Dirección Vicente Valdés"; no las rutas expresas Verde/Roja, que se saltan estaciones) y sus
paradas en orden. El resultado (data/transporte/metro.json, pocos KB) va en git y lo carga el
planificador de viajes; este script solo se vuelve a correr si se abre una línea o estación.
"""

import json
import re
from pathlib import Path

import osmium

DATA = Path(__file__).resolve().parent.parent / "data"
OSM_EXTRACT = DATA / "osm" / "Santiago.osm.pbf"
OUTPUT = DATA / "transporte" / "metro.json"

# "Línea 5: Dirección Vicente Valdés" / "Tren Nos – Estación Central: Dirección Nos"
_MAIN = re.compile(r"^(Línea \w+|Tren Nos – Estación Central): Dirección ")
# Los nombres de las paradas a veces traen el andén o el tipo: "Bellavista de La Florida (L5)".
_SUFFIX = re.compile(r"\s*\((L\d\w?|Andén.*|Metro.*)\)$")


def clean(name: str) -> str:
    name = _SUFFIX.sub("", name.strip())
    for prefix in ("Estación ", "Metro "):
        name = name.removeprefix(prefix)
    return name


def main() -> None:
    routes: dict[str, dict] = {}
    wanted_nodes: set[int] = set()
    for obj in osmium.FileProcessor(str(OSM_EXTRACT)).with_filter(osmium.filter.KeyFilter("route")):
        if not obj.is_relation() or obj.tags.get("route") not in ("subway", "train"):
            continue
        name = obj.tags.get("name") or ""
        if not _MAIN.match(name):
            continue
        ref = obj.tags.get("ref") or "Tren Nos"
        stops = [m.ref for m in obj.members if m.type == "n" and m.role.startswith("stop")]
        if len(stops) < 2 or ref in routes:  # un sentido por línea: el otro es el mismo al revés
            continue
        routes[ref] = {"ref": ref, "name": name.split(":")[0], "color": obj.tags.get("colour"), "stops": stops}
        wanted_nodes.update(stops)

    nodes: dict[int, tuple[str, float, float]] = {}
    for obj in osmium.FileProcessor(str(OSM_EXTRACT)).with_filter(osmium.filter.IdFilter(wanted_nodes)):
        if obj.is_node() and obj.location.valid():
            nodes[obj.id] = (clean(obj.tags.get("name") or ""), round(obj.location.lat, 6), round(obj.location.lon, 6))

    lines = []
    for ref in sorted(routes, key=lambda r: (r == "Tren Nos", r)):
        route = routes[ref]
        stations = []
        for node_id in route["stops"]:
            if node_id not in nodes or not nodes[node_id][0]:
                continue
            name, lat, lon = nodes[node_id]
            if stations and stations[-1]["name"] == name:
                continue
            stations.append({"name": name, "lat": lat, "lon": lon})
        lines.append({"ref": ref, "name": route["name"], "color": route["color"], "stations": stations})
        print(f"{ref}: {len(stations)} estaciones ({stations[0]['name']} – {stations[-1]['name']})")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"source": "OpenStreetMap (ODbL)", "lines": lines}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {OUTPUT}")


if __name__ == "__main__":
    main()
