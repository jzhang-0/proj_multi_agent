"""GATE-003 真实手机协作实测的存档与离线验证。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from bus import BusPaths

RUN_TAG = "GATE003-20260816-A"
EVIDENCE = Path("tests/evidence/gate-003-phone.json")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def evidence_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / EVIDENCE


def _edges(record: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    participants = record.get("participants", {})
    remote = f"im:{participants.get('remote_user', '')}"
    coordinator = str(participants.get("coordinator", ""))
    collaborator = str(participants.get("collaborator", ""))
    if not all((remote != "im:", coordinator, collaborator)):
        raise ValueError("参与者信息不完整")
    if coordinator == collaborator:
        raise ValueError("协调者和协作者必须是两个真实成员")
    return (
        (remote, coordinator),
        (coordinator, collaborator),
        (collaborator, coordinator),
        (coordinator, remote),
    )


def validate_record(record: dict[str, Any]) -> tuple[int, int]:
    """验证手机派活、成员往返、最终回手机的完整有向链。"""
    if record.get("goal") != "GATE-003" or record.get("run_tag") != RUN_TAG:
        raise ValueError("Goal 或实测 tag 不匹配")
    attestation = record.get("human_attestation", {})
    if attestation.get("device") != "physical phone browser":
        raise ValueError("缺少真实手机浏览器确认")
    if attestation.get("computer_untouched") is not True:
        raise ValueError("没有确认派活到收结果期间全程不碰电脑")
    if not str(attestation.get("confirmed_at", "")).strip():
        raise ValueError("缺少 human 确认时间")

    edges = _edges(record)
    audit = record.get("audit", [])
    if not isinstance(audit, list):
        raise ValueError("audit 必须是列表")
    for sender, recipient in edges:
        for event in ("deposit", "deliver"):
            if not any(
                isinstance(entry, dict)
                and entry.get("event") == event
                and entry.get("from") == sender
                and entry.get("to") == recipient
                and RUN_TAG in str(entry.get("preview", ""))
                for entry in audit
            ):
                raise ValueError(f"缺少 {event} 边:{sender}->{recipient}")

    transcript = record.get("transcript", [])
    if not isinstance(transcript, list):
        raise ValueError("transcript 必须是列表")
    expected_posts = [(sender, f"→ {recipient}:") for sender, recipient in edges]
    positions: list[int] = []
    for author, text_prefix in expected_posts:
        position = next(
            (
                index
                for index, item in enumerate(transcript)
                if isinstance(item, dict)
                and item.get("author") == author
                and text_prefix in str(item.get("text", ""))
                and RUN_TAG in str(item.get("text", ""))
            ),
            -1,
        )
        if position < 0:
            raise ValueError(f"手机群记录缺少:{author} {text_prefix}")
        positions.append(position)
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ValueError("手机群记录里的派活、协作、汇报顺序不对")
    return len(audit), len(transcript)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 必须是 JSON object")
    return value


def validate(root: Path | None = None) -> tuple[int, int]:
    path = evidence_path(root)
    if not path.is_file():
        raise ValueError(f"缺少证据:{path}")
    return validate_record(_load_json(path))


def _fetch_transcript(api_url: str, token: str) -> list[dict[str, Any]]:
    endpoint = api_url.rstrip("/") + "/api/messages?" + urlencode(
        {"since": 0, "token": token}
    )
    with urlopen(endpoint, timeout=30) as response:  # noqa: S310 (显式本地实测 URL)
        payload = json.load(response)
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("网关响应里的 messages 不是列表")
    return [item for item in messages if isinstance(item, dict) and RUN_TAG in str(item)]


def _audit_slice(bus_root: Path, start_line: int) -> list[dict[str, Any]]:
    lines = BusPaths.resolve(bus_root).log.read_text(encoding="utf-8").splitlines()
    selected: list[dict[str, Any]] = []
    for line in lines[start_line - 1 :]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and RUN_TAG in str(entry.get("preview", "")):
            selected.append(entry)
    return selected


def archive(
    *,
    api_url: str,
    token: str,
    bus_root: Path,
    start_line: int,
    remote_user: str,
    coordinator: str,
    collaborator: str,
    confirmed_at: str,
) -> tuple[int, int]:
    """在 human 确认真机操作后，存档本轮群记录和总线审计片段。"""
    record = {
        "schema": 1,
        "goal": "GATE-003",
        "run_tag": RUN_TAG,
        "participants": {
            "remote_user": remote_user,
            "coordinator": coordinator,
            "collaborator": collaborator,
        },
        "human_attestation": {
            "device": "physical phone browser",
            "computer_untouched": True,
            "confirmed_at": confirmed_at,
        },
        "archived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "transcript": _fetch_transcript(api_url, token),
        "audit": _audit_slice(bus_root, start_line),
    }
    counts = validate_record(record)
    target = evidence_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GATE-003 真实手机协作证据")
    subparsers = parser.add_subparsers(dest="command")
    capture = subparsers.add_parser("archive", help="存档已由 human 确认的真机实测")
    capture.add_argument("--api-url", required=True)
    capture.add_argument("--token", required=True)
    capture.add_argument("--bus-root", required=True, type=Path)
    capture.add_argument("--start-line", required=True, type=int)
    capture.add_argument("--user", required=True)
    capture.add_argument("--coordinator", required=True)
    capture.add_argument("--collaborator", required=True)
    capture.add_argument("--confirmed-at", required=True)
    subparsers.add_parser("verify", help="离线验证已存档证据")
    args = parser.parse_args(argv)

    try:
        if args.command == "archive":
            audit_count, transcript_count = archive(
                api_url=args.api_url,
                token=args.token,
                bus_root=args.bus_root,
                start_line=args.start_line,
                remote_user=args.user,
                coordinator=args.coordinator,
                collaborator=args.collaborator,
                confirmed_at=args.confirmed_at,
            )
        elif args.command == "verify":
            audit_count, transcript_count = validate()
        else:
            parser.error("请选 archive 或 verify")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"qa.gateway_phone: FAIL {exc}")
        return 1
    print(
        f"qa.gateway_phone: ok tag={RUN_TAG} "
        f"audit-events={audit_count} transcript-posts={transcript_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
