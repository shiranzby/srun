# srun-pppoe AI 辅助部署 Skill

> 基于 srun-pppoe 项目的完整历史战斗记录生成。
> 用户在遇到问题时，可让 AI 加载此 Skill 进行诊断修复。

---

## 🎯 Skill 目的

协助用户部署、诊断和修复 srun-pppoe 校园网 PPPoE 拨号工具，覆盖 Windows / Linux / OpenWrt 全平台。

---

## 📋 诊断流程

### 第一步：收集信息

```bash
# OpenWrt 路由器
date                                      # 系统时间
tail -30 /var/log/pppoe_dial.log          # pppd 日志
ps -w | grep -E 'srun|pppd'              # 进程状态
ip -4 addr show ppp0                      # ppp0 接口
ip route show default                     # 默认路由
iptables -t nat -C POSTROUTING -o ppp0 -j MASQUERADE  # NAT 规则
uci show firewall.@zone[1].network        # 防火墙 WAN 区域

# Windows
python --version
rasdial                            # 拨号状态
ipconfig                           # IP 地址
```

### 第二步：问题分类

| 症状 | 可能原因 | 优先级 |
|------|---------|--------|
| pppd 退出 -9 | NSS 模块冲突 / 进程竞争 | P0 |
| Modem hangup | BRAS 僵尸会话 / E2901 被踢后 BRAS 短暂拉黑 | P0 |
| E2901 (Third party -1) Line fault | 深澜服务器侧拒绝：账号状态异常 / 端口绑定校验失败 / 策略变更 | P0 |
| Error 691 | 密码错误 / 时间戳过期 | P0 |
| 连接成功但 ping 不通 | DNS / 防火墙未配置 | P1 |
| 自行断开 | daemon 误杀 / pppd LCP 超时 | P1 |
| AttributeError / UnboundLocalError | Python 语法压缩 bug | P0 |

---

## 🔧 已知问题与修复

### P0: E2901 (Third party -1) Line fault — 深澜服务器侧拒绝（2026-09-13 实案）

**现象**：之前能正常连接，突然连不上了。日志特征：

```
rcvd [CHAP Challenge ... name = "BRAS-CR16010-F"]
sent [CHAP Response ... name = "{S4}139a8\r\n363834383a363638@stu.nchu.edu.cn"]
rcvd [CHAP Failure id=0x1 "E2901: (Third party -1)Line fault, your authentication request is rejected."]
CHAP authentication failed
→ 紧接着的下一次重试：LCP ConfReq 一发出就 Modem hangup（BRAS 认证失败后短暂拉黑）
```

**根因分析流程**（已在实案中验证）：

1. **确认不是密码问题**：E2901 ≠ 691。密码错误是另一种报错；E2901 表示请求已到达 BRAS 并转发给第三方（深澜服务器），是**深澜侧主动拒绝**。
2. **验证 S4 编码无损**：把 CHAP Response 里的用户名解码，
   `{S4}<hash>\r\n<hex>` 中 hex 每字节减 4 还原账号。实案解码出 `24046224@stu.nchu.edu.cn` 完整无损。
3. **验证时间戳**：`hash(16进制) - sum(ord(账号各字符))` = 当天秒数。
   实案 `0x139a8(80296) - 402 = 79894s = 22:11:34`，与拨号时刻吻合 → 时间无误。
4. **结论**：请求格式完全正确，问题在服务器侧。

**E2901 常见服务器侧原因**（按概率排序）：
1. 账号在深澜后台状态异常（异常下线被标记、欠费、停机）
2. 深澜开启了端口/线路绑定校验，且绑定关系变更（如 BRAS 端口调整）
3. 学校当日变更了认证策略（如关闭该 VLAN 的 PPPoE 认证、启用 MAC 绑定）
4. 深澜与 BRAS 对接临时故障（第三方接口返回 -1）

**排查动作**：
- 用**另一台设备连校园网**打开 `http://<srun服务器>/` 网页认证 → 能登上 = 账号正常、是 PPPoE 线路/端口被改策略；登不上 = 账号被标记，找网络中心
- 同端口试 DHCP：`udhcpc -i <iface> -n -q -t 4 -T 3` → 无 lease 说明该口当前仅 PPPoE VLAN（或被切走）
- 全部失败且网页认证也异常 → 等待 1-2 小时重试（临时故障），仍不行报修网络中心

**脚本侧注意**：E2901 后紧跟的重试会立刻 Modem hangup（BRAS 拉黑），重试间隔应 ≥30s，避免频繁触发。

### P0: 嵌套 init stop 死锁（v7.0 原版）

**问题**：`cmd_disconnect` 内部调用 `subprocess.run([INIT_SCRIPT, "stop"])`，而 procd 的 `stop_service` 又同步调用 `srun.py stop` → `cmd_disconnect`，形成调用链死锁，15s 超时崩溃。

**修复**：新增 `_kill_daemon_directly()` 函数直接杀 daemon 进程 + 清理 pppd，不再嵌套调用 init stop。

**检查**：
```python
grep -c 'INIT_SCRIPT, "stop"' srun.py   # 应为 0
grep -c '_kill_daemon_directly' srun.py  # 应 ≥ 1
```

### P0: daemon 杀死已有连接

**问题**：`da()` 函数的 while 循环中无条件调用 `pe()`，而 `pe()` 内部第一件事就是 `killall -9 pppd`。daemon 启动后立即杀掉当前正在工作的 pppd。

**修复**：daemon 循环改为先检查 ppp0 接口是否存在，只有 ppp0 消失时才调用 `pe()` 重连。连接正常时每 30s 检查一次。

**正确逻辑**：
```python
while True:
  if pi()[0]:time.sleep(30);continue  # ppp0 正常，睡觉
  if pe():lg("+已连接")                # ppp0 没了，重连
  else:time.sleep(10)
```

### P0: Python 分号语法陷阱

**问题**：压缩代码时 `if not e.isdigit():continue;p=int(e)` 导致 `UnboundLocalError`。Python 把 `;` 后的 `p=int(e)` 当作 `if` 的 suite，`continue` 跳过它，`p` 从未赋值。

**修复**：将 `p=int(e)` 移到独立行：
```python
if not e.isdigit():continue
p=int(e)
```

### P1: 连接成功但 DNS/防火墙 未配置

**问题**：`pe()` 成功时内部已调用 `ps()`，但 `cn()` 中又调了一次 `ps()`。冗余调用不会导致问题，但早期版本中曾出现过 `cn()` 中缺少 `ps()` 调用导致 DNS/iptables 未配置。

**修复**：确保 `pe()` 内部成功时调用 `ps()`，且 `cn()` 中不重复调用。

### P1: 守护进程 ping 检测过于激进

**问题**：daemon 的健康检测用单次 `ping -c 1 -W 3 baidu.com`，网络抖动时误判为断线，杀掉 pppd 触发重连。

**修复**：daemon 改为仅监控 ppp0 接口是否存在，不依赖 ping。pppd 自身的 LCP Echo 已经足够判断连通性。

### P1: BRAS 僵尸会话 / 断电重连

**问题**：路由器断电时 pppd 未发 PADT 正常下线，BRAS 侧残留僵尸会话。重启后新 LCP 协商被直接丢弃（Modem hangup）。

**修复**：
1. 脚本检测到 Modem hangup 后按 E2620 逻辑处理，等待 10s 后重试
2. 如果多次重试失败，提示用户检查时间或去认证页面踢下线
3. BRAS 僵尸会话通常在 15-30 分钟后自动超时释放

### P1: srun-time 时间回拨

**问题**：init 脚本的 `start_service()` 无条件读取 `/etc/srun-time` 恢复时间，把 NTP 同步后的正确时间拨回过去，导致 S4 编码 hash 失效。

**修复**：彻底删除 `/etc/srun-time` 全套逻辑。init 脚本不再恢复时间。连接成功后由 NTP 自动同步。

### P2: 重新连接时序

**问题**：`cmd_reconnect` 顺序为"杀 daemon → 启动服务"，但 daemon 的 SIGTERM 回调异步执行 `stop()`，新 daemon 启动时旧 daemon 的清理未完成 → 新旧 pppd 竞争。

**修复**：顺序改为"先 `stop()` 同步断开 → 再 `_kill_daemon_directly()` → 最后 `init start`"。

---

## 🛠️ 修复模板

### 添加 Modem hangup 检测

```python
# 在 monitor() 函数中，与 E2620 检查并列：
if 'E2620' in line or 'Modem hangup' in line:
    log("会话被BRAS终止(Modem hangup)", L_WARN); return False, True
```

### 修复 daemon 不自杀 pppd

```python
# 原版
while True:
  if pe():lg("+已连接")
  else:time.sleep(10)

# 修复版
while True:
  if pi()[0]:time.sleep(30);continue
  if pe():lg("+已连接")
  else:time.sleep(10)
```

### 添加防火墙 WAN 区域配置

```python
# 在 ps() (post_setup) 中添加：
r=rn(["uci","-q","show","firewall.@zone[1]"])
if not any('.network' in l and I in l for l in r.stdout.split('\n')):
  rq(["uci","add_list",f"firewall.@zone[1].network={I}"],3)

# 在 sp() (stop) 中添加：
def frs():
  try:
    r=rn(["uci","-q","show","firewall.@zone[1]"])
    for l in r.stdout.split('\n'):
      if '.network' in l and I in l:
        rq(["uci","del_list",f"firewall.@zone[1].network={I}"],3)
  except:pass
```

---

## 🔍 验证清单

修复后验证以下各项：

```bash
# 1. 清理所有残留
for pid in $(grep -l 'srun.py' /proc/*/cmdline 2>/dev/null | cut -d/ -f3); 
do [ "$pid" != "$$" ] 2>/dev/null && kill -9 $pid 2>/dev/null; done
rm -f /var/run/srun-watchdog.pid

# 2. 启动连接
python3 srun.py
# 选 1 → 预期: 成功显示 "连接成功"

# 3. 测试网络
ping -c 3 baidu.com          # DNS + 连通性
nslookup baidu.com            # DNS 解析

# 4. 断开
# 选 2 → 预期: "已关闭开机自启并断开"
# 检查: ps -w | grep srun → 无进程
# 检查: ip -4 addr show ppp0 → 无

# 5. 重连
# 选 1 → 预期: 正常连接

# 6. 防火墙检查
uci show firewall.@zone[1].network | grep srun  # 应在 WAN zone 中
iptables -t nat -C POSTROUTING -o ppp0 -j MASQUERADE  # 应存在
```
