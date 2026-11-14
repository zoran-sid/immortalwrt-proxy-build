#!/bin/sh
# 90 秒确认窗口：未确认则回滚。
sleep 90
if [ "$(uci -q get wizard.main.confirmed 2>/dev/null)" = "1" ]; then
	rm -f /var/run/wizard-rollback.pid
	exit 0
fi
logger -t wizard "not confirmed within 90s, rolling back"
/usr/libexec/wizard/rollback.sh
rm -f /var/run/wizard-rollback.pid
