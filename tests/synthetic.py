"""Synthetic layouts and snapshots for writer tests (no Viron, no HTTP)."""

from __future__ import annotations

from types import MappingProxyType

from viron_usd.snapshot import GridInfo, LocationInfo, Placement, Snapshot, WorldLayout


def make_layout(grids=((1, 4, 4),), environment_id: int = 1, name: str = "synthetic") -> WorldLayout:
    """Grids as ``(grid_id, rows, columns)``; location ids are ``grid_id * 1000 + x * columns + y + 1``."""
    infos, locations, order = [], {}, {}
    for index, (gid, rows, cols) in enumerate(sorted(grids)):
        infos.append(GridInfo(gid, rows, cols, None, index))
        ids = []
        for x in range(rows):
            for y in range(cols):
                loc = loc_id(gid, cols, x, y)
                locations[loc] = LocationInfo(gid, x, y)
                ids.append(loc)
        order[gid] = tuple(ids)
    return WorldLayout(environment_id, name, tuple(infos), MappingProxyType(locations), MappingProxyType(order))


def loc_id(grid_id: int, columns: int, x: int, y: int) -> int:
    return grid_id * 1000 + x * columns + y + 1


def snap(layout: WorldLayout, tick: int, placed: dict, extras=None, warnings=(), wall_clock=None) -> Snapshot:
    """``placed`` maps entity id -> ``(grid_id, x, y)`` or ``(grid_id, x, y, name)``."""
    entities = {}
    for e, spec in placed.items():
        g, x, y = spec[:3]
        name = spec[3] if len(spec) > 3 else f"entity-{e}"
        entities[e] = Placement(name, g, loc_id(g, layout.grid(g).columns, x, y))
    return Snapshot(
        tick,
        float(1_000_000 + tick if wall_clock is None else wall_clock),
        MappingProxyType(entities),
        MappingProxyType(extras or {}),
        tuple(warnings),
    )
