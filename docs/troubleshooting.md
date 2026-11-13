# 排障：国内正常、Google 不通

这个现象只说明"直连路径还活着"，不能直接判定为 DNS 故障。按下面的顺序缩小范围，
每一步都能把候选原因砍掉一批。

## 0. 先分清是路由器本机还是 LAN 客户端

在路由器上跑 `curl`，再从 LAN 里一台电脑跑同样的命令。两条路径在
OpenWrt 里走不同的 nftables 链（OUTPUT vs PREROUTING），结论可能完全相反。

## 1. DNS 是否真的在应答

```sh
cat /etc/resolv.conf
cat /tmp/resolv.conf.d/resolv.conf.auto
nslookup baidu.com 127.0.0.1
nslookup google.com 127.0.0.1
nslookup -type=AAAA google.com 127.0.0.1
dig A google.com @127.0.0.1
```

判断标准：

- 两个域名都超时 → 本机 DNS 监听没起来（`ss -lntup | grep :53`）。
- `baidu.com` 正常、`google.com` SERVFAIL/超时 → 上游或代理 DNS 链路问题。
- 返回 `1.1.1.1`、`198.18.x.x` 之类的可疑地址 → 看看是不是 Fake-IP 与直连规则打架。

## 2. 节点能不能承载真实流量

UI 里的"延迟测试"经常只测 TCP 握手甚至只测 ICMP，不代表能代理。

```sh
curl -4 -I --connect-timeout 8 https://www.google.com/
curl -x socks5h://127.0.0.1:<port> -I https://www.google.com/
```

`socks5h`（远端解析域名）成功、`socks5` 失败，基本可以锁定 DNS 路径而不是节点。

## 3. 透明代理规则是否真的进了防火墙

```sh
nft list ruleset | grep -nE 'tproxy|redirect|mark'
ip rule show
ip route show table all | head -40
```

服务进程活着但 `nft list ruleset` 里没有对应链，就是"规则没加载"，
和 DNS 无关。这种情况重启服务通常能好，但要记录版本，因为这是回归 bug 的典型表现。

## 4. IPv6 是不是漏了

```sh
curl -4 -I https://www.google.com/
curl -6 -I https://www.google.com/
```

v4 通、v6 不通：说明 AAAA 解析和 IPv6 透明代理是两条独立路径。
不要用"关掉 IPv6"当修复，先确认 `ip -6 rule` 和 tproxy v6 链是否存在。

## 5. 客户端自己在绕过你

Chrome 的安全 DNS、Firefox DoH、Android Private DNS、iOS 加密 DNS 描述文件
都会跳过路由器 DNS。测试时先关掉，或者从客户端确认 `nslookup` 真的问到了路由器。

## 6. 用 network-doctor 收证据

```sh
sh /root/network-doctor.sh
```

它会把上面的检查跑一遍，输出 `DNS_LOCAL_FAILURE` / `NODE_FAILURE` /
`FIREWALL_RULE_MISSING` / `IPV6_PROXY_BYPASS` / `OOM_EVENT` 之类的分类，
并生成脱敏后的 tar.gz。把分类和日志一起看，比反复重启有效。

## 7. "跑一阵子就断"

优先查三件事，而不是加定时重启：

1. `dmesg | grep -i oom` —— 内存不够，核心被杀。
2. `logread | grep -i 'crashed\|restart'` —— 崩溃循环，重启只是掩盖。
3. `cat /proc/sys/net/netfilter/nf_conntrack_count` 对比 max —— 连接表打满。

procd 自带 respawn 阈值和重启间隔，用它的限流，不要写 `while true; do restart; done`。
