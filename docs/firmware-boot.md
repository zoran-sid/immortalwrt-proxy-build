# 固件层启动修复与预设精简

2026-11-16。本次修复面向 ImageBuilder 固件本身，设备无需再安装临时修复包。

## 构建内置兼容补丁

`prepare_compat.py` 在 make image 前读取 `thirdparty/lock.yml` 中固定的官方
HomeProxy 源码及 SHA256，生成 FILES 覆盖层：

- sing-box 1.14：迁移 legacy inbound、block 出站和已删除缓存字段。
- 日志创建运行目录，停止未运行服务幂等。
- 不注入旧版依赖小写 wan/IPv4 默认路由的启动门控；已核实的生成器自带 WAN DNS 回退。
- 每个自定义路由节点使用独立出站标签，多个策略复用同一个具体节点也合法；
  URLTest 成员与普通路由节点分别生成。新固件支持直接选择具体节点。
- 实际安装包 manifest 与审核版本不符时拒绝发布镜像，需要重新审核兼容性。
- 维护服务和 proxy-guard 的 rc.d 链接直接写入覆盖层，不依赖开机后才建链接。

这保留了官方 apk 签名校验。源码来源和哈希有记录，未引入未签名 apk 仓库。
官方 25.12.2 feed 的 sing-box 是 1.12.25，镜像使用该官方包；你设备上的 1.14.4
不代表官方包版本。兼容迁移同时按 1.12.25 和 1.14.4 校验，发布检查锁定实际
官方包版本，防止软件源更新后未经复核就继续打包。
另以两个核心逐一反编译验证了 RouteMint cn-mainland / ads-light 的全部 29 个
当前发布规则；实际 SRS 版本均为 2，两种核心均能读取。

## 保留用户选择

后续设备现场验证发现首次初始化脚本 `90-proxy-guard` 也早于上游创建配置文件。
它已移到 `zz-proxy-guard`；目标启用值写入失败时，proxy-switch 现在返回错误，
保留现有运行前端且不记录切换成功。初始化失败会保留默认脚本供下次重试。
新 QEMU 检查同时要求默认 PassWall 的应用开关为 1，防止只检查 rc.d 链接而遗漏。
此项为后续修正，前文所列已构建镜像尚不包含此项修正。

- fail-open 只暂时停核心，不清 enabled 和启动链接。
- 网络事件/周期收敛不会删除当前代理的活动规则。
- PassWall 只识别核心进程，DNS 辅助进程不算核心启动成功；HomeProxy 只识别 run，
  不把 check 和 log-cleaner 当成运行中的代理。
- 自定义模式关闭默认出口时，忽略非自定义模式留下的 main_node。
- 首启默认值不会覆盖升级保留下来的代理选择，即使没有完成过初始化向导。
- PassWall 新装默认启动延时 5 秒；已有向导完成配置保留原延时。
  初始化脚本放在 `zz-passwall-boot`，在上游 `luci-app-passwall` 创建默认配置后执行；
  旧的 89 前缀会过早看到配置不存在而跳过，真实镜像检查已发现并修正此顺序问题。
- HomeProxy 的独立开机 worker 每 5 秒补启动，最多两分钟；等待原生启动过程完成，
  核心运行即退出，用户关闭/fail-open 时退出。日常健康探测继续为 60 秒。

五秒是尝试间隔/启动延时，不是完整启动耗时保证。无有效节点、错误配置、上游
网络故障、节点不可达都可能使实际服务不可用。

## 其他插件

`service-boot` 按插件自己的 UCI 启用字段恢复缺失 rc.d 链接，并补启动一次。
链接已存在则交由原服务 boot/procd 管理，不重复重启、不改用户配置、不拉起
启用值为 0 的服务。已核实的映射：

| 服务 | 配置启用字段 |
|---|---|
| AdGuardHome | adguardhome.config.enabled |
| DDNS-GO | ddns-go.config.enabled |
| Cloudflared | cloudflared.config.enabled |
| UPnP | upnpd.config.enabled |
| NATMap | natmap 类型实例的 enable |
| NPS | nps 类型实例的 enabled |
| 微信推送 | wechatpush.config.enable |
| hd-idle | hd-idle 类型实例的 enabled |
| ttyd | ttyd 类型实例的 enable |

向导 DDNS-GO 原来误写 enable，已改为上游实际读取的 enabled。
FRP、Docker、Samba、Tailscale 等由上游原生启动链接/procd 管理，本仓库没有
通用修改其 enable 字段。保留已有模块，没有把所有预装服务强制开启。
有效认证、节点、隧道服务器、磁盘挂载及网络依赖仍须分别配置，不能保证任意
未配置服务都成功运行。用户在“系统→启动项”明确管理无 UCI 开关服务的自启动。

## 预设

删除 lite/rescue，保留 standard/full/custom。标签发版 8 个组合：
七个 ready 设备的 standard，加 x86-64/full；VM/full 可手动构建。
历史验证文档中保留旧 preset/run 名称作为溯源，不表示仍有这些构建入口。

## 验证

本地回归覆盖预设、构建契约、配置迁移、规则导入、包换行、启用状态恢复与
幂等。构建工作流对 x86-64-vm/standard 自动做真实镜像 QEMU 启动及重启检查：
预置生成器、PassWall 延时、共享节点生成配置校验、HomeProxy 核心重启自启、
DDNS-GO 缺失启动链接恢复及进程检查。

最新回归 [43739095008](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43739095008)：
95 项，94 项通过，1 项需要外部核心路径而跳过；该项已在本机分别使用官方
sing-box 1.12.25 与 1.14.4 补测通过。
架构与包兼容预检查：GL-MT2500 standard
[43732448316](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43732448316)、x86-64 full
[43732445629](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43732445629)
构建及 SHA256/BUILDINFO/manifest 校验通过。这两次预检查早于 PassWall 初始化顺序修正，
不能用来证明最终五秒默认值已生效；未做 ARM 真机启动测试。

镜像预检 [43738427960](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43738427960)
已确认真实生成器和核心配置校验、共享节点标签唯一、HomeProxy 核心启动及关机重启恢复、
DDNS-GO 启用状态的启动链接恢复及进程启动。该任务因旧镜像的 PassWall 默认延时仍为
60 秒而失败，不能标记为整体验证通过。

最终 ARM 构建：GL-MT2500 standard
[43739322776](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43739322776)，
镜像代码 `812ac30`，包含 `zz-passwall-boot` 顺序修正；构建及产物完整性检查通过。

最终虚拟机固件：[43738023389](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43738023389)，
镜像代码 `41b331c`，与后续提交的固件文件一致（后续仅修改测试、工作流和文档）。
构建与 SHA256/BUILDINFO/manifest 校验通过，14 个镜像文件及 manifest 共 15 项校验。
独立 QEMU 任务全部通过：首次初始化完成、预置生成器、维护启动链接、PassWall 五秒
默认值、真实生成器及 sing-box 配置校验、共享节点出站标签唯一、HomeProxy 核心启动、
关机再启动后的选择持久化与核心恢复、DDNS-GO 启动链接恢复和进程启动。
固件下载见该运行的 `firmware-25.12.2-x86-64-vm-standard` artifact，保留 14 天。

固件工作流支持 `verify_run_id` 复用同仓库已保存的 VM standard 镜像，独立重跑启动检查；
成功后保存 `verified-firmware-*`，保留原始 BUILDINFO 和校验和。复测不会发布 Release。

QEMU 使用本地虚拟 SOCKS 节点检查配置与进程，不代表真实代理数据面或 ARM
真机验证。PassWall 本轮检查覆盖默认延时，未使用真实节点在 QEMU 中验证其代理数据面。
