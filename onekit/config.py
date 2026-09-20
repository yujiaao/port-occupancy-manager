# -*- coding: utf-8 -*-
"""全局配置：路径定位与常量。"""
import os
import platform
import sys

SELF_PID = os.getpid()

# Windows 系统关键进程（System / PID 4 等），禁止终止，避免把系统搞崩
PROTECTED_PIDS = {0, 4}

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
# 源码运行时 BASE_DIR 即项目根目录（onekit/ 的上一级）
BASE_DIR = os.path.dirname(PKG_DIR)

DEFAULT_PORT = 8765
# taskkill 超时：某些安全软件会拦截并挂起该命令，必须设上限，避免 UI 永久卡死
KILL_TIMEOUT = 10

IS_WINDOWS = platform.system() == "Windows"


def resource_path(rel):
    """定位随附资源（index.html / static）。PyInstaller 打包后用 _MEIPASS 临时目录。"""
    base = getattr(sys, "_MEIPASS", BASE_DIR)
    return os.path.join(base, rel)


INDEX_PATH = resource_path("index.html")
STATIC_DIR = resource_path("static")
