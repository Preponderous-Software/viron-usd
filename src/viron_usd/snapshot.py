"""One tick's world state, as plain data.

:class:`WorldLayout` holds what does not change during a run (the environment's grids and its
locations) and is fetched once. :class:`Snapshot` holds what does: which entities are placed
where, and their names. :func:`take_snapshot` builds one from Viron with ``GET`` requests only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, NamedTuple

from .client import VironClient, VironNotFound

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GridInfo:
    grid_id: int
    rows: int
    columns: int
    name: str | None
    index: int  # position of the grid within the environment, ordered by grid id


class LocationInfo(NamedTuple):
    grid_id: int
    x: int
    y: int


@dataclass(frozen=True)
class WorldLayout:
    """The static part of one environment: its grids and every location's (grid, x, y)."""

    environment_id: int
    environment_name: str
    grids: tuple[GridInfo, ...]
    locations: Mapping[int, LocationInfo]
    location_order: Mapping[int, tuple[int, ...]] = field(default_factory=dict)

    @classmethod
    def fetch(cls, client: VironClient, environment_id: int) -> "WorldLayout":
        env = client.get_environment(environment_id)
        grid_dtos = sorted(client.get_grids(environment_id), key=lambda g: g.grid_id)
        grids = tuple(
            GridInfo(g.grid_id, g.rows, g.columns, g.name, index)
            for index, g in enumerate(grid_dtos)
        )
        locations: dict[int, LocationInfo] = {}
        order: dict[int, tuple[int, ...]] = {}
        for grid in grids:
            locs = client.get_locations_in_grid(grid.grid_id)
            order[grid.grid_id] = tuple(loc.location_id for loc in locs)
            for loc in locs:
                locations[loc.location_id] = LocationInfo(grid.grid_id, loc.x, loc.y)
        return cls(
            env.environment_id,
            env.name,
            grids,
            MappingProxyType(locations),
            MappingProxyType(order),
        )

    def grid(self, grid_id: int) -> GridInfo:
        for grid in self.grids:
            if grid.grid_id == grid_id:
                return grid
        raise KeyError(grid_id)

    def merged_with(self, newer: "WorldLayout") -> "WorldLayout":
        """A layout that keeps every grid of ``self`` at its index (so its origin, and the
        positions already recorded on it, never move) and appends grids that are new in
        ``newer`` after them. Locations are the union of both."""
        known = {g.grid_id for g in self.grids}
        grids = list(self.grids)
        next_index = max((g.index for g in grids), default=-1) + 1
        for g in newer.grids:
            if g.grid_id not in known:
                grids.append(GridInfo(g.grid_id, g.rows, g.columns, g.name, next_index))
                next_index += 1
        locations = dict(self.locations)
        locations.update(newer.locations)
        order = dict(self.location_order)
        order.update(newer.location_order)
        return WorldLayout(
            self.environment_id,
            newer.environment_name,
            tuple(grids),
            MappingProxyType(locations),
            MappingProxyType(order),
        )

    def grid_ids(self) -> tuple[int, ...]:
        return tuple(g.grid_id for g in self.grids)


class Placement(NamedTuple):
    name: str
    grid_id: int
    location_id: int


@dataclass(frozen=True)
class Snapshot:
    """The state of one environment at one tick.

    ``entities`` holds only entities placed in one of the environment's grids; an entity that
    is unplaced, deleted, or placed in another environment is absent. ``extras`` holds the
    optional per-entity values a driver passes to ``Recorder.capture`` (empty otherwise).
    ``warnings`` describes torn reads that could not be resolved by a retry.
    """

    tick: int
    wall_clock: float
    entities: Mapping[int, Placement]
    extras: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def _fallback_placements(
    client: VironClient, layout: WorldLayout
) -> tuple[dict[int, Placement], list[str]]:
    """G + N reads: one ``/entities/grid/{g}`` per grid, one ``/locations/entity/{e}`` per entity.

    Returns the placements plus a list of torn-read descriptions (empty when consistent).
    """
    placements: dict[int, Placement] = {}
    torn: list[str] = []
    for grid in layout.grids:
        try:
            listed = client.get_entities_in_grid(grid.grid_id)
        except VironNotFound:
            listed = []  # a grid removed since the layout was read holds nothing
        for entity in listed:
            if entity.entity_id in placements:
                torn.append(
                    f"entity {entity.entity_id} listed in grids "
                    f"{placements[entity.entity_id].grid_id} and {grid.grid_id}"
                )
                continue
            try:
                loc = client.get_location_of_entity(entity.entity_id)
            except VironNotFound:
                torn.append(f"entity {entity.entity_id} listed in grid {grid.grid_id} but has no location")
                continue
            info = layout.locations.get(loc.location_id)
            if info is None:
                torn.append(
                    f"entity {entity.entity_id} listed in grid {grid.grid_id} but its location "
                    f"{loc.location_id} is not in environment {layout.environment_id}"
                )
                continue
            if info.grid_id != grid.grid_id:
                torn.append(
                    f"entity {entity.entity_id} listed in grid {grid.grid_id} but its location "
                    f"{loc.location_id} is in grid {info.grid_id}"
                )
                # The location read came second, so it is the more recent fact: record it there.
                placements[entity.entity_id] = Placement(entity.name, info.grid_id, loc.location_id)
                continue
            placements[entity.entity_id] = Placement(entity.name, grid.grid_id, loc.location_id)
    return placements, torn


def _bulk_placements(
    client: VironClient, layout: WorldLayout
) -> tuple[dict[int, Placement], list[str]]:
    """2 reads: every placement from one ``/locations/placements`` query, then the names from
    ``/entities/environment/{id}``. A placed entity missing from the name list (created after
    the name read began) is looked up with ``/entities/{id}``.
    """
    rows = client.get_bulk_placements(layout.environment_id)
    names = {e.entity_id: e.name for e in client.get_entities_in_environment(layout.environment_id)}
    placements: dict[int, Placement] = {}
    torn: list[str] = []
    for row in rows:
        info = layout.locations.get(row.location_id)
        if info is None:
            continue  # a grid added since the layout was read: the fallback does not see it either
        name = names.get(row.entity_id)
        if name is None:
            try:
                name = client.get_entity(row.entity_id).name
            except VironNotFound:
                torn.append(f"entity {row.entity_id} is placed but no longer exists")
                continue
        placements[row.entity_id] = Placement(name, info.grid_id, row.location_id)
    return placements, torn


def read_placements(
    client: VironClient, layout: WorldLayout
) -> tuple[dict[int, Placement], list[str]]:
    """Every placed entity in ``layout``'s environment, with its name, grid, and location.

    Uses the bulk placement read when the Viron instance provides it (2 requests per tick),
    and the G + N fallback otherwise; both give the same placements for the same world.
    """
    if client.supports_bulk_placements(layout.environment_id):
        return _bulk_placements(client, layout)
    return _fallback_placements(client, layout)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(v) for v in value)
    return value


def freeze_extras(extras: Mapping[int, Mapping[str, Any]] | None) -> Mapping[int, Mapping[str, Any]]:
    """A read-only deep copy of driver extras, so later changes by the driver to its own dicts
    or lists cannot alter an already-captured snapshot (lists become tuples)."""
    return MappingProxyType(
        {
            int(entity_id): MappingProxyType({k: _freeze_value(v) for k, v in values.items()})
            for entity_id, values in (extras or {}).items()
        }
    )


def take_snapshot(
    client: VironClient,
    layout: WorldLayout,
    tick: int,
    *,
    extras: Mapping[int, Mapping[str, Any]] | None = None,
    clock: Callable[[], float] = time.time,
) -> Snapshot:
    """Read one tick's state. A torn read is retried once; if it is still torn, the snapshot is
    returned with ``warnings`` describing it (and a log warning), never silently."""
    wall_clock = clock()
    placements, torn = read_placements(client, layout)
    if torn:
        log.info("tick %s: torn read (%s); retrying once", tick, "; ".join(torn))
        wall_clock = clock()
        placements, torn = read_placements(client, layout)
        if torn:
            log.warning("tick %s: torn snapshot recorded after retry: %s", tick, "; ".join(torn))
    return Snapshot(
        tick=int(tick),
        wall_clock=float(wall_clock),
        entities=MappingProxyType(placements),
        extras=freeze_extras(extras),
        warnings=tuple(torn),
    )
