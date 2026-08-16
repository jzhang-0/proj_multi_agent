"""`~/.amux` 布局与路径注入。

默认家目录是 `~/.amux`,测试用环境变量 `AMUX_HOME` 指到临时目录,
不碰用户真正的家目录。
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_AMUX_HOME = "AMUX_HOME"
WORKSPACES_DIR = "workspaces"
INDEX_NAME = "paths.toml"
META_NAME = "workspace.toml"
PROJECT_CONFIG_NAME = "amux.toml"


def amux_home() -> Path:
    """`AMUX_HOME` 环境变量优先,否则 `~/.amux`。"""
    override = os.environ.get(ENV_AMUX_HOME)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".amux").resolve()
