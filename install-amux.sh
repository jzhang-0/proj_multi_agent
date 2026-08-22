#!/bin/bash
# 把源码 checkout 装成全局薄 shim(默认放在 ~/.local/bin)。
# 用法: ./install-amux.sh                历史兼容:安装 / 覆盖 amux
#       ./install-amux.sh uninstall      卸载历史 amux shim
#       ./install-amux.sh dev            安装 / 覆盖 amux-dev
#       ./install-amux.sh uninstall-dev  卸载 amux-dev
#
# 为什么是 shim 而不是 `uv tool install`:后者会把 pyproject 里的 console 和
# roster 也一并塞进全局 PATH,console 这种通用词占全局命名空间正是要避的;
# shim 只暴露 amux 一个,而且每次都走 `uv run`,依赖始终跟 uv.lock 一致。
set -e
repo="$(cd "$(dirname "$0")" && pwd)"
bindir="${AMUX_BIN_DIR:-$HOME/.local/bin}"
mode="${1:-install}"

if [ "$mode" = "uninstall" ]; then
  target="$bindir/amux"
  rm -f "$target"
  echo "已卸载 $target"
  exit 0
fi
if [ "$mode" = "uninstall-dev" ]; then
  target="$bindir/amux-dev"
  rm -f "$target"
  echo "已卸载 $target"
  exit 0
fi
if [ "$mode" != "install" ] && [ "$mode" != "dev" ]; then
  echo "用法: $0 [dev|uninstall|uninstall-dev]" >&2
  exit 2
fi

mkdir -p "$bindir"
if [ "$mode" = "dev" ]; then
  target="$bindir/amux-dev"
  cat > "$target" <<EOF
#!/bin/sh
# 由 $repo/install-amux.sh dev 生成,勿手改。
# 默认不设置 AMUX_HOME,与 PyPI 版共同读取 ~/.amux;显式 AMUX_DEV_HOME 可隔离。
if [ -n "\${AMUX_DEV_HOME:-}" ]; then
    exec env AMUX_HOME="\$AMUX_DEV_HOME" uv run --project "$repo" amux "\$@"
fi
exec uv run --project "$repo" amux "\$@"
EOF
  chmod +x "$target"
  echo "已安装 $target -> $repo"
  echo "amux-dev 默认复用 ~/.amux;隔离测试可设置 AMUX_DEV_HOME。"
else
  target="$bindir/amux"
cat > "$target" <<EOF
#!/bin/sh
# 由 $repo/install-amux.sh 生成,勿手改。
exec uv run --project "$repo" amux "\$@"
EOF
chmod +x "$target"

echo "已安装 $target -> $repo"
fi
case ":$PATH:" in
  *":$bindir:"*) echo "现在任意目录敲 $(basename "$target") 即可(新开的 shell 里生效;当前 shell 先跑一次 hash -r)。" ;;
  *) echo "注意:$bindir 不在 PATH 里,把它加进 shell 配置后才能直接敲 amux。" ;;
esac
