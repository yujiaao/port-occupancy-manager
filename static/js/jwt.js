// JWT 解密 Tab：解码 header/payload，展示时间声明状态，可选密钥做本地签名校验。
// 全程在浏览器内完成，token 不上传任何服务器。
import { $, toast, syntaxHighlight } from "./common.js";

let jwtState = null; // { header, payload, h, p, s, alg }

// ---------- base64url 工具 ----------
function b64urlToBytes(str) {
  str = str.replace(/-/g, "+").replace(/_/g, "/");
  const pad = str.length % 4;
  if (pad) str += "=".repeat(4 - pad);
  const bin = atob(str);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function b64urlDecodeUtf8(str) {
  return new TextDecoder().decode(b64urlToBytes(str));
}

function pemToDer(pem) {
  const b64 = pem.replace(/-----BEGIN PUBLIC KEY-----/g, "")
                 .replace(/-----END PUBLIC KEY-----/g, "")
                 .replace(/\s+/g, "");
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

// ---------- 时间辅助 ----------
function fmtTime(v) {
  if (typeof v !== "number") return String(v);
  let ms = v < 1e12 ? v * 1000 : v; // JWT 用秒，部分实现用毫秒
  const d = new Date(ms);
  if (isNaN(d.getTime())) return String(v);
  return d.toLocaleString();
}

// ---------- 签名校验（Web Crypto）----------
async function verifyHS(alg, dataBytes, sigBytes, secretText) {
  const hash = "SHA-" + alg.slice(2); // HS256 -> SHA-256
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secretText),
    { name: "HMAC", hash: { name: hash } }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, dataBytes));
  if (mac.length !== sigBytes.length) return false;
  let ok = 1;
  for (let i = 0; i < mac.length; i++) ok &= mac[i] === sigBytes[i];
  return ok === 1;
}

async function verifyAsym(alg, dataBytes, sigBytes, pem) {
  const HASH = { RS256: "SHA-256", RS384: "SHA-384", RS512: "SHA-512",
    PS256: "SHA-256", PS384: "SHA-384", PS512: "SHA-512",
    ES256: "SHA-256", ES384: "SHA-384", ES512: "SHA-512" };
  const hash = HASH[alg];
  let name, params;
  if (alg.startsWith("RS")) name = "RSASSA-PKCS1-v1_5";
  else if (alg.startsWith("PS")) name = "RSA-PSS";
  else name = "ECDSA";
  const key = await crypto.subtle.importKey(
    "spki", pemToDer(pem), { name }, false, ["verify"]);
  params = { name };
  if (name === "RSA-PSS") params.saltLength = alg === "PS256" ? 32 : alg === "PS384" ? 48 : 64;
  else if (name === "ECDSA") params.hash = { name: hash };
  return crypto.subtle.verify(params, key, sigBytes, dataBytes);
}

function showVerify(msg, ok) {
  const b = $("jwtVerifyBadge");
  b.style.display = "inline-flex";
  b.className = "badge " + (ok ? "st-ok" : "st-warn");
  $("jwtVerifyTxt").textContent = msg;
}

// ---------- 渲染 ----------
function renderClaims(header, payload) {
  const now = Math.floor(Date.now() / 1000);
  const items = [];
  items.push(["算法 alg", header.alg ?? "(未知)", "c1"]);
  items.push(["类型 typ", header.typ ?? "—", "c2"]);
  if (payload.iss != null) items.push(["签发者 iss", String(payload.iss), "c2"]);
  if (payload.sub != null) items.push(["主题 sub", String(payload.sub), "c2"]);
  if (payload.aud != null) items.push(["受众 aud", String(payload.aud), "c2"]);
  if (payload.iat != null) items.push(["签发时间 iat", fmtTime(payload.iat), "c3"]);
  if (payload.nbf != null) items.push(["生效时间 nbf", fmtTime(payload.nbf), "c3"]);
  if (payload.exp != null) items.push(["过期时间 exp", fmtTime(payload.exp), "c3"]);

  let status = "有效", scls = "st-ok";
  if (typeof payload.exp === "number" && now > payload.exp) { status = "已过期"; scls = "st-warn"; }
  if (typeof payload.nbf === "number" && now < payload.nbf) { status = "尚未生效"; scls = "st-warn"; }
  if (header.alg === "none") { status = "未签名 (alg=none)"; scls = "st-warn"; }
  items.push(["状态", status, scls === "st-ok" ? "c4" : "c4"]);

  const box = $("jwtClaims");
  box.innerHTML = "";
  for (const [k, v, c] of items) {
    const el = document.createElement("div");
    el.className = "stat " + c;
    el.innerHTML = '<div class="k">' + escHtml(k) + '</div><div class="v">' + escHtml(String(v)) + "</div>";
    box.appendChild(el);
  }
}

function escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt");
}

function parseJwt() {
  const token = ($("jwtInput").value || "").replace(/\s+/g, "");
  const badge = $("jwtBadge"), txt = $("jwtBadgeTxt");
  jwtState = null;
  if (!token) {
    $("jwtResult").style.display = "none";
    badge.className = "badge"; txt.textContent = "待输入";
    return;
  }
  const parts = token.split(".");
  if (parts.length !== 3) {
    badge.className = "badge st-warn"; txt.textContent = "格式错误";
    $("jwtResult").style.display = "none";
    toast("JWT 应为 header.payload.signature 三段，用 . 分隔", false);
    return;
  }
  const [h, p, s] = parts;
  let header, payload;
  try { header = JSON.parse(b64urlDecodeUtf8(h)); }
  catch (e) { badge.className = "badge st-warn"; txt.textContent = "Header 解码失败"; toast("Header 不是合法 JSON", false); return; }
  try { payload = JSON.parse(b64urlDecodeUtf8(p)); }
  catch (e) { badge.className = "badge st-warn"; txt.textContent = "Payload 解码失败"; toast("Payload 不是合法 JSON", false); return; }

  badge.className = "badge st-ok"; txt.textContent = "已解码";
  $("jwtResult").style.display = "block";
  renderClaims(header, payload);
  $("jwtHeader").innerHTML = syntaxHighlight(JSON.stringify(header, null, 2));
  $("jwtPayload").innerHTML = syntaxHighlight(JSON.stringify(payload, null, 2));

  const alg = header.alg || "(未知)";
  let hint = "算法 alg = " + alg;
  if (header.typ) hint += "，类型 typ = " + header.typ;
  if (alg === "none") hint += " —— 注意：alg=none 表示未签名，可被任意篡改";
  $("jwtAlgHint").textContent = hint;
  $("jwtVerifyBadge").style.display = "none";
  jwtState = { header, payload, h, p, s, alg };
}

async function doVerify() {
  if (!jwtState) { toast("请先填入有效 JWT", false); return; }
  const { alg, h, p, s } = jwtState;
  if (alg === "none") {
    showVerify(s ? "签名段非空但 alg=none，视为无效" : "alg=none：未签名（不验证）", !s);
    return;
  }
  if (!crypto.subtle) {
    showVerify("当前环境不支持 Web Crypto，无法校验签名（需 https 或 localhost）", false);
    return;
  }
  const secret = ($("jwtSecret").value || "").trim();
  if (!secret) { toast("请输入密钥（HS*）或 PEM 公钥（RS*/ES*/PS*）", false); return; }

  const dataBytes = new TextEncoder().encode(`${h}.${p}`);
  let sigBytes;
  try { sigBytes = b64urlToBytes(s); }
  catch (e) { showVerify("签名段解码失败", false); return; }

  let res;
  try {
    if (alg.startsWith("HS")) res = await verifyHS(alg, dataBytes, sigBytes, secret);
    else res = await verifyAsym(alg, dataBytes, sigBytes, secret);
  } catch (e) {
    showVerify("校验失败：" + e.message, false);
    return;
  }
  showVerify(res ? "✓ 签名校验通过" : "✗ 校验失败：密钥/公钥不匹配或 token 已被篡改", res);
}

// 复制按钮由 common.js 全局委托处理（data-copy 指向元素 id）
$("jwtInput").addEventListener("input", parseJwt);
$("jwtVerifyBtn").onclick = doVerify;

export function loadJwt() {
  const el = $("jwtInput");
  if (el) el.focus();
}
