#!/usr/bin/env python3
r"""
深澜 DHCP Portal 无线登录脚本 v4
===============================
100% 对照 BitSrunLoginGo: XXTEA + Base64 + HMAC-MD5 + SHA1 + GET 请求
v4: 修复监控面板 (速率/IPv6/时间刷新), 重写 IP 获取
"""
import sys
import os
import json
import time
import datetime
import urllib.parse
import signal
import traceback
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.crypto import (
    xencode, srun_md5, srun_password_hash, srun_chksum, srun_info_prefix
)
from modules.util import (
    http_get_json, get_local_ipv4, get_local_ipv6, get_iface_bytes,
    format_speed, format_bytes
)

# ── 加载配置 ────────────────────────────────────────
CFG_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    import yaml
    with open(os.path.join(CFG_DIR, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
except Exception:
    cfg = {}

AUTH = cfg.get("auth", {})
USERNAME = AUTH.get("username", "")
PASSWORD = AUTH.get("password", "")
SRUN_HOST = AUTH.get("server", "10.1.88.4")

DHCP = cfg.get("dhcp", {})
AC_ID = DHCP.get("ac_id", "1")
N_VAL = DHCP.get("n", "200")
TYPE_VAL = DHCP.get("type", "1")
INFO_PREFIX_RAW = DHCP.get("info_prefix", "SRBX1")
TIMEOUT = DHCP.get("timeout", 5)

GUARDIAN = cfg.get("guardian", {})
GUARDIAN_ENABLED = GUARDIAN.get("enabled", True)
GUARDIAN_DURATION = GUARDIAN.get("duration", 3)

RETRY = cfg.get("retry", {})
MAX_RETRIES = RETRY.get("max_retries", 5)
FIRST_COOLDOWN = RETRY.get("first_cooldown", 1)
MAX_BACKOFF = RETRY.get("max_backoff", 30)

# ── 日志 ────────────────────────────────────────────
LOG_FILE = os.path.join(
    os.environ.get("TEMP", os.path.expanduser("~")),
    f"srun_dhcp_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)

def log(msg: str, level: str = "INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── 认证流程 ────────────────────────────────────────

def do_login() -> bool:
    """单次 DHCP 登录 (GET 方式, 与 BitSrunLoginGo 一致)"""

    # 1. get_challenge
    ip = get_local_ipv4()
    enc_user = urllib.parse.quote(USERNAME, safe="")
    ts = int(time.time() * 1000)
    challenge_url = (f"http://{SRUN_HOST}/cgi-bin/get_challenge"
                     f"?callback=jsonp"
                     f"&username={enc_user}"
                     f"&ip={ip}"
                     f"&_={ts}")

    log(f"GET challenge...")
    ch_res = http_get_json(challenge_url, timeout=TIMEOUT)
    if "_error" in ch_res:
        log(f"get_challenge 失败: {ch_res['_error']}", "ERROR")
        return False

    token = ch_res.get("challenge", "")
    client_ip = ch_res.get("client_ip", ip)

    if not token:
        log("token 为空", "WARN")
        return False

    log(f"token={token[:20]}...  ip={client_ip}", "DEBUG")

    # 2. 加密 info
    info = {
        "username": USERNAME,
        "password": PASSWORD,
        "ip": client_ip,
        "acid": AC_ID,
        "enc_ver": DHCP.get("enc", "srun_bx1"),
    }
    info_json = json.dumps(info, separators=(",", ":"))
    encrypted_info = srun_info_prefix(INFO_PREFIX_RAW) + xencode(info_json, token)
    log(f"info encrypted: {len(encrypted_info)} chars", "DEBUG")

    # 3. 密码哈希: HMAC-MD5(token, password)
    md5_str = srun_md5(token, PASSWORD)   # 纯 hex (不含 {MD5})
    enc_password = srun_password_hash(token, PASSWORD)  # {MD5}<hex>

    # 4. chksum
    chksum = srun_chksum(token, USERNAME, md5_str, AC_ID,
                         client_ip, N_VAL, TYPE_VAL, encrypted_info)
    log(f"chksum={chksum.strip()[:16]}...", "DEBUG")

    # 5. 构建 GET URL
    params = {
        "action": "login",
        "username": USERNAME,
        "password": enc_password,
        "ac_id": AC_ID,
        "ip": client_ip,
        "info": encrypted_info,
        "chksum": chksum.strip(),
        "n": N_VAL,
        "type": TYPE_VAL,
        "os": DHCP.get("os", "Windows 10"),
        "name": DHCP.get("name", "windows"),
        "double_stack": "1" if DHCP.get("double_stack", False) else "0",
        "callback": "jsonp",
        "_": str(int(time.time() * 1000)),
    }
    login_url = "http://" + SRUN_HOST + "/cgi-bin/srun_portal?" + urllib.parse.urlencode(params)

    log("发送登录请求 (GET)...")
    resp = http_get_json(login_url, timeout=TIMEOUT)

    if "_error" in resp:
        log(f"登录 HTTP 错误: {resp['_error']}", "ERROR")
        if "_body" in resp:
            log(f"body: {resp['_body']}", "DEBUG")
        return False

    err = resp.get("error", "")
    log(f"响应: error={err}", "DEBUG")

    if err == "ok":
        log("登录成功!", "OK")
        return True
    elif "已在线" in str(resp) or "online" in str(resp).lower():
        log("已在线", "OK")
        return True
    else:
        log(f"登录失败: {err}", "WARN")
        log(f"完整响应: {json.dumps(resp, ensure_ascii=False)[:300]}", "DEBUG")
        return False


def test_network(host: str = "www.baidu.com") -> bool:
    """Ping 测试外网连通性 (带超时)"""
    import subprocess
    try:
        r = subprocess.run(["ping", "-n", "1", "-w", "2000", host],
                          capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


# ── 实时监控面板 ────────────────────────────────────────
# 根因: 原实现在主循环中调用 test_network(), 阻塞 2~4 秒
# 修复: 用后台线程定期检测网络状态, 主循环只读取共享变量, 不再阻塞

_network_online = True   # 后台线程更新, 主循环读取
_network_checking = False


def _network_monitor_thread():
    """后台线程: 每 10 秒检测一次网络, 更新 _network_online"""
    global _network_online
    while True:
        _network_online = test_network()
        time.sleep(10)


def start_network_monitor():
    """启动后台网络检测线程"""
    t = threading.Thread(target=_network_monitor_thread, daemon=True)
    t.start()


# ── 主流程 ──────────────────────────────────────────
def main():
    print()
    log("=" * 60)
    log("深澜 DHCP Portal 无线登录 v4 (BitSrunLoginGo 算法)")
    log(f"账号: {USERNAME}")
    log(f"服务器: {SRUN_HOST}")
    log("=" * 60)

    connected = False
    for attempt in range(1, MAX_RETRIES + 1):
        cooldown = FIRST_COOLDOWN if attempt == 1 else min(2 ** (attempt - 1), MAX_BACKOFF)
        if attempt > 1:
            log(f"冷却 {cooldown}s...")
            for i in range(cooldown, 0, -1):
                print(f"\r  剩余 {i:2d} 秒...", end="", flush=True)
                time.sleep(1)
            print()

        log(f"尝试 {attempt}/{MAX_RETRIES} 登录...")
        if do_login():
            connected = True
            break
        if attempt < MAX_RETRIES:
            log(f"将在 {min(2**attempt, MAX_BACKOFF)}s 后重试...")

    if not connected:
        log(f"所有 {MAX_RETRIES} 次登录均失败", "FAIL")
        log(f"日志: {LOG_FILE}")
        try:
            input("\n按 Enter 键退出...")
        except (EOFError, OSError):
            pass
        sys.exit(1)

    time.sleep(3)
    online = test_network()
    log(f"网络状态: {'可达' if online else '不通'}", "OK" if online else "WARN")
    log("─" * 40)

    # ── 实时监控面板 ──
    import msvcrt

    # VT 初始化
    try:
        import ctypes as _ct; from ctypes import wintypes as _wt
        _h = _ct.windll.kernel32.GetStdHandle(-11)
        _m = _wt.DWORD()
        _ct.windll.kernel32.GetConsoleMode(_h, _ct.byref(_m))
        _ct.windll.kernel32.SetConsoleMode(_h, _m.value | 0x0004)
    except: pass

    def on_sigint(s, f):
        sys.stdout.write("\x1b[?25h\n"); sys.stdout.flush()
        log("Ctrl+C 退出", "WARN"); sys.exit(0)
    signal.signal(signal.SIGINT, on_sigint)

    enter_pressed = threading.Event()
    def _input_thread():
        while not enter_pressed.is_set():
            if msvcrt.kbhit() and msvcrt.getch() in (b"\r", b"\n"):
                enter_pressed.set(); break
            time.sleep(0.1)
    threading.Thread(target=_input_thread, daemon=True).start()

    # 启动后台网络检测
    start_network_monitor()

    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()
    start_time = time.time()

    # 流量统计初始化
    prev_rx, prev_tx = get_iface_bytes()
    prev_time = time.time()
    rx_speed = tx_speed = 0.0
    total_rx = total_tx = 0

    try:
        while not enter_pressed.is_set():
            now = time.time()
            elapsed = int(now - start_time)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            cur_ipv4 = get_local_ipv4()
            cur_ipv6 = get_local_ipv6()
            now_str = datetime.datetime.now().strftime("%H:%M:%S")

            # 更新流量速率
            curr_rx, curr_tx = get_iface_bytes()
            if curr_rx > 0 and prev_rx > 0:
                dt = now - prev_time
                if dt > 0:
                    rx_speed = (curr_rx - prev_rx) / dt
                    tx_speed = (curr_tx - prev_tx) / dt
                total_rx = max(0, curr_rx - prev_rx) if prev_rx > 0 else 0
                total_tx = max(0, curr_tx - prev_tx) if prev_tx > 0 else 0
                prev_rx, prev_tx = curr_rx, curr_tx
                prev_time = now

            net_status = "可达" if _network_online else "不通"

            lines = [
                "============================================================",
                "  深澜 DHCP 在线监控  |  Enter 退出",
                "============================================================",
                f" 当前时间   : {now_str}",
                f" 在线时长   : {h:02d}:{m:02d}:{s:02d}",
                f" 认证方式   : DHCP Portal",
                f" 当前 IPv4  : {cur_ipv4}",
                f" 当前 IPv6  : {cur_ipv6}",
                " -----------------------------------------------------------",
                f" 实时速率   : ↓ {format_speed(rx_speed):>10s}  |  "
                f"↑ {format_speed(tx_speed):>10s}",
                f" 累计流量   : ↓ {format_bytes(total_rx):>10s}  |  "
                f"↑ {format_bytes(total_tx):>10s}",
                f" 外网状态   : {net_status}",
                "============================================================",
            ]
            sys.stdout.write("\x1b[H")
            for ln in lines:
                sys.stdout.write(ln.ljust(79) + "\n")
            sys.stdout.write("\x1b[0J")
            sys.stdout.flush()
            time.sleep(1)
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()

    log("退出")
    log(f"日志: {LOG_FILE}")


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
        except Exception:
            pass
        sys.exit(1)
    finally:
        log(f"日志: {LOG_FILE}")
        try:
            input("\n按 Enter 键退出...")
        except (EOFError, OSError):
            pass
