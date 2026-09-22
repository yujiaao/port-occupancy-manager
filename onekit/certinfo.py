# -*- coding: utf-8 -*-
"""HTTPS 证书检测 —— 直连目标端口读取 TLS 证书，并做信任 / 有效期 / 域名匹配校验。

实现只用标准库：
  * 证书字段来自 ssl 本身（`getpeercert()`），未受信任时用 `_test_decode_cert` 解 PEM；
  * 签名算法 / 公钥算法 / 密钥长度 `getpeercert()` 不提供，用文件末尾的极简 DER 读取器补上。

检测策略（两条链路）：
  1) 用系统 CA 做校验握手 —— 成功即「受信任」，并顺带拿到协议与加密套件；
  2) 若第 1 步失败（过期 / 自签名 / 域名不匹配 / 缺中间证书），再用不校验收敛一次
     把证书本身取回来，这样即便证书有问题也能把明细展示给用户。
"""
import datetime
import hashlib
import ipaddress
import os
import re
import socket
import ssl
import tempfile

DEFAULT_PORT = 443
DEFAULT_TIMEOUT = 10.0

# ---------------------------------------------------------------- OID 映射表
_SIG_ALGS = {
    "1.2.840.113549.1.1.4": "MD5withRSA",
    "1.2.840.113549.1.1.5": "SHA1withRSA",
    "1.2.840.113549.1.1.10": "RSASSA-PSS",
    "1.2.840.113549.1.1.11": "SHA256withRSA",
    "1.2.840.113549.1.1.12": "SHA384withRSA",
    "1.2.840.113549.1.1.13": "SHA512withRSA",
    "1.2.840.10045.4.1": "ECDSAwithSHA1",
    "1.2.840.10045.4.3.2": "ECDSAwithSHA256",
    "1.2.840.10045.4.3.3": "ECDSAwithSHA384",
    "1.2.840.10045.4.3.4": "ECDSAwithSHA512",
    "1.2.840.10040.4.3": "DSAwithSHA1",
    "1.3.101.112": "Ed25519",
    "1.3.101.113": "Ed448",
}
_KEY_ALGS = {
    "1.2.840.113549.1.1.1": "RSA",
    "1.2.840.10045.2.1": "EC",
    "1.2.840.10040.4.1": "DSA",
    "1.3.101.112": "Ed25519",
    "1.3.101.113": "Ed448",
}
_CURVES = {
    "1.2.840.10045.3.1.1": ("secp192r1", 192),
    "1.3.132.0.33": ("secp224r1", 224),
    "1.2.840.10045.3.1.7": ("prime256v1", 256),
    "1.3.132.0.10": ("secp256k1", 256),
    "1.3.132.0.34": ("secp384r1", 384),
    "1.3.132.0.35": ("secp521r1", 521),
}
# 弱算法（前端可据此提示风险）
_WEAK_SIG = {"MD5withRSA", "SHA1withRSA", "ECDSAwithSHA1", "DSAwithSHA1"}

_RDN_LABEL = {
    "commonName": "CN", "countryName": "C", "localityName": "L",
    "stateOrProvinceName": "ST", "organizationName": "O",
    "organizationalUnitName": "OU", "emailAddress": "E",
    "domainComponent": "DC", "serialNumber": "serialNumber",
}


# ------------------------------------------------------------ 极简 DER 读取
def _read_tlv(buf, pos):
    """读取一个 TLV，返回 (tag, value_start, value_len, next_pos)。"""
    if pos + 2 > len(buf):
        raise ValueError("DER 数据不足")
    tag = buf[pos]
    pos += 1
    first = buf[pos]
    pos += 1
    if first < 0x80:
        length = first
    else:
        n = first & 0x7F
        if n == 0 or n > 4 or pos + n > len(buf):
            raise ValueError("DER 长度非法")
        length = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    if pos + length > len(buf):
        raise ValueError("DER 长度越界")
    return tag, pos, length, pos + length


def _children(buf):
    """遍历一段 TLV 序列，yield (tag, value_bytes)。"""
    pos = 0
    while pos < len(buf):
        tag, start, length, nxt = _read_tlv(buf, pos)
        yield tag, buf[start:start + length]
        pos = nxt


def _oid_str(raw):
    """把 OID 的 DER 值转成 '1.2.840...' 字符串。"""
    if not raw:
        return ""
    first = raw[0]
    parts = [str(first // 40), str(first % 40)]
    val = 0
    for byte in raw[1:]:
        val = (val << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(str(val))
            val = 0
    return ".".join(parts)


def _parse_der_meta(der):
    """从证书 DER 中提取签名算法 / 公钥算法 / 密钥长度 / 序列号。失败返回 {}。"""
    out = {}
    try:
        tag, start, length, _ = _read_tlv(der, 0)
        if tag != 0x30:
            return out
        top = list(_children(der[start:start + length]))
        if len(top) < 3:
            return out
        tbs = top[0][1]
        # 外层签名算法
        for t, v in _children(top[1][1]):
            if t == 0x06:
                out["sigAlgOid"] = _oid_str(v)
                break

        kids = list(_children(tbs))
        i = 1 if kids and kids[0][0] == 0xA0 else 0   # version [0] EXPLICIT
        if i < len(kids) and kids[i][0] == 0x02:      # serialNumber
            out["serialHex"] = kids[i][1].hex().upper()
            i += 1
        i += 1                                        # tbs signature AlgorithmIdentifier
        i += 3                                        # issuer / validity / subject
        if i < len(kids) and kids[i][0] == 0x30:      # subjectPublicKeyInfo
            spki = list(_children(kids[i][1]))
            if spki and spki[0][0] == 0x30:
                alg = list(_children(spki[0][1]))
                if alg and alg[0][0] == 0x06:
                    out["keyAlgOid"] = _oid_str(alg[0][1])
                for t, v in alg[1:]:
                    if t == 0x06:                     # EC namedCurve
                        out["curveOid"] = _oid_str(v)
                        break
            if len(spki) >= 2 and spki[1][0] == 0x03:  # subjectPublicKey BIT STRING
                bit = spki[1][1]
                out["pubKeyBits"] = bit[1:] if bit[:1] == b"\x00" else bit
    except Exception:
        return out

    koid = out.get("keyAlgOid", "")
    out["keyAlg"] = _KEY_ALGS.get(koid, koid or None)
    if koid == "1.2.840.113549.1.1.1":                # RSA：模数位长即密钥长度
        try:
            # BIT STRING 里是 RSAPublicKey ::= SEQUENCE { modulus, exponent }，需下钻两层
            rsa = list(_children(out.get("pubKeyBits") or b""))[0][1]
            mod = list(_children(rsa))[0][1]
            out["keyBits"] = int.from_bytes(mod, "big").bit_length()
        except Exception:
            pass
    elif koid == "1.2.840.10045.2.1":                 # EC：取命名曲线位长
        curve = _CURVES.get(out.get("curveOid", ""))
        if curve:
            out["curve"], out["keyBits"] = curve
    elif koid == "1.3.101.112":
        out["keyBits"] = 256
    elif koid == "1.3.101.113":
        out["keyBits"] = 448
    out.pop("pubKeyBits", None)
    return out


# ---------------------------------------------------------------- 解析辅助
def split_target(target, port=None):
    """把 'example.com' / 'example.com:8443' / 'https://a.com/x' 解析为 (host, port)。"""
    t = (target or "").strip()
    if not t:
        return None, None
    if "://" in t:
        t = t.split("://", 1)[1]
    t = t.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not t:
        return None, None
    found_port = None
    if t.startswith("["):                              # IPv6 字面量 [::1]:443
        m = re.match(r"^\[(.+)\](?::(\d+))?$", t)
        if m:
            t, found_port = m.group(1), m.group(2)
    elif t.count(":") == 1:
        head, _, tail = t.partition(":")
        if tail.isdigit():
            t, found_port = head, tail
    if port:
        try:
            found_port = int(port)
        except (TypeError, ValueError):
            pass
    return t, int(found_port) if found_port else DEFAULT_PORT


def _flatten_rdn(rdn):
    """getpeercert() 的 subject/issuer 是嵌套元组，摊平成有序 [(label, value)]。"""
    out = []
    for part in rdn or ():
        for item in part:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                continue
            key, value = item
            out.append((_RDN_LABEL.get(key, key), str(value)))
    return out


def _rdn_text(pairs):
    return ", ".join(f"{k}={v}" for k, v in pairs)


def _decode_pem(pem_text):
    """用标准库解码器把 PEM 解成与 getpeercert() 同构的 dict；失败返回 None。"""
    decode = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decode is None:
        return None
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(pem_text)
        return decode(path)
    except Exception:
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _dns_match(pattern, host):
    """通配符只匹配一级子域：*.example.com 命中 a.example.com，不命中 a.b.example.com。"""
    pattern, host = pattern.lower(), host.lower()
    if pattern == host:
        return True
    if pattern.startswith("*."):
        return host.count(".") == pattern.count(".") and host.endswith(pattern[1:])
    return False


def _hostname_match(host, cert):
    """证书是否覆盖该主机名（优先 SAN，无 SAN 时回退 CN）。"""
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    sans = cert.get("subjectAltName") or ()
    if is_ip:
        return any(t == "IP Address" and v == host for t, v in sans)
    names = [v for t, v in sans if t == "DNS"]
    if not names:
        names = [v for k, v in _flatten_rdn(cert.get("subject")) if k == "CN"]
    return any(_dns_match(n, host) for n in names)


def _cert_time(value):
    """证书时间串 -> (datetime, 毫秒时间戳)；解析失败返回 (None, None)。"""
    try:
        ts = ssl.cert_time_to_seconds(value)
    except Exception:
        return None, None
    dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return dt, int(ts * 1000)


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else ""


def _fingerprint(der, algo):
    digest = hashlib.new(algo, der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _fetch(host, port, timeout, verify):
    """与目标握手，返回 (der, cert_dict, version, cipher, error)。"""
    if verify:
        ctx = ssl.create_default_context()
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as s:
            return (s.getpeercert(binary_form=True), s.getpeercert(),
                    s.version(), s.cipher(), None)


def _verify_error_text(err):
    """把 SSLCertVerificationError 转成可读中文说明。"""
    if isinstance(err, ssl.SSLCertVerificationError):
        name = getattr(err, "verify_message", "") or ""
        code = getattr(err, "verify_code", 0)
        # 取值对照 OpenSSL X509_V_ERR_*
        reason = {
            9: "证书尚未生效",
            10: "证书已过期",
            18: "自签名证书（不在系统信任列表中）",
            19: "证书链中的自签名证书",
            20: "无法获取本地签发者证书（可能缺少中间证书）",
            24: "签发者证书无效",
            62: "域名与证书不匹配",
        }.get(code, "")
        return (reason + ("；" if reason and name else "") + name) if (reason or name) else str(err)
    return str(err)


def inspect(target, port=None, timeout=DEFAULT_TIMEOUT):
    """检测目标 HTTPS 证书。返回统一的 dict（失败时 ok=False）。"""
    host, port = split_target(target, port)
    if not host:
        return {"ok": False, "error": "请输入域名或 URL"}
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = min(max(timeout, 1.0), 60.0)

    trusted, verify_error = False, None
    der = cert = version = cipher = None
    try:
        der, cert, version, cipher, _ = _fetch(host, port, timeout, verify=True)
        trusted = True
    except ssl.SSLCertVerificationError as e:
        verify_error = _verify_error_text(e)
    except ssl.SSLError as e:
        return {"ok": False, "host": host, "port": port,
                "error": f"TLS 握手失败（该端口可能不是 HTTPS）：{e}"}
    except socket.gaierror as e:
        return {"ok": False, "host": host, "port": port,
                "error": f"域名解析失败：{getattr(e, 'strerror', None) or e}"}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "host": host, "port": port,
                "error": f"连接超时（{timeout:g} 秒）：{host}:{port}"}
    except OSError as e:
        return {"ok": False, "host": host, "port": port,
                "error": f"无法连接 {host}:{port}：{getattr(e, 'strerror', None) or e}"}

    # 校验失败时再抓一次证书本体（不校验），保证明细仍可展示
    if der is None:
        try:
            der, cert, version, cipher, _ = _fetch(host, port, timeout, verify=False)
        except Exception as e:
            return {"ok": False, "host": host, "port": port,
                    "error": f"校验失败且无法读取证书：{verify_error or e}"}
        if not cert:
            cert = _decode_pem(ssl.DER_cert_to_PEM_cert(der)) or {}

    meta = _parse_der_meta(der)
    not_before, nb_ms = _cert_time(cert.get("notBefore"))
    not_after, na_ms = _cert_time(cert.get("notAfter"))
    now = datetime.datetime.now(datetime.timezone.utc)
    days_left = int((not_after - now).total_seconds() // 86400) if not_after else None
    subject = _flatten_rdn(cert.get("subject"))
    issuer = _flatten_rdn(cert.get("issuer"))
    sans = [f"{t}: {v}" for t, v in (cert.get("subjectAltName") or ())]
    serial = cert.get("serialNumber") or meta.get("serialHex", "")
    serial = ":".join(serial[i:i + 2] for i in range(0, len(serial), 2)) if serial else ""

    return {
        "ok": True,
        "host": host, "port": port,
        "trusted": trusted,
        "verifyError": verify_error,
        "hostnameMatch": _hostname_match(host, cert),
        "protocol": version, "cipher": (cipher or [None])[0],
        "cipherBits": (cipher or [None, None, None])[2],
        "subject": subject, "subjectText": _rdn_text(subject),
        "issuer": issuer, "issuerText": _rdn_text(issuer),
        "notBefore": _iso(not_before), "notAfter": _iso(not_after),
        "notBeforeTs": nb_ms, "notAfterTs": na_ms,
        "daysLeft": days_left,
        "expired": bool(not_after and now > not_after),
        "notYetValid": bool(not_before and now < not_before),
        "serial": serial,
        "version": cert.get("version") or 3,
        "san": sans,
        "ocsp": list(cert.get("OCSP") or ()),
        "caIssuers": list(cert.get("caIssuers") or ()),
        "crl": list(cert.get("crlDistributionPoints") or ()),
        "fingerprintSha256": _fingerprint(der, "sha256"),
        "fingerprintSha1": _fingerprint(der, "sha1"),
        "sigAlg": _SIG_ALGS.get(meta.get("sigAlgOid", ""), meta.get("sigAlgOid") or ""),
        "sigAlgOid": meta.get("sigAlgOid", ""),
        "weakSig": _SIG_ALGS.get(meta.get("sigAlgOid", "")) in _WEAK_SIG,
        "keyAlg": meta.get("keyAlg") or "",
        "keyBits": meta.get("keyBits"),
        "curve": meta.get("curve", ""),
        "pem": ssl.DER_cert_to_PEM_cert(der),
    }
