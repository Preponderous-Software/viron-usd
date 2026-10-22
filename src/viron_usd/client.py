"""Read-only client for Viron's REST API.

The recorder only ever reads from Viron. :class:`VironClient` has no method that sends anything
but ``GET``, and its transport interface does not even take an HTTP method: every request it
can make is a ``GET``. See ARCHITECTURE.md § Endpoints the recorder uses.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

DEFAULT_URL = "http://localhost:9999"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 3
TOKEN_ENV_VAR = "VIRON_TOKEN"


class VironError(Exception):
    """A Viron request failed (non-2xx status other than 404, or retries exhausted)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class VironNotFound(VironError):
    """Viron answered ``404`` — the thing asked about does not exist (any more)."""


class Response(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class Transport(Protocol):
    """Sends one HTTP ``GET``. There is deliberately no ``method`` parameter."""

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response: ...


class RequestsTransport:
    """Default transport, backed by a :class:`requests.Session`."""

    def __init__(self, session: Any = None):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        return self._session.get(url, headers=dict(headers), timeout=timeout)


@dataclass(frozen=True)
class EnvironmentInfo:
    environment_id: int
    name: str


@dataclass(frozen=True)
class GridDto:
    grid_id: int
    rows: int
    columns: int
    name: str | None


@dataclass(frozen=True)
class LocationDto:
    location_id: int
    x: int
    y: int


@dataclass(frozen=True)
class EntityDto:
    entity_id: int
    name: str


@dataclass(frozen=True)
class PlacementDto:
    entity_id: int
    environment_id: int
    grid_id: int
    location_id: int
    x: int
    y: int


def _retry_after_seconds(headers: Mapping[str, str], default: float) -> float:
    value = None
    for key, val in headers.items():
        if key.lower() == "retry-after":
            value = val
            break
    if value is None:
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default  # HTTP-date form: not used by Viron; fall back to the default backoff


class VironClient:
    """Thin, read-only wrapper over the Viron ``GET`` endpoints the recorder needs.

    The bearer token comes from ``token`` or, if that is ``None``, the ``VIRON_TOKEN``
    environment variable. A ``503`` is retried up to ``max_retries`` times, waiting for the
    number of seconds in its ``Retry-After`` header (Viron answers lock-wait overruns that way).
    """

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        token: str | None = None,
        *,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else os.environ.get(TOKEN_ENV_VAR)
        self.transport: Transport = transport if transport is not None else RequestsTransport()
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep
        self.request_count = 0
        self._bulk_placements: bool | None = None

    # -- transport -----------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            self.request_count += 1
            response = self.transport.get(url, self._headers(), self.timeout)
            status = response.status_code
            if status == 503 and attempt < self.max_retries:
                attempt += 1
                self._sleep(_retry_after_seconds(response.headers, default=float(attempt)))
                continue
            if status == 404:
                raise VironNotFound(f"GET {path} -> 404", status)
            if status >= 400:
                raise VironError(f"GET {path} -> {status}", status)
            return response.json()

    # -- endpoints (all GET) -------------------------------------------------------------

    def get_environment(self, environment_id: int) -> EnvironmentInfo:
        data = self._get(f"/api/v1/environments/{int(environment_id)}")
        return EnvironmentInfo(int(data["environmentId"]), str(data.get("name") or ""))

    def get_grids(self, environment_id: int) -> list[GridDto]:
        data = self._get(f"/api/v1/grids/environment/{int(environment_id)}")
        return [
            GridDto(int(g["gridId"]), int(g["rows"]), int(g["columns"]), g.get("name"))
            for g in data
        ]

    def get_locations_in_grid(self, grid_id: int) -> list[LocationDto]:
        data = self._get(f"/api/v1/locations/grid/{int(grid_id)}")
        return [LocationDto(int(loc["locationId"]), int(loc["x"]), int(loc["y"])) for loc in data]

    def get_entities_in_grid(self, grid_id: int) -> list[EntityDto]:
        data = self._get(f"/api/v1/entities/grid/{int(grid_id)}")
        return [EntityDto(int(e["entityId"]), str(e.get("name") or "")) for e in data]

    def get_location_of_entity(self, entity_id: int) -> LocationDto:
        loc = self._get(f"/api/v1/locations/entity/{int(entity_id)}")
        return LocationDto(int(loc["locationId"]), int(loc["x"]), int(loc["y"]))

    def get_entity(self, entity_id: int) -> EntityDto:
        e = self._get(f"/api/v1/entities/{int(entity_id)}")
        return EntityDto(int(e["entityId"]), str(e.get("name") or ""))

    def get_entities_in_environment(self, environment_id: int) -> list[EntityDto]:
        data = self._get(f"/api/v1/entities/environment/{int(environment_id)}")
        return [EntityDto(int(e["entityId"]), str(e.get("name") or "")) for e in data]

    def get_bulk_placements(self, environment_id: int) -> list[PlacementDto]:
        """Every placed entity in one environment, from one query (the bulk placement read
        Viron added after the pinned commit, Preponderous-Software/viron#235)."""
        data = self._get(f"/api/v1/locations/placements?environmentId={int(environment_id)}")
        return [
            PlacementDto(
                int(p["entityId"]),
                int(p["environmentId"]),
                int(p["gridId"]),
                int(p["locationId"]),
                int(p["x"]),
                int(p["y"]),
            )
            for p in data
        ]

    def supports_bulk_placements(self, environment_id: int) -> bool:
        """Whether this Viron instance offers the bulk placement read.

        Probed once with one ``get_bulk_placements(environment_id)`` call and remembered for
        the life of the client: any error answer (a Viron from before the endpoint, such as the
        pinned ``c0a6fc5``) means the per-grid + per-entity fallback is used instead.
        """
        if self._bulk_placements is None:
            try:
                self.get_bulk_placements(environment_id)
            except (VironError, KeyError, TypeError, ValueError):
                self._bulk_placements = False
            else:
                self._bulk_placements = True
        return self._bulk_placements
