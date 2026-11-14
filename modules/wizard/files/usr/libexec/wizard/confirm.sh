#!/bin/sh
set -u
uci -q set wizard.main.confirmed='1'
uci -q set wizard.main.done='1'
uci -q commit wizard
echo "confirmed"
