#!/bin/bash
#
# wifi_adapter.sh — возврат Wi-Fi адаптера ноутбука в обычный
# клиентский режим (после работы в режиме точки доступа).
#
# Запускается ярлыком "Wi-Fi Adapter" с рабочего стола через pkexec.

set -euo pipefail

IFACE="wlp3s0"

notify() {
    echo "$1"
}

if [ "$EUID" -ne 0 ]; then
    echo "Этот скрипт должен запускаться с правами root (через pkexec или sudo)."
    exit 1
fi

notify "Возвращаю $IFACE в клиентский режим…"

if [ -f /run/hostapd.pid ]; then
    kill "$(cat /run/hostapd.pid)" 2>/dev/null || true
    rm -f /run/hostapd.pid
fi
pkill -f "hostapd -B" 2>/dev/null || true

if [ -f /run/dnsmasq-hotspot.pid ]; then
    kill "$(cat /run/dnsmasq-hotspot.pid)" 2>/dev/null || true
    rm -f /run/dnsmasq-hotspot.pid
fi
pkill -f "dnsmasq -C.*dnsmasq-hotspot.conf" 2>/dev/null || true

systemctl stop hostapd dnsmasq 2>/dev/null || true

ip addr flush dev "$IFACE"
ip link set "$IFACE" down

if command -v nmcli >/dev/null 2>&1; then
    nmcli device set "$IFACE" managed yes 2>/dev/null || true
    sleep 1
    nmcli device connect "$IFACE" 2>/dev/null || true
fi

systemctl start wpa_supplicant 2>/dev/null || true
sleep 1
ip link set "$IFACE" up 2>/dev/null || true

notify "$IFACE снова в режиме клиента Wi-Fi."
notify "Проверьте подключение командой: nmcli device status"
