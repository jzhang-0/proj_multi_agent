from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "install-amux.sh"


def _install_dev(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "installed"
    official = bin_dir / "amux"
    bin_dir.mkdir()
    official.write_text("official-pypi-command\n", encoding="utf-8")
    subprocess.run(
        [str(INSTALLER), "dev"],
        cwd=REPO_ROOT,
        env={**os.environ, "AMUX_BIN_DIR": str(bin_dir)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert official.read_text(encoding="utf-8") == "official-pypi-command\n"
    return bin_dir / "amux-dev", official


def _fake_uv(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        "printf 'AMUX_HOME=%s\\n' \"${AMUX_HOME-<unset>}\" > \"$AMUX_TEST_LOG\"\n"
        "printf '<%s>' \"$@\" >> \"$AMUX_TEST_LOG\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return fake_bin


def _run_shim(shim: Path, fake_bin: Path, log: Path, **extra: str) -> str:
    env = os.environ.copy()
    env.pop("AMUX_HOME", None)
    env.pop("AMUX_DEV_HOME", None)
    env.update(extra)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["AMUX_TEST_LOG"] = str(log)
    subprocess.run([str(shim), "--version"], env=env, check=True)
    return log.read_text(encoding="utf-8")


def test_dev_shim_preserves_official_command_and_reuses_default_home(tmp_path: Path) -> None:
    shim, _official = _install_dev(tmp_path)
    assert shim.is_file()
    assert os.access(shim, os.X_OK)

    output = _run_shim(shim, _fake_uv(tmp_path), tmp_path / "uv.log")

    assert output.startswith("AMUX_HOME=<unset>\n")
    assert f"<run><--project><{REPO_ROOT}><amux><--version>" in output


def test_dev_shim_uses_explicit_isolated_home_and_can_be_uninstalled(
    tmp_path: Path,
) -> None:
    shim, official = _install_dev(tmp_path)
    isolated = tmp_path / "isolated-state"

    output = _run_shim(
        shim,
        _fake_uv(tmp_path),
        tmp_path / "uv-isolated.log",
        AMUX_DEV_HOME=str(isolated),
    )
    assert output.startswith(f"AMUX_HOME={isolated}\n")

    subprocess.run(
        [str(INSTALLER), "uninstall-dev"],
        cwd=REPO_ROOT,
        env={**os.environ, "AMUX_BIN_DIR": str(shim.parent)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert not shim.exists()
    assert official.read_text(encoding="utf-8") == "official-pypi-command\n"
