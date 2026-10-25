"""Simula una flota de colectivos repartida por la Región Metropolitana, para demos y pruebas.

    python -m scripts.simulate_fleet --api http://127.0.0.1:8000
    python -m scripts.simulate_fleet --per-commune 1 --pilot-lines 6 --include-rural

Toma `--per-commune` líneas urbanas de cada comuna (por comuna principal del trazado) y
`--pilot-lines` de la comuna piloto; registra un auto por línea la primera vez y guarda sus
credenciales en `.demo/flota_demo.json` (gitignored) para reutilizarlas en las siguientes
ejecuciones. Cada auto recorre su trazado ida y vuelta desde un punto al azar.
"""

import argparse
import asyncio
import json
import random
import string
import threading
from pathlib import Path

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from scripts.simulate_driver import simulate

CREDENTIALS_FILE = Path(".demo/flota_demo.json")

# Líneas con trazado principal activo, agrupadas por comuna; orden estable por código.
_LINES_SQL = text(
    """
    SELECT commune, line_code
    FROM routes
    WHERE source = 'gore_rm' AND is_active AND kind = 'trazado'
      AND (:include_rural OR source_attrs->>'tipo' NOT LIKE '%RURAL%')
    GROUP BY commune, line_code
    ORDER BY commune, line_code
    """
)


async def pick_lines(per_commune: int, pilot: str, pilot_lines: int, include_rural: bool, seed: int) -> list[tuple[str, str]]:
    async with SessionLocal() as session:
        rows = (await session.execute(_LINES_SQL, {"include_rural": include_rural})).all()
    await engine.dispose()
    by_commune: dict[str, list[str]] = {}
    for commune, line in rows:
        by_commune.setdefault(commune, []).append(line)
    rng = random.Random(seed)
    chosen = []
    for commune, lines in sorted(by_commune.items()):
        k = pilot_lines if commune == pilot else per_commune
        chosen += [(commune, line) for line in rng.sample(lines, min(k, len(lines)))]
    return chosen


def _random_plate(rng: random.Random) -> str:
    # Formato actual de patentes chilenas: 4 letras (sin vocales) + 2 dígitos.
    letters = "".join(c for c in string.ascii_uppercase if c not in "AEIOUQÑ")
    return "".join(rng.choices(letters, k=4)) + f"{rng.randint(10, 99)}"


def ensure_vehicles(api: str, lines: list[tuple[str, str]]) -> list[dict]:
    """Devuelve credenciales por línea, registrando solo los autos que aún no existen."""
    saved: dict[str, dict] = json.loads(CREDENTIALS_FILE.read_text()) if CREDENTIALS_FILE.exists() else {}
    admin = httpx.Client(base_url=api, headers={"X-Admin-Key": get_settings().admin_api_key}, timeout=15)
    rng = random.Random()
    fleet, created = [], 0
    for commune, line in lines:
        if line not in saved:
            while True:  # reintenta si la patente aleatoria ya existe
                r = admin.post("/api/v1/vehicles", json={"plate": _random_plate(rng), "line_code": line, "max_capacity": 4})
                if r.status_code != 409:
                    break
            r.raise_for_status()
            v = r.json()
            saved[line] = {"id": v["id"], "token": v["device_token"], "plate": v["plate"], "commune": commune}
            created += 1
        fleet.append({"line": line, **saved[line]})
    CREDENTIALS_FILE.parent.mkdir(exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(saved, indent=1, ensure_ascii=False))
    print(f"Flota: {len(fleet)} autos ({created} registrados ahora, {len(fleet) - created} reutilizados)")
    return fleet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--per-commune", type=int, default=2, help="líneas simuladas por comuna")
    parser.add_argument("--pilot", default="La Florida")
    parser.add_argument("--pilot-lines", type=int, default=6, help="líneas simuladas en la comuna piloto")
    parser.add_argument("--include-rural", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--speed-kmh", type=float, default=25)
    parser.add_argument("--pings", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    lines = asyncio.run(pick_lines(args.per_commune, args.pilot, args.pilot_lines, args.include_rural, args.seed))
    fleet = ensure_vehicles(args.api, lines)
    communes = sorted({v["commune"] for v in fleet})
    print(f"{len(communes)} comunas: {', '.join(communes)}")

    rng = random.Random(args.seed)
    threads = [
        threading.Thread(
            target=simulate,
            args=(args.api, v["id"], v["token"]),
            kwargs=dict(
                pings=args.pings, interval_s=args.interval, speed_kmh=args.speed_kmh,
                start_m=rng.uniform(0, 30_000),  # punto de partida al azar en el ida y vuelta
                direction=rng.randint(0, 1), seed=k, verbose=False, stop_on_error=False,
            ),
            daemon=True,
        )
        for k, v in enumerate(fleet)
    ]
    for t in threads:
        t.start()
    print("Simulando… (Ctrl+C para detener)")
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
