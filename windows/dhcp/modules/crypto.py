#!/usr/bin/env python3
"""
深澜 DHCP Portal 加密模块 v2
============================
100% 对照 BitSrunLoginGo 算法实现:
- srun 自定义 XXTEA (sensCode/lenCode 打包)
- 自定义 Base64 (srun alphabet)
- HMAC-MD5 密码哈希
- SHA1 校验和 (含尾随换行)
"""
import struct
import hashlib
import hmac
import math

SRUN_BASE64_ALPHABET = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"


# ═══════════════════════════════════════════════════════
#  自定义 Base64 (与 Go XBase64.go 完全一致)
# ═══════════════════════════════════════════════════════

def srun_base64_encode(data: bytes) -> str:
    if not data:
        return ""
    result = []
    imax = len(data) - len(data) % 3
    for i in range(0, imax, 3):
        b10 = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        result.append(SRUN_BASE64_ALPHABET[b10 >> 18])
        result.append(SRUN_BASE64_ALPHABET[(b10 >> 12) & 63])
        result.append(SRUN_BASE64_ALPHABET[(b10 >> 6) & 63])
        result.append(SRUN_BASE64_ALPHABET[b10 & 63])
    i = imax
    if len(data) - imax == 1:
        b10 = data[i] << 16
        result.append(SRUN_BASE64_ALPHABET[b10 >> 18])
        result.append(SRUN_BASE64_ALPHABET[(b10 >> 12) & 63])
        result.append("=")
        result.append("=")
    elif len(data) - imax == 2:
        b10 = (data[i] << 16) | (data[i + 1] << 8)
        result.append(SRUN_BASE64_ALPHABET[b10 >> 18])
        result.append(SRUN_BASE64_ALPHABET[(b10 >> 12) & 63])
        result.append(SRUN_BASE64_ALPHABET[(b10 >> 6) & 63])
        result.append("=")
    return "".join(result)


# ═══════════════════════════════════════════════════════
#  XXTEA — 严格对照 Go sensCode/lenCode + 修改版算法
# ═══════════════════════════════════════════════════════

def _bytes_to_u32(data: bytes, append_len: bool) -> list:
    """Go sensCode: 将 bytes 打包为 uint32 数组"""
    result = []
    l = len(data)
    for i in range(0, l, 4):
        v = data[i] if i < l else 0
        v |= (data[i + 1] << 8) if i + 1 < l else 0
        v |= (data[i + 2] << 16) if i + 2 < l else 0
        v |= (data[i + 3] << 24) if i + 3 < l else 0
        result.append(v)
    if append_len:
        result.append(l)
    return result


def _u32_to_bytes(arr: list, has_len: bool) -> bytes:
    """Go lenCode: 将 uint32 数组解包为 bytes"""
    t = bytearray()
    for v in arr:
        t.append(v & 0xFF)
        t.append((v >> 8) & 0xFF)
        t.append((v >> 16) & 0xFF)
        t.append((v >> 24) & 0xFF)
    if has_len:
        orig = arr[-1] if arr else 0
        if orig <= len(t):
            return bytes(t[:orig])
        return bytes(t)
    return bytes(t)


def xxtea_encrypt(data: bytes, key: bytes) -> bytes:
    """srun 修改版 XXTEA (与 Go XEncode.go 一致)"""
    if not data:
        return b""

    pwd = _bytes_to_u32(data, append_len=True)       # sensCode(data, true)
    pwdk = _bytes_to_u32(key, append_len=False)       # sensCode(key, false)

    while len(pwdk) < 4:
        pwdk.append(0)

    n = len(pwd) - 1
    if n < 1:
        return b""

    z = pwd[n]
    y = pwd[0]
    c = 0x9E3779B9
    m = e = p = 0
    q = int(math.floor(6 + 52 / (n + 1)))
    d = 0

    while q > 0:
        d = (d + c) & 0xFFFFFFFF
        e = (d >> 2) & 3
        p = 0
        while p < n:
            y = pwd[p + 1]
            m = (z >> 5) ^ (y << 2)
            m = m + (((y >> 3) ^ (z << 4)) ^ (d ^ y))
            m = m + (pwdk[(p & 3) ^ e] ^ z)
            pwd[p] = (pwd[p] + m) & 0xFFFFFFFF
            z = pwd[p]
            p += 1
        y = pwd[0]
        m = (z >> 5) ^ (y << 2)
        m = m + (((y >> 3) ^ (z << 4)) ^ (d ^ y))
        m = m + (pwdk[(p & 3) ^ e] ^ z)
        pwd[n] = (pwd[n] + m) & 0xFFFFFFFF
        z = pwd[n]
        q -= 1

    return _u32_to_bytes(pwd, has_len=False)  # lenCode(pwd, false)


# ═══════════════════════════════════════════════════════
#  认证函数 — 严格对照 Go srun.go + encode.go
# ═══════════════════════════════════════════════════════

def xencode(data_str: str, key_str: str) -> str:
    """XXTEA + 自定义 Base64"""
    encrypted = xxtea_encrypt(data_str.encode("utf-8"), key_str.encode("utf-8"))
    return srun_base64_encode(encrypted)


def srun_md5(token: str, password: str) -> str:
    """Go Md5: HMAC-MD5(key=token, msg=password) → hex"""
    mac = hmac.new(token.encode("utf-8"), password.encode("utf-8"), hashlib.md5)
    return mac.hexdigest()


def srun_sha1(content: str) -> str:
    """Go Sha1: SHA1 → hex + trailing newline"""
    h = hashlib.sha1(content.encode("utf-8")).hexdigest()
    return h + "\n"


def srun_password_hash(token: str, password: str) -> str:
    """{MD5}<HMAC-MD5(token, password)>"""
    return "{MD5}" + srun_md5(token, password)


def srun_chksum(token: str, username: str, md5_str: str,
                ac_id: str, ip: str, n: str, type_: str, info: str) -> str:
    """Go chksum: Sha1(token+user+token+md5+token+acid+token+ip+token+n+token+type+token+info)"""
    joined = (token + username + token + md5_str + token + ac_id +
              token + ip + token + n + token + type_ + token + info)
    return srun_sha1(joined)


def srun_info_prefix(prefix: str) -> str:
    """Go: fmt.Sprintf("{%s}", prefix) — 如 "SRBX1" → "{SRBX1}" """
    return "{" + prefix + "}"
