import logging
import random

import pytest
from pxr import Gf, Usd, UsdGeom

from fake_viron import FakeViron
from viron_usd import cli, mapping
from viron_usd.recorder import Recorder, run_layer_path


def open_run(path):
    stage = Usd.Stage.Open(str(path))
    assert stage is not None and stage.GetCompositionErrors() == []
    return stage


def attr(stage, env, e, name):
    return stage.GetPrimAtPath(mapping.entity_path(env, e)).GetAttribute(name)


def is_visible(stage, env, e, t):
    prim = stage.GetPrimAtPath(mapping.entity_path(env, e))
    return UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(t)) == UsdGeom.Tokens.inherited


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_driven_mode_matches_the_script_at_every_tick(viron, tmp_path):
    env = viron.create_environment("w", 2, 4, 4)
    grids = viron.grids_of(env)
    rng = random.Random(7)
    ents = [viron.create_entity(f"e{i}") for i in range(5)]
    expected = {}
    with Recorder.create(viron.client(), env, tmp_path) as rec:
        for tick in range(20):
            for e in ents:
                r = rng.random()
                if r < 0.15:
                    viron.remove(e)
                elif r < 0.85:
                    viron.place(e, rng.choice(grids), rng.randrange(4), rng.randrange(4))
                if rng.random() < 0.1:
                    viron.rename(e, f"e{e}-t{tick}")
            rec.capture(tick)
            expected[tick] = {
                e: (viron.locations[viron.placement[e]], viron.entities[e]) for e in viron.placement
            }
    stage = open_run(run_layer_path(tmp_path))
    layout = rec.layout
    assert stage.GetStartTimeCode() == 0 and stage.GetEndTimeCode() == 19
    for tick, placed in expected.items():
        for e in ents:
            assert is_visible(stage, env, e, tick) == (e in placed), (tick, e)
            if e in placed:
                (g, x, y), name = placed[e]
                want = Gf.Vec3d(*mapping.entity_position(layout.grid(g), x, y, e))
                assert Gf.IsClose(attr(stage, env, e, "xformOp:translate").Get(tick), want, 1e-9)
                assert attr(stage, env, e, "viron:name").Get(tick) == name
    assert {m for m, _ in viron.requests} == {"GET"}


def test_capture_rejects_non_increasing_ticks(viron, tmp_path):
    env = viron.create_environment("w", 1, 2, 2)
    rec = Recorder.create(viron.client(), env, tmp_path)
    rec.capture(3)
    for bad in (3, 2):
        with pytest.raises(ValueError):
            rec.capture(bad)
    rec.close()
    with pytest.raises(RuntimeError):
        rec.capture(10)


def test_recorder_rejects_writer_for_other_environment(viron, tmp_path):
    env = viron.create_environment("a", 1, 2, 2)
    other = viron.create_environment("b", 1, 2, 2)
    rec = Recorder.create(viron.client(), env, tmp_path)
    with pytest.raises(ValueError):
        Recorder(viron.client(), other, rec.writer)


def test_poll_mode_records_requested_ticks_with_fake_clock(viron, tmp_path):
    env = viron.create_environment("w", 1, 3, 3)
    e = viron.create_entity("a")
    viron.place(e, viron.grids_of(env)[0], 0, 0)
    clock = FakeClock()
    rec = Recorder.create(viron.client(), env, tmp_path, clock=lambda: clock.now)
    assert rec.run_polling(0.5, 6, sleep=clock.sleep, monotonic=clock.monotonic) == 6
    rec.close()
    assert clock.slept == [0.5] * 5
    stage = open_run(run_layer_path(tmp_path))
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 5)
    wall = stage.GetPrimAtPath("/World").GetAttribute("viron:wallClock")
    assert [wall.Get(t) for t in range(6)] == [1000.0 + 0.5 * t for t in range(6)]


def test_poll_mode_does_not_burst_when_behind(viron, tmp_path):
    env = viron.create_environment("w", 1, 1, 1)
    clock = FakeClock()

    def slow_sleep(seconds):
        clock.slept.append(seconds)
        clock.now += seconds + 5  # oversleeps badly

    rec = Recorder.create(viron.client(), env, tmp_path)
    rec.run_polling(1.0, 4, sleep=slow_sleep, monotonic=clock.monotonic)
    # capture 0, sleep, capture 1 (overslept: 5 s late), capture 2 at once, sleep, capture 3:
    # one late capture is taken immediately, then spacing restarts — no burst of catch-ups.
    assert clock.slept == [1.0, 1.0]


def test_poll_mode_validates_arguments(viron, tmp_path):
    rec = Recorder.create(viron.client(), viron.create_environment("w", 1, 1, 1), tmp_path)
    with pytest.raises(ValueError):
        rec.run_polling(-1, 1)
    with pytest.raises(ValueError):
        rec.run_polling(1, -1)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            rec.run_polling(bad, 1)
    assert rec.captures == 0


# -- extras ----------------------------------------------------------------------------------


def _extras_world(viron, tmp_path):
    env = viron.create_environment("w", 1, 3, 3)
    g = viron.grids_of(env)[0]
    a, b = viron.create_entity("a"), viron.create_entity("b")
    viron.place(a, g, 0, 0)
    viron.place(b, g, 1, 1)
    return env, a, b, Recorder.create(viron.client(), env, tmp_path)


def test_no_extras_means_no_extras_attributes(viron, tmp_path):
    env, a, b, rec = _extras_world(viron, tmp_path)
    for t in range(3):
        rec.capture(t)
    rec.close()
    stage = open_run(run_layer_path(tmp_path))
    names = [p.GetName() for prim in stage.Traverse() for p in prim.GetProperties()]
    assert not [n for n in names if n.startswith("extras:")]
    geom = UsdGeom.Cube(stage.GetPrimAtPath(mapping.entity_geom_path(env, a)))
    assert geom.GetDisplayColorAttr().GetTimeSamples() == []


def test_extras_resolve_at_every_tick(viron, tmp_path):
    env, a, b, rec = _extras_world(viron, tmp_path)
    script = {
        0: {a: {"category": "wolf", "hp": 10, "speed": 1.5, "alpha": True, "heading": (1, 0, 0)}},
        1: {},
        2: {a: {"hp": 7}},
        3: {a: {"hp": 7, "speed": 2.25, "heading": [0, 1, 0]}, b: {"category": "sheep"}},
        4: {},
        5: {a: {"alpha": False}},
    }
    want = {}
    state = {a: {}, b: {}}
    for t, extras in script.items():
        rec.capture(t, extras=extras)
        for e, values in extras.items():
            state[e].update(values)
        want[t] = {e: dict(v) for e, v in state.items()}
    rec.close()
    stage = open_run(run_layer_path(tmp_path))
    for t, ents in want.items():
        for e, values in ents.items():
            for key, value in values.items():
                got = attr(stage, env, e, f"extras:{key}").Get(t)
                if isinstance(value, (list, tuple)):
                    assert Gf.IsClose(got, Gf.Vec3d(*value), 1e-9), (t, key)
                else:
                    assert got == value, (t, e, key)
    assert attr(stage, env, b, "extras:category").Get(0) == "sheep"  # held backwards from first sample
    assert len(attr(stage, env, a, "extras:category").GetTimeSamples()) <= 2


def test_extras_types(viron, tmp_path):
    env, a, b, rec = _extras_world(viron, tmp_path)
    rec.capture(0, extras={a: {"f": True, "i": 3, "d": 0.5, "s": "x", "v": (1, 2, 3)}})
    rec.close()
    stage = open_run(run_layer_path(tmp_path))
    types = {k: str(attr(stage, env, a, f"extras:{k}").GetTypeName()) for k in "fidsv"}
    assert types == {"f": "bool", "i": "int", "d": "double", "s": "string", "v": "double3"}


def test_color_extra_drives_display_color_from_that_tick(viron, tmp_path):
    env, a, b, rec = _extras_world(viron, tmp_path)
    rec.capture(0)
    rec.capture(1)
    rec.capture(2, extras={a: {"color": (0.9, 0.2, 0.1)}})
    rec.capture(3)
    rec.capture(4, extras={a: {"color": [0.0, 0.0, 1.0]}})
    rec.close()
    stage = open_run(run_layer_path(tmp_path))
    color = UsdGeom.Cube(stage.GetPrimAtPath(mapping.entity_geom_path(env, a))).GetDisplayColorAttr()
    hashed = Gf.Vec3f(*mapping.entity_color(a))
    got = [color.Get(t)[0] for t in range(5)]
    assert Gf.IsClose(got[0], hashed, 1e-6) and Gf.IsClose(got[1], hashed, 1e-6)
    assert Gf.IsClose(got[2], Gf.Vec3f(0.9, 0.2, 0.1), 1e-6)
    assert Gf.IsClose(got[3], Gf.Vec3f(0.9, 0.2, 0.1), 1e-6)
    assert Gf.IsClose(got[4], Gf.Vec3f(0, 0, 1), 1e-6)
    assert not attr(stage, env, a, "extras:color").IsValid()


def test_unrepresentable_extra_leaves_tick_retryable(viron, tmp_path):
    env, a, b, rec = _extras_world(viron, tmp_path)
    with pytest.raises(ValueError):
        rec.capture(0, extras={a: {"hp": 2**40}})
    rec.capture(0, extras={a: {"hp": 5}})  # the same tick can be captured once corrected
    rec.close()
    assert attr(open_run(run_layer_path(tmp_path)), env, a, "extras:hp").Get(0) == 5


def test_extras_type_change_raises_and_leaves_tick_unwritten(viron, tmp_path):
    env, a, b, rec = _extras_world(viron, tmp_path)
    rec.capture(0, extras={a: {"hp": 10}})
    with pytest.raises(ValueError):
        rec.capture(1, extras={a: {"hp": 9.5}})
    rec.capture(1, extras={a: {"hp": 9}})
    rec.close()


@pytest.mark.parametrize(
    "extras, error",
    [
        ({"bad key": 1}, ValueError),
        ({"color": (2, 0, 0)}, ValueError),
        ({"color": "red"}, ValueError),
        ({"thing": None}, TypeError),
        ({"big": 2**40}, ValueError),
        ({"vec": (10**400, 0, 0)}, ValueError),
    ],
)
def test_invalid_extras_are_rejected(viron, tmp_path, extras, error):
    env, a, b, rec = _extras_world(viron, tmp_path)
    with pytest.raises(error):
        rec.capture(0, extras={a: extras})


def test_extras_for_unplaced_entity_are_ignored_with_warning(viron, tmp_path, caplog):
    env, a, b, rec = _extras_world(viron, tmp_path)
    loner = viron.create_entity("unplaced")
    with caplog.at_level(logging.WARNING):
        rec.capture(0, extras={loner: {"category": "ghost"}})
    rec.close()
    assert f"extras for entity {loner} ignored" in caplog.text
    stage = open_run(run_layer_path(tmp_path))
    assert not stage.GetPrimAtPath(mapping.entity_path(env, loner)).IsValid()


# -- CLI ---------------------------------------------------------------------------------------


@pytest.fixture
def cli_viron(viron, monkeypatch):
    env = viron.create_environment("w", 1, 3, 3)
    e = viron.create_entity("a")
    viron.place(e, viron.grids_of(env)[0], 1, 1)
    seen = {}

    def make_client(url, token):
        seen["url"], seen["token"] = url, token
        return viron.client(token=token)

    monkeypatch.setattr(cli, "VironClient", make_client)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return env, e, seen


def test_record_writes_usda_by_default(cli_viron, tmp_path, capsys):
    env, e, seen = cli_viron
    out = tmp_path / "run"
    rc = cli.main(["record", "--environment", str(env), "--ticks", "3", "--interval", "0", "--chunk-size", "0",
                   "--token", "test-token", "--viron-url", "http://viron.test", "--out", str(out)])
    assert rc == 0
    assert seen == {"url": "http://viron.test", "token": "test-token"}
    path = out / "run.usda"
    assert path.read_text().startswith("#usda")
    stage = open_run(path)
    assert stage.GetEndTimeCode() == 2 and is_visible(stage, env, e, 2)
    assert "recorded 3 tick(s)" in capsys.readouterr().out


def test_record_writes_usdc_with_binary(cli_viron, tmp_path):
    env, e, _ = cli_viron
    rc = cli.main(["record", "--environment", str(env), "--ticks", "2", "--interval", "0",
                   "--token", "test-token", "--out", str(tmp_path), "--binary", "--ticks-per-second", "24",
                   "--chunk-size", "0"])
    assert rc == 0
    path = tmp_path / "run.usdc"
    assert path.read_bytes()[:8] == b"PXR-USDC" and not (tmp_path / "run.usda").exists()
    assert open_run(path).GetTimeCodesPerSecond() == 24


def test_record_token_from_environment_variable(cli_viron, tmp_path, monkeypatch):
    env, e, seen = cli_viron
    monkeypatch.setenv("VIRON_TOKEN", "test-token")
    assert cli.main(["record", "--environment", str(env), "--ticks", "1", "--out", str(tmp_path)]) == 0


def test_interrupting_record_leaves_a_valid_stage(cli_viron, tmp_path, monkeypatch, capsys):
    env, e, _ = cli_viron
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", sleep)
    rc = cli.main(["record", "--environment", str(env), "--interval", "0.01",
                   "--token", "test-token", "--out", str(tmp_path)])
    assert rc == 130
    stage = open_run(tmp_path / "run.usda")
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 2)
    assert "interrupted" in capsys.readouterr().err


def test_record_reports_viron_errors(cli_viron, tmp_path, capsys):
    rc = cli.main(["record", "--environment", "999", "--ticks", "1", "--token", "test-token", "--out", str(tmp_path)])
    assert rc == 1
    assert "404" in capsys.readouterr().err


def test_record_bad_token_reports_401(cli_viron, tmp_path, capsys):
    env, _, _ = cli_viron
    rc = cli.main(["record", "--environment", str(env), "--ticks", "1", "--token", "nope", "--out", str(tmp_path)])
    assert rc == 1 and "401" in capsys.readouterr().err


def test_record_without_a_token_warns(cli_viron, tmp_path, monkeypatch, capsys, caplog):
    env, _, seen = cli_viron
    monkeypatch.delenv("VIRON_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING, logger="viron_usd"):
        rc = cli.main(["record", "--environment", str(env), "--ticks", "1", "--out", str(tmp_path)])
    assert rc == 1 and seen["token"] is None
    assert "no token given (--token or $VIRON_TOKEN)" in caplog.text
    assert "401" in capsys.readouterr().err


def test_record_viron_error_mid_run_saves_what_was_recorded(cli_viron, viron, tmp_path, monkeypatch, capsys):
    env, e, _ = cli_viron
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 2:  # after ticks 0 and 1: the next capture's entity list fails
            viron.inject(r"^/api/v1/entities/grid/", 500)

    monkeypatch.setattr("time.sleep", sleep)
    rc = cli.main(["record", "--environment", str(env), "--interval", "5",
                   "--token", "test-token", "--out", str(tmp_path)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "-> 500; saving what was recorded" in captured.err
    assert f"recorded 2 tick(s) to {tmp_path / 'run.usda'}" in captured.out
    stage = open_run(tmp_path / "run.usda")
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 1) and is_visible(stage, env, e, 1)


@pytest.mark.parametrize(
    "bad",
    [["--ticks", "0"], ["--interval", "-1"], ["--ticks-per-second", "0"], [],
     ["--interval", "nan"], ["--interval", "inf"], ["--ticks-per-second", "nan"],
     ["--ticks-per-second", "inf"], ["--ticks-per-second", "1e309"],
     ["--chunk-size", "-1"], ["--ticks", "-3"], ["--ticks", "two"], ["--interval", "soon"]],
)
def test_record_argument_validation(bad, capsys):
    args = ["record"] + (bad + ["--environment", "1"] if bad else [])
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2


def test_record_is_chunked_by_default(cli_viron, tmp_path):
    env, e, _ = cli_viron
    assert cli.main(["record", "--environment", str(env), "--ticks", "3", "--interval", "0",
                     "--token", "test-token", "--out", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "chunk_0000.usda", "run.manifest.usda", "run.topology.usda", "run.usda"]


def test_record_chunked_binary(cli_viron, tmp_path):
    env, e, _ = cli_viron
    assert cli.main(["record", "--environment", str(env), "--ticks", "5", "--interval", "0", "--chunk-size", "2",
                     "--binary", "--token", "test-token", "--out", str(tmp_path)]) == 0
    chunks = sorted(p.name for p in tmp_path.glob("chunk_*"))
    assert chunks == ["chunk_0000.usdc", "chunk_0001.usdc", "chunk_0002.usdc"]
    stage = open_run(tmp_path / "run.usda")
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 4) and is_visible(stage, env, e, 4)


def test_recorder_chunked_rereads_grids_at_chunk_boundary(viron, tmp_path):
    env = viron.create_environment("w", 1, 3, 3)
    e = viron.create_entity("a")
    viron.place(e, viron.grids_of(env)[0], 0, 0)
    rec = Recorder.create(viron.client(), env, tmp_path, chunk_size=3)
    rec.capture(0)
    new_grid = viron.add_grid(env, 3, 3)
    viron.place(e, new_grid, 2, 2)
    rec.capture(1)  # same chunk: the new grid is not known yet, so the entity is not placed in the environment
    rec.capture(2)
    rec.capture(3)  # next chunk: grids re-read
    rec.close()
    stage = open_run(tmp_path / "run.usda")
    assert [is_visible(stage, env, e, t) for t in range(4)] == [True, False, False, True]
    assert stage.GetPrimAtPath(mapping.cells_path(env, new_grid)).IsValid()
    want = Gf.Vec3d(*mapping.entity_position(rec.layout.grid(new_grid), 2, 2, e))
    assert Gf.IsClose(attr(stage, env, e, "xformOp:translate").Get(3), want, 1e-9)


def test_flatten_command(cli_viron, tmp_path, capsys):
    env, e, _ = cli_viron
    assert cli.main(["record", "--environment", str(env), "--ticks", "4", "--interval", "0", "--chunk-size", "2",
                     "--token", "test-token", "--out", str(tmp_path)]) == 0
    assert cli.main(["flatten", str(tmp_path / "run.usda")]) == 0
    assert (tmp_path / "run_flat.usda").exists()
    assert cli.main(["flatten", str(tmp_path / "run.usda"), "--binary"]) == 0
    assert (tmp_path / "run_flat.usdc").read_bytes()[:8] == b"PXR-USDC"
    assert cli.main(["flatten", str(tmp_path / "run.usda"), "-o", str(tmp_path / "x.usdc")]) == 2
    assert "disagrees with --binary" in capsys.readouterr().err
    assert cli.main(["flatten", str(tmp_path / "missing.usda")]) == 1
    (tmp_path / "broken.usda").write_text("#usda 1.0\nthis is not usd {\n")
    assert cli.main(["flatten", str(tmp_path / "broken.usda")]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_flatten_command_writes_to_the_output_path(cli_viron, tmp_path, capsys):
    env, e, _ = cli_viron
    assert cli.main(["record", "--environment", str(env), "--ticks", "3", "--interval", "0", "--chunk-size", "2",
                     "--token", "test-token", "--out", str(tmp_path / "run")]) == 0
    out = tmp_path / "baked.usda"
    assert cli.main(["flatten", str(tmp_path / "run" / "run.usda"), "-o", str(out)]) == 0
    assert f"wrote {out}" in capsys.readouterr().out
    assert not (tmp_path / "run" / "run_flat.usda").exists()
    stage = open_run(out)
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 2) and is_visible(stage, env, e, 2)


@pytest.mark.parametrize("environment", ["abc", "1.5"])
def test_record_rejects_a_non_integer_environment(environment, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["record", "--environment", environment])
    assert exc.value.code == 2 and "--environment" in capsys.readouterr().err


@pytest.mark.parametrize("chunk_size", [None, 4])
def test_bulk_placement_read_records_the_same_stage_as_the_fallback(tmp_path, chunk_size):
    """The bulk read changes how Viron is read, never what is recorded."""
    out = {}
    for bulk in (False, True):
        fake = FakeViron(bulk_placements=bulk)
        env = fake.create_environment("w", 2, 3, 3)
        grids = fake.grids_of(env)
        rng = random.Random(5)
        ents = [fake.create_entity(f"e{i}") for i in range(5)]
        client = fake.client()
        out[bulk] = tmp_path / ("bulk" if bulk else "fallback")
        with Recorder.create(client, env, out[bulk], chunk_size=chunk_size, clock=lambda: 0.0) as rec:
            for tick in range(13):
                for e in ents:
                    r = rng.random()
                    if r < 0.15:
                        fake.remove(e)
                    elif r < 0.85:
                        fake.place(e, rng.choice(grids), rng.randrange(3), rng.randrange(3))
                    if rng.random() < 0.1:
                        fake.rename(e, f"e{e}-t{tick}")
                if tick == 6:
                    grids.append(fake.add_grid(env, 3, 3))  # only seen at the next chunk boundary
                rec.capture(tick)
        assert client.supports_bulk_placements(env) is bulk
    files = sorted(p.relative_to(out[False]).as_posix() for p in out[False].rglob("*.usd*"))
    assert files and files == sorted(p.relative_to(out[True]).as_posix() for p in out[True].rglob("*.usd*"))
    for name in files:
        assert (out[True] / name).read_bytes() == (out[False] / name).read_bytes(), name
