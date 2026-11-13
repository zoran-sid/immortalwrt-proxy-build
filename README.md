# ImmortalWrt proxy firmware builder (x86-64)

用 ImmortalWrt 官方 **ImageBuilder** 在 GitHub Actions 上构建带科学上网插件的固件。
不编译源码，单次构建约 5–10 分钟，产物带 manifest、SHA256 和构建溯源信息。

> 这是"下载 + 组装"而不是"从源码编译"。需要改内核选项、裁剪 busybox、
> 打补丁时才需要换成源码编译（见文末）。

## 支持范围（当前）

| 项目 | 状态 |
|---|---|
| 目标平台 | x86/64（`generic` profile，UEFI + Legacy BIOS） |
| ImmortalWrt 版本 | 25.12.2（默认，apk 包管理）／24.10.6（opkg，按需） |
| 代理 profile | `passwall`、`openclash`、`homeproxy`、`none` |
| ARM64 实体路由 | **未实现**，需要先确定具体设备型号 |
| 真机 / 真实网络测试 | **未做**（CI 只保证构建成功） |

## 代理 profile 说明

| profile | 内容 | 说明 |
|---|---|---|
| `passwall` | luci-app-passwall + xray-core + sing-box | 默认；规则与 DNS 逻辑为国内用户常见配置 |
| `openclash` | luci-app-openclash | 适合需要完整 Clash 规则/订阅生态；官方发布页明确不建议同时开 IPv6 |
| `homeproxy` | luci-app-homeproxy + sing-box | ImmortalWrt 原生前端，TProxy/TUN 走 fw4+nftables |
| `none` | 无代理 | 基线镜像，用于对照和救援 |

三个插件都直接来自 ImmortalWrt 官方 feed（`releases/<ver>/packages/x86_64/luci`），
不引入第三方 ipk，因此不会出现内核 ABI 不匹配导致的装不上。

## 用法

### 网页触发

Actions → `build-firmware` → Run workflow，选择版本、代理 profile；
勾选 `release` 会把产物发布成 Release。

### 命令行触发

```bash
gh workflow run build-firmware -f version=25.12.2 -f proxy=passwall -f release=true
gh run list --workflow=build-firmware --limit 3
gh run watch <run-id>
```

### 打 tag 自动发版

```bash
git tag v25.12.2 && git push origin v25.12.2
```

会依次构建 `passwall`、`openclash`、`none` 三个 profile 并发布到同名 Release。

### 本地构建（Linux）

```bash
VERSION=25.12.2 PROXY_PROFILE=passwall bash scripts/build.sh
# 产物在 dist/images/、dist/SHA256SUMS-<ver>-x86_64-<profile>.txt、dist/BUILDINFO-<ver>-x86_64-<profile>.txt
```

macOS 不能直接跑：ImageBuilder 是 Linux x86_64 二进制。本机需要 Linux 或容器。

## 产物

```
dist/images/immortalwrt-25.12.2-x86_64-passwall-squashfs-combined-efi.img.gz
dist/images/immortalwrt-25.12.2-x86_64-passwall-squashfs-combined.img.gz
dist/images/immortalwrt-25.12.2-x86_64-passwall-ext4-combined-efi.img.gz
dist/images/immortalwrt-25.12.2-x86_64-passwall-*.qcow2 / *.vmdk / *.vdi
dist/images/immortalwrt-25.12.2-x86_64-passwall.manifest
dist/SHA256SUMS-25.12.2-x86_64-passwall.txt
dist/BUILDINFO-25.12.2-x86_64-passwall.txt   # 版本、包列表、IB 校验和、commit、run id
```

写盘：

```bash
gunzip -c immortalwrt-*.img.gz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

## 首次登录后必做

1. 改 LAN IP、关闭/保留 DHCP（旁路由模式务必关 DHCP，或只做 DHCP 但网关指向本机）。
2. 插件的 DNS 只保留一条链路，不要 dnsmasq + SmartDNS + AdGuardHome 叠加。
3. 代理插件里先只填一个节点验证连通，再导入订阅。
4. 出问题先跑 `scripts/diagnostics/network-doctor.sh`，它只读、会自动脱敏。

## 排障

```bash
sh /root/network-doctor.sh            # 全量报告 + 故障分类
sh /root/network-doctor.sh --redact-node-ip
```

输出会给出类似 `DNS_LOCAL_FAILURE`、`FIREWALL_RULE_MISSING`、`IPV6_PROXY_BYPASS`
的分类，而不是一句"网络异常"。详见 [docs/troubleshooting.md](docs/troubleshooting.md)。

## 交接

后续由其他 agent 接手时先读 [AGENTS.md](AGENTS.md)：里面有已验证/未验证的界限、
已经踩过的坑、不变量和下一步的验收标准。Claude Code 通过 `CLAUDE.md` 自动加载同一份内容。

## 仓库结构

```
targets/x86_64.yml              # 目标描述（target/profile/镜像格式/测试状态）
configs/common/packages.txt     # 所有 profile 共用的包
configs/proxy/<profile>/        # 每个代理 profile 的包清单（可选 files/ 覆盖层）
scripts/build.sh                # 下载 IB → 校验 → make image → 收集产物
scripts/diagnostics/            # 只读诊断
.github/workflows/build.yml     # 构建 + 发布
.github/workflows/validate.yml  # shellcheck / YAML / 包清单检查
```

## 本次验证记录（2026-11-13）

| 项 | 结果 |
|---|---|
| 上游版本核实 | 25.12.2 为 current stable（2026-09-06 发布），24.10.6 为 old stable（2026-04-26） |
| 包管理器 | 25.12 用 apk（`.apk` / `index.json`），24.10 用 opkg |
| 构建 | passwall / openclash / homeproxy 三个 profile 全部成功 |
| 产物 | 12 个镜像格式/profile（img.gz、qcow2、vdi、vmdk、vhdx、iso） |
| 校验 | `sha256sum -c` 对已下载镜像验证通过 |
| manifest | 从构建 rootfs 的 apk 数据库生成，passwall profile 共 309 个包 |
| 未验证 | 启动、联网、IPv6、真机、24–72h 稳定性 |

Feed 里锁定的插件版本是 `luci-app-passwall 26.10.29-r1`、`luci-app-openclash 0.48.002`、
`luci-app-homeproxy 26.298.29275`、`xray-core 26.10.21-r1`、`sing-box 1.12.25-r1`。
上游项目最新 tag 可能比这更新，但 25.12.2 这个 release 只保证这一组可用。

## 未完成 / 已知限制

- **没有真机测试**：CI 只证明镜像能构建出来，不证明能启动、能上网。
- **没有 QEMU 冒烟测试**：需要自托管 runner 或本地 KVM，当前工作流不覆盖。
- **没有 IPv6 专项验证**：插件镜像保留 IPv6 栈，但透明代理的 v6 路径未验证。
- **ARM64 未支持**：不同设备 `target/subtarget/profile` 不同，不能做"通用 ARM64 镜像"。
- **24.10.6 兼容性未在 CI 验证**：`VERSION=24.10.6` 走 opkg 路径，代码兼容但未跑过。

改成源码编译（需要自定义内核/补丁）时，替换 `scripts/build.sh` 为
`git clone immortalwrt` + `feeds update/install` + `make menuconfig` 的流程，
GitHub 上单次约 1–3 小时，14GB 磁盘是硬约束，需要先清理 runner 的预装 SDK。
