#!/usr/bin/env python3
"""
PPPoE 实用工具模块 v3
=================
v3: 重写 get_ppp_info() 使用行扫描状态机 (替代正则分块, 适配中文/英文 Windows)
"""
import subprocess
import os
import ctypes
import sys
import re
from ctypes import wintypes


# ── rasdial 封装 ────────────────────────────────────

def rasdial(*args, timeout: int = 30) -> tuple:
    """运行 rasdial 命令, 返回 (returncode, output_text)"""
    cmd = ["rasdial"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = ""
        for data in (r.stdout, r.stderr):
            if data:
                try:
                    out += data.decode("gbk", errors="replace")
                except Exception:
                    out += data.decode("utf-8", errors="replace")
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -2, str(e)


# ── 网络测试 ────────────────────────────────────────

def test_network(host: str = "www.baidu.com") -> bool:
    """Ping 测试外网连通性"""
    try:
        pr = subprocess.run(
            ["ping", "-n", "2", "-w", "2000", host],
            capture_output=True, timeout=10
        )
        return pr.returncode == 0
    except Exception:
        return False


# ── 自动创建 PPPoE 宽带连接 ────────────────────────

RAS_MaxDeviceType = 16
RAS_MaxDeviceName = 128


class RASDEVINFOW(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("szDeviceType", ctypes.c_wchar * (RAS_MaxDeviceType + 1)),
        ("szDeviceName", ctypes.c_wchar * (RAS_MaxDeviceName + 1)),
    ]


def _enum_pppoe_device():
    """通过 RasEnumDevicesW 查找 PPPoE 适配器"""
    try:
        rasapi = ctypes.windll.rasapi32
    except Exception:
        return None, None

    buf_size = wintypes.DWORD(0)
    dev_count = wintypes.DWORD(0)
    rasapi.RasEnumDevicesW(None, ctypes.byref(buf_size), ctypes.byref(dev_count))
    if buf_size.value == 0:
        return None, None

    num = buf_size.value // ctypes.sizeof(RASDEVINFOW)
    if dev_count.value > 0:
        num = dev_count.value
    buf = (RASDEVINFOW * num)()
    for i in range(num):
        buf[i].dwSize = ctypes.sizeof(RASDEVINFOW)
    ret = rasapi.RasEnumDevicesW(buf, ctypes.byref(buf_size), ctypes.byref(dev_count))
    if ret != 0:
        return None, None

    for i in range(dev_count.value):
        if "pppoe" in buf[i].szDeviceType.lower():
            return buf[i].szDeviceName, f"PPPoE{i}-0"
    return None, None


def ensure_pppoe_entry(name: str, log_fn=None) -> bool:
    """确保 PPPoE 宽带连接存在"""
    def _log(msg, level="INFO"):
        if log_fn:
            log_fn(msg, level)

    rc, out = rasdial(name, timeout=5)
    if "623" not in out:
        return True

    _log(f"'{name}' 宽带连接不存在, 自动创建...", "INFO")

    device_name, port_name = _enum_pppoe_device()
    if not device_name:
        _log("未检测到 PPPoE 适配器", "ERROR")
        return False

    _log(f"适配器: {device_name} ({port_name})")

    pbk_dir = os.path.join(os.environ["APPDATA"],
                           "Microsoft", "Network", "Connections", "Pbk")
    pbk_path = os.path.join(pbk_dir, "rasphone.pbk")
    os.makedirs(pbk_dir, exist_ok=True)

    entry = (
        f"\r\n[{name}]\r\n"
        f"Encoding=1\r\n"
        f"Type=5\r\n"
        f"MEDIA=rastapi\r\n"
        f"Port={port_name}\r\n"
        f"Device={device_name}\r\n"
    )

    try:
        with open(pbk_path, "a", encoding="ascii") as f:
            f.write(entry)
    except Exception as e:
        _log(f"写入 phonebook 失败: {e}", "ERROR")
        return False

    rc2, out2 = rasdial(name, timeout=5)
    if "623" not in out2:
        _log(f"'{name}' 创建成功!", "OK")
        return True

    _log("自动创建未生效", "WARN")
    return False


# ── IP 获取 (v3: 行扫描状态机) ───────────────────
# 根因: re.split(r'\n\s*\n', output) 在某些 Windows 版本/语言中
#        无法正确分割适配器块, 导致匹配失败
# 修复: 逐行扫描, 以 "适配器" 或 ": " 结尾的行作为块起始

def _ipconfig_output() -> str:
    """获取 ipconfig 输出 (GBK 解码)"""
    try:
        r = subprocess.run(["ipconfig"], capture_output=True, timeout=5)
        return (r.stdout or b"").decode("gbk", errors="replace")
    except Exception:
        return ""


def get_ppp_info() -> tuple:
    """获取 PPPoE 适配器: (ipv4, ipv6, adapter_name)

    使用行扫描状态机, 适配中文/英文 Windows。
    优先匹配 PPP/srun 适配器, 其次匹配含默认网关的适配器。
    """
    output = _ipconfig_output()
    if not output:
        return "-", "-", "PPP"

    lines = output.splitlines()

    # 第一遍: 找到所有适配器块, 每个块是一个 (name, [lines]) 元组
    adapters = []  # [(name, [line, ...]), ...]
    current_name = ""
    current_lines = []

    for raw in lines:
        stripped = raw.strip()
        # 适配器名行: 以 ":" 结尾, 且含 "适配器" 或 "adapter" (不区分大小写)
        if stripped.endswith(":") and (
            "适配器" in stripped or "adapter" in stripped.lower()
        ):
            # 保存上一个块
            if current_name:
                adapters.append((current_name, current_lines))
            current_name = stripped.rstrip(":")
            current_lines = [stripped]
        else:
            if current_name:
                current_lines.append(stripped)

    # 保存最后一个块
    if current_name:
        adapters.append((current_name, current_lines))

    # 如果没有按适配器分块 (某些精简输出), 整个输出作为一个块
    if not adapters:
        adapters = [("default", lines)]

    # 优先找 PPP/srun 适配器块
    target_block = None
    for name, blk_lines in adapters:
        lower_name = name.lower()
        if "ppp" in lower_name or "srun" in lower_name:
            target_block = blk_lines
            break

    # 其次找含默认网关的块 (当前活动适配器)
    if not target_block:
        for name, blk_lines in adapters:
            block_text = "\n".join(blk_lines)
            if "默认网关" in block_text or "Default Gateway" in block_text:
                target_block = blk_lines
                break

    # 仍未找到: 用第一个块
    if not target_block and adapters:
        target_block = adapters[0][1]
        current_name = adapters[0][0]

    if not target_block:
        return "-", "-", "PPP"

    # 从目标块中提取 IP
    block_text = "\n".join(target_block)

    # IPv4: 匹配 "IPv4 地址 . . . . : 10.x.x.x" 或 "IPv4 Address . . . . : 10.x.x.x"
    # 注意: 中文 Windows 用 ":" 或 "："(全角), 英文用 ":"
    ipv4 = "-"
    for line in target_block:
        # 尝试匹配 IPv4 地址 (支持中英文 Windows, 支持全角/半角冒号)
        m = re.search(
            r'(?:IPv4|IP\s*Address)[^\n:：]*[:：]\s*(\d+\.\d+\.\d+\.\d+)',
            line, re.IGNORECASE
        )
        if m:
            ipv4 = m.group(1)
            break

    # IPv6: 排除含 % 的链路本地地址
    ipv6 = "-"
    for line in target_block:
        m = re.search(
            r'IPv6[^\n:：]*[:：]\s*([0-9a-fA-F:]+)',
            line, re.IGNORECASE
        )
        if m:
            v6 = m.group(1).strip()
            if '%' not in v6 and not v6.startswith("::1"):
                ipv6 = v6
                break

    # 确定适配器名
    adapter_name = current_name if current_name else "PPP"
    if not adapter_name or adapter_name == "default":
        # 从 target_block 第一行获取
        if target_block:
            first = target_block[0]
            if first.endswith(":"):
                adapter_name = first.rstrip(":")

    return ipv4, ipv6, adapter_name


# ── 流量统计 ────────────────────────────────────────

def get_iface_bytes() -> tuple:
    """获取 PPPoE 适配器的收发字节数 (rx, tx)

    通过 netsh interface ipv4 show subinterfaces 获取
    输出格式:  接口名称   接收字节数    发送字节数
    """
    try:
        r = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "subinterfaces"],
            capture_output=True, timeout=5
        )
        output = r.stdout.decode("gbk", errors="replace")
        for line in output.split("\n"):
            # 找含 PPP 的行 (PPPoE 适配器名通常含 PPP)
            if "ppp" in line.lower():
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
