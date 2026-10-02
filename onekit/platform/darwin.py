# -*- coding: utf-8 -*-
"""macOS / Linux 平台后端：用标准命令替代 Windows API。

覆盖范围：
- 内存：vm_stat + sysctl（macOS）/ /proc/meminfo（Linux）
- 端口：lsof -i -P -n
- 进程终止：SIGKILL
- 服务：launchctl（macOS）/ systemctl（Linux）
- 磁盘：os.statvfs + /Volumes（macOS）或 df（Linux）
- 清理类别：~/Library/Caches、/tmp 等
"""
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time

from .base import PlatformBackend
from ..config import PROTECTED_PIDS, SELF_PID


def _run(args, timeout=20, encoding=None, errors="replace"):
    try:
        return subprocess.run(
            args, capture_output=True, text=True,
            encoding=encoding, errors=errors, timeout=timeout,
        )
    except Exception:
        return None


_IS_MACOS = sys.platform == "darwin"

# 结束这些进程会直接让图形会话或本工具失效
_CRITICAL_PROCS = {"launchd", "kernel_task", "WindowServer", "loginwindow"}
_MAC_PROTECTED_SERVICES = {
    "com.apple.WindowServer",
    "com.apple.loginwindow",
    "com.apple.Dock",
    "com.apple.SystemUIServer",
    "com.apple.Finder",
    "com.apple.coreservicesd",
    "com.apple.cfprefsd",
}
# df 里这些挂载点是系统内部卷，不作为用户磁盘展示
_SKIP_MOUNTS = {
    "/dev",
    "/System/Volumes/Preboot",
    "/System/Volumes/VM",
    "/System/Volumes/Update",
    "/System/Volumes/iSCPreboot",
    "/System/Volumes/xarts",
    "/System/Volumes/Hardware",
}


def _page_size():
    out = _run(["sysctl", "-n", "hw.pagesize"], timeout=5)
    if out and out.stdout.strip().isdigit():
        return int(out.stdout.strip())
    return 4096


def _parse_swap_field(text, key):
    m = re.search(rf"{key}\s*=\s*([\d.]+)\s*(\w+)", text or "")
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).lower()
    mult = {"g": 1 << 30, "m": 1 << 20, "k": 1 << 10}.get(unit[:1], 1)
    return int(val * mult)


class DarwinBackend(PlatformBackend):
    """macOS / Linux 平台实现。"""

    # ── 系统信息 ──────────────────────────────────────────────

    def memory_snapshot(self):
        if _IS_MACOS:
            return self._macos_memory()
        return self._linux_memory()

    def _macos_memory(self):
        """macOS: vm_stat + sysctl。页面大小随架构变化（Intel 4K / Apple 芯片 16K）。"""
        page_size = _page_size()
        out = _run(["sysctl", "-n", "hw.memsize"], timeout=5)
        if out is None or not out.stdout.strip():
            return None
        try:
            total_phys = int(out.stdout.strip())
        except ValueError:
            return None

        out = _run(["vm_stat"], timeout=5)
        if out is None:
            return None
        stats = {}
        for line in out.stdout.splitlines():
            m = re.match(r"^(.+?):\s+([\d]+)", line)
            if m:
                stats[m.group(1).strip()] = int(m.group(2))

        def pages(*names):
            return sum(stats.get(n, 0) for n in names) * page_size

        # 与活动监视器「可用内存」接近：空闲 + 非活跃 + 投机 + 可清除
        avail_phys = pages("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")
        avail_phys = min(avail_phys, total_phys)
        used_phys = max(0, total_phys - avail_phys)

        swap_out = _run(["sysctl", "-n", "vm.swapusage"], timeout=5)
        swap_text = swap_out.stdout if swap_out else ""
        swap_total = _parse_swap_field(swap_text, "total")
        swap_avail = _parse_swap_field(swap_text, "free")
        if swap_avail > swap_total:
            swap_avail = swap_total

        def pct(p, t):
            return round(p * 100.0 / t, 1) if t > 0 else 0.0

        return {
            "profile": "posix",
            "platform": "Darwin",
            "memoryLoad": round(used_phys * 100 / total_phys, 1) if total_phys else 0,
            "physical": {
                "total": total_phys, "avail": avail_phys,
                "used": used_phys, "usedPct": pct(used_phys, total_phys),
            },
            "commit": {
                "total": swap_total, "avail": swap_avail,
                "used": max(0, swap_total - swap_avail),
                "usedPct": pct(swap_total - swap_avail, swap_total),
            },
            "virtual": {
                "total": total_phys, "avail": avail_phys,
                "used": used_phys, "usedPct": pct(used_phys, total_phys),
            },
        }

    def _linux_memory(self):
        """Linux: /proc/meminfo。"""
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
        except OSError:
            return None
        info = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                val = int(parts[1]) * 1024  # kB → bytes
                info[key] = val

        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = max(0, total - avail)
        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)

        def pct(p, t):
            return round(p * 100.0 / t, 1) if t > 0 else 0.0

        return {
            "profile": "posix",
            "platform": "Linux",
            "memoryLoad": pct(used, total),
            "physical": {
                "total": total, "avail": avail,
                "used": used, "usedPct": pct(used, total),
            },
            "commit": {
                "total": swap_total, "avail": swap_free,
                "used": max(0, swap_total - swap_free),
                "usedPct": pct(swap_total - swap_free, swap_total),
            },
            "virtual": {
                "total": total, "avail": avail,
                "used": used, "usedPct": pct(used, total),
            },
        }

    def uptime_seconds(self):
        if _IS_MACOS:
            out = _run(["sysctl", "-n", "kern.boottime"], timeout=5)
            if out and out.stdout:
                m = re.search(r"sec\s*=\s*(\d+)", out.stdout)
                if m:
                    return int(time.time()) - int(m.group(1))
        else:
            try:
                with open("/proc/uptime", "r") as f:
                    return int(float(f.read().split()[0]))
            except Exception:
                pass
        return None

    def is_admin(self):
        return os.getuid() == 0

    # ── 端口与进程 ────────────────────────────────────────────

    def proc_name_map(self):
        out = _run(["ps", "-eo", "pid,comm"], timeout=10)
        if out is None:
            return {}
        mapping = {}
        for line in out.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                try:
                    mapping[int(parts[0])] = parts[1]
                except ValueError:
                    pass
        return mapping

    def get_connections(self):
        """lsof -nP -i。macOS 上协议在 NODE 列，地址在 NAME 列（形如 *:8765 或 127.0.0.1:1->1.2.3.4:443）。"""
        out = _run(["lsof", "-nP", "-i"], timeout=20)
        if out is None or not (out.stdout or "").strip():
            return []
        conns = []
        for line in out.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 9:
                continue
            proto = parts[7] if parts[7] in ("TCP", "UDP") else ""
            if not proto:
                for p in parts:
                    if p in ("TCP", "UDP"):
                        proto = p
                        break
            if not proto:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            endpoint = parts[8]
            state = parts[9].strip("()") if len(parts) >= 10 and parts[9].startswith("(") else ""
            local = endpoint.split("->", 1)[0]
            conns.append({
                "protocol": proto,
                "local": local,
                "state": state,
                "pid": pid,
                "name": parts[0],
            })
        return conns

    def _child_pids(self, pid, depth=0):
        if depth > 6:
            return []
        out = _run(["pgrep", "-P", str(pid)], timeout=5)
        kids = []
        if out is None or not out.stdout:
            return kids
        for line in out.stdout.splitlines():
            try:
                child = int(line.strip())
            except ValueError:
                continue
            kids.append(child)
            kids.extend(self._child_pids(child, depth + 1))
        return kids

    def kill_pid(self, pid):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return {"success": False, "error": "无效的 PID", "pid": pid}
        if pid <= 1 or pid in PROTECTED_PIDS or pid == SELF_PID:
            return {"success": False, "error": "受保护的进程，禁止终止", "pid": pid}
        comm = os.path.basename(self.proc_name_map().get(pid, ""))
        if comm in _CRITICAL_PROCS:
            return {"success": False, "error": f"系统关键进程 {comm}，禁止终止", "pid": pid}
        targets = []
        for child in self._child_pids(pid):
            if child not in targets and child != SELF_PID and child > 1:
                targets.append(child)
        targets.append(pid)
        killed = []
        denied = False
        for target in targets:
            try:
                os.kill(target, signal.SIGKILL)
                killed.append(target)
            except ProcessLookupError:
                continue
            except PermissionError:
                denied = True
        if killed:
            extra = f"（含子进程 {len(killed) - (1 if pid in killed else 0)} 个）" if len(killed) > 1 else ""
            return {"success": True, "pid": pid, "message": "已发送 SIGKILL" + extra, "killed": killed}
        if denied:
            return {"success": False, "error": "权限不足。结束其他用户的进程需要用 sudo 启动 OneKit", "pid": pid}
        return {"success": False, "error": "进程不存在", "pid": pid}

    # ── 系统服务 ──────────────────────────────────────────────

    _svc_lock = threading.Lock()
    _svc_cache = {"data": None, "ts": 0.0}
    _SERVICES_CACHE_TTL = 20.0

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
        if _IS_MACOS:
            return self._macos_services()
        return self._linux_services()

    def _macos_services(self):
        """macOS: launchctl list。"""
        out = _run(["launchctl", "list"], timeout=15)
        if out is None:
            return None
        services = []
        for line in out.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pid_str, exit_str, label = parts[0], parts[1], parts[2]
            try:
                pid = int(pid_str) if pid_str != "-" else 0
            except ValueError:
                pid = 0
            state = "Running" if pid > 0 else "Stopped"
            services.append({
                "name": label,
                "display": label,
                "state": state,
                "mode": "Auto",
                "pid": pid,
                "protected": label in _MAC_PROTECTED_SERVICES,
            })
        if not services:
            return None
        return {"services": services, "admin": self.is_admin(), "platform": "Darwin"}

    def _linux_services(self):
        """Linux: systemctl list-units。"""
        out = _run(["systemctl", "list-units", "--type=service", "--all",
                     "--no-pager", "--no-legend"], timeout=15)
        if out is None:
            return None
        services = []
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            name = parts[0].removesuffix(".service")
            load_state = parts[1]
            active = parts[2]
            sub = parts[3]
            state = "Running" if active == "active" and sub == "running" else "Stopped"
            services.append({
                "name": name,
                "display": name,
                "state": state,
                "mode": load_state,
                "pid": 0,
                "protected": False,
            })
        if not services:
            return None
        return {"services": services, "admin": self.is_admin(), "platform": "Linux"}

    def _service_target(self, name):
        """launchctl 现代语法需要域前缀。用户服务走 gui/<uid>，已带域的名字保持原样。"""
        if "/" in name:
            return name
        if _IS_MACOS:
            return f"gui/{os.getuid()}/{name}"
        return name

    def service_action(self, name, action, mode=""):
        name = (name or "").strip()
        if not name:
            return {"success": False, "error": "无效的服务名", "name": name}
        if action not in ("start", "stop", "restart", "mode"):
            return {"success": False, "error": "不支持的操作", "name": name}
        if _IS_MACOS and name in _MAC_PROTECTED_SERVICES:
            return {"success": False, "error": "受保护的系统服务，禁止操作", "name": name}

        if _IS_MACOS:
            target = self._service_target(name)
            if action == "mode":
                verb = "disable" if mode == "Disabled" else "enable"
                res = _run(["launchctl", verb, target], timeout=30)
            elif action == "stop":
                res = _run(["launchctl", "bootout", target], timeout=30)
            elif action == "restart":
                res = _run(["launchctl", "kickstart", "-k", target], timeout=30)
            else:
                _run(["launchctl", "enable", target], timeout=15)
                res = _run(["launchctl", "kickstart", target], timeout=30)
        else:
            if action == "mode":
                res = _run(["systemctl", "enable" if mode != "Disabled" else "disable",
                            name], timeout=30)
            else:
                res = _run(["systemctl", action, name], timeout=30)

        if res is None:
            return {"success": False, "error": "操作超时", "name": name}
        ok = res.returncode == 0
        msg = (res.stderr or res.stdout or "").strip()
        result = {"success": ok, "name": name}
        if ok:
            result["state"] = "Running" if action in ("start", "restart") else "Stopped"
            self.services_snapshot(force=True)
        else:
            result["error"] = msg[:500] or "操作失败"
        return result

    # ── 磁盘 ──────────────────────────────────────────────────

    def disk_snapshot(self):
        if _IS_MACOS:
            return self._macos_disks()
        return self._linux_disks()

    def _macos_fstypes(self):
        out = _run(["mount"], timeout=5)
        mapping = {}
        if out is None:
            return mapping
        for line in out.stdout.splitlines():
            m = re.search(r" on (.*) \(([^,)]+)", line)
            if m:
                mapping[m.group(1)] = m.group(2)
        return mapping

    def _macos_disks(self):
        """macOS: df -k -P。数据卷才反映用户真正能用的空间，系统快照卷单独标出。"""
        out = _run(["df", "-k", "-P"], timeout=10)
        if out is None or not out.stdout:
            return None
        fstypes = self._macos_fstypes()
        disks = []
        for line in out.stdout.splitlines()[1:]:
            m = re.match(r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+(.+)$", line)
            if not m:
                continue
            fs_dev, blocks, used_k, avail_k, cap, mount = m.groups()
            mount = mount.strip()
            if fs_dev in ("devfs", "map") or fs_dev.startswith("map "):
                continue
            if mount in _SKIP_MOUNTS or "TimeMachine" in mount:
                continue
            if mount.startswith("/System/Volumes/Data/"):
                continue
            used = int(used_k) * 1024
            free = int(avail_k) * 1024
            # APFS 的块数是整个容器，Capacity 才是本卷 used/(used+avail)。用后者，避免百分比和容量对不上。
            total = used + free
            if total <= 0:
                continue
            if mount == "/":
                label, dtype, rank = "系统卷", "系统盘", 1
            elif mount == "/System/Volumes/Data":
                label, dtype, rank = "数据卷", "数据盘", 0
            elif mount.startswith("/Volumes/"):
                label, dtype, rank = os.path.basename(mount) or mount, "外置磁盘", 2
            else:
                label, dtype, rank = mount, "固定磁盘", 3
            disks.append({
                "drive": mount,
                "label": label,
                "fs": fstypes.get(mount, ""),
                "type": dtype,
                "total": total, "used": used, "free": free,
                "usedPct": round(used * 100.0 / total, 1),
                "ready": True,
                "_rank": rank,
            })
        disks.sort(key=lambda d: (d["_rank"], d["drive"]))
        for d in disks:
            d.pop("_rank", None)
        return {"disks": disks, "platform": "Darwin"} if disks else None

    def _linux_disks(self):
        """Linux: df 输出解析。"""
        out = _run(["df", "-B1", "--output=target,fstype,size,used,avail,pcent"],
                   timeout=10)
        if out is None:
            return None
        disks = []
        for line in out.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            mount, fstype = parts[0], parts[1]
            try:
                total = int(parts[2])
                used = int(parts[3])
                free = int(parts[4])
            except ValueError:
                continue
            pct_str = parts[5].rstrip("%")
            try:
                used_pct = float(pct_str)
            except ValueError:
                used_pct = 0.0

            disks.append({
                "drive": mount,
                "label": os.path.basename(mount) or "/",
                "fs": fstype,
                "type": "固定磁盘",
                "total": total, "used": used, "free": free,
                "usedPct": used_pct,
                "ready": True,
            })
        return {"disks": disks, "platform": "Linux"} if disks else None

    def clean_catalogs(self):
        home = os.environ.get("HOME", "")
        if _IS_MACOS:
            return [
                ("user_caches", "用户缓存", os.path.join(home, "Library", "Caches"), True),
                ("logs", "日志文件", os.path.join(home, "Library", "Logs"), True),
                ("tmp", "临时文件", "/tmp", True),
                ("npm_cache", "npm 缓存", os.path.join(home, ".npm", "_cacache"), True),
                ("pip_cache", "pip 缓存", os.path.join(home, "Library", "Caches", "pip"), True),
                ("brew_cache", "Homebrew 缓存", os.path.join(home, "Library", "Caches", "Homebrew"), True),
                ("yarn_cache", "Yarn 缓存", os.path.join(home, "Library", "Caches", "Yarn"), True),
                ("go_build", "Go 构建缓存", os.path.join(home, "Library", "Caches", "go-build"), True),
                ("trash", "废纸篓", os.path.join(home, ".Trash"), False),
            ]
        # Linux
        return [
            ("user_caches", "用户缓存", os.path.join(home, ".cache"), True),
            ("tmp", "临时文件", "/tmp", True),
            ("npm_cache", "npm 缓存", os.path.join(home, ".npm", "_cacache"), True),
            ("pip_cache", "pip 缓存", os.path.join(home, ".cache", "pip"), True),
            ("yarn_cache", "Yarn 缓存", os.path.join(home, ".cache", "yarn"), True),
            ("go_build", "Go 构建缓存", os.path.join(home, ".cache", "go-build"), True),
            ("trash", "回收站", os.path.join(home, ".local", "share", "Trash"), False),
        ]

    def is_protected_path(self, path):
        low = os.path.abspath(path)
        # 数据卷挂在 /System/Volumes/Data，用户文件的真实路径在这里，不能按 /System 一刀切
        if low == "/System/Volumes/Data" or low.startswith("/System/Volumes/Data/"):
            return False
        guards = ["/System", "/usr", "/bin", "/sbin", "/private/var", "/Library"]
        return any(low == g or low.startswith(g + "/") for g in guards)

    # ── 进程探针 ──────────────────────────────────────────────

    _proc_lock = threading.Lock()
    _proc_cache = {"data": None, "ts": 0.0}
    _PROC_CACHE_TTL = 15.0

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
        """按常驻内存（RSS）取 Top 20。macOS 的 ps -m 才是按内存排序。"""
        cmd = (["ps", "-axm", "-o", "pid=,rss=,comm="] if _IS_MACOS
               else ["ps", "-eo", "pid=,rss=,comm=", "--sort=-rss"])
        out = _run(cmd, timeout=10)
        if out is None or not out.stdout:
            return None
        top = []
        for line in out.stdout.splitlines():
            m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)$", line)
            if not m:
                continue
            pid = int(m.group(1))
            rss_kb = int(m.group(2))
            if rss_kb <= 0:
                continue
            name = os.path.basename(m.group(3).strip()) or m.group(3).strip()
            top.append({
                "pid": pid,
                "name": name[:60],
                "commit": rss_kb * 1024,
                "ws": rss_kb * 1024,
                "peak": 0,
            })
            if len(top) >= 20:
                break

        os_info = {
            "caption": f"{platform.system()} {platform.release()}",
            "version": platform.version(),
            "hypervisorPresent": False,
            "bootTime": "",
        }
        boot = self.uptime_seconds()
        if boot is not None:
            import datetime
            bt = datetime.datetime.now() - datetime.timedelta(seconds=boot)
            os_info["bootTime"] = bt.isoformat()

        return {"top": top, "os": os_info}
