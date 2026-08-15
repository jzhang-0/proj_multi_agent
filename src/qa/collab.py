"""QA-005 真实多成员协作取证与离线验证。"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bus import BusPaths

RUN_TAG = "QA005-20260816-A"
MEMBERS = ("claude", "codex", "cursor", "agy")
EXPECTED_EDGES = (
    ("human", "claude"),
    ("claude", "codex"),
    ("claude", "cursor"),
    ("claude", "agy"),
    ("codex", "claude"),
    ("cursor", "claude"),
    ("agy", "claude"),
    ("claude", "human"),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def evidence_paths(root: Path | None = None) -> tuple[Path, Path, Path]:
    base = root or repo_root()
    stem = base / "tests" / "baseline" / "qa-005-collaboration-160x40"
    return (
        stem.with_suffix(".txt"),
        stem.with_suffix(".ansi"),
        base / "tests" / "evidence" / "qa-005-audit.jsonl",
    )


def _load_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}含非法 JSONL:{exc}") from exc
        if not isinstance(entry, dict):
            raise ValueError(f"{path}每行必须是 JSON object")
        entries.append(entry)
    return entries


def validate(root: Path | None = None) -> tuple[int, int]:
    """验证存档同时证明四人消息链、可见画面和真实控制。"""
    plain_path, ansi_path, audit_path = evidence_paths(root)
    for path in (plain_path, ansi_path, audit_path):
        if not path.is_file():
            raise ValueError(f"缺少证据:{path}")

    entries = _load_entries(audit_path)
    message_events = [
        entry for entry in entries if entry.get("event") in {"deposit", "deliver"}
    ]
    for sender, recipient in EXPECTED_EDGES:
        for event in ("deposit", "deliver"):
            if not any(
                entry.get("event") == event
                and entry.get("from") == sender
                and entry.get("to") == recipient
                and RUN_TAG in str(entry.get("preview", ""))
                for entry in message_events
            ):
                raise ValueError(f"缺少 {event} 边:{sender}->{recipient}")

    controls = [entry for entry in entries if entry.get("event") == "control"]
    if not any(
        entry.get("from") == "human"
        and entry.get("to") in MEMBERS
        and entry.get("action") == "interrupt"
        and entry.get("changed") is True
        for entry in controls
    ):
        raise ValueError("没有真实成功的 F5 interrupt 审计事件")

    plain = plain_path.read_text(encoding="utf-8")
    for marker in (*MEMBERS, RUN_TAG, "[控制] ✓ interrupt"):
        if marker not in plain:
            raise ValueError(f"截取物里缺少:{marker}")
    if "\x1b[" not in ansi_path.read_text(encoding="utf-8"):
        raise ValueError("带色截取物没有 ANSI 样式")
    return len(message_events), len(controls)


def _capture(session: str, *, colored: bool) -> str:
    args = ["tmux", "capture-pane", "-p", "-t", session]
    if colored:
        args.append("-e")
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def archive(session: str, bus_root: Path, start_line: int) -> tuple[int, int]:
    """只存档本轮 tag 的标准审计事件和本轮控制事件。"""
    plain_path, ansi_path, audit_path = evidence_paths()
    plain_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    plain_path.write_text(_capture(session, colored=False), encoding="utf-8")
    ansi_path.write_text(_capture(session, colored=True), encoding="utf-8")

    source = BusPaths.resolve(bus_root).log
    selected: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines()[start_line - 1 :]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or "event" not in entry:
            continue
        if RUN_TAG in str(entry.get("preview", "")) or entry.get("event") == "control":
            selected.append(entry)
    audit_path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in selected),
        encoding="utf-8",
    )
    return validate()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QA-005 真实协作证据")
    subparsers = parser.add_subparsers(dest="command")
    capture_parser = subparsers.add_parser("archive", help="从真实 console 与 bus 存档")
    capture_parser.add_argument("--session", required=True)
    capture_parser.add_argument("--bus-root", default=str(repo_root() / "bus"))
    capture_parser.add_argument("--start-line", type=int, required=True)
    subparsers.add_parser("verify", help="离线验证已存档证据")
    args = parser.parse_args(argv)

    try:
        if args.command == "archive":
            messages, controls = archive(
                args.session, Path(args.bus_root), args.start_line
            )
        elif args.command == "verify":
            messages, controls = validate()
        else:
            parser.error("请选 archive 或 verify")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"qa.collab: FAIL {exc}")
        return 1
    print(
        f"qa.collab: ok tag={RUN_TAG} members={','.join(MEMBERS)} "
        f"message-events={messages} controls={controls}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
