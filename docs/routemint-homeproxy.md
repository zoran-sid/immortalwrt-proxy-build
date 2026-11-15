# RouteMint 内地规则导入 HomeProxy

适用：本仓库修复后的 HomeProxy（官方 c84adb9 生成器，已安装 sing-box 1.14
兼容补丁）。安装包：`python scripts/package_routemint.py` →
`dist/routemint-homeproxy-cn.tar.gz`。

上传到设备 `/tmp/` 后执行：

```sh
tar -xzf /tmp/routemint-homeproxy-cn.tar.gz -C /tmp
sh /tmp/routemint-homeproxy/install.sh
```

只校验不应用：在最后一条命令后增加 `--check-only`。
如果 LuCI 有未应用的编辑，脚本会停止，须先保存应用或撤销这些编辑。

## 导入内容

- cn-mainland＋ads-light，29 个规则集，保留上游优先顺序和默认代理出口。
- 国内、游戏下载、网盘等直连；广告/追踪器拒绝；AI、流媒体和境外服务代理。
- 创建 RouteMint 路由节点、规则集、路由规则及 DNS 设置，已有自定义规则暂时
  停用而不删除；原始节点和订阅保留。
- 每个分类远程规则以原生 sing-box `update_interval=1d` 更新；规则顺序和
  策略映射是安装包中的预设快照，仓库模板变化不会自动重写个人配置。
- 国内和节点引导解析走阿里 DoH；代理类查询走 Cloudflare DoH，出口与相应
  服务策略一致。保留原 IPv6、端口和代理模式；不等于启用此前未配置的 IPv6 接管。
- 默认沿用当前自定义默认出口，或原 main_node（支持 URLTest 列表）。所有代理
  分类默认共用当前节点。没有节点名地区过滤，不保证 AI 解锁或媒体地区。

可通过环境变量绑定已有节点 ID，例如：

```sh
RM_AI_NODE=cfg123456 RM_NETFLIX_NODE=cfgabcdef sh /tmp/routemint-homeproxy/install.sh
```

上面的 ID 是示例，必须换成设备已有的 node section ID。支持 RM_PROXY_NODE、
RM_AI_NODE、RM_NETFLIX_NODE、RM_DISNEY_NODE、RM_YOUTUBE_NODE、RM_SPOTIFY_NODE、
RM_TIKTOK_NODE、RM_MAX_NODE、RM_PRIMEVIDEO_NODE、RM_STREAMING_NODE。

## 校验与恢复

1. 备份到设备 `/root/routemint-homeproxy-backup-*`，权限 700；包含设备配置和
   节点，不要上传或公开。
2. 使用独立 UCI 配置/增量目录生成待应用配置；旧节点信息不会输出到日志。
3. 下载全部 SRS 并用设备的 sing-box 反编译验证。直连下载失败时尝试现有
   HomeProxy 本地 SOCKS 端口；下载失败则不改运行配置。
4. 复制设备原生成器，只改配置游标和输出路径，执行真实生成及 `sing-box check`。
5. 校验成功后暂停 guard、原子替换配置、统一切到 HomeProxy，再恢复 guard。
   未观察到核心连续运行三次则还原三个原配置并尝试恢复原代理。

配置校验与核心存活不等同于真实代理上网验证。导入后检查目标服务出口、国内
下载和 DNS 是否符合预期，再验证重启。

## 本地测试范围

7 项策略/包测试：节点及订阅保留、顺序、DNS 同出口、AI 覆盖、幂等、缺失
节点拒绝、临时生成器路径和包换行。4 项隔离 shell 事务测试：校验失败不动
运行配置、仅检查、正常切换、核心未启动时还原配置及原前端。shell 语法通过。
ucode 逻辑以 JS 兼容子集和假 UCI 游标测试；真实设备原生编译与真实生成器
校验由安装器执行。尚未在用户设备验证此次策略导入和流量出口。

首次设备反馈：ucode 配置生成程序执行成功，29 个 SRS 均通过反编译；临时
生成器因复制到 /tmp 后找不到同目录 `homeproxy.uc` 而停止。运行配置尚未替换。
修正为编译和执行预览时都显式添加 `-L /etc/homeproxy/scripts`，并在下载前
先编译预览生成器，尽早发现模块或语法问题。实际导入结果仍待用户重试。

用户修正搜索路径后重试成功：设备输出 `Staged HomeProxy configuration passed
sing-box check.` 和 `Applied RouteMint cn-mainland / ads-light.`。已确认设备上的
原生 UCI 生成、29 个 SRS 反编译、真实配置校验、应用以及核心连续存活检查通过。
节点出口的真实网站访问、DNS/分类命中及此次规则导入后的重启仍待用户验证。
