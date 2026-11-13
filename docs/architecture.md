# 架构与设计取舍

## 为什么用 ImageBuilder 而不是源码编译

源码编译（`make menuconfig` → `make -j$(nproc)`）在 GitHub 托管 runner 上：

- 单次 1–3 小时，14GB 磁盘常不够，需要先删预装 SDK；
- 缓存难做对，`dl/` 与 toolchain 体积大、命中率低；
- 失败点多（feed 更新、上游 patch），排障成本高。

ImageBuilder 只做"解包 + 装 ipk/apk + 重新打包"，5–10 分钟完成，
并且产物与官方发布同源同内核，插件 ABI 不会错配。

代价：不能改内核编译选项、不能裁 busybox、不能打源码补丁。
真需要这些时再切源码编译，仓库结构不影响。

## 数据流

```
workflow_dispatch / tag
        │
        ▼
   plan job ── matrix: [passwall, openclash, none]
        │
        ▼
 build job (per profile)
   scripts/build.sh
     ├── 下载 sha256sums + ImageBuilder (.tar.zst)
     ├── 校验 SHA256
     ├── 合并 configs/common + configs/proxy/<p> 包清单
     ├── make image PROFILE=generic PACKAGES=... ROOTFS_PARTSIZE=1024
     └── 收集 img.gz/qcow2/vmdk + manifest + SHA256SUMS + BUILDINFO
        │
        ▼
 upload-artifact（每个 profile 一个）
        │
        ▼（可选）
 release job → gh release create/upload
```

## 版本固定

| 组件 | 固定方式 |
|---|---|
| ImmortalWrt | `VERSION` 输入 + ImageBuilder 的 `sha256sums` 校验 |
| 插件 | 随该 release 的官方 feed，不额外引第三方 ipk |
| Action | 官方 action 全部 pin 到 commit SHA |

上游 25.12.2 的 feed 指向固定 commit（见 `releases/25.12.2/packages/x86_64/feeds.conf`），
所以同一个 `VERSION` 值天然可复现，不需要自己维护 feed lock。

## 为什么不默认塞满功能

镜像越大，升级越慢，出问题时变量越多。默认只装：LuCI、dnsmasq-full、curl、
kmod-tun、kmod-nft-tproxy、nftables、conntrack、tcpdump，加一个代理前端。
需要 ddns、frp、tailscale 时在 profile 的 `packages.txt` 里加一行即可。

## 旁路由假设

镜像不预设 LAN 网段、不预设 DHCP 角色。首次启动走 ImmortalWrt 默认
`192.168.1.1`，登录后自己改。文档不假设你的主路由是 `192.168.1.0/24`。
