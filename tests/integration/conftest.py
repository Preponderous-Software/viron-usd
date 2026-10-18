"""Fixtures for the integration suite: a real Viron started locally (never a remote one).

Start Viron first with ``tests/integration/start_viron.sh`` (Docker Compose, pinned commit),
then run ``pytest -m integration``. ``VIRON_URL`` (default http://localhost:9999),
``JWT_SECRET`` and ``JWT_ISSUER`` must match the instance; the defaults match start_viron.sh.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest
import requests

from viron_token import DEFAULT_ISSUER, DEFAULT_SECRET, mint_token

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class GetOnlySession(requests.Session):
    """A requests session that fails the test on any non-GET request (MVP criterion 1/10)."""

    def __init__(self):
        super().__init__()
        self.methods: list[str] = []

    def request(self, method, url, *args, **kwargs):
        self.methods.append(method.upper())
        if method.upper() != "GET":
            raise AssertionError(f"the recorder sent {method.upper()} {url}; it may only send GET")
        return super().request(method, url, *args, **kwargs)


@pytest.fixture(scope="session")
def viron_url() -> str:
    url = os.environ.get("VIRON_URL", "http://localhost:9999").rstrip("/")
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        pytest.fail(f"integration tests only run against a locally started Viron, not {url}")
    try:
        health = requests.get(f"{url}/actuator/health", timeout=5)
    except requests.RequestException as exc:
        pytest.fail(f"no Viron at {url} ({exc}); start one with tests/integration/start_viron.sh")
    assert health.status_code == 200, health.text
    return url


@pytest.fixture(scope="session")
def viron_token() -> str:
    return mint_token(
        os.environ.get("JWT_SECRET", DEFAULT_SECRET), os.environ.get("JWT_ISSUER", DEFAULT_ISSUER)
    )


@pytest.fixture
def get_only_session() -> GetOnlySession:
    return GetOnlySession()
