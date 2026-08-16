"""工作区登记簿:源数据在 `workspaces/<slug>/workspace.toml`,反查在 `paths.toml`。"""

from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

from workspace.errors import SlugError, WorkspaceError, WorkspaceNotFound
from workspace.model import Workspace
from workspace.paths import INDEX_NAME, META_NAME, WORKSPACES_DIR, amux_home
from workspace.slug import allocate_slug, suggested_slug, validate_slug


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Store:
    """一个 AMUX_HOME 下的工作区登记簿。"""

    def __init__(self, home: str | os.PathLike[str] | None = None) -> None:
        self.home = Path(home).expanduser().resolve() if home is not None else amux_home()

    @classmethod
    def default(cls) -> Store:
        return cls()

    @property
    def workspaces_dir(self) -> Path:
        return self.home / WORKSPACES_DIR

    @property
    def index_file(self) -> Path:
        return self.home / INDEX_NAME

    def ensure_home(self) -> Store:
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        return self

    def state_dir(self, slug: str) -> Path:
        return self.workspaces_dir / slug

    def get(self, slug: str) -> Workspace | None:
        meta = self.state_dir(slug) / META_NAME
        if not meta.is_file():
            return None
        return self._load_meta(slug, meta)

    def get_by_path(self, project_root: str | os.PathLike[str]) -> Workspace | None:
        root = Path(project_root).expanduser().resolve()
        return self._scan().get(root)

    def slugs(self) -> set[str]:
        if not self.workspaces_dir.is_dir():
            return set()
        return {
            child.name
            for child in self.workspaces_dir.iterdir()
            if child.is_dir() and (child / META_NAME).is_file()
        }

    def list(self) -> list[Workspace]:
        return sorted(
            (ws for slug in self.slugs() if (ws := self.get(slug)) is not None),
            key=lambda item: item.slug,
        )

    def path_index(self) -> dict[Path, Workspace]:
        """绝对项目根 → 工作区。以各 slug 目录为源,并回写 paths.toml。"""
        mapping = self._scan()
        self._write_index(mapping)
        return mapping

    def _scan(self) -> dict[Path, Workspace]:
        mapping: dict[Path, Workspace] = {}
        for ws in self.list():
            mapping[ws.project_root] = ws
        return mapping

    def add(
        self,
        project_root: str | os.PathLike[str],
        *,
        slug: str | None = None,
    ) -> tuple[Workspace, bool]:
        """登记一个项目。已登记则原样返回,created=False。"""
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise WorkspaceError(f"不是目录,无法登记为工作区: {root}")
        existing = self.get_by_path(root)
        if existing is not None:
            return existing, False

        taken = self.slugs()
        if slug is not None:
            chosen = validate_slug(slug)
            occupant = self.get(chosen)
            if occupant is not None:
                raise SlugError(
                    f"slug {chosen!r} 已被 {occupant.project_root} 占用,换一个 --slug"
                )
        else:
            chosen = allocate_slug(suggested_slug(root.name), taken)

        self.ensure_home()
        state = self.state_dir(chosen)
        state.mkdir(parents=True, exist_ok=False)
        meta = state / META_NAME
        meta.write_text(f"path = {_toml_str(str(root))}\n", encoding="utf-8")
        workspace = Workspace(slug=chosen, project_root=root, state_dir=state)
        self.path_index()
        return workspace, True

    def remove(self, slug: str) -> Workspace:
        """取消登记并删除状态目录,不碰用户项目文件。"""
        workspace = self.get(slug)
        if workspace is None:
            raise WorkspaceNotFound(f"没有叫 {slug!r} 的工作区")
        shutil.rmtree(workspace.state_dir)
        self.path_index()
        return workspace

    def _load_meta(self, slug: str, meta: Path) -> Workspace:
        try:
            raw = tomllib.loads(meta.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise WorkspaceError(f"无法解析 {meta}: {exc}") from exc
        path_value = raw.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise WorkspaceError(f"{meta} 缺少 path")
        return Workspace(
            slug=slug,
            project_root=Path(path_value).expanduser().resolve(),
            state_dir=meta.parent,
        )

    def _write_index(self, mapping: dict[Path, Workspace]) -> None:
        self.ensure_home()
        lines = [
            "# path → slug 反查。源数据是 workspaces/<slug>/workspace.toml。",
            "[paths]",
        ]
        for path, ws in sorted(mapping.items(), key=lambda item: str(item[0])):
            lines.append(f"{_toml_str(str(path))} = {_toml_str(ws.slug)}")
        self.index_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
