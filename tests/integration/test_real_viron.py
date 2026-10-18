"""Integration suite against a real, locally started Viron — MVP acceptance criteria 1–5 and 10."""

from __future__ import annotations

import importlib.util
import statistics
import sys
import time
from pathlib import Path

import pytest
import requests
from pxr import Gf, Usd, UsdGeom

from viron_usd import mapping
from viron_usd.client import RequestsTransport, VironClient, VironError
from viron_usd.recorder import Recorder

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TICKS = 50


def load_random_walk():
    spec = importlib.util.spec_from_file_location("random_walk", ROOT / "examples" / "random_walk.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["random_walk"] = module
    spec.loader.exec_module(module)
    return module


def visible(stage, env, e, t):
    prim = stage.GetPrimAtPath(mapping.entity_path(env, e))
    return prim.IsValid() and UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(t)) == UsdGeom.Tokens.inherited


def translate_attr(stage, env, e):
    return stage.GetPrimAtPath(mapping.entity_path(env, e)).GetAttribute("xformOp:translate")


def test_token_is_required(viron_url):
    response = requests.get(f"{viron_url}/api/v1/environments", timeout=10)
    assert response.status_code == 401
    with pytest.raises(VironError) as exc:
        VironClient(viron_url, "not-a-token").get_grids(1)
    assert exc.value.status == 401


def test_scripted_random_walk_meets_criteria_1_to_5(viron_url, viron_token, get_only_session, tmp_path):
    rw = load_random_walk()
    driver = rw.RandomWalkDriver(viron_url, viron_token, seed=11)
    env = driver.create_world("viron-usd-it", grids=2, size=6)
    walkers = [driver.add_entity(f"walker-{i}") for i in range(6)]
    still = walkers[0]   # never moves: criterion 5
    pauser = walkers[1]  # still for ticks 5..14, moves at 15: criterion 4
    late = None

    client = VironClient(viron_url, viron_token, transport=RequestsTransport(get_only_session))
    expected: dict[int, dict[int, tuple[int, int, int]]] = {}
    names: dict[int, dict[int, str]] = {}
    latencies = []
    with Recorder.create(client, env, tmp_path, chunk_size=20) as recorder:
        for tick in range(TICKS):
            if tick == 10:
                late = driver.add_entity("late-arrival")
            if tick == 30:
                driver.remove_entity(walkers[2])
            if tick == 35:
                driver.rename(walkers[3], "renamed-walker")
            if tick:
                frozen = {still} | ({pauser} if 5 <= tick <= 14 else set())
                driver.step(frozen=frozen)
                if tick == 15 and driver.placements()[pauser] == expected[14][pauser]:
                    pytest.fail("pauser could not move at tick 15 (boxed in); pick another seed")
            start = time.perf_counter()
            recorder.capture(tick)
            latencies.append(time.perf_counter() - start)
            expected[tick] = driver.placements()
            names[tick] = {e: ("renamed-walker" if e == walkers[3] and tick >= 35 else None) for e in expected[tick]}

    # Criterion 1: only GET requests left the recorder.
    assert get_only_session.methods and set(get_only_session.methods) == {"GET"}

    stage = Usd.Stage.Open(str(tmp_path / "run.usda"))
    assert stage.GetCompositionErrors() == []
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, TICKS - 1)
    layout = recorder.layout
    everyone = walkers + [late]
    for tick, placed in expected.items():
        for e in everyone:
            # Criterion 3: presence.
            assert visible(stage, env, e, tick) == (e in placed), (tick, e)
            if e in placed:
                # Criterion 2: placement = cell centre + the entity's fan-out offset.
                g, x, y = placed[e]
                want = Gf.Vec3d(*mapping.entity_position(layout.grid(g), x, y, e))
                assert Gf.IsClose(translate_attr(stage, env, e).Get(tick), want, 1e-9), (tick, e)
                if names[tick].get(e):
                    assert stage.GetPrimAtPath(mapping.entity_path(env, e)).GetAttribute("viron:name").Get(tick) == names[tick][e]
    assert not visible(stage, env, late, 9) and visible(stage, env, late, 10)

    # Criterion 4: no drift while still from tick 4 (its last move) through 14.
    hold = translate_attr(stage, env, pauser).Get(5)
    for t in range(5, 15):
        assert Gf.IsClose(translate_attr(stage, env, pauser).Get(t), hold, 1e-9), t

    # Criterion 5: an entity that never moves has at most 2 translate samples per layer.
    for chunk in sorted(tmp_path.glob("chunk_*.usda")):
        layer_stage = Usd.Stage.Open(str(chunk))
        attr = translate_attr(layer_stage, env, still)
        assert len(attr.GetTimeSamples()) <= 2, chunk.name

    print(
        f"\n[viron-usd integration] {TICKS} ticks, {len(everyone)} entities, 2 grids: capture latency "
        f"mean {1000 * statistics.mean(latencies):.1f} ms, p95 "
        f"{1000 * sorted(latencies)[int(0.95 * (len(latencies) - 1))]:.1f} ms, "
        f"max {1000 * max(latencies):.1f} ms"
    )


def test_random_walk_demo_50_ticks_criterion_10(viron_url, viron_token, get_only_session, tmp_path):
    rw = load_random_walk()
    out = tmp_path / "demo"
    summary = rw.main(
        ["--viron-url", viron_url, "--token", viron_token, "--ticks", str(TICKS), "--entities", "8",
         "--grid-size", "8", "--out", str(out), "--chunk-size", "1000"],
        recorder_session=get_only_session,
    )
    assert set(get_only_session.methods) == {"GET"}
    stage = Usd.Stage.Open(summary["run"])
    assert stage.GetCompositionErrors() == []
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, TICKS - 1)
    env = summary["environment"]
    entities = stage.GetPrimAtPath(mapping.entities_path(env)).GetChildren()
    assert len(entities) == 8
    moved = 0
    for prim in entities:
        e = prim.GetAttribute("viron:entityId").Get()
        assert all(visible(stage, env, e, t) for t in range(TICKS))
        samples = prim.GetAttribute("xformOp:translate").GetTimeSamples()
        moved += len(samples) > 2
    assert moved >= 4  # the walkers actually walked
    print(
        f"\n[viron-usd integration] random_walk.py {TICKS} ticks, 8 entities: capture latency mean "
        f"{summary['capture_ms_mean']:.1f} ms, p95 {summary['capture_ms_p95']:.1f} ms, "
        f"{summary['requests_per_capture']:.1f} GET requests per capture"
    )
