"""
SRUN PPPoE - Wireshark/TShark Capture Script v2
================================================
Captures PPPoE frames using Wireshark CLI (tshark),
then dials the connection to get full frame data with hex payload.

Output:
  - captures/pppoe_<timestamp>.pcapng  (open in Wireshark GUI)
  - captures/pppoe_<timestamp>.txt      (hex dump readable text)
"""
import sys
import os
import subprocess
import time
import datetime
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from modules.encode import srun_encode_v4
from modules.util import rasdial, ensure_pppoe_entry

# -- Config ------------------------------------------------------------------
try:
    import yaml
    with open(os.path.join(BASE_DIR, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
except Exception:
    cfg = {}

USERNAME = cfg.get("auth", {}).get("username", "24046224@stu.nchu.edu.cn")
PASSWORD = cfg.get("auth", {}).get("password", "")
PPPOE_NAME = cfg.get("pppoe", {}).get("connection_name", "srun-pppoe")

# Known MACs
PC_MAC = "40-C2-BA-55-BF-F5".lower().replace("-", ":")
BRAS_MAC = "C4-07-78-01-E0-02".lower().replace("-", ":")

# -- Output ------------------------------------------------------------------
CAPTURE_DIR = os.path.join(BASE_DIR, "captures")
os.makedirs(CAPTURE_DIR, exist_ok=True)
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
PCAP_PATH = os.path.join(CAPTURE_DIR, f"pppoe_{TS}.pcapng")
TXT_PATH = os.path.join(CAPTURE_DIR, f"pppoe_{TS}.txt")


def log(msg, level="INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def find_tshark():
    candidates = [
        r"E:\Wireshark\tshark.exe",
        r"C:\Program Files\Wireshark\tshark.exe",
        r"C:\Program Files (x86)\Wireshark\tshark.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Wireshark", "tshark.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    try:
        r = subprocess.run(["where", "tshark"], capture_output=True, timeout=5, shell=True)
        if r.returncode == 0:
            path = r.stdout.decode("gbk", errors="replace").strip().split("\n")[0]
            if path:
                return path.strip()
    except Exception:
        pass
    return None


def check_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def get_interfaces_with_mac():
    """Get network interfaces with MAC addresses via getmac"""
    interfaces = []

    # Method 1: getmac /v
    try:
        r = subprocess.run(
            "getmac /v /fo list",
            capture_output=True, timeout=10, shell=True
        )
        out = r.stdout.decode("gbk", errors="replace")
        lines = out.split("\n")

        cur_name = None
        cur_mac = None
        cur_conn = None
        for line in lines:
            line = line.strip()
            if line.startswith("连接名称") or line.startswith("Connection Name"):
                if cur_name and cur_mac:
                    interfaces.append((cur_name.replace(":", "").strip(), cur_mac, cur_conn))
                parts = line.split(":", 1)
                cur_name = parts[1].strip() if len(parts) > 1 else None
                cur_mac = None
                cur_conn = None
            elif "MAC" in line or "Physical" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    mac = parts[1].strip()
                    if mac and mac.count("-") == 5:
                        cur_mac = mac.lower()
            elif "传输" in line or "Transport" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    cur_conn = parts[1].strip()

        if cur_name and cur_mac:
            interfaces.append((cur_name.strip(), cur_mac, cur_conn))

        if interfaces:
            return interfaces
    except Exception:
        pass

    # Method 2: ipconfig /all
    try:
        r = subprocess.run(
            "ipconfig /all",
            capture_output=True, timeout=10, shell=True
        )
        out = r.stdout.decode("gbk", errors="replace")
        lines = out.split("\n")
        cur_name = None
        cur_mac = None
        for line in lines:
            line = line.strip()
            if "适配器" in line or "adapter" in line.lower():
                if cur_name and cur_mac:
                    interfaces.append((cur_name, cur_mac, ""))
                cur_name = line.strip()
                cur_mac = None
            if "物理地址" in line or "Physical Address" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    mac = parts[1].strip()
                    if mac and mac.count("-") == 5:
                        cur_mac = mac.lower()
        if cur_name and cur_mac:
            interfaces.append((cur_name, cur_mac, ""))
        return interfaces
    except Exception:
        pass

    return interfaces


def list_interactive_interface(tshark_path):
    """List interfaces and let user choose"""
    print()
    log("=" * 60)
    log("Network Interfaces")
    log("=" * 60)

    # Show from getmac
    raw_interfaces = get_interfaces_with_mac()
    campus_iface = None
    if raw_interfaces:
        for i, (name, mac, conn) in enumerate(raw_interfaces, 1):
            is_campus = "(CAMPUS)" if mac == PC_MAC else ""
            print(f"  [{i}] {name}")
            print(f"       MAC: {mac} {is_campus}")
            if mac == PC_MAC:
                campus_iface = (name, mac)

    # Show from tshark -D
    print()
    log("tshark interface list:")
    r = subprocess.run([tshark_path, "-D"], capture_output=True, timeout=10)
    tshark_out = r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace")
    for line in tshark_out.split("\n"):
        line = line.strip()
        if line and "Npcap Loopback" not in line:
            # Check if this matches a known interface
            iface_id = line.split()[0].rstrip(":,") if line else ""
            print(f"  {line}")

    print()
    if campus_iface:
        log(f"Recommend: interface matching PC MAC {PC_MAC}", "TIP")

    choice = input("Enter interface number (from tshark -D list): ").strip()

    # Try to extract the interface ID from the user's input
    try:
        num = int(choice)
        tshark_lines = [l.strip() for l in tshark_out.split("\n") if l.strip() and "Npcap Loopback" not in l]
        if 1 <= num <= len(tshark_lines):
            selected = tshark_lines[num - 1]
            iface_id = selected.split()[0].rstrip(":,")
            log(f"Selected: {selected}", "OK")
            return iface_id
    except (ValueError, IndexError):
        pass

    # User might have typed the interface name
    return choice


def capture(tshark_path, iface):
    """Main capture flow"""
    print()
    log("=" * 60)
    log("SRUN PPPoE Wireshark Capture")
    log(f"tshark: {tshark_path}")
    log(f"iface:  {iface}")
    log(f"output: {PCAP_PATH}")
    log(f"txt:    {TXT_PATH}")
    log("=" * 60)

    # Ensure PPPoE entry
    if not ensure_pppoe_entry(PPPOE_NAME):
        log("PPPoE entry failed!", "FATAL")
        return False

    # Disconnect old
    log("[1] Disconnect old...")
    rasdial(PPPOE_NAME, "/d")

    # Start tshark
    log(f"[2] Start tshark capture (PPPoE filter, 20s duration)...")
    log(f"    Interface: {iface}")

    pcap_dir = os.path.dirname(PCAP_PATH)
    if not os.path.exists(pcap_dir):
        os.makedirs(pcap_dir)

    # Delete any old capture files first
    for old in [PCAP_PATH, TXT_PATH]:
        if os.path.exists(old):
            os.remove(old)

    proc = subprocess.Popen(
        [tshark_path,
         "-i", iface,
         "-f", "ether proto 0x8863 or ether proto 0x8864",
         "-w", PCAP_PATH,
         "-a", "duration:20"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(2)

    # Dial
    log("[3] Dial...")
    try:
        encoded = srun_encode_v4(USERNAME)
    except Exception as e:
        log(f"Encode failed: {e}", "ERROR")
        proc.terminate()
        return False

    encoded_str = encoded.decode("latin-1")
    log(f"    Encoded user: {repr(encoded_str[:80])}")

    rc, out = rasdial(PPPOE_NAME, encoded_str, PASSWORD, timeout=25)

    if rc == 0:
        log("    SUCCESS!", "OK")
        time.sleep(5)  # Capture more frames
        rasdial(PPPOE_NAME, "/d")
    else:
        log(f"    FAILED rc={rc}", "WARN")

    # Stop capture
    log("[4] Stop capture...")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    # Show tshark errors
    _, stderr = proc.communicate()
    if stderr:
        err_text = stderr.decode("utf-8", errors="replace")[:500]
        if err_text.strip():
            log(f"tshark stderr: {err_text}", "DEBUG")

    # Verify output
    if os.path.exists(PCAP_PATH):
        size = os.path.getsize(PCAP_PATH)
        log(f"[5] PCAP: {PCAP_PATH} ({size} bytes)", "OK")

        if size > 100:
            # Convert to text
            log("[6] Convert to txt...")
            try:
                with open(TXT_PATH, "w", encoding="utf-8") as f:
                    r = subprocess.run(
                        [tshark_path, "-r", PCAP_PATH, "-V"],
                        capture_output=True, timeout=60
                    )
                    f.write(r.stdout.decode("utf-8", errors="replace"))
                    if r.stderr:
                        f.write("\n--- stderr ---\n")
                        f.write(r.stderr.decode("utf-8", errors="replace"))
                log(f"    TXT: {TXT_PATH} ({os.path.getsize(TXT_PATH)} bytes)", "OK")
            except Exception as e:
                log(f"    TXT conversion failed: {e}", "WARN")

            # Summary
            log("[7] Frame summary:")
            try:
                r = subprocess.run(
                    [tshark_path, "-r", PCAP_PATH, "-q"],
                    capture_output=True, timeout=30
                )
                summary = r.stdout.decode("utf-8", errors="replace")
                if summary.strip():
                    for line in summary.split("\n")[:30]:
                        if line.strip():
                            log(f"    {line.strip()}")
            except Exception as e:
                log(f"    Summary failed: {e}", "WARN")

            return rc == 0
        else:
            log("    PCAP file is empty (0 packets captured)", "WARN")
            log("    Check: wrong interface or no PPPoE traffic")
            # Show available packets to help debug
            try:
                r = subprocess.run(
                    [tshark_path, "-r", PCAP_PATH],
                    capture_output=True, timeout=10
                )
                log(f"    tshark -r says: {r.stdout.decode('utf-8', errors='replace')[:200]}")
            except Exception:
                pass
            return False
    else:
        log("    PCAP file NOT created!", "ERROR")
        log("    tshark may have failed to start on this interface")
        return False


def main():
    print()
    if not check_admin():
        log("Admin privilege required!", "FATAL")
        log("Right-click -> Run as administrator", "TIP")
        input("\nPress Enter to exit...")
        return 1

    tshark = find_tshark()
    if not tshark:
        log("tshark.exe not found!", "FATAL")
        log("Checked: E:\\Wireshark\\tshark.exe", "DEBUG")
        log("Checked: C:\\Program Files\\Wireshark\\tshark.exe", "DEBUG")
        log("Install Wireshark with TShark, or verify path.", "TIP")
        input("\nPress Enter to exit...")
        return 1

    log(f"tshark: {tshark}", "OK")

    iface = list_interactive_interface(tshark)
    if not iface:
        log("No interface selected!", "FATAL")
        return 1

    ok = capture(tshark, iface)

    print()
    if ok:
        log("SUCCESS!", "OK")
    else:
        log("Completed (with issues)", "WARN")

    log(f"PCAP: {PCAP_PATH}")
    log(f"TXT:  {TXT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\nInterrupted", "WARN")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        log(f"Error: {e}", "FATAL")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            input("\nPress Enter to exit...")
        except (EOFError, OSError):
            pass
