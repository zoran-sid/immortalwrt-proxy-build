# 升级与回滚

## 保留两个版本

```sh
# 升级前
sysupgrade -b /tmp/backup-$(date +%F).tar.gz     # 配置备份
opkg list-installed > /tmp/pkgs-$(date +%F).txt  # 25.12 起用 apk info
```

保留上一版镜像文件本身。squashfs 镜像的 `sysupgrade` 可以保留配置，
ext4 镜像会丢掉分区结构，升级前一定先备份。

## 回滚

- **x86 物理机**：换一个 U 盘/SSD 启动旧镜像，或用 IPMI/PiKVM 挂载旧 iso。
- **VM**：升级前打 snapshot，回滚直接还原快照，这是最快路径。
- **实体 ARM 路由**：取决于设备有没有 recovery 模式（TFTP/USB）。未确认过
  recovery 方法的设备，不要在没有串口的情况下升级。

## 版本之间

| 从 → 到 | 注意 |
|---|---|
| 24.10.x → 25.12.x | 包管理器从 opkg 换成 apk，配置大体兼容，但第三方 ipk 不能再装 |
| 25.12.x → 25.12.y | 直接 `sysupgrade`，保留配置 |
| 换代理 profile | 不要原地覆盖，重刷镜像更干净；至少先备份 `/etc/config/passwall` 等 |

## 构建溯源

每次构建的 `BUILDINFO` 记录了版本、包清单、ImageBuilder 校验和、commit 和
run id。出了问题先对这份文件，确认出问题的是哪一次构建，而不是"某天编的"。
