#!/bin/bash
#
# start_hotspot.sh — перевод Wi-Fi адаптера ноутбука в режим точки
# доступа (hostapd + dnsmasq) для раздачи Wi-Fi студентам.
#
# SSID: aaf-note
# Пароль: A-A-F_n_o_t_e
#
# Запускается ярлыком "Wi-Fi Hotspot" с рабочего стола через pkexec.
# Обратный переход — через wifi_adapter.sh ("Wi-Fi Adapter").
#
# Скрипт сам определяет путь к проекту по своему расположению, поэтому
# не зависит от того, чьи переменные окружения видны после pkexec.

set -euo pipefail

IFACE="wlp3s0"
AP_IP="192.168.50.1/24"
SSID="aaf-note"
PASSPHRASE="A-A-F_n_o_t_e"
PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
HOSTAPD_CONF="$PROJECT_DIR/deploy/hostapd.conf"
DNSMASQ_CONF="$PROJECT_DIR/deploy/dnsmasq-hotspot.conf"

notify() {
    # Уведомления от root-процесса до пользовательской сессии обычно не
    # доходят без доп. настройки DBUS_SESSION_BUS_ADDRESS, поэтому здесь
    # только текстовый вывод — визуальный статус проще смотреть по
    # результату подключения телефона к сети.
    echo "$1"
}

if [ "$EUID" -ne 0 ]; then
    echo "Этот скрипт должен запускаться с правами root (через pkexec или sudo)."
    exit 1
fi

notify "Переключаю $IFACE в режим точки доступа…"

if command -v nmcli >/dev/null 2>&1; then
    nmcli device set "$IFACE" managed no 2>/dev/null || true
fi

systemctl stop wpa_supplicant 2>/dev/null || true
systemctl stop hostapd dnsmasq 2>/dev/null || true
pkill -f "hostapd -B" 2>/dev/null || true
pkill -f "dnsmasq -C $DNSMASQ_CONF" 2>/dev/null || true

ip link set "$IFACE" down
ip addr flush dev "$IFACE"
ip addr add "$AP_IP" dev "$IFACE"
ip link set "$IFACE" up

mkdir -p "$(dirname "$HOSTAPD_CONF")"
cat > "$HOSTAPD_CONF" << EOF
interface=${IFACE}
driver=nl80211
ssid=${SSID}
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_passphrase=${PASSPHRASE}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
macaddr_acl=0
ignore_broadcast_ssid=0
max_num_sta=30
EOF

cat > "$DNSMASQ_CONF" << EOF
interface=${IFACE}
bind-interfaces
dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,12h
dhcp-option=3,192.168.50.1
dhcp-option=6,192.168.50.1
address=/control-test.local/192.168.50.1
EOF

hostapd -B "$HOSTAPD_CONF" -P /run/hostapd.pid
dnsmasq -C "$DNSMASQ_CONF" -x /run/dnsmasq-hotspot.pid

notify "Готово. SSID: ${SSID}, пароль: ${PASSPHRASE}"
notify "IP ноутбука в сети точки доступа: 192.168.50.1"
notify "Веб-сервис (если запущен): http://192.168.50.1:8000/teacher"
