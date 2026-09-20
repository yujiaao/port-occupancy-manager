# -*- coding: utf-8 -*-
"""系统内存监控 —— 物理内存 / 提交空间(页面文件 commit) / 内存大户进程。

背景：IntelliJ 系崩溃常见根因是「Windows 系统级 commit（页面文件）耗尽」，例如 JVM
hs_err 日志里：TotalPageFile size 40644M (AvailPageFile size 63M)。
这里用与 hs_err 同源的 GlobalMemoryStatusEx 采集，指标可与崩溃日志逐项对齐。
"""
import platform
import threading
import time

from .shell import as_list, run_ps_json
from .winapi import memory_status, pct, uptime_seconds

PROC_CACHE_TTL = 15.0  # 内存大户进程缓存秒数（PowerShell 慢，没必要高频跑）

# 内存大户进程 —— 按「提交大小(PageFileUsage)」排序（任务管理器里的"提交大小"）
PS_PROC_PROBE = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='SilentlyContinue';"
    "$procs=@(Get-CimInstance Win32_Process | "
    "Select-Object ProcessId,Name,WorkingSetSize,PageFileUsage,PeakPageFileUsage | "
    "Sort-Object PageFileUsage -Descending | Select-Object -First 20);"
    "$cs=Get-CimInstance Win32_ComputerSystem;"
    "$os=Get-CimInstance Win32_OperatingSystem;"
    "$boot='';try{$boot=$os.LastBootUpTime.ToString('o')}catch{};"
    "@{procs=@($procs);hypervisorPresent=[bool]$cs.HypervisorPresent;"
    "osCaption=[string]$os.Caption;osVersion=[string]$os.Version;bootTime=$boot}"
    "|ConvertTo-Json -Compress -Depth 3"
)


def kb_to_bytes(v):
    """PageFileUsage / PeakPageFileUsage 单位是 KB。"""
    try:
        return int(v) * 1024 if v is not None else 0
    except (TypeError, ValueError):
        return 0


def as_is_bytes(v):
    """WorkingSetSize 单位本身就是字节。"""
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _proc_probe():
    """执行 PowerShell 进程探针并归一化结果；失败返回 None。"""
    data = run_ps_json(PS_PROC_PROBE, timeout=20)
    if not isinstance(data, dict):
        return None
    top = []
    for p in as_list(data.get("procs") or []):
        try:
            pid = int(p.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        top.append({
            "pid": pid,
            "name": str(p.get("Name") or "")[:60],
            "commit": kb_to_bytes(p.get("PageFileUsage")),    # 私有提交大小 (KB)
            "ws": as_is_bytes(p.get("WorkingSetSize")),       # 工作集/物理内存 (字节)
            "peak": kb_to_bytes(p.get("PeakPageFileUsage")),  # 峰值提交 (KB)
        })
    top.sort(key=lambda x: -x["commit"])
    return {
        "top": top[:20],
        "os": {
            "caption": str(data.get("osCaption") or ""),
            "version": str(data.get("osVersion") or ""),
            "hypervisorPresent": bool(data.get("hypervisorPresent")),
            "bootTime": str(data.get("bootTime") or ""),
        },
    }


_proc_lock = threading.Lock()
_proc_cache = {"data": None, "ts": 0.0}


def proc_snapshot():
    """带缓存的进程快照；PowerShell 较慢，成功结果缓存 PROC_CACHE_TTL 秒。"""
    now = time.monotonic()
    with _proc_lock:
        if _proc_cache["data"] is not None and now - _proc_cache["ts"] < PROC_CACHE_TTL:
            return _proc_cache["data"]
    data = _proc_probe()
    if data is not None:
        with _proc_lock:
            _proc_cache["data"] = data
            _proc_cache["ts"] = time.monotonic()
    return data


def memory_snapshot():
    """系统内存快照（字节）；非 Windows 或调用失败返回 None。"""
    st = memory_status()
    if st is None:
        return None
    total_phys = st.ullTotalPhys
    avail_phys = st.ullAvailPhys
    limit = st.ullTotalPageFile       # commit limit（崩溃日志 TotalPageFile）
    avail = st.ullAvailPageFile       # 剩余 commit（崩溃日志 AvailPageFile）
    return {
        "memoryLoad": int(st.dwMemoryLoad),
        "physical": {
            "total": total_phys, "avail": avail_phys,
            "used": total_phys - avail_phys,
            "usedPct": pct(total_phys - avail_phys, total_phys),
        },
        "commit": {
            "total": limit, "avail": avail,
            "used": limit - avail,
            "usedPct": pct(limit - avail, limit),
        },
        "virtual": {
            "total": st.ullTotalVirtual, "avail": st.ullAvailVirtual,
            "used": st.ullTotalVirtual - st.ullAvailVirtual,
            "usedPct": pct(st.ullTotalVirtual - st.ullAvailVirtual, st.ullTotalVirtual),
        },
    }


def build_stats():
    """汇总给 /api/stats 的完整负载：内存指标 + 运行时长 + 内存大户 Top 20。"""
    mem = memory_snapshot()
    if mem is None:
        return {
            "ok": False,
            "reason": "仅支持 Windows（GlobalMemoryStatusEx 不可用）",
            "platform": platform.system(),
            "ts": int(time.time() * 1000),
        }
    stats = {
        "ok": True,
        "ts": int(time.time() * 1000),
        "uptimeSec": uptime_seconds(),
    }
    stats.update(mem)
    ps = proc_snapshot()
    stats["top"] = ps["top"] if ps else None
    stats["os"] = ps["os"] if ps else None
    return stats
