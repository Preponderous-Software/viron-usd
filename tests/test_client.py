import inspect

import pytest

from fake_viron import FakeResponse, FakeViron, ReadOnlyViolation
from viron_usd.client import (
    RequestsTransport,
    VironClient,
    VironError,
    VironNotFound,
    _retry_after_seconds,
)


def test_reads_each_endpoint_with_dto_shapes(viron):
    env = viron.create_environment("world", 1, 2, 3)
    grid = viron.grids_of(env)[0]
    e = viron.create_entity("wolf")
    viron.place(e, grid, 1, 2)
    client = viron.client()

    assert client.get_environment(env).name == "world"
    [g] = client.get_grids(env)
    assert (g.grid_id, g.rows, g.columns, g.name) == (grid, 2, 3, None)
    locs = client.get_locations_in_grid(grid)
    assert len(locs) == 6 and {(l.x, l.y) for l in locs} == {(x, y) for x in range(2) for y in range(3)}
    [ent] = client.get_entities_in_grid(grid)
    assert (ent.entity_id, ent.name) == (e, "wolf")
    loc = client.get_location_of_entity(e)
    assert loc.location_id == viron.location_id(grid, 1, 2) and (loc.x, loc.y) == (1, 2)
    assert client.get_entity(e).name == "wolf"
    assert client.supports_bulk_placements() is False


def test_bearer_token_from_argument(viron):
    viron.create_environment("w", 1, 1, 1)
    assert viron.client(token="test-token").get_environment(1).environment_id == 1
    with pytest.raises(VironError) as exc:
        viron.client(token="wrong").get_environment(1)
    assert exc.value.status == 401


def test_bearer_token_from_environment_variable(monkeypatch):
    monkeypatch.setenv("VIRON_TOKEN", "from-env")
    fake = FakeViron(token="from-env")
    fake.create_environment("w", 1, 1, 1)
    client = VironClient("http://viron.test", transport=fake)
    assert client.token == "from-env"
    assert client.get_environment(1).name == "w"


def test_no_token_sends_no_authorization_header(monkeypatch):
    monkeypatch.delenv("VIRON_TOKEN", raising=False)
    seen = {}

    class T:
        def get(self, url, headers, timeout):
            seen.update(headers)
            seen["timeout"] = timeout
            return FakeResponse(200, {"environmentId": 1, "name": "w"})

    VironClient("http://x", transport=T(), timeout=2.5).get_environment(1)
    assert "Authorization" not in seen and seen["timeout"] == 2.5


def test_404_raises_not_found(viron):
    with pytest.raises(VironNotFound):
        viron.client().get_location_of_entity(99)


def test_503_is_retried_honouring_retry_after(viron):
    viron.create_environment("w", 1, 1, 1)
    viron.inject(r"/environments/1$", 503, {"Retry-After": "2"})
    viron.inject(r"/environments/1$", 503, {"retry-after": "0.5"})
    slept = []
    client = viron.client(sleep=slept.append)
    assert client.get_environment(1).name == "w"
    assert slept == [2.0, 0.5]


def test_503_gives_up_after_max_retries(viron):
    for _ in range(3):
        viron.inject(r"/environments/1$", 503)
    with pytest.raises(VironError) as exc:
        viron.client(max_retries=2).get_environment(1)
    assert exc.value.status == 503


def test_503_without_retry_after_backs_off_by_attempt_number(viron):
    viron.create_environment("w", 1, 1, 1)
    viron.inject(r"/environments/1$", 503)
    viron.inject(r"/environments/1$", 503)
    slept = []
    client = viron.client(sleep=slept.append)
    assert client.get_environment(1).name == "w"
    assert slept == [1.0, 2.0]
    assert client.request_count == 3


def test_503_retries_count_every_attempt(viron):
    for _ in range(3):
        viron.inject(r"/environments/1$", 503)
    client = viron.client(max_retries=2)
    with pytest.raises(VironError):
        client.get_environment(1)
    assert client.request_count == 3


def test_other_error_statuses_are_raised_without_retry(viron):
    viron.create_environment("w", 1, 1, 1)
    viron.inject(r"/environments/1$", 500)
    slept = []
    client = viron.client(sleep=slept.append)
    with pytest.raises(VironError) as exc:
        client.get_environment(1)
    assert exc.value.status == 500 and not isinstance(exc.value, VironNotFound)
    assert slept == [] and client.request_count == 1
    assert client.get_environment(1).name == "w"


def test_missing_names_read_as_empty_strings():
    bodies = {
        "/api/v1/environments/1": {"environmentId": 1, "name": None},
        "/api/v1/entities/2": {"entityId": 2},
        "/api/v1/entities/grid/3": [{"entityId": 4, "name": None}],
    }

    class T:
        def get(self, url, headers, timeout):
            return FakeResponse(200, bodies[url.replace("http://x", "")])

    client = VironClient("http://x", transport=T())
    assert client.get_environment(1).name == ""
    assert client.get_entity(2).name == ""
    assert [e.name for e in client.get_entities_in_grid(3)] == [""]


def test_retry_after_parsing():
    assert _retry_after_seconds({"Retry-After": "3"}, 1.0) == 3.0
    assert _retry_after_seconds({}, 1.5) == 1.5
    assert _retry_after_seconds({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 1.0) == 1.0
    assert _retry_after_seconds({"Retry-After": "-4"}, 1.0) == 0.0


def test_fake_viron_rejects_every_non_get_method():
    fake = FakeViron()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(ReadOnlyViolation):
            fake.request(method, "http://viron.test/api/v1/entities")


def test_client_only_ever_issues_get(viron):
    """MVP acceptance criterion 1: every public client call reaches the transport as a GET."""
    env = viron.create_environment("w", 1, 2, 2)
    grid = viron.grids_of(env)[0]
    e = viron.create_entity("a")
    viron.place(e, grid, 0, 0)
    client = viron.client()
    args = {"environment_id": env, "grid_id": grid, "entity_id": e}
    called = 0
    for name, method in inspect.getmembers(client, inspect.ismethod):
        if name.startswith("_"):
            continue
        params = [p for p in inspect.signature(method).parameters]
        method(*[args[p] for p in params])
        called += 1
    assert called >= 7
    assert viron.requests and {m for m, _ in viron.requests} == {"GET"}


def test_client_and_transport_expose_no_write_methods():
    for cls in (VironClient, RequestsTransport):
        names = {n.lower() for n in dir(cls)}
        for verb in ("post", "put", "patch", "delete"):
            assert not any(n == verb or n.startswith(verb + "_") for n in names), (cls, verb)
    assert "method" not in inspect.signature(RequestsTransport.get).parameters


def test_requests_transport_delegates_to_session_get():
    calls = []

    class Session:
        def get(self, url, headers, timeout):
            calls.append((url, headers, timeout))
            return FakeResponse(200, [])

        def post(self, *a, **k):  # pragma: no cover - must never be called
            raise AssertionError("POST")

    client = VironClient("http://x/", token="t", transport=RequestsTransport(Session()))
    assert client.get_grids(4) == []
    assert calls == [("http://x/api/v1/grids/environment/4", {"Accept": "application/json", "Authorization": "Bearer t"}, 10.0)]
