// Base64 编解码 Tab：文本 ↔ Base64（标准 / URL 安全），纯前端。
import { $, toast } from "./common.js";

let b64Dir = "enc"; // enc=编码, dec=解码
let b64Var = "std"; // std=标准, url=URL安全
let lastOut = "";

function strToBin(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return bin;
}
function binToStr(bin) {
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
// 解码时自动兼容 URL 安全字符（- / _），并补齐 padding
function normalize(b64) {
  let s = b64.trim();
  if (s.includes("-") || s.includes("_")) s = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = s.length % 4;
  if (pad) s += "=".repeat(4 - pad);
  return s;
}

function run() {
  const raw = $("b64Input").value;
  const badge = $("b64Badge"), txt = $("b64BadgeTxt");
  if (!raw.trim()) {
    badge.className = "badge"; txt.textContent = "待输入";
    $("b64Output").textContent = ""; $("b64Err").style.display = "none"; lastOut = "";
    return;
  }
  try {
    let out;
    if (b64Dir === "enc") {
      let b = btoa(strToBin(raw));
      if (b64Var === "url") b = b.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      out = b;
    } else {
      out = binToStr(atob(normalize(raw)));
    }
    lastOut = out;
    badge.className = "badge st-ok";
    txt.textContent = (b64Dir === "enc" ? "已编码" : "已解码") + (b64Var === "url" ? " · URL安全" : "");
    $("b64Output").textContent = out;
    $("b64Err").style.display = "none";
  } catch (e) {
    lastOut = "";
    badge.className = "badge st-warn";
    txt.textContent = "处理失败";
    $("b64Output").textContent = "";
    $("b64Err").style.display = "block";
    $("b64Err").textContent = (b64Dir === "dec" ? "Base64 非法：" : "编码失败：") + (e.message || e);
  }
}

function setDir(d) {
  b64Dir = d;
  document.querySelectorAll("#b64DirSeg button").forEach((b) =>
    b.classList.toggle("active", b.dataset.dir === d));
  run();
}
function setVar(v) {
  b64Var = v;
  document.querySelectorAll("#b64VarSeg button").forEach((b) =>
    b.classList.toggle("active", b.dataset.var === v));
  run();
}

function copy() {
  if (!lastOut) { toast("没有可复制的结果", false); return; }
  navigator.clipboard.writeText(lastOut).then(() => toast("已复制", true)).catch(() => toast("复制失败", false));
}
function swap() {
  if (!lastOut) { toast("先生成结果再交换", false); return; }
  $("b64Input").value = lastOut;
  setDir(b64Dir === "enc" ? "dec" : "enc");
  // setDir 已触发 run()
}
function clearAll() {
  $("b64Input").value = "";
  run();
}

$("b64Input").addEventListener("input", run);
$("b64DirSeg").addEventListener("click", (e) => { const b = e.target.closest("button[data-dir]"); if (b) setDir(b.dataset.dir); });
$("b64VarSeg").addEventListener("click", (e) => { const b = e.target.closest("button[data-var]"); if (b) setVar(b.dataset.var); });
$("b64CopyBtn").onclick = copy;
$("b64SwapBtn").onclick = swap;
$("b64ClearBtn").onclick = clearAll;

export function loadB64() {
  const el = $("b64Input");
  if (el) el.focus();
}
