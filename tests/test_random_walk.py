"""examples/random_walk.py against the in-memory fake Viron (the real-Viron run is in tests/integration)."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from pxr import Usd, UsdGeom

from fake_viron import FakeResponse, FakeViron
from viron_usd import mapping

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rw():
    spec = importlib.util.spec_from_file_location("random_walk_unit", ROOT / "examples" / "random_walk.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["random_walk_unit"] = module
    spec.loader.exec_module(module)
    return module


class DriverSession:
    """Serves the driver's writes (and reads) from a FakeViron, like a requests.Session."""

    def __init__(self, fake: FakeViron):
        self.fake = fake
        self.headers = {}
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        path = re.sub(r"^https?://[^/]+", "", url)
        self.calls.append((method, path))
        f = self.fake
        if method == "GET":
            if m := re.fullmatch(r"/api/v1/locations/(\d+)/neighbors", path):
                g, x, y = f.locations[int(m[1])]
                ids = [loc for loc, (lg, lx, ly) in f.locations.items()
                       if lg == g and (lx, ly) != (x, y) and abs(lx - x) <= 1 and abs(ly - y) <= 1]
                return _resp(200, [{"locationId": i, "x": f.locations[i][1], "y": f.locations[i][2]} for i in ids])
            r = f.get(url, headers=self.headers)
            return _resp(r.status_code, r.body)
        if method == "POST" and path == "/api/v1/environments":
            env = f.create_environment(json["name"], json["numGrids"], json["gridSize"], json["gridSize"])
            return _resp(201, {"environmentId": env, "name": json["name"]})
        if method == "POST" and path == "/api/v1/entities":
            return _resp(201, {"entityId": f.create_entity(json["name"]), "name": json["name"]})
        if method == "PUT" and (m := re.fullmatch(r"/api/v1/locations/(\d+)/entity/(\d+)(/move)?", path)):
            loc, e = int(m[1]), int(m[2])
            if m[3] and loc in f.placement.values():
                return _resp(409, {"status": 409})
            f.placement[e] = loc
            return _resp(204 if m[3] else 200, None)
        if method == "DELETE" and (m := re.fullmatch(r"/api/v1/locations/entity/(\d+)", path)):
            f.remove(int(m[1]))
            return _resp(204, None)
        if method == "PATCH" and (m := re.fullmatch(r"/api/v1/entities/(\d+)/name", path)):
            f.rename(int(m[1]), json["name"])
            return _resp(200, None)
        raise AssertionError(f"unexpected {method} {path}")


def _resp(status, body):
    r = FakeResponse(status, body)
    r.content = b"" if body is None else b"x"
    r.text = str(body)
    return r


def test_driver_walks_entities_to_unoccupied_neighbours(rw):
    fake = FakeViron()
    d = rw.RandomWalkDriver("http://viron.test", None, seed=3, session=DriverSession(fake))
    env = d.create_world("w", 1, 6)
    ents = [d.add_entity(f"w{i}") for i in range(5)]
    assert len(set(d.position.values())) == 5
    for _ in range(10):
        before = dict(d.position)
        d.step(frozen={ents[0]})
        assert d.position[ents[0]] == before[ents[0]]
        assert len(set(d.position.values())) == 5  # never two on one location
        for e in ents[1:]:
            (g0, x0, y0), (g1, x1, y1) = d.coords[before[e]], d.coords[d.position[e]]
            assert g0 == g1 and max(abs(x0 - x1), abs(y0 - y1)) <= 1
    assert {e: fake.placement[e] for e in ents} == d.position
    d.remove_entity(ents[1])
    assert ents[1] not in fake.placement
    d.rename(ents[2], "renamed")
    assert fake.entities[ents[2]] == "renamed"
    assert env in fake.environments


def test_main_records_a_run(rw, tmp_path, monkeypatch, capsys):
    fake = FakeViron()
    monkeypatch.setattr(rw.requests, "Session", lambda: DriverSession(fake))
    out = tmp_path / "run"
    summary = rw.main(
        ["--viron-url", "http://viron.test", "--token", "", "--ticks", "12", "--entities", "4",
         "--grid-size", "5", "--out", str(out), "--chunk-size", "5"],
        recorder_session=fake,
    )
    assert summary["run"] == str(out / "run.usda")
    assert sorted(p.name for p in out.glob("chunk_*")) == ["chunk_0000.usda", "chunk_0001.usda", "chunk_0002.usda"]
    stage = Usd.Stage.Open(summary["run"])
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 11)
    entities = stage.GetPrimAtPath(mapping.entities_path(summary["environment"])).GetChildren()
    assert len(entities) == 4
    for prim in entities:
        assert UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(11)) == UsdGeom.Tokens.inherited
    assert "capture latency" in capsys.readouterr().out
    assert {m for m, _ in fake.requests} == {"GET"}  # the recorder's own requests


@pytest.mark.parametrize(
    "bad",
    [["--ticks", "0"], ["--ticks", "-3"], ["--entities", "0"], ["--grids", "0"], ["--grid-size", "0"],
     ["--chunk-size", "-1"], ["--entities", "10", "--grid-size", "3"],
     ["--ticks-per-second", "0"], ["--ticks-per-second", "-1"], ["--ticks-per-second", "nan"],
     ["--ticks-per-second", "inf"]],
)
def test_main_rejects_bad_arguments_before_any_request(rw, monkeypatch, bad):
    def no_session():
        raise AssertionError("no Viron request may be made")

    monkeypatch.setattr(rw.requests, "Session", no_session)
    with pytest.raises(SystemExit) as exc:
        rw.main(["--viron-url", "http://viron.test"] + bad)
    assert exc.value.code == 2
