"""ROS-001: roster.toml 加载与校验。"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from roster.load import load_roster
from roster.paths import default_path, repo_root
from roster.schema import RosterError, roster_from_dict
from roster.start import window_command


def write_toml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_repo_roster_loads_four_enabled_members() -> None:
    roster = load_roster()
    names = [member.name for member in roster.enabled_members()]
    assert names == ["claude", "codex", "cursor", "agy"]
    claude = roster.get("claude")
    assert claude is not None
    assert claude.command == "claude"
    assert claude.args == ("--permission-mode", "acceptEdits")
    assert "claude" in claude.render_greeting()
    assert "{name}" not in claude.render_greeting()


def test_load_from_explicit_path(tmp_path: Path) -> None:
    path = write_toml(
        tmp_path / "roster.toml",
        """
default_greeting_template = "hi {name}"
[[members]]
name = "bot"
command = "cat"
args = ["-n"]
enabled = true
env = { LANG = "C" }
""",
    )
    roster = load_roster(path)
    bot = roster.get("bot")
    assert bot is not None
    assert bot.env["LANG"] == "C"
    assert bot.args == ("-n",)
    assert window_command(bot).startswith("cat -n ")
    assert "hi bot" in window_command(bot)


def test_disabled_member_excluded_from_enabled(tmp_path: Path) -> None:
    path = write_toml(
        tmp_path / "r.toml",
        """
default_greeting_template = "x {name}"
[[members]]
name = "on"
command = "true"
[[members]]
name = "off"
command = "true"
enabled = false
""",
    )
    roster = load_roster(path)
    assert [m.name for m in roster.enabled_members()] == ["on"]
    assert roster.get("off") is not None


@pytest.mark.parametrize(
    "body",
    [
        "members = []\n",
        "[[members]]\ncommand = 'true'\n",
        """
default_greeting_template = "x {name}"
[[members]]
name = "human"
command = "true"
""",
        """
default_greeting_template = "x {name}"
[[members]]
name = "dup"
command = "true"
[[members]]
name = "dup"
command = "true"
""",
        """
default_greeting_template = "x {name}"
[[members]]
name = "bad name"
command = "true"
""",
        """
default_greeting_template = "x {name}"
[[members]]
name = "a"
command = "true"
enabled = "yes"
""",
        """
default_greeting_template = "x {name}"
[[members]]
name = "bad.bot"
command = "true"
""",
    ],
)
def test_invalid_roster_rejected(body: str) -> None:
    with pytest.raises(RosterError):
        roster_from_dict(tomllib.loads(body))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RosterError, match="找不到"):
        load_roster(tmp_path / "nope.toml")


def test_start_sh_is_thin_wrapper() -> None:
    text = (repo_root() / "start.sh").read_text(encoding="utf-8")
    assert "python -m roster" in text
    assert "ROSTER=(" not in text
    assert default_path().is_file()
