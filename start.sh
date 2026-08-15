#!/bin/bash
# 薄入口:成员名册在 roster.toml,启动逻辑在 src/roster。
# 用法: ./start.sh          启动全部启用成员 + hub
#       ./start.sh stop     关闭名册中的成员会话
#       ./start.sh <名字>   只启动某一个成员
set -e
cd "$(dirname "$0")"
exec uv run python -m roster "$@"
