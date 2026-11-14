# AGENTS.md — 交接说明

给后续接手的 agent（Codex / Claude Code / 其他）。读完这份就能继续干活，
不需要重新探索仓库，也不要重新踩已经踩过的坑。

最后更新：2026-11-14。这一版是「模块 + 预设 + 多目标」重构后的状态。

---

## 1. 这是什么

用 ImmortalWrt 官方 **ImageBuilder** 产出多目标固件：x86-64、x86-64 虚拟机，
以及 5 个受支持的 ARM64 设备。镜像内容由「模块 + 预设」组合决定，不编译源码。

- 仓库：`zoran-sid/immortalwrt-proxy-build`（**public**）
- 默认分支：`main`
- 上游基线：ImmortalWrt **25.12.2**（apk 包管理）

**不要**改成 `make menuconfig` 源码编译流程，除非用户明确要求自定义内核。

## 2. 当前状态（先看这个）

| 项 | 状态 |
|---|---|
| 结构重构（targets/modules/presets/resolve/build/workflows） | ✅ 已完成 |
| 解析器/构建契约/BusyBox/回归测试（37 项） | ✅ 本地与 CI 通过 |
| 包名核实（x86_64 / aarch64_cortex-a53 / aarch64_generic） | ✅ 逐包核对过 feed |
| 重构后 CI 构建（rescue、standard、full、vm、gl-mt2500 lite） | ✅ 全部通过，产物 SHA256/BUILDINFO/manifest 校验通过（`docs/validation.md`） |
| QEMU 启动冒烟（rescue、standard） | ✅ 12 项检查 + 冷启动持久化通过 |
| QEMU 内 proxy-switch / fail-open / 向导回滚 / 诊断 | ✅ 40/40 PASS（run 43563224816 镜像；`docs/validation.md`） |
| 向导 UI / proxy-guard / Nikki on ARM | ⚠️ QEMU 内已验证逻辑；**未真机验证** |
| IPv6 端到端 / soak | ❌ 未做（QEMU 用户态网络无 IPv6 上游） |
| Brume 3（GL-MT5000） | ⏳ 上游未提供 profile，仅预留 `targets/brume3.yml` |

不要声称任何镜像「可用」或「真机稳定」；只能声称「构建通过 / QEMU 冒烟通过」。

## 3. 常用命令

```bash
# 单目标
gh workflow run build-firmware -f target=x86-64 -f preset=standard

# 自定义模块（preset=custom 时 modules 才生效）
gh workflow run build-firmware -f target=gl-mt2500 -f preset=custom \
  -f modules=base,nat,proxy-homeproxy,proxy-guard,wizard

# 观察
gh run list --workflow=build-firmware --limit 10
gh run watch <run-id>
XDG_CACHE_HOME=/tmp/ghcache gh run view <run-id> --log-failed

# 全矩阵发版（14 个构建）
git tag v25.12.2 && git push origin v25.12.2

# 本地解析/校验（不需要构建）
python3 scripts/resolve.py check --target x86-64 --preset standard
python3 scripts/resolve.py matrix --event tag | python3 -m json.tool
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 产物校验（SHA256SUMS / BUILDINFO / manifest）
python3 tests/verify_artifacts.py dist

# QEMU 冒烟（Windows 原生 QEMU 也可以；无 KVM 时用 TCG）
python3 tests/qemu_smoke.py --image firmware.img --log smoke.log
python3 tests/qemu_runtime.py --image standard.img --log runtime.log

# 本地构建（Linux；需要 zstd qemu-utils genisoimage python3-yaml）
TARGET_NAME=gl-mt2500 PRESET=lite bash scripts/build.sh
```

macOS 不能构建（ImageBuilder 是 Linux 二进制）。本机无 Docker daemon 时不要尝试本地绕路。

## 4. 结构

```
targets/<name>.yml      真被 build.sh 读取：arch/target/subtarget/profile/rootfs_partsize/status/kind
modules/<name>/         module.yml + packages.txt + 可选 files/（合并成 ImageBuilder 的 FILES）
presets/<name>.yml      modules + targets + proxy_default（可按 target kind 分别指定）
thirdparty/lock.yml     白名单：url + sha256 + 许可证 + 选择理由（repo 或 binary 两类）
scripts/resolve.py      解析器，唯一真相：冲突/requires/目标兼容/预算/files 合并/矩阵
scripts/thirdparty.py   下载 → sha256 校验 → 解包成 apk 本地仓库，或写二进制到覆盖层
scripts/build.sh        编排：resolve → IB → 第三方 → make image → 产物与元数据
tests/test_resolve.py   解析器单测
tests/test_build_plan.py 解析器/build.sh 契约、预设与 ARM 产物回归
tests/test_module_scripts.py 设备端 shell/UI 静态回归（BusyBox、脱敏、回滚）
tests/verify_artifacts.py 构建产物完整性校验
tests/qemu_smoke.py     QEMU 启动冒烟（串口控制台）
tests/qemu_runtime.py   QEMU 内 proxy-switch / watchdog / 向导 / 诊断测试
```

产物命名：`immortalwrt-<ver>-<target>-<preset>-<格式>`，元数据
`BUILDINFO-<ver>-<target>-<preset>.txt` / `SHA256SUMS-<ver>-<target>-<preset>.txt`。
custom 构建的 preset 是 `custom-<sha256(排序模块列表)[:8]>`。

## 5. 解析规则（改模块前必须知道）

- `conflicts`：双向检查，冲突直接报错（如三个 DNS 模块互斥）。
- `requires`：传递闭包，依赖排在前面（`monitor-pro` 依赖 `monitor-lite`）。
- `targets` / `arch`：空数组 = 全部；非空则严格限制，不满足就报错，**不静默跳过**。
- `implicit: true`：不进预设，按 arch/target 自动附加（`x86-nics`、`vm-guest`）。
- `proxy-guard`：只要选了任一 `proxy-*`，解析器自动补上（防止互斥失效）。
- `status: blocked`：报错并给出原因（`vpn-easytier`）。
- 体积预算：`sum(min_flash_mb) ≤ rootfs_partsize - 128`。
- files 合并：多个模块的 `files/` 按模块顺序合并；同一路径内容不同 → 报错，
  内容相同 → 去重。可执行位统一成 755，其余 644。

## 6. 已核实的上游事实（2026-11-14，别重复调研）

- 25.12.2 = current stable（2026-09-06）；24.10.6 = old stable；25.12 起用 apk。
- 内核 6.12.103；x86 kmods 仓库含 `kmod-tun`、`kmod-nft-tproxy`、`kmod-nft-fullcone`、
  `kmod-r8125/r8126/igc`；两个 ARM 目标同样有前四个（无 x86 网卡驱动）。
- `block-mount` 不在通用 feed，在 **target 专属** packages feed 里。
- 没有 `nftables` 包（是 `nftables-json`）；没有 `acme`（是 `acme-acmesh`）；
  没有 `luci-app-mosdns`（只有 `mosdns` 核心）。
- ARM profile 名：`glinet_gl-mt2500`、`glinet_gl-mt2500-airoha`、`friendlyarm_nanopi-r5s/r6s/r76s`。
- 第三方：Nikki v1.26.2 提供 25.12 apk 仓库（三种 arch）但当前 **blocked**（索引签名）；
  mihomo v1.19.34 有 amd64/arm64 二进制（overlay 注入可用）；
  EasyTier 没有 25.12 ABI 资产（blocked）。

## 7. 踩过的坑（别重犯）

1. `sha256sums` 是 `<sha> *<file>`，用 awk 匹配 `$2 == n || $2 == "*" n`。
2. 装 `dnsmasq-full` 前必须先 `-dnsmasq`。
3. `qemu-img`（qcow2/vdi/vmdk/vhdx）和 `mkisofs`（ISO）不在 IB 内，CI 要装
   `qemu-utils genisoimage`。
4. release job 没有 git 上下文，必须传 `GH_REPO`。
5. 多目标合并 artifact 时元数据文件名必须带 target/preset，否则互相覆盖。
6. `gh release upload artifacts/*` 会把目录当资产，用 `find -type f -print0 | xargs -0`。
7. `make image` 不产出 manifest，从 `build_dir/target-*/root-*/lib/apk/db/installed`
   （或 opkg 的 `usr/lib/opkg/status`）生成。
8. **新增**：解析器读 YAML，build job 必须装 `python3-yaml`（plan job 也要）。
9. **新增**：GitHub matrix 需要 `{"include": [...]}` 结构，`fromJSON` 直接吃。
10. **新增**：`python3 scripts/resolve.py matrix` 里不要复用循环变量名（曾把 target 名
    覆盖成 `full`，导致矩阵错乱）。已有单测覆盖。
11. **已废弃**：过去为了注入第三方 apk，会把 `.apk` 拷进 `$IB_DIR/packages/`
    再用 `apk mkndx --allow-untrusted` 重建索引，并传 `CONFIG_SIGNATURE_CHECK=`。
    这会关闭整个构建的 apk 签名校验；现在 `build.sh` 直接拒绝第三方 apk 仓库
    （`TP_REPOS_DIRS` 非空即失败）。第三方**二进制**（overlay 注入，如 mihomo）
    不受影响。
12. **新增**：`build.sh` 里 `cd "$IB_DIR"` 之后不能用相对路径读 `$WORKDIR` 的文件，
    IB 校验和要提前存进变量。
13. **新增**：`custom-<hash>` 预设必须能被再次解析（resolve 把 `custom-*` 当 custom 处理）。
14. **未解决**：Nikki 的 apk 索引签名。第三方包要进镜像，必须先有可信的
    本地 apk 索引（构建密钥 + `apk mkndx --sign` + 公钥放进 `$IB_DIR/keys/`），
    或者上游给出签名方案。`proxy-nikki` 保持 `blocked`，不要加回预设，也不要
    用关闭签名校验的方式绕过。
15. **新增**：`resolve.py` 与 `build.sh` 之间有环境变量契约。重构时漏发
    `PRESET_KIND`，`set -u` 让构建在镜像已经做好、写 BUILDINFO 时退出，CI 什么都没
    上传（run 43551668831）。现在 build.sh 在 source plan 后立刻检查必需变量，
    `tests/test_build_plan.py` 也会校验。
16. **新增**：ARM 产物不一定是 `.img.gz`。mediatek/filogic 的 glinet 设备出
    `squashfs-factory.bin` / `squashfs-sysupgrade.bin`，只收集 `.img.gz` 会让
    gl-mt2500/lite 在构建成功后报 "no comparable images produced"（run 43553962605）。
    收集 `.bin/.itb/.ubi`，并跳过 initramfs/preloader/u-boot/fip。
17. **新增**：`qemu-ga` 与 `open-vm-tools` 都装 `/sbin/shutdown`，25.12.2 的 apk
    会因文件冲突拒绝同时安装（run 43553957085）。`vm-guest` 只保留 qemu-ga；
    VMware 用户用 custom 构建替换。
18. **新增**：x86 ImageBuilder 镜像默认只有 LAN，没有 `network.wan`。首次登录向导
    才是配置 WAN 的正常路径；QEMU 冒烟测试会显式加 `eth1` DHCP WAN。
19. **新增**：x86 的 overlay 是 `/dev/sda2` 尾部偏移处的 f2fs loop 设备。f2fs 惰性
    检查点：`uci commit` 后立刻断电可能回滚到上一个检查点（标记测试已复现）。
    验证持久化时要 `sync` 并等待约 20 秒再断电；不要据此判断镜像不持久化。
20. **新增**：QEMU 串口在 TCG 高负载下会丢字符。测脚本要限制发送速率、用成帧
    输出（`@@B`/`@@E`）解析，不能依赖一次 `send` 完整到达，也不能依赖 `$?`
    单独标记（可能整行被截断）。

## 8. 改动指南

**加模块**：新建 `modules/<name>/{module.yml,packages.txt}`，包名先在对应 release 的
feed 里确认（apk 看 `packages/<arch>/<feed>/index.json`，kmod 看
`targets/<t>/<st>/kmods/<kver>/`）。要覆盖文件就加 `files/`（rootfs 布局）。
加入某个预设才会默认出现；`implicit: true` 则按 arch/target 自动附加。

**加预设**：`presets/<name>.yml`，写 `modules`、`targets`（或 `all`）、
`proxy_default`（可写成按 kind 的 map）。tag 矩阵是否需要新预设，改
`resolve.py::matrix` 里 `chosen` 的规则，并补单测。

**加设备**：复制一个 `targets/*.yml`，填真实 `target/subtarget/profile/arch`。
profile 名取自该 target 的 `profiles.json`。ImageBuilder 文件名由
`immortalwrt-imagebuilder-<ver>-<target>-<subtarget>` 推导，不需要手写。
Brume 3 的模板是 `targets/brume3.yml`，上游就绪后把 `status` 改成 `ready`。

**加第三方**：在 `thirdparty/lock.yml` 新增条目（`type: repo` 或 `binary`），
每个 arch 都要 url + sha256（GitHub API 的 `digest` 字段可直接用），并在注释里写明
选择理由与检查日期。`type: repo` 的压缩包必须包含 `packages.adb`（和 `index.json`）。

**换版本**：`-f version=<ver>`。24.10 走 opkg，`nftables-json`、apk 本地仓库注入、
manifest 解析这些假设都要复核。

## 9. 不变量

- 只用官方 feed 的包；第三方只能来自 `thirdparty/lock.yml`，且 sha256 必须匹配。
- 多个代理前端可共存安装，但同一时刻只能启用一个（`proxy-guard` 负责）。
- 默认 `fail-open`：代理持续失败时恢复直连。改 `policy=closed` 要用户显式选择。
- 不写凭据、订阅、节点到仓库或固件；向导只写设备本地 UCI。
- Action 全部 pin commit SHA；默认 `contents: read`，只有 release job 用写权限。
- 不提交二进制；第三方只记录 URL + sha256。
- 元数据命名 `<NAME>-<ver>-<target>-<preset>.txt`，release 流程依赖它。
- 解析失败必须让构建在下载 IB 之前就停下（resolve 先跑）。

## 10. 未完成 / 下一步（按价值）

1. **WSL2 本地构建环境**：当前机器没有安装 WSL（需要管理员 + 可能重启）。
   命令：管理员 PowerShell 执行 `wsl --install -d Ubuntu`，重启后再跑
   `python3 tests/test_resolve.py`、`tests/verify_artifacts.py` 和 `scripts/build.sh`。
   在此之前本地构建用 CI；QEMU 冒烟在 Windows 原生 QEMU 上已验证。
2. **给本地 apk 索引签名**（恢复 Nikki 的前置条件，见 §7.14）。
3. **真机验证**：向导四场景、proxy-guard 断网切换、真实网卡驱动、ARM 设备启动。
4. **IPv6 端到端**：需要有可路由 IPv6 上游的环境，验证 AAAA/DNS/TPROXY 一致性。
5. **soak**：start/stop/restart × 100，确认无重复核心进程、规则不重复追加。
6. **ARM 硬件测试**：GL-MT2500 / NanoPi 需要真实设备，构建成功≠可启动。

## 11. 需要人决定的事

1. 机场订阅格式（Clash YAML / 通用 URL / V2Ray base64 / sing-box JSON）——
   决定向导里"导入订阅"能做到哪一步（目前只把 URL 存进本机 UCI，仍需到插件页面粘贴）。
2. 是否要 ARM VM（armsr）——当前明确不做。
3. 是否需要 frps、OpenClash 进入默认套餐（现在都不进）。

## 12. 完成标准

```bash
gh workflow run build-firmware -f target=<t> -f preset=<p>
# 等绿后
gh release view <ver> --json assets --jq '.assets[].name' | grep '<t>-<p>'
```

涉及解析器的改动必须 `python3 tests/test_resolve.py -v` 全绿；
涉及模块的改动必须确认目标包在对应 arch 的 feed 里真实存在（不是"应该存在"）。
涉及 `build.sh` 的改动要确认 `dist/` 里镜像、manifest、BUILDINFO、SHA256SUMS 四样齐全。
