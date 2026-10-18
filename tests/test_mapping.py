import subprocess
import sys
from dataclasses import dataclass

import pytest

from viron_usd import mapping as m


@dataclass
class G:
    grid_id: int
    rows: int
    columns: int
    index: int


def test_prim_paths():
    assert m.environment_path(3) == "/World/env_3"
    assert m.grids_path(3) == "/World/env_3/grids"
    assert m.grid_path(3, 17) == "/World/env_3/grids/grid_17"
    assert m.cells_path(3, 17) == "/World/env_3/grids/grid_17/cells"
    assert m.entities_path(3) == "/World/env_3/entities"
    assert m.entity_path(3, 42) == "/World/env_3/entities/e_42"
    assert m.entity_geom_path(3, 42) == "/World/env_3/entities/e_42/geom"


@pytest.mark.parametrize(
    "grid, x, y, expected",
    [
        (G(1, 3, 4, 0), 0, 0, (0.5, 0.5, 0.0)),
        (G(1, 3, 4, 0), 2, 3, (2.5, 3.5, 0.0)),
        (G(2, 3, 4, 1), 0, 0, (5.5, 0.5, 0.0)),  # origin 1 × (3 + 2)
        (G(2, 3, 4, 1), 2, 3, (7.5, 3.5, 0.0)),
        (G(9, 10, 10, 2), 9, 0, (33.5, 0.5, 0.0)),  # origin 2 × (10 + 2)
    ],
)
def test_cell_center_for_several_grids_and_corners(grid, x, y, expected):
    assert m.cell_center(grid, x, y) == expected


def test_grid_origin_side_by_side_along_x():
    assert m.grid_origin(G(1, 5, 7, 0)) == (0.0, 0.0, 0.0)
    assert m.grid_origin(G(2, 5, 7, 3)) == (21.0, 0.0, 0.0)


def test_face_index():
    g = G(1, 3, 4, 0)
    assert [m.face_index(g, x, y) for x in range(3) for y in range(4)] == list(range(12))


def test_entity_offset_is_deterministic_and_in_range():
    assert m.entity_offset(7) == m.entity_offset(7)
    for e in range(1, 2001):
        dx, dy = m.entity_offset(e)
        assert -0.25 <= dx <= 0.25 and -0.25 <= dy <= 0.25


def test_entity_offset_is_stable_across_processes():
    code = "from viron_usd.mapping import entity_offset; print(repr(entity_offset(12345)))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == repr(m.entity_offset(12345))


def test_entity_offset_known_value():
    # SHA-256("1") starts 6b86b273 ff34fce1.
    dx, dy = m.entity_offset(1)
    assert dx == pytest.approx(-0.25 + 0x6B86B273 * 0.5 / 0xFFFFFFFF)
    assert dy == pytest.approx(-0.25 + 0xFF34FCE1 * 0.5 / 0xFFFFFFFF)


def test_two_entities_on_one_cell_get_different_positions():
    g = G(1, 3, 3, 0)
    assert m.entity_position(g, 1, 1, 10) != m.entity_position(g, 1, 1, 11)


def test_entity_cube_stays_inside_its_cell():
    g = G(1, 4, 4, 1)
    half = m.ENTITY_SIZE / 2
    for e in range(1, 5001):
        x, y = e % 4, (e // 4) % 4
        px, py, pz = m.entity_position(g, x, y, e)
        ox = m.grid_origin(g)[0]
        assert ox + x < px - half and px + half < ox + x + 1
        assert y < py - half and py + half < y + 1
        assert pz == pytest.approx(0.2)


def test_entity_color_is_stable_rgb():
    c = m.entity_color(5)
    assert c == m.entity_color(5) and c != m.entity_color(6)
    assert len(c) == 3 and all(0.0 <= v <= 1.0 for v in c)


@pytest.mark.parametrize(
    "value, type_name",
    [(True, "bool"), (3, "int"), (2.5, "double"), ("wolf", "string"), ((1, 2.0, 3), "double3"), ([0, 0, 1], "double3")],
)
def test_extra_type(value, type_name):
    assert m.extra_type(value) == type_name


@pytest.mark.parametrize("value", [None, (1, 2), (1, 2, "x"), (True, 1, 2), {"a": 1}])
def test_extra_type_rejects(value):
    with pytest.raises(TypeError):
        m.extra_type(value)


@pytest.mark.parametrize("value", [2**31, -(2**31) - 1, (10**400, 0, 0)])
def test_extra_type_rejects_unrepresentable_values(value):
    with pytest.raises(ValueError):
        m.extra_type(value)


def test_extra_type_int32_bounds():
    assert m.extra_type(2**31 - 1) == "int" and m.extra_type(-(2**31)) == "int"


def test_extra_key_validity():
    assert m.is_valid_extra_key("category")
    assert m.is_valid_extra_key("ns:hp_2")
    for bad in ("", "2x", "has space", "a-b", "a:", "category\n", "a\nb", 3):
        assert not m.is_valid_extra_key(bad)


def test_normalize_color():
    assert m.normalize_color([1, 0, 0.5]) == (1.0, 0.0, 0.5)
    for bad in ((1.2, 0, 0), (0, 0), "red", (10**400, 0, 0)):
        with pytest.raises(ValueError):
            m.normalize_color(bad)
