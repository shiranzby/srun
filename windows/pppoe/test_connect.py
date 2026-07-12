#!/usr/bin/env python3
r"""
深澜 PPPoE 一键测试脚本 v5
===========================
- 指数退避重试 (1s-2s-4s-8s-16s-30s)
- 连接后保持5秒, 输出 IPv4+IPv6
- 完整日志记录
"""
import subprocess
import time
import sys
import os
import datetime
import traceback

# ── 配置 ────────────────────────────────────────────
USERNAME = "24046224@stu.nchu.edu.cn"
PASSWORD = "shy0618123."
PPPOE_NAME = "srun-pppoe"
SRUN_HOST = "10.1.88.4"
FIRST_COOLDOWN = 1
MAX_BACKOFF = 30
MAX_RETRIES = 5

# ── 日志 ────────────────────────────────────────────
LOG_FILE = os.path.join(
    os.environ.get("TEMP", os.path.expanduser("~")),
    f"srun_test_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)

def log(msg: str, level: str = "INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def fail(msg: str, code: int = 1):
    log(msg, "FAIL")
    log(f"日志: {LOG_FILE}")
    sys.exit(code)

def banner():
    print(r"""
  ╔════════════════════════════════════════════════╗
  ║     深澜校园网 PPPoE 认证测试 v5              ║
  ║     账号: 24046224@stu.nchu.edu.cn            ║
  ╚════════════════════════════════════════════════╝
""")

# ── 编码 ────────────────────────────────────────────
def encode_username(uname: str, ts: int = None) -> bytes:
    if ts is None:
        now = time.localtime()
        ts = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    if "@" in uname:
        account, rest = uname.split("@", 1)
        domain = "@" + rest
    else:
        account, domain = uname, ""
    s = sum(ord(c) for c in account)
    h = f"{s + ts:x}"
    shifted = "".join(f"{ord(c) + 4:02x}" for c in account)
    return f"{{S4}}{h}\r\n{shifted}{domain}".encode("latin-1")

# ── rasdial 封装 ────────────────────────────────────
def rasdial(*args, timeout: int = 30) -> tuple:
    cmd = ["rasdial"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = ""
        for data in (r.stdout, r.stderr):
            if data:
                try:
                    out += data.decode("gbk", errors="replace")
                except:
                    out += data.decode("utf-8", errors="replace")
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -2, str(e)

# ── IP 获取 ─────────────────────────────────────────
def get_pppoe_ip_info():
    """正则从 ipconfig 块提取 srun-pppoe 适配器的 IPv4/IPv6"""
    import re
    try:
        r = subprocess.run(["ipconfig"], capture_output=True, timeout=5)
        output = (r.stdout or b"").decode("gbk", errors="replace")
        blocks = re.split(r'\n\s*\n', output)
        for block in blocks:
            if not re.search(r'ppp|srun', block, re.IGNORECASE):
                continue
            nm = re.search(r'^(.+?):\s*$', block, re.MULTILINE)
            name = nm.group(1).strip() if nm else "srun-pppoe"
            ip4 = re.search(r'(?:IPv4|IP\s*Address)[^\d]*?:\s*(\d+\.\d+\.\d+\.\d+)',
                            block, re.IGNORECASE)
            ipv4 = ip4.group(1) if ip4 else "-"
            ipv6 = "-"
            for m in re.finditer(r'IPv6[^\n:]*:\s*([\da-fA-F:]+)', block):
                v6 = m.group(1).strip()
                if '%' not in v6:
                    ipv6 = v6; break
            return ipv4, ipv6, name
    except:
        pass
    return "-", "-", "srun-pppoe"

# ── 主流程 ──────────────────────────────────────────
def main():
    banner()
    log(f"日志: {LOG_FILE}")

    # 1. 断开旧连接
    log("[1] 断开旧连接...")
    rc, out = rasdial(PPPOE_NAME, "/d")
    log(f"结果: rc={rc}")

    # 2. 重试循环
    connected = False
    for attempt in range(1, MAX_RETRIES + 1):
        cooldown = FIRST_COOLDOWN if attempt == 1 else min(2 ** (attempt - 1), MAX_BACKOFF)
        log("-" * 40)
        log(f"[2] 尝试 {attempt}/{MAX_RETRIES} 冷却 {cooldown}s...")
        for i in range(cooldown, 0, -1):
            print(f"\r  剩余 {i:2d} 秒...", end="", flush=True)
            time.sleep(1)
        print()

        log("编码 + 拨号...")
        try:
            encoded = encode_username(USERNAME)
        except Exception as e:
            fail(f"编码失败: {e}")

        rc, out = rasdial(PPPOE_NAME, encoded.decode("latin-1"), PASSWORD, timeout=35)
        if rc == 0:
            connected = True; log("拨号成功!", "OK"); break

        if "691" in out: log("错误 691: 用户名/密码/已登录/时间戳过期", "WARN")
        elif "651" in out or "769" in out: log("错误 651/769: 网线未插", "WARN")
        elif "623" in out: log("错误 623: 宽带连接不存在", "WARN")
        else: log(f"未知错误 rc={rc}", "WARN")

        if attempt < MAX_RETRIES:
            log(f"将在 {min(2**attempt, MAX_BACKOFF)}s 后重试...")

    if not connected:
        fail(f"所有 {MAX_RETRIES} 次拨号均失败")

    # 3. 保持5秒 + 获取IP
    log("-" * 40)
    log("[3] 保持连接 5 秒并获取 IP...")
    time.sleep(5)
    ipv4, ipv6, adapter = get_pppoe_ip_info()
    log(f"适配器: {adapter}")
    log(f"IPv4: {ipv4}")
    log(f"IPv6: {ipv6}")

    # 4. 测试网络
    log("-" * 40)
    log("[4] 测试网络连通性...")
    try:
        r = subprocess.run(["ping", "-n", "2", "-w", "2000", "www.baidu.com"],
                          capture_output=True, timeout=10)
        if r.returncode == 0:
            log("外网可达 - 认证成功!", "OK")
            log("=" * 60)
            log("SUCCESS - 深澜 PPPoE 认证成功!", "OK")
            log(f"IPv4 = {ipv4}")
            log(f"IPv6 = {ipv6}")
            log("=" * 60)
        else:
            log("外网不可达, 测试内网...", "WARN")
            r2 = subprocess.run(["ping", "-n", "2", "-w", "2000", SRUN_HOST],
                               capture_output=True, timeout=5)
            if r2.returncode == 0:
                log(f"内网({SRUN_HOST})可达, 可能欠费/需选套餐", "WARN")
            else:
                log("内外网均不通", "WARN")
    except Exception as e:
        log(f"Ping 异常: {e}", "ERROR")

    # 5. 断开
    log("-" * 40)
    log("[5] 断开连接...")
    rasdial(PPPOE_NAME, "/d")
    log(f"完成! 日志: {LOG_FILE}")
    log("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"未捕获异常: {e}", "FATAL")
        traceback.print_exc()
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except:
            pass
        sys.exit(1)
    finally:
        try:
            input("\n按 Enter 键退出...")
        except (EOFError, OSError):
            pass
