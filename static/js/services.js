// 系统服务 Tab：列表渲染、搜索/筛选、启动/停止/重启/改启动类型。
import { $, toast, esc, state } from "./common.js";

let svcAll = [];
let svcState = "ALL";
let svcMode = "ALL";
let svcTimer = null;
let svcPending = null;    // {name, display, act, mode}
let svcAdmin = false;
let svcLoaded = false;

export async function fetchServices(force) {
  const btn = $("svcRefreshBtn");
  btn.innerHTML = '<span class="spin"></span> 刷新中';
  try {
    const res = await fetch("/api/services" + (force ? "?force=1" : ""), { cache: "no-store" });
    const d = await res.json();
    svcAll = d.services || [];
    svcAdmin = !!d.admin;
    svcLoaded = true;
    state.servicesLoaded = true;
    $("svcAdminHint").textContent = svcAdmin
      ? "已以管理员身份运行，可启动 / 停止服务。"
      : "未以管理员身份运行：启停与修改启动类型可能被系统拒绝（Access denied）。";
    $("svcBadge").className = "badge " + (d.ok === false || !svcAdmin ? "st-warn" : "st-ok");
    $("svcBadgeTxt").textContent = d.ok === false ? "获取失败"
      : (svcAdmin ? "管理员模式" : "受限模式");
    renderServices();
    if (d.ok === false) toast("获取服务列表失败：" + (d.error || "未知原因"), false);
  } catch (e) {
    toast("获取服务列表失败：" + e.message, false);
  } finally {
    btn.textContent = "刷新";
  }
}

function modeTag(mode) {
  const map = { Auto: ["mode-auto", "自动"], Manual: ["mode-manual", "手动"], Disabled: ["mode-disabled", "禁用"] };
  const it = map[mode];
  return it ? '<span class="tag ' + it[0] + '">' + it[1] + '</span>'
            : '<span class="tag stopped">' + esc(mode || "未知") + '</span>';
}
function modeSelect(s) {
  const map = { Auto: "Automatic", Manual: "Manual", Disabled: "Disabled" };
  const cur = map[s.mode] || "";
  let html = '<select class="svcsel" data-name="' + esc(s.name) + '" data-display="' + esc(s.display) +
             '" data-old="' + cur + '"' + (s.protected ? " disabled" : "") + ">";
  if (!cur) html += '<option value="">' + esc(s.mode || "未知") + "</option>";
  [["Automatic", "自动"], ["Manual", "手动"], ["Disabled", "禁用"]].forEach(([v, t]) => {
    html += '<option value="' + v + '"' + (v === cur ? " selected" : "") + ">" + t + "</option>";
  });
  return html + "</select>";
}

function renderServices() {
  const q = ($("svcSearch").value || "").trim().toLowerCase();
  const rows = svcAll.filter((s) => {
    if (svcState !== "ALL" && s.state !== svcState) return false;
    if (svcMode !== "ALL" && s.mode !== svcMode) return false;
    if (!q) return true;
    return (s.display + " " + s.name).toLowerCase().includes(q);
  });

  const running = svcAll.filter((s) => s.state === "Running").length;
  $("sv-total").textContent = svcAll.length;
  $("sv-running").textContent = running;
  $("sv-stopped").textContent = svcAll.length - running;
  $("sv-auto").textContent = svcAll.filter((s) => s.mode === "Auto").length;

  const tbody = $("svcBody");
  tbody.innerHTML = "";
  $("svcEmpty").style.display = rows.length ? "none" : "block";
  if (!rows.length) $("svcEmpty").textContent = svcLoaded ? "没有匹配的服务" : "正在加载服务列表…";

  for (const s of rows) {
    const isRun = s.state === "Running";
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td><div class="svcname"><span class="d" title="' + esc(s.display) + '">' + esc(s.display) + '</span>' +
        '<span class="n">' + esc(s.name) +
          (s.protected ? ' · <b style="color:var(--warn)">受保护</b>' : "") + '</span></div></td>' +
      '<td>' + (isRun ? '<span class="tag run">运行中</span>' : '<span class="tag stopped">已停止</span>') + '</td>' +
      '<td>' + modeTag(s.mode) + '</td>' +
      '<td class="pid">' + (s.pid || "—") + '</td>' +
      '<td style="text-align:right;white-space:nowrap">' +
        '<button class="svcbtn start" data-act="start" data-name="' + esc(s.name) + '" data-display="' + esc(s.display) + '"' +
          (isRun || s.protected ? " disabled" : "") + '>启动</button> ' +
        '<button class="svcbtn stop" data-act="stop" data-name="' + esc(s.name) + '" data-display="' + esc(s.display) + '"' +
          (!isRun || s.protected ? " disabled" : "") + '>停止</button> ' +
        modeSelect(s) +
      '</td>';
    tbody.appendChild(tr);
  }
}

$("svcBody").addEventListener("click", (e) => {
  const b = e.target.closest(".svcbtn");
  if (!b || b.disabled) return;
  openSvcConfirm(b.dataset.name, b.dataset.display, b.dataset.act, "");
});
$("svcBody").addEventListener("change", (e) => {
  const sel = e.target.closest(".svcsel");
  if (!sel || sel.disabled) return;
  sel.dataset.pending = sel.value;
  openSvcConfirm(sel.dataset.name, sel.dataset.display, "mode", sel.value);
});

function openSvcConfirm(name, display, act, mode) {
  svcPending = { name, display, act, mode };
  const modeTxt = { Automatic: "自动", Manual: "手动", Disabled: "禁用" }[mode] || mode;
  const actTxt = act === "start" ? "启动" : act === "stop" ? "停止"
    : act === "restart" ? "重启" : "将启动类型改为「" + modeTxt + "」";
  $("svcModalTitle").textContent = "确认" + actTxt + "？";
  let extra = "";
  if (act === "stop") extra = '<p class="warnbox">停止服务可能导致依赖它的功能不可用，请确认无其它服务依赖它。</p>';
  if (act === "mode" && mode === "Disabled") extra = '<p class="warnbox">禁用后该服务不会随系统启动（当前已运行的实例不受影响）。</p>';
  if (!svcAdmin) extra += '<p class="warnbox">当前未以管理员身份运行，操作可能被系统拒绝。</p>';
  $("svcModalBody").innerHTML =
    '<p>服务：<span class="hl">' + esc(display || name) + '</span></p>' +
    '<p>名称：<span class="hl">' + esc(name) + '</span></p>' + extra;
  $("svcModal").classList.add("show");
}
function restoreSelect() {
  const sel = document.querySelector("#svcBody .svcsel[data-pending]");
  if (sel) { sel.value = sel.dataset.old || ""; delete sel.dataset.pending; }
}
function closeSvcModal() { $("svcModal").classList.remove("show"); svcPending = null; }

$("svcModalCancel").onclick = () => { restoreSelect(); closeSvcModal(); };
$("svcModal").addEventListener("click", (e) => {
  if (e.target.id === "svcModal") { restoreSelect(); closeSvcModal(); }
});
$("svcModalOk").onclick = async () => {
  const task = svcPending;
  if (!task) return;
  closeSvcModal();
  const doneTxt = task.act === "mode" ? "已更新启动类型：" : task.act === "start" ? "已启动：" : "已停止：";
  try {
    const res = await fetch("/api/service", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: task.name, action: task.act, mode: task.mode }),
    });
    const d = await res.json();
    if (d.success) {
      toast(doneTxt + task.name, true);
      setTimeout(() => fetchServices(true), 700);
    } else {
      toast("操作失败：" + (d.error || "未知错误"), false);
      fetchServices(true);
    }
  } catch (e) {
    toast("请求失败：" + e.message, false);
  }
};

$("svcRefreshBtn").onclick = () => fetchServices(true);
$("svcSearch").addEventListener("input", renderServices);
$("svcStateSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  [...$("svcStateSeg").children].forEach((x) => x.classList.remove("active"));
  b.classList.add("active"); svcState = b.dataset.state; renderServices();
});
$("svcModeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  [...$("svcModeSeg").children].forEach((x) => x.classList.remove("active"));
  b.classList.add("active"); svcMode = b.dataset.mode; renderServices();
});
$("svcAutoRefresh").addEventListener("change", (e) => {
  if (e.target.checked) {
    svcTimer = setInterval(() => { if (state.curView === "services") fetchServices(); }, 10000);
    fetchServices();
  } else { clearInterval(svcTimer); svcTimer = null; }
});
