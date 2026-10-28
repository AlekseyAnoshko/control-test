#!/bin/bash
# Install only our table atomically. Keep the last policy on service stop/failure.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
[[ $EUID -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
exec 9>/run/control-test-firewall.lock
flock -x 9
config=/etc/control-test/gateway.nft
test -r "$config"
transaction=$(mktemp /run/control-test-nft.XXXXXX)
trap 'rm -f "$transaction"' EXIT
if nft list table inet control_test >/dev/null 2>&1; then
    echo 'delete table inet control_test' > "$transaction"
fi
cat "$config" >> "$transaction"
nft --check --file "$transaction"
nft --file "$transaction"
# Enable routing only after installing ACL. IPv6 is denied on the AP by ACL.
sysctl -w net.ipv4.ip_forward=1
