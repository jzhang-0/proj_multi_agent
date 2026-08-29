"""构建发行制品并在源码仓库外完成隔离安装 smoke。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

DISTRIBUTION = "amux-team"
NORMALIZED_DISTRIBUTION = "amux_team"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _project_version(root: Path) -> str:
    raw = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return raw["project"]["version"]


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode:
        detail = (proc.stdout + proc.stderr).strip()
        raise RuntimeError(f"命令失败 ({proc.returncode}): {' '.join(argv)}\n{detail}")
    return proc


def _assert_artifacts(dist_dir: Path, version: str) -> tuple[Path, Path]:
    wheel = dist_dir / f"{NORMALIZED_DISTRIBUTION}-{version}-py3-none-any.whl"
    sdist = dist_dir / f"{NORMALIZED_DISTRIBUTION}-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise RuntimeError(f"缺少预期制品: {wheel.name} / {sdist.name}")

    dist_info = f"{NORMALIZED_DISTRIBUTION}-{version}.dist-info"
    required = {
        "amux_runtime/prompts/README.md",
        "amux_runtime/prompts/common.md",
        "amux_runtime/prompts/leader.md",
        "amux_runtime/prompts/member.md",
        "amux_runtime/roster.toml",
        "work/__init__.py",
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/METADATA",
        f"{dist_info}/entry_points.txt",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(required - names)
        if missing:
            raise RuntimeError(f"wheel 缺少文件: {', '.join(missing)}")
        metadata = archive.read(f"{dist_info}/METADATA").decode()
        entry_points = archive.read(f"{dist_info}/entry_points.txt").decode()
    if f"Name: {DISTRIBUTION}" not in metadata or f"Version: {version}" not in metadata:
        raise RuntimeError("wheel METADATA 的发行名或版本不正确")
    if "License-Expression: MIT" not in metadata or "License-File: LICENSE" not in metadata:
        raise RuntimeError("wheel METADATA 没有正确声明 MIT 许可证")
    if "Requires-Dist: textual>=1.0" not in metadata:
        raise RuntimeError("wheel METADATA 缺少 textual 依赖")
    if "Requires-Dist: watchfiles>=0.24" not in metadata:
        raise RuntimeError("wheel METADATA 缺少 watchfiles 依赖")
    if "amux = console.cli:main" not in entry_points:
        raise RuntimeError("wheel 没有 amux 命令入口")

    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
    required_suffixes = (
        "/AGENTS.md",
        "/LICENSE",
        "/roster.toml",
        "/src/amux_runtime/prompts/common.md",
        "/src/amux_runtime/prompts/leader.md",
        "/src/amux_runtime/prompts/member.md",
        "/src/amux_runtime/prompts/README.md",
        "/src/amux_runtime/roster.toml",
        "/src/work/__init__.py",
    )
    missing_sdist = [
        suffix for suffix in required_suffixes if not any(name.endswith(suffix) for name in names)
    ]
    if missing_sdist:
        raise RuntimeError(f"sdist 缺少文件: {', '.join(missing_sdist)}")
    return wheel, sdist


def _isolated_smoke(wheel: Path, version: str, *, offline: bool = False) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("找不到 uv，无法做隔离安装 smoke")
    with tempfile.TemporaryDirectory(prefix="amux-release-") as raw:
        temp = Path(raw)
        project = temp / "outside-source" / "demo"
        project.mkdir(parents=True)
        venv = temp / "venv"
        env = os.environ.copy()
        env["AMUX_HOME"] = str(temp / "amux-home")
        env["UV_CACHE_DIR"] = str(temp / "uv-cache")
        _run(
            [uv, "venv", "--python", sys.executable, str(venv)],
            cwd=project,
            env=env,
        )
        install_argv = [
            uv,
            "pip",
            "install",
            "--python",
            str(venv / "bin" / "python"),
        ]
        if offline:
            install_argv.extend(["--offline", "--no-deps"])
        install_argv.append(str(wheel))
        _run(
            install_argv,
            cwd=project,
            env=env,
        )
        if not offline:
            _run(
                [
                    str(venv / "bin" / "python"),
                    "-c",
                    "import PIL, textual, watchfiles; print('release dependencies ok')",
                ],
                cwd=project,
                env=env,
            )
        amux = venv / "bin" / "amux"
        version_out = _run([str(amux), "--version"], cwd=project, env=env).stdout
        if f"amux {version}" not in version_out:
            raise RuntimeError(f"隔离安装版本输出异常: {version_out.strip()}")
        added = _run(
            [str(amux), "member", "add", "claude"],
            cwd=project,
            env=env,
        ).stdout
        if "已加入 claude" not in added:
            raise RuntimeError(f"包内名册回退失败: {added.strip()}")
        _run([str(amux), "team", "init"], cwd=project, env=env)
        shown = _run(
            [str(amux), "team", "show", "fable-core"],
            cwd=project,
            env=env,
        ).stdout
        if "Claude Fable 5" not in shown:
            raise RuntimeError("隔离安装无法读取默认团队")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("dist"))
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--offline-smoke",
        action="store_true",
        help="只离线安装 wheel payload，不验证依赖解析；不得用于正式发布",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repo_root()
    dist_dir = (root / args.out_dir).resolve()
    version = _project_version(root)
    try:
        if not args.skip_build:
            _run(
                [
                    shutil.which("uv") or "uv",
                    "build",
                    "--no-sources",
                    "--out-dir",
                    str(dist_dir),
                ],
                cwd=root,
            )
        wheel, sdist = _assert_artifacts(dist_dir, version)
        _isolated_smoke(wheel, version, offline=args.offline_smoke)
    except (OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"[release] FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"[release] PASS: {wheel.name}")
    print(f"[release] PASS: {sdist.name}")
    if args.offline_smoke:
        print("[release] PASS: wheel payload 已离线安装并读取包内资源（未验证依赖）")
    else:
        print("[release] PASS: wheel 及完整依赖已用全新缓存联网安装")
        print("[release] PASS: 命令入口与包内资源已在源码仓库外验证")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
