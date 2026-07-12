#!/usr/bin/env python3
"""
深澜 DHCP Portal 工具模块 v3
============================
纯 Python urllib, 零外部依赖
v3: 重写 IP 获取 (ipconfig 解析), 添加流量统计, 添加 IPv6 获取
"""
import urllib.request
import urllib.parse
import json
import subprocess
import re
import time


def http_get_json(url: str, timeout: int = 5) -> dict:
    """HTTP GET, 解析 JSONP 响应 (callback 剥离)"""
    req = urllib.request.Request(url)
    req.add_header("User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/89.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"_error": str(e)}

    # 剥离 JSONP callback: "jQuery(...);" → 提取括号内 JSON
    if "(" in body and ")" in body:
        start = body.index("(") + 1
        end = body.rindex(")")
        body = body[start:end]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_error": "JSON parse error", "_body": body[:200]}


# ── IPv4 获取 ────────────────────────────────────────
# 根因: 原实现依赖 PowerShell Get-NetIPConfiguration, 在某些系统/权限下失效
# 修复: 直接解析 ipconfig 输出, 按适配器块提取默认网关所在块的 IPv4

def _get_ipconfig_output() -> str:
    """获取 ipconfig /all 输出 (GBK 解码)"""
    try:
        r = subprocess.run(["ipconfig", "/all"], capture_output=True, timeout=5)
        return (r.stdout or b"").decode("gbk", errors="replace")
    except Exception:
        return ""


def get_local_ipv4() -> str:
    """获取本机 IPv4 (默认网关所在适配器)"""
    output = _get_ipconfig_output()
    if not output:
        return "0.0.0.0"

    # 按行扫描, 状态机方式解析适配器块
    # 适配中文/英文 Windows
    current_adapter = ""
    in_default_gateway_block = False
    best_ipv4 = ""

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 适配器名行: "以太网适配器 Ethernet 0:" 或 "Ethernet adapter Ethernet 0:"
        if line.endswith(":") and ("适配器" in line or "adapter" in line.lower()):
            current_adapter = line.rstrip(":")
            # 重置: 新适配器块
            in_default_gateway_block = False
            continue

        # 检测默认网关行 (中文: "默认网关"; 英文: "Default Gateway")
        if "默认网关" in line or "Default Gateway" in line:
            in_default_gateway_block = True
            # 同一行可能附带 IP: "默认网关 . . . . . . . : 10.1.88.100"
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
            if ip_match:
                # 同一行有 IP, 说明网关就是这条线, 直接取该适配器 IPv4
                pass  # 继续扫该块内的 IPv4 地址行
            continue

        # 在默认网关块内, 找 IPv4 地址
        if in_default_gateway_block:
            # IPv4 地址行: "IPv4 地址 . . . . . . . : 10.1.88.100" (中文)
            #             或 "IPv4 Address. . . . . . . . : 10.1.88.100" (英文)
            # 注意: 行内可能有 "(首选)" 或 "(Preferred)"
            ipv4_match = re.search(
                r'IPv4[^\n:]*[:：]\s*(\d+\.\d+\.\d+\.\d+)',
                line, re.IGNORECASE
            )
            if ipv4_match:
                best_ipv4 = ipv4_match.group(1)
                break  # 找到第一个默认网关块的 IPv4 即可

    if best_ipv4:
        return best_ipv4

    # 降级: 用 socket 连接外网获取本地 IP
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass

    return "0.0.0.0"


def get_local_ipv6() -> str:
    """获取本机 IPv6 (默认网关所在适配器, 排除 % 后缀的链路本地地址)"""
    output = _get_ipconfig_output()
    if not output:
        return "-"

    current_adapter = ""
    in_default_gateway_block = False
    best_ipv6 = "-"

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.endswith(":") and ("适配器" in line or "adapter" in line.lower()):
            current_adapter = line.rstrip(":")
            in_default_gateway_block = False
            continue

        if "默认网关" in line or "Default Gateway" in line:
            # 网关行本身可能有 IPv6
            # 格式: "默认网关 . . . . . . . : [::]" 或实际 IPv6
            in_default_gateway_block = True
            continue

        if in_default_gateway_block:
            # IPv6 地址行
            ipv6_match = re.search(
                r'IPv6[^\n:]*[:：]\s*([0-9a-fA-F:]+)',
                line, re.IGNORECASE
            )
            if ipv6_match:
                v6 = ipv6_match.group(1).strip()
                # 排除链路本地 (fe80::%) 和 ::1
                if "%" not in v6 and not v6.startswith("::1"):
                    best_ipv6 = v6
                    break

    return best_ipv6


# ── 流量统计 ────────────────────────────────────────
# 复用 PPPoE 版的逻辑: netsh interface ipv4 show subinterfaces

def get_iface_bytes() -> tuple:
    """获取当前活动网络适配器的收发字节数 (rx, tx)
    通过 netsh interface ipv4 show subinterfaces 获取
    返回 (rx_bytes, tx_bytes), 失败返回 (0, 0)
    """
    try:
        r = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "subinterfaces"],
            capture_output=True, timeout=5
        )
        output = r.stdout.decode("gbk", errors="replace")

        # 找包含默认路由的活动适配器
        # subinterfaces 输出格式:
        #  接口名称   接收字节数    发送字节数
        #  PPPoE      123456789   987654321
        best_line = ""
        for line in output.splitlines():
            # 跳过表头和空行
            if "接口" in line or "Interface" in line or not line.strip():
                continue
            # 取第一个有数字的行 (活动适配器)
            nums = [int(w.replace(",", ""))
                    for w in line.split()
                    if w.replace(",", "").isdigit()]
            if len(nums) >= 2:
                return nums[-2], nums[-1]
    except Exception:
        pass
    return 0, 0


def format_speed(bps: float) -> str:
    """格式化速率 (bytes/s)"""
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.2f} MB/s"
    elif bps >= 1024:
        return f"{bps / 1024:.2f} KB/s"
    return f"{bps:.1f} B/s"


def format_bytes(b: int) -> str:
    """格式化累计流量"""
    if b >= 1024 * 1024 * 1024:
        return f"{b / 1024 / 1024 / 1024:.2f} GB"
    elif b >= 1024 * 1024:
        return f"{b / 1024 / 1024:.2f} MB"
    elif b >= 1024:
        return f"{b / 1024:.2f} KB"
    return f"{b} B"
