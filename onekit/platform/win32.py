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

# sc.exe 启动类型参数映射
SC_START_MODES = {
    "Automatic": "auto",
    "AutomaticDelayedStart": "delayed-auto",
    "Manual": "demand",
    "Disabled": "disabled",
}

# Win32 错误码 → (原因, 处理建议)
SVC_ERROR_HINTS = {
    5: ("没有权限打开该服务（Access denied）",
        "确认本工具是以管理员身份运行的（页面徽章应显示「管理员模式」）；"
        "若已提权仍失败，该服务可能被安全软件或自定义 ACL 保护，请用 services.msc 手动停止。"),
    1060: ("服务在系统中已不存在", "刷新列表即可；刚卸载过的服务重启后会消失。"),
    1061: ("服务当前无法接受控制消息，服务控制管理器无法通知它退出",
           "该进程很可能不是真正的 Windows 服务程序（例如用 sc create 直接把 nginx.exe 注册成服务，"
           "而 nginx.exe 并未实现服务控制处理器）。建议改用 nginx -s stop，或直接结束 nginx.exe 进程。"),
    1062: ("服务当前未启动，无需停止", "刷新列表即可。"),
    1072: ("服务已被标记为删除，需重启后才会真正移除", "重启系统即可。"),
    1052: ("该服务的控制请求被拒绝", "稍后重试，或改用 services.msc。"),
    1053: ("服务在规定时间内没有响应控制请求", "进程可能已卡死，可结束对应进程。"),
    1056: ("服务实例已在运行", "刷新列表即可。"),
    1058: ("服务已被禁用，无法启动", "先把启动类型改为「自动」或「手动」再启动。"),
    1059: ("服务配置了循环依赖，无法启动", "检查该服务的依赖配置。"),
    1069: ("服务因登录失败而无法启动", "检查该服务的登录账户与密码。"),
    1077: ("上次启动后服务配置未生效", "重启服务或重启系统。"),
    1079: ("该服务配置的账户与同进程内其它服务不同", "调整服务的登录账户。"),
}
# SCM 明确无法控制进程时，允许回退到终止进程（常见：nginx.exe 这类“假服务”）
FORCE_KILL_CODES = frozenset({5, 1053, 1061})


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

        # ① 首选 PowerShell / ServiceController（可等待状态变化）
        data = self._ps_service_action(name, action, mode)
        if isinstance(data, dict) and data.get("success"):
            data["name"] = name
            self.services_snapshot(force=True)
            return data
        ps_err = (data or {}).get("error") or ""

        # ② 回退 sc.exe：能拿到明确的 Win32 错误码，便于定位真实原因
        ok, code, raw = self._sc_action(name, action, mode)
        if ok:
            self.services_snapshot(force=True)
            return {"success": True, "name": name, "via": "sc.exe"}

        if code:
            reason, hint = SVC_ERROR_HINTS.get(
                code, ("服务控制管理器拒绝了该操作",
                       "可在管理员 PowerShell 中手动执行 sc.exe 核对。"))
            reason = f"{reason}（Win32 错误 {code}）"
        else:
            reason = ps_err[:300] or (raw[:300] if raw else "未知错误")
            hint = "若已用管理员身份运行仍失败，请改用 services.msc 或管理员 PowerShell 手动操作。"

        # ③ 停止失败且 SCM 无法控制该进程（如用 sc create 注册的 nginx.exe），直接结束进程
        if action == "stop" and code in FORCE_KILL_CODES:
            pid = self._service_pid(name)
            if pid and pid not in PROTECTED_PIDS and pid != SELF_PID:
                killed = self.kill_pid(pid)
                self.services_snapshot(force=True)
                if killed.get("success"):
                    return {"success": True, "name": name, "pid": pid, "forced": True,
                            "message": f"服务控制管理器无法控制该进程，已强制结束进程 PID {pid}"}
                hint += " 尝试强制结束进程同样失败：" + str(killed.get("error") or "")
            else:
                hint += " 未取到有效进程 PID，无法强制结束。"

        if not self.is_admin():
            hint = ("当前 OneKit 未以管理员身份运行（页面显示「受限模式」），"
                    "请退出后右键「以管理员身份运行」再试。" + hint)
        return {"success": False, "name": name, "error": reason, "hint": hint,
                "code": code, "ps": ps_err[:300], "admin": self.is_admin()}

    def _ps_service_action(self, name, action, mode=""):
        """通过 PowerShell ServiceController 执行服务操作。"""
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
        return data

    def _sc_once(self, name, action, mode=""):
        """执行单条 sc.exe 命令；返回 (是否成功, Win32 错误码, 原始输出)。"""
        if action == "mode":
            args = ["sc.exe", "config", name, "start=", SC_START_MODES[mode]]
        else:
            args = ["sc.exe", action, name]
        res = _run(args, timeout=45, **PS_GBK)
        if res is None:
            return False, 0, "sc.exe 执行超时"
        raw = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        m = re.search(r"FAILED\s+(\d+)", raw)
        code = int(m.group(1)) if m else 0
        if res.returncode == 0 and code == 0:
            return True, 0, raw
        if code == 0 and res.returncode > 0:
            code = res.returncode
        return False, code, raw

    def _sc_action(self, name, action, mode=""):
        """sc.exe 版服务操作（restart 拆成 stop + start）。"""
        if action == "restart":
            ok, code, raw = self._sc_once(name, "stop")
            if not ok:
                return ok, code, raw
            time.sleep(1.0)
            return self._sc_once(name, "start")
        return self._sc_once(name, action, mode)

    def _service_pid(self, name):
        """读取服务当前进程 PID（sc queryex），失败返回 0。"""
        res = _run(["sc.exe", "queryex", name], timeout=15, **PS_GBK)
        if res is None:
            return 0
        m = re.search(r"PID\s*:\s*(\d+)", res.stdout or "")
        try:
            return int(m.group(1))
        except (AttributeError, TypeError, ValueError):
            return 0

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
