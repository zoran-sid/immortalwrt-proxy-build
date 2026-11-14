# 架构与设计取舍

## 为什么用 ImageBuilder 而不是源码编译

源码编译在 GitHub 托管 runner 上单次 1–3 小时，14GB 磁盘经常不够，缓存命中率低，
失败点多。ImageBuilder 只做「解包 + 装 apk + 重新打包」，10 分钟完成，产物与官方
发布同源同内核，插件 ABI 不会错配。

代价：不能改内核编译选项、不能裁 busybox、不能打源码补丁。真需要时再切源码编译，
`targets/` 与 `modules/` 的数据结构不影响这件事。

## 数据流

```
workflow_dispatch / tag
        │
        ▼
  resolve.py matrix ── 生成 target × preset 矩阵（tag 14 个组合）
        │
        ▼
  build job (per combination)
    scripts/build.sh
      ├── resolve.py     冲突/requires/目标兼容/体积预算 → 包列表 + files 覆盖层
      ├── 下载 ImageBuilder + sha256 校验（按 target/subtarget）
      ├── thirdparty.py  下载并校验第三方 → 解包成 apk 本地仓库 / 写二进制
      ├── 往 IB 的 repositories 追加本地仓库路径
      ├── make image PROFILE=... PACKAGES=... FILES=overlay ROOTFS_PARTSIZE=...
      └── 收集镜像 + manifest + SHA256SUMS + BUILDINFO
        │
        ▼
 upload-artifact（每个组合一个，名字带 target/preset）
        │
        ▼（可选）
 release job → gh release create/upload
```

## 模块与预设的关系

- 模块是「一组包 + 可选文件覆盖」，声明冲突、依赖、适用目标与最小体积。
- 预设是「模块有序列表 + 适用目标 + 默认代理 + rootfs 大小」。
- 解析器是唯一真相：CI 与本地构建走同一份逻辑，配置错误在下载 ImageBuilder 之前就失败。
- `implicit` 模块（x86 网卡驱动、虚拟机增强）不进预设，按 arch/target 自动附加。
- 只要选了任一代理前端，解析器会自动补 `proxy-guard`，保证「可共存、只启用一个」不被绕过。

## 代理互斥与 fail-open

`proxy-guard` 提供三样东西：

1. 首启 uci-defaults：按预设的默认前端启用一个，其余停用。
2. `/usr/sbin/proxy-switch <passwall|homeproxy|nikki|off>`：停用其余、清理残留 nft 表、
   再启用目标；失败返回非 0。
3. procd 看门狗：DNS + HTTP 204 双探针，连续失败达阈值后按 policy 处理。
   `open`（默认）停用代理恢复直连，冷却后再尝试切回；`closed` 保留原状只告警。

所有重启/切换都有退避，没有 `while true; do restart; done`。

## 版本固定

| 组件 | 固定方式 |
|---|---|
| ImmortalWrt | `version` 输入 + ImageBuilder `sha256sums` 校验 |
| 官方插件 | 随该 release 的 feed，天然可复现 |
| 第三方 | `thirdparty/lock.yml` 的 tag + 每 arch url + sha256，构建时逐个校验 |
| Action | 官方 action 全部 pin commit SHA |

## 产物命名

`immortalwrt-<ver>-<target>-<preset>-<格式>`。custom 构建的 preset 是模块列表的短 hash，
BUILDINFO 里保留完整模块列表，便于对照。

## 不预设的东西

镜像不预设 LAN 网段、不预设 DHCP 角色、不写订阅。这些全部交给首次向导或用户自己改。
