#!/bin/sh
# 确认向导配置，取消 90 秒自动回滚。
set -u
uci -q set wizard.main.confirmed='1'
uci -q set wizard.main.done='1'
uci -q set wizard.main.applied='0'
uci -q commit wizard
rm -f /var/run/wizard-rollback.pid
echo "confirmed"
