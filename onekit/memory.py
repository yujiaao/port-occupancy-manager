# -*- coding: utf-8 -*-
"""系统内存监控 —— 通过平台抽象层获取内存 / 运行时长 / 进程快照。"""
import platform
import time

from .platform import backend


def memory_snapshot():
    """系统内存快照（字节）；不支持的平台返回 None。"""
    return backend.memory_snapshot()


def build_stats():
    """汇总给 /api/stats 的完整负载：内存指标 + 运行时长 + 内存大户 Top 20。"""
    mem = backend.memory_snapshot()
    if mem is None:
        return {
            "ok": False,
            "reason": f"当前平台 {platform.system()} 暂不支持内存监控",
            "platform": platform.system(),
            "ts": int(time.time() * 1000),
        }
    stats = {
        "ok": True,
        "ts": int(time.time() * 1000),
        "uptimeSec": backend.uptime_seconds(),
    }
    stats.update(mem)
    ps = backend.proc_snapshot()
    stats["top"] = ps["top"] if ps else None
    stats["os"] = ps["os"] if ps else None
    return stats
