#!/usr/bin/env bash
# One-off for existing Ubuntu VMs, run as root from the Vultr console after staging /opt/smb.
# Does not change SSH/firewalls: their CIDRs require an operator review on an existing VM.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run as root from the host console.' >&2; exit 1; }
[ -x /opt/smb/watchdog.sh ] && [ -f /opt/smb/watchdog.py ] || { echo 'Stage watchdog.sh and watchdog.py in /opt/smb first.' >&2; exit 1; }
install -d -m 0755 /etc/systemd/journald.conf.d
cat >/etc/systemd/journald.conf.d/60-smb.conf <<'CONF'
[Journal]
SystemMaxUse=256M
RuntimeMaxUse=64M
SystemKeepFree=1G
MaxRetentionSec=14day
CONF
cat >/etc/systemd/system/smb-watchdog.service <<'UNIT'
[Unit]
Description=SMB host watchdog
After=network-online.target docker.service
ConditionPathExists=/opt/smb/watchdog.sh

[Service]
Type=oneshot
ExecStart=/opt/smb/watchdog.sh
TimeoutStartSec=240
UMask=0077
UNIT
cat >/etc/systemd/system/smb-watchdog.timer <<'UNIT'
[Unit]
Description=Check SMB operations every five minutes

[Timer]
OnBootSec=5min
OnCalendar=*-*-* *:00/5:00
Persistent=true
AccuracySec=15s

[Install]
WantedBy=timers.target
UNIT
systemctl restart systemd-journald
systemctl enable --now systemd-timesyncd
# Synchronization may take a short time after enabling NTP.
timedatectl set-ntp true
for _ in $(seq 1 30); do
  [ "$(timedatectl show --property=NTPSynchronized --value)" = yes ] && break
  sleep 2
done
[ "$(timedatectl show --property=NTPSynchronized --value)" = yes ] || { echo 'Time is not synchronized; repair NTP before proceeding.' >&2; exit 1; }
systemctl daemon-reload
systemctl enable --now smb-watchdog.timer
