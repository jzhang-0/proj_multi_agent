#!/bin/bash
# 把 amux 装成全局命令(在 ~/.local/bin 放一个薄 shim,指回本仓库)。
# 用法: ./install-amux.sh            安装 / 覆盖安装
#       ./install-amux.sh uninstall  卸载
#
# 为什么是 shim 而不是 `uv tool install`:后者会把 pyproject 里的 console 和
# roster 也一并塞进全局 PATH,console 这种通用词占全局命名空间正是要避的;
# shim 只暴露 amux 一个,而且每次都走 `uv run`,依赖始终跟 uv.lock 一致。
set -e
repo="$(cd "$(dirname "$0")" && pwd)"
bindir="${AMUX_BIN_DIR:-$HOME/.local/bin}"
target="$bindir/amux"

if [ "$1" = "uninstall" ]; then
  rm -f "$target"
  echo "已卸载 $target"
  exit 0
fi

mkdir -p "$bindir"
cat > "$target" <<EOF
#!/bin/sh
# 由 $repo/install-amux.sh 生成,勿手改。
exec uv run --project "$repo" amux "\$@"
EOF
chmod +x "$target"

echo "已安装 $target -> $repo"
case ":$PATH:" in
  *":$bindir:"*) echo "现在任意目录敲 amux 即可(新开的 shell 里生效;当前 shell 先跑一次 hash -r)。" ;;
  *) echo "注意:$bindir 不在 PATH 里,把它加进 shell 配置后才能直接敲 amux。" ;;
esac
