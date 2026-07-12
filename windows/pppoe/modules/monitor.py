#!/usr/bin/env python3
"""
PPPoE 监控模块 v2
=================
实时流量监控面板 — 严格 \x1b[H 绝对定位, 无闪烁
"""
import sys
import time
import datetime
import threading
import msvcrt

# 相对导入: monitor.py 位于 modules/ 包内
try:
    from . import util as _u
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import util as _u


# format_speed / format_bytes 通过 _u (util 模块) 调用
# 见下方循环中的 _u.format_speed() / _u.format_bytes()


# ── VT 初始化 ──
try:
    import ctypes
    from ctypes import wintypes
    _h = ctypes.windll.kernel32.GetStdHandle(-11)
    _m = wintypes.DWORD()
    ctypes.windll.kernel32.GetConsoleMode(_h, ctypes.byref(_m))
    ctypes.windll.kernel32.SetConsoleMode(_h, _m.value | 0x0004)
except Exception:
    pass


def run_monitor(start_time, base_rx, base_tx, adapter_name):
    """实时监控面板 — \x1b[H 绝对定位, Enter 退出
    返回 (final_rx, final_tx)
    """
    enter_pressed = threading.Event()

    def _input_thread():
        while not enter_pressed.is_set():
            if msvcrt.kbhit():
                if msvcrt.getch() in (b"\r", b"\n"):
                    enter_pressed.set()
                    break
            time.sleep(0.1)

    threading.Thread(target=_input_thread, daemon=True).start()

    sys.stdout.write("\x1b[?25l")  # 隐藏光标
    sys.stdout.flush()

    prev_rx, prev_tx = base_rx, base_tx
    prev_time = start_time
    curr_rx = curr_tx = base_rx
    total_rx = total_tx = rx_speed = tx_speed = 0
    first_frame = True

    try:
        while not enter_pressed.is_set():
            now = time.time()
            elapsed = int(now - start_time)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60

            curr_rx, curr_tx = _u.get_iface_bytes()
            if curr_rx > 0:
                drx = max(0, curr_rx - prev_rx)
                dtx = max(0, curr_tx - prev_tx)
                dt = now - prev_time
                rx_speed = drx / dt if dt > 0 else 0
                tx_speed = dtx / dt if dt > 0 else 0
                total_rx = max(0, curr_rx - base_rx)
                total_tx = max(0, curr_tx - base_tx)
                prev_rx, prev_tx = curr_rx, curr_tx
                prev_time = now

            ipv4, ipv6, _ = _u.get_ppp_info()
            now_str = datetime.datetime.now().strftime("%H:%M:%S")

            lines = [
                "============================================================",
                "  深澜 PPPoE 在线监控  |  Enter 断开并退出",
                "============================================================",
                f" 当前时间   : {now_str}",
                f" 在线时长   : {h:02d}:{m:02d}:{s:02d}",
                f" 适配器     : {adapter_name}",
                f" IPv4       : {ipv4}",
                f" IPv6       : {ipv6}",
                " -----------------------------------------------------------",
                f" 实时速率   : ↓ {_u.format_speed(rx_speed):>10s}  |  "
                f"↑ {_u.format_speed(tx_speed):>10s}",
                f" 累计流量   : ↓ {_u.format_bytes(total_rx):>10s}  |  "
                f"↑ {_u.format_bytes(total_tx):>10s}",
                "============================================================",
            ]

            # 每帧都从屏幕左上角开始 (绝对定位), 覆盖旧内容
            sys.stdout.write("\x1b[H")
            for ln in lines:
                # 行尾填充空格到 80 列, 确保覆盖旧内容
                sys.stdout.write(ln.ljust(79) + "\n")
            # 清除面板后可能残留的旧行
            sys.stdout.write("\x1b[0J")
            sys.stdout.flush()

            first_frame = False
            time.sleep(1)

    finally:
        sys.stdout.write("\x1b[?25h")  # 恢复光标
        sys.stdout.flush()

    final_rx = curr_rx - base_rx if curr_rx > 0 else 0
    final_tx = curr_tx - base_tx if curr_tx > 0 else 0
    return final_rx, final_tx
