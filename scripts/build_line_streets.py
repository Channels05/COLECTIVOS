"""Calcula por qué calles pasa cada línea oficial, para describirla en la app.

    pip install osmium   # solo para este script, no lo usa el servidor
    curl -o data/osm/Santiago.osm.pbf https://download.bbbike.org/osm/bbbike/Santiago/Santiago.osm.pbf
    python -m scripts.build_line_streets

El registro del GORE solo trae el código de la línea ("230007"), que ningún pasajero conoce, y
no el texto del letrero. Lo más reconocible que se puede derivar del trazado son sus calles
principales: "Por Av. Vicuña Mackenna, Walker Martínez y Av. La Florida".

1. Lee las calles con nombre de un extracto de OpenStreetMap (BBBike "Santiago", ~40 MB: cubre
   el Gran Santiago; las líneas rurales que salen de ahí quedan sin descripción). Overpass no
   sirve para esto: la API pública suele caerse con consultas de este tamaño.
2. Recorre cada trazado cada 20 m y anota la calle más cercana (a menos de 12 m).
3. Se queda con las calles más largas del recorrido, en el orden en que aparecen en la ida, y
   guarda además la secuencia completa de calles de cada sentido (la ficha de la línea en la app).

El resultado (data/gore/line_streets.json, pocos KB) va en git y lo aplica `describe_lines`
al arrancar el servidor; este script solo se vuelve a correr si cambian los trazados.
"""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import osmium
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, shape

from scripts.import_gore_routes import _NAME_RE

DATA = Path(__file__).resolve().parent.parent / "data"
GEOJSON = DATA / "gore" / "colectivos_rm.geojson"
OSM_EXTRACT = DATA / "osm" / "Santiago.osm.pbf"
OUTPUT = DATA / "gore" / "line_streets.json"

SAMPLE_EVERY_M = 20
MAX_STREET_DISTANCE_M = 12
MIN_STREET_LENGTH_M = 300  # menos que esto es un tramo de paso, no "por dónde va"
MAX_STREETS = 3
MIN_RUN_M = 120  # en la secuencia de calles: menos que esto seguido es un cruce o una esquina
# Calles de servicio, estacionamientos y senderos tienen nombre a veces, pero no describen nada.
HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
    "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
}
# Solo el trazado principal: las vías alternativas y los bucles no son "por dónde va".
MAIN_KINDS = {"TRAZADOS", ""}

_utm = Transformer.from_crs(4326, 32719, always_xy=True)


def to_utm(geom):
    return shapely.transform(geom, lambda xy: np.column_stack(_utm.transform(xy[:, 0], xy[:, 1])))


def load_streets(path: Path) -> tuple[list[str], list[LineString]]:
    names, lines = [], []
    processor = osmium.FileProcessor(str(path)).with_locations().with_filter(osmium.filter.KeyFilter("highway"))
    for obj in processor:
        if not obj.is_way() or obj.tags.get("highway") not in HIGHWAYS or "name" not in obj.tags:
            continue
        coords = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
        if len(coords) >= 2:
            names.append(obj.tags["name"])
            lines.append(to_utm(LineString(coords)))
    return names, lines


def short_name(name: str) -> str:
    # La calzada lateral de una avenida es, para el pasajero, la misma avenida.
    name = name.removesuffix(" Lateral")
    for long, short in (("Avenida ", "Av. "), ("Pasaje ", "Pje. "), ("General ", "Gral. ")):
        if name.startswith(long):
            return short + name[len(long):]
    return name


def street_sequence(visited: list[str]) -> list[str]:
    """Las calles en el orden en que las recorre ("Rojas Magallanes → Walker Martínez → …").

    Se ignoran los tramos cortos (cruces, esquinas: menos de MIN_RUN_M seguidos) y se juntan las
    repeticiones que quedan al sacarlos.
    """
    runs: list[list] = []  # [calle, metros seguidos]
    for name in visited:
        # Las calzadas de una avenida ("Av. Vicuña Mackenna Oriente" y "Poniente") son la misma.
        name = name.removesuffix(" Oriente").removesuffix(" Poniente")
        if runs and runs[-1][0] == name:
            runs[-1][1] += SAMPLE_EVERY_M
        else:
            runs.append([name, SAMPLE_EVERY_M])
    ordered: list[str] = []
    for name, meters in runs:
        if meters >= MIN_RUN_M and (not ordered or ordered[-1] != name):
            ordered.append(name)
    return ordered


def describe(streets: list[str]) -> str:
    if len(streets) == 1:
        return f"Por {streets[0]}"
    return f"Por {', '.join(streets[:-1])} y {streets[-1]}"


def main() -> None:
    geojson = json.loads(GEOJSON.read_text(encoding="utf-8"))
    by_line: dict[str, dict[str, list]] = defaultdict(lambda: {"I": [], "R": []})
    for feature in geojson["features"]:
        props = feature["properties"]
        match = _NAME_RE.match(props.get("nombre") or "")
        if not match or (props.get("recorrido") or "").strip() not in MAIN_KINDS:
            continue
        by_line[match["line"]][match["dir"]].append(shape(feature["geometry"]))

    names, street_lines = load_streets(OSM_EXTRACT)
    print(f"{len(names)} tramos de calles con nombre en {OSM_EXTRACT.name}", flush=True)
    tree = shapely.STRtree(street_lines)

    result = {}
    for code, dirs in sorted(by_line.items()):
        # "21 de Mayo" y "Av. 21 de Mayo" son la misma calle para el pasajero: se cuentan juntas
        # y se muestra la variante con más metros.
        length_by_key: dict[str, float] = defaultdict(float)
        length_by_name: dict[str, float] = defaultdict(float)
        first_seen: dict[str, int] = {}
        sequence: dict[str, list[str]] = {}
        for direction in ("I", "R"):
            visited: list[str] = []  # calle de cada muestra, en el orden del trazado
            for geom in dirs[direction]:
                multi = geom if isinstance(geom, MultiLineString) else MultiLineString([geom])
                for part in to_utm(multi).geoms:
                    points = shapely.line_interpolate_point(part, np.arange(0, part.length, SAMPLE_EVERY_M))
                    point_idx, street_idx = tree.query_nearest(points, max_distance=MAX_STREET_DISTANCE_M, all_matches=False)
                    # query_nearest devuelve en orden de punto; un punto sin calle cerca no aparece.
                    for s in street_idx[np.argsort(point_idx, kind="stable")]:
                        name = short_name(names[s])
                        key = name.removeprefix("Av. ")
                        length_by_key[key] += SAMPLE_EVERY_M
                        length_by_name[name] += SAMPLE_EVERY_M
                        visited.append(name)
                        if direction == "I":
                            first_seen.setdefault(key, len(first_seen))
            if visited:
                sequence["ida" if direction == "I" else "regreso"] = street_sequence(visited)
        display = {}
        for name, _ in sorted(length_by_name.items(), key=lambda kv: kv[1]):
            display[name.removeprefix("Av. ")] = name  # la más larga queda al final
        longest = [k for k, m in sorted(length_by_key.items(), key=lambda kv: -kv[1]) if m >= MIN_STREET_LENGTH_M]
        main_streets = [display[k] for k in sorted(longest[:MAX_STREETS], key=lambda k: first_seen.get(k, 1_000))]
        if main_streets:
            result[code] = {"streets": main_streets, "description": describe(main_streets), "sequence": sequence}

    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"{len(result)} de {len(by_line)} líneas con calles -> {OUTPUT}")


if __name__ == "__main__":
    main()
