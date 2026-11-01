"""Calles por donde pasa cada línea oficial, en orden (data/gore/line_streets.json).

El archivo lo genera `scripts/build_line_streets` desde OpenStreetMap y va en git; se lee una vez.
"""

import json
from functools import cache
from pathlib import Path

STREETS = Path(__file__).resolve().parents[2] / "data" / "gore" / "line_streets.json"


@cache
def _load() -> dict[str, dict]:
    if not STREETS.exists():
        return {}
    return json.loads(STREETS.read_text(encoding="utf-8"))


def sequence(line_code: str) -> dict[str, list[str]]:
    """{"ida": [...], "regreso": [...]}; vacío si la línea no tiene trazado oficial con calles."""
    return _load().get(line_code, {}).get("sequence", {})
