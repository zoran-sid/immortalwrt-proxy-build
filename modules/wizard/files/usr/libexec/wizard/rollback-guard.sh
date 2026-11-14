#!/bin/sh
# 90 秒确认窗口：未确认则回滚。
sleep 90
[ "$(uci -q get wizard.main.confirmed 2>/dev/null)" = "1" ] && exit 0
logger -t wizard "not confirmed within 90s, rolling back"
/usr/libexec/wizard/rollback.sh
