import asyncio
import struct
import subprocess
import hashlib
import time
import os
import sys
import ctypes
import socket
from pytun_pmd3 import TunTapDevice
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Настройки
SERVER_IP = '163.5.29.66'
SERVER_PORT = 65432
TUN_IP = '10.0.0.2'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1280
ADAPTER_NAME = "PyVPN"

# --- НАСТРОЙКИ TROJAN ---
PASSWORD = "SWaT_2008"
SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()

key_material = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=b'trojan-vpn-salt',
    iterations=100000,
).derive(PASSWORD.encode())
cipher = ChaCha20Poly1305(key_material)
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
    """Автоматически определяет IP реального роутера (шлюза) в Windows"""
    try:
        # Запрашиваем таблицу маршрутизации
        result = subprocess.run("route print -4 0.0.0.0", capture_output=True, text=True, shell=True)
        lines = result.stdout.split('\n')
        
        for line in lines:
            line = line.strip()
            # Ищем строку базового маршрута 0.0.0.0
            if line.startswith("0.0.0.0") and "0.0.0.0" in line:
                parts = line.split()
                if len(parts) >= 5:
                    gateway = parts[2]
                    interface_ip = parts[3]
                    # Убеждаемся, что это не наш VPN-шлюз и не интерфейс VPN
                    if gateway != TUN_GW and interface_ip != TUN_IP:
                        return gateway
        return None
    except Exception as e:
        print(f"Ошибка при определении шлюза: {e}")
        return None

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
    # 1. Проверка прав администратора
    if not is_admin():
        print("❌ ОШИБКА: VPN требует прав администратора для изменения маршрутов!")
        print("Пожалуйста, запусти файл от имени Администратора (Правая кнопка мыши -> Запуск от имени администратора).")
        sys.exit(1)

    # 2. Автоопределение шлюза
    print("Поиск шлюза по умолчанию...")
    local_gw = get_default_gateway()
    if not local_gw:
        print("❌ ОШИБКА: Не удалось автоматически определить IP вашего роутера (Wi-Fi/ETH).")
        print("Убедитесь, что у вас работает обычный интернет, и перезапустите программу.")
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

    # Используем найденный local_gw вместо хардкода
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
    adapter = setup_wintun()
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VPNClientProtocol(adapter),
        local_addr=('0.0.0.0', 0)
    )

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
        # Ловим любую другую ошибку и выводим её
        print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
    finally:
        # Эта строка не даст окну закрыться, пока ты не нажмешь Enter
        print("\nНажмите Enter, чтобы закрыть окно...")
        input()