"""An in-memory fake Viron for unit tests.

It answers the ``GET`` endpoints the recorder uses with the same JSON shapes as Viron's DTOs
(``EnvironmentDto``, ``GridDto``, ``LocationDto``, ``EntityDto``) and **raises on any other
HTTP method** — MVP acceptance criterion 1. Tests mutate the world directly through the helper
methods (``place``, ``move``, ``rename`` …), standing in for a simulation that drives Viron.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


class ReadOnlyViolation(AssertionError):
    """The recorder tried to send a non-GET request to Viron."""


@dataclass
class FakeResponse:
    status_code: int
    body: Any = None
    headers: dict = field(default_factory=dict)

    def json(self) -> Any:
        return self.body


class FakeViron:
    """A tiny Viron world plus a transport that serves it."""

    def __init__(self, token: str | None = None):
        self.token = token
        self.environments: dict[int, str] = {}
        self.grids: dict[int, dict] = {}  # grid id -> {env, rows, columns, name}
        self.locations: dict[int, tuple[int, int, int]] = {}  # loc id -> (grid, x, y)
        self.entities: dict[int, str] = {}
        self.placement: dict[int, int] = {}  # entity id -> location id
        self.requests: list[tuple[str, str]] = []
        self.before_get: list[Callable[[str], None]] = []
        self.injected: list[tuple[str, FakeResponse]] = []
        self._next = {"env": 1, "grid": 1, "loc": 1, "entity": 1}

    # -- world building (the "driver" side; never reached through the transport) -----------

    def _id(self, kind: str) -> int:
        value = self._next[kind]
        self._next[kind] += 1
        return value

    def create_environment(self, name: str, num_grids: int, rows: int, columns: int) -> int:
        env = self._id("env")
        self.environments[env] = name
        for _ in range(num_grids):
            self.add_grid(env, rows, columns)
        return env

    def add_grid(self, env: int, rows: int, columns: int, name: str | None = None) -> int:
        grid = self._id("grid")
        self.grids[grid] = {"env": env, "rows": rows, "columns": columns, "name": name}
        for x in range(rows):
            for y in range(columns):
                self.locations[self._id("loc")] = (grid, x, y)
        return grid

    def grids_of(self, env: int) -> list[int]:
        return sorted(g for g, d in self.grids.items() if d["env"] == env)

    def location_id(self, grid: int, x: int, y: int) -> int:
        for loc, (g, lx, ly) in self.locations.items():
            if (g, lx, ly) == (grid, x, y):
                return loc
        raise KeyError((grid, x, y))

    def create_entity(self, name: str) -> int:
        entity = self._id("entity")
        self.entities[entity] = name
        return entity

    def place(self, entity: int, grid: int, x: int, y: int) -> None:
        self.placement[entity] = self.location_id(grid, x, y)

    move = place

    def remove(self, entity: int) -> None:
        self.placement.pop(entity, None)

    def delete_entity(self, entity: int) -> None:
        self.placement.pop(entity, None)
        self.entities.pop(entity, None)

    def rename(self, entity: int, name: str) -> None:
        self.entities[entity] = name

    def inject(self, path_regex: str, status: int, headers: dict | None = None) -> None:
        """Answer the next GET whose path matches ``path_regex`` with ``status``."""
        self.injected.append((path_regex, FakeResponse(status, {"status": status}, headers or {})))

    # -- transport --------------------------------------------------------------------------

    def request(self, method: str, url: str, **_: Any) -> FakeResponse:
        """Generic entry point: anything but GET is a read-only violation."""
        self.requests.append((method.upper(), url))
        if method.upper() != "GET":
            raise ReadOnlyViolation(f"recorder sent {method.upper()} {url}")
        raise AssertionError("use get()")  # pragma: no cover

    # requests.Session-style aliases so a fake session also rejects writes
    def post(self, url: str, **kw: Any) -> FakeResponse:
        return self.request("POST", url, **kw)

    def put(self, url: str, **kw: Any) -> FakeResponse:
        return self.request("PUT", url, **kw)

    def patch(self, url: str, **kw: Any) -> FakeResponse:
        return self.request("PATCH", url, **kw)

    def delete(self, url: str, **kw: Any) -> FakeResponse:
        return self.request("DELETE", url, **kw)

    def get(self, url: str, headers: dict | None = None, timeout: float | None = None) -> FakeResponse:
        self.requests.append(("GET", url))
        path = re.sub(r"^https?://[^/]+", "", url)
        for hook in list(self.before_get):
            hook(path)
        for i, (pattern, response) in enumerate(self.injected):
            if re.search(pattern, path):
                del self.injected[i]
                return response
        if self.token is not None:
            auth = (headers or {}).get("Authorization")
            if auth != f"Bearer {self.token}":
                return FakeResponse(401, {"status": 401, "message": "Unauthorized"})
        return self._route(path)

    def _route(self, path: str) -> FakeResponse:
        def nf(msg: str) -> FakeResponse:
            return FakeResponse(404, {"status": 404, "message": msg})

        def env_dto(e: int) -> dict:
            return {"environmentId": e, "name": self.environments[e], "creationDate": "2026-10-17"}

        def grid_dto(g: int) -> dict:
            d = self.grids[g]
            return {"gridId": g, "rows": d["rows"], "columns": d["columns"], "name": d["name"]}

        def loc_dto(loc: int) -> dict:
            _, x, y = self.locations[loc]
            return {"locationId": loc, "x": x, "y": y}

        def entity_dto(e: int) -> dict:
            return {"entityId": e, "name": self.entities[e], "creationDate": "2026-10-17"}

        if m := re.fullmatch(r"/api/v1/environments/(\d+)", path):
            e = int(m[1])
            return FakeResponse(200, env_dto(e)) if e in self.environments else nf("env")
        if m := re.fullmatch(r"/api/v1/grids/environment/(\d+)", path):
            return FakeResponse(200, [grid_dto(g) for g in self.grids_of(int(m[1]))])
        if m := re.fullmatch(r"/api/v1/locations/grid/(\d+)", path):
            g = int(m[1])
            return FakeResponse(200, [loc_dto(loc) for loc in sorted(self.locations) if self.locations[loc][0] == g])
        if m := re.fullmatch(r"/api/v1/entities/grid/(\d+)", path):
            g = int(m[1])
            ids = sorted(e for e, loc in self.placement.items() if self.locations[loc][0] == g)
            return FakeResponse(200, [entity_dto(e) for e in ids])
        if m := re.fullmatch(r"/api/v1/locations/entity/(\d+)", path):
            e = int(m[1])
            return FakeResponse(200, loc_dto(self.placement[e])) if e in self.placement else nf("loc")
        if m := re.fullmatch(r"/api/v1/entities/(\d+)", path):
            e = int(m[1])
            return FakeResponse(200, entity_dto(e)) if e in self.entities else nf("entity")
        return nf(f"no route {path}")

    def client(self, **kw: Any):
        from viron_usd.client import VironClient

        kw.setdefault("sleep", lambda s: None)
        return VironClient("http://viron.test", token=kw.pop("token", self.token), transport=self, **kw)

    def get_paths(self) -> list[str]:
        return [re.sub(r"^https?://[^/]+", "", u) for _, u in self.requests]
