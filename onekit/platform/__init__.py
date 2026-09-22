# -*- coding: utf-8 -*-
"""平台抽象层：按 sys.platform 自动选择后端实现。

所有功能模块通过 `from .platform import backend` 获取当前平台的实现，
不再直接 import winapi / shell 等 Windows-only 模块。
"""
import sys

if sys.platform == "win32":
    from .win32 import Win32Backend as _Backend
elif sys.platform == "darwin":
    from .darwin import DarwinBackend as _Backend
else:
    # Linux / 其他 Unix：暂用 darwin 后端（大部分命令通用），后续可拆 linux.py
    from .darwin import DarwinBackend as _Backend

backend = _Backend()

__all__ = ["backend"]
