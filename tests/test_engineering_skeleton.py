"""QA-001 工程骨架契约测试。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pytest import CaptureFixture

from console import __version__
from console.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_project_requires_supported_python_and_exposes_console_script() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["project"]["requires-python"] == ">=3.11"
    assert config["project"]["scripts"]["console"] == "console.cli:main"
    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert config["tool"]["ruff"]["target-version"] == "py311"


def test_console_entry_point_runs(capsys: CaptureFixture[str]) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out

    assert "总控台工程环境已就绪" in output


def test_package_has_version() -> None:
    assert __version__ == "0.1.0"
