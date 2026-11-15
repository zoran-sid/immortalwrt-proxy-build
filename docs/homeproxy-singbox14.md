# HomeProxy 与 sing-box 1.14 的配置兼容修复

2026-11-15，x86 虚拟机用户提供的实际结果：

- sing-box 1.14.4；HomeProxy 生成器 29298 字节。
- `start_rc=0`，但没有 sing-box 进程；日志为 `legacy inbound fields ... removed in sing-box 1.13.0`。
- `homeproxy.config.enabled=0`，`homeproxy enabled` 返回 1。
- 因此配置兼容失败和自启动未启用同时存在，仅重建 rc.d 链接无法恢复。

## 配置兼容

核对官方 luci c84adb9 的
`applications/luci-app-homeproxy/root/etc/homeproxy/scripts/generate_client.uc`：
文件长度和用户的行号完全吻合，sha256 为
`7545f12f2af648ab09c64bea943257ed5c768f8689d910d3beb9aed15b069812`。
补丁仍在设备上校验完整 hash；不匹配会拒绝修改。

`homeproxy-config-patch` 将小段 ucode 插入生成器的 writefile 前，在
removeBlankAttrs 之前完成迁移。不是修改重启后会丢失的临时 JSON：

- 入站 sniff/sniff_timeout/domain_strategy 迁移到对应入站的 sniff/resolve 规则。
- 去除已删除的 sniff_override_destination，使用当前 sniff 动作语义。
- block 出站及引用迁移为 reject，默认 block 出口也保留拒绝行为。
- 去除 1.14 已删除的 independent_cache、store_rdrc、rdrc_timeout。
- 清除 default_domain_resolver 中多余的 action。
- 不改节点、订阅、端口和密码。复杂自定义配置仍须由真实核心校验；补丁不承诺
  迁移所有历史配置（例如旧 direct 出站的地址覆盖、selector 中引用 block）。

写入前用 `ucode -c` 只编译不执行，并保存原生成器为 `.proxy-guard-original`。
首次启动及 guard 启动都会应用补丁；已打补丁则跳过。

官方依据：
[HomeProxy 生成器](https://github.com/immortalwrt/luci/blob/c84adb9/applications/luci-app-homeproxy/root/etc/homeproxy/scripts/generate_client.uc)、
[sing-box 迁移说明](https://sing-box.sagernet.org/migration/)。

## 启动监管

- --ensure 不再删除运行中目标的 nft 表。
- 失败重试前先让目标服务清理残留 DNS/路由状态。
- PassWall 的进程判断限定其运行目录，避免把 HomeProxy 的 sing-box 当成 PassWall。
- 正常监控每个 interval（默认 60 秒）收敛一次，覆盖错过 WAN 事件或锁竞争的情况。
- 网络事件不再要求接口名含小写 wan，兼容用户的 `WAN`、`uplink` 以及旁路由接口。
- fail-open 期间网络事件不绕过退避；重启后重新尝试保存的选择。
- 启用值和 active 未变化时不重复 commit，避免周期性写 flash。
- 已完成向导的升级也会确保 proxy-guard 自启动。

## 当前设备不刷机修复

构建机运行 `python scripts/package_homeproxy_fix.py` 生成
`dist/homeproxy-fix.tar.gz`，其中没有节点/凭据或二进制。
上传到路由器的 `/tmp/homeproxy-fix.tar.gz`，执行：

```sh
tar -xzf /tmp/homeproxy-fix.tar.gz -C /tmp
sh /tmp/homeproxy-fix/install.sh
```

安装器在 `/root/homeproxy-fix-backup-<时间>-<pid>` 保存原文件及相关 UCI，权限 700。
先在设备上生成真实配置并执行 `sing-box check`，失败则还原生成器，保持服务选择。
通过后安装 guard 修复、选择 HomeProxy（停止其他代理）、恢复自启动并检查核心进程。
备份含设备原配置，不要上传或公开。

确认上网正常后执行 `sync; reboot`，重启后检查：

```sh
uci -q get homeproxy.config.enabled  # 1
/etc/init.d/homeproxy enabled; echo $?  # 0
pgrep -af 'sing-box.*sing-box-c.json'
tail -n 30 /var/run/homeproxy/homeproxy.log
```

若需回退，先停止 guard 和 HomeProxy，再用备份中的原文件覆盖对应路径。
恢复旧固件前自行保存该备份。回退会同时恢复旧版兼容/监管问题。

## 验证范围

用户首次安装在 `proxy-switch` 执行时报 `not found`：打包时直接读取了 Windows
工作区的 CRLF，导致脚本的解释器成为 `/bin/sh\r`。旧 shell 测试先用 read_text
读取并重写，隐式消除了 CRLF，因此未暴露这个交付问题。现在打包时显式规范化
LF 并更新 tar 成员长度，新增 .gitattributes 及实际压缩包字节/执行回归测试。
已解压旧包可在设备上先运行以下命令再续装，保留原备份：

```sh
find /tmp/homeproxy-fix -type f -exec sed -i 's/\r$//' {} \;
sh /tmp/homeproxy-fix/install.sh
```

本地 55 项测试全部通过，包括六项实际 shell 执行测试及三项配置迁移测试。
真实官方 Windows sing-box 1.14.4 对相同 fixture：迁移前报 legacy inbound，
迁移后 `check` 返回 0。shell 语法与 diff 检查通过。
ucode 编译和用户真实配置校验由设备安装器执行。

尚未完成：用户虚拟机安装反馈、Linux 真实节点流量验证、冷重启和长时间运行。
不能将 fixture 的 schema 检查通过称为用户设备已经恢复或固件真机稳定。

## 用户设备安装反馈

用户在 x86 虚拟机上将旧包转换为 LF 后续装，安装器输出：
`HomeProxy core is running; autostart restored.`
因此已确认：设备上 ucode 编译通过、真实节点配置的 sing-box check 通过、核心
进程启动成功。此前“用户虚拟机安装反馈未完成”的状态更新为安装已通过。
仍待用户确认实际代理上网及重启后的自动恢复；长时间运行尚未验证。

用户重启后首次检查：enabled=1、自启动返回 0，但未见核心；随后日志显示
watchdog 运行且在 `20:36:54` 自动 reconcile，`20:37:07` sing-box 启动。
该日志证明重启后自动重试已执行，仍需进程存活和实际上网结果。
设备 resolver 文件标注接口名 `WAN`，旧 hotplug 的 `*wan*` 筛选不会匹配；
后续修复包已取消接口名称假设，以便这种接口也能触发启动重试。

随后用户提供重启后的进程检查，PID 8785 正在执行
`/usr/bin/sing-box run --config /var/run/homeproxy/sing-box-c.json`。
结合 enabled=1、自启动 rc=0 和自动 reconcile 日志，已验证本次重启后核心
自动恢复，启动耗时约一分钟。用户尚未确认实际代理上网，未做长期稳定性验证。

## HomeProxy / PassWall 切换

停止一个服务不等于切换保存的代理选择：服务的 stop 不一定清 UCI enabled，
此前互斥关闭的另一个代理也不会因此自动启用。使用统一切换入口：

```sh
proxy-switch passwall   # 切到 PassWall，停用 HomeProxy 并更新 active
proxy-switch homeproxy # 切回 HomeProxy，停用 PassWall 并更新 active
proxy-switch off       # 关闭全部代理并清除 active 选择
```

用户在 HomeProxy 停止后报告 PassWall 无法启动，执行 `proxy-switch passwall`
后返回 0，active=passwall、homeproxy.enabled=0、passwall.enabled=1；
实际进程为 `/tmp/etc/passwall/bin/xray run -c /tmp/etc/passwall/acl/default/global.json`，
PassWall 日志确认防火墙规则加载和运行完成。
已确认本次实际切换启动成功；PassWall 此次切换后的冷重启和实际代理上网未验证。
隔离回归同时检查切换后 flags、链接、active 一致，后续 reconcile 保留 PassWall
规则和进程且不会重新拉起 HomeProxy。

## PassWall 重启后的 60 秒延时

用户再次重启后，enabled rc=0、active=passwall、state=normal、enabled=1；
实际进程 PID 9007 为 PassWall Xray，日志确认 nftables 和运行完成。
因此这次 PassWall 重启后自动启动已验证，实际代理上网仍待确认。

日志同时明确显示 `执行启动延时 60 秒后再启动!`：首次检查时尚处于启动延时。
guard 的第一次收敛也在约 60 秒，和 PassWall 自身启动发生锁竞争，打印
`脚本已经在运行，不重复运行，退出.`；该次最终启动成功，不应把这行当作失败。

设备端可显式缩短 PassWall 的启动延时，保留 guard 默认 60 秒重试：

```sh
uci set 'passwall.@global_delay[0].start_delay=5'
uci commit passwall
uci -q get 'passwall.@global_delay[0].start_delay'
sync
```

这里 5 秒仅指开始启动前的等待，不保证从开机起 5 秒内完成启动；网络就绪和
规则加载仍可能耗时。尚待用户确认调整后重启结果。需要恢复原延时时将 5 改回 60。

用户随后确认 PassWall 调整后已自启动，并要求 HomeProxy 也缩短等待。
HomeProxy 上游没有 start_delay 参数，新增开机专用 `homeproxy-boot-retry`：
由 procd 管理，每 5 秒调用现有 reconcile，核心存活就退出，最多持续两分钟；
被用户关闭或处于 fail-open 时退出，不改变日常 60 秒探测及故障退避。
该实现通过 14 项 shell 行为测试及 3 项包测试，专用更新包为
`dist/homeproxy-fast-start.tar.gz`，尚未在用户设备部署。

用户又报告通过两个插件页面切换后 HomeProxy 无法启动，当前先排查页面状态与
监管选择及实际日志，暂缓部署快速启动更新。此前命令切换成功不能等价于两个
插件页面互相切换已经验证。

该次切换后进一步确认：`sing-box run` PID 20463 正在运行，配置日志的三条
WARN 不是此次启动的致命错误。首次 pgrep 发生在 procd 拉起核心之前；不能
把当时的空输出单独当作启动失败。运行判断收紧为 `sing-box run`，不再把
`sing-box check` 当作核心存活。

快速补启动还会识别正在执行的 HomeProxy 原生 boot/start/restart/reload，
等待其完成，避免每 5 秒检查反而打断配置生成。专用包安装方式：

```sh
tar -xzf /tmp/homeproxy-fast-start.tar.gz -C /tmp
sh /tmp/homeproxy-fast-start/install.sh
```

仅适用于已安装基础修复的设备。安装器备份旧 init/监管脚本，保留已保存的
代理选择；日常探测仍为 60 秒。该包尚待用户设备安装及重启验证。

用户安装专用包成功，安装器报告 `every 5 seconds, up to 2 minutes`，
Saved frontend=homeproxy。原核心 PID 20463 继续运行，已确认更新安装未切换前端
或打断该核心。尚待更新后的重启及真实上网确认，不能据此声称开机五秒内启动。

用户随后重启：`uptime` 显示 up 0 min，新 PID 8427 正在运行
`/usr/bin/sing-box run --config /var/run/homeproxy/sing-box-c.json`。
已验证专用更新安装后的重启自启动成功，并在开机不足一分钟时观察到核心。
没有精确的启动秒数测量，不能声称五秒内完成；用户尚未确认实际代理上网。
