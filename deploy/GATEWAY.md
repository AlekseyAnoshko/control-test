# Шлюз между двумя Wi-Fi адаптерами

Цель: сохранить обычный веб-доступ в Интернет, закрыть известные AI-домены
и предоставить локальный тест. Строгого режима без Интернета нет.

Встроенный `wlp3s0` — WAN-клиент внешнего Wi-Fi. USB `wlx00127b21755b` —
AP студентов, `10.10.10.1/24`. Это маршрутизатор с NAT, не сетевой мост.
Имена интерфейсов, адреса, DNS и порт задаются в `gateway.json`.

## Что фильтруется

- nftables перенаправляет обычный TCP/UDP DNS студентов на локальный dnsmasq.
- dnsmasq возвращает NXDOMAIN для доменов из `blocked-ai-domains.txt` и их
  поддоменов, для всех типов DNS-записей. Общие домены `google.com`,
  `microsoft.com`, `yandex.ru` целиком не блокируются.
- Из AP через WAN разрешён обычный IPv4-трафик и ответы на соединения.
  Внешний TCP/UDP 853 (DoT/DoQ) запрещён, IPv6 через AP закрыт.
  QUIC и другие протоколы Интернета не ограничиваются по номерам портов.
- Закрыт доступ студентов к частным и специальным IPv4-сетям за WAN.
  Доступ к обычным публичным сайтам сохраняется.
- На ноутбуке со стороны AP доступны DHCP, DNS, выбранный веб-порт и ping.
  Остальные интерфейсы и чужие nftables-таблицы этот набор правил не изменяет.
  Другие установленные файрволы могут дополнительно запрещать трафик.

Это доменная блокировка, а не распознавание нейросетей внутри HTTPS.
DoH, VPN/прокси, заранее сохранённые IP и новые домены могут
обойти список. AI внутри поисковиков также не отделяется от обычного поиска.
Сотовую связь и автоматическое переключение телефона сервер не контролирует.
Работа обычных сайтов и проверок доступности Интернета снижает вероятность
переключения, но не гарантирует её отсутствие. Проверьте реальные телефоны.

## Подготовка

Целевая система: Linux с NetworkManager, nftables с поддержкой inet NAT
(ядро 5.2+), dnsmasq, Python 3.10+, systemd и util-linux (flock).
Используйте существующий профиль `control-test-hotspot` в режиме AP с WPA2.
Ниже имена и адреса из проверенной конфигурации README; при изменении JSON
замените их и в командах NetworkManager и Uvicorn.

Не запускайте старые hostapd-скрипты. Не останавливайте глобально
`wpa_supplicant`: он может обслуживать WAN. Один DHCP/DNS-сервер должен
обслуживать AP; режим NetworkManager `shared` здесь не используется.

Из корня репозитория, без root:

```bash
cp deploy/gateway.json.example gateway.json
# При необходимости отредактируйте gateway.json и список AI-доменов.
python3 deploy/gateway.py --config gateway.json --output build/gateway
python3 -m unittest discover -s tests -v
```

Проверьте интерфейсы (`ip -br address`, `nmcli device status`), AP-поддержку
USB (`iw list`), WAN-маршрут и отсутствие пересечения внешней сети с
`10.10.10.0/24`. Подготовленные файлы можно просмотреть до применения.

## Применение на ноутбуке

Выполняйте с локального терминала: студенты временно отключатся.
Установите пакеты `nftables dnsmasq-base` средствами вашей ОС, если отсутствуют.
Сначала сохраните действующие конфигурации и правила для отката.
Не используйте `nft flush ruleset`.

```bash
sudo mkdir -p /etc/control-test
sudo nft list ruleset | sudo tee /etc/control-test/rules-before.txt >/dev/null
sudo install -m 644 build/gateway/gateway.nft /etc/control-test/gateway.nft
sudo install -m 644 build/gateway/dnsmasq.conf /etc/control-test/dnsmasq.conf
sudo install -m 755 deploy/firewall.sh /etc/control-test/firewall.sh
sudo install -m 644 deploy/control-test-firewall.service /etc/systemd/system/
sudo install -m 644 deploy/control-test-dnsmasq.service /etc/systemd/system/
sudo /usr/sbin/dnsmasq --test --conf-file=/etc/control-test/dnsmasq.conf
sudo systemctl daemon-reload
sudo systemctl enable --now control-test-firewall.service
sudo systemctl disable --now dnsmasq.service
sudo systemctl stop control-test-dnsmasq.service
sudo nmcli connection modify control-test-hotspot \
  connection.interface-name wlx00127b21755b \
  connection.autoconnect yes ipv4.method manual \
  ipv4.addresses 10.10.10.1/24 ipv4.gateway "" ipv4.dns "" \
  ipv4.never-default yes ipv6.method disabled \
  802-11-wireless.ap-isolation yes
sudo nmcli connection down control-test-hotspot
sudo nmcli connection up control-test-hotspot ifname wlx00127b21755b
sudo systemctl enable --now control-test-dnsmasq.service
```

Если `ap-isolation` не поддержан установленным NetworkManager/драйвером,
остановитесь и проверьте изоляцию клиентов отдельно: трафик между студентами
на одном AP может проходить мимо маршрутизируемого ACL.
Отключение системного dnsmasq допустимо только если он не нужен другим сетям;
конфликтующие DHCP/DNS-службы сначала разберите по инструкции README.

`firewall.sh` проверяет и заменяет только таблицу `inet control_test` одной
транзакцией, затем включает IPv4 forwarding. При ошибке проверки старые правила
сохраняются. Остановка firewall-unit намеренно не удаляет ACL.
Не включайте сторонний flow offload для AP: он может обходить обычный forward.
Не делайте глобальную перезагрузку nftables с очисткой таблиц после запуска.

Запустите веб-сервер по инструкции README на `10.10.10.1:8000`, один worker.
При использовании Nginx задайте `web_port: 80` в JSON и заново сгенерируйте
правила. Веб-сервер не следует привязывать к WAN-адресу.

## Приёмка с устройства студента

1. Получен адрес `10.10.10.100–250`, шлюз/DNS `10.10.10.1`.
2. Открываются `http://10.10.10.1:8000/` и обычные внешние HTTPS-сайты.
3. `nslookup chatgpt.com 10.10.10.1` возвращает NXDOMAIN;
   `nslookup chatgpt.com 8.8.8.8` также блокируется перенаправлением DNS.
   Проверьте и поддомен, например `api.openai.com`.
4. Проверьте ChatGPT, Claude, Gemini, DeepSeek из браузера после очистки DNS
   и закрытия старых соединений. Отдельно проверьте включённый DoH/Private DNS:
   DoH не гарантированно блокируется, DoT может сделать DNS телефона недоступным.
5. Убедитесь, что IPv6, прямой внешний DNS/853 и доступ к WAN-роутеру закрыты;
   счётчики: `sudo nft list table inet control_test`.
6. Проведите полный тест, затем повторите после перезагрузки ноутбука,
   переподключения USB и смены внешнего Wi-Fi. Проверьте автоматическое
   переключение на мобильную связь на используемых моделях телефонов.

Проверка из самого ноутбука не подтверждает ACL: нужен клиент за AP.
Автоматические тесты генератора не заменяют эту приёмку на оборудовании.

## Обновление списка и откат

Добавляйте по одному DNS-домену на строку, без URL/путей/`*`.
Повторите генерацию, установку двух файлов и проверки выше, затем:

```bash
sudo systemctl reload control-test-firewall.service
sudo systemctl restart control-test-dnsmasq.service
```

Для отката сначала отключите профиль AP, чтобы не открыть нефильтрованный
доступ, затем остановите/отключите обе новые службы, восстановите сохранённую
конфигурацию DNS/NM и удалите только таблицу проекта:

```bash
sudo nmcli connection down control-test-hotspot
sudo systemctl disable --now control-test-dnsmasq.service control-test-firewall.service
sudo nft delete table inet control_test
```

IPv4 forwarding не сбрасывается автоматически: он может использоваться другими
сетями. Восстановите предыдущее значение вручную. Не поднимайте AP без ACL.

## Что ещё отделяет проект от законченной экзаменационной системы

В веб-приложении пока нет защиты прав преподавателя, сохранения в БД и
полноценного восстановления сессии студента. Эти ограничения остаются:
ACL не запрещает студенту обращаться к открытым административным HTTP/WS
маршрутам на разрешённом веб-порту. Не используйте для значимых экзаменов
до исправления этих частей и аппаратной приёмки.
