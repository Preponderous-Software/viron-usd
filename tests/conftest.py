import pytest

from fake_viron import FakeViron


@pytest.fixture
def viron() -> FakeViron:
    return FakeViron(token="test-token")


@pytest.fixture
def viron_bulk() -> FakeViron:
    """A fake Viron that also serves the bulk placement read."""
    return FakeViron(token="test-token", bulk_placements=True)
