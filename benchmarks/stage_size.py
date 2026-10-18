"""Measure stage size for the fixed scenario in ARCHITECTURE.md § Size.

100 entities on one 50×50 grid, 10,000 ticks, 50 % of the entities moving one step per tick;
written chunked (1,000 ticks per chunk) and unchunked, as text .usda and binary .usdc.
Synthetic snapshots are fed straight to the writers — no Viron is needed.

    python benchmarks/stage_size.py [--ticks N] [--out DIR]
"""

from __future__ import annotations

import argparse
import random
import tempfile
import time
from pathlib import Path
from types import MappingProxyType

from viron_usd.chunks import ChunkedWriter
from viron_usd.snapshot import GridInfo, LocationInfo, Placement, Snapshot, WorldLayout
from viron_usd.writer import TimeSampledWriter

ROWS = COLS = 50
ENTITIES = 100
MOVING = 0.5


def layout() -> WorldLayout:
    locs = {x * COLS + y + 1: LocationInfo(1, x, y) for x in range(ROWS) for y in range(COLS)}
    return WorldLayout(1, "bench", (GridInfo(1, ROWS, COLS, None, 0),), MappingProxyType(locs))


def snapshots(ticks: int, seed: int = 1):
    rng = random.Random(seed)
    pos = {e: (rng.randrange(ROWS), rng.randrange(COLS)) for e in range(1, ENTITIES + 1)}
    for t in range(ticks):
        for e in rng.sample(sorted(pos), int(ENTITIES * MOVING)):
            x, y = pos[e]
            neighbours = [
                (x + dx, y + dy)
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                if (dx or dy) and 0 <= x + dx < ROWS and 0 <= y + dy < COLS
            ]
            pos[e] = rng.choice(neighbours)  # always a different cell
        placed = {e: Placement(f"e{e}", 1, x * COLS + y + 1) for e, (x, y) in pos.items()}
        yield Snapshot(t, 1_700_000_000.0 + t, MappingProxyType(placed))


def size(paths) -> int:
    return sum(p.stat().st_size for p in paths)


def run(out: Path, ticks: int) -> list[tuple[str, int, int, float]]:
    rows = []
    for binary in (False, True):
        ext = "usdc" if binary else "usda"
        start = time.perf_counter()
        path = out / f"unchunked.{ext}"
        w = TimeSampledWriter.create(path, layout())
        for s in snapshots(ticks):
            w.write(s)
        w.close()
        rows.append((f"unchunked .{ext}", 1, size([path]), time.perf_counter() - start))

        start = time.perf_counter()
        d = out / f"chunked_{ext}"
        cw = ChunkedWriter(d, layout(), chunk_size=1000, binary=binary)
        for s in snapshots(ticks):
            cw.write(s)
        cw.close()
        files = cw.chunk_paths + [d / n for n in ("run.usda", "run.topology.usda", "run.manifest.usda")]
        rows.append((f"chunked (1000) .{ext}", len(files), size(files), time.perf_counter() - start))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ticks", type=int, default=10_000)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        out = args.out or Path(tmp)
        out.mkdir(parents=True, exist_ok=True)
        print(f"{ENTITIES} entities, {args.ticks} ticks, {int(MOVING * 100)} % moving per tick")
        print(f"{'layout':<24}{'files':>6}{'bytes':>14}{'MiB':>9}{'seconds':>9}")
        for name, files, nbytes, secs in run(out, args.ticks):
            print(f"{name:<24}{files:>6}{nbytes:>14,}{nbytes / 2**20:>9.1f}{secs:>9.1f}")


if __name__ == "__main__":
    main()
