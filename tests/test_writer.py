"""TimeSampledWriter on synthetic snapshots — MVP acceptance criteria 2–6."""

import pytest
from pxr import Gf, Usd, UsdGeom

from synthetic import make_layout, snap
from viron_usd import mapping
from viron_usd.writer import TimeSampledWriter

E = 1  # environment id


def record(tmp_path, layout, frames, **kw):
    """frames: list of (tick, placed) — returns the reopened stage."""
    path = tmp_path / "run.usda"
    writer = TimeSampledWriter.create(path, layout, **kw)
    for tick, placed in frames:
        writer.write(snap(layout, tick, placed))
    writer.close()
    return Usd.Stage.Open(str(path))


def translate(stage, e, t):
    return stage.GetPrimAtPath(mapping.entity_path(E, e)).GetAttribute("xformOp:translate").Get(Usd.TimeCode(t))


def visible(stage, e, t):
    vis = UsdGeom.Imageable(stage.GetPrimAtPath(mapping.entity_path(E, e))).ComputeVisibility(Usd.TimeCode(t))
    assert vis in (UsdGeom.Tokens.inherited, UsdGeom.Tokens.invisible)
    return vis == UsdGeom.Tokens.inherited


def expected(layout, g, x, y, e):
    return Gf.Vec3d(*mapping.entity_position(layout.grid(g), x, y, e))


def same(a, b, tol=1e-9):
    """Linear interpolation between two equal samples can differ from them in the last ulp."""
    return Gf.IsClose(a, b, tol)


def test_criterion_2_correct_placement(tmp_path):
    layout = make_layout(((1, 4, 4), (2, 4, 4)))
    path_of = {
        0: {7: (1, 0, 0), 8: (2, 3, 3)},
        1: {7: (1, 1, 1), 8: (2, 3, 3)},
        2: {7: (2, 2, 0), 8: (1, 0, 0)},  # entities may jump grids between ticks
        3: {7: (2, 2, 1), 8: (1, 0, 0)},
    }
    stage = record(tmp_path, layout, sorted(path_of.items()))
    for t, placed in path_of.items():
        for e, (g, x, y) in placed.items():
            assert translate(stage, e, t) == expected(layout, g, x, y, e)


def test_criterion_3_presence(tmp_path):
    layout = make_layout()
    # entity 1: placed 0-9; entity 2: first placed at 3, removed at 6, back at 8;
    # entity 3: placed at 0, deleted (gone for good) from 5.
    frames = []
    for t in range(10):
        placed = {1: (1, 0, 0)}
        if 3 <= t < 6 or t >= 8:
            placed[2] = (1, 1, 1)
        if t < 5:
            placed[3] = (1, 2, 2)
        frames.append((t, placed))
    stage = record(tmp_path, layout, frames)
    for t in range(10):
        assert visible(stage, 1, t)
        assert visible(stage, 2, t) == (3 <= t < 6 or t >= 8), t
        assert visible(stage, 3, t) == (t < 5), t


def test_entity_first_placed_on_first_tick_is_visible_from_start(tmp_path):
    stage = record(tmp_path, make_layout(), [(5, {1: (1, 0, 0)}), (6, {1: (1, 0, 0)})])
    attr = stage.GetPrimAtPath(mapping.entity_path(E, 1)).GetAttribute("visibility")
    assert attr.GetTimeSamples() == [5.0, 6.0]
    assert visible(stage, 1, 5)


def test_criterion_4_no_drift_with_hold_keys(tmp_path):
    layout = make_layout()
    frames = [(t, {1: (1, 0, 0)}) for t in range(0, 10)] + [(10, {1: (1, 1, 0)})]
    stage = record(tmp_path, layout, frames)
    start = expected(layout, 1, 0, 0, 1)
    for t in range(0, 10):
        assert same(translate(stage, 1, t), start), t
    assert translate(stage, 1, 10) == expected(layout, 1, 1, 0, 1)
    samples = stage.GetPrimAtPath(mapping.entity_path(E, 1)).GetAttribute("xformOp:translate").GetTimeSamples()
    assert samples == [0.0, 9.0, 10.0]


def test_hold_key_uses_last_captured_tick_when_ticks_are_skipped(tmp_path):
    layout = make_layout()
    stage = record(tmp_path, layout, [(0, {1: (1, 0, 0)}), (4, {1: (1, 0, 0)}), (9, {1: (1, 1, 1)})])
    samples = stage.GetPrimAtPath(mapping.entity_path(E, 1)).GetAttribute("xformOp:translate").GetTimeSamples()
    assert samples == [0.0, 4.0, 9.0]
    assert translate(stage, 1, 4) == expected(layout, 1, 0, 0, 1)


@pytest.mark.parametrize("n", [1, 2, 50, 300])
def test_criterion_5_sparse(tmp_path, n):
    stage = record(tmp_path, make_layout(), [(t, {1: (1, 2, 3)}) for t in range(n)])
    prim = stage.GetPrimAtPath(mapping.entity_path(E, 1))
    assert len(prim.GetAttribute("xformOp:translate").GetTimeSamples()) <= 2
    assert len(prim.GetAttribute("visibility").GetTimeSamples()) <= 2
    assert len(prim.GetAttribute("viron:name").GetTimeSamples()) <= 2


def test_criterion_6_rename(tmp_path):
    frames = [(t, {1: (1, 0, 0, "wolf" if t < 4 else "old wolf")}) for t in range(8)]
    stage = record(tmp_path, make_layout(), frames)
    name = stage.GetPrimAtPath(mapping.entity_path(E, 1)).GetAttribute("viron:name")
    assert [name.Get(t) for t in range(8)] == ["wolf"] * 4 + ["old wolf"] * 4


def test_entity_prim_shape_and_identity(tmp_path):
    stage = record(tmp_path, make_layout(), [(0, {42: (1, 0, 0)})])
    prim = stage.GetPrimAtPath(mapping.entity_path(E, 42))
    assert prim.IsA(UsdGeom.Xform)
    assert prim.GetAttribute("viron:entityId").Get() == 42
    cube = UsdGeom.Cube(stage.GetPrimAtPath(mapping.entity_geom_path(E, 42)))
    assert cube.GetSizeAttr().Get() == pytest.approx(0.4)
    assert tuple(cube.GetDisplayColorAttr().Get()[0]) == pytest.approx(mapping.entity_color(42))
    assert UsdGeom.Xformable(prim).GetOrderedXformOps()[0].GetOpName() == "xformOp:translate"


def test_wall_clock_and_time_range(tmp_path):
    layout = make_layout()
    path = tmp_path / "run.usda"
    writer = TimeSampledWriter.create(path, layout, ticks_per_second=24)
    for t in range(3, 8):
        writer.write(snap(layout, t, {}, wall_clock=100.0 + t * 2))
    writer.close()
    stage = Usd.Stage.Open(str(path))
    assert stage.GetStartTimeCode() == 3 and stage.GetEndTimeCode() == 7
    assert stage.GetTimeCodesPerSecond() == 24 and stage.GetFramesPerSecond() == 24
    clock = stage.GetPrimAtPath("/World").GetAttribute("viron:wallClock")
    assert [clock.Get(t) for t in range(3, 8)] == [106.0, 108.0, 110.0, 112.0, 114.0]


def test_default_ticks_per_second_is_10(tmp_path):
    stage = record(tmp_path, make_layout(), [(0, {})])
    assert stage.GetTimeCodesPerSecond() == 10 and stage.GetFramesPerSecond() == 10


def test_ticks_must_increase(tmp_path):
    layout = make_layout()
    writer = TimeSampledWriter.create(tmp_path / "r.usda", layout)
    writer.write(snap(layout, 5, {}))
    for bad in (5, 4):
        with pytest.raises(ValueError):
            writer.write(snap(layout, bad, {}))


def test_write_after_close_raises_and_close_is_idempotent(tmp_path):
    layout = make_layout()
    writer = TimeSampledWriter.create(tmp_path / "r.usda", layout)
    writer.close()
    writer.close()
    with pytest.raises(RuntimeError):
        writer.write(snap(layout, 0, {}))


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_invalid_ticks_per_second(tmp_path, bad):
    with pytest.raises(ValueError):
        TimeSampledWriter.create(tmp_path / "r.usda", make_layout(), ticks_per_second=bad)


def test_torn_ticks_recorded_in_custom_layer_data(tmp_path):
    layout = make_layout()
    path = tmp_path / "r.usda"
    writer = TimeSampledWriter.create(path, layout)
    writer.write(snap(layout, 0, {}))
    writer.write(snap(layout, 1, {}, warnings=["entity 3 listed in grids 1 and 2"]))
    writer.close()
    data = Usd.Stage.Open(str(path)).GetRootLayer().customLayerData
    assert data["vironUsd:tornTicks"] == {"1": "entity 3 listed in grids 1 and 2"}


def test_binary_layer_by_extension(tmp_path):
    layout = make_layout()
    writer = TimeSampledWriter.create(tmp_path / "r.usdc", layout)
    writer.write(snap(layout, 0, {1: (1, 0, 0)}))
    writer.close()
    assert (tmp_path / "r.usdc").read_bytes()[:8] == b"PXR-USDC"
    stage = Usd.Stage.Open(str(tmp_path / "r.usdc"))
    assert translate(stage, 1, 0) == expected(layout, 1, 0, 0, 1)
    assert stage.GetCompositionErrors() == []


def test_position_is_held_exactly_while_unplaced(tmp_path):
    layout = make_layout()
    frames = [(0, {1: (1, 0, 0)}), (1, {1: (1, 0, 0)})] + [(t, {}) for t in range(2, 6)] + [(6, {1: (1, 3, 3)})]
    stage = record(tmp_path, layout, frames)
    for t in range(0, 6):
        assert same(translate(stage, 1, t), expected(layout, 1, 0, 0, 1)), t
        assert visible(stage, 1, t) == (t < 2)
    assert translate(stage, 1, 6) == expected(layout, 1, 3, 3, 1) and visible(stage, 1, 6)
