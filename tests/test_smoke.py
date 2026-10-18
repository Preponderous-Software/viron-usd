"""Smoke tests: the pinned USD runtime works and the CLI entry point runs."""

import subprocess
import sys

import pytest
from pxr import Gf, Usd, UsdGeom

from viron_usd import __version__
from viron_usd.cli import main


def test_usd_core_is_the_pinned_version():
    assert Usd.GetVersion() == (0, 26, 8)


def test_one_prim_stage_with_a_time_sample_round_trips(tmp_path):
    path = tmp_path / "smoke.usda"
    stage = Usd.Stage.CreateNew(str(path))
    xform = UsdGeom.Xform.Define(stage, "/World")
    op = xform.AddTranslateOp()
    op.Set(Gf.Vec3d(1.0, 2.0, 3.0), Usd.TimeCode(5))
    stage.GetRootLayer().Save()

    reopened = Usd.Stage.Open(str(path))
    attr = reopened.GetPrimAtPath("/World").GetAttribute("xformOp:translate")
    assert attr.GetTimeSamples() == [5.0]
    assert attr.Get(Usd.TimeCode(5)) == Gf.Vec3d(1.0, 2.0, 3.0)


def test_cli_help_exits_cleanly(capsys):
    assert main([]) == 0
    assert "viron-usd" in capsys.readouterr().out


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_console_script_module_runs():
    result = subprocess.run(
        [sys.executable, "-m", "viron_usd.cli", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "usage: viron-usd" in result.stdout
