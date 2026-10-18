"""Deterministic recordings whose every output file is pinned as a golden copy.

The goldens in ``tests/golden/`` were written by ``viron-usd`` 0.1.0 *before* the writer moved
onto the shared ``usd-recorder`` library (commit 5b4b6f2). ``test_equivalence.py`` re-records the
same scenarios with the current code and requires every file to match its golden copy exactly
(text layers byte for byte; binary ``.usdc`` layers by their text export), so the move is proven
not to change any stage ``viron-usd`` writes.

Regenerate (only when an output change is intended)::

    python tests/equivalence/scenarios.py tests/golden
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from pxr import Sdf  # noqa: E402

from synthetic import make_layout, snap  # noqa: E402
from viron_usd.chunks import ChunkedWriter, flatten  # noqa: E402
from viron_usd.writer import TimeSampledWriter  # noqa: E402

LAYOUT = make_layout(((1, 5, 5), (2, 4, 6)))
GROWN = make_layout(((1, 5, 5), (2, 4, 6), (3, 3, 3)))
TICKS = 30


def frames(seed: int = 11):
    """A random walk with late arrivals, removals, renames, typed extras, driver colours and a
    torn tick. Entity 6 arrives at tick 15 (a later chunk); entity 7 lives on grid 3, which only
    exists from tick 14 when the layout is refreshed."""
    rng = random.Random(seed)
    grids = {1: (5, 5), 2: (4, 6), 3: (3, 3)}
    pos = {e: (1, rng.randrange(5), rng.randrange(5)) for e in range(1, 7)}
    names = {e: f"e{e}" for e in range(1, 8)}
    out = []
    for t in range(TICKS):
        placed, extras = {}, {}
        for e, (g, x, y) in list(pos.items()):
            if e == 6 and t < 15:
                continue
            if rng.random() < 0.4:
                g = rng.choice((1, 2))
                x, y = rng.randrange(grids[g][0]), rng.randrange(grids[g][1])
                pos[e] = (g, x, y)
            if rng.random() < 0.05:
                names[e] = f"e{e}@{t}"
            if rng.random() < 0.1:
                continue  # unplaced this tick
            placed[e] = (g, x, y, names[e])
            values = {}
            if e in (1, 2) and rng.random() < 0.6:
                values["energy"] = round(rng.uniform(0, 10), 3)
                values["alive"] = rng.random() < 0.8
            if e == 2 and t % 4 == 0:
                values["mood"] = rng.choice(["calm", "curious", "wary"])
            if e in (3, 6) and rng.random() < 0.5:
                values["count"] = rng.randrange(100)
                values["heading"] = (rng.uniform(-1, 1), rng.uniform(-1, 1), 0.0)
            if e == 4 and t >= 9 and rng.random() < 0.5:
                values["color"] = (round(rng.random(), 3), round(rng.random(), 3), 0.5)
            if values:
                extras[e] = values
        if t >= 16 and t % 3:
            placed[7] = (3, t % 3, (t // 3) % 3, names[7])
        warnings = ("entity 9 listed in grids 1 and 2",) if t == 21 else ()
        out.append((t, placed, extras, warnings))
    return out


def _snap(layout, t, placed, extras, warnings):
    return snap(layout, t, placed, extras=extras, warnings=warnings)


def record_unchunked(path: Path) -> None:
    writer = TimeSampledWriter.create(path, GROWN)
    for t, placed, extras, warnings in frames():
        writer.write(_snap(GROWN, t, placed, extras, warnings))
    writer.close()


def record_chunked(out_dir: Path, binary: bool) -> None:
    calls = []

    def refresh():
        calls.append(1)
        return GROWN if len(calls) >= 2 else LAYOUT

    writer = ChunkedWriter(out_dir, LAYOUT, chunk_size=7, binary=binary, refresh_layout=refresh)
    for t, placed, extras, warnings in frames():
        writer.begin_tick(t)
        writer.write(_snap(writer.layout, t, placed, extras, warnings))
    writer.close()


def record_all(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    record_unchunked(root / "single" / "run.usda")
    record_unchunked(root / "single_binary" / "run.usdc")
    record_chunked(root / "chunked", binary=False)
    record_chunked(root / "chunked_binary", binary=True)
    flatten(root / "chunked" / "run.usda")
    flatten(root / "chunked_binary" / "run.usda", binary=True)


_FLATTENED_FROM = re.compile(r"(Generated from Composed Stage of root layer )\S*?([^/\s]+/run\.usda)")


def canonical(path: Path) -> str:
    """A file's comparable text: ``.usda`` as written, ``.usdc`` as its text export. The
    absolute directory ``flatten`` records in its ``doc`` string is replaced by ``<root>/``."""
    if path.suffix == ".usdc":
        text = Sdf.Layer.OpenAsAnonymous(str(path)).ExportToString()
    else:
        text = path.read_text()
    return _FLATTENED_FROM.sub(r"\1<root>/\2", text)


def files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*.usd[ac]"))}


if __name__ == "__main__":
    target = Path(sys.argv[1])
    record_all(target)
    for name, path in files(target).items():
        if path.suffix == ".usda":  # keep no machine-specific path in the goldens
            path.write_text(canonical(path))
        print(name)
