"""tmux 版本探测。"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from tmuxctl.errors import (
    TmuxCommandError,
    TmuxNotFoundError,
    TmuxTimeoutError,
    TmuxVersionError,
)

MIN_VERSION = (3, 2)
DEFAULT_TIMEOUT = 5.0

_VERSION_RE = re.compile(r"tmux\s+(\d+)\.(\d+)([A-Za-z0-9._-]*)", re.IGNORECASE)


@dataclass(frozen=True)
class TmuxVersion:
    """解析后的 tmux 版本。"""

    major: int
    minor: int
    suffix: str = ""

    def as_tuple(self) -> tuple[int, int]:
        """用于比较的 (major, minor)。suffix 不参与比较。"""
        return (self.major, self.minor)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}{self.suffix}"


def parse_version(output: str) -> TmuxVersion:
    """从 `tmux -V` 输出解析版本号。"""
    match = _VERSION_RE.search(output)
    if match is None:
        raise TmuxVersionError(f"无法解析 tmux 版本输出: {output!r}")
    return TmuxVersion(int(match.group(1)), int(match.group(2)), match.group(3) or "")


def probe(tmux: str = "tmux", *, timeout: float = DEFAULT_TIMEOUT) -> TmuxVersion:
    """探测 tmux 是否存在且 ≥ 3.2,否则抛出明确错误。"""
    argv = [tmux, "-V"]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        required = f"{MIN_VERSION[0]}.{MIN_VERSION[1]}"
        raise TmuxNotFoundError(f"未找到 tmux。请先安装 tmux ≥ {required}。") from exc
    except subprocess.TimeoutExpired as exc:
        raise TmuxTimeoutError(f"探测 tmux 版本超时 ({timeout}s): {' '.join(argv)}") from exc
    if proc.returncode != 0:
        raise TmuxCommandError(argv, proc.returncode, proc.stderr, proc.stdout)
    version = parse_version((proc.stdout or "") + (proc.stderr or ""))
    if version.as_tuple() < MIN_VERSION:
        required = f"{MIN_VERSION[0]}.{MIN_VERSION[1]}"
        raise TmuxVersionError(
            f"tmux 版本过低: 当前 {version},需要 ≥ {required}。请升级 tmux 后重试。"
        )
    return version
