# HomeProxy 重启后不自启：根因与修复

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

（本节由实际执行结果填写，见提交时的 `docs/validation.md` 与 QEMU 日志。）

- 静态测试：45 项（含 fail-open 保留、reconcile、hotplug、i18n 完整性）。
- QEMU 真实镜像：
  - 插件 UI 式启用（只有 UCI 标志、无 rc.d 链接）→ reconcile 建链接并启动
  - 冷重启 → HomeProxy 自动运行
  - fail-open → 停止运行但保留标志/链接；再次重启仍能启动
  - 无节点 → 不自动启动、不建链接
  - 重复 reconcile → 单个 sing-box、nftables 规则数不变
  - zh-cn lmo 文件实际存在

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
