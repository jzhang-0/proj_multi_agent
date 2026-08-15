#!/usr/bin/env python3
"""本机多 AI 消息 hub。

轮询 bus/queue/ 里的消息文件,按收件人名字找到同名 tmux 会话,
用 send-keys 把消息"打字"进那个终端并回车。全部流量打印在本进程
的终端里(这个窗口就是人看的"群聊记录"),同时追加到 bus/log.jsonl。

收件人为 human 的消息不投递,只在这里高亮显示。
"""
import functools
import glob
import json
import os
import shutil
import subprocess
import time

print = functools.partial(print, flush=True)

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(ROOT, "bus", "queue")
DONE = os.path.join(ROOT, "bus", "processed")
LOG = os.path.join(ROOT, "bus", "log.jsonl")


def tmux_session_exists(name):
    r = subprocess.run(["tmux", "has-session", "-t", f"={name}"], capture_output=True)
    return r.returncode == 0


def deliver(m):
    """把消息注入收件人的 tmux 会话,返回是否成功。"""
    to = m["to"]
    if to == "human":
        return True  # 人的消息只显示在 hub 窗口,不需要投递
    if not tmux_session_exists(to):
        return False
    text = m["text"].replace("\n", " ")
    line = f"[群消息] 来自 {m['from']}: {text} —— 如需回复,运行: ./msg {m['from']} \"你的回复\""
    subprocess.run(["tmux", "send-keys", "-t", to, "-l", line], check=True)
    time.sleep(0.3)  # 给输入框一点时间接住文本,再敲回车
    subprocess.run(["tmux", "send-keys", "-t", to, "Enter"], check=True)
    return True


def main():
    os.makedirs(QUEUE, exist_ok=True)
    os.makedirs(DONE, exist_ok=True)
    print(f"[hub] 已启动,监听 {QUEUE}")
    print("[hub] 收件人名字 = tmux 会话名;发给 human 的消息只在本窗口显示\n")
    while True:
        for path in sorted(glob.glob(os.path.join(QUEUE, "*.json"))):
            with open(path, encoding="utf-8") as f:
                m = json.load(f)
            ok = deliver(m)
            mark = "★" if m["to"] == "human" else ("✓" if ok else "✗ 投递失败(没有这个 tmux 会话)")
            print(f"{m['ts']}  {m['from']} -> {m['to']}: {m['text']}  {mark}")
            m["delivered"] = ok
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
            shutil.move(path, os.path.join(DONE, os.path.basename(path)))
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[hub] 已退出")
