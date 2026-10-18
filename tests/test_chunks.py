"""Chunked layers, value clips and flatten — MVP acceptance criteria 7–9."""

import random

import pytest
from pxr import Gf, Sdf, Usd, UsdGeom

from synthetic import make_layout, snap
from viron_usd import mapping
from viron_usd.chunks import ChunkedWriter, chunk_name, default_flat_path, flatten, has_clips
from viron_usd.writer import TimeSampledWriter

E = 1


def scripted_frames(ticks, entities=6, seed=3, late=None):
    """A random walk with removals and renames; ``late`` = (entity, first tick) arrives late."""
    rng = random.Random(seed)
    pos = {e: (1, rng.randrange(5), rng.randrange(5)) for e in range(1, entities + 1)}
    names = {e: f"e{e}" for e in pos}
    frames = []
    for t in range(ticks):
        placed = {}
        for e, (g, x, y) in list(pos.items()):
            if late and e == late[0] and t < late[1]:
                continue
            r = rng.random()
            if r < 0.4:
                g = rng.choice((1, 2))
                x, y = rng.randrange(5), rng.randrange(5)
                pos[e] = (g, x, y)
            if rng.random() < 0.01:
                names[e] = f"e{e}@{t}"
            if rng.random() < 0.05:
                continue  # unplaced this tick
            placed[e] = (g, x, y, names[e])
        frames.append((t, placed))
    return frames


LAYOUT = make_layout(((1, 5, 5), (2, 5, 5)))


def record_unchunked(path, frames, binary=False):
    writer = TimeSampledWriter.create(path, LAYOUT)
    for t, placed in frames:
        writer.write(snap(LAYOUT, t, placed))
    writer.close()
    return Usd.Stage.Open(str(path))


def record_chunked(out_dir, frames, chunk_size, binary=False):
    writer = ChunkedWriter(out_dir, LAYOUT, chunk_size=chunk_size, binary=binary)
    for t, placed in frames:
        writer.write(snap(LAYOUT, t, placed))
    writer.close()
    return writer


def resolved(stage, e, t):
    prim = stage.GetPrimAtPath(mapping.entity_path(E, e))
    if not prim.IsValid():
        return ("absent",)
    vis = UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(t))
    if vis == UsdGeom.Tokens.invisible:
        return ("invisible",)  # an unplaced entity has no position or name to compare
    return (
        "visible",
        prim.GetAttribute("xformOp:translate").Get(t),
        prim.GetAttribute("viron:name").Get(t),
    )


def assert_same_values(a, b, ticks, entities):
    clock_a = a.GetPrimAtPath("/World").GetAttribute("viron:wallClock")
    clock_b = b.GetPrimAtPath("/World").GetAttribute("viron:wallClock")
    for t in ticks:
        assert clock_a.Get(t) == clock_b.Get(t), t
        for e in entities:
            ra, rb = resolved(a, e, t), resolved(b, e, t)
            if ra == ("absent",) or rb == ("absent",):
                assert ra[0] in ("absent", "invisible") and rb[0] in ("absent", "invisible"), (t, e)
                continue
            assert ra[0] == rb[0], (t, e, ra, rb)
            if ra[0] == "visible":
                assert Gf.IsClose(ra[1], rb[1], 1e-9) and ra[2] == rb[2], (t, e, ra, rb)


@pytest.fixture(scope="module")
def long_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("long")
    frames = scripted_frames(2500, entities=5, late=(5, 2100))
    chunked = record_chunked(tmp / "chunked", frames, 1000)
    unchunked = record_unchunked(tmp / "flat_ref.usda", frames)
    return tmp, frames, chunked, unchunked


def test_criterion_7_three_chunks_and_time_range(long_run):
    tmp, frames, chunked, unchunked = long_run
    assert [p.name for p in chunked.chunk_paths] == ["chunk_0000.usda", "chunk_0001.usda", "chunk_0002.usda"]
    assert sorted(p.name for p in (tmp / "chunked").glob("chunk_*")) == [p.name for p in chunked.chunk_paths]
    stage = Usd.Stage.Open(str(chunked.root_path))
    assert stage.GetCompositionErrors() == []
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 2499)
    for i, (lo, hi) in enumerate([(0, 999), (1000, 1999), (2000, 2499)]):
        layer = Sdf.Layer.FindOrOpen(str(chunked.chunk_paths[i]))
        assert (layer.startTimeCode, layer.endTimeCode) == (lo, hi)
    assert stage.GetTimeCodesPerSecond() == 10
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert stage.GetDefaultPrim().GetPath() == "/World"


def test_criterion_7_values_equal_unchunked(long_run):
    tmp, frames, chunked, unchunked = long_run
    stage = Usd.Stage.Open(str(chunked.root_path))
    assert_same_values(stage, unchunked, range(0, 2500), range(1, 6))


def test_criterion_8_late_arrival_invisible_in_earlier_chunks(long_run):
    tmp, frames, chunked, unchunked = long_run
    stage = Usd.Stage.Open(str(chunked.root_path))
    prim = stage.GetPrimAtPath(mapping.entity_path(E, 5))
    assert prim.IsValid()
    for t in range(0, 2100):
        assert UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(t)) == UsdGeom.Tokens.invisible, t
    first_visible = next(t for t, placed in frames if 5 in placed)
    assert UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(first_visible)) == UsdGeom.Tokens.inherited


def test_static_scene_is_in_the_topology_layer_only(long_run):
    tmp, frames, chunked, unchunked = long_run
    stage = Usd.Stage.Open(str(chunked.root_path))
    for grid in LAYOUT.grids:
        mesh = stage.GetPrimAtPath(mapping.cells_path(E, grid.grid_id))
        assert mesh.IsA(UsdGeom.Mesh) and len(mesh.GetAttribute("viron:locationIds").Get()) == 25
    topology = Sdf.Layer.FindOrOpen(str(tmp / "chunked" / "run.topology.usda"))
    assert topology.GetPrimAtPath(mapping.cells_path(E, 1)) is not None
    for path in chunked.chunk_paths:
        assert Sdf.Layer.FindOrOpen(str(path)).GetPrimAtPath(mapping.cells_path(E, 1)) is None


def test_criterion_9_flatten_matches_and_has_no_clips(long_run):
    tmp, frames, chunked, unchunked = long_run
    root = Sdf.Layer.FindOrOpen(str(chunked.root_path))
    assert has_clips(root)
    out = flatten(chunked.root_path, tmp / "run_flat.usda")
    flat_layer = Sdf.Layer.FindOrOpen(str(out))
    assert not has_clips(flat_layer) and not flat_layer.subLayerPaths
    flat = Usd.Stage.Open(str(out))
    stage = Usd.Stage.Open(str(chunked.root_path))
    assert (flat.GetStartTimeCode(), flat.GetEndTimeCode()) == (0, 2499)
    assert_same_values(stage, flat, range(0, 2500), range(1, 6))


def test_flatten_binary(long_run):
    tmp, frames, chunked, unchunked = long_run
    out = flatten(chunked.root_path, tmp / "run_flat.usdc", binary=True)
    assert out.read_bytes()[:8] == b"PXR-USDC"
    assert not has_clips(Sdf.Layer.FindOrOpen(str(out)))
    assert_same_values(Usd.Stage.Open(str(chunked.root_path)), Usd.Stage.Open(str(out)), range(0, 2500, 7), range(1, 6))


def test_flatten_default_output_and_extension_mismatch(tmp_path):
    frames = scripted_frames(30)
    w = record_chunked(tmp_path, frames, 10)
    assert flatten(w.root_path) == tmp_path / "run_flat.usda"
    assert default_flat_path(w.root_path, binary=True) == tmp_path / "run_flat.usdc"
    with pytest.raises(ValueError):
        flatten(w.root_path, tmp_path / "x.usdc")
    with pytest.raises(ValueError):
        flatten(w.root_path, tmp_path / "x.usda", binary=True)
    with pytest.raises(ValueError):
        flatten(w.root_path, tmp_path / "x.txt")


def test_binary_chunks_stay_with_text_root(tmp_path):
    frames = scripted_frames(25)
    w = record_chunked(tmp_path, frames, 10, binary=True)
    assert [p.name for p in w.chunk_paths] == [chunk_name(i, True) for i in range(3)]
    assert all(p.read_bytes()[:8] == b"PXR-USDC" for p in w.chunk_paths)
    for name in ("run.usda", "run.topology.usda", "run.manifest.usda"):
        assert (tmp_path / name).read_text().startswith("#usda"), name
    ref = record_unchunked(tmp_path / "ref.usda", frames)
    assert_same_values(Usd.Stage.Open(str(w.root_path)), ref, range(25), range(1, 7))


def test_chunks_align_to_first_tick_and_skip_gaps(tmp_path):
    frames = [(5, {1: (1, 0, 0)}), (6, {1: (1, 1, 0)}), (40, {1: (1, 2, 0)}), (41, {1: (1, 2, 0)})]
    w = record_chunked(tmp_path, frames, 10)
    assert len(w.chunk_paths) == 2
    stage = Usd.Stage.Open(str(w.root_path))
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (5, 41)
    ref = record_unchunked(tmp_path / "ref.usda", frames)
    for t in (5, 6, 40, 41):
        assert resolved(stage, 1, t) == resolved(ref, 1, t)


def test_grids_are_reread_at_chunk_boundaries(tmp_path):
    small = make_layout(((1, 5, 5),))
    reads = []

    def refresh():
        reads.append(1)
        return LAYOUT  # grid 2 appears

    w = ChunkedWriter(tmp_path, small, chunk_size=5, refresh_layout=refresh)
    for t in range(5):
        w.write(snap(small, t, {1: (1, 0, 0)}))
    assert reads == []
    for t in range(5, 10):
        w.write(snap(LAYOUT, t, {1: (2, 1, 1)}))
    w.close()
    assert reads == [1]
    stage = Usd.Stage.Open(str(w.root_path))
    assert stage.GetPrimAtPath(mapping.cells_path(E, 2)).IsValid()
    want = Gf.Vec3d(*mapping.entity_position(LAYOUT.grid(2), 1, 1, 1))
    assert Gf.IsClose(stage.GetPrimAtPath(mapping.entity_path(E, 1)).GetAttribute("xformOp:translate").Get(7), want, 1e-9)


def test_chunked_writer_rules(tmp_path):
    with pytest.raises(ValueError):
        ChunkedWriter(tmp_path, LAYOUT, chunk_size=0)
    for bad in (0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ChunkedWriter(tmp_path, LAYOUT, ticks_per_second=bad)
    w = ChunkedWriter(tmp_path, LAYOUT, chunk_size=10)
    w.write(snap(LAYOUT, 3, {}))
    with pytest.raises(ValueError):
        w.write(snap(LAYOUT, 3, {}))
    w.close()
    w.close()
    with pytest.raises(RuntimeError):
        w.write(snap(LAYOUT, 9, {}))


def test_empty_run_writes_nothing(tmp_path):
    w = ChunkedWriter(tmp_path / "empty", LAYOUT, chunk_size=10)
    w.close()
    assert not w.root_path.exists()


def test_chunk_with_no_tick_is_dropped(tmp_path):
    w = ChunkedWriter(tmp_path, LAYOUT, chunk_size=5)
    for t in range(5):
        w.write(snap(LAYOUT, t, {1: (1, 0, 0)}))
    w.begin_tick(5)  # e.g. the snapshot for tick 5 then failed
    w.close()
    assert [p.name for p in w.chunk_paths] == ["chunk_0000.usda"]
    assert not (tmp_path / "chunk_0001.usda").exists()
    assert Usd.Stage.Open(str(w.root_path)).GetEndTimeCode() == 4


def test_failed_first_attempt_does_not_anchor_chunk_boundaries(tmp_path):
    w = ChunkedWriter(tmp_path, LAYOUT, chunk_size=5)
    w.begin_tick(0)  # the capture for tick 0 then failed
    for t in range(3, 8):
        w.write(snap(LAYOUT, t, {1: (1, 0, 0)}))
    w.close()
    assert [p.name for p in w.chunk_paths] == ["chunk_0000.usda"]
    stage = Usd.Stage.Open(str(w.root_path))
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (3, 7)


def test_extras_and_color_persist_across_chunk_boundaries(tmp_path):
    w = ChunkedWriter(tmp_path, LAYOUT, chunk_size=3)
    ref = TimeSampledWriter.create(tmp_path / "ref.usda", LAYOUT)
    extras = {1: {"hp": 10, "kind": "wolf", "color": (0.9, 0.2, 0.1)}}
    for t in range(9):
        placed = {1: (1, t % 5, 0)} if t != 4 else {}  # unplaced at tick 4 (chunk 1)
        s = snap(LAYOUT, t, placed, extras=extras if t == 1 else ({1: {"hp": 7}} if t == 7 else {}))
        w.write(s)
        ref.write(s)
    w.close()
    ref.close()
    stage = Usd.Stage.Open(str(w.root_path))
    flat = Usd.Stage.Open(str(tmp_path / "ref.usda"))
    prim, rprim = (st.GetPrimAtPath(mapping.entity_path(E, 1)) for st in (stage, flat))
    color, rcolor = (UsdGeom.Cube(st.GetPrimAtPath(mapping.entity_geom_path(E, 1))).GetDisplayColorAttr() for st in (stage, flat))
    for t in range(1, 9):
        if t == 4:
            continue
        assert prim.GetAttribute("extras:hp").Get(t) == rprim.GetAttribute("extras:hp").Get(t) == (7 if t >= 7 else 10), t
        assert prim.GetAttribute("extras:kind").Get(t) == "wolf", t
        assert Gf.IsClose(color.Get(t)[0], Gf.Vec3f(0.9, 0.2, 0.1), 1e-6), t
        assert Gf.IsClose(rcolor.Get(t)[0], Gf.Vec3f(0.9, 0.2, 0.1), 1e-6), t


def test_extras_type_change_across_chunk_boundary_raises(tmp_path):
    w = ChunkedWriter(tmp_path, LAYOUT, chunk_size=2)
    w.write(snap(LAYOUT, 0, {1: (1, 0, 0)}, extras={1: {"hp": 10}}))
    w.write(snap(LAYOUT, 1, {1: (1, 0, 0)}))
    w.write(snap(LAYOUT, 2, {1: (1, 0, 0)}))  # new chunk; hp not given
    with pytest.raises(ValueError):
        w.write(snap(LAYOUT, 3, {1: (1, 0, 0)}, extras={1: {"hp": 9.5}}))
    w2 = ChunkedWriter(tmp_path / "b", LAYOUT, chunk_size=2)
    w2.write(snap(LAYOUT, 0, {1: (1, 0, 0)}, extras={1: {"hp": 10}}))
    w2.write(snap(LAYOUT, 1, {}))
    w2.write(snap(LAYOUT, 2, {}))  # new chunk, entity not seen in it yet
    with pytest.raises(ValueError):
        w2.write(snap(LAYOUT, 3, {1: (1, 0, 0)}, extras={1: {"hp": "ten"}}))


def test_failed_layout_refresh_is_retried(tmp_path):
    small = make_layout(((1, 5, 5),))
    calls = {"n": 0}

    def refresh():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("viron unavailable")
        return LAYOUT

    w = ChunkedWriter(tmp_path, small, chunk_size=2, refresh_layout=refresh)
    w.write(snap(small, 0, {}))
    w.write(snap(small, 1, {}))
    with pytest.raises(RuntimeError):
        w.begin_tick(2)
    w.write(snap(LAYOUT, 2, {1: (2, 0, 0)}))  # retried refresh picks up grid 2
    assert calls["n"] == 2 and w.layout is LAYOUT
    w.close()
    assert Usd.Stage.Open(str(w.root_path)).GetPrimAtPath(mapping.cells_path(E, 2)).IsValid()
