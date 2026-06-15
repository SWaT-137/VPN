Вот полный список всех команд, которые мы использовали при создании проекта. Я разделил их на логические блоки: команды, которые вводил ты (или скрипт установки), и команды, которые программа выполняет автоматически под капотом.

---

### 🪟 ЧАСТЬ 1: КЛИЕНТ (Windows)

Это команды, которые использовались для сборки проекта и управления им, а также команды ОС, которые наш Python-скрипт выполняет в фоновом режиме.

#### 1.1. Установка библиотек (Терминал PowerShell/CMD)
*   `pip install pytun-pmd3 cryptography pystray Pillow` — Установка основных библиотек (TUN-адаптер, шифрование, трей, отрисовка иконки).
*   `pip install pyinstaller` — Установка упаковщика в `.exe`.

#### 1.2. Сборка .exe (Терминал PowerShell/CMD)
*   **Для PyVPN:**
    ```powershell
    python -m PyInstaller --noconsole --onefile --hidden-import=pystray._win32 --hidden-import=win32api --hidden-import=win32gui --add-binary "C:\ПУТЬ_ДО\wintun.dll;pytun_pmd3/wintun/bin/amd64" --name PyVPN client.py
    ```
---

### 🐧 ЧАСТЬ 1.3: СЕРВЕР (Ubuntu Linux)

Команды для подготовки сервера, настройки ядра, файрвола и управления службами.

#### 2.1. Обновление и базовые пакеты (Терминал SSH)
*   `sudo apt update && sudo apt upgrade -y` — Обновление системы.
*   `sudo apt install python3 python3-venv python3-pip iptables-persistent -y` — Установка Python и утилит для сохранения правил файрвола.

#### 2.2. Настройка Python-окружения (Терминал SSH)
*   `python3 -m venv venv` — Создание виртуального окружения.
*   `source venv/bin/activate` — Активация окружения (для ручного запуска).
*   `pip install pytun cryptography` — Установка библиотек для первого VPN.
*   `pip install python-pytun cryptography` — Установка библиотек для LAN-VPN (форк pytun для Python 3).

#### 2.3. Настройка сети и ядра (Выполнялись вручную или через install.sh)
*   **Разрешение пересылки пакетов (NAT):**
    *   `sudo sysctl -w net.ipv4.ip_forward=1` — Включение форвардинга.
    *   `sudo sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf` — Сохранение после перезагрузки.
    *   `sudo sysctl -p` — Применение настроек sysctl.
*   **Настройка NAT (только для PyVPN, не для LAN!):**
    *   `sudo iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE` — Подмена IP для выхода в интернет.
    *   `sudo iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu` — Фикс фрагментации пакетов (важно для скорости).
    *   `sudo netfilter-persistent save` — Сохранение правил iptables навсегда.
*   **Увеличение буферов UDP (Для скорости):**
    *   `sudo sysctl -w net.core.rmem_max=8388608`
    *   `sudo sysctl -w net.core.wmem_max=8388608`
    *   `sudo sysctl -w net.core.rmem_default=8388608`
    *   `sudo sysctl -w net.core.wmem_default=8388608`
*   **Узнать интерфейс:**
    *   `ip a` или `ip route` — Узнать имя сетевого интерфейса (eth0, ens3 и т.д.) для NAT.

#### 2.4. Управление службами Systemd (Терминал SSH)
*   `sudo nano /etc/systemd/system/pyvpn.service` — Создание файла службы интернет-VPN.
*   `sudo nano /etc/systemd/system/pylan.service` — Создание файла службы LAN-VPN.
*   `sudo systemctl daemon-reload` — Обновление конфигурации systemd.
*   `sudo systemctl enable pyvpn` / `pylan` — Добавление в автозагрузку.
*   `sudo systemctl start pyvpn` / `pylan` — Запуск.
*   `sudo systemctl restart pyvpn` / `pylan` — Перезапуск (после изменения кода).
*   `sudo systemctl status pyvpn` / `pylan` — Проверка статуса (работает/упал).

#### 2.5. Логи и отладка (Терминал SSH)
*   `sudo journalctl -u pyvpn -f` — Просмотр логов интернет-VPN в реальном времени.
*   `sudo journalctl -u pylan -f` — Просмотр логов LAN-VPN.

#### 2.6. Работа с файлами (Терминал SSH)
*   `sudo mkdir /opt/VnP` / `/opt/Geo_net` — Создание папок.
*   `sudo cp ...` — Копирование файлов.
*   `sudo nano ...` — Редактирование файлов.

---

### 📜 ЧАСТЬ 3: Скрипт автоматизации (`install.sh`)

Этот скрипт объединяет большинство команд из Части 1.3 в один файл. Запускается одной командой `bash install.sh` и делает всё сам: обновляет пакеты, создает папки, настраивает venv, качает библиотеки, настраивает sysctl, iptables и systemd.