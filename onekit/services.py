# -*- coding: utf-8 -*-
"""Windows 服务 —— 列表 / 启动 / 停止 / 重启 / 设置启动类型。"""
import json
import re
import threading
import time

from .config import IS_WINDOWS
from .shell import PS_BASE_ARGS, as_list, run, run_ps_json
from .winapi import is_admin

SERVICES_CACHE_TTL = 20.0   # 服务列表缓存秒数（PowerShell 较慢，低频刷新即可）

# 受保护服务：停止/禁用会直接导致系统崩溃，或让本工具依赖的 WMI 失效
PROTECTED_SERVICES = {
    "rpcss", "dcomlaunch", "lsass", "lsaiso", "winlogon", "smss", "csrss",
    "services", "wininit", "winmgmt", "lsm", "samss", "plugplay", "nsi",
}
VALID_START_MODES = {"Automatic", "AutomaticDelayedStart", "Manual", "Disabled"}

# 服务名中禁止出现的 PowerShell 注入字符
_INVALID_NAME_CHARS = re.compile(r"['\";|&`$<>\r\n]")

PS_SERVICES = (
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


def _services_probe():
    data = run_ps_json(PS_SERVICES, timeout=30)
    if not isinstance(data, dict):
        return None
    out = []
    for s in as_list(data.get("services") or []):
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
    data = _services_probe()
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
    if not name or _INVALID_NAME_CHARS.search(name):
        return {"success": False, "error": "无效的服务名", "name": name}
    if name.lower() in PROTECTED_SERVICES:
        return {"success": False,
                "error": "受保护的系统服务，禁止操作（停止可能导致系统崩溃或本工具失效）",
                "name": name}
    if action not in ("start", "stop", "restart", "mode"):
        return {"success": False, "error": "不支持的操作", "name": name}
    if action == "mode" and mode not in VALID_START_MODES:
        return {"success": False, "error": "无效的启动类型：" + str(mode), "name": name}

    res = run(PS_BASE_ARGS + [_ps_service_action(name, action, mode)],
              timeout=45, encoding="utf-8", errors="replace")
    if res is None:
        return {"success": False,
                "error": "操作超时（服务无响应或正被其它进程占用）", "name": name}

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
