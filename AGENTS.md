# AGENTS.md — 交接说明

给后续接手的 agent（Codex / Claude Code / 其他）。读完这一份就应该能继续干活，
不需要重新探索仓库、不需要重新踩一遍已经踩过的坑。

最后更新：2026-11-13，对应 commit `1a704da`。

---

## 1. 这是什么

用 ImmortalWrt 官方 **ImageBuilder** 在 GitHub Actions 上产出 x86-64 固件的仓库，
镜像内集成一个科学上网前端（PassWall / OpenClash / HomeProxy，三选一）。

**不是**源码编译。不要把它改成 `make menuconfig` 的流程，除非用户明确要求自定义内核。

- 仓库：`zoran-sid/immortalwrt-proxy-build`（**private**）
- 默认分支：`main`
- 发布：Release tag `25.12.2`，已含 51 个资产（3 个 profile × 12 镜像 + 元数据）

## 2. 当前状态（先看这个）

| 项 | 状态 | 证据 |
|---|---|---|
| 三个 profile 构建 | ✅ 全绿 | runs `43442349059`(passwall) / `43442356505`(openclash) / `43442364453`(homeproxy) |
| 校验和 | ✅ | 下载镜像后 `sha256sum -c` 通过 |
| package manifest | ✅ | passwall 309 包，由构建 rootfs 的 apk 数据库生成 |
| validate 工作流 | ✅ | shellcheck / YAML / 包清单检查 |
| 启动 / 联网 / 真机 | ❌ **从未验证** | 不要声称"可用" |
| IPv6 透明代理 | ❌ 未验证 | 镜像保留 v6 栈，路径没测 |
| ARM64 | ❌ 未实现 | 缺设备型号，不能做"通用 ARM64" |
| 24.10.6（opkg）路径 | ⚠️ 代码兼容但没跑过 | 只有 25.12.2 被实际验证 |

## 3. 常用命令

```bash
# 触发构建（单 profile）
gh workflow run build-firmware -f version=25.12.2 -f proxy=passwall -f release=true

# 观察
gh run list --workflow=build-firmware --limit 5
gh run watch <run-id>
gh run view <run-id> --json status,conclusion,jobs --jq '.jobs[]|"\(.name): \(.conclusion)"'

# 失败的日志（注意 sandbox：gh 默认写 ~/.cache，被拦时用这个）
XDG_CACHE_HOME=/tmp/ghcache gh run view <run-id> --log-failed

# 打 tag 走全量发版（passwall + openclash + none）
git tag v25.12.2 && git push origin v25.12.2

# 本地构建（必须 Linux；macOS 跑不了 Linux 版 ImageBuilder）
VERSION=25.12.2 PROXY_PROFILE=passwall bash scripts/build.sh
```

本地跑之前需要：`sudo apt-get install -y zstd qemu-utils genisoimage`。
本机无 Docker daemon 时不要试图在 macOS 上跑，直接走 Actions。

## 4. 结构

```
targets/x86_64.yml              目标描述（target/profile/测试状态），目前只是文档，未被执行逻辑读取
configs/common/packages.txt     所有 profile 共用；以 '-' 开头的行表示移除默认包
configs/proxy/<p>/packages.txt  每个 profile 的增量包
configs/proxy/<p>/files/        可选，作为 ImageBuilder 的 FILES 覆盖层（目前都不存在）
scripts/build.sh                下载 IB → 校验 → make image → 收集镜像 → 生成元数据
scripts/diagnostics/network-doctor.sh   在设备上跑的只读诊断，自动脱敏
.github/workflows/build.yml     plan → build(matrix) → release
.github/workflows/validate.yml  shellcheck / YAML / 包清单 / 上游可达性
```

`build.sh` 的输入全部是环境变量：`VERSION`、`TARGET`、`PROFILE`、`PROXY_PROFILE`、
`ROOTFS_PARTSIZE`、`MIRROR`。输出固定在 `dist/`：

```
dist/images/immortalwrt-<ver>-x86_64-<profile>-<fmt>
dist/SHA256SUMS-<ver>-x86_64-<profile>.txt
dist/BUILDINFO-<ver>-x86_64-<profile>.txt
```

## 5. 已核实的上游事实（2026-11-13）

- `25.12.2` = current stable（2026-09-06）；`24.10.6` = old stable（2026-04-26）
- 25.12 系列使用 **apk**（`.apk` / `index.json`），24.10 使用 opkg
- x86-64 内核 `6.12.103`
- ImageBuilder 的 sha256：`b9b290f5ef60a22bccd5bdca9c1e7523b8fc84e60462afbf550e20db94acf2c6`
- 官方 feed 内的版本：`luci-app-passwall 26.10.29-r1`、`luci-app-openclash 0.48.002`、
  `luci-app-homeproxy 26.298.29275`、`xray-core 26.10.21-r1`、`sing-box 1.12.25-r1`
- 第三方项目的最新 tag 可能更新，但本仓库只使用该 release 自带 feed，**不要**混入第三方 ipk

## 6. 已经踩过的坑（别重犯）

1. **checksum 格式**：下载站的 `sha256sums` 是 `<hash> *<file>`（二进制标记 `*`），
   `grep " file"` 匹配不到。现在用 awk 匹配 `$2 == n || $2 == "*" n`。
2. **dnsmasq 冲突**：默认包含 `dnsmasq`，要装 `dnsmasq-full` 必须在清单里先写 `-dnsmasq`。
   ImageBuilder 的 `-pkg` 语义在 Makefile 里是过滤 BUILD_PACKAGES，apk/opkg 都支持。
3. **包名**：没有 `nftables` 这个包，实际是 `nftables-json` / `nftables-nojson`。
4. **宿主工具**：`qemu-img`（qcow2/vdi/vmdk/vhdx）和 `mkisofs`（ISO）不在 IB 里，
   CI 必须装 `qemu-utils genisoimage`；`mkfs.fat`、`mcopy`、`mkfs.ext4`、grub 工具是 IB 自带的。
5. **release job 没有 git 上下文**：`gh release` 会 `fatal: not a git repository`，
   必须传 `GH_REPO: ${{ github.repository }}`。
6. **多 profile 合并**：三个 artifact 合并到同一目录时，`BUILDINFO` / `SHA256SUMS`
   同名会互相覆盖。现在文件名带 profile 后缀，不要改回去。
7. **上传目录**：`gh release upload artifacts/*` 会把 `images/` 目录当资产，失败。
   现在用 `find -type f -print0 | xargs -0 gh release upload`。
8. **manifest**：`make image` 不产出 manifest。现在从
   `build_dir/target-*/root-*/lib/apk/db/installed`（或 opkg 的 `usr/lib/opkg/status`）生成，
   这比再跑一次 `make manifest`（会重建 rootfs）便宜得多。

## 7. 改动指南

**加一个包**：改 `configs/*/packages.txt`，一行一个，`#` 注释，`-foo` 表示移除。
先确认包在该 release 的 feed 里存在（`releases/<ver>/packages/x86_64/<feed>/index.json`
对 apk，或 `Packages.gz` 对 opkg）。

**加一个代理 profile**：`mkdir configs/proxy/<name>` + `packages.txt`，
再把它加进 `build.yml` 的 `plan` job（tag 触发的矩阵）和 `workflow_dispatch` 的 `options`。
注意：同一镜像里只放一个透明代理前端，不要把 passwall 和 openclash 塞一起。

**加 target（ARM）**：不能只改 `targets/*.yml`。需要真实的
`target/subtarget/profile`，ImageBuilder 文件名也会变（`immortalwrt-imagebuilder-<ver>-<target>-<subtarget>...`），
`build.sh` 里有多处 `x86_64` 硬编码（产物命名、matrix），必须先重构命名再扩展。
在拿到具体设备型号前**不要**开工。

**换版本**：`-f version=<ver>` 即可，但 24.10 走 opkg，`nftables-json`、`apk` 相关假设要复核。

**改根分区大小**：`ROOTFS_PARTSIZE`（MiB），默认 1024。

## 8. 不变量（改代码时不能破）

- 只用官方 feed 的包；不引入第三方 ipk，不 pin 到上游项目的最新 tag
- Action 全部 pin 到 commit SHA（`checkout@11d5960a…`、`upload-artifact@ea165f8d…`、
  `download-artifact@d3f86a10…`），不要改回 `@v4`
- 默认 `permissions: contents: read`，只有 release job 用 `contents: write`
- 不把凭据、订阅 URL、节点信息写进仓库或固件
- 不在 CI 里跑真实机场节点；CI 只做合成测试
- 元数据文件名保持 `<NAME>-<ver>-x86_64-<profile>.txt`，release 流程依赖这个模式
- 不提交二进制（`.gitignore` 已覆盖 `dist/`、`.work/`）

## 9. 未完成 / 建议的下一步

按价值排序，每项都给了验收标准：

1. **QEMU 冒烟测试**（最高价值）：x86 镜像用 `qemu-system-x86_64` 无头启动，
   验证 ubus 可用、dnsmasq 起来、firewall4 加载、代理前端服务存在。
   验收：新 workflow 能在 CI 里跑，失败时能拿到串口日志。
2. **ARM64 reference target**：等用户给型号。验收：真实 `target/subtarget/profile`，
   Release 名字里写明设备，不出现"arm64-generic"。
3. **IPv6 路径测试**：v4/v6 的 DNS、tproxy、route 分别断言。验收：
   `network-doctor` 的 `IPV6_PROXY_BYPASS` 分类在 CI 合成环境里可复现。
4. **soak / 生命周期测试**：`start/stop/restart × 100`，确认无重复核心进程、
   nftables 不重复追加。验收：脚本 + 一台长期在线的测试机。
5. **诊断脚本落地验证**：`network-doctor.sh` 从未在真实设备上跑过，
   `classify()` 里的判断逻辑只是启发式。验收：在 x86 虚机上人为制造
   DNS 失败/规则缺失，确认分类正确。

## 10. 需要人先回答的四件事

1. ARM 实体机型号（决定 target）
2. 机场订阅格式（Clash YAML / 通用 URL / V2Ray base64 / sing-box JSON）
3. 宿主与虚拟化平台（PVE / VMware / Hyper-V / Apple Silicon）
4. 代理失效策略：`fail-open` 还是 `fail-closed`

在拿到 1 之前，不要动 ARM；在拿到 4 之前，不要写 watchdog 的默认行为。

## 11. 诊断与排障约定

- 任何"国内能开、Google 不通"的报障，先跑 `network-doctor.sh` 拿分类，
  不要直接改 DNS 配置。
- 不要用"关闭 IPv6"当修复。
- 不要写 `while true; do restart; done`，用 procd 的 respawn 阈值 + 退避。
- 订阅/规则更新必须是"下载到临时文件 → 校验 → 原子替换 → 健康检查 → 失败回滚"。

## 12. 完成标准

改完任何东西，至少跑通：

```bash
gh workflow run build-firmware -f version=25.12.2 -f proxy=<受影响的 profile>
# 等绿
gh release view 25.12.2 --json assets --jq '.assets|length'   # 数量不应减少
```

涉及 `build.sh` 的改动，还要确认 `dist/` 里三个文件都在：
镜像、`BUILDINFO-*.txt`、`SHA256SUMS-*.txt`，且 `sha256sum -c` 通过。
涉及镜像内容的改动，下载 manifest 确认目标包真的在里面，而不是"构建成功"。
