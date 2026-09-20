// JSON 格式化 Tab：格式化 / 压缩 / 校验，可选排序键，错误定位到行列，支持复制 / 下载 / 粘贴。
import { $, toast, syntaxHighlight, download } from "./common.js";

// 根据字符位置计算行/列，便于报出 JSON 解析错误的精确位置
function locate(text, pos) {
  let line = 1, col = 0;
  for (let i = 0; i < pos && i < text.length; i++) {
    if (text[i] === "\n") { line++; col = 0; } else col++;
  }
  return { line, col };
}

// 递归按 key 字典序排序对象（数组保持原序）
function deepSort(obj) {
  if (Array.isArray(obj)) return obj.map(deepSort);
  if (obj && typeof obj === "object") {
    const out = {};
    for (const k of Object.keys(obj).sort()) out[k] = deepSort(obj[k]);
    return out;
  }
  return obj;
}

function showError(badge, txt, raw, msg) {
  badge.className = "badge st-warn";
  txt.textContent = "解析失败";
  $("jsonErr").style.display = "block";
  $("jsonErr").textContent = msg + "\n" + raw;
  $("jsonOutput").innerHTML = "";
}

// mode: "format" | "minify"
function run(mode) {
  const raw = $("jsonInput").value;
  const badge = $("jsonBadge"), txt = $("jsonBadgeTxt");
  if (!raw.trim()) {
    badge.className = "badge"; txt.textContent = "待输入";
    $("jsonOutput").innerHTML = ""; $("jsonErr").style.display = "none";
    return;
  }
  let obj;
  try {
    obj = JSON.parse(raw);
  } catch (e) {
    const m = e.message || String(e);
    let pos = null, line = null, col = null;
    const pm = m.match(/position (\d+)/);
    if (pm) pos = +pm[1];
    const lm = m.match(/line (\d+) column (\d+)/) || m.match(/at line (\d+) column (\d+)/);
    if (lm) { line = +lm[1]; col = +lm[2]; }
    else if (pos != null) { const lc = locate(raw, pos); line = lc.line; col = lc.col; }
    let msg = "JSON 解析失败";
    if (line != null) msg += `：第 ${line} 行第 ${col} 列附近`;
    else if (pos != null) msg += `：位置 ${pos} 附近`;
    showError(badge, txt, m, msg);
    return;
  }

  if ($("jsonSort").checked) obj = deepSort(obj);
  let out;
  if (mode === "minify") out = JSON.stringify(obj);
  else {
    const ind = $("jsonIndent").value === "4" ? "    " : $("jsonIndent").value === "tab" ? "\t" : "  ";
    out = JSON.stringify(obj, null, ind);
  }
  badge.className = "badge st-ok";
  txt.textContent = mode === "minify" ? "已压缩" : "已格式化";
  $("jsonErr").style.display = "none";
  $("jsonOutput").innerHTML = syntaxHighlight(out);
}

function validate() {
  const raw = $("jsonInput").value;
  const badge = $("jsonBadge"), txt = $("jsonBadgeTxt");
  if (!raw.trim()) { badge.className = "badge"; txt.textContent = "待输入"; return; }
  try {
    JSON.parse(raw);
    badge.className = "badge st-ok"; txt.textContent = "校验通过 · 合法 JSON";
    $("jsonErr").style.display = "none";
  } catch (e) {
    const m = e.message || String(e);
    let pos = null, line = null, col = null;
    const pm = m.match(/position (\d+)/);
    if (pm) pos = +pm[1];
    const lm = m.match(/line (\d+) column (\d+)/) || m.match(/at line (\d+) column (\d+)/);
    if (lm) { line = +lm[1]; col = +lm[2]; }
    else if (pos != null) { const lc = locate(raw, pos); line = lc.line; col = lc.col; }
    let msg = "JSON 不合法";
    if (line != null) msg += `：第 ${line} 行第 ${col} 列附近`;
    else if (pos != null) msg += `：位置 ${pos} 附近`;
    showError(badge, txt, m, msg);
  }
}

async function paste() {
  try {
    const t = await navigator.clipboard.readText();
    $("jsonInput").value = t;
    toast("已从剪贴板粘贴", true);
  } catch (e) {
    toast("无法读取剪贴板（需 https 或 localhost 授权）", false);
  }
}

function copy() {
  const t = $("jsonOutput").textContent;
  if (!t) { toast("没有可复制的结果", false); return; }
  navigator.clipboard.writeText(t).then(() => toast("已复制", true)).catch(() => toast("复制失败", false));
}

function dl() {
  const t = $("jsonOutput").textContent;
  if (!t) { toast("没有可下载的结果", false); return; }
  download("formatted.json", t, "application/json");
}

$("jsonFormatBtn").onclick = () => run("format");
$("jsonMinifyBtn").onclick = () => run("minify");
$("jsonValidateBtn").onclick = validate;
$("jsonPasteBtn").onclick = paste;
$("jsonCopyBtn").onclick = copy;
$("jsonDownloadBtn").onclick = dl;

export function loadJsonFmt() {
  const el = $("jsonInput");
  if (el) el.focus();
}
