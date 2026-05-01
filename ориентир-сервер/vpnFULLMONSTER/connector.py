#!/usr/bin/env python3
"""
XRAY-Reality VPN Manager (исправленный v2)
"""
import os
import sys
import json
import ctypes
import logging
import subprocess
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)-5s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

XRAY_CONFIG = {
    "server_ip": "222.167.211.76",
    "server_port": 443,
    "uuid": "f50b747e-7425-4a82-b21b-9a54995ac384",
    "public_key": "crqA0pa4xcMrI674wGGvqhv9S-cMKyM3NGs243Iw_QQ",
    "short_id": "a9b8f4a1",
    "sni": "www.microsoft.com",
    "fingerprint": "chrome"
}

TUN_NAME = "XrayVPN"
TUN_MTU = 1420
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def run_shell(cmd):
    """Выполнить cmd.exe команду, вернуть returncode. Текст не читаем — нет проблем с кодировкой."""
    return subprocess.run(cmd, shell=True, creationflags=NO_WINDOW).returncode


def route_print():
    """route print с правильной кодировкой CP866 (OEM)"""
    r = subprocess.run('route print 0.0.0.0', capture_output=True, shell=True, creationflags=NO_WINDOW)
    return r.stdout.decode('cp866', errors='replace')


def route_print_ip(ip):
    """route print IP с правильной кодировкой"""
    r = subprocess.run(f'route print {ip} *', capture_output=True, shell=True, creationflags=NO_WINDOW)
    return r.stdout.decode('cp866', errors='replace')


def ps_cmd(cmd):
    """PowerShell команда (UTF-8)"""
    r = subprocess.run(
        ['powershell', '-NoProfile', '-Command', cmd],
        capture_output=True, creationflags=NO_WINDOW)
    return r.stdout.decode('utf-8-sig', errors='replace').strip()


class XrayManager:
    def __init__(self):
        self.xray_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xray.exe")
        self.process = None
        self.log_file = None

    def generate_config(self):
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "tun-in",
                    "protocol": "tun",
                    "settings": {
                        "name": TUN_NAME,
                        "mtu": TUN_MTU,
                        "address": ["10.88.88.1/24"],
                        "gateway": "10.88.88.1",
                        "stack": "gvisor",
                        "sniffing": {
                            "enabled": True,
                            "destOverride": ["http", "tls", "quic"],
                            "routeOnly": True
                        }
                    }
                }
            ],
            "outbounds": [
                {
                    "tag": "proxy",
                    "protocol": "vless",
                    "settings": {
                        "vnext": [{
                            "address": XRAY_CONFIG["server_ip"],
                            "port": XRAY_CONFIG["server_port"],
                            "users": [{
                                "id": XRAY_CONFIG["uuid"],
                                "encryption": "none",
                                "flow": "xtls-rprx-vision"
                            }]
                        }]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "serverName": XRAY_CONFIG["sni"],
                            "fingerprint": XRAY_CONFIG["fingerprint"],
                            "publicKey": XRAY_CONFIG["public_key"],
                            "shortId": XRAY_CONFIG["short_id"]
                        }
                    }
                },
                {"tag": "direct", "protocol": "freedom", "settings": {}},
                {"tag": "block", "protocol": "blackhole", "settings": {"response": {"type": "none"}}}
            ],
            "dns": {
                "servers": [
                    {"address": "https://1.1.1.1/dns-query", "detour": "proxy"},
                    {"address": "https://8.8.8.8/dns-query", "detour": "proxy"}
                ],
                "queryStrategy": "UseIPv4"
            },
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {"type": "field", "ip": ["169.254.0.0/16", "224.0.0.0/4", "ff00::/8"], "outboundTag": "block"},
                    {"type": "field", "inboundTag": ["tun-in"], "port": "53", "outboundTag": "proxy"},
                    {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"}
                ]
            }
        }
        with open("xray_config.json", 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

    def start(self):
        self.log_file = open("xray_console.log", "w", encoding='utf-8')
        self.process = subprocess.Popen(
            [self.xray_path, "run", "-c", "xray_config.json"],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW
        )
        logger.info(f"🚀 Xray запущен (PID: {self.process.pid})")

    def stop(self):
        if self.log_file:
            self.log_file.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class RoutingManager:
    @staticmethod
    def get_tun_index():
        idx = ps_cmd(f'Get-NetAdapter -Name "{TUN_NAME}" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ifIndex')
        return int(idx) if idx.isdigit() else None

    @staticmethod
    def get_main_adapter_info():
        """Возвращает (gateway_ip, main_interface_index)"""
        text = route_print()
        for line in text.split('\n'):
            parts = line.split()
            if len(parts) >= 5 and parts[0] == '0.0.0.0' and parts[1] == '0.0.0.0':
                gateway = parts[2]
                interface_ip = parts[3]
                idx = ps_cmd(
                    f'(Get-NetIPAddress -AddressFamily IPv4 -IPAddress "{interface_ip}" '
                    f'-ErrorAction SilentlyContinue).InterfaceIndex')
                if idx.isdigit():
                    return gateway, int(idx)
        return None, None

    @staticmethod
    def add_routes():
        server_ip = XRAY_CONFIG["server_ip"]

        # Ждём появления адаптера (до 15 секунд)
        tun_index = None
        for i in range(15):
            tun_index = RoutingManager.get_tun_index()
            if tun_index:
                break
            time.sleep(1)
            logger.info(f"⏳ Ожидание адаптера {TUN_NAME}... ({i+1}/15)")

        if not tun_index:
            logger.error(f"❌ Адаптер {TUN_NAME} так и не появился!")
            return False
        logger.info(f"📌 TUN-адаптер индекс: {tun_index}")

        gateway, main_if = RoutingManager.get_main_adapter_info()
        if not gateway or not main_if:
            logger.error("❌ Не удалось определить основной сетевой интерфейс!")
            return False
        logger.info(f"📌 Физический интерфейс: индекс={main_if}, шлюз={gateway}")

        # Удаляем старые split routes
        run_shell('route delete 0.0.0.0 mask 128.0.0.0')
        run_shell('route delete 128.0.0.0 mask 128.0.0.0')

        # ╔══════════════════════════════════════════════════════════════╗
        # ║  Маршрут к серверу VPN — ЧЕРЕЗ ФИЗИЧЕСКИЙ интерфейс        ║
        # ║  Проверяем по returncode, а не по тексту (костыль с OEM)    ║
        # ╚══════════════════════════════════════════════════════════════╝
        rc = run_shell(f'route add {server_ip} mask 255.255.255.255 {gateway} IF {main_if} metric 1')
        if rc == 0:
            logger.info(f"✅ Маршрут к {server_ip} через IF {main_if} — OK")
        else:
            logger.error(f"❌ route add вернул код {rc} — не удалось добавить маршрут к серверу")
            return False

        # Split routes через туннель
        rc1 = run_shell(f'route add 0.0.0.0 mask 128.0.0.0 0.0.0.0 IF {tun_index} metric 5')
        rc2 = run_shell(f'route add 128.0.0.0 mask 128.0.0.0 0.0.0.0 IF {tun_index} metric 5')
        if rc1 == 0 and rc2 == 0:
            logger.info(f"✅ Split routes через IF {tun_index} — OK")
        else:
            logger.error(f"❌ Split routes не добавились (коды: {rc1}, {rc2})")
            return False

        # Отключаем IPv6 на туннеле (чтобы DNS не уходил в fe80::)
        run_shell(f'netsh interface ipv6 set interface "{TUN_NAME}" disable')
        logger.info("📌 IPv6 на туннеле отключен")

        # Финальная проверка — маршрут к серверу НЕ через туннель
        time.sleep(1)
        check_text = route_print_ip(server_ip)
        bad = False
        for line in check_text.split('\n'):
            line = line.strip()
            if server_ip in line and line:
                logger.info(f"🔍 {line}")
                if str(tun_index) in line:
                    bad = True
        if bad:
            logger.error("⚠️  ВНИМАНИЕ: маршрут к серверу идёт через ТУННЕЛЬ!")

        logger.info("✅ Все маршруты настроены")
        return True

    @staticmethod
    def remove_routes():
        run_shell('route delete 0.0.0.0 mask 128.0.0.0')
        run_shell('route delete 128.0.0.0 mask 128.0.0.0')
        run_shell(f'route delete {XRAY_CONFIG["server_ip"]} mask 255.255.255.255')
        run_shell(f'netsh interface ipv6 set interface "{TUN_NAME}" enable')


def check_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def main():
    if not check_admin():
        logger.error("❌ ЗАПУСТИТЕ ОТ ИМЕНИ АДМИНИСТРАТОРА!")
        sys.exit(1)

    if not os.path.exists("xray.exe") or not os.path.exists("wintun.dll"):
        logger.error("❌ Не найден xray.exe или wintun.dll в папке со скриптом!")
        sys.exit(1)

    xray = XrayManager()

    try:
        logger.info("🛠️  Запуск VPN...")
        xray.generate_config()
        xray.start()

        if not RoutingManager.add_routes():
            logger.error("❌ Не удалось настроить маршруты.")
            sys.exit(1)

        logger.info("=" * 50)
        logger.info("🎉 VPN ЗАПУЩЕН!")
        logger.info("💡 Ctrl+C для остановки.")
        logger.info("=" * 50)

        while True:
            if xray.process.poll() is not None:
                logger.error("❌ Xray упал! Смотрите xray_console.log")
                break
            time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        logger.info("\n⏳ Выключение...")
        RoutingManager.remove_routes()
        xray.stop()
        logger.info("✅ Готово.")


if __name__ == "__main__":
    main()