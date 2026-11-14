# 验证报告 — ImmortalWrt Phase 2

日期：2026-11-14
执行环境：Windows 11（26100）× PowerShell 5.1 + 原生 QEMU 11.1.3（TCG）
仓库：`zoran-sid/immortalwrt-proxy-build`
验证分支：`fix/build-metadata-and-presets`（不覆盖 `main`）

本报告只记录**实际执行过**的测试。状态定义：

- **PASS**：实际执行并通过
- **FAIL**：实际执行但失败
- **BLOCKED**：因环境/硬件/凭据无法执行
- **NOT TESTED**：尚未执行

构建成功、QEMU 启动成功、网络功能正常、真实硬件稳定是四个不同结论；本报告
分别标注，不把前者当成后者。

---

## 1. 环境

| 项 | 结果 |
|---|---|
| Windows | 11，build 26100（PowerShell 5.1） |
| CPU 虚拟化 | 固件已启用 VT-x；未启用 Hyper-V / WHPX（无管理员） |
| 磁盘 | C: 32.7 GB 可用，E: 227 GB 可用（镜像/日志放 E:） |
| WSL2 | **BLOCKED**：未安装；`wsl --install` 需要管理员并可能重启 |
| Python | 3.12.10（用户级）+ PyYAML 6.0.3 |
| ShellCheck | 0.10.0（portable） |
| QEMU | 11.1.3，MSYS2 `ucrt64` 便携安装，无需管理员；TCG 无 KVM |
| Git / gh | Git 2.54.0；gh 使用 Windows 凭据管理器中的现有 token（repo+workflow） |

WSL 不可用不阻塞 CI 构建与 QEMU 验证，但**本地 ImageBuilder 构建**仍需要
Linux（WSL 或 Linux 主机）。安装命令与后续步骤见 §8。

网络说明：当前进程在 Low 完整级时 schannel 报 `SEC_E_NO_CREDENTIALS`，
提升到 Medium 后 HTTPS 正常；脚本统一使用 Git 的 `credential.helper=manager`
读取已有凭据，未把 token 写入仓库或日志。

---

## 2. 仓库与 CI 实况（开始前重新拉取）

- `main`：`e5e20c56188510f2a26dceba6d2fb919378bcce1`（与交接信息一致）
- 交接时最后一次 Build CI `43551668831` **已结束，结论 failure**（不是“仍在运行”）
- 交接时 Validate CI `43551665683`：success
- 仓库实际为 **public**（交接文档写的是 private，已过期）

### 2.1 失败根因（run 43551668831）

日志最后一行：

```
scripts/build.sh: line 176: PRESET_KIND: unbound variable
```

镜像已经构建完成（`==> collecting images`、`==> writing metadata` 之后才失败），
但 BUILDINFO 引用了 `resolve.py` 从未导出的 `PRESET_KIND`，在 `set -u` 下退出，
导致 artifact 上传步骤被跳过；因此“构建失败”实际上不是镜像构建失败。

### 2.2 其他已确认的构建缺陷

| 问题 | 证据 | 修复 |
|---|---|---|
| `presets/full.yml` 仍含 blocked 的 `proxy-nikki` | `resolve.py matrix --event tag` 直接失败，full/矩阵无法解析 | 从 full 移除；新增测试禁止预设引用 blocked 模块 |
| ARM 产物只收集 `.img.gz` | run 43553962605（gl-mt2500/lite）成功建镜像后报 `no comparable images produced` | 收集 `.bin/.itb/.ubi`，跳过 initramfs/preloader/u-boot/fip |
| `qemu-ga` 与 `open-vm-tools` 文件冲突 | run 43553957085：`trying to overwrite sbin/shutdown owned by open-vm-tools` | vm-guest 只保留 qemu-ga；文档说明 VMware 用 custom |
| `CONFIG_SIGNATURE_CHECK=` 全局关闭 apk 签名校验 | `scripts/build.sh` 旧代码 | 删除；第三方 apk 仓库改为直接拒绝，签名校验保持开启 |
| `dist/` 校验和会混入其他目标 | `sha256sum -- *` 对整个 `dist/images` | 只对本 target/preset 的文件做清理与校验 |
| 构建产物没有自动校验 | — | 新增 `tests/verify_artifacts.py` 并在 build job 中执行 |

---

## 3. 构建结果

分支 `fix/build-metadata-and-presets` 上的实际 CI 结果：

| 目标 / 预设 | Run | 提交 | 结论 | artifact 校验 |
|---|---|---|---|---|
| x86-64 / rescue | [43553951623](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43553951623) | 1e3ea2c | **PASS** | **PASS**（15 个文件，含 manifest） |
| x86-64 / standard | [43554466382](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43554466382) | c93c4df | **PASS** | **PASS** + QEMU 冒烟 PASS |
| x86-64 / full | [43553946255](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43553946255) | 1e3ea2c | **PASS** | **PASS** |
| x86-64-vm / standard | [43554233088](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43554233088) | a57f08d | **PASS** | **PASS**（含 qcow2/vmdk/vhdx/vdi） |
| gl-mt2500 / lite | [43554228173](https://github.com/zoran-sid/immortalwrt-proxy-build/actions/runs/43554228173) | a57f08d | **PASS** | **PASS**（factory/sysupgrade.bin） |
| x86-64 / standard（watchdog 修复后复验） | 43558113595 | 197152c | 见 §6.2 | — |

产物校验（`tests/verify_artifacts.py`）检查：每个 SHA256 条目存在且哈希匹配、
没有未纳入校验的产物、BUILDINFO 必需键齐全、manifest 非空、文件名符合
`immortalwrt-<ver>-<target>-<preset>-*`。

示例（rescue）：

```
artifact dir: .../rescue
build: 25.12.2 / x86-64 / rescue (preset)
images checked: 15, product files: 14
PASS: checksums, BUILDINFO and manifest are complete and consistent
```

BUILDINFO 已包含 `preset_kind=preset`、`default_proxy`、模块列表、IB sha256、
git commit、run id/url、构建时间。

---

## 4. QEMU 启动冒烟（Windows 原生 QEMU，TCG）

测试脚本：`tests/qemu_smoke.py`
镜像：`immortalwrt-25.12.2-x86-64-{rescue,standard}-squashfs-combined.img.gz`
解压为 raw，`-machine q35 -m 1024 -smp 2`，IDE 磁盘，两块 e1000，
串口 TCP 控制台；`eth0` = LAN，`eth1` = QEMU 用户态 DHCP WAN。

| 检查 | rescue | standard |
|---|---|---|
| ubus / system board | PASS | PASS |
| procd service list | PASS | PASS |
| LAN 192.168.1.1 | PASS | PASS |
| dnsmasq A | PASS | PASS |
| dnsmasq AAAA | PASS | PASS |
| nftables ruleset | PASS | PASS |
| dnsmasq 进程 | PASS | PASS |
| fw4 nftables 表 | PASS | PASS |
| SSH :22 | PASS | PASS |
| LuCI HTTP 200 | PASS | PASS |
| WAN 默认路由 | PASS | PASS |
| 外网 IPv4（baidu） | PASS | PASS |
| 冷启动后 UCI + WAN 持久化 | PASS | PASS |

结论：**x86 镜像可在 QEMU 启动，SSH/LuCI/DNS/防火墙/基础转发正常**。
这仍不等于真实硬件或真实网络稳定。

### 4.1 两个真实发现

1. **x86 镜像默认没有 WAN**：`/etc/config/network` 只有 `lan`，没有 `wan`。
   首次登录向导（主路由 DHCP/PPPoE）是正常配置路径；冒烟测试显式添加
   `eth1` DHCP WAN。
2. **f2fs overlay 惰性检查点**：overlay 是 `/dev/sda2` 尾部偏移处的 f2fs loop
   设备。`uci commit` 后立刻断电，f2fs 会回滚到上一个检查点（标记测试复现：
   标记已写入宿主镜像，但下次挂载用旧检查点）。`sync` + 等待约 20 秒后
   冷启动，hostname 与 WAN 配置均保留。冒烟测试因此等待检查点再断电。

串口在 TCG 下会丢字符，测试脚本已限速发送并用 `@@B/@@E` 帧解析输出；这是
测试基础设施问题，不是固件问题。

---

## 5. 代理前端切换（QEMU 内，standard 镜像）

第一次运行时部分检查因为串口丢字符被误判；随后用聚焦脚本逐条复现，确认：

| 场景 | 结果 |
|---|---|
| `proxy-switch passwall` | rc=0，active=passwall，passwall enabled，homeproxy disabled |
| `proxy-switch homeproxy` | rc=0，active=homeproxy，passwall disabled |
| 切换到未安装的 nikki | rc=1，输出 `nikki is not installed`，active 仍为 homeproxy，homeproxy 仍 enabled |
| 目标存在但启动失败（临时把 passwall init 换成 `exit 1`） | rc=1，自动恢复 homeproxy，active 回到 homeproxy |
| 连续切换 3 轮 | nftables 无重复表（dupes=0） |
| `proxy-switch off` | active=off，xray/sing-box/mihomo 均停止 |

结论：**互斥切换、未安装前端保护、失败回滚在 QEMU 内按设计工作**。

### 5.1 看门狗真实缺陷（已修复）

运行日志：

```
daemon.err proxy-guard-watchdog: /usr/sbin/proxy-guard-watchdog:
/lib/functions.sh: line 546: IPKG_INSTROOT: parameter not set
```

`set -u` 与 `/lib/functions.sh` 冲突，watchdog 一启动就退出；procd 重试 5 次后
放弃，`state` 一直是 `normal`，fail-open 永远不会触发。这是**会导致代理故障时
不恢复**的 P0 级缺陷。

修复：source 前定义/导出 `IPKG_INSTROOT`，仅 source 期间临时关闭 nounset。
新增静态回归测试。修复后的完整运行时复验见 §6.2。

---

## 6. 向导与回滚（QEMU 内）

第一次运行时已执行并通过：

| 场景 | 结果 |
|---|---|
| 非法 LAN（999.1.1.1） | 拒绝，rc≠0，无副作用 |
| 未安装的代理前端（nikki） | 拒绝，rc≠0，未创建备份 |
| 应用 router-dhcp + LAN 192.168.99.1 | rc=0，UCI 与接口生效 |
| 90 秒不确认 | 自动回滚：LAN 恢复 192.168.1.1，applied=0，dnsmasq 存活 |
| 回滚后备份/服务 | 备份在 `/etc/wizard-backups`（持久化），回滚显式 reload network/dnsmasq/firewall |

第一次运行在 `curl`（没有 `-m`）处超时，脚本中断，因此以下项尚未完成：
确认路径、订阅 token 日志检查、network-doctor 脱敏、IPv6 信息项。
已修复测试脚本（重试、`curl -m`、显式 if/else），待 watchog 修复镜像重跑后补全。

---

## 7. 诊断工具（代码审查，运行时待复验）

`scripts/diagnostics/network-doctor.sh` 的真实问题：

1. `--redact-node-ip` **替换**了原来的 `redact()`，导致订阅 URL/UUID/token
   不再脱敏。已改为叠加（`redact_secrets | redact_node_ip`）。
2. 使用 bash here-string `<<<`，BusyBox ash（OpenWrt `/bin/sh`）不支持，
   分类逻辑在真机上会静默失效。已改为 POSIX 管道。
3. 只凭“Google 不通”就同时报 DNS 与节点故障；现在显式检测 CN/foreign DNS，
   区分 `DNS_LOCAL_FAILURE`、`DNS_FOREIGN_FAILURE`、`NODE_FAILURE`、
   `INTERNET_DOWN`，并且只有代理实际启用时才报 `FIREWALL_RULE_MISSING`。

---

## 8. 未完成 / BLOCKED

| 项 | 状态 | 原因 / 下一步 |
|---|---|---|
| WSL2 本地 ImageBuilder 构建 | BLOCKED | 无管理员，`wsl --install` 需要安装特性+重启 |
| ARM 真机启动（GL-MT2500/NanoPi） | BLOCKED | 无硬件 |
| 真实机场订阅 / 节点出口 | BLOCKED | 需要凭据；不保存真实订阅 |
| IPv6 透明代理端到端 | BLOCKED | QEMU 用户态网络无可路由 IPv6 上游 |
| 真实网卡（RTL8125/8126/I225） | NOT TESTED | 需要对应硬件 |
| 24–72h soak / 反复重启 | NOT TESTED | 需要更长时间与环境 |
| 向导四个场景真机走查 | NOT TESTED | QEMU 只覆盖 router-dhcp 与校验/回滚逻辑 |
| Brume 3 | BLOCKED | 上游没有 profile，保持 pending-upstream |
| Nikki / EasyTier | BLOCKED | 签名/ABI 未解决，保持 blocked |

WSL2 安装（需要管理员 PowerShell，之后重启）：

```powershell
wsl --install -d Ubuntu
```

重启后：

```bash
sudo apt-get update
sudo apt-get install -y zstd qemu-utils genisoimage python3-yaml shellcheck
cd /path/to/immortalwrt-proxy-build
python3 -m unittest discover -s tests -p 'test_*.py' -v
TARGET_NAME=x86-64 PRESET=standard bash scripts/build.sh
python3 tests/verify_artifacts.py dist
```

---

## 9. 复现命令（已在本次执行中实际使用）

```powershell
# 静态检查
python -m unittest discover -s tests -p "test_*.py" -v
shellcheck -x scripts/build.sh scripts/diagnostics/network-doctor.sh

# 下载并校验构建 artifact
gh run download <run-id> --repo zoran-sid/immortalwrt-proxy-build -n firmware-<ver>-<target>-<preset> -D artifacts/<name>
python tests/verify_artifacts.py artifacts/<name>

# QEMU 冒烟（Windows 原生 QEMU）
python tests/qemu_smoke.py --qemu E:\...\qemu-system-x86_64.exe `
  --image E:\...\immortalwrt-25.12.2-x86-64-standard-squashfs-combined.img `
  --log smoke.log

# 代理/向导/诊断运行时测试
python tests/qemu_runtime.py --qemu E:\...\qemu-system-x86_64.exe `
  --image E:\...\standard.img --log runtime.log
```

镜像解压（Windows 没有 gunzip 时）：

```powershell
python -c "import gzip,shutil; f=gzip.open(src,'rb'); o=open(dst,'wb'); shutil.copyfileobj(f,o,1<<20)"
```
