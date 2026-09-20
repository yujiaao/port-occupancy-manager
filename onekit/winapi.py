# -*- coding: utf-8 -*-
"""Windows 原生 API 封装（仅用标准库 ctypes）。

把「怎么调 Win32」和「拿到数据后怎么组装业务结果」分开：
本模块只负责拿到原始值，业务语义放在 memory / disks 等功能域里。
"""
import ctypes
from ctypes import wintypes

from .config import IS_WINDOWS

# 逻辑驱动器类型（GetDriveTypeW 返回值）
DRIVE_TYPES = {
    0: "未知", 1: "无根目录", 2: "可移动", 3: "固定磁盘",
    4: "网络驱动器", 5: "光驱", 6: "RAM 磁盘",
}


class MEMORYSTATUSEX(ctypes.Structure):
    """GlobalMemoryStatusEx 的入参结构体。"""
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),      # 物理内存总量
        ("ullAvailPhys", ctypes.c_ulonglong),      # 物理内存可用
        ("ullTotalPageFile", ctypes.c_ulonglong),  # 提交空间上限 commit limit
        ("ullAvailPageFile", ctypes.c_ulonglong),  # 剩余可提交量（根因指标）
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def pct(part, total):
    """百分比（保留 1 位小数）；分母 <=0 时返回 0.0。"""
    if total <= 0:
        return 0.0
    return round(part * 100.0 / total, 1)


def memory_status():
    """GlobalMemoryStatusEx 原始结构体；非 Windows 或调用失败返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        fn = ctypes.windll.kernel32.GlobalMemoryStatusEx
        if not fn(ctypes.byref(status)):
            return None
        return status
    except Exception:
        return None


def uptime_seconds():
    """系统运行时长（GetTickCount64，秒）；失败返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        fn = ctypes.windll.kernel32.GetTickCount64
        fn.restype = ctypes.c_ulonglong
        return int(fn() // 1000)
    except Exception:
        return None


def is_admin():
    """是否以管理员身份运行（启停服务通常需要管理员）。"""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def logical_drives():
    """逻辑驱动器列表，如 ['C:\\\\', 'D:\\\\']。

    注意：GetLogicalDriveStringsW 返回的是 MULTI_SZ（各盘符以 NUL 分隔、双 NUL 结尾），
    必须用 buf[:] 取完整缓冲区；buf.value 只取到第一个 NUL，会漏掉其余盘符。
    """
    if not IS_WINDOWS:
        return []
    buf = ctypes.create_unicode_buffer(512)
    if not ctypes.windll.kernel32.GetLogicalDriveStringsW(511, buf):
        return []
    return [d for d in buf[:].split("\x00") if d]


def volume_info(drive):
    """查询单个驱动器，返回 (ready, dtype, label, fs, total, free)。"""
    k32 = ctypes.windll.kernel32
    free_caller = ctypes.c_ulonglong()
    total = ctypes.c_ulonglong()
    free = ctypes.c_ulonglong()
    # 未就绪的盘（空光驱、离线网络盘）查询会失败：调用方仍会列出，只是不显示容量
    ready = bool(k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(drive), ctypes.byref(free_caller),
                                         ctypes.byref(total), ctypes.byref(free)))
    dtype = k32.GetDriveTypeW(ctypes.c_wchar_p(drive))
    label = ctypes.create_unicode_buffer(256)
    fs = ctypes.create_unicode_buffer(256)
    ok = k32.GetVolumeInformationW(ctypes.c_wchar_p(drive), label, 255,
                                   None, None, None, fs, 255)
    return (ready, dtype, label.value if ok else "", fs.value if ok else "",
            total.value, free.value)
