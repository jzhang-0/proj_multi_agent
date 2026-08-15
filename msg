#!/usr/bin/env python3
"""发消息: ./msg <收件人> <内容...>

发件人名字取自环境变量 AGENT_NAME(人直接在终端里用时默认是 human)。
消息落盘到 bus/queue/,由 hub.py 负责投递到收件人所在的 tmux 会话。
"""
import json
import os
import sys
import time
import uuid

if len(sys.argv) < 3:
    print('用法: msg <收件人> <内容...>  例如: ./msg bob "帮我跑一下测试"')
    sys.exit(1)

to = sys.argv[1]
text = " ".join(sys.argv[2:])
sender = os.environ.get("AGENT_NAME", "human")

qdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bus", "queue")
os.makedirs(qdir, exist_ok=True)

msg = {"to": to, "from": sender, "text": text, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
fname = f"{time.time_ns()}-{uuid.uuid4().hex[:6]}.json"
# 先写临时文件再原子改名,避免 hub 读到写了一半的文件
tmp = os.path.join(qdir, "." + fname)
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(msg, f, ensure_ascii=False)
os.rename(tmp, os.path.join(qdir, fname))
print(f"[msg] {sender} -> {to}: 已进入队列")
