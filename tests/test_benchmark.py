import runpy
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "stage_size.py"


def test_stage_size_benchmark_runs_on_a_short_scenario(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["stage_size.py", "--ticks", "30", "--out", str(tmp_path)])
    runpy.run_path(str(BENCH), run_name="__main__")
    out = capsys.readouterr().out
    for row in ("unchunked .usda", "chunked (1000) .usda", "unchunked .usdc", "chunked (1000) .usdc"):
        assert row in out
    assert (tmp_path / "chunked_usda" / "run.usda").exists()
