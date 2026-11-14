# ImmortalWrt 多目标固件（x86 / ARM64 / 虚拟机）

用 ImmortalWrt 官方 **ImageBuilder** 在 GitHub Actions 上构建固件。镜像按「模块 + 预设」组装：
同一个仓库既能出 x86 物理机、x86 虚拟机，也能出 GL.iNet Brume 2、NanoPi R5S/R6S/R76S 的固件。

不编译源码，单次构建 10 分钟左右，产物带 manifest、SHA256 和构建溯源。

## 设备支持矩阵

| 目标 | 架构 | target / subtarget | profile | 可用预设 | 状态 |
|---|---|---|---|---|---|
| x86-64 | x86_64 | x86/64 | generic | standard / full / rescue | ✅ 支持 |
| x86-64-vm | x86_64 | x86/64 | generic | standard | ✅ 支持（PVE / ESXi / Hyper-V） |
| GL-MT2500（MaxLinear PHY） | aarch64_cortex-a53 | mediatek/filogic | glinet_gl-mt2500 | lite / rescue | ✅ 支持 |
| GL-MT2500（Airoha PHY） | aarch64_cortex-a53 | mediatek/filogic | glinet_gl-mt2500-airoha | lite / rescue | ✅ 支持 |
| NanoPi R5S | aarch64_generic | rockchip/armv8 | friendlyarm_nanopi-r5s | lite / rescue | ✅ 支持 |
| NanoPi R6S | aarch64_generic | rockchip/armv8 | friendlyarm_nanopi-r6s | standard / full / rescue | ✅ 支持 |
| NanoPi R76S | aarch64_generic | rockchip/armv8 | friendlyarm_nanopi-r76s | standard / full / rescue | ✅ 支持 |
| GL-MT5000（Brume 3） | aarch64_cortex-a53 | mediatek/filogic | 未提供 | — | ⏳ 等待上游 |

**关于 Brume 3**：MT7987A + RTL8366ub 的交换芯片驱动仍在上游，25.12.2 与 snapshot 的
`mediatek/filogic` profile 列表里都没有 `glinet_gl-mt5000`。仓库里已有
`targets/brume3.yml`（`status: pending-upstream`），上游出现该 profile 后把 status 改成
`ready` 即可参与构建，不需要改代码。

没有「通用 ARM64 镜像」：每个设备的 profile 都是独立的，刷错设备会变砖。

## 预设

| 预设 | 适用 | 模块 | 默认代理 | rootfs |
|---|---|---|---|---|
| `standard` | 所有目标 | base, nat, PassWall + HomeProxy + guard, frp, nps, monitor-lite, AdGuardHome, vpn-mesh, remote-access, qos, notify, wizard | x86: PassWall / ARM: HomeProxy | 1024 MiB |
| `full` | x86-64、x86-64-vm、R6S、R76S | standard + monitor-pro, nas, docker | 同上 | 2048 MiB |
| `lite` | MT2500、MT2500-airoha、R5S | base, nat, HomeProxy + guard, frp, monitor-lite, vpn-mesh, wizard | HomeProxy | 1024 MiB |
| `rescue` | 所有目标 | base（无任何代理前端） | 无 | 1024 MiB |

`tag` 发版矩阵共 14 个构建：x86-64 × {standard, full, rescue}、x86-64-vm × {standard}、
5 个 ARM 设备 × {lite 或 standard, rescue}。

## 模块

| 分类 | 模块 | 内容 | standard 默认 |
|---|---|---|---|
| base | `base` | LuCI + 中文 + Argon、dnsmasq-full、nftables、TProxy/FullCone kmod、ttyd、attendedsysupgrade | 必选 |
| base | `x86-nics` | RTL8125 / RTL8126 / Intel I225 驱动（仅 x86，自动附加） | 自动 |
| nat | `nat` | UPnP（nftables）、NATMap | ✔ |
| proxy | `proxy-passwall` | PassWall + Xray + sing-box | ✔ |
| proxy | `proxy-homeproxy` | HomeProxy + sing-box | ✔ |
| proxy | `proxy-nikki` | Nikki + Mihomo（第三方，锁 v1.26.2）——**blocked**，见下 | — |
| proxy | `proxy-openclash` | OpenClash，构建时预置 mihomo 内核 | 可选 |
| proxy | `proxy-guard` | 代理互斥 + `proxy-switch` + fail-open 看门狗 | 自动 |
| tunnel | `frp` / `frps` | frpc / frps | ✔ / 可选 |
| tunnel | `nps` | npc / nps（上游维护停滞，建议优先 FRP） | ✔ |
| monitor | `monitor-lite` | nlbwmon、vnstat2、collectd 基础指标 | ✔ |
| monitor | `monitor-pro` | netdata、Prometheus node exporter | full |
| dns | `dns-adguardhome` | AdGuardHome（预装不接管 53，向导里启用） | ✔ |
| dns | `dns-smartdns` / `dns-mosdns` | SmartDNS / mosdns 核心（互斥，mosdns 无 LuCI） | 可选 |
| vpn | `vpn-mesh` | Tailscale、ZeroTier、WireGuard | ✔ |
| vpn | `vpn-easytier` | EasyTier（**blocked**：上游无 25.12 ABI 资产） | — |
| remote | `remote-access` | DDNS-GO、ACME、Cloudflared | ✔ |
| qos | `qos` | SQM、应用过滤/家长控制 | ✔ |
| storage | `nas` | Samba4、diskman、OpenList、hd-idle | full |
| container | `docker` | Docker + Dockerman | full |
| notify | `notify` | 微信推送（掉线/上下线） | ✔ |
| vm | `vm-guest` | qemu-guest-agent、open-vm-tools（仅 VM 目标，自动附加） | 自动 |
| wizard | `wizard` | 首次登录向导（纯 LuCI JS + shell） | ✔ |

代理前端可以同时**安装**，但同一时刻只允许一个**启用**：`proxy-guard` 在首启和
`proxy-switch` 切换时停用其余前端并清理残留 nftables 表。`proxy-openclash` 保留为可选，
不进入任何预设。

> **Nikki 暂不可用**：ImageBuilder 25.12.2 的本地包索引带签名校验，第三方 apk
> 重新索引后 apk 报 `UNTRUSTED signature` 并忽略（构建 run 43550558856 / 43550786477 /
> 43551246458 均因此失败）。模块保留但标记 `blocked`，需要先给本地索引签名（见
> `AGENTS.md` §7.14）。

## 快速开始

### 网页触发

Actions → `build-firmware` → Run workflow：

- `target`：设备
- `preset`：套餐；选 `custom` 时用 `modules` 填逗号分隔的模块名
- `release`：是否发布到 Release

### 命令行

```bash
# 单个目标
gh workflow run build-firmware -f target=gl-mt2500 -f preset=lite

# 自定义模块组合（文件名会带模块列表的短 hash）
gh workflow run build-firmware -f target=x86-64 -f preset=custom \
  -f modules=base,nat,proxy-homeproxy,wizard

# 正式发版：14 个目标/预设组合全出
git tag v25.12.2 && git push origin v25.12.2
```

### 本地构建（Linux）

```bash
sudo apt-get install -y zstd qemu-utils genisoimage python3-yaml
TARGET_NAME=x86-64 PRESET=standard bash scripts/build.sh
```

macOS 上跑不了：ImageBuilder 是 Linux x86_64 二进制，且本地没有 Docker 时没有替代路径。

## 产物

```
dist/images/immortalwrt-25.12.2-x86-64-standard-squashfs-combined-efi.img.gz
dist/images/immortalwrt-25.12.2-gl-mt2500-lite-squashfs-sysupgrade.img.gz
dist/images/immortalwrt-25.12.2-x86-64-vm-standard-squashfs-combined-efi.qcow2
dist/images/immortalwrt-<ver>-<target>-<preset>.manifest
dist/SHA256SUMS-<ver>-<target>-<preset>.txt
dist/BUILDINFO-<ver>-<target>-<preset>.txt   # 模块列表、包、IB 校验和、第三方 sha、run id
```

命名规则：`immortalwrt-<ver>-<target>-<preset>-<格式>`。custom 构建的 `<preset>` 是
`custom-<8 位 hash>`，BUILDINFO 里保留完整模块列表。

## 首次登录向导

首次登录后进入 **系统 → 初始化向导**（`admin/system/wizard`），四步：

1. **场景**：主路由 PPPoE / 主路由 DHCP / 旁路由 / 旅行网关
2. **参数**：LAN 地址、掩码、旁路由网关、DNS、管理密码（≥ 8 位）
3. **代理**：选前端 + 订阅地址（只写本机 UCI，不上传）+ 可选 AdGuardHome / FullCone / Tailscale / DDNS
4. **确认**：应用后 90 秒内确认，超时自动回滚

向导的重型逻辑在 shell 里，SSH 下可以直接用同一套：

```sh
/usr/libexec/wizard/apply.sh scenario=bypass lan_ip=192.168.1.2 \
  lan_mask=255.255.255.0 gateway=192.168.1.1 proxy=homeproxy root_pass='********'
/usr/libexec/wizard/confirm.sh      # 90 秒内确认
```

> 截图待补（向导 UI 尚未在真机上验证，见「构建状态与未验证项」）。

## 第三方来源与许可证

只允许 `thirdparty/lock.yml` 白名单内的第三方资产，构建时逐个校验 sha256，不匹配直接失败。

| 资产 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| [OpenWrt-nikki](https://github.com/nikkinikki-org/OpenWrt-nikki) | v1.26.2 | GPL-2.0 | Nikki + Mihomo apk 仓库（blocked，见上） |
| [mihomo](https://github.com/MetaCubeX/mihomo) | v1.19.34 | GPL-3.0 | OpenClash 预置内核 |

**未纳入**：EasyTier 上游只提供 ipk（22.03.7）与 SNAPSHOT apk，没有与 25.12.2 ABI 匹配的
资产，`modules/vpn-easytier` 标记为 `blocked`。Nikki 的 ARM 资产只覆盖
`aarch64_cortex-a53` 与 `aarch64_generic`，其它架构会在解析阶段直接报错。

新增第三方包请改 `thirdparty/lock.yml`，并在 `AGENTS.md` 的流程内操作。

## 构建状态与未验证项

最近一轮验证（2026-11-14，分支 `fix/build-metadata-and-presets`，详见
`docs/validation.md`）：

| 项 | 结果 |
|---|---|
| x86-64 / rescue 构建 + 产物校验 | ✅ PASS（run 43553951623） |
| x86-64 / standard 构建 + 产物校验 | ✅ PASS（run 43554466382） |
| x86-64 / full 构建 | ✅ PASS（run 43553946255，含 Docker/NAS） |
| x86-64-vm / standard 构建 | ✅ PASS（run 43554233088） |
| gl-mt2500 / lite 构建 | ✅ PASS（run 43554228173，factory/sysupgrade.bin） |
| QEMU 启动（rescue / standard） | ✅ PASS：ubus、procd、dnsmasq A/AAAA、nftables/fw4、SSH、LuCI、WAN、外网、冷启动持久化 |
| QEMU 内代理切换 / fail-open / 向导回滚 | 见 `docs/validation.md`（按实际执行结果记录） |
| ARM 真机启动 | ❌ 未验证（只有构建产物，没有对应硬件） |
| 真实机场订阅 / 节点出口 | ❌ 未验证（不保存凭据，也不接入真实节点） |
| IPv6 透明代理 / TProxy 端到端 | ❌ 未验证（QEMU 用户态网络没有可路由的 IPv6 上游） |
| 24–72h soak、真实网卡驱动 | ❌ 未验证 |

**x86 镜像默认只有 LAN**：ImageBuilder 25.12.2 的 x86 网络配置不含 WAN，
需要首次登录向导（主路由 DHCP/PPPoE）或手工 `uci set network.wan...` 才有外网。
QEMU 冒烟测试会显式添加 `eth1` DHCP WAN。

**f2fs overlay 的检查点**：x86 镜像把 overlay 放在 `/dev/sda2` 尾部的 f2fs
loop 设备上。f2fs 惰性检查点，写入后立刻断电可能回滚到上一个检查点（实测
标记丢失）。正常重启或写入后等待约 20 秒即可持久化，冒烟测试因此会等待
检查点后再断电。

不要声称任何镜像「已在真机稳定运行」；目前能证明的是「构建通过 + 产物
一致 + QEMU 启动/基础网络通过」。

## 仓库结构

```
targets/<name>.yml     设备/平台描述（arch, target, subtarget, profile, rootfs, 状态）
modules/<name>/        module.yml + packages.txt + 可选 files/（FILES 覆盖层）
presets/<name>.yml     模块列表 + 适用目标 + 默认代理
thirdparty/lock.yml    第三方资产白名单（url + sha256 + 许可证 + 选择理由）
scripts/resolve.py     模块解析：冲突/依赖/目标兼容/体积预算/files 合并
scripts/thirdparty.py  下载 → sha256 校验 → 解包 → 写本地 apk 仓库
scripts/build.sh       解析 → 下载 IB → 组装 → make image → 产物与元数据
tests/test_resolve.py  解析器单测（冲突、目标限制、hash 稳定性、矩阵）
```

## 本地验证（Windows / Linux，QEMU）

仓库自带两个不依赖路由器的测试入口：

```bash
# 静态检查：解析器、构建契约、BusyBox 兼容性、redaction、看门狗/向导回归
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 产物校验：SHA256SUMS / BUILDINFO / manifest 是否齐全一致
python3 tests/verify_artifacts.py dist

# QEMU 启动冒烟：ubus、procd、DNS、nftables、SSH、LuCI、WAN、外网、冷启动持久化
python3 tests/qemu_smoke.py --image firmware.img --log smoke.log

# 代理/看门狗/向导运行时测试（需要含 proxy-guard + wizard 的 standard 镜像）
python3 tests/qemu_runtime.py --image standard.img --log runtime.log
```

Windows 上没有 WSL 也可以跑：QEMU for Windows（或 MSYS2 的
`mingw-w64-ucrt-x86_64-qemu`）加 Python 3 即可，TCG 软件模拟不需要 KVM。
Linux 上存在 `/dev/kvm` 时会自动启用 KVM。镜像格式用
`*-squashfs-combined.img.gz`（解压成 `.img`）。

> 串口控制台在高负载 TCG 下会丢字符，`tests/qemu_smoke.py` 已对输入限速并
> 用 `@@B/@@E` 帧解析输出；自己写脚本时不要假设一次 `send` 能完整到达。

## 面向公众

仓库现在是 **public**。镜像不含任何订阅、节点或凭据；第三方资产只记录来源
URL 与 sha256，可审计。
