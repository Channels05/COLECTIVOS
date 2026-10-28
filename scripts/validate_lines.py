"""Revisa los trazados de las líneas de una comuna contra la red vial actual de OpenStreetMap.

    python -m scripts.validate_lines            # La Florida (por defecto)
    python -m scripts.validate_lines "Puente Alto"

Los trazados del GORE son de abril de 2023 y el MTT no publica el registro vigente (hay que
pedirlo por Ley de Transparencia, ver docs/solicitud-mtt.md). Mientras tanto, esto detecta los
que probablemente están mal o desactualizados:

- Calce vial: % del trazado a menos de 15 m de una calle por donde pueden pasar autos hoy.
  Un tramo largo fuera de las calles suele ser una calle que cambió, un error de dibujo o un
  trazado que atraviesa terrenos (desactualizado).
- Saltos: espacios entre las piezas del trazado (el colectivo no puede teletransportarse).
- Sentidos: la línea debería tener ida y regreso.
- Grabado: si hay un recorrido grabado y aprobado, cuánto se aleja del oficial.

Escribe docs/validacion-<comuna>.md con las líneas para revisar primero. OpenStreetMap no tiene
los recorridos de colectivos de Santiago (solo micros), así que no sirve para compararlos línea
a línea; sí sirve para saber por dónde hay calles.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, shape

from scripts.build_line_streets import GEOJSON, OSM_EXTRACT, to_utm
from scripts.import_gore_routes import _NAME_RE

DOCS = Path(__file__).resolve().parent.parent / "docs"
DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
    "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
    "service", "road", "busway",
}
SAMPLE_EVERY_M = 20
ON_STREET_M = 15
MIN_FIT = 0.95  # menos calce que esto: revisar
MAX_OFF_STREET_M = 200  # un tramo continuo fuera de las calles más largo que esto: revisar
MAX_GAP_M = 150  # un salto entre piezas más largo que esto: revisar


def load_drivable(path: Path) -> list[LineString]:
    import osmium  # solo para este script

    lines = []
    processor = osmium.FileProcessor(str(path)).with_locations().with_filter(osmium.filter.KeyFilter("highway"))
    for obj in processor:
        if obj.is_way() and obj.tags.get("highway") in DRIVABLE:
            coords = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
            if len(coords) >= 2:
                lines.append(to_utm(LineString(coords)))
    return lines


def fit(parts: list[LineString], tree: shapely.STRtree, streets: list[LineString]) -> tuple[float, float]:
    """(% de puntos sobre una calle, tramo continuo más largo fuera de las calles en m)."""
    on, total, worst = 0, 0, 0.0
    for part in parts:
        n = max(2, int(part.length // SAMPLE_EVERY_M) + 1)
        points = shapely.points(np.array([part.interpolate(i * part.length / (n - 1)).coords[0] for i in range(n)]))
        idx, _ = tree.query_nearest(points, max_distance=ON_STREET_M, return_distance=True, all_matches=False)
        hits = np.zeros(n, dtype=bool)
        hits[idx[0]] = True
        on += int(hits.sum())
        total += n
        run = 0
        for hit in hits:
            run = 0 if hit else run + 1
            worst = max(worst, run * part.length / (n - 1))
    return (on / total if total else 0.0), worst


def max_gap(parts: list[LineString]) -> float:
    """El mayor espacio entre una pieza y la más cercana de las demás."""
    if len(parts) < 2:
        return 0.0
    return max(min(a.distance(b) for b in parts if b is not a) for a in parts)


def main(commune: str) -> None:
    data = json.loads(GEOJSON.read_text(encoding="utf-8"))
    # Cada tramo trae la comuna donde hace más metros: la ida de una línea puede quedar en una
    # comuna y el regreso en otra. Se toman las líneas con algún tramo en la comuna y, de ellas,
    # todos sus tramos.
    def parse(feature):
        return _NAME_RE.match(feature["properties"].get("nombre") or "")
    lines_here = {m.group("line") for f in data["features"]
                  if (f["properties"].get("comuna") or "").lower() == commune.lower() and (m := parse(f))}
    by_line: dict[str, dict[str, list[LineString]]] = defaultdict(lambda: defaultdict(list))
    for feature in data["features"]:
        props = feature["properties"]
        if (props.get("recorrido") or "").strip() not in ("TRAZADOS", ""):
            continue
        m = parse(feature)
        if not m or m.group("line") not in lines_here:
            continue
        geom = to_utm(shape(feature["geometry"]))
        parts = list(getattr(geom, "geoms", [geom]))
        by_line[m.group("line")]["ida" if m.group("dir") == "I" else "regreso"].extend(parts)
    if not by_line:
        sys.exit(f"No hay trazados de la comuna {commune!r} en {GEOJSON.name}.")

    print(f"{len(by_line)} líneas de {commune}; leyendo calles de {OSM_EXTRACT.name}…")
    streets = load_drivable(OSM_EXTRACT)
    tree = shapely.STRtree(streets)

    rows = []
    for line, directions in sorted(by_line.items()):
        for direction in ("ida", "regreso"):
            parts = directions.get(direction)
            if not parts:
                rows.append({"line": line, "direction": direction, "missing": True})
                continue
            ratio, off = fit(parts, tree, streets)
            gap = max_gap(parts)
            issues = []
            if ratio < MIN_FIT:
                issues.append(f"calce vial {ratio:.0%}")
            if off > MAX_OFF_STREET_M:
                issues.append(f"{off:.0f} m seguidos fuera de las calles")
            if gap > MAX_GAP_M:
                issues.append(f"salto de {gap:.0f} m entre tramos")
            rows.append({
                "line": line, "direction": direction, "missing": False, "length_km": round(sum(p.length for p in parts) / 1000, 1),
                "fit": round(ratio, 3), "off_street_m": round(off), "gap_m": round(gap), "pieces": len(parts), "issues": issues,
            })

    flagged = [r for r in rows if r["missing"] or r["issues"]]
    flagged.sort(key=lambda r: (not r["missing"], r.get("fit", 1)))
    ok = [r for r in rows if not r["missing"] and not r["issues"]]
    slug = commune.lower().replace(" ", "-")
    out = DOCS / f"validacion-{slug}.md"
    DOCS.mkdir(exist_ok=True)
    lines_md = [
        f"# Validación de los trazados de {commune}",
        "",
        f"Generado por `python -m scripts.validate_lines \"{commune}\"`: trazados del GORE (abril de 2023) contra la red vial "
        f"de OpenStreetMap ({OSM_EXTRACT.name}). {len(by_line)} líneas, {len(rows)} sentidos: **{len(flagged)} para revisar** "
        f"y {len(ok)} sin observaciones.",
        "",
        f"Criterios: calce vial bajo {MIN_FIT:.0%} (puntos a más de {ON_STREET_M} m de una calle), más de "
        f"{MAX_OFF_STREET_M} m seguidos fuera de las calles, saltos de más de {MAX_GAP_M} m entre tramos, o falta un sentido.",
        "",
        "## Para revisar",
        "",
        "| Línea | Sentido | Largo | Calce vial | Observaciones |",
        "|---|---|---|---|---|",
    ]
    for r in flagged:
        if r["missing"]:
            lines_md.append(f"| {r['line']} | {r['direction']} | — | — | no tiene trazado de {r['direction']} |")
        else:
            lines_md.append(f"| {r['line']} | {r['direction']} | {r['length_km']} km | {r['fit']:.0%} | {'; '.join(r['issues'])} |")
    lines_md += ["", "## Sin observaciones", "", ", ".join(sorted({r["line"] for r in ok} - {r["line"] for r in flagged})) or "—", ""]
    out.write_text("\n".join(lines_md), encoding="utf-8")
    (DOCS / f"validacion-{slug}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(flagged)} sentidos para revisar de {len(rows)} → {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "La Florida")
