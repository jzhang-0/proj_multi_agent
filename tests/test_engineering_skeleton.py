"""QA-001 工程骨架契约测试。"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from console import __version__
from console.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_project_requires_supported_python_and_exposes_console_script() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["project"]["requires-python"] == ">=3.11"
    assert config["project"]["scripts"]["console"] == "console.cli:main"
    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert config["tool"]["ruff"]["target-version"] == "py311"


def test_console_entry_point_runs(tmp_path: Path) -> None:
    # CON-001 之后 `console` 不带参数会起全屏 TUI(会一直跑),入口接线用
    # --headless 验证:它等价于纯 hub 模式,清一次队列就返回。
    assert main(["--headless", "--once", "--bus-root", str(tmp_path / "bus")]) == 0


def test_console_reports_version() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0


def test_package_has_version() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == config["project"]["version"]
