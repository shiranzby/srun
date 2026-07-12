#!/usr/bin/env python3
"""
SRunEncodeAccountV4D — PPPoE 用户名编码模块
===========================================
格式: {S4}<hex_hash>\r\n<hex_shifted_account><domain>
"""
import time


def srun_encode_v4(username: str, ts: int = None) -> bytes:
    """编码 PPPoE 认证用户名

    Args:
        username: 学号@域名 (如 24046224@stu.nchu.edu.cn)
        ts: 当天秒数 (小时*3600+分钟*60+秒), None=自动获取

    Returns:
        编码后的字节串 (latin-1)
    """
    if ts is None:
        now = time.localtime()
        ts = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec

    if "@" in username:
        account, rest = username.split("@", 1)
        domain = "@" + rest
    else:
        account = username
        domain = ""

    account_sum = sum(ord(c) for c in account)
    hash_val = account_sum + ts
    shifted_hex = "".join(f"{ord(c) + 4:02x}" for c in account)

    return f"{{S4}}{hash_val:x}\r\n{shifted_hex}{domain}".encode("latin-1")
