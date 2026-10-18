"""Record the README showcase run: coloured walkers that sometimes share a cell.

A variant of ``examples/random_walk.py`` for the README animation. It reuses that demo's
``RandomWalkDriver`` but lets a walker step onto an occupied neighbour (Viron lets a location
hold several entities; a step onto company is a remove + place, since ``/move`` refuses an
occupied target), with a pull toward company so shared cells — and the in-cell fan-out — show
up often. Each walker gets a fixed colour through the ``color`` extra.

**The driver writes to Viron** — run it only against a throwaway Viron you started yourself
(docs/PLAYBACK.md § 2). The recorder still only reads.

    export VIRON_TOKEN=...
    python scripts/record_showcase.py --out runs/showcase
    viron-usd flatten runs/showcase/run.usda      # only needed for a chunked run
    python scripts/render_gif.py runs/showcase/run.usda --out docs/images/replay.gif
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from random_walk import RandomWalkDriver  # noqa: E402

from viron_usd.client import DEFAULT_URL, TOKEN_ENV_VAR, VironClient  # noqa: E402
from viron_usd.recorder import Recorder  # noqa: E402

# Twelve well-separated hues that stand out from a grey checkerboard, RGB in [0, 1].
PALETTE = [
    (0.12, 0.47, 0.71), (1.00, 0.50, 0.05), (0.17, 0.63, 0.17), (0.84, 0.15, 0.16),
    (0.58, 0.40, 0.74), (0.55, 0.34, 0.29), (0.89, 0.47, 0.76), (0.00, 0.20, 0.55),
    (0.60, 0.80, 0.10), (0.09, 0.75, 0.81), (0.98, 0.85, 0.20), (0.60, 0.00, 0.35),
]


class SocialWalkDriver(RandomWalkDriver):
    """Random walk that may step onto occupied cells, preferring them with probability ``pull``."""

    def __init__(self, *args, pull: float = 0.35, **kwargs):
        super().__init__(*args, **kwargs)
        self.pull = pull

    def step(self, frozen: set[int] = frozenset()) -> int:
        moved = 0
        for entity in sorted(self.position):
            if entity in frozen or self.rng.random() < 0.25:  # some walkers rest each tick
                continue
            here = self.position[entity]
            options = self.neighbors(here)
            occupied = set(self.position.values()) - {here}
            company = [n for n in options if n in occupied]
            target = self.rng.choice(company if company and self.rng.random() < self.pull else options)
            if target in occupied:
                # Viron's move rejects an occupied target (409), but placing an unplaced entity
                # on an occupied location is allowed: take it off the grid, then place it.
                self._call("DELETE", f"/api/v1/locations/entity/{entity}")
                self._call("PUT", f"/api/v1/locations/{target}/entity/{entity}")
            else:
                self._call("PUT", f"/api/v1/locations/{target}/entity/{entity}/move")
            self.position[entity] = target
            moved += 1
        return moved


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--viron-url", default=DEFAULT_URL)
    p.add_argument("--ticks", type=int, default=150)
    p.add_argument("--entities", type=int, default=12)
    p.add_argument("--grids", type=int, default=2)
    p.add_argument("--grid-size", type=int, default=7)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--ticks-per-second", type=float, default=10.0)
    p.add_argument("--out", default="runs/showcase")
    args = p.parse_args(argv)

    token = os.environ.get(TOKEN_ENV_VAR)
    driver = SocialWalkDriver(args.viron_url, token, seed=args.seed)
    env = driver.create_world(f"showcase-{int(time.time())}", args.grids, args.grid_size)
    colors = {}
    for i in range(args.entities):
        colors[driver.add_entity(f"walker-{i}")] = PALETTE[i % len(PALETTE)]

    client = VironClient(args.viron_url, token)
    shared_ticks = 0
    with Recorder.create(client, env, args.out, ticks_per_second=args.ticks_per_second, chunk_size=None) as rec:
        for tick in range(args.ticks):
            if tick:
                driver.step()
            rec.capture(tick, extras={e: {"color": c} for e, c in colors.items()})
            shared_ticks += len(set(driver.position.values())) < len(driver.position)
    print(f"recorded environment {env}: {args.ticks} ticks, {args.entities} entities, "
          f"{shared_ticks} ticks with a shared cell -> {args.out}")


if __name__ == "__main__":
    main()
