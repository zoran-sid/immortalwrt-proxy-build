#!/bin/sh
# 恢复到最近一次向导备份。只动 /etc/config。
set -u
LATEST="$(ls -1t /tmp/wizard-backup-*.tar.gz 2>/dev/null | head -n1)"
[ -n "$LATEST" ] || { logger -t wizard "rollback: no backup found"; exit 1; }
logger -t wizard "rollback from $LATEST"
tar -xzf "$LATEST" -C / 2>/dev/null || exit 1
uci -q set wizard.main.confirmed='1'
uci -q set wizard.main.applied='0'
uci -q commit wizard
reload_config
logger -t wizard "rollback done"
exit 0
