// UTF-8 转义 Tab：文本 ↔ \uXXXX（Unicode 转义）/ \xHH（UTF-8 字节转义），纯前端。
import { $, toast } from "./common.js";

let uDir = "enc"; // enc=编码, dec=解码
let uFmt = "uni"; // uni=\uXXXX, byte=\xHH
let lastOut = "";

// 文本 → \uXXXX（可打印 ASCII 保持原样，其余按码点转义，含代理对）
function encUni(str) {
  let out = "";
  for (const ch of str) {
    const c = ch.charCodeAt(0);
    if (c >= 0x20 && c <= 0x7e) {
      out += ch === "\\" ? "\\\\" : ch;
      continue;
    }
    const cp = ch.codePointAt(0);
    if (cp <= 0xffff) {
      out += "\\u" + cp.toString(16).toUpperCase().padStart(4, "0");
    } else {
      const hi = Math.floor((cp - 0x10000) / 0x400) + 0xd800;
      const lo = ((cp - 0x10000) % 0x400) + 0xdc00;
      out += "\\u" + hi.toString(16).toUpperCase().padStart(4, "0") +
             "\\u" + lo.toString(16).toUpperCase().padStart(4, "0");
    }
  }
  return out;
}

// 文本 → UTF-8 字节 \xHH
function encByte(str) {
  const bytes = new TextEncoder().encode(str);
  let out = "";
  for (const b of bytes) out += "\\x" + b.toString(16).toUpperCase().padStart(2, "0");
  return out;
}

// 转义 → 文本（\uXXXX 或 \u{...}）
function decUni(str) {
  if (/\\u(?!([0-9A-Fa-f]{4}|\{[0-9A-Fa-f]+\}))/i.test(str)) {
    throw new Error("\\u 后需为 4 位十六进制 或 \\u{...}");
  }
  return str
    .replace(/\\u\{([0-9A-Fa-f]+)\}/gi, (m, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/\\u([0-9A-Fa-f]{4})/gi, (m, h) => String.fromCharCode(parseInt(h, 16)));
}

// 转义 → 文本（\xHH，UTF-8 字节）
function decByte(str) {
  const bytes = [];
  let i = 0;
  while (i < str.length) {
    if (str[i] === "\\" && str[i + 1] === "x") {
      const hex = str.substr(i + 2, 2);
      if (!/^[0-9A-Fa-f]{2}$/.test(hex)) throw new Error("非法的 \\xHH：" + str.substr(i, 4));
      bytes.push(parseInt(hex, 16));
      i += 4;
    } else {
      const b = new TextEncoder().encode(str[i]);
      for (const x of b) bytes.push(x);
      i += 1;
    }
  }
  return new TextDecoder().decode(new Uint8Array(bytes));
}

function run() {
  const raw = $("utf8Input").value;
  const badge = $("utf8Badge"), txt = $("utf8BadgeTxt");
  if (!raw.trim()) {
    badge.className = "badge"; txt.textContent = "待输入";
    $("utf8Output").textContent = ""; $("utf8Err").style.display = "none"; lastOut = "";
    return;
  }
  try {
    let out;
    if (uDir === "enc") out = uFmt === "uni" ? encUni(raw) : encByte(raw);
    else out = uFmt === "uni" ? decUni(raw) : decByte(raw);
    lastOut = out;
    badge.className = "badge st-ok";
    txt.textContent = (uDir === "enc" ? "已编码" : "已解码") + (uFmt === "uni" ? " · \\uXXXX" : " · \\xHH");
    $("utf8Output").textContent = out;
    $("utf8Err").style.display = "none";
  } catch (e) {
    lastOut = "";
    badge.className = "badge st-warn"; txt.textContent = "处理失败";
    $("utf8Output").textContent = "";
    $("utf8Err").style.display = "block";
    $("utf8Err").textContent = (uDir === "dec" ? "转义非法：" : "编码失败：") + (e.message || e);
  }
}

function setDir(d) {
  uDir = d;
  document.querySelectorAll("#utf8DirSeg button").forEach((b) => b.classList.toggle("active", b.dataset.dir === d));
  run();
}
function setFmt(f) {
  uFmt = f;
  document.querySelectorAll("#utf8FmtSeg button").forEach((b) => b.classList.toggle("active", b.dataset.fmt === f));
  run();
}

function copy() {
  if (!lastOut) { toast("没有可复制的结果", false); return; }
  navigator.clipboard.writeText(lastOut).then(() => toast("已复制", true)).catch(() => toast("复制失败", false));
}
function swap() {
  if (!lastOut) { toast("先生成结果再交换", false); return; }
  $("utf8Input").value = lastOut;
  setDir(uDir === "enc" ? "dec" : "enc");
}
function clearAll() {
  $("utf8Input").value = "";
  run();
}

$("utf8Input").addEventListener("input", run);
$("utf8DirSeg").addEventListener("click", (e) => { const b = e.target.closest("button[data-dir]"); if (b) setDir(b.dataset.dir); });
$("utf8FmtSeg").addEventListener("click", (e) => { const b = e.target.closest("button[data-fmt]"); if (b) setFmt(b.dataset.fmt); });
$("utf8CopyBtn").onclick = copy;
$("utf8SwapBtn").onclick = swap;
$("utf8ClearBtn").onclick = clearAll;

export function loadUtf8() {
  const el = $("utf8Input");
  if (el) el.focus();
}
