#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Port Inspector — 端口占用查看 / 进程终止 / 系统内存监控（零依赖，仅用标准库）。

页面顶部两个 Tab：
  1) 端口占用  —— 查看本机端口占用并按 PID / 端口终止进程
  2) 系统内存监控 —— 监控 Windows 物理内存与提交空间(页面文件 commit)，阈值触发弹窗报警

运行:  python server.py [端口] [--no-browser]   默认 8765
访问:  http://127.0.0.1:8765
打包:  pyinstaller --onefile --add-data "index.html;." server.py
"""
import csv
import ctypes
import io
import json
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SELF_PID = os.getpid()
# Windows 系统关键进程（System / PID 4 等），禁止终止，避免把系统搞崩
PROTECTED_PIDS = {0, 4}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8765
# taskkill 超时：某些安全软件会拦截并挂起该命令，必须设上限，避免 UI 永久卡死
KILL_TIMEOUT = 10


def resource_path(rel):
    """定位随附资源（index.html）。PyInstaller 打包后用 _MEIPASS 临时目录。"""
    base = getattr(sys, "_MEIPASS", BASE_DIR)
    return os.path.join(base, rel)


INDEX_PATH = resource_path("index.html")


def _win_proc_map():
    """返回 {pid: name} 映射，用于补全进程名。优先 tasklist，失败用 PowerShell 兜底。"""
    # 1) tasklist CSV
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=15,
        )
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
    except Exception:
        pass
    # 2) PowerShell 兜底
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object ProcessId,Name | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=15,
        )
        data = json.loads(out.stdout)
        if isinstance(data, dict):
            data = [data]
        mapping = {}
        for p in data:
            try:
                mapping[int(p.get("ProcessId"))] = p.get("Name", "") or ""
            except (TypeError, ValueError):
                pass
        return mapping
    except Exception:
        return {}


def _pid_exists(pid):
    """进程是否存活。无法判断（命令被限制/无输出）时返回 None，由调用方兜底。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=6,
        )
    except Exception:
        return None
    if not out.stdout.strip():
        return None
    return str(pid) in out.stdout


def _local_port(local):
    """从 '0.0.0.0:8080' / '[::]:8080' 中提取端口号（int），失败返回 None。"""
    if not local or ":" not in local:
        return None
    port_part = local.rsplit(":", 1)[1]
    # 去掉 IPv6 可能的括号残留
    port_part = port_part.strip("[]")
    try:
        return int(port_part)
    except ValueError:
        return None


def _windows_connections():
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=20,
        )
    except Exception:
        return []
    procs = _win_proc_map()
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
            "protocol": proto,
            "local": local,
            "state": state,
            "pid": pid,
            "name": procs.get(pid, ""),
        })
    return conns


def _unix_connections():
    try:
        out = subprocess.run(
            ["lsof", "-i", "-P", "-n"],
            capture_output=True, text=True, errors="ignore", timeout=20,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    conns = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        name = " ".join(parts[8:])
        m = re.match(r"(TCP|UDP)\s+([^:]+):(\d+)(?:\s+\((\w+)\))?", name)
        if m:
            proto, addr, port, state = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        else:
            m2 = re.search(r"([\d.]+|\*|\S+):(\d+)", name)
            if not m2:
                continue
            proto = "TCP" if "TCP" in name else "UDP"
            addr, port = m2.group(1), m2.group(2)
            state = ""
        conns.append({
            "protocol": proto,
            "local": f"{addr}:{port}",
            "state": state,
            "pid": pid,
            "name": parts[0],
        })
    return conns


def get_connections():
    if platform.system() == "Windows":
        return _windows_connections()
    return _unix_connections()


def kill_pid(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的 PID", "pid": pid}
    if pid <= 0 or pid in PROTECTED_PIDS or pid == SELF_PID:
        return {"success": False, "error": "受保护的进程，禁止终止", "pid": pid}

    if platform.system() == "Windows":
        exists = _pid_exists(pid)
        if exists is False:
            return {"success": False, "error": f"进程 {pid} 不存在"}
        try:
            res = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=KILL_TIMEOUT,
            )
            ok = res.returncode == 0
            msg = (res.stdout or res.stderr).strip()
            return {"success": ok, "pid": pid, "message": msg or ("已终止" if ok else "taskkill 返回非零")}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "终止命令超时（可能被安全软件拦截或进程无响应）", "pid": pid}
        except Exception as e:
            return {"success": False, "error": f"终止失败：{e}", "pid": pid}

    # Unix
    try:
        os.kill(pid, signal.SIGKILL)
        return {"success": True, "pid": pid, "message": "已发送 SIGKILL"}
    except ProcessLookupError:
        return {"success": False, "error": "进程不存在", "pid": pid}
    except PermissionError:
        return {"success": False, "error": "权限不足，请用管理员/root 权限运行", "pid": pid}


def find_pids_by_port(port):
    """返回占用指定本地端口的所有 PID 集合（去重）。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return set()
    conns = get_connections()
    pids = set()
    for c in conns:
        if _local_port(c.get("local", "")) == port:
            pids.add(c["pid"])
    return pids


def kill_port(port):
    """终止占用指定端口的全部进程，返回汇总结果。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的端口号", "port": port, "pids": []}
    pids = find_pids_by_port(port)
    if not pids:
        return {"success": False, "error": f"端口 {port} 上未发现任何连接", "port": port, "pids": []}
    results = []
    for pid in sorted(pids):
        r = kill_pid(pid)
        r["pid"] = pid
        results.append(r)
    killed = [r["pid"] for r in results if r.get("success")]
    skipped = [r["pid"] for r in results if not r.get("success")]
    return {
        "success": len(killed) > 0,
        "port": port,
        "pids": sorted(pids),
        "results": results,
        "killed": killed,
        "skipped": skipped,
    }


# ===========================================================================
# 系统内存监控 —— 物理内存 / 提交空间(页面文件 commit) / 内存大户进程
# ===========================================================================
# 背景：IntelliJ 系崩溃常见根因是「Windows 系统级 commit（页面文件）耗尽」，例如 JVM
# hs_err 日志里：TotalPageFile size 40644M (AvailPageFile size 63M)。
# 这里用与 hs_err 同源的 GlobalMemoryStatusEx 采集，指标可与崩溃日志逐项对齐。

PROC_CACHE_TTL = 15.0  # 内存大户进程缓存秒数（PowerShell 慢，没必要高频跑）
IS_WINDOWS = platform.system() == "Windows"


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
    """系统内存快照（字节）；非 Windows 或调用失败返回 None。"""
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


# 内存大户进程 —— 按「提交大小(PageFileUsage)」排序（任务管理器里的“提交大小”）
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


def _run_ps_json(script, timeout=20):
    """执行 PowerShell 脚本并把 stdout 解析为 JSON；失败返回 None。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def _run_proc_probe():
    data = _run_ps_json(_PS_PROBE, timeout=20)
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
            "commit": _kb_to_bytes(p.get("PageFileUsage")),    # 私有提交大小 (KB)
            "ws": _as_is_bytes(p.get("WorkingSetSize")),       # 工作集/物理内存 (字节)
            "peak": _kb_to_bytes(p.get("PeakPageFileUsage")),  # 峰值提交 (KB)
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


# ===========================================================================
# 系统服务 —— 列表 / 启动 / 停止 / 设置启动类型
# ===========================================================================

SERVICES_CACHE_TTL = 20.0   # 服务列表缓存秒数（PowerShell 较慢，低频刷新即可）
# 受保护服务：停止/禁用会直接导致系统崩溃，或让本工具依赖的 WMI 失效
PROTECTED_SERVICES = {
    "rpcss", "dcomlaunch", "lsass", "lsaiso", "winlogon", "smss", "csrss",
    "services", "wininit", "winmgmt", "lsm", "samss", "plugplay", "nsi",
}
VALID_START_MODES = {"Automatic", "AutomaticDelayedStart", "Manual", "Disabled"}

_PS_SERVICES = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='SilentlyContinue';"
    "$list=@(Get-CimInstance Win32_Service | "
    "Select-Object Name,DisplayName,State,StartMode,ProcessId | "
    "Sort-Object @{Expression='State';Descending=$true},DisplayName);"
    "@{services=@($list)}|ConvertTo-Json -Compress -Depth 3"
)


def _ps_service_action(name, action, mode=""):
    """生成服务操作脚本：start / stop / restart / mode(改启动类型)。"""
    safe = name.replace("'", "''")   # PowerShell 单引号字符串内转义
    return (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$ErrorActionPreference='Stop';"
        "$name='{n}';"
        "try{{"
        "  if('{a}' -eq 'start'){{Start-Service -Name $name -ErrorAction Stop}}"
        "  elseif('{a}' -eq 'stop'){{Stop-Service -Name $name -Force -ErrorAction Stop}}"
        "  elseif('{a}' -eq 'restart'){{Restart-Service -Name $name -Force -ErrorAction Stop}}"
        "  elseif('{a}' -eq 'mode'){{Set-Service -Name $name -StartupType '{m}' -ErrorAction Stop}};"
        "  $s=Get-CimInstance Win32_Service -Filter \"Name='$name'\";"
        "  @{{success=$true;state=[string]$s.State;mode=[string]$s.StartMode;"
        "pid=[int]$s.ProcessId}}|ConvertTo-Json -Compress"
        "}}catch{{"
        "  @{{success=$false;error=$_.Exception.Message}}|ConvertTo-Json -Compress"
        "}}"
    ).format(n=safe, a=action, m=mode)


def is_admin():
    """是否以管理员身份运行（启停服务通常需要管理员）。"""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_services_probe():
    data = _run_ps_json(_PS_SERVICES, timeout=30)
    if not isinstance(data, dict):
        return None
    raw = data.get("services") or []
    if isinstance(raw, dict):   # 单条时 PowerShell 可能解包成对象
        raw = [raw]
    out = []
    for s in raw:
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
    return {"services": out, "admin": is_admin()}


_svc_lock = threading.Lock()
_svc_cache = {"data": None, "ts": 0.0}


def services_snapshot(force=False):
    """带缓存的服务快照；force=True 用于操作后立刻刷新。"""
    now = time.monotonic()
    with _svc_lock:
        if (not force and _svc_cache["data"] is not None
                and now - _svc_cache["ts"] < SERVICES_CACHE_TTL):
            return _svc_cache["data"]
    data = _run_services_probe()
    if data is not None:
        with _svc_lock:
            _svc_cache["data"] = data
            _svc_cache["ts"] = time.monotonic()
    return data


def service_action(name, action, mode=""):
    """启动 / 停止 / 重启服务，或修改启动类型。"""
    if not IS_WINDOWS:
        return {"success": False, "error": "仅支持 Windows", "name": name}
    name = (name or "").strip()
    # 拒绝含 PowerShell 注入字符的服务名
    if not name or re.search(r"['\";|&`$<>\r\n]", name):
        return {"success": False, "error": "无效的服务名", "name": name}
    if name.lower() in PROTECTED_SERVICES:
        return {"success": False, "error": "受保护的系统服务，禁止操作（停止可能导致系统崩溃或本工具失效）", "name": name}
    if action not in ("start", "stop", "restart", "mode"):
        return {"success": False, "error": "不支持的操作", "name": name}
    if action == "mode" and mode not in VALID_START_MODES:
        return {"success": False, "error": "无效的启动类型：" + str(mode), "name": name}

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _ps_service_action(name, action, mode)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "操作超时（服务无响应或正被其它进程占用）", "name": name}
    except Exception as e:
        return {"success": False, "error": f"执行失败：{e}", "name": name}

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
        services_snapshot(force=True)   # 操作完成后立刻刷新缓存
    return data


# ===========================================================================
# 磁盘 —— 分区容量 / 垃圾扫描清理 / 大文件扫描
# ===========================================================================

# 清理类别：(id, 显示名, 路径, 默认是否勾选)。全部为可安全删除的缓存/临时目录。
def _clean_catalogs():
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


_DRIVE_TYPES = {0: "未知", 1: "无根目录", 2: "可移动", 3: "固定磁盘",
                4: "网络驱动器", 5: "光驱", 6: "RAM 磁盘"}


def _drives():
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


def disk_snapshot():
    """各分区容量（GetDiskFreeSpaceExW / GetVolumeInformationW）。"""
    if not IS_WINDOWS:
        return None
    k32 = ctypes.windll.kernel32
    out = []
    for d in _drives():
        free_caller = ctypes.c_ulonglong()
        total = ctypes.c_ulonglong()
        free = ctypes.c_ulonglong()
        # 未就绪的盘（空光驱、离线网络盘）查询会失败：仍列出，只是不显示容量
        ready = bool(k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(d), ctypes.byref(free_caller),
                                             ctypes.byref(total), ctypes.byref(free)))
        dtype = k32.GetDriveTypeW(ctypes.c_wchar_p(d))
        label = ctypes.create_unicode_buffer(256)
        fs = ctypes.create_unicode_buffer(256)
        ok = k32.GetVolumeInformationW(ctypes.c_wchar_p(d), label, 255,
                                       None, None, None, fs, 255)
        if ready:
            total_v, free_v = total.value, free.value
            used = max(0, total_v - free_v)
        else:
            total_v = free_v = used = 0
        out.append({
            "drive": d,
            "label": label.value if ok else "",
            "fs": fs.value if ok else "",
            "type": _DRIVE_TYPES.get(dtype, "未知"),
            "total": total_v, "used": used, "free": free_v,
            "usedPct": _pct(used, total_v),
            "ready": ready,
        })
    return {"disks": out} if out else None


def _recycle_dirs():
    dirs = []
    for d in _drives():
        p = os.path.join(d, "$RECYCLE.BIN")
        try:
            if os.path.isdir(p):
                dirs.append(p)
        except OSError:
            pass
    return dirs


def _catalog_paths(cid):
    """返回该类别对应的真实目录列表（回收站为各盘 $RECYCLE.BIN）。"""
    if cid == "recycle":
        return _recycle_dirs()
    for i, _n, path, _r in _clean_catalogs():
        if i == cid:
            return [path] if path else []
    return []


def _walk_stats(path, deadline, max_items=40000):
    """统计目录总大小与文件数；无权限的文件跳过。"""
    total = count = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            count += 1
            if count >= max_items or time.time() > deadline:
                return total, count
    return total, count


def clean_scan(time_budget=18.0):
    """扫描各清理类别占用；受总时间预算约束，避免卡住请求。"""
    deadline = time.time() + time_budget
    items = []
    for cid, name, path, rec in _clean_catalogs():
        paths = [p for p in _catalog_paths(cid) if p and os.path.isdir(p)]
        size = files = 0
        for p in paths:
            s, c = _walk_stats(p, deadline)
            size += s
            files += c
        items.append({
            "id": cid, "name": name,
            "path": " ; ".join(paths) if paths else (path or "—"),
            "size": size, "files": files,
            "exists": bool(paths), "recommended": rec,
        })
        if time.time() > deadline:
            break
    return {"items": items, "truncated": time.time() > deadline}


def _purge_dir(path, deadline):
    """清空目录内容（文件 + 空子目录），保留目录本身。"""
    removed = freed = failed = 0
    for root, dirs, files in os.walk(path, topdown=False, onerror=lambda e: None):
        for f in files:
            if time.time() > deadline:
                return removed, freed, failed
            fp = os.path.join(root, f)
            try:
                size = os.path.getsize(fp)
                os.remove(fp)
                freed += size
                removed += 1
            except OSError:
                failed += 1
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))   # 只删已空的目录
            except OSError:
                pass
    return removed, freed, failed


def clean_run(ids, time_budget=90.0):
    """清理指定类别；被占用/无权限的文件计入 failed。"""
    deadline = time.time() + time_budget
    details = []
    removed = freed = failed = 0
    for cid in ids:
        s_removed = s_freed = s_failed = 0
        for p in _catalog_paths(cid):
            if not p or not os.path.isdir(p) or time.time() > deadline:
                continue
            r, f, e = _purge_dir(p, deadline)
            s_removed += r
            s_freed += f
            s_failed += e
        name = next((n for i, n, _p, _r in _clean_catalogs() if i == cid), cid)
        details.append({"id": cid, "name": name, "removed": s_removed,
                        "freed": s_freed, "failed": s_failed})
        removed += s_removed
        freed += s_freed
        failed += s_failed
    return {"success": removed > 0, "removed": removed, "freed": freed,
            "failed": failed, "details": details}


def scan_big_files(root, min_mb=100, limit=50, time_limit=25.0):
    """扫描大文件（只读），返回 Top N。"""
    if not root or not os.path.isdir(root):
        return {"files": [], "scanned": 0, "error": "路径不存在或不可访问"}
    min_bytes = max(1, int(min_mb)) * 1024 * 1024
    found = []
    scanned = 0
    end = time.time() + time_limit
    for cur, _dirs, files in os.walk(root, onerror=lambda e: None):
        for f in files:
            scanned += 1
            if scanned % 2000 == 0 and time.time() > end:
                found.sort(key=lambda x: -x["size"])
                return {"files": found[:limit], "scanned": scanned, "truncated": True}
            p = os.path.join(cur, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size >= min_bytes:
                found.append({"path": p, "size": st.st_size, "mtime": int(st.st_mtime)})
    found.sort(key=lambda x: -x["size"])
    return {"files": found[:limit], "scanned": scanned, "truncated": False}


def _is_protected_path(path):
    """系统目录（Windows / Program Files）下的文件禁止删除。"""
    low = os.path.abspath(path).lower()
    guards = [
        os.environ.get("windir", r"C:\Windows").lower(),
        os.environ.get("ProgramFiles", r"C:\Program Files").lower(),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)").lower(),
    ]
    return any(g and (low == g or low.startswith(g + os.sep)) for g in guards)


def delete_paths(paths):
    """删除指定文件（大文件清理）；受保护目录拒绝删除。"""
    removed = freed = failed = 0
    details = []
    for p in paths or []:
        try:
            if not os.path.isfile(p):
                failed += 1
                details.append({"path": p, "ok": False, "error": "不是文件或不存在"})
                continue
            if _is_protected_path(p):
                failed += 1
                details.append({"path": p, "ok": False, "error": "受保护的系统目录，拒绝删除"})
                continue
            size = os.path.getsize(p)
            os.remove(p)
            freed += size
            removed += 1
            details.append({"path": p, "ok": True, "size": size})
        except Exception as e:
            failed += 1
            details.append({"path": p, "ok": False, "error": str(e)})
    return {"success": removed > 0, "removed": removed, "freed": freed,
            "failed": failed, "details": details}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "r", encoding="utf-8") as f:
                    html = f.read()
            except FileNotFoundError:
                html = "<h1>index.html 未找到，请将其放在 server.py 同目录</h1>"
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/api/ports":
            conns = get_connections()
            self._json(200, {"ports": conns, "count": len(conns)})
        elif path == "/api/stats":
            self._json(200, build_stats())
        elif path == "/api/services":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            force = qs.get("force", [""])[0] == "1"
            snap = services_snapshot(force=force)
            if snap is None:
                self._json(200, {
                    "ok": False, "services": [], "admin": is_admin(),
                    "error": "无法获取服务列表（PowerShell 被禁用或非 Windows）",
                })
            else:
                self._json(200, dict(ok=True, **snap))
        elif path == "/api/disks":
            snap = disk_snapshot()
            if snap is None:
                self._json(200, {"ok": False, "disks": [], "error": "无法获取磁盘信息（仅支持 Windows）"})
            else:
                self._json(200, dict(ok=True, **snap))
        else:
            self._json(404, {"error": "not found"})

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return None

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self._read_json_body()
        if data is None:
            self._json(400, {"success": False, "error": "无效的请求体"})
            return
        if path in ("/api/kill", "/api/kill_port"):
            try:
                if path == "/api/kill":
                    result = kill_pid(data.get("pid"))
                else:
                    result = kill_port(data.get("port"))
            except Exception as e:
                result = {"success": False, "error": f"服务器异常：{e}"}
            self._json(200, result)
        elif path == "/api/service":
            try:
                result = service_action(data.get("name"), data.get("action"),
                                        data.get("mode") or "")
            except Exception as e:
                result = {"success": False, "error": f"服务器异常：{e}"}
            self._json(200, result)
        elif path == "/api/clean_scan":
            try:
                result = clean_scan()
            except Exception as e:
                result = {"items": [], "error": f"扫描失败：{e}"}
            self._json(200, result)
        elif path == "/api/clean":
            try:
                result = clean_run(data.get("ids") or [])
            except Exception as e:
                result = {"success": False, "error": f"清理失败：{e}"}
            self._json(200, result)
        elif path == "/api/bigfiles":
            try:
                result = scan_big_files(data.get("path") or "",
                                        data.get("min_mb") or 100,
                                        int(data.get("limit") or 50))
            except Exception as e:
                result = {"files": [], "error": f"扫描失败：{e}"}
            self._json(200, result)
        elif path == "/api/delete_paths":
            try:
                result = delete_paths(data.get("paths") or [])
            except Exception as e:
                result = {"success": False, "error": f"删除失败：{e}"}
            self._json(200, result)
        else:
            self._json(404, {"error": "not found"})

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
    # 多线程：进程快照（PowerShell）较慢时不会阻塞端口查询等其它请求
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"PortInspector 已启动： {url}")
    print("功能：端口占用查看 / 系统内存监控（页面顶部 Tab 切换）")
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
