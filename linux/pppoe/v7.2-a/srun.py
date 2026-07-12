#!/usr/bin/env python3

"""

srun.py - 校园网 SRUN PPPoE 联网管理工具 v7.0

用法: python3 srun.py [connect|stop|daemon|menu|monitor|install|uninstall|reconfig|settime]

"""

import sys, os, time, subprocess, stat, select, atexit, signal, base64

try:

    import glob

except ImportError:

    glob = None



# 日志等级常量

L_INFO = "INFO"; L_WARN = "WARN"; L_ERROR = "ERROR"

L_DEBUG = "DEBUG"; L_OK = "OK"; L_FATAL = "FATAL"



# ═══════════════════════════════════════════════════════════

# 配置区 - 用户配置（可按需修改）

# ═══════════════════════════════════════════════════════════

DEFAULT_USR = "241234@stu.nchu.edu.cn"

DEFAULT_PWD = "123456"

RUNTIME = {"usr": DEFAULT_USR, "pwd": DEFAULT_PWD, "phy": "",

           "dns1": "10.1.1.1", "dns2": "8.8.8.8"}

DEBUG = False  # True=输出异常详情用于调试

def _detect_phy():
    """自动检测WAN接口：排除br-lan成员，选择有载波的eth接口"""
    _skip = {"lo", "br-lan", "dummy0"}
    _lan_members = set()
    _brif = "/sys/class/net/br-lan/brif"
    if os.path.isdir(_brif):
        _lan_members = set(os.listdir(_brif))
    _candidates = []
    for _d in os.listdir("/sys/class/net"):
        if _d in _skip or _d.startswith(("ppp", "ifb", "gre", "sit", "tunl", "ip6tnl", "wifi")): continue
        _f = "/sys/class/net/" + _d + "/carrier"
        if os.path.exists(_f):
            try:
                with open(_f) as _fh:
                    if _fh.read().strip() == "1" and _d not in _lan_members:
                        _candidates.append(_d)
            except: pass
    for _c in _candidates:
        if _c.startswith("eth"): return _c
    if _candidates: return _candidates[0]
    return "eth1"




# PPPoE 连接参数

CONNECT_RETRY_COUNT = 5          # 连接重试次数

CONNECT_TIMEOUT = 45             # 每次连接等待超时（秒）

E2620_RETRY_DELAY = 10           # E2620 后等待重试（秒）

CARRIER_TIMEOUT = 15             # 网口 carrier 检测超时

PPP_RETRY = 3                    # pppd /dev/ppp 重试次数

WAIT_CARRIER_STEP = 2            # carrier 轮询间隔

LOG_POLL_INTERVAL = 2            # monitor 日志轮询间隔



# 看守进程参数

PING_INTERVAL = 10               # 连通性检测间隔（秒）

CONNECTIVITY_FAIL_THRESHOLD = 3  # 连通性连续失败触发重连次数

CONNECTIVITY_TIMEOUT = 8         # 并行检测总超时（秒）



# IPv6 参数

RA_WAIT_TIME = 30                # IPv6 RA 轮询总超时（秒）

RA_RETRY_DELAY = 5               # RA 超时后额外等待（秒）



# 监控面板参数

MONITOR_REFRESH = 1              # 帧刷新间隔（秒）

STATUS_CACHE_TTL = 5             # 服务/自启状态缓存有效期（秒）

PPP_INFO_CACHE_TTL = 3           # ppp0 IP 信息缓存有效期（秒）



# ═══════════════════════════════════════════════════════════

# 系统常量（运行环境相关，通常不需要修改）

# ═══════════════════════════════════════════════════════════

CFG = "/root/srun/srun.conf"

SVC = "srun-pppoe"

IFACE = "pppoe_dial"       # macvlan 接口名

PPP_IFACE = "ppp0"         # PPPoE 虚拟接口名

try:

    _pppd_result = subprocess.run(["which", "pppd"], capture_output=True, text=True)

    PPPD = _pppd_result.stdout.strip() or "/usr/sbin/pppd"

except FileNotFoundError:

    PPPD = "/usr/sbin/pppd"

PLUGIN = "/usr/lib/pppd/2.4.7/rp-pppoe.so"  # 最终 fallback

if glob is not None:

    _plugins = sorted(glob.glob("/usr/lib/pppd/*/*pppoe*.so"))

    if _plugins:

        PLUGIN = _plugins[0]

LOG = "/var/log/pppoe_dial.log"

CHAP_SECRETS = "/etc/ppp/chap-secrets"

PAP_SECRETS = "/etc/ppp/pap-secrets"

UCI_NAME = "srun"

PID_FILE = "/var/run/srun-watchdog.pid"

INIT_SCRIPT = "/etc/init.d/srun-pppoe"



# 配置键映射（配置文件名 → RUNTIME 键名）

CFG_MAP = {"USR": "usr", "PWD": "pwd", "PHY": "phy", "DNS1": "dns1", "DNS2": "dns2"}



# ═══════════════════════════════════════════════════════════

# 系统工具包装

# ═══════════════════════════════════════════════════════════

def run(cmd, timeout=5):

    """执行系统命令并捕获输出"""

    return subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)



def run_quiet(cmd, timeout=5):

    """执行系统命令，忽略输出"""

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)



def kill_pppd():

    """优雅断开 + 强制清理 pppd（先 TERM 发 PADT 通知 BRAS，再 KILL 兜底）"""

    r = subprocess.run(["killall", "pppd"], capture_output=True, text=True)

    if r.returncode != 0:

        log("pppd 进程不存在，跳过 TERM", L_DEBUG)

    time.sleep(1)

    subprocess.run(["killall", "-9", "pppd"], capture_output=True)



def recreate_dev_ppp():

    """重建 /dev/ppp 设备节点"""

    run_quiet(["rm", "-f", "/dev/ppp"])

    run_quiet(["mknod", "/dev/ppp", "c", "108", "0"])



def debug_exc(name, e):

    """调试模式输出异常详情，生产模式静默"""

    if DEBUG:

        log(f"{name}: {e}", L_DEBUG)



# ═══════════════════════════════════════════════════════════

# 日志与配置

# ═══════════════════════════════════════════════════════════

def log(m, l=L_INFO):

    if l == L_DEBUG and not DEBUG: return

    print(f"[{time.strftime('%m-%d %H:%M:%S')}] [{l}] {m}", flush=True)



def load_cfg():

    """通用 KEY=VALUE 配置文件解析（CFG_MAP 白名单）"""

    if os.path.exists(CFG):

        try:

            with open(CFG) as f:

                for line in f:

                    line = line.strip()

                    if "=" not in line or line.startswith("#"): continue

                    k, v = line.split("=", 1)

                    v = v.strip().strip("\"'")

                    try:

                        v = base64.b64decode(v).decode()

                    except Exception:

                        pass  # old format (unencoded)

                    if k in CFG_MAP:

                        RUNTIME[CFG_MAP[k]] = v

        except Exception as _e: debug_exc("load_cfg", _e)
    if not RUNTIME["phy"]:
        detected = _detect_phy()
        log(f"自动检测WAN接口: {detected}", L_OK)
        RUNTIME["phy"] = detected
        save_all_cfg()



def save_all_cfg(u=None, p=None, h=None, d1=None, d2=None):

    """原子写入配置（tmp + os.replace 防断电损坏）"""

    u = u if u is not None else RUNTIME["usr"]

    p = p if p is not None else RUNTIME["pwd"]

    h = h if h is not None else RUNTIME["phy"]

    d1 = d1 if d1 is not None else RUNTIME["dns1"]

    d2 = d2 if d2 is not None else RUNTIME["dns2"]

    tmp = CFG + ".tmp"

    with open(tmp, "w") as f:

        for key, val in [("USR", u), ("PWD", p), ("PHY", h), ("DNS1", d1), ("DNS2", d2)]:

            f.write(f'{key}="{base64.b64encode(val.encode()).decode()}"\n')

    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)

    os.replace(tmp, CFG)  # 原子替换，断电不丢配置

    RUNTIME["usr"] = u; RUNTIME["pwd"] = p; RUNTIME["phy"] = h

    RUNTIME["dns1"] = d1; RUNTIME["dns2"] = d2



load_cfg()



# ═══════════════════════════════════════════════════════════

# S4 编码

# ═══════════════════════════════════════════════════════════

def srun_encode_v4(username: str) -> bytes:

    now = time.localtime()

    ts = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec

    if "@" in username:

        account, rest = username.split("@", 1)

        domain = "@" + rest

    else:

        account = username; domain = ""

    account_sum = sum(ord(c) for c in account)

    hash_val = account_sum + ts

    shifted_hex = "".join(f"{ord(c) + 4:02x}" for c in account)

    return f"{{S4}}{hash_val:x}\r\n{shifted_hex}{domain}".encode("latin-1")



# ═══════════════════════════════════════════════════════════

# 辅助

# ═══════════════════════════════════════════════════════════

def get_carrier(iface):

    try:

        with open(f"/sys/class/net/{iface}/carrier") as f: return f.read().strip()

    except Exception as _e: debug_exc("get_carrier", _e); return "0"



def get_mac(iface):

    try:

        with open(f"/sys/class/net/{iface}/address") as f: return f.read().strip()

    except Exception as _e: debug_exc("get_mac", _e); return "?"



def wait_carrier(iface, timeout=CARRIER_TIMEOUT):

    t0 = time.time()

    while time.time() - t0 < timeout:

        c = get_carrier(iface)

        if c == "1":

            log(f"{iface}: carrier=1 (mac={get_mac(iface)})")

            return True

        time.sleep(WAIT_CARRIER_STEP)

    log(f"{iface}: carrier timeout ({timeout}s)", L_WARN)

    return False



_ppp0_cache = None; _ppp0_cache_ts = 0

PPP_UP_TS = 0  # 连接成功时间戳，用于在线时长计算

def _ppp0_info(force=False):

    """获取 ppp0 IPv4/IPv6 地址（带 3s 缓存）。返回 (ipv4, ipv6)"""

    global _ppp0_cache, _ppp0_cache_ts

    now = time.time()

    if not force and _ppp0_cache and now - _ppp0_cache_ts < PPP_INFO_CACHE_TTL:

        return _ppp0_cache

    r = run(["ip", "addr", "show", PPP_IFACE])

    v4 = None; v6 = None

    for line in r.stdout.split('\n'):

        if 'inet ' in line: v4 = line.strip().split()[1].split('/')[0]

        if 'inet6 ' in line and 'scope global' in line:

            v6 = line.strip().split()[1].split('/')[0]

    _ppp0_cache = (v4, v6); _ppp0_cache_ts = now

    return v4, v6



def _ppp0_has_ip():

    return _ppp0_info()[0] is not None



def _firewall_remove_srun():

    """从 WAN 防火墙区域移除 srun 条目"""

    try:

        r = run(["uci", "-q", "show", "firewall.@zone[1]"])

        for line in r.stdout.split('\n'):

            if '.network' in line and UCI_NAME in line:

                run_quiet(["uci", "del_list", f"firewall.@zone[1].network={UCI_NAME}"], timeout=3)

    except Exception as _e: debug_exc("_firewall_remove_srun", _e)



def _firewall_add_srun():

    """将 srun 加入 WAN 防火墙区域（仅当不在其中时）"""

    try:

        r = run(["uci", "-q", "show", "firewall.@zone[1]"])

        if not any('.network' in l and UCI_NAME in l for l in r.stdout.split('\n')):

            run_quiet(["uci", "add_list", f"firewall.@zone[1].network={UCI_NAME}"], timeout=3)

            return True

    except Exception as _e: debug_exc("_firewall_add_srun", _e)

    return False



def detect_interfaces():

    """检测可用网口（按特征过滤，免维护名单）"""

    skip_prefixes = ("ppp", "ifb", "wifi", "ath", "sit", "gre", "ip6", "erspan",

                     "gretap", "bond", "dummy", "teql", "soc", "ipsecdummy",

                     "docker", "veth", "tun", "tap", "wg")

    up = []; down = []

    try:

        for name in os.listdir("/sys/class/net"):

            if name == "lo" or name.startswith(skip_prefixes): continue

            try:

                with open(f"/sys/class/net/{name}/carrier") as f:

                    c = f.read().strip()

                (up if c == "1" else down).append(name)

            except Exception as _e: debug_exc("detect_interfaces:carrier", _e); down.append(name)

    except Exception as _e: debug_exc("detect_interfaces:listdir", _e)

    return up, down



def fmt_iface_color(name, state):

    if state == "up": return f"\033[32m{name}\033[0m"

    return f"\033[31m{name}\033[0m"



def _kill_competing():

    """杀掉其他 srun.py 进程（通过 /proc/PID/cmdline 精确匹配）"""

    my_pid = os.getpid()

    for entry in os.listdir("/proc"):

        if not entry.isdigit(): continue

        pid = int(entry)

        if pid == my_pid: continue

        try:

            with open(f"/proc/{pid}/cmdline", "rb") as f:

                cmdline = f.read().decode("utf-8", errors="replace").replace("\0", " ")

            parts = cmdline.strip().split()

            if any(p == "srun.py" or p.endswith("/srun.py") for p in parts):

                os.kill(pid, signal.SIGTERM)

        except (OSError, IOError, PermissionError):

            pass



def _kill_odhcp6c():

    run_quiet(["killall", "odhcp6c"])



def _invalidate_ppp0_cache():

    global _ppp0_cache, _ppp0_cache_ts

    _ppp0_cache = None; _ppp0_cache_ts = 0



# ═══════════════════════════════════════════════════════════

# PPPoE 核心

# ═══════════════════════════════════════════════════════════

def cleanup_all():

    run_quiet(["ip", "link", "delete", IFACE])

    _kill_odhcp6c()

    _firewall_remove_srun()

    try:

        run_quiet(["uci", "delete", f"network.{UCI_NAME}"])

        run_quiet(["uci", "commit"])

    except Exception as _e: debug_exc("cleanup_all", _e)

    _invalidate_ppp0_cache()



def create_iface():
    cleanup_all()
    run_quiet(["ip", "link", "set", RUNTIME["phy"], "up"])
    time.sleep(2)
    r = run(["ip", "link", "add", "link", RUNTIME["phy"], "name", IFACE, "type", "macvlan", "mode", "private"])
    if r.returncode != 0:
        log(f"macvlan 创建失败: {r.stderr}", L_ERROR)
        run_quiet(["ip", "link", "delete", IFACE], timeout=3)
        time.sleep(1)
        r = run(["ip", "link", "add", "link", RUNTIME["phy"], "name", IFACE, "type", "macvlan", "mode", "private"])
        if r.returncode != 0: log(f"macvlan 创建仍失败: {r.stderr}", L_ERROR); return False
    run_quiet(["ip", "link", "set", IFACE, "up"])
    if not wait_carrier(IFACE):
        run_quiet(["ip", "link", "set", RUNTIME["phy"], "down"])
        time.sleep(1); run_quiet(["ip", "link", "set", RUNTIME["phy"], "up"])
        time.sleep(3); run_quiet(["ip", "link", "set", IFACE, "up"])
        if not wait_carrier(IFACE): return False
    log(f"接口: {IFACE} (mac={get_mac(IFACE)})")
    return True




def write_secrets():

    content = f'* * "{RUNTIME["pwd"]}" *\n'

    for f in [CHAP_SECRETS, PAP_SECRETS]:

        try:

            if os.path.exists(f) and open(f).read() == content:

                continue

        except (OSError, IOError):

            pass

        with open(f, "w") as fh: fh.write(content)

        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)

    log("密码已写入 (通配符)", L_OK)



def post_setup():

    global PPP_UP_TS

    log("后置配置: DNS + 防火墙 + LuCI + IPv6...")

    # DNS（多渠道兜底：/tmp/resolv.conf 供 libc 解析器直接使用，

    #               /tmp/resolv.conf.d/resolv.conf.auto 供 dnsmasq 使用，

    #               /tmp/dnsmasq.d/ 供 dnsmasq conf-dir 使用）

    try:

        # 方式 1: /tmp/resolv.conf（路由器的系统的 libc 解析器直接读取）

        with open("/tmp/resolv.conf", "w") as f:

            f.write(f"nameserver {RUNTIME['dns1']}\nnameserver {RUNTIME['dns2']}\n")

        # 方式 2: dnsmasq 的 resolvfile（OpenWrt 默认路径）

        run_quiet(["mkdir", "-p", "/tmp/resolv.conf.d"], timeout=3)

        with open("/tmp/resolv.conf.d/resolv.conf.auto", "w") as f:

            f.write(f"nameserver {RUNTIME['dns1']}\nnameserver {RUNTIME['dns2']}\n")

        # 方式 3: dnsmasq 的 conf-dir（最可靠，SIGHUP 即生效）

        run_quiet(["mkdir", "-p", "/tmp/dnsmasq.d"], timeout=3)

        with open("/tmp/dnsmasq.d/srun-dns.conf", "w") as f:

            f.write(f"server={RUNTIME['dns1']}\nserver={RUNTIME['dns2']}\n")

        log(f"DNS: {RUNTIME['dns1']}, {RUNTIME['dns2']}", L_OK)

    except Exception as e: log(f"DNS 写入失败: {e}", L_WARN)

    run_quiet(["/etc/init.d/dnsmasq", "restart"], timeout=10)

    # SIGHUP 兜底（某些构建 restart 可能不重新加载 conf-dir）

    run_quiet(["killall", "-1", "dnsmasq"], timeout=3)

    # iptables

    for check, add in [

        (["iptables", "-t", "nat", "-C", "POSTROUTING", "-o", PPP_IFACE, "-j", "MASQUERADE"],

         ["iptables", "-t", "nat", "-A", "POSTROUTING", "-o", PPP_IFACE, "-j", "MASQUERADE"]),

        (["iptables", "-C", "FORWARD", "-i", "br-lan", "-o", PPP_IFACE, "-j", "ACCEPT"],

         ["iptables", "-A", "FORWARD", "-i", "br-lan", "-o", PPP_IFACE, "-j", "ACCEPT"]),

    ]:

        if run(check).returncode != 0:

            run_quiet(add); log(f"iptables 已添加: {add[2]}", L_OK)

    # UCI 接口

    show = run(["uci", "show", f"network.{UCI_NAME}"])

    if UCI_NAME not in show.stdout:

        for k, v in [("", "interface"), ("ifname", PPP_IFACE), ("proto", "none"), ("auto", "0")]:

            run_quiet(["uci", "set", f"network.{UCI_NAME}{'.' + k if k else ''}={v}"], timeout=3)

        log("UCI srun 接口已创建", L_OK)

    if _firewall_add_srun(): log("srun 已加入 WAN 防火墙区域", L_OK)

    run_quiet(["uci", "commit"])



    try:

        v4, v6 = _ppp0_info()

        if v4:

            run_quiet(["uci", "set", f"network.{UCI_NAME}.ipaddr={v4}"], timeout=3)

            log(f"ppp0 IPv4: {v4}", L_OK)

        try:

            # IPv6: 先检测内核是否支持 IPv6

            _ipv6_supported = os.path.exists("/proc/net/if_inet6")

            if not _ipv6_supported:

                log("IPv6: 内核不支持，跳过", L_DEBUG)

            else:

                # 配置 sysctl 以允许 SLAAC（强制 accept_ra=2）

                run_quiet(["sysctl", "-w", f"net.ipv6.conf.all.accept_ra=1"])

                run_quiet(["sysctl", "-w", f"net.ipv6.conf.{PPP_IFACE}.forwarding=0"])

                run_quiet(["sysctl", "-w", f"net.ipv6.conf.{PPP_IFACE}.accept_ra=2"])

                run_quiet(["sysctl", "-w", f"net.ipv6.conf.{PPP_IFACE}.accept_ra_pinfo=1"])

                _kill_odhcp6c()

                if run(["which", "odhcp6c"]).returncode == 0:

                    subprocess.Popen(["odhcp6c", "-d", "-N", "try", PPP_IFACE],

                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    log(f"IPv6: odhcp6c 已启动，{RA_WAIT_TIME}s 内等待 RA...", L_INFO)

                else:

                    log(f"IPv6: 内核 SLAAC, {RA_WAIT_TIME}s 内等待 RA...", L_INFO)

                for _ in range(RA_WAIT_TIME):

                    time.sleep(1)

                    _, v6 = _ppp0_info(force=True)

                    if v6: break

                if v6:

                    run_quiet(["uci", "set", f"network.{UCI_NAME}.ip6addr={v6}"], timeout=3)

                    log(f"ppp0 IPv6: {v6}", L_OK)

                    # 检查并添加默认 IPv6 路由

                    _r6 = run(["ip", "-6", "route"])

                    if "default" not in _r6.stdout:

                        run_quiet(["ip", "-6", "route", "add", "default", "dev", PPP_IFACE])

                        log("IPv6: 已添加默认路由", L_OK)

                    # ip6tables 防火墙规则

                    if _ipv6_supported and run(["which", "ip6tables"]).returncode == 0:

                        c6 = ["ip6tables", "-C", "FORWARD", "-i", "br-lan", "-o", PPP_IFACE, "-j", "ACCEPT"]

                        if run(c6).returncode != 0:

                            run_quiet(["ip6tables", "-A", "FORWARD", "-i", "br-lan", "-o", PPP_IFACE, "-j", "ACCEPT"])

                            log("ip6tables FORWARD 已添加", L_OK)

                else:

                    log("IPv6: 首次等待未收到 RA，延迟重试...", L_DEBUG)

                    time.sleep(RA_RETRY_DELAY)

                    _, v6 = _ppp0_info(force=True)

                    if v6:

                        run_quiet(["uci", "set", f"network.{UCI_NAME}.ip6addr={v6}"], timeout=3)

                        log(f"ppp0 IPv6 (延迟): {v6}", L_OK)

                        _r6 = run(["ip", "-6", "route"])

                        if "default" not in _r6.stdout:

                            run_quiet(["ip", "-6", "route", "add", "default", "dev", PPP_IFACE])

                    else:

                        log("IPv6: 未收到 RA（BRAS 可能未下发前缀）", L_WARN)

        except Exception as e: log(f"IPv6 配置异常: {e}", L_WARN)

        run_quiet(["uci", "commit"])

    except Exception as e: log(f"IP 检测异常: {e}", L_WARN)

    # NTP 时间同步（连接成功后有网络才执行）
    log("NTP 时间同步中...", L_INFO)
    for _ntp_srv in ["ntp.aliyun.com", "ntp.tencent.com", "cn.pool.ntp.org"]:
        r = run_quiet(["ntpd", "-n", "-p", _ntp_srv, "-q"], timeout=10)
        if r.returncode == 0:
            log(f"时间已同步 (来源: {_ntp_srv})", L_OK)
            break
        else:
            log(f"NTP {_ntp_srv} 失败，尝试下一个...", L_DEBUG)
    else:
        run_quiet(["ntpd", "-n", "-p", "ntp.aliyun.com", "-p", "ntp.tencent.com", "-q"], timeout=15)
        log("NTP 时间同步完成", L_OK)

    log("后置配置完成", L_OK)

    PPP_UP_TS = time.time()



def uci_cleanup():

    try:

        run_quiet(["uci", "delete", f"network.{UCI_NAME}"])

        _firewall_remove_srun()

        run_quiet(["uci", "commit"])

        run_quiet(["rm", "-f", "/tmp/dnsmasq.d/srun-dns.conf", "/etc/dnsmasq.d/srun-dns.conf"])

    except Exception as _e: debug_exc("uci_cleanup", _e)



def pppd_start():

    """启动 pppd，处理 /dev/ppp 抢占冲突"""

    # 预检：检查 pppd 插件是否存在

    if not os.path.exists(PLUGIN):

        log(f"pppd 插件不存在: {PLUGIN}", L_FATAL)

        log("请安装: opkg update && opkg install ppp-mod-pppoe", L_FATAL)

        return None, 0

    run_quiet(["killall", "-9", "pppd"])

    time.sleep(2)

    recreate_dev_ppp()

    write_secrets()

    try:

        encoded_user = srun_encode_v4(RUNTIME["usr"]).decode("latin-1")

    except Exception as e: log(f"S4 编码失败: {e}", L_FATAL); return None, 0

    log(f"编码用户名 ({len(encoded_user)} 字符)")

    # 密码通过命令行参数传递到 pppd（出现在 /proc/PID/cmdline 中）。

    # 安全说明：OpenWrt 上仅 root 可读 /proc/PID/cmdline，且 busybox ps 默认截断参数。

    # pppd 2.4.7（OpenWrt 当前发行版）不支持 passwordfd 从 fd 读取密码，

    # 此功能需要 pppd 2.5+。在升级到 pppd 2.5+ 前不应尝试改为 passwordfd 方案。

    # 此外，pppd 自身 logfile 的 debug 输出会对密码做掩码处理，不泄露。

    cmd = [PPPD, "plugin", PLUGIN, IFACE, "rp_pppoe_service", SVC, "noauth",

           "user", encoded_user, "password", RUNTIME["pwd"],

           "mru", "1492", "mtu", "1492", "noaccomp", "nopcomp",

           "holdoff", "3", "noipdefault", "defaultroute", "usepeerdns",

           "+ipv6", "ipv6cp-use-ipaddr",

           "persist", "maxfail", "0",

           "lcp-echo-interval", "10", "lcp-echo-failure", "3",

           "nodetach", "debug", "logfile", LOG]

    try:

        log_pos = os.path.getsize(LOG) if os.path.exists(LOG) else 0

    except OSError:

        log_pos = 0

    for retry in range(PPP_RETRY):

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,

                                 start_new_session=True)

        time.sleep(1)

        if proc.poll() is None:

            log(f"PID: {proc.pid} (尝试 {retry+1})")

            return proc, log_pos

        _, err = proc.communicate(timeout=1)

        err_msg = err.decode(errors="replace")[:200].strip()

        log(f"pppd 快速退出 (尝试 {retry+1})", L_WARN)

        if err_msg: log(f"  pppd 错误: {err_msg}", L_WARN)

        run_quiet(["killall", "-9", "pppd"])

        time.sleep(1)

        recreate_dev_ppp()

    log("pppd 启动失败（3 次重试均退出）", L_FATAL)

    return None, 0



def monitor(proc, timeout=40, log_pos=0):

    t0 = time.time(); last_pos = log_pos; next_report = PING_INTERVAL

    while time.time() - t0 < timeout:

        elapsed = int(time.time() - t0)

        v4, _ = _ppp0_info()

        if v4: log(f"已连接! inet {v4}"); return True, False

        try:

            with open(LOG) as f:

                curr_size = os.path.getsize(LOG)

                if last_pos > curr_size: last_pos = 0

                f.seek(last_pos)

                for line in f:

                    if 'E2620' in line or 'Modem hangup' in line: log("会话被BRAS终止(Modem hangup)", L_WARN); return False, True

                last_pos = f.tell()

        except Exception as _e: debug_exc("monitor:log", _e)

        if proc.poll() is not None:

            log(f"pppd 已退出 ({proc.returncode})", L_WARN)

            try:

                with open(LOG) as f:

                    for l in f.readlines()[-5:]: log(f"  {l.strip()}", L_INFO)

            except OSError: pass

            return False, False

        if elapsed >= next_report: log(f"等待连接中... ({elapsed}s)", L_INFO); next_report += PING_INTERVAL

        time.sleep(LOG_POLL_INTERVAL)

    log("连接超时", L_WARN); return False, False



def pppoe_connect():

    # 移除NSS硬件PPPoE模块（IPQ60xx兼容性，否则pppd会被SIGKILL杀退）
    run_quiet(["rmmod", "qca_nss_pppoe"], timeout=3)

    for attempt in range(CONNECT_RETRY_COUNT):

        log(f"第 {attempt+1}/{CONNECT_RETRY_COUNT} 次尝试")

        if not create_iface(): time.sleep(5); continue

        proc, log_pos = pppd_start()

        if not proc: continue

        ok, e2620 = monitor(proc, timeout=CONNECT_TIMEOUT, log_pos=log_pos)

        if ok:

            try: post_setup()

            except Exception as e: log(f"后置配置异常 (非致命): {e}", L_WARN)

            # 连通性检查

            try:

                _r = run(["ip", "route"])

                if _r.returncode == 0 and "default" not in _r.stdout:

                    run_quiet(["ip", "route", "add", "default", "dev", PPP_IFACE])

                    log("已添加默认路由", L_OK)

            except Exception: pass

            # MSS clamp：修正PPPoE TCP分包（MTU 1492，需钳制MSS）
            _mss_src = ["iptables", "-t", "mangle", "-C", "FORWARD", "-p", "tcp",
                        "--tcp-flags", "SYN,RST", "SYN", "-j", "TCPMSS", "--clamp-mss-to-pmtu"]
            _mss_add = ["iptables", "-t", "mangle", "-A", "FORWARD", "-p", "tcp",
                        "--tcp-flags", "SYN,RST", "SYN", "-j", "TCPMSS", "--clamp-mss-to-pmtu"]
            if run(_mss_src).returncode != 0:
                run_quiet(_mss_add)

            if not _check_connectivity():

                log("连接成功但无法访问外网（检查路由/DNS）", L_WARN)

            else:

                log("外网连通性正常", L_OK)

            return True

        else:

            stop()

            # 给 BRAS 足够时间释放 session（pppd PADT + BRAS 处理 ≈ 3s）

            time.sleep(max(E2620_RETRY_DELAY if e2620 else 5, 3))

    log("所有尝试均失败", L_ERROR)
    print("  ⚠ 提示: 检查设备时间是否正确，如不正确请使用菜单 8 重新设置")
    print(f"  ⚠ 当前系统时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return False



def stop():

    global PPP_UP_TS

    uci_cleanup()

    kill_pppd()

    run_quiet(["ip", "link", "delete", IFACE])

    _kill_odhcp6c()

    if os.path.exists(PID_FILE): os.remove(PID_FILE)

    _invalidate_ppp0_cache()

    PPP_UP_TS = 0

    log("已断开")

def _kill_daemon_directly():
    """直接停掉 daemon 进程（绕过 init/procd 的同步嵌套调用）

    原因：procd stop_service 内部同步调用 `srun.py stop` -> cmd_disconnect
    -> 如果 cmd_disconnect 又调用 init stop，会构成进程内同步嵌套调用链
    -> 外层 init stop 等 srun.py stop 完成，srun.py stop 内的 init stop 又等
    -> procd 完成 -> 整个调用链 15s+ 超时

    正确做法：菜单断开是"外部"调用，直接杀 daemon 进程 + 清理 pppd 即可。
    init stop 调用 srun.py stop 时也是同样的需求，因此共享这条路径。
    """
    # 收集要杀的 PID 列表
    targets = set()
    # 1) 从 PID_FILE 取 daemon PID
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f: targets.add(int(f.read().strip()))
        except (OSError, ValueError): pass
    # 2) 兜底：扫描所有 srun.py daemon 命令行（不杀自己）
    my_pid = os.getpid()
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit(): continue
            p = int(entry)
            if p == my_pid: continue
            try:
                with open(f"/proc/{p}/cmdline", "rb") as f:
                    cl = f.read().decode("utf-8", errors="replace").replace("\0", " ")
                if "srun.py" in cl and "daemon" in cl:
                    targets.add(p)
            except (OSError, IOError, PermissionError): pass
    except OSError: pass

    # 3) 先 SIGTERM，等 1.5s，再 SIGKILL 兜底（IPQ60xx 上 NSS bug 可能让 pppd 阻塞 SIGTERM）
    for pid in targets:
        try: os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError): pass
    time.sleep(1.5)
    for pid in targets:
        try:
            os.kill(pid, 0)  # 检查是否还活着
            os.kill(pid, signal.SIGKILL)  # 还在就强杀
        except (OSError, ProcessLookupError): pass
    # 4) 清理 PID_FILE（即便 daemon 没被强杀，也保证下次 cmd_connect 重新判断）
    try: os.remove(PID_FILE)
    except OSError: pass

def cmd_disconnect():
    """断开：菜单场景下直接停 daemon + 清理 pppd，不嵌套调用 init stop

    修复点：从菜单调用时直接 _kill_daemon_directly() + stop()，
    不再走 init stop（init stop 在 procd 内部会同步调用 srun.py stop，
    而 srun.py stop 又走 cmd_disconnect，构成进程内同步嵌套，触发 15s 超时）。
    """
    log("正在断开连接...", L_INFO)
    # 1) 先杀 daemon 进程（让 procd 看到子进程退出）
    _kill_daemon_directly()
    # 2) 等 daemon 释放资源（pppd 优雅断 PADT）
    time.sleep(2)
    # 3) 兜底清理 pppd + 接口（即使 daemon 已死也确保 ppp0 消失）
    if _ppp0_has_ip():
        stop()
    log("已断开", L_OK)

def _cleanup_procs(procs):

    """清理残留的 curl 进程（TERM → 1s 优雅等待 → KILL）"""

    for p in procs.values():

        try: p.terminate()

        except (ProcessLookupError, OSError): pass

    time.sleep(1)

    for p in list(procs.values()):

        try: p.kill()

        except (ProcessLookupError, OSError): pass



def _check_connectivity():

    """并行检测内外网连通性。任一成功即判定在线"""

    targets = [

        ("内网", "http://web.nchu.edu.cn", 3),

        ("外网1", "http://baidu.com", 5),

        ("外网2", "http://qq.com", 5),

        ("外网3", "http://taobao.com", 5),

    ]

    procs = {}

    try:

        for name, url, to in targets:

            p = subprocess.Popen(

                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",

                 "--connect-timeout", str(to), "-m", str(to + 3), url],

                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            procs[name] = p



        deadline = time.time() + CONNECTIVITY_TIMEOUT

        while time.time() < deadline:

            for name, p in list(procs.items()):

                if p.poll() is not None:

                    code = p.stdout.read().decode().strip()

                    if code in ("200", "301", "302"):

                        return True

                    del procs[name]

            time.sleep(0.1)



        if "内网" in procs:

            try:

                out, _ = procs["内网"].communicate(timeout=3)

                if out.decode().strip() in ("200", "301", "302"):

                    return True

            except (OSError, subprocess.TimeoutExpired, ValueError): pass

        return False

    finally:

        _cleanup_procs(procs)



def _ping_loop():

    log(f"启动连通性监控: HTTP 检测 baidu.com 每 {PING_INTERVAL}s", L_OK)

    fail_count = 0

    # 状态横幅
    _s = []
    if _ppp0_has_ip():
        _ip = run(["ip", "-4", "addr", "show", PPP_IFACE])
        if _ip.returncode == 0:
            for _line in _ip.stdout.split(chr(10)):
                if "inet " in _line:
                    _a = _line.strip().split()[1].split("/")[0]
                    _s.append(f"已连接 ({_a})")
                    break
        else:
            _s.append("已连接")
    else:
        _s.append("离线")
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f: pid = int(f.read().strip())
            os.kill(pid, 0)
            _s.append("守护运行中")
        except: pass
    elif os.path.exists(INIT_SCRIPT):
        try:
            _r = subprocess.run([INIT_SCRIPT, "enabled"], capture_output=True, timeout=3)
            if _r.returncode == 0:
                _s.append("自启已装")
        except: pass
    _status = " | ".join(_s) if _s else "未连接"
    print(f"  状态: {_status}")
    print()
    while True:

        time.sleep(PING_INTERVAL)

        if not _ppp0_has_ip():

            log("ppp0 丢失，准备重连", L_WARN); return False

        if _check_connectivity():

            fail_count = 0

        else:

            fail_count += 1

            log(f"外网连通性检测失败 ({fail_count}/{CONNECTIVITY_FAIL_THRESHOLD})，继续监控", L_WARN)

            if fail_count >= CONNECTIVITY_FAIL_THRESHOLD:

                log(f"连续 {CONNECTIVITY_FAIL_THRESHOLD} 次检测失败，触发重连", L_WARN); return False



def _check_cred():

    if _is_default_cred():

        print()

        print("========== 错误 ==========")

        print("请先配置上网账号和密码！")

        print("选菜单 3) 账号配置 或运行: python3 srun.py reconfig")

        print("===========================")

        return False

    return True



def cmd_connect():
    """连接：有自启服务则启动daemon，无则手动拨号"""
    if not _check_cred(): return

    if os.path.exists(INIT_SCRIPT):
        _pid_alive = False
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE) as f: pid = int(f.read().strip())
                os.kill(pid, 0)
                with open(f"/proc/{pid}/cmdline", "rb") as cf:
                    _pid_alive = b"srun.py" in cf.read()
            except: pass
        if not _pid_alive:
            log("启动开机自启服务...", L_INFO)
            subprocess.run([INIT_SCRIPT, "start"], timeout=15)
            time.sleep(2)
        log("守护进程运行中，最近日志:", L_INFO)
        try:
            _r = run(["tail", "-8", LOG])
            for _l in _r.stdout.strip().split(chr(10))[-6:]:
                if _l.strip(): print(f"  {_l}")
        except: pass
        return

    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f: pid = int(f.read().strip())
            os.kill(pid, 0)
            with open(f"/proc/{pid}/cmdline", "rb") as cf:
                if b"srun.py" in cf.read():
                    log("看守进程已在运行中", L_INFO); return
        except: pass

    if _ppp0_has_ip():
        log("ppp0 已有 IP，执行后置配置", L_INFO)
        try: post_setup()
        except Exception as e: log(f"后置配置异常: {e}", L_WARN)
        log("已连接", L_OK); return

    _kill_competing()
    time.sleep(2)
    if pppoe_connect(): log("已连接", L_OK)
    else:
        log("连接失败", L_ERROR)
        print("  ⚠ 提示: 检查设备时间是否正确，如不正确请使用菜单 8 重新设置")
        print(f"  ⚠ 当前系统时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

def cmd_daemon():

    if not _check_cred():

        sys.exit(1)

    def _sigterm(signum, frame):

        try:

            if os.path.exists(PID_FILE): os.remove(PID_FILE)

        except OSError: pass

        stop(); log("守护进程退出", L_WARN); sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm)

    signal.signal(signal.SIGINT, _sigterm)

    with open(PID_FILE, "w") as f: f.write(str(os.getpid()))

    atexit.register(lambda: os.remove(PID_FILE) if os.path.exists(PID_FILE) else None)

    log("看守进程启动")

    # 如果已有连接，直接进入监控而非重建（避免 E2620 冲突）

    if _ppp0_has_ip():

        log("检测到已有连接，直接进入连通性监控", L_OK)

        _ping_loop()

        log("连通性检测失败，清理后重连", L_WARN); stop()

    while True:

        log("正在连接...")

        try:

            if pppoe_connect():

                log("已连接，开始连通性监控", L_OK)

                _ping_loop()

                log("连通性检测失败，清理后重连", L_WARN); stop()

        except Exception as e: log(f"连接异常: {e}", L_WARN)

        log("10 秒后重试..."); time.sleep(10)



def _raw_input(prompt):

    """安全读取用户输入，避免 OpenWrt readline 对 @ 等字符的损坏（最多重试 3 次）"""

    sys.stdout.write(prompt); sys.stdout.flush()

    for _ in range(3):

        try:

            return sys.stdin.buffer.readline().decode("utf-8", errors="strict").strip()

        except UnicodeDecodeError:

            log("输入包含非 UTF-8 字符，请在纯 ASCII 终端中重试", L_ERROR)

    log("输入多次解码失败，已跳过", L_WARN)

    return ""



def cmd_settime():

    """手动设置系统时间"""

    import re

    print()

    print("========== 设置系统时间 ==========")

    print(f"当前: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    t = _raw_input("新时间 (14位: YYYYMMDDHHMMSS，回车取消): ").strip()

    if not t:

        print("已取消"); return

    m = re.match(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$', t)

    if not m:

        log("格式错误，需要14位数字: YYYYMMDDHHMMSS", L_ERROR)

        return

    y, mo, d, h, mi, s = m.groups()

    r = run(["date", "-s", f"{y}-{mo}-{d} {h}:{mi}:{s}"])

    if r.returncode != 0:

        log(f"设置失败: {r.stderr}", L_ERROR)

    else:

        print(f"已设置: {time.strftime('%Y-%m-%d %H:%M:%S')}")



def cmd_reconfig():

    """账号配置（含 DNS）"""

    print()

    print("========== 账号配置 ==========")

    print(f"当前用户名: {RUNTIME['usr']}")

    print(f"当前密码:   {RUNTIME['pwd']}")

    print(f"当前 DNS1:  {RUNTIME['dns1']}")

    print(f"当前 DNS2:  {RUNTIME['dns2']}")

    u = _raw_input("新用户名 (直接回车不变): ")

    p = _raw_input("新密码 (直接回车不变): ")

    d1 = _raw_input(f"新 DNS1 (直接回车 {RUNTIME['dns1']}): ").strip() or RUNTIME["dns1"]

    d2 = _raw_input(f"新 DNS2 (直接回车 {RUNTIME['dns2']}): ").strip() or RUNTIME["dns2"]

    up, down = detect_interfaces()

    print(f"\n当前物理接口: {RUNTIME['phy']}")

    print("可用网口:")

    for i in up: print(f"  {fmt_iface_color(i, 'up')}  (已连接)")

    for i in down: print(f"  {fmt_iface_color(i, 'down')} (未连接)")

    n_phy = _raw_input(f"网口 (直接回车使用 {RUNTIME['phy']}): ") or RUNTIME["phy"]

    if not u and not p and n_phy == RUNTIME["phy"] and d1 == RUNTIME["dns1"] and d2 == RUNTIME["dns2"]:

        print("未做修改"); return

    nu = u or RUNTIME["usr"]; np = p or RUNTIME["pwd"]

    print(f"\n即将保存:")

    print(f"  用户名: {nu}")

    print(f"  密码:   {np}")

    print(f"  DNS1:   {d1}")

    print(f"  DNS2:   {d2}")

    print(f"  网口:   {n_phy}")

    ok = _raw_input("确认？[Y/n]: ").strip().lower()

    if ok in ("n", "no"): print("已取消"); return

    save_all_cfg(nu, np, n_phy, d1, d2)

    print(f"已保存")

    a = _raw_input("是否重新启动连接？[Y/n]: ").strip().lower()

    if a not in ("n", "no"): stop(); time.sleep(2); cmd_connect()

    else: print("如需重新连接请选菜单 1")



# ═══════════════════════════════════════════════════════════

# 监控面板

# ═══════════════════════════════════════════════════════════

def cmd_monitor():

    if not sys.stdin.isatty(): print("面板需要交互终端"); return

    def esc(s): return f"\033[{s}"

    def fullscreen(): print(esc("?1049h") + esc("?25l"), end="", flush=True)

    def exit_scr(): print(esc("?25h") + esc("?1049l"), end="", flush=True)

    fullscreen()

    _frame_count = 0

    prev_rx = 0; prev_tx = 0; prev_ts = time.time()

    _status_cache = (0, "停止", "是" if os.path.exists(INIT_SCRIPT) else "否")

    try:

        while True:

            now = time.strftime('%m-%d %H:%M:%S')

            # 每 1s 刷新，每 5 帧强制刷新一次缓存（避免缓存锁住无 IP 状态）

            _frame_count += 1

            v4, v6 = _ppp0_info(force=(_frame_count % PPP_INFO_CACHE_TTL == 0))

            iface = RUNTIME["phy"]



            rx_bytes = 0; tx_bytes = 0

            try:

                with open("/proc/net/dev") as f:

                    for line in f:

                        if line.lstrip().startswith(PPP_IFACE + ":"):

                            parts = line.split()

                            rx_bytes = int(parts[1]); tx_bytes = int(parts[9])

            except Exception as _e: debug_exc("cmd_monitor:proc_net", _e)



            curr_ts = time.time(); elapsed = curr_ts - prev_ts

            if prev_rx > 0 and elapsed > 0:

                down = max(0, rx_bytes - prev_rx) / 1024 / 1024 / elapsed

                up = max(0, tx_bytes - prev_tx) / 1024 / 1024 / elapsed

            else:

                down = 0; up = 0

            prev_rx = rx_bytes; prev_tx = tx_bytes; prev_ts = curr_ts



            up_str = "-"

            if v4:

                e = 0

                if PPP_UP_TS > 0:

                    # 同进程：用内存变量最精确

                    e = int(time.time() - PPP_UP_TS)

                else:

                    # 跨进程：回退到 sysfs 文件时间戳

                    try:

                        r = run(["stat", "-c", "%Y", f"/sys/class/net/{PPP_IFACE}"])

                        if r.returncode == 0 and r.stdout.strip():

                            e = int(time.time()) - int(r.stdout.strip())

                    except (OSError, ValueError): pass

                if e > 0:

                    h, m, s = e // 3600, (e % 3600) // 60, e % 60

                    up_str = f"{h:02d}:{m:02d}:{s:02d}"



            # 服务/自启状态带缓存（不会每秒变化）

            st_ts, st_d, st_boot = _status_cache

            if curr_ts - st_ts > STATUS_CACHE_TTL:

                daemon_running = False

                # 优先查 PID 文件（比 ps 更可靠）

                if os.path.exists(PID_FILE):

                    try:

                        with open(PID_FILE) as f:

                            pid = int(f.read().strip())

                        os.kill(pid, 0)

                        daemon_running = True

                    except (OSError, ValueError, PermissionError): pass

                if not daemon_running:

                    try:

                        r = run(["ps", "ww"])

                        if 'srun.py daemon' in r.stdout:

                            daemon_running = True

                    except OSError: pass

                if daemon_running:

                    d = "重连中" if not v4 else "运行中"

                elif v4:

                    d = "手动"  # 有 IP 但无看守进程

                else:

                    d = "停止"

                boot = "是" if os.path.exists(INIT_SCRIPT) else "否"

                _status_cache = (curr_ts, d, boot)

            else:

                d, boot = st_d, st_boot



            w = 48

            buf = []

            buf.append("=" * w + "\n")

            buf.append("  srun PPPoE 监控面板  (回车返回)\n")

            buf.append("-" * w + "\n")

            buf.append(f"  时间: {now}\n")

            buf.append(f"  服务: {d}\n")

            buf.append(f"  自启: {boot}\n")

            buf.append(f"  IPv4: {v4 or '无'}\n")

            buf.append(f"  IPv6: {v6 or '无'}\n")

            buf.append(f"  出口: {iface}\n")

            buf.append(f"  在线: {up_str}\n")

            buf.append("-" * w + "\n")

            buf.append(f"  下行: {down:.2f} MB/s\n")

            buf.append(f"  上行: {up:.2f} MB/s\n")

            buf.append("=" * w)

            print(f"\033[H\033[J{''.join(buf)}", end="", flush=True)



            if select.select([sys.stdin], [], [], MONITOR_REFRESH)[0]:

                sys.stdin.readline(); break

    except KeyboardInterrupt: pass

    finally: exit_scr()



# ═══════════════════════════════════════════════════════════

# 安装 / 卸载

# ═══════════════════════════════════════════════════════════

INIT_SCRIPT_CONTENT = """#!/bin/sh /etc/rc.common

# srun-pppoe - 校园网 PPPoE 开机自启服务

START=99; STOP=10; USE_PROCD=1

start_service() {

    procd_open_instance

    procd_set_param command /usr/bin/python3 /root/srun/srun.py daemon

    procd_set_param respawn 5 10 5

    procd_set_param stdout 1; procd_set_param stderr 1

    procd_set_param pidfile /var/run/srun-watchdog.pid

    procd_close_instance

}

stop_service() { /usr/bin/python3 /root/srun/srun.py stop 2>/dev/null; }

"""



def cmd_install():

    dst = "/root/srun/srun.py"

    self_path = os.path.realpath(__file__)

    if self_path != dst:

        try:

            subprocess.run(["cp", self_path, dst], check=True)

            subprocess.run(["chmod", "+x", dst], check=True)

            save_all_cfg()

        except Exception as e: print(f"复制失败: {e}"); return

    print(f"已安装: {dst}")

    try:

        with open(INIT_SCRIPT, "w") as f: f.write(INIT_SCRIPT_CONTENT.replace("/usr/bin/python3", os.path.realpath(sys.executable)))

        os.chmod(INIT_SCRIPT, 0o755)

        subprocess.run([INIT_SCRIPT, "enable"], capture_output=True)

        print(f"init 脚本已安装: {INIT_SCRIPT}")

    except Exception as e: print(f"init 脚本安装失败: {e}"); return

    a = _raw_input("现在启动服务？[Y/n]: ").lower()

    if a not in ("n", "no"):

        try:

            subprocess.run([INIT_SCRIPT, "start"], check=True)

            print("服务已启动")

            if _ppp0_has_ip():

                print("看守进程已接管当前连接")

        except subprocess.CalledProcessError:

            print("服务启动失败，请手动检查 /etc/init.d/srun-pppoe start")



def cmd_uninstall():

    a = _raw_input("确认卸载？输入 yes 确认: ")

    if a != "yes": print("已取消"); return

    if os.path.exists(INIT_SCRIPT):

        # 修复：先直接杀 daemon 进程，再 disable，最后清理 init 脚本
        # （原版用 init stop 会触发 srun.py stop -> cmd_disconnect -> init stop 嵌套死锁）
        _kill_daemon_directly()
        time.sleep(1)
        subprocess.run([INIT_SCRIPT, "disable"], capture_output=True)
        try: os.remove(INIT_SCRIPT)
        except OSError as e: log(f"删除 init 脚本失败: {e}", L_WARN)

    stop()

    print("PPPoE 已断开（脚本文件未被删除，可继续使用菜单）")



# ═══════════════════════════════════════════════════════════

# 菜单

# ═══════════════════════════════════════════════════════════

def _is_default_cred():

    return not os.path.exists(CFG) or RUNTIME["usr"] == DEFAULT_USR or RUNTIME["pwd"] == DEFAULT_PWD



def cmd_reconnect():
    """重新连接：先同步断开，再停 daemon，最后重启服务

    修复 v7.0-b 的时序问题：
    原先顺序是"杀daemon → 等 → 启动服务"，但 daemon 的 SIGTERM 回调
    `_sigterm → stop()` 是异步执行的。新 daemon 启动时旧 daemon 的清理
    还没完成 → 两台 daemon + pppd 竞争 macvlan/ppp0 → 日志显示 PADT
    断开发生在"服务已重启"之后。

    正确顺序：先 `stop()` 同步清理 pppd/macvlan/UCI，
    再 `_kill_daemon_directly()` 停 daemon（此时 daemon 无连接，
    _sigterm 里的 stop() 几乎立即返回），最后 `init start` 从头开始。
    """
    log("重新连接中...", L_INFO)
    if os.path.exists(INIT_SCRIPT):
        # 步骤1: 先断开现有连接（同步清理 pppd、macvlan、UCI）
        log("  步骤1: 断开现有连接...", L_INFO)
        stop()
        time.sleep(1)

        # 步骤2: 停掉 daemon 进程（此时已无连接，daemon 退出迅速）
        log("  步骤2: 停用服务...", L_INFO)
        _kill_daemon_directly()
        time.sleep(2)

        # 步骤3: 启动服务（daemon 自动创建新 macvlan 并拨号）
        log("  步骤3: 启动服务...", L_INFO)
        for _attempt in range(3):
            r = subprocess.run([INIT_SCRIPT, "start"], capture_output=True, timeout=15)
            if r.returncode == 0: break
            time.sleep(1.5)
        time.sleep(2)

        # 步骤4: 等待新 daemon 建立连接，检查 ppp0 状态
        log("  步骤4: 等待 daemon 连接中... (8s)", L_INFO)
        _connected = False
        for _i in range(8):
            time.sleep(1)
            _invalidate_ppp0_cache()
            v4, _ = _ppp0_info()
            if v4:
                _connected = True
                break

        if _connected:
            log("连接已恢复", L_OK)
            log(f"  IPv4: {v4}", L_INFO)
        else:
            log("daemon 正在后台重试，请用菜单 5 查看连接状态", L_INFO)
        return

    # 无 init 服务：手动断开 + 手动拨号
    log("  步骤1: 断开当前连接...", L_INFO)
    if _ppp0_has_ip() or os.path.exists(PID_FILE):
        stop()
        time.sleep(2)
    log("  步骤2: 重新拨号...", L_INFO)
    cmd_connect()

def cmd_menu():

    if _is_default_cred():

        print()

        print("========== 首次使用 ==========")

        print("检测到未配置账号信息。请先选菜单 3) 账号配置 设置上网账号和密码。")

        print("==============================")



    while True:

        print()

        print("========== srun PPPoE 管理 ==========")

        print("  1) 启动连接")

        print("  2) 断开连接")

        print("  3) 账号配置")

        print("  4) 查看日志")

        print("  5) 监控面板")

        print("  6) 安装开机自启")

        print("  7) 卸载服务")

        print("  8) 设置时间")
        print("  9) 重新连接")
        print("  0) 退出")

        op = _raw_input("请选择: ")

        if op == "1": cmd_connect()

        elif op == "2": cmd_disconnect()

        elif op == "3": cmd_reconfig()

        elif op == "4":

            try:

                r = run(["tail", "-30", LOG])

                for line in r.stdout.split('\n'):

                    if line.strip(): print(f"  {line}")

                    else: print()

            except Exception as _e: print(f"(无日志: {_e})")

        elif op == "5": cmd_monitor()

        elif op == "6": cmd_install()

        elif op == "7": cmd_uninstall()

        elif op == "8": cmd_settime()

        elif op == "9": cmd_reconnect()

        elif op == "0": break

        else: print("无效选项")

        _raw_input("\n按回车继续...")



# ═══════════════════════════════════════════════════════════

# 入口

# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":

    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "menu"

    h = {

        "connect": cmd_connect, "daemon": cmd_daemon,

        "stop": cmd_disconnect,

        "menu": cmd_menu, "reconfig": cmd_reconfig,

        "monitor": cmd_monitor, "install": cmd_install, "uninstall": cmd_uninstall,

        "settime": cmd_settime,

        "help": lambda: print(__doc__), "-h": lambda: print(__doc__), "--help": lambda: print(__doc__),

    }

    handler = h.get(cmd)

    if handler:

        try: handler()

        except KeyboardInterrupt: print("\n(已中断)")

    else: print(f"未知命令: {cmd}\n{__doc__}")

