import pytest

from fake_viron import FakeViron


@pytest.fixture
def viron() -> FakeViron:
    return FakeViron(token="test-token")
