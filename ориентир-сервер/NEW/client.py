import asyncio
import struct
import subprocess
import hashlib
import time
import os
import sys
import ctypes
import socket
import json # Добавлено для конфига
from pytun_pmd3 import TunTapDevice
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# === НАСТРОЙКИ ПО УМОЛЧАНИЮ ===
CONFIG_FILE = "config.json"

# Глобальные переменные, которые заполнятся из конфига
SERVER_IP = '163.5.29.66'
SERVER_PORT = 65432
TUN_IP = '10.0.0.2'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1280
ADAPTER_NAME = "PyVPN"
PASSWORD = "SWaT_2008"

# Криптография (инициализируется позже, после загрузки пароля)
SHA224_HASH = None
cipher = None
# ------------------------

CMD_DATA = 0x00
CMD_PING = 0x01
CMD_PONG = 0x02

# === ФУНКЦИИ АВТОМАТИЗАЦИИ ===

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def get_default_gateway():
    try:
        result = subprocess.run("route print -4 0.0.0.0", capture_output=True, text=True, shell=True)
        lines = result.stdout.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith("0.0.0.0") and "0.0.0.0" in line:
                parts = line.split()
                if len(parts) >= 5:
                    gateway = parts[2]
                    interface_ip = parts[3]
                    if gateway != TUN_GW and interface_ip != TUN_IP:
                        return gateway
        return None
    except Exception as e:
        print(f"Ошибка при определении шлюза: {e}")
        return None

def load_config():
    global SERVER_IP, SERVER_PORT, TUN_IP, TUN_GW, NETMASK, MTU, ADAPTER_NAME, PASSWORD
    global SHA224_HASH, cipher

    if not os.path.exists(CONFIG_FILE):
        print(f"Файл {CONFIG_FILE} не найден. Создаю шаблон...")
        default_cfg = {
            "server_ip": "163.5.29.66",
            "server_port": 65432,
            "tun_ip": "10.0.0.2",
            "password": "SWaT_2008",
            "adapter_name": "PyVPN"
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_cfg, f, indent=4)
        print(f"✅ Создан файл {CONFIG_FILE}. Отредактируйте его и перезапустите программу.")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        
        SERVER_IP = cfg.get("server_ip", SERVER_IP)
        SERVER_PORT = cfg.get("server_port", SERVER_PORT)
        TUN_IP = cfg.get("tun_ip", TUN_IP)
        TUN_GW = TUN_IP[:-1] + '1' # Автоматически делаем шлюз (если IP 10.0.0.3, шлюз 10.0.0.1)
        PASSWORD = cfg.get("password", PASSWORD)
        ADAPTER_NAME = cfg.get("adapter_name", ADAPTER_NAME)
        MTU = cfg.get("mtu", MTU)

        # Инициализируем криптографию на основе загруженного пароля
        SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
        key_material = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'trojan-vpn-salt',
            iterations=100000,
        ).derive(PASSWORD.encode())
        cipher = ChaCha20Poly1305(key_material)
        
        print(f"✅ Конфигурация из {CONFIG_FILE} загружена.")
    except Exception as e:
        print(f"❌ Ошибка чтения {CONFIG_FILE}: {e}")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

# ============================

class VPNClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, tun_device):
        self.transport = None
        self.adapter = tun_device
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.last_tx = 0
        self.last_rx = 0
        self.last_time = time.time()

    def connection_made(self, transport):
        self.transport = transport
        print("UDP транспорт готов (Trojan + Anti-DPI).")

    def datagram_received(self, data, addr):
        if len(data) < 36: return
            
        try:
            nonce = data[:12]
            ciphertext = data[12:]
            plaintext = cipher.decrypt(nonce, ciphertext, None)
            
            recv_hash = plaintext[:56]
            recv_time_bytes = plaintext[56:64]
            cmd = plaintext[64]

            if recv_hash == SHA224_HASH:
                pkt_time = struct.unpack('!d', recv_time_bytes)[0]
                if abs(time.time() - pkt_time) > 30: return

                if cmd == CMD_DATA:
                    ip_packet = plaintext[65:]
                    self.adapter.write(ip_packet)
                    self.rx_bytes += len(ip_packet)
                elif cmd == CMD_PONG:
                    pass 
        except Exception:
            pass

    def error_received(self, exc):
        print(f"UDP ошибка: {exc}")

def setup_wintun():
    if not is_admin():
        print("❌ ОШИБКА: VPN требует прав администратора!")
        print("Запустите файл от имени Администратора.")
        sys.exit(1)

    print("Поиск шлюза по умолчанию...")
    local_gw = get_default_gateway()
    if not local_gw:
        print("❌ ОШИБКА: Не удалось определить IP вашего роутера.")
        sys.exit(1)
    print(f"✅ Автоматически найден шлюз: {local_gw}")

    print(f"Создание адаптера {ADAPTER_NAME}...")
    adapter = TunTapDevice(name=ADAPTER_NAME)
    adapter.mtu = MTU
    adapter.up()

    print(f"Назначение IP {TUN_IP} и шлюза {TUN_GW}...")
    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" static {TUN_IP} {NETMASK} {TUN_GW}', shell=True)
    subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True)
    subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)

    print("Ожидание применения настроек Windows (3 сек)...")
    time.sleep(3)

    print("Очистка старых маршрутов...")
    subprocess.run(f'route delete {SERVER_IP}', shell=True)
    subprocess.run(f'route delete 0.0.0.0', shell=True)

    print(f"Добавление исключения для VPN-сервера {SERVER_IP} через {local_gw}...")
    subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {local_gw} metric 5', shell=True)

    print("Принудительное перенаправление всего трафика через VPN...")
    subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {TUN_GW} metric 1', shell=True)

    print("✅ WinTun настроен. VPN активен.")
    return adapter

def format_speed(bps):
    mbps = bps * 8 / 1000000
    return f"{mbps:.2f} Mbps"

async def print_metrics(protocol):
    while True:
        await asyncio.sleep(2)
        now = time.time()
        elapsed = now - protocol.last_time
        if elapsed > 0:
            tx_speed = (protocol.tx_bytes - protocol.last_tx) / elapsed
            rx_speed = (protocol.rx_bytes - protocol.last_rx) / elapsed
            print(f"[Метрики] Отправка: {format_speed(tx_speed)} | Прием: {format_speed(rx_speed)}")
            protocol.last_tx = protocol.tx_bytes
            protocol.last_rx = protocol.rx_bytes
            protocol.last_time = now

async def send_ping(protocol):
    while True:
        await asyncio.sleep(15)
        try:
            current_time = struct.pack('!d', time.time())
            vip_bytes = socket.inet_aton(TUN_IP)
            ping_payload = SHA224_HASH + current_time + struct.pack('B', CMD_PING) + vip_bytes
            
            nonce = os.urandom(12)
            ciphertext = cipher.encrypt(nonce, ping_payload, None)
            frame = nonce + ciphertext
            
            protocol.transport.sendto(frame, (SERVER_IP, SERVER_PORT))
        except Exception:
            pass

async def read_from_wintun(adapter, transport, protocol):
    loop = asyncio.get_event_loop()
    while True:
        try:
            packet = await loop.run_in_executor(None, adapter.read)
            if packet:
                current_time = struct.pack('!d', time.time())
                trojan_payload = SHA224_HASH + current_time + struct.pack('B', CMD_DATA) + packet
                
                nonce = os.urandom(12)
                ciphertext = cipher.encrypt(nonce, trojan_payload, None)
                frame = nonce + ciphertext
                
                transport.sendto(frame, (SERVER_IP, SERVER_PORT))
                protocol.tx_bytes += len(packet)
        except Exception:
            pass

async def main():
    # 1. Загружаем конфигурацию
    load_config()

    # 2. Настраиваем сеть
    adapter = setup_wintun()
    
    # 3. Запускаем транспорт
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VPNClientProtocol(adapter),
        local_addr=('0.0.0.0', 0)
    )

    # 4. Запускаем фоновые задачи
    asyncio.create_task(read_from_wintun(adapter, transport, protocol))
    asyncio.create_task(print_metrics(protocol))
    asyncio.create_task(send_ping(protocol))

    await asyncio.Future()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановка клиента...")
    except Exception as e:
        print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
    finally:
        print("\nНажмите Enter, чтобы закрыть окно...")
        input()