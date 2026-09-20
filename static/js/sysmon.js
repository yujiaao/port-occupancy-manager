// 系统内存监控 Tab：轮询指标、阈值评估、趋势图、报警弹窗。
import { $, toast, esc, fmtB, fmtSmart, fmtDur, fmtClock, state, GB, MB } from "./common.js";

// 存储键沿用旧名，避免改名后用户已保存的告警阈值丢失
const SETTINGS_KEY = "portinspector_sysmon_v1";
const BASE_TITLE = document.title;
const MAX_POINTS = 240;

const sysDefaults = {
  refreshSec: 2,
  warnAvailGb: 2,       // commit 剩余 < 2GB -> 警告
  critAvailGb: 0.5,     // commit 剩余 < 0.5GB -> 严重（崩溃现场仅 63MB）
  warnCommitPct: 85,
  critCommitPct: 95,
  warnPhysGb: 4,
  critPhysGb: 1,
  predict: true,
  predictWarnMin: 60,
  predictCritMin: 15,
  sound: true,
  notif: true,
  popup: true,
  snoozeMin: 5,
};

let S = loadSysSettings();
let mHistory = [];        // {t, ca, pa} 字节
let metrics = null;       // 最新指标
let memLevel = "off";     // ok | warn | crit | off
let activeCrit = false;   // 已告警且未恢复/未静默
let snoozeUntil = 0;
let sysFail = 0;
let lastTick = 0;
let sysTimer = null;
let titleFlash = null;
let audioCtx = null;
let topSig = "";
let prevLevel = "off";
let audioUnlocked = false;

function loadSysSettings() {
  const out = Object.assign({}, sysDefaults);
  try {
    const raw = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    for (const k in raw) if (k in sysDefaults) out[k] = raw[k];
  } catch (e) { /* 忽略损坏的本地设置 */ }
  return out;
}
function saveSysSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(S)); } catch (e) {}
}
function syncSysSettingsUI() {
  document.querySelectorAll("[data-key]").forEach((el) => {
    const k = el.dataset.key;
    if (el.type === "checkbox") el.checked = !!S[k];
    else el.value = S[k];
  });
}
document.querySelectorAll("[data-key]").forEach((el) => {
  el.addEventListener("change", () => {
    const k = el.dataset.key;
    if (el.type === "checkbox") S[k] = el.checked;
    else { const v = parseFloat(el.value); if (!isNaN(v)) S[k] = v; }
    saveSysSettings();
    restartSysTimer();
    toast("设置已保存", "ok");
  });
});
$("resetBtn").onclick = () => {
  S = Object.assign({}, sysDefaults);
  saveSysSettings(); syncSysSettingsUI(); restartSysTimer();
  toast("已恢复默认阈值", "ok");
};

// ---------- 声音 / 通知 / 标题闪烁 ----------
function ensureAudio() {
  if (!S.sound || !audioUnlocked) return;
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch (e) {}
}
function alarmSound() {
  ensureAudio();
  if (!audioCtx) return;
  const t0 = audioCtx.currentTime;
  for (let i = 0; i < 3; i++) {
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(880 + i * 60, t0 + i * 0.4);
    g.gain.setValueAtTime(0.0001, t0 + i * 0.4);
    g.gain.exponentialRampToValueAtTime(0.3, t0 + i * 0.4 + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.4 + 0.34);
    o.connect(g); g.connect(audioCtx.destination);
    o.start(t0 + i * 0.4); o.stop(t0 + i * 0.4 + 0.35);
  }
}
function systemNotify(title, body) {
  if (!S.notif || !("Notification" in window)) return;
  const TAG = "onekit-sysmon";
  try {
    if (Notification.permission === "granted") new Notification(title, { body, tag: TAG });
    else if (Notification.permission !== "denied") {
      Notification.requestPermission().then((p) => {
        if (p === "granted") new Notification(title, { body, tag: TAG });
      }).catch(() => {});
    }
  } catch (e) {}
}
$("notifBtn").onclick = () => {
  if (!("Notification" in window)) { toast("当前浏览器不支持系统通知", "err"); return; }
  Notification.requestPermission().then((p) =>
    toast(p === "granted" ? "系统通知已启用" : "通知权限被拒绝，仅保留页面内弹窗", p === "granted" ? "ok" : "err"));
};
function startTitleFlash() {
  if (titleFlash) return;
  let on = false;
  titleFlash = setInterval(() => {
    on = !on;
    document.title = on ? "⚠ 内存告警 · OneKit" : BASE_TITLE;
  }, 900);
}
function stopTitleFlash() {
  if (titleFlash) { clearInterval(titleFlash); titleFlash = null; }
  document.title = BASE_TITLE;
}
["pointerdown", "keydown"].forEach((ev) =>
  window.addEventListener(ev, () => { audioUnlocked = true; ensureAudio(); }));

// ---------- 评估与预测 ----------
function predict() {
  if (mHistory.length < 10) return null;
  const pts = mHistory.slice(-20).filter((x) => x.ca > 0);
  if (pts.length < 10) return null;
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  const t0 = pts[0].t;
  for (const pt of pts) {
    const x = (pt.t - t0) / 1000, y = pt.ca / GB;
    sx += x; sy += y; sxx += x * x; sxy += x * y;
  }
  const k = pts.length, denom = k * sxx - sx * sx;
  if (Math.abs(denom) < 1e-9) return null;
  const a = (k * sxy - sx * sy) / denom;   // GB/秒
  if (a >= -0.0001) return null;           // 未在下降
  const lastY = pts[pts.length - 1].ca / GB;
  const minLeft = lastY / (-a) / 60;
  if (!isFinite(minLeft)) return null;
  return { minLeft: Math.max(0, Math.round(minLeft)), gbPerMin: -a * 60 };
}
function sysEvaluate(m) {
  const reasons = { warn: [], crit: [] };
  const c = m.commit, p = m.physical;
  const hasCommit = c.total > 0;
  const availGb = c.avail / GB, physGb = p.avail / GB;
  if (hasCommit) {
    if (c.usedPct >= S.critCommitPct) reasons.crit.push("提交空间占用已达 " + c.usedPct + "%（>=" + S.critCommitPct + "%），已接近上限");
    else if (c.usedPct >= S.warnCommitPct) reasons.warn.push("提交空间占用 " + c.usedPct + "%（>=" + S.warnCommitPct + "%）");
    if (availGb <= S.critAvailGb) reasons.crit.push("提交空间剩余仅 " + fmtSmart(c.avail) + "（<" + S.critAvailGb + "GB），Windows 上任何进程申请内存都可能失败");
    else if (availGb <= S.warnAvailGb) reasons.warn.push("提交空间剩余 " + fmtSmart(c.avail) + "（<" + S.warnAvailGb + "GB）");
  }
  if (physGb <= S.critPhysGb) reasons.crit.push("物理内存剩余仅 " + fmtSmart(p.avail) + "（<" + S.critPhysGb + "GB）");
  else if (physGb <= S.warnPhysGb) reasons.warn.push("物理内存剩余 " + fmtSmart(p.avail) + "（<" + S.warnPhysGb + "GB）");
  const pred = predict();
  if (S.predict && pred) {
    if (pred.minLeft <= S.predictCritMin) reasons.crit.push("按近 8 分钟下降趋势，提交空间约 " + pred.minLeft + " 分钟后耗尽");
    else if (pred.minLeft <= S.predictWarnMin) reasons.warn.push("按趋势推算约 " + pred.minLeft + " 分钟后提交空间将耗尽");
  }
  return { level: reasons.crit.length ? "crit" : (reasons.warn.length ? "warn" : "ok"), warn: reasons.warn, crit: reasons.crit, pred };
}

// ---------- 报警 ----------
function fireAlarm(ev) {
  const c = metrics && metrics.commit;
  const big = ((metrics && metrics.top) || []).filter((x) => c && c.total > 0 && x.commit / c.total >= 0.05).slice(0, 3);
  const reasons = (ev.crit && ev.crit.length) ? ev.crit : (ev.warn || []);
  let html = "<p>系统即将/已经到达内存上限，继续放任可能复现「native 内存分配失败 → 应用崩溃」。</p>";
  html += "<ul>" + reasons.map((r) => "<li>" + esc(r) + "</li>").join("") + "</ul>";
  if (big.length) {
    html += "<p>当前较大的内存占用进程：</p><ul>";
    big.forEach((x) => html += "<li>" + esc(x.name || "未知进程") + " (PID " + x.pid + ") 提交 " + fmtB(x.commit) + "</li>");
    html += "</ul>";
  }
  html += '<div class="tip">建议：立即保存工作 → 切换到「端口占用」页或任务管理器按“提交大小”排序找元凶（vmmem / Docker / 浏览器）→ 必要时重启电脑，并把虚拟内存改为“系统管理的大小”。</div>';
  $("alertBody").innerHTML = html;
  if (ev.level === "crit") $("alertTitle").textContent = "⚠ 严重告警：提交空间（页面文件）即将耗尽";
  else { $("alertTitle").textContent = "⚠ 内存预警"; $("alertTitle").style.color = "var(--warn)"; }
  if (S.popup) $("alertModal").classList.add("show");
  activeCrit = true;
  systemNotify("OneKit " + (ev.level === "crit" ? "严重告警" : "预警"),
    (reasons[0] || "内存指标异常") + (big.length ? " · 最大占用：" + big[0].name : ""));
  if (S.sound) alarmSound();
  startTitleFlash();
}
$("alertOk").onclick = () => {
  $("alertModal").classList.remove("show");
  snoozeUntil = Date.now() + S.snoozeMin * 60000;
  activeCrit = false;
  stopTitleFlash();
  toast(S.snoozeMin > 0 ? "已静默 " + S.snoozeMin + " 分钟，超时后若仍异常会再次提醒" : "已关闭，异常持续将再次提醒", "ok");
};
$("alertModal").addEventListener("click", (e) => { if (e.target.id === "alertModal") $("alertOk").click(); });
$("testBtn").onclick = () => {
  if (!metrics) { toast("暂无数据，请稍候再测试", "err"); return; }
  fireAlarm({ level: "crit", crit: ["（测试）模拟严重告警：提交空间剩余 " + fmtSmart(metrics.commit.avail) + "，触发阈值报警链路"], warn: [] });
};

// ---------- 渲染 ----------
const TIP_OK = "请继续保持观察。若提交空间剩余持续低于 2GB，可在“报警设置”中调低阈值提前预警。";
const TIP_WARN = "建议查看下方“内存大户”，清理可释放的进程；若趋势继续下降请尽快保存工作。";
const TIP_CRIT = "此状态接近或已达“分配 1MB 都可能失败”的崩溃点（与 GitAskPassApp / JVM 崩溃日志一致）。建议立即保存并重启电脑，或先终止占内存的大户（vmmem / Docker / 浏览器）。";

function setStatus(lv, title, desc, hint) {
  const badge = $("statusBadge"), banner = $("banner");
  badge.className = "badge st-" + (lv === "crit" ? "crit" : lv === "warn" ? "warn" : lv);
  banner.className = "st-" + lv;
  $("badgeTxt").textContent = { ok: "状态正常", warn: "内存吃紧", crit: "内存告警", off: "连接中断" }[lv] || "状态正常";
  $("bannerTitle").textContent = title;
  $("bannerDesc").textContent = desc || "";
  $("bannerHint").textContent = hint || "";
  memLevel = lv;
  $("tabSysmon").classList.toggle("alert", lv === "crit");
}

function renderAll() {
  if (!metrics) return;
  const c = metrics.commit, p = metrics.physical;
  const hasCommit = c.total > 0;
  const commitHot = hasCommit && c.usedPct >= S.critCommitPct;

  $("cardCommit").classList.toggle("hot", commitHot);
  $("st-commitPct").textContent = hasCommit ? c.usedPct + "%" : "--";
  $("st-commitSub").textContent = hasCommit ? "已提交 " + fmtSmart(c.used) + " / 上限 " + fmtSmart(c.total) : "未检测到页面文件配置";
  $("barCommit").style.width = (hasCommit ? Math.min(100, c.usedPct) : 0) + "%";
  $("barCommit").style.background = commitHot ? "var(--danger)" : "";

  $("st-physPct").textContent = p.usedPct + "%";
  $("st-physSub").textContent = "可用 " + fmtSmart(p.avail) + " / 共 " + fmtB(p.total);
  $("barPhys").style.width = Math.min(100, p.usedPct) + "%";

  $("st-avail").textContent = hasCommit ? fmtSmart(c.avail) : "--";
  $("cardAvail").classList.toggle("hot", hasCommit && c.avail <= S.critAvailGb * GB);
  const pr = predict();
  $("st-trend").textContent = pr
    ? (pr.gbPerMin >= 0.01 ? "下降 " + pr.gbPerMin.toFixed(2) + " GB/分" : "缓慢下降")
    : (mHistory.length < 10 ? "采集中…" : "走势平稳");
  $("barAvail").style.width = (hasCommit ? Math.max(0, 100 - c.usedPct) : 0) + "%";
  $("st-load").textContent = metrics.memoryLoad + "%";
  $("st-uptime").textContent = fmtDur(metrics.uptimeSec);
  $("st-boot").textContent = "更新于 " + fmtClock(metrics.ts);

  $("gCommit").style.width = (hasCommit ? Math.min(100, c.usedPct) : 0) + "%";
  $("gCommitVal").textContent = hasCommit ? c.usedPct + "%" : "--";
  $("gPhys").style.width = Math.min(100, p.usedPct) + "%";
  $("gPhysVal").textContent = p.usedPct + "%";
  $("gLoad").style.width = Math.min(100, metrics.memoryLoad) + "%";
  $("gLoadVal").textContent = metrics.memoryLoad + "%";
  const gColor = (v, warnT, critT) => v >= critT ? "var(--danger)" : v >= warnT ? "var(--warn)" : "var(--ok)";
  $("gCommit").style.background = gColor(hasCommit ? c.usedPct : 0, S.warnCommitPct, S.critCommitPct);
  const physWarnPct = p.total > 0 ? 100 - S.warnPhysGb * GB / p.total * 100 : 85;
  const physCritPct = p.total > 0 ? 100 - S.critPhysGb * GB / p.total * 100 : 95;
  $("gPhys").style.background = gColor(p.usedPct, physWarnPct, physCritPct);

  renderKV();
  renderTop();
}

function renderKV() {
  const m = metrics, c = m.commit, p = m.physical, v = m.virtual, os = m.os || {};
  const rows = [
    ["物理内存 总量", fmtB(p.total)],
    ["物理内存 已用", fmtB(p.used) + "（" + p.usedPct + "%）"],
    ["物理内存 可用", fmtB(p.avail), p.avail < S.warnPhysGb * GB],
    ["", ""],
    ["提交空间上限 Commit limit", fmtB(c.total)],
    ["已提交 commit charge", fmtB(c.used)],
    ["剩余可提交 AvailPageFile", fmtB(c.avail), c.total > 0 && c.avail <= S.critAvailGb * GB],
    ["提交占用率", c.total > 0 ? c.usedPct + "%" : "--"],
    ["", ""],
    ["内存负载 Memory Load", m.memoryLoad + "%"],
    ["虚拟内存(进程地址)可用", fmtB(v.avail)],
    ["系统运行时长", fmtDur(m.uptimeSec)],
    ["采集时刻", fmtClock(m.ts)],
  ];
  const osLine = [];
  if (os.caption) osLine.push(esc(os.caption));
  if (os.version) osLine.push(esc(os.version));
  if (os.hypervisorPresent) osLine.push("Hyper-V/虚拟机监控已启用");
  if (os.bootTime) osLine.push("启动于 " + esc(String(os.bootTime).replace("T", " ").slice(0, 19)));
  if (osLine.length) rows.push(["", ""], ["系统", osLine.join(" · ")]);
  let html = "";
  for (const [k, val, warn] of rows) {
    if (k === "" && val === "") { html += '<dd class="sep"></dd>'; continue; }
    html += "<dt>" + esc(k) + "</dt><dd" + (warn ? ' class="big"' : "") + ">" + esc(val) + "</dd>";
  }
  $("kv").innerHTML = html;
}

function renderTop() {
  const top = metrics && metrics.top;
  if (!top || !top.length) {
    $("topBody").innerHTML = "";
    $("topEmpty").style.display = "block";
    $("topEmpty").textContent = "暂无进程数据（PowerShell 被禁用或非 Windows）";
    return;
  }
  const sig = JSON.stringify(top);
  if (sig === topSig) return;   // 进程表每 15s 才变化，避免高频重绘
  topSig = sig;
  $("topEmpty").style.display = "none";
  const total = (metrics.commit && metrics.commit.total) || 0;
  let html = "";
  top.slice(0, 20).forEach((x, i) => {
    const share = total > 0 ? (x.commit / total * 100) : 0;
    const lv = share >= 8 ? 2 : share >= 4 ? 1 : 0;
    const name = x.name || "未知进程";
    html += `<tr class="hot-lv${lv}">
      <td class="rank">${i + 1}</td>
      <td><div class="pname"><span class="icon">${esc(name[0] || "?")}</span>
        <span class="nm" title="${esc(name)}">${esc(name)}</span>
        <span class="pid">PID ${x.pid}</span></div></td>
      <td class="mb">${fmtB(x.commit)}</td>
      <td class="mb">${fmtB(x.peak)}</td>
      <td class="mb">${fmtB(x.ws)}</td>
      <td class="mb">${total > 0 ? share.toFixed(1) + "%" : "--"}</td></tr>`;
  });
  $("topBody").innerHTML = html;
}

// ---------- 趋势图 ----------
function drawChart() {
  const cv = $("chart"), ctx = cv.getContext("2d");
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;                       // 页面在别的 Tab 时画布不可见
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== w * dpr || cv.height !== h * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (mHistory.length < 2) {
    ctx.fillStyle = "#8b93a7"; ctx.font = "12px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("采集中，稍候绘制趋势曲线…", w / 2, h / 2);
    return;
  }
  const padL = 48, padR = 16, padT = 8, padB = 22;
  const iw = w - padL - padR, ih = h - padT - padB;
  const series = [mHistory.map((x) => ({ x: x.t, y: x.ca })), mHistory.map((x) => ({ x: x.t, y: x.pa }))];
  let maxY = 0;
  series.forEach((s) => s.forEach((pt) => { if (pt.y > maxY) maxY = pt.y; }));
  const yMaxGb = Math.max(1, Math.ceil(maxY / GB * 1.15 * 10) / 10);
  const t0 = mHistory[0].t, t1 = mHistory[mHistory.length - 1].t;
  const X = (t) => padL + (t - t0) / Math.max(1, t1 - t0) * iw;
  const Y = (v) => padT + ih - (v / GB) / yMaxGb * ih;

  ctx.strokeStyle = "rgba(255,255,255,0.06)"; ctx.fillStyle = "#8b93a7";
  ctx.font = "10px 'Cascadia Mono', Consolas, monospace"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const gb = yMaxGb * i / 4, y = Y(gb * GB);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(gb.toFixed(1), padL - 6, y);
  }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.fillText(fmtClock(t0), padL, h - padB + 4);
  ctx.fillText("现在 " + fmtClock(t1), w - padR, h - padB + 4);
  if (S.critAvailGb > 0 && S.critAvailGb <= yMaxGb) {
    const yc = Y(S.critAvailGb * GB);
    ctx.strokeStyle = "rgba(255,93,108,0.55)"; ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(padL, yc); ctx.lineTo(w - padR, yc); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(255,93,108,0.9)"; ctx.textAlign = "left";
    ctx.fillText("严重线 " + S.critAvailGb + "GB", padL + 6, yc - 8);
  }
  const colors = ["#38d39f", "#9b6bff"];
  series.forEach((s, si) => {
    ctx.strokeStyle = colors[si]; ctx.lineWidth = 1.8; ctx.lineJoin = "round";
    ctx.beginPath();
    s.forEach((pt, i) => { const x = X(pt.x), y = Y(pt.y); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
    ctx.stroke();
  });
}
window.addEventListener("resize", () => { if (state.curView === "sysmon") requestAnimationFrame(drawChart); });

// ---------- 轮询 ----------
export async function sysTick() {
  const now = Date.now();
  try {
    const res = await fetch("/api/stats", { cache: "no-store" });
    const d = await res.json();
    sysFail = 0;
    if (lastTick && now - lastTick > 45000) mHistory = [];   // 断流恢复，丢弃旧趋势
    lastTick = now;
    if (!d.ok) {
      setStatus("off", "数据源不可用", d.reason || "采集失败", "仅支持 Windows 环境。");
      return;
    }
    metrics = d;
    state.memLoaded = true;
    mHistory.push({ t: now, ca: d.commit.avail, pa: d.physical.avail });
    if (mHistory.length > MAX_POINTS) mHistory.shift();
    const ev = sysEvaluate(d);
    renderAll();
    if (state.curView === "sysmon") drawChart();
    applyLevel(ev);
  } catch (e) {
    sysFail++;
    if (sysFail >= 2) {
      setStatus("off", "与监控服务连接中断", "请确认服务端仍在运行；恢复后本页会自动重连。", "");
      stopTitleFlash();
    }
  }
}

function applyLevel(ev) {
  const isCrit = ev.level === "crit", isWarn = ev.level === "warn";
  const now = Date.now();
  const reasonTop = ev.crit[0] || ev.warn[0] || "";
  if (isCrit) {
    setStatus("crit", "严重：提交空间（页面文件）即将耗尽，可能复现进程崩溃", reasonTop || "内存异常，请尽快处理", TIP_CRIT);
    if (!activeCrit && now >= snoozeUntil) { activeCrit = true; fireAlarm(ev); }
    else if (now < snoozeUntil) startTitleFlash();
  } else if (isWarn) {
    setStatus("warn", "内存吃紧，请留意提交空间剩余", reasonTop || "", TIP_WARN);
    activeCrit = false;
    stopTitleFlash();
    if (ev.warn.length && prevLevel !== "warn") toast("预警：" + ev.warn[0], "warn");
  } else {
    setStatus("ok", "系统内存状态正常", buildOkDesc(), TIP_OK);
    activeCrit = false;
    stopTitleFlash();
  }
  prevLevel = memLevel;
}
function buildOkDesc() {
  const c = metrics.commit;
  if (!c || c.total <= 0) return "提交空间数据不可用";
  return "提交空间剩余 " + fmtSmart(c.avail) + "，占用 " + c.usedPct + "%；物理内存剩余 " + fmtSmart(metrics.physical.avail) + "。";
}
export function restartSysTimer() {
  if (sysTimer) clearInterval(sysTimer);
  sysTimer = setInterval(sysTick, Math.max(1, S.refreshSec) * 1000);
}

export { drawChart };

// ---------- 启动 ----------
syncSysSettingsUI();
restartSysTimer();
sysTick();
