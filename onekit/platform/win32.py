# -*- coding: utf-8 -*-
"""Windows 平台后端：封装现有 winapi / shell / ports / services / disks 逻辑。"""
import csv
import io
import json
import os
import re
import signal
import threading
import time

from .base import PlatformBackend
from ..config import KILL_TIMEOUT, PROTECTED_PIDS, SELF_PID


# ── Win32 API 封装（内联，避免循环导入）──────────────────────

import ctypes
from ctypes import wintypes


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


DRIVE_TYPES = {
    0: "未知", 1: "无根目录", 2: "可移动", 3: "固定磁盘",
    4: "网络驱动器", 5: "光驱", 6: "RAM 磁盘",
}

VALID_START_MODES = {"Automatic", "AutomaticDelayedStart", "Manual", "Disabled"}
PROTECTED_SERVICES = {
    "rpcss", "dcomlaunch", "lsass", "lsaiso", "winlogon", "smss", "csrss",
    "services", "wininit", "winmgmt", "lsm", "samss", "plugplay", "nsi",
}
_INVALID_NAME_CHARS = re.compile(r"['\";|&`$<>\r\n]")


def _pct(part, total):
    if total <= 0:
        return 0.0
    return round(part * 100.0 / total, 1)


def _run(args, timeout=20, encoding=None, errors="ignore"):
    import subprocess
    try:
        return subprocess.run(
            args, capture_output=True, text=True,
            encoding=encoding, errors=errors, timeout=timeout,
        )
    except Exception:
        return None


PS_EXE = "powershell"
PS_BASE_ARGS = [PS_EXE, "-NoProfile", "-NonInteractive", "-Command"]


def _run_ps(script, timeout=20):
    return _run(PS_BASE_ARGS + [script], timeout=timeout,
                encoding="utf-8", errors="replace")


def _run_ps_json(script, timeout=20):
    out = _run_ps(script, timeout=timeout)
    if out is None or out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def _as_list(data):
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


PS_GBK = {"encoding": "gbk", "errors": "ignore"}


class Win32Backend(PlatformBackend):
    """Windows 平台实现。"""

    # ── 系统信息 ──────────────────────────────────────────────

    def memory_snapshot(self):
        try:
            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            fn = ctypes.windll.kernel32.GlobalMemoryStatusEx
            if not fn(ctypes.byref(status)):
                return None
        except Exception:
            return None
        total_phys = status.ullTotalPhys
        avail_phys = status.ullAvailPhys
        limit = status.ullTotalPageFile
        avail = status.ullAvailPageFile
        return {
            "memoryLoad": int(status.dwMemoryLoad),
            "physical": {
                "total": total_phys, "avail": avail_phys,
                "used": total_phys - avail_phys,
                "usedPct": _pct(total_phys - avail_phys, total_phys),
            },
            "commit": {
                "total": limit, "avail": avail,
                "used": limit - avail,
                "usedPct": _pct(limit - avail, limit),
            },
            "virtual": {
                "total": status.ullTotalVirtual, "avail": status.ullAvailVirtual,
                "used": status.ullTotalVirtual - status.ullAvailVirtual,
                "usedPct": _pct(status.ullTotalVirtual - status.ullAvailVirtual,
                                status.ullTotalVirtual),
            },
        }

    def uptime_seconds(self):
        try:
            fn = ctypes.windll.kernel32.GetTickCount64
            fn.restype = ctypes.c_ulonglong
            return int(fn() // 1000)
        except Exception:
            return None

    def is_admin(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    # ── 端口与进程 ────────────────────────────────────────────

    def proc_name_map(self):
        out = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=15, **PS_GBK)
        if out is not None:
            mapping = {}
            for line in out.stdout.splitlines():
                row = next(csv.reader(io.StringIO(line)), [])
                if len(row) >= 2:
                    try:
                        mapping[int(row[1])] = row[0]
                    except ValueError:
                        pass
            if mapping:
                return mapping
        out = _run([PS_EXE, "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process | Select-Object ProcessId,Name | ConvertTo-Json -Compress"],
                    timeout=15, encoding="utf-8", errors="ignore")
        if out is None:
            return {}
        try:
            data = json.loads(out.stdout)
            if isinstance(data, dict):
                data = [data]
            return {int(p.get("ProcessId")): p.get("Name", "") or ""
                    for p in data if p.get("ProcessId") is not None}
        except Exception:
            return {}

    def get_connections(self):
        out = _run(["netstat", "-ano"], timeout=20, **PS_GBK)
        if out is None:
            return []
        procs = self.proc_name_map()
        conns = []
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            proto = parts[0]
            if proto not in ("TCP", "UDP"):
                continue
            local = parts[1]
            state = parts[3] if proto == "TCP" else ""
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            conns.append({
                "protocol": proto, "local": local,
                "state": state, "pid": pid,
                "name": procs.get(pid, ""),
            })
        return conns

    def kill_pid(self, pid):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return {"success": False, "error": "无效的 PID", "pid": pid}
        if pid <= 0 or pid in PROTECTED_PIDS or pid == SELF_PID:
            return {"success": False, "error": "受保护的进程，禁止终止", "pid": pid}
        res = _run(["taskkill", "/PID", str(pid), "/F", "/T"],
                   timeout=KILL_TIMEOUT, **PS_GBK)
        if res is None:
            return {"success": False,
                    "error": "终止命令超时（可能被安全软件拦截或进程无响应）", "pid": pid}
        ok = res.returncode == 0
        msg = (res.stdout or res.stderr).strip()
        return {"success": ok, "pid": pid,
                "message": msg or ("已终止" if ok else "taskkill 返回非零")}

    # ── 系统服务 ──────────────────────────────────────────────

    _svc_lock = threading.Lock()
    _svc_cache = {"data": None, "ts": 0.0}
    _SERVICES_CACHE_TTL = 20.0

    PS_SERVICES = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$ErrorActionPreference='SilentlyContinue';"
        "$list=@(Get-CimInstance Win32_Service | "
        "Select-Object Name,DisplayName,State,StartMode,ProcessId | "
        "Sort-Object @{Expression='State';Descending=$true},DisplayName);"
        "@{services=@($list)}|ConvertTo-Json -Compress -Depth 3"
    )

    def services_snapshot(self, force=False):
        now = time.monotonic()
        with self._svc_lock:
            if (not force and self._svc_cache["data"] is not None
                    and now - self._svc_cache["ts"] < self._SERVICES_CACHE_TTL):
                return self._svc_cache["data"]
        data = self._services_probe()
        if data is not None:
            with self._svc_lock:
                self._svc_cache["data"] = data
                self._svc_cache["ts"] = time.monotonic()
        return data

    def _services_probe(self):
        data = _run_ps_json(self.PS_SERVICES, timeout=30)
        if not isinstance(data, dict):
            return None
        out = []
        for s in _as_list(data.get("services") or []):
            name = str(s.get("Name") or "")
            if not name:
                continue
            try:
                pid = int(s.get("ProcessId") or 0)
            except (TypeError, ValueError):
                pid = 0
            out.append({
                "name": name,
                "display": str(s.get("DisplayName") or name),
                "state": str(s.get("State") or ""),
                "mode": str(s.get("StartMode") or ""),
                "pid": pid,
                "protected": name.lower() in PROTECTED_SERVICES,
            })
        if not out:
            return None
        return {"services": out, "admin": self.is_admin()}

    def service_action(self, name, action, mode=""):
        name = (name or "").strip()
        if not name or _INVALID_NAME_CHARS.search(name):
            return {"success": False, "error": "无效的服务名", "name": name}
        if name.lower() in PROTECTED_SERVICES:
            return {"success": False,
                    "error": "受保护的系统服务，禁止操作", "name": name}
        if action not in ("start", "stop", "restart", "mode"):
            return {"success": False, "error": "不支持的操作", "name": name}
        if action == "mode" and mode not in VALID_START_MODES:
            return {"success": False, "error": "无效的启动类型：" + str(mode), "name": name}

        safe = name.replace("'", "''")
        script = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "$ErrorActionPreference='Stop';"
            "$name='{n}';"
            "try{{"
            "  if('{a}' -eq 'start'){{Start-Service -Name $name -ErrorAction Stop}}"
            "  elseif('{a}' -eq 'stop'){{Stop-Service -Name $name -Force -ErrorAction Stop}}"
            "  elif('{a}' -eq 'restart'){{Restart-Service -Name $name -Force -ErrorAction Stop}}"
            "  elif('{a}' -eq 'mode'){{Set-Service -Name $name -StartupType '{m}' -ErrorAction Stop}};"
            "  $s=Get-CimInstance Win32_Service -Filter \"Name='$name'\";"
            "  @{{success=$true;state=[string]$s.State;mode=[string]$s.StartMode;"
            "pid=[int]$s.ProcessId}}|ConvertTo-Json -Compress"
            "}}catch{{"
            "  @{{success=$false;error=$_.Exception.Message}}|ConvertTo-Json -Compress"
            "}}"
        ).format(n=safe, a=action, m=mode)

        res = _run(PS_BASE_ARGS + [script], timeout=45,
                   encoding="utf-8", errors="replace")
        if res is None:
            return {"success": False, "error": "操作超时", "name": name}
        data = None
        if res.stdout.strip():
            try:
                data = json.loads(res.stdout)
            except Exception:
                data = None
        if not isinstance(data, dict):
            msg = (res.stderr or res.stdout or "未知错误").strip()
            return {"success": False, "error": msg[:500] or "未知错误", "name": name}
        data["name"] = name
        if data.get("success"):
            self.services_snapshot(force=True)
        return data

    # ── 磁盘 ──────────────────────────────────────────────────

    def disk_snapshot(self):
        k32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(512)
        if not k32.GetLogicalDriveStringsW(511, buf):
            return None
        drives = [d for d in buf[:].split("\x00") if d]
        out = []
        for drive in drives:
            free_caller = ctypes.c_ulonglong()
            total = ctypes.c_ulonglong()
            free = ctypes.c_ulonglong()
            ready = bool(k32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(drive), ctypes.byref(free_caller),
                ctypes.byref(total), ctypes.byref(free)))
            dtype = k32.GetDriveTypeW(ctypes.c_wchar_p(drive))
            label = ctypes.create_unicode_buffer(256)
            fs = ctypes.create_unicode_buffer(256)
            ok = k32.GetVolumeInformationW(
                ctypes.c_wchar_p(drive), label, 255,
                None, None, None, fs, 255)
            used = max(0, total.value - free.value) if ready else 0
            if not ready:
                total.value = free.value = 0
            out.append({
                "drive": drive,
                "label": label.value if ok else "",
                "fs": fs.value if ok else "",
                "type": DRIVE_TYPES.get(dtype, "未知"),
                "total": total.value, "used": used, "free": free.value,
                "usedPct": _pct(used, total.value),
                "ready": ready,
            })
        return {"disks": out} if out else None

    def clean_catalogs(self):
        env = os.environ
        local = env.get("LOCALAPPDATA", "")
        roaming = env.get("APPDATA", "")
        windir = env.get("windir", r"C:\Windows")
        temp = env.get("TEMP") or (os.path.join(local, "Temp") if local else "")
        return [
            ("user_temp", "用户临时文件", temp, True),
            ("win_temp", "Windows 临时文件", os.path.join(windir, "Temp"), True),
            ("thumbcache", "缩略图缓存", os.path.join(local, "Microsoft", "Windows", "Explorer"), True),
            ("crash_dumps", "崩溃转储文件", os.path.join(local, "CrashDumps"), True),
            ("wer", "Windows 错误报告", os.path.join(local, "Microsoft", "Windows", "WER"), True),
            ("npm_cache", "npm 缓存", os.path.join(roaming, "npm-cache"), True),
            ("pip_cache", "pip 缓存", os.path.join(local, "pip", "cache"), True),
            ("yarn_cache", "Yarn 缓存", os.path.join(local, "Yarn", "Cache"), True),
            ("go_build", "Go 构建缓存", os.path.join(local, "go-build"), True),
            ("prefetch", "Windows 预取文件", os.path.join(windir, "Prefetch"), False),
            ("wu_cache", "Windows Update 缓存", os.path.join(windir, "SoftwareDistribution", "Download"), False),
            ("recycle", "回收站", "", False),
        ]

    def is_protected_path(self, path):
        low = os.path.abspath(path).lower()
        guards = [
            os.environ.get("windir", r"C:\Windows").lower(),
            os.environ.get("ProgramFiles", r"C:\Program Files").lower(),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)").lower(),
        ]
        return any(g and (low == g or low.startswith(g + os.sep)) for g in guards)

    # ── 进程探针 ──────────────────────────────────────────────

    _proc_lock = threading.Lock()
    _proc_cache = {"data": None, "ts": 0.0}
    _PROC_CACHE_TTL = 15.0

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

    def proc_snapshot(self):
        now = time.monotonic()
        with self._proc_lock:
            if (self._proc_cache["data"] is not None
                    and now - self._proc_cache["ts"] < self._PROC_CACHE_TTL):
                return self._proc_cache["data"]
        data = self._proc_probe()
        if data is not None:
            with self._proc_lock:
                self._proc_cache["data"] = data
                self._proc_cache["ts"] = time.monotonic()
        return data

    def _proc_probe(self):
        data = _run_ps_json(self.PS_PROC_PROBE, timeout=20)
        if not isinstance(data, dict):
            return None

        def kb_to_bytes(v):
            try:
                return int(v) * 1024 if v is not None else 0
            except (TypeError, ValueError):
                return 0

        def as_is_bytes(v):
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0

        top = []
        for p in _as_list(data.get("procs") or []):
            try:
                pid = int(p.get("ProcessId"))
            except (TypeError, ValueError):
                continue
            top.append({
                "pid": pid,
                "name": str(p.get("Name") or "")[:60],
                "commit": kb_to_bytes(p.get("PageFileUsage")),
                "ws": as_is_bytes(p.get("WorkingSetSize")),
                "peak": kb_to_bytes(p.get("PeakPageFileUsage")),
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
