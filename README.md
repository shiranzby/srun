# srun-pppoe — 校园网 SRUN PPPoE 联网管理工具

> 适配 **Windows** / **Linux** / **OpenWrt** 的深澜 (Srun 4xxx) SRUN 认证 PPPoE 拨号解决方案。

---

## 🔐 深澜 S4 加密原理

深澜校园网认证使用自研 **S4/XEncode** 算法对用户名和时间戳进行编码：
1. 客户端与服务器协商时间戳（基于当前系统时间）
2. 将原始用户名 + S4 密钥 + 时间戳输入 `xencode()` 函数
3. 输出格式：`{S4}xxxxxx@原始用户名`（如 `{S4}1056b\r\n363834383a363638@stu.nchu.edu.cn`）
4. 编码后的用户名作为 PPPoE CHAP 认证的用户名发送
5. 密码保持明文传输（pppd 通过 chap-secrets 文件传递）

> **关键：系统时间必须正确，与北京时间相差需10秒内**，否则 S4 编码的 hash 与 BRAS 不匹配 → 认证被拒 (Error 691 / Modem hangup)。

---

## 📦 版本选择

| 版本 | 用途 | 特点 | 行数 |
|------|------|------|------|
| [**v7.2-a**](linux/pppoe/linux/pppoe/v7.2-a/srun.py) | 完整版 | 监控面板 / IPv6 / NTP 同步 / 日志系统 | 2,086 |
| [**v7.2-b**](linux/pppoe/v7.2-b/srun.py) | 精简版 | 极致精简，保留全部核心功能 | 313 |

### 核心功能
- ✅ S4 编码认证
- ✅ pppd 拨号 + LCP 保活
- ✅ 自动重试（Modem hangup / E2620 / 断线检测）
- ✅ 开机自启（procd 守护进程）
- ✅ 后置配置（DNS / iptables / UCI / 防火墙 WAN 区域）
- ✅ 断线自动重连
- ✅ 守护进程智能保活（仅 ppp0 消失才重启，不误杀）

---

## 🖥️ Windows 部署

### 前置条件

```bat
:: 检查 Python3 是否安装
python --version
:: 如果输出类似 Python 3.x.x 则正常
:: 否则请前往 https://www.python.org/downloads/ 安装
```

### 快速开始

```bat
:: 1. 编辑配置
notepad windows/pppoe/config.yaml

:: 2. 双击运行
windows/pppoe/connect_keep.bat
```

### 文件说明

```
windows/pppoe/
├── connect_keep.bat      # 一键启动（批处理）
├── connect_keep.py       # 主程序（S4 编码 + 拨号 + 保活）
├── disconnect.bat        # 断开连接
├── config.yaml           # 账号配置（用户名 / 密码 / 网卡）
├── test_connect.bat      # 测试连接
├── modules/
│   ├── encode.py          # S4/XEncode 编码模块
│   ├── monitor.py         # 连通性监控
│   └── util.py            # rasdial 拨号工具
```

---

## 🐧 Linux DHCP 部署

适用于不支持 PPPoE 的 Linux 桌面环境。

```
linux/dhcp/
├── srun_dhcp.sh          # DHCP + S4 认证脚本
└── config.yaml           # 配置
```

---

## 📡 OpenWrt 路由器部署

### 前置条件检查

```bash
# 1. 检查 Python3
python3 --version && echo "✅ Python3 OK" || echo "❌ Python3 未安装"

# 2. 检查 ppp-mod-pppoe
opkg list-installed | grep ppp-mod-pppoe && echo "✅ ppp-mod-pppoe OK" || echo "❌ ppp-mod-pppoe 未安装"

# 3. 检查 pppd
which pppd && echo "✅ pppd OK" || echo "❌ pppd 未安装"
```

### 方式一：SSH + SCP 部署（有网络）

<details>
<summary><code><strong>方式一：SSH + SCP 部署</strong></code> - 路由器有网，推荐</summary>
<br>

```bash
# 1. 查看路由器架构
uname -m
# 输出类似: aarch64 / mipsel_24kc / arm_cortex-a7 等

# 2. 如果缺依赖，安装
opkg update
opkg install python3-base python3-light ppp-mod-pppoe

# 3. 从电脑上传脚本到路由器（Windows 可用 MobaXterm/SCP）
#    把 linux/pppoe/linux/pppoe/v7.2-a/srun.py 拖到 /root/srun/ 目录

# 4. SSH 登录路由器
ssh root@192.168.6.1

# 5. 首次配置
cd /root/srun
chmod +x srun.py
python3 srun.py
# 选 3 → 填账号密码 → 选 1 启动连接
```

</details>

### 方式二：离线安装（路由器无网络）

<details>
<summary><code><strong>方式二：离线安装依赖</strong></code> - 路由器无网络</summary>
<br>

**步骤 1：查询路由器架构**

```bash
# 在路由器上执行
opkg print-architecture | grep -v all | grep -v noarch | head -3
# 或
uname -m
```

**步骤 2：找到对应架构的 ipk**

```
dependencies/python-offline-packages/
├── 21.02/          # OpenWrt 21.02
├── 22.03/          # OpenWrt 22.03
├── 23.05/          # OpenWrt 23.05（最常见）
└── 24.10/          # OpenWrt 24.10
    └── aarch64_cortex-a53/
    ├── aarch64_cortex-a72/
    ├── mipsel_24kc/        # 小米 R3G / 新3 等
    ├── arm_cortex-a7/      # 友善 NanoPi 等
    └── ...（共 30+ 架构）
```

**步骤 3：下载到电脑，上传到路由器**

```bash
# 在电脑上找到对应架构目录，如 mipsel_24kc
# 通过 SCP / MobaXterm 拖到路由器 /tmp 目录

# 在路由器上安装
cd /tmp
opkg install *.ipk
```

**步骤 4：上传 srun.py 并运行**

```bash
scp linux/pppoe/linux/pppoe/v7.2-a/srun.py root@192.168.6.1:/root/srun/
# 或使用 MobaXterm 直接拖拽上传
```

</details>

### 方式三：MobaXterm 图形化操作

<details>
<summary><code><strong>方式三：MobaXterm 图形化上传</strong></code> - 适合 Windows 用户</summary>
<br>

1. 打开 MobaXterm，点击 **Session** → **SSH**
2. 输入路由器 IP（如 `192.168.6.1`）、用户名 `root`、密码
3. 连接成功后，左侧文件浏览器找到 `/root/srun/`
4. 把 `linux/pppoe/linux/pppoe/v7.2-a/srun.py` 从本地直接拖拽到右侧窗口
5. 下方终端执行：

```bash
cd /root/srun
chmod +x srun.py
python3 srun.py
```

</details>

### 菜单说明

| 选项 | v7.2-a 完整版 | v7.2-b 精简版 |
|------|--------------|--------------|
| 1 | 启动连接 | 启动连接 |
| 2 | 断开连接（自动关闭自启） | 断开连接（自动关闭自启） |
| 3 | 账号配置 | 重新连接 |
| 4 | 查看日志 | 配置（账号+时间） |
| 5 | 监控面板 | — |
| 6 | 安装开机自启 | — |
| 7 | 卸载服务 | — |
| 8 | 设置时间 | — |
| 9 | 重新连接 | — |
| 0 | 退出 | 退出 |

---

## ❓ 常见问题

### Modem hangup / E2620
**现象**：pppd 收到 LCP ConfAck 后立即被 BRAS 断开。
**原因**：会话冲突 / 僵尸会话未释放 / 时间戳不匹配。
**解决**：
- 等 60 秒后自动重试（脚本内置）
- 去校园网认证页面手动踢下线
- 确认系统时间正确（路由器 `date` 检查）

### 连接成功但 ping baidu.com 不通
**原因**：DNS 未配置或防火墙规则缺失。
**解决**：菜单 2 断开 → 菜单 1 重连。脚本会自动配置 DNS 和 iptables。
**验证**：
```bash
ping -c 1 -W 2 10.1.1.1       # DNS 服务器可达？
nslookup baidu.com             # DNS 解析正常？
ping -c 1 -W 2 110.242.68.3   # 直连 IP 通？
```

### 连接后自行断开（断流）
**原因**：早期版本守护进程 (`da()`) 用 ping 做健康检查，网络抖动时误杀 pppd。
**状态**：✅ 已修复。v7.2 版本守护进程仅监控 ppp0 接口是否存在，不依赖 ping。
**验证**：
```bash
tail -f /var/log/pppoe_dial.log   # 看 pppd 日志
```

### Error 691 / 密码错误
**原因**：
1. 系统时间错误导致 S4 编码 hash 过期
2. 账号密码确实不对
3. 账号已在别处登录

**解决**：
```bash
date              # 检查时间
python3 srun.py   # 菜单 3 重新配置账号密码
# 菜单 8 手动设置时间（格式: YYYYMMDDHHMMSS）
```

### E2901: (Third party -1) Line fault —— 服务器侧拒绝
**特征**：之前能连上，突然连不上；日志里 CHAP 认证被明确拒绝：
```
rcvd [CHAP Failure id=0x1 "E2901: (Third party -1)Line fault, your authentication request is rejected."]
```
这不是脚本/密码问题——请求已到达 BRAS 并转发给深澜服务器，是**深澜侧主动拒绝**。
紧跟的下一次重试会立刻 Modem hangup，那是 BRAS 认证失败后的短暂拉黑，属于连锁症状。

**原因**（按概率）：
1. 账号在深澜后台状态异常（异常下线被标记 / 欠费 / 停机）
2. 学校开启了端口/线路绑定且绑定关系变更
3. 学校当日变更了认证策略（关闭该端口 PPPoE 认证 / 启用 MAC 绑定）
4. 深澜与 BRAS 对接临时故障

**排查**：
```bash
# 1. 确认请求格式没问题：看 CHAP Response 里的用户名（{S4}hex 部分每字节减 4 = 账号）
logread | grep "CHAP Response" | tail -2

# 2. 用另一台设备连校园网，打开网页认证页面登录
#    能登上 → 账号正常，是 PPPoE 线路/端口侧被改策略
#    登不上 → 账号被标记，联系网络中心
```
若为临时故障，等 1-2 小时重试；仍失败建议报修网络中心并说明 "E2901 Third party -1"。

### 路由器断电重启后连不上
**原因**：BRAS 侧残留僵尸会话。pppd 发起 LCP 协商后 BRAS 不响应（Modem hangup）。
**解决**：
```bash
# 等 30-60 秒让脚本自动重试（v7.2 每次重试间隔 10s）
# 或去认证页面手动踢下线
# 或等 15-30 分钟僵尸会话自动超时
```

### pppd 被 SIGKILL (-9)
**原因**：NSS 硬件加速模块 (`qca_nss_pppoe`) 与 pppd 冲突。常见于 IPQ60xx 系列（如 iStoreOS）。
**解决**：脚本每次拨号前自动执行 `rmmod qca_nss_pppoe`。如果无效：
```bash
# 手动卸载
rmmod qca_nss_pppoe
lsmod | grep nss_pppoe    # 确认已卸载
```

### init stop 超时 15 秒
**原因**：旧版本 `cmd_disconnect` 内部嵌套调用 init stop，与 procd 形成同步死锁。
**状态**：✅ 已修复（v7.2-a/b 均通过 `_kill_daemon_directly()` 绕过）。

### Windows 拨号提示错误 619 / 691
**原因**：账号已在线，或时间戳错误。
**解决**：

1. 确认路由器已断开（菜单 2）
2. 在 PC 上运行 `disconnect.bat` 清理残留
3. 检查 PC 系统时间是否正确

---

## 📂 文件结构

```
srun-pppoe/
├── README.md                             # 本文件
├── CHANGELOG.md                          # 版本历史
├── AI_srun_skill.md                      # AI 部署助手 Skill
├── v7.2-a/
│   └── srun.py                           # 完整版 srun 脚本
├── v7.2-b/
│   └── srun.py                           # 精简版 srun 脚本
├── windows/pppoe/                        # Windows PPPoE 客户端
│   ├── connect_keep.bat/py
│   ├── disconnect.bat
│   ├── config.yaml
│   └── modules/
│       ├── encode.py                     # S4 编码
│       ├── monitor.py                    # 保活监控
│       └── util.py                       # 工具函数
├── linux/dhcp/                           # Linux DHCP 客户端
│   ├── srun_dhcp.sh
│   └── config.yaml
├── tools/
│   └── MobaXterm_Personal_22.1(中文汉化版).exe   # SSH/SCP 图形化客户端
└── dependencies/                         # 离线依赖包
    └── python-offline-packages/
        ├── 21.02/                        # OpenWrt 21.02
        ├── 22.03/                        # OpenWrt 22.03
        ├── 23.05/                        # OpenWrt 23.05
        └── 24.10/                        # OpenWrt 24.10
            ├── aarch64_cortex-a53/
            ├── aarch64_cortex-a72/
            ├── mipsel_24kc/
            ├── arm_cortex-a7/
            └── ...（共 30+ 架构）
```

---

## 🤖 AI 辅助部署

将本仓库目录交给支持 AI 编程助手的工具（如 Claude Code、Cursor 等），并加载仓库中的 [`AI_srun_skill.md`](AI_srun_skill.md) —— AI 将根据完整的历史战斗记录协助你：

- 诊断连接问题
- 分析路由器日志
- 配置防火墙 / DNS
- 修复常见故障

---

## 📄 许可证

MIT
