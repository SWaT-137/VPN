#!/bin/bash

# Скрипт автоматической установки Trojan-VPN сервера

echo "🚀 Начинаю установку VPN сервера..."

# 1. Обновление системы и установка зависимостей
echo "[1/7] Обновление пакетов и установка Python..."
apt update && apt upgrade -y
apt install python3 python3-venv python3-pip iptables-persistent -y

# 2. Создание рабочей директории
echo "[2/7] Создание рабочей директории /opt/VnP..."
mkdir -p /opt/VnP

# 3. Копирование файлов проекта (скрипт ожидает, что server.py и users.json лежат рядом)
echo "[3/7] Копирование файлов сервера..."
if [ -f "server.py" ]; then
    cp server.py /opt/VnP/
else
    echo "❌ ОШИБКА: Файл server.py не найден рядом с install.sh!"
    exit 1
fi

if [ -f "users.json" ]; then
    cp users.json /opt/VnP/
else
    echo "⚠️ Внимание: users.json не найден. Будет создан пустой шаблон."
    echo "{}" > /opt/VnP/users.json
fi

# 4. Создание и активация виртуального окружения Python
echo "[4/7] Настройка виртуального окружения Python..."
python3 -m venv /opt/VnP/venv
/opt/VnP/venv/bin/pip install --upgrade pip
/opt/VnP/venv/bin/pip install pytun cryptography

# 5. Настройка сетевого форвардинга (чтобы сервер мог пропускать трафик)
echo "[5/7] Настройка сетевого форвардинга (sysctl)..."
sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf
sysctl -p

# 6. Настройка NAT и Firewall (iptables)
echo "[6/7] Настройка NAT (iptables)..."
# Определяем основной сетевой интерфейс сервера автоматически
MAIN_IF=$(ip route | grep default | awk '{print $5}' | head -n 1)

if [ -z "$MAIN_IF" ]; then
    echo "❌ ОШИБКА: Не удалось определить сетевой интерфейс. NAT не настроен!"
else
    # Включаем маскарадинг (NAT)
    iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o $MAIN_IF -j MASQUERADE
    # Включаем исправление MTU (обязательно для скорости)
    iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    # Сохраняем правила навсегда
    netfilter-persistent save
    echo "✅ NAT настроен для интерфейса $MAIN_IF"
fi

# Увеличиваем буферы UDP (для скорости)
echo "net.core.rmem_max=8388608" >> /etc/sysctl.conf
echo "net.core.wmem_max=8388608" >> /etc/sysctl.conf
echo "net.core.rmem_default=8388608" >> /etc/sysctl.conf
echo "net.core.wmem_default=8388608" >> /etc/sysctl.conf
sysctl -p

# 7. Создание службы Systemd для автозапуска
echo "[7/7] Создание службы systemd..."
cat <<EOF > /etc/systemd/system/pyvpn.service
[Unit]
Description=Python Trojan VPN Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/VnP
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/VnP/venv/bin/python /opt/VnP/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# Перезапускаем демон и включаем автозапуск
systemctl daemon-reload
systemctl enable pyvpn
systemctl restart pyvpn

echo ""
echo "=================================================="
echo "✅ УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО!"
echo "VPN сервер запущен и добавлен в автозагрузку."
echo ""
echo "Полезные команды:"
echo "  Статус:   sudo systemctl status pyvpn"
echo "  Логи:     sudo journalctl -u pyvpn -f"
echo "  Перезапуск: sudo systemctl restart pyvpn"
echo "=================================================="
