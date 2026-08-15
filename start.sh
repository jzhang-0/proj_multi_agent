#!/bin/bash
# 一键启动 AI 群聊:为每个成员开一个 tmux 会话跑对应 CLI,然后在前台跑 hub。
# 用法: ./start.sh          启动全部成员 + hub
#       ./start.sh stop     关闭全部成员会话
#       ./start.sh <名字>   只启动某一个成员(claude/codex/cursor/agy)
set -e
cd "$(dirname "$0")"

# 名字|启动命令(名字必须和 tmux 会话名一致,消息按这个名字路由)
ROSTER=(
  "claude|claude --permission-mode acceptEdits"
  "codex|codex -s workspace-write -a on-request"
  "cursor|agent --force"
  "agy|agy --dangerously-skip-permissions -i"
)

PRIME='你是本机AI群聊的成员,你的名字是 NAME。先阅读本目录的 AGENTS.md 了解通信协议,然后运行 ./msg human NAME已上线 向人类报到,之后待命等待群消息。'

start_one() {
  local name="$1" cmd="$2"
  if tmux has-session -t "=$name" 2>/dev/null; then
    echo "[start] $name 已在运行,跳过"
    return
  fi
  local prompt="${PRIME//NAME/$name}"
  tmux new-session -d -s "$name" -e AGENT_NAME="$name" -c "$PWD" "$cmd '$prompt'; exec \$SHELL"
  echo "[start] $name 已启动 (查看: tmux attach -t $name)"
}

if [ "$1" = "stop" ]; then
  for spec in "${ROSTER[@]}"; do
    name="${spec%%|*}"
    tmux kill-session -t "=$name" 2>/dev/null && echo "[stop] $name 已关闭" || true
  done
  exit 0
fi

if [ -n "$1" ]; then
  for spec in "${ROSTER[@]}"; do
    name="${spec%%|*}"; cmd="${spec#*|}"
    [ "$name" = "$1" ] && { start_one "$name" "$cmd"; exit 0; }
  done
  echo "未知成员: $1"; exit 1
fi

for spec in "${ROSTER[@]}"; do
  start_one "${spec%%|*}" "${spec#*|}"
done

echo
echo "全部成员已拉起。本窗口是群聊记录(hub),Ctrl-C 退出(不影响成员会话)。"
echo "派活示例: ./msg claude 写一个fizzbuzz到fizzbuzz.py 写完让codex review 通过后向我汇报"
echo
exec python3 hub.py
