# -*- coding: utf-8 -*-
"""外部命令 / PowerShell 调用封装。

统一处理超时与编码，失败一律返回 None，由调用方决定兜底行为，
避免每个功能模块都写一遍 try/except subprocess。
"""
import json
import subprocess

PS_EXE = "powershell"
# 完整的 PowerShell 调用前缀（含可执行文件），各模块可直接与脚本拼接后交给 run()/run_ps()
PS_BASE_ARGS = [PS_EXE, "-NoProfile", "-NonInteractive", "-Command"]


def run(args, timeout=20, encoding=None, errors="ignore"):
    """执行外部命令；超时 / 异常返回 None。

    encoding=None 时沿用系统默认编码（如 lsof 这类本机命令）。
    """
    try:
        return subprocess.run(
            args, capture_output=True, text=True,
            encoding=encoding, errors=errors, timeout=timeout,
        )
    except Exception:
        return None


def run_ps(script, timeout=20):
    """执行 PowerShell 脚本，返回 CompletedProcess 或 None。"""
    return run(PS_BASE_ARGS + [script], timeout=timeout,
               encoding="utf-8", errors="replace")


def run_ps_json(script, timeout=20):
    """执行 PowerShell 脚本并把 stdout 解析为 JSON；失败返回 None。"""
    out = run_ps(script, timeout=timeout)
    if out is None or out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def as_list(data):
    """PowerShell 只返回一条记录时会解包成对象，这里统一成 list。"""
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []
