"""slug 生成、校验、撞名自动加后缀。

human 拍板:默认取目录名;两个项目同名时 `name-2`、`name-3`。
tmux 会把会话名里的 `:` 当成 session:window、把 `.` 改写成 `_`,
所以这两类字符在生成和显式 `--slug` 两处都直接报错。
"""

from __future__ import annotations

import re

from workspace.errors import SlugError

SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MAX_SLUG_LENGTH = 64


def validate_slug(slug: str) -> str:
    """校验 slug 可安全拼进 tmux 会话名 `<成员>@<slug>`。"""
    if not slug:
        raise SlugError("slug 不能为空")
    if ":" in slug:
        raise SlugError(
            f"slug {slug!r} 含非法字符 ':'(tmux 会把它当成 session:window 分隔符,"
            "可能静默命中已有会话)"
        )
    if "." in slug:
        raise SlugError(
            f"slug {slug!r} 含非法字符 '.'(tmux 会把它静默改写成 '_')"
        )
    if not SLUG_PATTERN.fullmatch(slug):
        raise SlugError(
            f"slug {slug!r} 只能用字母、数字、连字符、下划线,且必须以字母或数字开头"
        )
    if len(slug) > MAX_SLUG_LENGTH:
        raise SlugError(f"slug 最长 {MAX_SLUG_LENGTH} 字符,实际 {len(slug)}")
    return slug


def suggested_slug(dirname: str) -> str:
    """从项目目录名得到候选 slug;目录名本身非法时要求 --slug。"""
    try:
        return validate_slug(dirname)
    except SlugError as exc:
        raise SlugError(
            f"目录名 {dirname!r} 不能直接做 slug({exc})。"
            "请用 --slug 指定一个只用字母数字和连字符的名字"
        ) from exc


def implicit_slug(dirname: str) -> str:
    """自动登记时从目录名挤出一个合法 slug;挤不出来就用 `ws`。

    显式 `amux workspace add` 仍走 `suggested_slug`(非法就要求 `--slug`)。
    人在某个目录裸敲 `amux` 不该因为目录名带点就被弹回 amux 仓库。
    """
    try:
        return suggested_slug(dirname)
    except SlugError:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "-", dirname).strip("-_")
        if not cleaned or not cleaned[0].isalnum():
            cleaned = "ws" if not cleaned else f"ws-{cleaned}"
        return validate_slug(cleaned[:MAX_SLUG_LENGTH].rstrip("-_"))


def allocate_slug(base: str, taken: set[str]) -> str:
    """base 已被占用则依次试 base-2、base-3,直到空位。"""
    candidate = validate_slug(base)
    if candidate not in taken:
        return candidate
    n = 2
    while True:
        suffix = f"{candidate}-{n}"
        if len(suffix) > MAX_SLUG_LENGTH:
            raise SlugError(f"无法为 {candidate!r} 分配不撞名的 slug(加后缀后超长)")
        if suffix not in taken:
            return suffix
        n += 1
