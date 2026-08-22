"""总控台命令行入口。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("amux-team")
except PackageNotFoundError:  # 直接把 src/ 放进 PYTHONPATH 时仍给出可诊断版本
    __version__ = "0+unknown"
