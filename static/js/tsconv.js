// Timestamp 转日期 Tab：时间戳(秒/毫秒) ↔ 日期 双向转换，纯前端。
import { $ } from "./common.js";

const WD = ["日", "一", "二", "三", "四", "五", "六"];
const pad = (n) => String(n).padStart(2, "0");

function toMs(value, unit) {
  if (unit === "s") return value * 1000;
  if (unit === "ms") return value;
  // 自动：当前纪元下毫秒通常 >= 1e12，秒 < 1e12
  return Math.abs(value) >= 1e12 ? value : value * 1000;
}

function relTime(d) {
  const diff = d.getTime() - Date.now();
  const abs = Math.abs(diff);
  const units = [
    [31536000000, "年"], [2592000000, "个月"], [86400000, "天"],
    [3600000, "小时"], [60000, "分钟"], [1000, "秒"],
  ];
  for (const [ms, name] of units) {
    if (abs >= ms) {
      const n = Math.floor(abs / ms);
      return diff >= 0 ? `未来 ${n}${name}后` : `${n}${name}前`;
    }
  }
  return "刚刚";
}

// 渲染一行行结果，data-copy 指向 value 元素 id
function renderRows(container, rows) {
  container.innerHTML = "";
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "ts-row";
    const lab = document.createElement("span");
    lab.className = "ts-label"; lab.textContent = r.label;
    const val = document.createElement("span");
    val.className = "ts-val"; val.id = r.id; val.textContent = r.value;
    const btn = document.createElement("button");
    btn.className = "btn ghost"; btn.dataset.copy = r.id; btn.textContent = "复制";
    row.append(lab, val, btn);
    container.appendChild(row);
  }
  container.style.display = "block";
}

function convert() {
  const raw = ($("tsInput").value || "").trim();
  const badge = $("tsBadge"), txt = $("tsBadgeTxt");
  if (!raw) { badge.className = "badge"; txt.textContent = "待输入"; $("tsOut").style.display = "none"; return; }
  const num = Number(raw);
  if (!isFinite(num)) { badge.className = "badge st-warn"; txt.textContent = "请输入数字"; $("tsOut").style.display = "none"; return; }
  const ms = toMs(num, $("tsUnit").value);
  const d = new Date(ms);
  if (isNaN(d.getTime())) { badge.className = "badge st-warn"; txt.textContent = "时间戳越界"; $("tsOut").style.display = "none"; return; }

  badge.className = "badge st-ok";
  txt.textContent = ($("tsUnit").value === "auto" ? "自动识别 · " : "") + (Math.abs(num) >= 1e12 ? "毫秒" : "秒");
  renderRows($("tsOut"), [
    { label: "本地时间", id: "tsLocal", value: d.toLocaleString() },
    { label: "UTC", id: "tsUtc", value: d.toUTCString() },
    { label: "ISO 8601", id: "tsIso", value: d.toISOString() },
    { label: "日期 (本地)", id: "tsDate", value: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` },
    { label: "时间 (本地)", id: "tsTime", value: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` },
    { label: "星期", id: "tsWeek", value: "星期" + WD[d.getDay()] },
    { label: "相对现在", id: "tsRel", value: relTime(d) },
    { label: "对应秒", id: "tsSec", value: String(Math.floor(ms / 1000)) },
    { label: "对应毫秒", id: "tsMs", value: String(ms) },
  ]);
}

function dateToStamp() {
  const raw = ($("tsDate").value || "").trim();
  const badge = $("tsBadge"), txt = $("tsBadgeTxt");
  if (!raw) { toast("请输入日期", false); return; }
  let t = Date.parse(raw);
  if (isNaN(t)) t = Date.parse(raw.replace(" ", "T")); // 容错：空格当 T
  if (isNaN(t)) {
    badge.className = "badge st-warn"; txt.textContent = "日期无法解析";
    $("tsOut2").style.display = "none"; return;
  }
  badge.className = "badge st-ok"; txt.textContent = "日期 → 时间戳";
  renderRows($("tsOut2"), [
    { label: "秒 (s)", id: "ts2Sec", value: String(Math.floor(t / 1000)) },
    { label: "毫秒 (ms)", id: "ts2Ms", value: String(t) },
    { label: "解析为", id: "ts2Iso", value: new Date(t).toISOString() },
  ]);
}

function fillNow() {
  const now = Date.now();
  $("tsInput").value = String(now);
  convert();
}

$("tsInput").addEventListener("input", convert);
$("tsUnit").addEventListener("change", convert);
$("tsNowBtn").onclick = fillNow;
$("tsToStampBtn").onclick = dateToStamp;

export function loadTsConv() {
  const el = $("tsInput");
  if (el) el.focus();
}
