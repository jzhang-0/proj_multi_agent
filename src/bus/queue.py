"""文件队列:入队、扫描、归档、死信。

一条消息一个 json 文件,写临时文件再原子改名,读方不会看到半截内容
(与 v0 `msg` 的做法一致)。文件名前缀是纳秒时间戳,排序即先进先出。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from bus.audit import AuditEvent, AuditLog
from bus.message import MalformedMessage, Message
from bus.paths import BusPaths


def new_filename() -> str:
    """纳秒时间戳 + 随机后缀,保证同一毫秒内并发入队也不撞名。"""
    return f"{time.time_ns()}-{uuid.uuid4().hex[:6]}.json"


def deposit(message: Message, paths: BusPaths, *, audit: bool = True) -> Path:
    """把消息写进队列,返回最终文件路径。

    默认顺手记一条 `deposit` 审计事件——入队方是 `./msg` 这种短命进程,
    不在这里记就没有别的地方记了。`audit=False` 留给不想留痕的场景(bench)。
    """
    paths.ensure()
    filename = new_filename()
    tmp = paths.queue / f".{filename}"
    tmp.write_text(json.dumps(message.to_dict(), ensure_ascii=False), encoding="utf-8")
    final = paths.queue / filename
    tmp.rename(final)
    if audit:
        AuditLog(paths).record(AuditEvent.DEPOSIT, message)
    return final


def pending(paths: BusPaths) -> list[Path]:
    """按入队顺序列出待投递消息;临时文件(`.` 开头)不算。"""
    if not paths.queue.is_dir():
        return []
    return sorted(path for path in paths.queue.glob("*.json") if not path.name.startswith("."))


def read_message(path: Path) -> Message:
    """读一条消息;文件读不出或结构非法都抛 `MalformedMessage`。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedMessage(f"不是合法 JSON: {exc}") from exc
    except OSError as exc:
        raise MalformedMessage(f"读取失败: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise MalformedMessage(f"不是 UTF-8 文本: {exc}") from exc
    return Message.from_dict(raw)


def _move(path: Path, target_dir: Path) -> Path:
    """移动文件到目标目录,重名时加后缀避免覆盖。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix}"
    path.rename(target)
    return target


def archive(path: Path, paths: BusPaths) -> Path:
    """处理完毕,归档到 `processed/`。"""
    return _move(path, paths.processed)


def quarantine(path: Path, paths: BusPaths, reason: str) -> Path:
    """畸形消息进死信目录,同名 `.err` 文件记原因,便于人事后查。"""
    target = _move(path, paths.dead)
    target.with_suffix(f"{target.suffix}.err").write_text(
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} {reason}\n", encoding="utf-8"
    )
    return target
