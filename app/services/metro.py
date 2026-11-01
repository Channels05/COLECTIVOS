"""Red de Metro de Santiago (y Tren Nos) para combinar con colectivos en el planificador.

Las líneas y sus estaciones en orden vienen de data/transporte/metro.json (lo genera
`scripts/build_metro` desde OpenStreetMap). Un viaje en Metro se estima con minutos fijos por
estación y por combinación: alcanza para comparar "colectivo + Metro" con "solo colectivo".
"""

import heapq
import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

METRO_FILE = Path(__file__).resolve().parents[2] / "data" / "transporte" / "metro.json"

MIN_PER_HOP = 2.0  # entre dos estaciones seguidas, con la detención
MIN_PER_TRANSFER = 5.0  # caminar al otro andén y esperar el tren de la otra línea
ACCESS_MIN = 4.0  # entrar a la estación, pagar y esperar el primer tren
TRANSFER_RADIUS_M = 350  # estaciones de distinta línea más cerca que esto son una combinación


@dataclass(frozen=True)
class Station:
    key: int  # índice en la red
    line: str  # "L5"
    name: str
    lat: float
    lon: float


@dataclass
class MetroLeg:
    ref: str
    line_name: str
    color: str | None
    start: Station
    end: Station
    stops: int  # estaciones que avanza


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta (haversine), suficiente para caminar a una estación."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Network:
    def __init__(self, data: dict) -> None:
        self.stations: list[Station] = []
        self.lines: dict[str, dict] = {}
        self.edges: dict[int, list[tuple[int, float]]] = {}
        for line in data["lines"]:
            self.lines[line["ref"]] = line
            previous: Station | None = None
            for s in line["stations"]:
                station = Station(len(self.stations), line["ref"], s["name"], s["lat"], s["lon"])
                self.stations.append(station)
                self.edges[station.key] = []
                if previous is not None:
                    self.edges[previous.key].append((station.key, MIN_PER_HOP))
                    self.edges[station.key].append((previous.key, MIN_PER_HOP))
                previous = station
        for a in self.stations:
            for b in self.stations:
                if a.line != b.line and (a.name == b.name or distance_m(a.lat, a.lon, b.lat, b.lon) < TRANSFER_RADIUS_M):
                    self.edges[a.key].append((b.key, MIN_PER_TRANSFER))

    def near(self, lat: float, lon: float, radius_m: float) -> list[tuple[Station, float]]:
        """Estaciones a menos de `radius_m` en línea recta, con su distancia (una por línea)."""
        found = [(s, distance_m(lat, lon, s.lat, s.lon)) for s in self.stations]
        return sorted([(s, d) for s, d in found if d <= radius_m], key=lambda sd: sd[1])

    def ride(self, start: int, targets: set[int]) -> dict[int, tuple[float, list[int]]]:
        """Dijkstra desde `start`: {estación destino: (minutos, camino)} para las de `targets`."""
        best: dict[int, float] = {start: 0.0}
        previous: dict[int, int] = {}
        queue = [(0.0, start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost > best.get(node, math.inf):
                continue
            for nxt, step in self.edges[node]:
                if cost + step < best.get(nxt, math.inf):
                    best[nxt] = cost + step
                    previous[nxt] = node
                    heapq.heappush(queue, (cost + step, nxt))
        result = {}
        for target in targets:
            if target in best and target != start:
                path = [target]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                result[target] = (best[target], path[::-1])
        return result

    def legs(self, path: list[int]) -> list[MetroLeg]:
        """El camino agrupado por línea: "L5 de Bellavista a Baquedano, 9 estaciones" + "L1 …"."""
        legs: list[MetroLeg] = []
        for a, b in zip(path, path[1:], strict=False):
            sa, sb = self.stations[a], self.stations[b]
            if sa.line != sb.line:
                continue  # combinación: el tramo siguiente empieza en la otra línea
            if legs and legs[-1].ref == sa.line and legs[-1].end.key == a:
                legs[-1].end = sb
                legs[-1].stops += 1
            else:
                line = self.lines[sa.line]
                legs.append(MetroLeg(sa.line, line["name"], line.get("color"), sa, sb, 1))
        return legs


@cache
def network() -> Network | None:
    if not METRO_FILE.exists():
        return None
    return Network(json.loads(METRO_FILE.read_text(encoding="utf-8")))
