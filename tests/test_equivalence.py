"""The stages viron-usd writes are unchanged by its move onto usd-recorder.

Every scenario in ``tests/equivalence/scenarios.py`` is re-recorded and each output file is
compared with the golden copy written by the pre-migration code (see that module's docstring).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "equivalence"))

import scenarios  # noqa: E402

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    root = tmp_path_factory.mktemp("equivalence")
    scenarios.record_all(root)
    return root


def test_same_set_of_files(recorded):
    assert sorted(scenarios.files(recorded)) == sorted(scenarios.files(GOLDEN))


@pytest.mark.parametrize("name", sorted(scenarios.files(GOLDEN)))
def test_file_matches_golden(recorded, name):
    got = scenarios.canonical(recorded / name)
    want = scenarios.canonical(GOLDEN / name)
    assert got == want, f"{name} differs from its pre-migration golden copy"


def test_golden_binary_layers_are_binary():
    for name, path in scenarios.files(GOLDEN).items():
        if name.endswith(".usdc"):
            assert path.read_bytes()[:8] == b"PXR-USDC", name
