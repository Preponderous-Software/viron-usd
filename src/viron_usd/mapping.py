"""Viron coordinates → USD prim paths and positions.

Pure functions only: no HTTP imports and no stage access, so everything here is trivially
unit-testable. Extras value typing is delegated to ``usd_recorder.values``.
See ARCHITECTURE.md § Viron → USD mapping and § Shared cells.

Conventions: one Viron cell is 1 unit (``metersPerUnit = 1``), Z is up, Viron ``x`` → USD +X,
Viron ``y`` → USD +Y, and grids are laid out side by side along X with a gap of ``GRID_GAP``
cells between them, ordered by grid id.
"""

from __future__ import annotations

import colorsys
import hashlib
from typing import Protocol

from usd_recorder import values

GRID_GAP = 2
ENTITY_SIZE = 0.4
ENTITY_Z = ENTITY_SIZE / 2
MAX_OFFSET = 0.25
AXIS_CONVENTION = (
    "Viron x -> USD +X, Viron y -> USD +Y, Z up; one Viron cell = 1 unit; "
    "grids side by side along X ordered by grid id"
)

Vec3 = tuple[float, float, float]


class GridLike(Protocol):
    grid_id: int
    rows: int
    columns: int
    index: int


# -- prim paths ---------------------------------------------------------------------------

WORLD_PATH = "/World"


def environment_path(environment_id: int) -> str:
    return f"{WORLD_PATH}/env_{int(environment_id)}"


def grids_path(environment_id: int) -> str:
    return f"{environment_path(environment_id)}/grids"


def grid_path(environment_id: int, grid_id: int) -> str:
    return f"{grids_path(environment_id)}/grid_{int(grid_id)}"


def cells_path(environment_id: int, grid_id: int) -> str:
    return f"{grid_path(environment_id, grid_id)}/cells"


def entities_path(environment_id: int) -> str:
    return f"{environment_path(environment_id)}/entities"


def entity_path(environment_id: int, entity_id: int) -> str:
    return f"{entities_path(environment_id)}/e_{int(entity_id)}"


def entity_geom_path(environment_id: int, entity_id: int) -> str:
    return f"{entity_path(environment_id, entity_id)}/geom"


# -- positions ----------------------------------------------------------------------------


def grid_origin(grid: GridLike) -> Vec3:
    """Translation of a grid's plane: ``(index × (rows + GRID_GAP), 0, 0)``."""
    return (float(grid.index * (grid.rows + GRID_GAP)), 0.0, 0.0)


def cell_center(grid: GridLike, x: int, y: int) -> Vec3:
    """Centre of cell ``(x, y)`` of ``grid`` in stage space, on the ``z = 0`` plane."""
    ox, oy, _ = grid_origin(grid)
    return (ox + x + 0.5, oy + y + 0.5, 0.0)


def face_index(grid: GridLike, x: int, y: int) -> int:
    """Face of the grid's cell mesh that represents location ``(x, y)``."""
    return int(x) * int(grid.columns) + int(y)


def _digest(entity_id: int) -> bytes:
    # Stable across processes, unlike Python's hash() of a str.
    return hashlib.sha256(str(int(entity_id)).encode("ascii")).digest()


def entity_offset(entity_id: int) -> tuple[float, float]:
    """Fan-out offset ``(dx, dy)`` in ``[-MAX_OFFSET, MAX_OFFSET]`` for an entity within its cell.

    The first 8 bytes of SHA-256 of the decimal id, read as two big-endian unsigned 32-bit
    integers, each scaled to ``[-0.25, 0.25]``. Applied to every entity, alone in its cell or not.
    """
    d = _digest(entity_id)
    a = int.from_bytes(d[0:4], "big")
    b = int.from_bytes(d[4:8], "big")
    scale = 2 * MAX_OFFSET / 0xFFFFFFFF
    return (-MAX_OFFSET + a * scale, -MAX_OFFSET + b * scale)


def entity_position(grid: GridLike, x: int, y: int, entity_id: int) -> Vec3:
    """Translation of entity ``entity_id`` standing on cell ``(x, y)`` of ``grid``."""
    cx, cy, _ = cell_center(grid, x, y)
    dx, dy = entity_offset(entity_id)
    return (cx + dx, cy + dy, ENTITY_Z)


def entity_color(entity_id: int) -> Vec3:
    """Display colour derived from the entity id (bytes 8–11 of the same SHA-256)."""
    d = _digest(entity_id)
    hue = int.from_bytes(d[8:12], "big") / 0x100000000
    return tuple(float(c) for c in colorsys.hsv_to_rgb(hue, 0.65, 0.9))  # type: ignore[return-value]


# -- driver extras ------------------------------------------------------------------------
#
# Value typing (bool, int, double, string, double3; 32-bit ints; colours in [0, 1]) is
# usd-recorder's, so viron-usd records extras exactly as any other usd-recorder consumer does.

COLOR_KEY = "color"
INT32_MIN, INT32_MAX = values.INT32_MIN, values.INT32_MAX

is_valid_extra_key = values.is_valid_property_name
"""Whether ``key`` can be used in the property name ``extras:<key>``."""

extra_type = values.value_type
"""USD value type name for a driver extra: bool, int, double, string, or double3 (``TypeError``
for anything else, ``ValueError`` for an int outside 32 bits or a vector overflowing a double)."""

normalize_color = values.normalize_color
"""Validate the reserved ``color`` extra: three numbers in ``[0, 1]``."""
