#!/usr/bin/env python3
r"""
深澜 PPPoE 持续连接脚本 v4
===========================
- 首次运行自动创建宽带连接 (无需手动配置)
- 实时监控面板: IPv4/IPv6 + 速率 + 累计流量
- 指数退避重试 (1s→2s→4s→8s→16s→30s)
- Enter 退出(自动断开) / Ctrl+C 退出(保留连接)
"""
import sys
import os
import time
import datetime
import signal
import traceback

# 添加模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.encode import srun_encode_v4
from modules.util import (
    rasdial, test_network, ensure_pppoe_entry,
    get_ppp_info, get_iface_bytes, format_bytes
)
from modules.monitor import run_monitor

# ── 加载配置 ────────────────────────────────────────
try:
    import yaml
    with open(os.path.join(os.path.dirname(__file__), "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
except Exception:
    cfg = {}

AUTH = cfg.get("auth", {})
USERNAME = AUTH.get("username", "24046224@stu.nchu.edu.cn")
PASSWORD = AUTH.get("password", "")
SRUN_HOST = AUTH.get("server", "10.1.88.4")

PPPOE = cfg.get("pppoe", {})
PPPOE_NAME = PPPOE.get("connection_name", "srun-pppoe")

RETRY = cfg.get("retry", {})
MAX_RETRIES = RETRY.get("max_retries", 5)
FIRST_COOLDOWN = RETRY.get("first_cooldown", 1)
MAX_BACKOFF = RETRY.get("max_backoff", 30)

# ── 日志 ────────────────────────────────────────────
LOG_FILE = os.path.join(
    os.environ.get("TEMP", os.path.expanduser("~")),
    f"srun_keep_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

def fail(msg: str, code: int = 1):
    log(msg, "FAIL")
    log(f"日志: {LOG_FILE}")
    sys.exit(code)


# ── 单次拨号 ────────────────────────────────────────
def do_connect() -> bool:
    try:
        encoded = srun_encode_v4(USERNAME)
    except Exception as e:
        log(f"编码失败: {e}", "ERROR")
        return False

    ts = time.localtime()
    ts_sec = ts.tm_hour * 3600 + ts.tm_min * 60 + ts.tm_sec
    log(f"时间戳: {ts_sec}", "DEBUG")

    rc, out = rasdial(PPPOE_NAME, encoded.decode("latin-1"), PASSWORD, timeout=35)
    if rc == 0:
        return True

    if "691" in out:
        log("错误 691: 用户名/密码/时间戳过期/账号已登录", "WARN")
    elif "651" in out or "769" in out:
        log("错误 651/769: 网卡未连接校园网", "WARN")
    elif "623" in out:
        log("错误 623: 宽带连接不存在", "WARN")
    elif "756" in out:
        log("错误 756: 已有连接拨号中", "WARN")
    else:
        log(f"未知错误 rc={rc}", "WARN")
    return False


def graceful_disconnect():
    log("-" * 40)
    log("断开 PPPoE 连接...")
    rc, out = rasdial(PPPOE_NAME, "/d")
    log(f"结果: rc={rc} ({'已断开' if rc == 0 else '无连接'})")


# ── 主流程 ──────────────────────────────────────────
def main():
    print()
    log("=" * 60)
    log("深澜 PPPoE 持续连接 v4")
    log(f"账号: {USERNAME}")
    log(f"策略: 最多 {MAX_RETRIES} 次重试, {FIRST_COOLDOWN}s 起步, 指数退避上限 {MAX_BACKOFF}s")
    log("=" * 60)

    # 0. 自动创建宽带连接
    if not ensure_pppoe_entry(PPPOE_NAME, log_fn=log):
        fail("宽带连接创建失败, 请在 ncpa.cpl 中手动创建 'srun-pppoe'")

    # 1. 断开旧连接
    log("[1] 断开旧连接...")
    rc, out = rasdial(PPPOE_NAME, "/d")
    log(f"结果: rc={rc}")

    # 2. 重试循环
    connected = False
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt == 1:
            cooldown = FIRST_COOLDOWN
        else:
            cooldown = min(2 ** (attempt - 1), MAX_BACKOFF)

        log("-" * 40)
        log(f"[2] 尝试 {attempt}/{MAX_RETRIES} 冷却 {cooldown}s...")
        for i in range(cooldown, 0, -1):
            print(f"\r  剩余 {i:2d} 秒...", end="", flush=True)
            time.sleep(1)
        print()

        log("编码 + 拨号...")
        if do_connect():
            log("拨号成功!", "OK")
            connected = True
            break
        else:
            log(f"第 {attempt} 次失败", "WARN")
            if attempt < MAX_RETRIES:
                next_cool = min(2 ** attempt, MAX_BACKOFF)
                log(f"将在 {next_cool}s 后重试...")

    if not connected:
        fail(f"所有 {MAX_RETRIES} 次拨号均失败")

    # 3. 网络验证
    log("-" * 40)
    log("[3] 等待网络就绪 (3s)...")
    time.sleep(3)

    ipv4, ipv6, adapter_name = get_ppp_info()
    log(f"适配器: {adapter_name}")
    log(f"IPv4: {ipv4}")
    log(f"IPv6: {ipv6}")

    if test_network():
        log("外网可达!", "OK")
    else:
        log("外网 Ping 失败, 测试内网...", "WARN")
        if test_network(SRUN_HOST):
            log(f"内网({SRUN_HOST})可达, 可能账号欠费/需选套餐", "WARN")
        else:
            log("内外网均不通", "WARN")

    # 4. 监控面板
    log("-" * 40)
    log("[4] 进入监控面板", "OK")

    base_rx, base_tx = get_iface_bytes()
    start_time = time.time()

    def on_sigint(sig, frame):
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()
        log("Ctrl+C 退出 (连接未断开)", "WARN")
        log(f"如需断开: disconnect.bat")
        sys.exit(0)

    signal.signal(signal.SIGINT, on_sigint)

    final_rx, final_tx = run_monitor(start_time, base_rx, base_tx, adapter_name)

    log(f"本次流量: ↓ {format_bytes(final_rx)}  ↑ {format_bytes(final_tx)}")

    # 5. 退出
    graceful_disconnect()
    log("完成!", "OK")
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
        try:
            input("\n按 Enter 键退出...")
        except (EOFError, OSError):
            pass
