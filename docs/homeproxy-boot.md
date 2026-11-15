# HomeProxy 重启后不自启：根因与修复

> 2026-11-15 补充：用户真机日志确认还有生成器与 sing-box 1.14.4 的配置
> 兼容问题，旧测试没有覆盖。请先看
> [配置兼容修复与不刷机安装](homeproxy-singbox14.md)。下文记录的是此前的
> 启用状态/启动时序问题；此前状态机通过不能证明真实代理能够启动。

日期：2026-11-15
适用：ImmortalWrt 25.12.2、模块化构建（x86 / ARM / VM）、HomeProxy + proxy-guard

## 1. 现象

- 首次配置 HomeProxy 后代理正常。
- 重启宿主/虚拟机后 HomeProxy 显示 inactive。
- `/etc/init.d/homeproxy enabled` 返回 1，`/etc/rc.d/` 没有 HomeProxy 链接。
- UCI 节点配置仍在；TUN / nftables / sing-box 都正常。
- 手动 `/etc/init.d/homeproxy restart` 可以恢复。
- 首次手动重启出现 `can't create /var/run/homeproxy/homeproxy.log: nonexistent directory`。

## 2. 根因（代码 + 实机镜像证据）

### 2.1 首次启动的互斥逻辑会删除 HomeProxy 的 rc.d 链接

`proxy-switch` 的职责是同一时刻只保留一个代理前端。首次启动
`/etc/uci-defaults/90-proxy-guard` 按 x86 预设启用 PassWall，于是对 HomeProxy 执行：

```sh
uci set homeproxy.config.enabled=0
/etc/init.d/homeproxy stop
/etc/init.d/homeproxy disable     # 删除 /etc/rc.d/S99homeproxy
```

镜像自身确实带 `S99homeproxy`（解包 squashfs 可见），但首次启动后就被移除。

### 2.2 看门狗 fail-open 会再次清除“用户选择”

`proxy-guard-watchdog` 在连续探测失败后执行：

```sh
proxy-switch off --keep-record
```

旧实现对所有前端执行 `disable` 并把 UCI `enabled` 置 0。结果是：

- 服务被停止（符合 fail-open 预期）；
- **`homeproxy.config.enabled` 被清成 0**；
- **`/etc/rc.d/S99homeproxy` 被删除**；
- `proxy-guard.main.active=homeproxy` 仍然保留。

QEMU 实测（旧镜像）：

```
[before off] uci get homeproxy.config.enabled -> 1
             /etc/init.d/homeproxy enabled    -> rc=0
             rc.d: K10homeproxy S99homeproxy
[after  off] uci get homeproxy.config.enabled -> 0
             /etc/init.d/homeproxy enabled    -> rc=1
             rc.d: no-link
```

下次启动时没有 S99 链接，HomeProxy 不会运行；UCI 里的节点配置却仍然存在，
与用户观察完全一致。

### 2.3 从 HomeProxy 自己的 LuCI 页面启用不会重建链接

HomeProxy 的 `enabled` 只是 UCI 标志；它的 init 脚本 `start_service()` 甚至不读这个
标志。LuCI 页面启用时只会写标志并重启服务，不会调用
`/etc/init.d/homeproxy enable`。因此一旦链接被 2.1/2.2 删除，重启永远不会自启。

### 2.4 `/var/run/homeproxy` 是 tmpfs

HomeProxy 的 `stop_service()` / `reload_service()` 在 `start_service()` 创建目录之前
就调用 `log()` 写 `/var/run/homeproxy/homeproxy.log`，于是出现
`nonexistent directory`。这不阻止启动，但会污染日志、掩盖真实错误。

### 2.5 源码证据与两个报错的独立解释

实际镜像中的文件（luci-app-homeproxy `26.298.29275~c84adb9`）：

| 文件 | 行 | 内容 |
|---|---|---|
| `/etc/init.d/homeproxy` | 15–16 | `RUN_DIR="/var/run/homeproxy"`、`LOG_PATH="$RUN_DIR/homeproxy.log"` |
| 同上 | 26–28 | `log() { echo ... >> "$LOG_PATH"; }`（**不做目录检查**） |
| 同上 | 30 / 51 | `start_service()`；直到第 51 行才 `mkdir -p "$RUN_DIR"` |
| 同上 | 253 | 启动成功后才 `log "sing-box ... started."` |
| 同上 | 256 / 314 | `stop_service()`；末尾 `log "Service stopped."`（**在 start 的 mkdir 之前**） |
| 同上 | 317–326 | `service_stopped()` 最后执行 `ip tuntap del`，设备不存在时返回非 0 |
| 同上 | 335–337 | `service_triggers()` 注册 `interface.*.up` wan → `homeproxy reload` |
| `rc.common` | 23–28 | `restart() { trap '' TERM; stop; trap - TERM; start; }`，第 27 行是 `start` |
| `rc.common` | 156–163 | `stop() { procd_lock; stop_service; procd_kill; service_stopped; }` |
| `/lib/functions/procd.sh` | `_procd_kill()` | `ubus call service delete` |

由此可以精确回答：

1. **为什么目录不存在时 restart 报错？**`restart` 先 `stop_service()`；它在第 314
   行写日志，而 `mkdir -p "$RUN_DIR"` 只在 `start_service()` 第 51 行。目录不存在
   时 `log()` 的重定向失败。报错前缀指向 `rc.common` 第 27 行（restart 的 start
   调用点），实际写入者是 HomeProxy 的 `log()`。
2. **错误发生在 stop 阶段吗？**是。`stop_service()` 的日志写入发生在
   `start_service()` 创建目录之前。
3. **`Command failed: Not found` 从哪来？**`rc.common stop()` → `procd_kill()` →
   `ubus call service delete`；服务本来就没有运行实例时 ubus 返回 `Not found`。
   它不是配置错误，也不是 sing-box 崩溃。
4. **为什么 `start` 能恢复？**`start_service()` 第 51 行先 `mkdir -p "$RUN_DIR"`，
   再生成配置并启动 sing-box；服务未运行时没有 stop 阶段的日志问题。
5. **上游实现是否一致？**是。25.12.2 feed 的 `luci-app-homeproxy` 就是上述脚本；
   2026-11-15 核对 x86_64 / aarch64_cortex-a53 / aarch64_generic 三个架构。
6. **是否版本兼容问题？**这些 stop/目录提示本身不能说明配置是否兼容。
   后续用户真实日志确认，旧生成器与 sing-box 1.14.4 确实存在配置兼容问题，
   见文首补充；不能根据 stop 提示排除版本问题。

## 3. 修复

遵循现有模块系统，不新增配置管理框架。

### 3.1 `proxy-switch`

- `--keep-enabled`：fail-open 只停止运行中的服务，保留 UCI `enabled` 和 rc.d 链接。
- `--ensure`：幂等；服务已在运行时**不重启**，只做互斥与启用状态收敛。
- `--no-start`：只收敛启用状态与互斥，启动交给前端自己的 S99 脚本（避免同一次启动
  里被启动两次）。
- 启动前创建 `/var/run/homeproxy`（755，`sing-box:sing-box`），并同样处理
  passwall/openclash/nikki 的运行目录。

### 3.2 `proxy-guard-watchdog`

fail-open 改为：

```sh
proxy-switch off --keep-record --keep-enabled
```

停止运行时但不再抹掉用户选择。

### 3.3 新增 `proxy-guard-reconcile`

在开机（`proxy-guard` init，START=95）和 WAN `ifup`（hotplug）时执行：

- 目标选择：`active` 记录（包括持久化的 fail-open）优先；否则取唯一启用项；
  多项启用时优先“真正配置了节点/服务器”的前端。
- HomeProxy 没有 `main_node`、没有 server、也不是 custom 默认出口时，不启动。
- 链接已存在：只做互斥收敛（`--ensure --no-start`），把启动留给 S99。
- 链接缺失（典型：插件 UI 启用）：`proxy-switch --ensure` 创建链接并启动。
- WAN 事件路径幂等：运行中不重启，避免重复 sing-box / 重复 nftables。

### 3.4 运行目录

`proxy-guard` init 在 watchdog 之前创建 `/var/run/homeproxy` 并 chown，消除
HomeProxy reload/stop 的日志目录错误。

### 3.4 上游 init 的最小补丁（hash 保护）

`/usr/sbin/homeproxy-runtime-patch` 在每次开机、S99homeproxy 之前运行：

- 校验 `/etc/init.d/homeproxy` 的 sha256 是否为固定上游版本
  `47e6244...`；不匹配时**只记录错误、不修改文件**（避免把补丁套到未知版本）。
- 仅在匹配时插入三处最小修改：
  1. `log()` 开头 `mkdir -p "$RUN_DIR"` + `chown sing-box:sing-box`；
  2. `service_stopped()` 末尾 `return 0`（停止未运行服务不再返回非 0）；
  3. `start_service()` 开头等待 WAN 默认路由 + DNS（ubus `dns-server` 或
     `/tmp/resolv.conf.d/resolv.conf.auto`），否则直接返回，由 WAN
     `ifup/ifupdate` 事件再启动；避免在 DNS 未就绪时生成
     `default DNS server not found` 的坏配置。
- 补丁先在临时文件里生成并校验（标记 + 语法），通过后才 `cat >` 覆盖原文件，
  保留原权限；失败时不动原文件。
- 上游升级导致 hash 变化时，构建/首次启动日志会明确报
  `homeproxy init hash changed ... patch NOT applied`，需要人工复核。

### 3.5 中文语言包

审计所有模块的 LuCI 应用，补齐上游存在的 `luci-i18n-<app>-zh-cn`：

- base：firewall、package-manager、ttyd、argon-config、attendedsysupgrade
- smartdns、dockerman、frpc、frps
- nlbwmon、vnstat2、statistics、netdata
- samba4、diskman、openlist、hd-idle
- upnp、natmap、wechatpush、nps
- sqm、appfilter
- ddns-go、acme、cloudflared
- tailscale-community、zerotier

共 31 个语言包，已在 25.12.2 的 `x86_64`、`aarch64_cortex-a53`、
`aarch64_generic` 三个 feed 中逐一确认存在。
`adguardhome` 与 `openclash` 上游没有独立 zh-cn 包（OpenClash 自带中文），
已在测试中列为白名单。

## 4. 变更文件

| 文件 | 作用 |
|---|---|
| `modules/proxy-guard/files/usr/sbin/proxy-switch` | `--keep-enabled/--ensure/--no-start`、运行目录 |
| `modules/proxy-guard/files/usr/sbin/proxy-guard-watchdog` | fail-open 保留启用状态 |
| `modules/proxy-guard/files/usr/sbin/proxy-guard-reconcile`（新增） | 开机/WAN 事件收敛 |
| `modules/proxy-guard/files/usr/sbin/homeproxy-runtime-patch`（新增） | 上游 init 的 hash 保护补丁 |
| `modules/proxy-guard/files/etc/hotplug.d/iface/99-proxy-guard`（新增） | WAN up 事件驱动 |
| `modules/proxy-guard/files/etc/init.d/proxy-guard` | 启动前建目录 + reconcile |
| `modules/proxy-guard/files/etc/uci-defaults/90-proxy-guard` | 确保 guard 自启 |
| `modules/*/packages.txt` | 31 个 zh-cn 语言包 |
| `tests/test_homeproxy_boot.py`、`tests/test_i18n.py`、`tests/qemu_homeproxy.py` | 回归与真实镜像测试 |

## 5. 行为保证

- 用户启用 HomeProxy 后，链接/标志在 fail-open 和重启后仍保留；重启会重新启动。
- 未配置节点/服务器时不自动启动 HomeProxy。
- 运行目录在启动前存在且属主正确。
- 启动由 procd 管理（沿用 HomeProxy 自身的 `procd_*`，不替换上游 init）。
- WAN 就绪使用 hotplug `ifup` 事件 + HomeProxy 自带的 interface trigger，无固定 sleep。
- 幂等 reconcile：重复调用不会重复启动进程或追加 nftables。
- 不修改用户节点、订阅、网关、DNS；不改变默认代理选择策略。
- 仍然只允许一个前端运行；PassWall/OpenClash/Nikki 的互斥逻辑不变。

## 6. 测试

实际执行结果（镜像 run `43689978067`，commit `747f2b5`）：

- 静态测试：46 项 PASS（解析器、构建契约、BusyBox、fail-open 保留、reconcile、
  hotplug、i18n 完整性）。
- QEMU 真实镜像状态机测试：**23/23 PASS**
  - 插件 UI 式启用（只有 UCI 标志、无 rc.d 链接）→ reconcile 建链接、active 记录
  - 冷重启后 rc.d 链接、active 记录、`enabled` 状态保持
  - fail-open 后停止运行但保留 `enabled=1`、S99 链接、active；再次冷启动链接仍在
  - 无节点：不自动启动、不建链接
  - 删除 `/var/run/homeproxy` 后 stop/restart 返回 0，无 `nonexistent directory`
  - 连续 restart 不增加 nftables 规则
  - zh-cn 语言包与 lmo 文件实际存在
- **未验证（BLOCKED）**：带真实节点/订阅的 HomeProxy 数据面在冷启动后是否立即
  恢复代理。QEMU 中无法凭空构造一个被 `generate_client.uc` 接受的真实节点；
  自造的 custom/direct 与假节点都被生成器拒绝，因此**不把状态机通过当成代理
  数据面通过**。需在用户自己的节点配置上执行 §7 的验证步骤。

## 7. 升级/验证步骤

旧固件 → 新固件（sysupgrade 保留 `/etc/config`）：

1. 刷入新镜像，保留配置。
2. 首次启动后检查：
   ```sh
   /etc/init.d/homeproxy enabled; echo $?     # 0
   ls /etc/rc.d | grep homeproxy              # S99homeproxy
   pgrep -f 'sing-box run'                    # 有进程
   uci get homeproxy.config.enabled           # 1
   ```
3. 冷重启后再检查一次。

如果旧固件上已经被 fail-open 清成 `enabled=0`，升级后 guard 会依据
`proxy-guard.main.active` + `state=failopen` 重新启用；若用户是手动关闭的
（`active=off`），不会擅自启动。

## 8. 当前运行固件的临时修复（不刷机）

在旧固件上执行：

```sh
mkdir -p /var/run/homeproxy
chown sing-box:sing-box /var/run/homeproxy
/etc/init.d/homeproxy enable        # 重建 S99homeproxy
/etc/init.d/homeproxy start
```

这能让本次运行恢复；但**重启后仍可能被旧的 fail-open 再次清掉 enabled 状态**，
所以只是临时方案。永久修复需要刷入包含本提交的新固件。

## 9. 回滚办法

1. 刷机前备份：
   ```sh
   sysupgrade -b /tmp/backup-$(date +%s).tar.gz
   ```
   并保存旧固件镜像。
2. 新固件异常时，用旧镜像 sysupgrade；`/etc/config` 默认保留。
3. 如需临时停用新的 reconcile/guard（会失去 fail-open 修复）：
   ```sh
   /etc/init.d/proxy-guard disable
   /etc/init.d/homeproxy enable
   /etc/init.d/homeproxy restart
   ```
4. 恢复备份：
   ```sh
   sysupgrade -r /tmp/backup-*.tar.gz
   ```

## 10. 剩余风险 / 未完成

- **数据面未验证**：无真实节点/订阅，QEMU 只能验证状态机与配置持久化，不能证明
  冷启动后 sing-box 一定成功代理；需在真实节点上按 §7 验证。
- **上游 init 补丁依赖版本 hash**：`luci-app-homeproxy` 升级后 hash 变化，补丁会
  明确报错并跳过；早期目录创建和 reconcile 仍有效，但 `stop` 幂等/DNS 门控需要
  人工复核补丁。
- **ARM 未做真机启动验证**；只验证了 x86-64-vm 构建与 QEMU。
- 多代理插件共存只验证了“不会同时启动”的状态收敛，未做真实数据面并发测试。
- IPv6、DNS 污染、长时间 soak 未测试。
