// 磁盘清理 Tab：分区容量、垃圾扫描/清理、大文件扫描/删除。
import { $, toast, esc, fmtB, fmtClock, state } from "./common.js";

let disks = [];
let disksLoaded = false;
let cleanItems = [];
let cleanSel = new Set();
let bigFiles = [];
let bigSel = new Set();
let diskConfirm = null;

function openDiskConfirm(title, bodyHtml, onOk) {
  diskConfirm = onOk;
  $("diskModalTitle").textContent = title;
  $("diskModalBody").innerHTML = bodyHtml;
  $("diskModal").classList.add("show");
}
function closeDiskConfirm() { $("diskModal").classList.remove("show"); diskConfirm = null; }
$("diskModalOk").onclick = () => { const fn = diskConfirm; closeDiskConfirm(); if (fn) fn(); };
$("diskModalCancel").onclick = closeDiskConfirm;
$("diskModal").addEventListener("click", (e) => { if (e.target.id === "diskModal") closeDiskConfirm(); });

export async function fetchDisks() {
  try {
    const d = await (await fetch("/api/disks", { cache: "no-store" })).json();
    disks = d.disks || [];
    disksLoaded = true;
    state.disksLoaded = true;
    $("diskBadge").className = "badge " + (d.ok === false ? "st-warn" : "st-ok");
    $("diskBadgeTxt").textContent = d.ok === false ? "获取失败" : disks.length + " 个分区";
    renderDisks();
    if (d.ok === false) toast("获取磁盘信息失败：" + (d.error || "未知原因"), false);
  } catch (e) {
    toast("获取磁盘信息失败：" + e.message, false);
  }
}

function renderDisks() {
  const g = $("diskGrid");
  g.innerHTML = "";
  for (const d of disks) {
    const ready = d.ready !== false;
    const pct = d.usedPct;
    const color = !ready ? "var(--muted)"
      : pct >= 90 ? "var(--danger)" : pct >= 75 ? "var(--warn)" : "var(--ok)";
    const card = document.createElement("div");
    card.className = "disk";
    card.innerHTML =
      '<div class="dh"><div class="dl"' + (ready ? '' : ' style="opacity:.55"') + '>' +
          esc(d.drive.replace("\\", "")) + '</div>' +
        '<div style="min-width:0"><div class="dt">' + esc(d.label || d.drive) + '</div>' +
        '<div class="ds">' + esc(d.type) + ' · ' + esc(d.fs || (ready ? "—" : "未就绪")) + '</div></div>' +
        '<div class="dp" style="color:' + color + '">' + (ready ? pct + "%" : "—") + '</div></div>' +
      '<div class="dbar"><i style="width:' + (ready ? Math.min(100, pct) : 0) +
          '%;background:' + color + '"></i></div>' +
      '<div class="dn">' + (ready
        ? '<span>已用 ' + fmtB(d.used) + '</span><span>可用 ' + fmtB(d.free) + ' / ' + fmtB(d.total) + '</span>'
        : '<span>无介质 / 不可访问</span><span>—</span>') + '</div>';
    g.appendChild(card);
  }
}

async function scanClean() {
  const btn = $("cleanScanBtn");
  btn.innerHTML = '<span class="spin"></span> 扫描中';
  btn.disabled = true;
  try {
    const r = await fetch("/api/clean_scan", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const d = await r.json();
    cleanItems = d.items || [];
    cleanSel = new Set(cleanItems.filter((x) => x.recommended && x.size > 0).map((x) => x.id));
    renderClean();
    if (d.truncated) toast("扫描超时，部分目录未统计完整", "warn");
  } catch (e) {
    toast("扫描失败：" + e.message, false);
  } finally {
    btn.textContent = "扫描可清理项";
    btn.disabled = false;
  }
}

function renderClean() {
  const tb = $("cleanBody");
  tb.innerHTML = "";
  $("cleanEmpty").style.display = cleanItems.length ? "none" : "block";
  let total = 0;
  for (const it of cleanItems) {
    if (cleanSel.has(it.id)) total += it.size;
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input type="checkbox" class="chk" data-id="' + esc(it.id) + '"' +
        (cleanSel.has(it.id) ? " checked" : "") + (it.size ? "" : " disabled") + '></td>' +
      '<td>' + esc(it.name) +
        (it.exists ? "" : ' <span style="color:var(--muted);font-size:11px">（不存在）</span>') + '</td>' +
      '<td><span class="fpath" title="' + esc(it.path) + '">' + esc(it.path) + '</span></td>' +
      '<td class="mb">' + fmtB(it.size) + '</td>' +
      '<td class="mb">' + (it.files || "—") + '</td>';
    tb.appendChild(tr);
  }
  $("cleanSummary").textContent = cleanItems.length
    ? "共 " + cleanItems.length + " 类 · 已选 " + fmtB(total) : "尚未扫描";
  $("cleanRunBtn").disabled = !(cleanSel.size && total > 0);
}

$("cleanBody").addEventListener("change", (e) => {
  const c = e.target.closest(".chk");
  if (!c) return;
  if (c.checked) cleanSel.add(c.dataset.id); else cleanSel.delete(c.dataset.id);
  renderClean();
});
$("cleanScanBtn").onclick = scanClean;
$("cleanRunBtn").onclick = () => {
  const picked = cleanItems.filter((x) => cleanSel.has(x.id));
  if (!picked.length) return;
  const total = picked.reduce((s, x) => s + x.size, 0);
  openDiskConfirm("确认清理选中项？",
    "<p>将永久删除以下类别中的文件（不进回收站）：</p><ul>" +
    picked.map((x) => "<li>" + esc(x.name) + " · " + fmtB(x.size) + "</li>").join("") + "</ul>" +
    "<p>预计释放 <b>" + fmtB(total) + "</b>。被程序占用的文件会被跳过。</p>" +
    '<p class="warnbox">删除后不可恢复，请确认没有程序正在使用这些文件。</p>',
    runClean);
};

async function runClean() {
  const ids = [...cleanSel];
  toast("清理中，请稍候…", "ok");
  try {
    const r = await fetch("/api/clean", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const d = await r.json();
    if (d.removed) toast("已删除 " + d.removed + " 个文件，释放 " + fmtB(d.freed) +
      (d.failed ? "，跳过 " + d.failed + " 个（占用/无权限）" : ""), true);
    else toast("没有文件被删除" + (d.failed ? "（" + d.failed + " 个被占用或无权限）" : ""), "warn");
    cleanSel = new Set();
    await scanClean();
    fetchDisks();
  } catch (e) {
    toast("清理失败：" + e.message, false);
  }
}

async function scanBig() {
  const path = ($("bigPath").value || "").trim();
  if (!path) { toast("请输入扫描路径", false); return; }
  const btn = $("bigScanBtn");
  btn.innerHTML = '<span class="spin"></span> 扫描中';
  btn.disabled = true;
  try {
    const r = await fetch("/api/bigfiles", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path, min_mb: Number($("bigMin").value) || 100, limit: 50 }),
    });
    const d = await r.json();
    bigFiles = d.files || [];
    bigSel = new Set();
    renderBig();
    $("bigSummary").textContent = d.error ? d.error
      : "扫描 " + (d.scanned || 0) + " 个文件，命中 " + bigFiles.length + " 个" +
        (d.truncated ? "（已超时截断）" : "");
    if (d.error) toast(d.error, false);
  } catch (e) {
    toast("扫描失败：" + e.message, false);
  } finally {
    btn.textContent = "扫描大文件";
    btn.disabled = false;
  }
}

function renderBig() {
  const tb = $("bigBody");
  tb.innerHTML = "";
  $("bigEmpty").style.display = bigFiles.length ? "none" : "block";
  let total = 0;
  for (const f of bigFiles) {
    if (bigSel.has(f.path)) total += f.size;
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input type="checkbox" class="chk" data-p="' + esc(f.path) + '"' +
        (bigSel.has(f.path) ? " checked" : "") + '></td>' +
      '<td><span class="fpath" title="' + esc(f.path) + '">' + esc(f.path) + '</span></td>' +
      '<td class="mb">' + fmtB(f.size) + '</td>' +
      '<td class="mb">' + esc(fmtClock(f.mtime * 1000)) + '</td>';
    tb.appendChild(tr);
  }
  $("bigDelBtn").disabled = !bigSel.size;
  if (bigSel.size) $("bigSummary").textContent = "已选 " + bigSel.size + " 个文件 · " + fmtB(total);
}

$("bigBody").addEventListener("change", (e) => {
  const c = e.target.closest(".chk");
  if (!c) return;
  if (c.checked) bigSel.add(c.dataset.p); else bigSel.delete(c.dataset.p);
  renderBig();
});
$("bigScanBtn").onclick = scanBig;
$("bigDelBtn").onclick = () => {
  const paths = [...bigSel];
  if (!paths.length) return;
  const total = bigFiles.filter((f) => bigSel.has(f.path)).reduce((s, f) => s + f.size, 0);
  openDiskConfirm("确认删除选中的 " + paths.length + " 个文件？",
    "<p>将永久删除以下文件（不进回收站）：</p><ul>" +
    paths.slice(0, 8).map((p) => "<li>" + esc(p) + "</li>").join("") +
    (paths.length > 8 ? "<li>…等共 " + paths.length + " 个</li>" : "") + "</ul>" +
    "<p>预计释放 <b>" + fmtB(total) + "</b>。</p>" +
    '<p class="warnbox">删除后不可恢复；系统目录下的文件会被服务端拒绝。</p>',
    () => runDeleteBig(paths));
};

async function runDeleteBig(paths) {
  try {
    const r = await fetch("/api/delete_paths", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: paths }),
    });
    const d = await r.json();
    if (d.removed) toast("已删除 " + d.removed + " 个文件，释放 " + fmtB(d.freed) +
      (d.failed ? "，失败/拒绝 " + d.failed + " 个" : ""), true);
    else toast("没有文件被删除" + (d.failed ? "（" + d.failed + " 个失败，可能无权限或受保护）" : ""), "warn");
    bigSel = new Set();
    await scanBig();
    fetchDisks();
  } catch (e) {
    toast("删除失败：" + e.message, false);
  }
}
