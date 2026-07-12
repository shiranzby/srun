#!/bin/sh
#=============================================================================
# srun_dhcp.sh — 深澜 DHCP Portal 无线认证 OpenWrt 方案
# 说明: 纯 Shell 实现, 不依赖 bitsrun 二进制
# 依赖: curl 或 wget, awk, openssl, python3 (XXTEA+Base64 加密, 见下文)
#
# XXTEA 加密依赖说明:
#   默认方案: 调用 Python3 执行 XXTEA+Base64 加密 (最可靠)
#   纯 Shell 降级: 如有 python3, 则调用 Windows/DHCP/modules/crypto.py
#   无 Python3: 请先安装 (opkg install python3)
#=============================================================================

SCRIPT_PATH="$0"
if command -v readlink >/dev/null 2>&1; then
    resolved="$(readlink -f "$0" 2>/dev/null || true)"
    [ -n "$resolved" ] && SCRIPT_PATH="$resolved"
fi
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" 2>/dev/null && pwd)"
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR="$(pwd)"

# ── 配置 (可通过环境变量覆盖) ──
SRUN_HOST="${SRUN_HOST:-10.1.88.4}"
SRUN_USERNAME="${SRUN_USERNAME:-24046224@stu.nchu.edu.cn}"
SRUN_PASSWORD="${SRUN_PASSWORD:-shy0618123.}"
AC_ID="${AC_ID:-1}"
GUARDIAN_DURATION="${GUARDIAN_DURATION:-3}"
LOG_FILE="${LOG_FILE:-/var/log/srun_dhcp.log}"

# ── Python3 检测 ──
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi


# ===================== 工具函数 =====================
# 说明: 日志/命令检测/网络检测 等通用工具函数

log_msg() {
    echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG_FILE" 2>/dev/null
}

log_info()  { log_msg "[INFO]  $1"; }
log_warn()  { log_msg "[WARN]  $1"; }
log_error() { log_msg "[ERROR] $1"; }


# ===================== 加密函数 =====================
# 说明: XXTEA + 自定义 Base64 加密 (深澜 DHCP Portal 认证必需)
#
# 依赖: Python3 (推荐, 最可靠)
#   如无 Python3, 则需安装: opkg install python3
#
# 实现策略:
#   1. 优先调用 Python3 执行加密 (使用 Windows/DHCP/modules/crypto.py)
#   2. 找不到 crypto.py 时报错退出

# 调用 Python3 执行 XXTEA 加密
# 输入: $1=data_str, $2=key_str
# 输出: 加密后的 Base64 字符串
srun_xencode() {
    [ -z "$PYTHON_BIN" ] && { log_error "需要 python3 进行加密"; return 1; }

    # 尝试多种路径查找 crypto.py
    local crypto_py=""
    for p in \
        "${SCRIPT_DIR}/../DHCP/modules/crypto.py" \
        "/usr/lib/srun_dhcp/crypto.py" \
        "/usr/bin/srun_crypto.py"; do
        if [ -f "$p" ]; then
            crypto_py="$p"
            break
        fi
    done

    if [ -z "$crypto_py" ]; then
        log_error "找不到 crypto.py, 请安装 Python3 并将 crypto.py 放在脚本同级目录"
        return 1
    fi

    $PYTHON_BIN -c "
import sys, json, os
sys.path.insert(0, os.path.dirname('$crypto_py'))
from crypto import xencode
data = sys.argv[1]
key = sys.argv[2]
print(xencode(data, key))
" "$1" "$2" 2>/dev/null || echo ""
}

# 调用 Python3 计算 HMAC-MD5 密码哈希
srun_password_calc() {
    [ -z "$PYTHON_BIN" ] && { log_error "需要 python3 进行加密"; return 1; }

    local crypto_py=""
    for p in \
        "${SCRIPT_DIR}/../DHCP/modules/crypto.py" \
        "/usr/lib/srun_dhcp/crypto.py" \
        "/usr/bin/srun_crypto.py"; do
        if [ -f "$p" ]; then
            crypto_py="$p"
            break
        fi
    done

    [ -z "$crypto_py" ] && { log_error "找不到 crypto.py"; return 1; }

    $PYTHON_BIN -c "
import sys, os
sys.path.insert(0, os.path.dirname('$crypto_py'))
from crypto import srun_password_hash
token = sys.argv[1]
pwd = sys.argv[2]
print(srun_password_hash(token, pwd))
" "$1" "$2" 2>/dev/null || echo ""
}

# 调用 Python3 计算 SHA1 校验和
srun_chksum_calc() {
    [ -z "$PYTHON_BIN" ] && { log_error "需要 python3 进行加密"; return 1; }

    local crypto_py=""
    for p in \
        "${SCRIPT_DIR}/../DHCP/modules/crypto.py" \
        "/usr/lib/srun_dhcp/crypto.py" \
        "/usr/bin/srun_crypto.py"; do
        if [ -f "$p" ]; then
            crypto_py="$p"
            break
        fi
    done

    [ -z "$crypto_py" ] && { log_error "找不到 crypto.py"; return 1; }

    $PYTHON_BIN -c "
import sys, os
sys.path.insert(0, os.path.dirname('$crypto_py'))
from crypto import srun_chksum, srun_sha1
# 参数: token, username, md5_str, ac_id, ip, n, type_, info
joined = sys.argv[1]
print(srun_sha1(joined).strip())
" "$1" 2>/dev/null || echo ""
}


# ===================== 网络函数 =====================
# 说明: 获取本地 IP / 获取 challenge / 解析响应 / 登录 / 网络测试

# 获取本地 IPv4 (默认路由接口)
get_local_ip() {
    if have_cmd ip; then
        ip route show default 2>/dev/null | awk '/default/{print $7; exit}' || \
        ip route show default 2>/dev/null | awk '/default/{print $5; exit}'
    fi
    ifconfig 2>/dev/null | awk '/inet /{print $2}' | head -1 | cut -d/ -f1 || \
    echo "0.0.0.0"
}

# 获取 challenge (深澜认证第一步)
get_challenge() {
    local ip callback url
    ip="$(get_local_ip)"
    callback="jQuery$(date +%s%N 2>/dev/null || date +%s000)"
    url="http://${SRUN_HOST}/cgi-bin/get_challenge?callback=${callback}&username=$(python3_quote "${SRUN_USERNAME}")&ip=${ip}&_=$(date +%s%N 2>/dev/null || date +%s000)"

    http_get "$url"
}

# URL 编码 (Python3)
python3_quote() {
    $PYTHON_BIN -c "import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1" 2>/dev/null || echo "$1"
}

# HTTP GET (curl 或 wget 兼容)
http_get() {
    local url="$1"
    if have_cmd curl; then
        curl -s --connect-timeout 5 "$url" 2>/dev/null
    elif have_cmd wget; then
        wget -q -O- --timeout=5 "$url" 2>/dev/null
    else
        log_error "需要 curl 或 wget"
        echo ""
    fi
}

# HTTP POST (curl 或 wget 兼容)
http_post() {
    local url="$1"
    local data="$2"
    if have_cmd curl; then
        curl -s --connect-timeout 5 -X POST -d "$data" "$url" 2>/dev/null
    elif have_cmd wget; then
        wget -q -O- --timeout=5 --post-data="$data" "$url" 2>/dev/null
    else
        log_error "需要 curl 或 wget"
        echo ""
    fi
}

# 解析 challenge JSONP 响应
# 输入: JSONP 字符串
# 输出: "token client_ip" (空格分隔)
parse_challenge() {
    local body="$1"
    # JSONP: jQuery123(...); → 提取括号内JSON
    local json_str
    json_str="$(echo "$body" | sed 's/^[^(]*(\(.*\));\?$/\1/' 2>/dev/null)"
    if [ -z "$json_str" ]; then
        json_str="$body"
    fi
    # 用 Python3 解析 JSON (POSIX兼容: 用 pipe 替代 here-string)
    printf '%s\n' "$json_str" | $PYTHON_BIN -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('challenge',''), d.get('client_ip',''))
except Exception:
    pass
" 2>/dev/null || echo ""
}

# ===================== 登录流程 =====================

do_login() {
    local challenge_body token client_ip info_str encrypted_info hmd5 chksum
    local post_data url result

    log_info "获取 challenge..."
    challenge_body="$(get_challenge)"
    if [ -z "$challenge_body" ]; then
        log_error "获取 challenge 失败"
        return 1
    fi

    # 解析
    local parse_result
    parse_result="$(parse_challenge "$challenge_body")"
    token="$(echo "$parse_result" | awk '{print $1}')"
    client_ip="$(echo "$parse_result" | awk '{print $2}')"
    [ -z "$client_ip" ] && client_ip="$(get_local_ip)"
    [ -z "$token" ] && { log_error "token 为空"; return 1; }

    log_info "token: $(printf '%s' "$token" | cut -c1-20)..."

    # 构建 info
    info_str="{\"username\":\"${SRUN_USERNAME}\",\"password\":\"${SRUN_PASSWORD}\",\"ip\":\"${client_ip}\",\"acid\":\"${AC_ID}\",\"enc_ver\":\"srun_bx1\"}"

    # XXTEA + Base64
    encrypted_info="$(srun_xencode "$info_str" "$token")"
    if [ -z "$encrypted_info" ]; then
        log_error "加密失败"
        return 1
    fi
    encrypted_info="SRBX1${encrypted_info}"

    # 密码
    hmd5="$(srun_password_calc "$token" "$SRUN_PASSWORD")"

    # 校验和 (参数列表: token, username, md5_str, ac_id, ip, n, type_, info)
    local joined="${token}${SRUN_USERNAME}${token}${hmd5}${token}${AC_ID}${token}${client_ip}${token}200${token}1${token}${encrypted_info}"
    chksum="$(srun_chksum_calc "$joined")"

    log_info "发送登录请求..."
    url="http://${SRUN_HOST}/cgi-bin/srun_portal"

    # 构建 POST 数据
    post_data="action=login"
    post_data="${post_data}&username=$(python3_quote "${SRUN_USERNAME}")"
    post_data="${post_data}&password=$(python3_quote "${hmd5}")"
    post_data="${post_data}&ac_id=${AC_ID}"
    post_data="${post_data}&ip=${client_ip}"
    post_data="${post_data}&chksum=${chksum}"
    post_data="${post_data}&info=$(python3_quote "${encrypted_info}")"
    post_data="${post_data}&n=200&type=1&os=Linux&name=linux&double_stack=0"

    result="$(http_post "$url" "$post_data")"

    if echo "$result" | grep -qiE '"error":"ok"|"error_msg":"ok"|success'; then
        log_info "登录成功!"
        return 0
    elif echo "$result" | grep -qi "已登录\|already online\|online"; then
        log_info "已在线"
        return 0
    else
        log_error "登录失败: $(echo "$result" | head -c 200)"
        return 1
    fi
}

# ===================== 网络测试 =====================

test_network() {
    if have_cmd ping; then
        ping -c 2 -W 2 www.baidu.com >/dev/null 2>&1
    else
        # 无 ping 命令, 用 wget/curl 测试
        if have_cmd wget; then
            wget -q -O /dev/null --timeout=3 "http://www.baidu.com" 2>/dev/null
        elif have_cmd curl; then
            curl -s --max-time 3 "http://www.baidu.com" >/dev/null 2>&1
        else
            return 1
        fi
    fi
}

# ===================== 守护模式 =====================
# 说明: 定期检测网络, 断线自动重连

run_daemon() {
    local fail_count=0
    log_info "守护模式启动 (间隔 ${GUARDIAN_DURATION}s)"

    while true; do
        sleep "$GUARDIAN_DURATION"
        if ! test_network; then
            fail_count=$((fail_count + 1))
            log_warn "网络断开 (${fail_count})"
            if [ $fail_count -ge 2 ]; then
                log_info "尝试重新登录..."
                if do_login; then
                    fail_count=0
                    log_info "重登成功"
                fi
            fi
        else
            [ $fail_count -gt 0 ] && log_info "网络恢复"
            fail_count=0
        fi
    done
}

# ===================== 菜单 =====================

menu() {
    while true; do
        echo ""
        echo "========== 深澜 DHCP 管理 =========="
        echo "1) 登录"
        echo "2) 守护模式 (自动重连)"
        echo "0) 退出"
        printf "请选择: "
        read -r op
        case "$op" in
            1) do_login ;;
            2) run_daemon ;;
            0) exit 0 ;;
            *) echo "无效选项" ;;
        esac
    done
}

# ===================== 入口 =====================
# 用法: $0 [--login|--daemon|--menu]

case "${1:-}" in
    --login)    do_login ;;
    --daemon)   run_daemon ;;
    --menu|"")  menu ;;
    *)
        echo "用法: $0 [--login|--daemon|--menu]"
        echo ""
        echo "命令:"
        echo "  --login     登录校园网"
        echo "  --daemon    守护模式 (自动重连)"
        echo "  (无参数)    交互菜单"
        exit 1
        ;;
esac
