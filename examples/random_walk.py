"""Playback demo driver: entities wander a Viron grid, recorded tick by tick.

The driver creates an environment, creates and places entities, then each tick moves every
entity to a random unoccupied neighbouring location and calls ``recorder.capture(tick)``.
**The driver writes to Viron** (``POST``/``PUT``/``DELETE``) — it stands in for a simulation.
The recorder it feeds still only reads (``GET``).

Run it only against a Viron you started locally (see docs/PLAYBACK.md):

    export VIRON_TOKEN=...      # JWT bearer token for the local Viron
    python examples/random_walk.py --ticks 200 --entities 12 --out runs/random_walk
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import requests

from viron_usd.client import DEFAULT_URL, TOKEN_ENV_VAR, VironClient
from viron_usd.recorder import Recorder


class DriverError(RuntimeError):
    pass


class RandomWalkDriver:
    """A minimal simulation over Viron's REST API (this is the only code here that writes)."""

    def __init__(self, base_url: str, token: str | None, *, seed: int = 0, session: Any = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout
        self.rng = random.Random(seed)
        self.environment_id: int | None = None
        self.grid_ids: list[int] = []
        self.position: dict[int, int] = {}  # entity id -> location id
        self.coords: dict[int, tuple[int, int, int]] = {}  # location id -> (grid id, x, y)
        self._neighbors: dict[int, list[int]] = {}

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.session.request(method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
        if response.status_code >= 400:
            raise DriverError(f"{method} {path} -> {response.status_code}: {response.text[:200]}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def create_world(self, name: str, grids: int, size: int) -> int:
        env = self._call("POST", "/api/v1/environments", json={"name": name, "numGrids": grids, "gridSize": size})
        self.environment_id = int(env["environmentId"])
        self.grid_ids = sorted(int(g["gridId"]) for g in self._call("GET", f"/api/v1/grids/environment/{self.environment_id}"))
        for g in self.grid_ids:
            for loc in self._call("GET", f"/api/v1/locations/grid/{g}"):
                self.coords[int(loc["locationId"])] = (g, int(loc["x"]), int(loc["y"]))
        return self.environment_id

    def add_entity(self, name: str) -> int:
        """Create an entity and place it on a random unoccupied location."""
        entity = int(self._call("POST", "/api/v1/entities", json={"name": name})["entityId"])
        occupied = set(self.position.values())
        free = [loc for loc in sorted(self.coords) if loc not in occupied]
        if not free:
            raise DriverError("no unoccupied location left")
        target = self.rng.choice(free)
        self._call("PUT", f"/api/v1/locations/{target}/entity/{entity}")
        self.position[entity] = target
        return entity

    def remove_entity(self, entity: int) -> None:
        """Take an entity off the grid (it stays in Viron, unplaced)."""
        self._call("DELETE", f"/api/v1/locations/entity/{entity}")
        del self.position[entity]

    def rename(self, entity: int, name: str) -> None:
        self._call("PATCH", f"/api/v1/entities/{entity}/name", json={"name": name})

    def neighbors(self, location: int) -> list[int]:
        if location not in self._neighbors:
            self._neighbors[location] = [int(n["locationId"]) for n in self._call("GET", f"/api/v1/locations/{location}/neighbors")]
        return self._neighbors[location]

    def step(self, frozen: set[int] = frozenset()) -> int:
        """Move every entity not in ``frozen`` to a random unoccupied neighbour. Returns moves made."""
        moved = 0
        for entity in sorted(self.position):
            if entity in frozen:
                continue
            occupied = set(self.position.values())
            options = [n for n in self.neighbors(self.position[entity]) if n not in occupied]
            if not options:
                continue
            target = self.rng.choice(options)
            self._call("PUT", f"/api/v1/locations/{target}/entity/{entity}/move")
            self.position[entity] = target
            moved += 1
        return moved

    def placements(self) -> dict[int, tuple[int, int, int]]:
        """Entity id -> (grid id, x, y), as the driver last left them."""
        return {e: self.coords[loc] for e, loc in self.position.items()}


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Drive a random walk on a local Viron and record it with viron-usd.")
    p.add_argument("--viron-url", default=DEFAULT_URL)
    p.add_argument("--token", default=None, help=f"JWT bearer token (default: ${TOKEN_ENV_VAR})")
    p.add_argument("--ticks", type=_positive_int, default=200)
    p.add_argument("--entities", type=_positive_int, default=12)
    p.add_argument("--grids", type=_positive_int, default=1)
    p.add_argument("--grid-size", type=_positive_int, default=12)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="runs/random_walk", help="run directory (default runs/random_walk)")
    p.add_argument("--chunk-size", type=_non_negative_int, default=1000, help="ticks per chunk layer; 0 = one layer")
    p.add_argument("--ticks-per-second", type=float, default=10.0, help="playback rate (default 10)")
    p.add_argument("--binary", action="store_true")
    return p


def main(argv: list[str] | None = None, *, recorder_session: Any = None) -> dict:
    args = build_parser().parse_args(argv)
    if args.entities > args.grids * args.grid_size * args.grid_size:
        build_parser().error("more entities than locations")
    token = args.token if args.token is not None else os.environ.get(TOKEN_ENV_VAR)
    driver = RandomWalkDriver(args.viron_url, token, seed=args.seed)
    env = driver.create_world(f"random-walk-{int(time.time())}", args.grids, args.grid_size)
    for i in range(args.entities):
        driver.add_entity(f"walker-{i}")

    from viron_usd.client import RequestsTransport

    transport = RequestsTransport(recorder_session) if recorder_session is not None else None
    client = VironClient(args.viron_url, token, transport=transport)
    latencies = []
    with Recorder.create(client, env, args.out, ticks_per_second=args.ticks_per_second,
                         binary=args.binary, chunk_size=args.chunk_size or None) as recorder:
        for tick in range(args.ticks):
            if tick:
                driver.step()
            start = time.perf_counter()
            recorder.capture(tick)
            latencies.append(time.perf_counter() - start)
    root = Path(args.out) / ("run.usda" if args.chunk_size else ("run.usdc" if args.binary else "run.usda"))
    summary = {
        "environment": env,
        "ticks": args.ticks,
        "entities": args.entities,
        "run": str(root),
        "capture_ms_mean": 1000 * statistics.mean(latencies) if latencies else 0.0,
        "capture_ms_p95": 1000 * sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else 0.0,
        "requests_per_capture": (client.request_count - 0) / max(1, len(latencies)),
    }
    print(
        f"recorded environment {env}: {args.ticks} ticks, {args.entities} entities -> {root}\n"
        f"capture latency: mean {summary['capture_ms_mean']:.1f} ms, p95 {summary['capture_ms_p95']:.1f} ms "
        f"({summary['requests_per_capture']:.1f} GET requests per capture incl. layout reads)"
    )
    return summary


if __name__ == "__main__":
    try:
        main()
    except DriverError as exc:
        print(f"random_walk: error: {exc}", file=sys.stderr)
        sys.exit(1)
