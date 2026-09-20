// 端口占用 Tab：列表渲染、搜索/筛选、按 PID/端口号终止、导出。
import { $, toast, esc, download } from "./common.js";

let allPorts = [];
let curProto = "ALL";
let autoTimer = null;
let pending = null; // {type:'pid', pid} | {type:'port', port, items}

export async function fetchPorts() {
  const btn = $("refreshBtn");
  btn.innerHTML = '<span class="spin"></span> 刷新中';
  try {
    const res = await fetch("/api/ports", { cache: "no-store" });
    const data = await res.json();
    allPorts = data.ports || [];
    render();
  } catch (e) {
    toast("获取端口失败：" + e.message, false);
  } finally {
    btn.textContent = "刷新";
  }
}

function render() {
  const q = $("search").value.trim().toLowerCase();
  const rows = allPorts.filter(p => {
    if (curProto !== "ALL" && p.protocol !== curProto) return false;
    if (!q) return true;
    return (p.local + " " + p.pid + " " + (p.name || "")).toLowerCase().includes(q);
  });

  // 统计
  const tcp = allPorts.filter(p => p.protocol === "TCP").length;
  const listen = allPorts.filter(p => (p.state || "").toUpperCase() === "LISTENING" || (p.state || "").toUpperCase() === "LISTEN").length;
  const procs = new Set(allPorts.map(p => p.pid)).size;
  $("s-total").textContent = allPorts.length;
  $("s-tcp").textContent = tcp;
  $("s-listen").textContent = listen;
  $("s-proc").textContent = procs;

  const tbody = $("tbody");
  tbody.innerHTML = "";
  $("empty").style.display = rows.length ? "none" : "block";

  for (const p of rows) {
    const tr = document.createElement("tr");
    const protoTag = `<span class="tag ${p.protocol.toLowerCase()}">${p.protocol}</span>`;
    const st = (p.state || "").toUpperCase();
    const stateTag = st === "LISTENING" || st === "LISTEN"
      ? `<span class="tag listen">${p.state}</span>`
      : (p.state ? `<span class="tag other">${p.state}</span>` : '<span class="muted">—</span>');
    tr.innerHTML = `
      <td>${protoTag}</td>
      <td>${p.local}</td>
      <td>${stateTag}</td>
      <td class="pid">${p.pid}</td>
      <td class="name" title="${p.name || ''}">${p.name || "—"}</td>
      <td style="text-align:right"><button class="kill" data-pid="${p.pid}" data-name="${p.name || ''}" data-port="${p.local}">终止</button></td>`;
    tbody.appendChild(tr);
  }
}

// 事件委托：终止按钮
$("tbody").addEventListener("click", (e) => {
  const b = e.target.closest(".kill");
  if (!b) return;
  openKillPid(b.dataset.pid, b.dataset.name, b.dataset.port);
});

function openKillPid(pid, name, port) {
  pending = { type: "pid", pid };
  $("m-title").textContent = "确认终止进程？";
  $("m-body").innerHTML = `
    <p>即将终止进程 <span class="hl">${name || "未知进程"}</span></p>
    <p>PID：<span class="hl">${pid}</span> ｜ 占用：<span class="hl">${port}</span></p>
    <p class="warnbox">该操作不可撤销，进程将被强制结束（含其子进程）。</p>`;
  $("modal").classList.add("show");
}

function openKillPort(port, items) {
  pending = { type: "port", port, items };
  const rows = items.map(it =>
    `<div class="row"><span class="pname">${it.name || "未知进程"} · ${it.local}</span><span class="ppid">PID ${it.pid}</span></div>`
  ).join("");
  $("m-title").textContent = `确认终止端口 ${port} 上的全部进程？`;
  $("m-body").innerHTML = `
    <p>以下进程正在占用端口 <span class="hl">${port}</span>（共 ${items.length} 个）：</p>
    <div class="proc-list">${rows}</div>
    <p class="warnbox">该操作不可撤销，这些进程将被强制结束（含其子进程）。</p>`;
  $("modal").classList.add("show");
}

function closeModal() { $("modal").classList.remove("show"); pending = null; }

$("m-cancel").onclick = closeModal;
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

$("m-confirm").onclick = async () => {
  if (!pending) return;
  const task = pending;
  closeModal();
  try {
    let res, data;
    if (task.type === "pid") {
      res = await fetch("/api/kill", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pid: Number(task.pid) })
      });
      data = await res.json();
      if (data.success) toast(`已终止 PID ${task.pid}`, true);
      else toast("终止失败：" + (data.error || data.message || "未知错误"), false);
    } else {
      res = await fetch("/api/kill_port", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: Number(task.port) })
      });
      data = await res.json();
      if (data.success) toast(`端口 ${task.port} 已清理：${data.killed.length} 个进程已终止` + (data.skipped.length ? `，${data.skipped.length} 个跳过` : ""), true);
      else toast("终止失败：" + (data.error || "未知错误"), false);
    }
  } catch (e) {
    toast("请求失败：" + e.message, false);
  } finally {
    setTimeout(fetchPorts, 500);
  }
};

// 按端口号终止
$("killPortBtn").onclick = async () => {
  const port = parseInt($("portInput").value, 10);
  if (!port || port < 1 || port > 65535) { toast("请输入有效的端口号（1-65535）", false); return; }
  const btn = $("killPortBtn");
  btn.innerHTML = '<span class="spin"></span> 查找中';
  try {
    const res = await fetch("/api/ports", { cache: "no-store" });
    const data = await res.json();
    const items = (data.ports || []).filter(p => {
      const lp = p.local.split(":").pop();
      return parseInt(lp, 10) === port;
    });
    if (!items.length) { toast(`端口 ${port} 上未发现任何连接`, false); return; }
    openKillPort(port, items);
  } catch (e) {
    toast("查询失败：" + e.message, false);
  } finally {
    btn.textContent = "查找并终止";
  }
};
$("portInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("killPortBtn").click(); });

// 导出
$("exportCsv").onclick = () => {
  if (!allPorts.length) { toast("当前没有数据可导出", false); return; }
  const header = ["协议", "本地地址", "状态", "PID", "进程名"];
  const lines = [header.join(",")];
  for (const p of allPorts) {
    const row = [p.protocol, p.local, p.state || "", p.pid, p.name || ""]
      .map(v => `"${String(v).replace(/"/g, '""')}"`);
    lines.push(row.join(","));
  }
  // BOM 让 Excel 正确识别中文
  download(`ports_${Date.now()}.csv`, "﻿" + lines.join("\r\n"), "text/csv;charset=utf-8");
  toast(`已导出 ${allPorts.length} 条到 CSV`, true);
};
$("exportJson").onclick = () => {
  if (!allPorts.length) { toast("当前没有数据可导出", false); return; }
  download(`ports_${Date.now()}.json`, JSON.stringify(allPorts, null, 2), "application/json");
  toast(`已导出 ${allPorts.length} 条到 JSON`, true);
};

// 搜索 / 筛选
$("search").addEventListener("input", render);
$("protoSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  [...$("protoSeg").children].forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  curProto = b.dataset.proto;
  render();
});

$("refreshBtn").onclick = fetchPorts;
$("autoRefresh").addEventListener("change", (e) => {
  if (e.target.checked) { autoTimer = setInterval(fetchPorts, 3000); }
  else { clearInterval(autoTimer); autoTimer = null; }
});

// 初始加载
fetchPorts();
