#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SysMonitor — Windows 内存/提交空间(页面文件)监控与报警 · 零第三方依赖。

背景：IntelliJ 系崩溃常见根因是「Windows 系统级 commit（页面文件）耗尽」，
例如 JVM hs_err 日志里：
    Memory: 4k page, system-wide physical 32452M (8394M free)
    TotalPageFile size 40644M (AvailPageFile size 63M)
本工具用与 hs_err 同源的 GlobalMemoryStatusEx 实时采集：
  - 物理内存（total / avail / used）
  - 提交空间 commit（limit / used / avail，即崩溃日志里的 TotalPageFile / AvailPageFile）
  - 内存负载 / 系统运行时长
  - 按「提交大小」排序的内存大户进程（含 vmmem / Docker 等，供定位元凶）
页面端在低于阈值时弹窗 + 系统通知 + 声音报警。

运行:  python sysmon.py [端口] [--no-browser]     默认 8766
访问:  http://127.0.0.1:8766
打包:  pyinstaller SysMonitor.spec
"""
import ctypes
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8766
PROC_CACHE_TTL = 15.0  # 内存大户进程的缓存秒数（PowerShell 慢，低频刷新即可）
IS_WINDOWS = platform.system() == "Windows"


def resource_path(rel):
    """定位随附资源（sysmon.html）。PyInstaller 打包后用 _MEIPASS 临时目录。"""
    base = getattr(sys, "_MEIPASS", BASE_DIR)
    return os.path.join(base, rel)


HTML_PATH = resource_path("sysmon.html")


# ---------------------------------------------------------------------------
# Windows 内存指标 —— GlobalMemoryStatusEx（与 JVM hs_err 日志同源）
# ---------------------------------------------------------------------------

class _MEMORYSTATUSEX(ctypes.Structure):
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


def _pct(part, total):
    if total <= 0:
        return 0.0
    return round(part * 100.0 / total, 1)


def memory_snapshot():
    """返回字节单位的系统内存快照；非 Windows 或调用失败返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        fn = ctypes.windll.kernel32.GlobalMemoryStatusEx
        if not fn(ctypes.byref(status)):
            return None
        total_phys = status.ullTotalPhys
        avail_phys = status.ullAvailPhys
        limit = status.ullTotalPageFile       # commit limit（崩溃日志 TotalPageFile）
        avail = status.ullAvailPageFile       # 剩余 commit（崩溃日志 AvailPageFile）
        return {
            "memoryLoad": int(status.dwMemoryLoad),
            "physical": {
                "total": total_phys,
                "avail": avail_phys,
                "used": total_phys - avail_phys,
                "usedPct": _pct(total_phys - avail_phys, total_phys),
            },
            "commit": {
                "total": limit,
                "avail": avail,
                "used": limit - avail,
                "usedPct": _pct(limit - avail, limit),
            },
            "virtual": {
                "total": status.ullTotalVirtual,
                "avail": status.ullAvailVirtual,
                "used": status.ullTotalVirtual - status.ullAvailVirtual,
                "usedPct": _pct(status.ullTotalVirtual - status.ullAvailVirtual,
                                 status.ullTotalVirtual),
            },
        }
    except Exception:
        return None


def uptime_seconds():
    """系统运行时长（GetTickCount64），失败返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        fn = ctypes.windll.kernel32.GetTickCount64
        fn.restype = ctypes.c_ulonglong
        return int(fn() // 1000)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 内存大户进程 —— 按「提交大小(PageFileUsage)」排序（对应任务管理器的“提交大小”）
# ---------------------------------------------------------------------------

_PS_PROBE = (
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


def _kb_to_bytes(v):
    """PageFileUsage / PeakPageFileUsage 单位是 KB。"""
    try:
        return int(v) * 1024 if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _as_is_bytes(v):
    """WorkingSetSize 单位本身就是字节。"""
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _run_proc_probe():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_PROBE],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        data = json.loads(out.stdout)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("procs") or []
    if isinstance(raw, dict):  # 单进程时 PowerShell 可能解包成对象
        raw = [raw]
    top = []
    for p in raw:
        try:
            pid = int(p.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        top.append({
            "pid": pid,
            "name": str(p.get("Name") or "")[:60],
            "commit": _kb_to_bytes(p.get("PageFileUsage")),   # 私有提交大小 (KB)
            "ws": _as_is_bytes(p.get("WorkingSetSize")),      # 工作集/物理内存 (字节)
            "peak": _kb_to_bytes(p.get("PeakPageFileUsage")), # 峰值提交 (KB)
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
    data = _run_proc_probe()
    if data is not None:
        with _proc_lock:
            _proc_cache["data"] = data
            _proc_cache["ts"] = time.monotonic()
    return data


def build_stats():
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


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, content_type):
        data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/sysmon.html", "/index.html"):
            try:
                with open(HTML_PATH, "r", encoding="utf-8") as f:
                    html = f.read()
            except FileNotFoundError:
                html = "<h1>sysmon.html 未找到，请将其放在 sysmon.py 同目录</h1>"
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/api/stats":
            self._send(200, json.dumps(build_stats(), ensure_ascii=False),
                       "application/json; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}),
                       "application/json; charset=utf-8")

    def log_message(self, *args):
        pass


def main():
    port = DEFAULT_PORT
    no_browser = False
    for a in sys.argv[1:]:
        if a == "--no-browser":
            no_browser = True
        elif a.isdigit():
            port = int(a)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"系统内存监控已启动： {url}")
    print("监控 Windows 物理内存 / 提交空间(页面文件) / 内存大户进程")
    print("按 Ctrl+C 停止")
    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
