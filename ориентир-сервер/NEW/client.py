import asyncio
import struct
import subprocess
import hashlib
import time
import os
import sys
import ctypes
import socket
import json
from pytun_pmd3 import TunTapDevice
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Настройки по умолчанию
CONFIG_FILE = "config.json"
SERVER_IP = '163.5.29.66'
SERVER_PORT = 65432
NETMASK = '255.255.255.0'
MTU = 1280
ADAPTER_NAME = "PyVPN"
PASSWORD = "SWaT_2008"

SHA224_HASH = None
cipher = None

CMD_DATA = 0x00
CMD_PING = 0x01
CMD_PONG = 0x02
CMD_IP_REQ = 0x03
CMD_IP_ACK = 0x04

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
                    if not gateway.startswith("10.0.0") and not interface_ip.startswith("10.0.0"):
                        return gateway
        return None
    except Exception as e:
        print(f"Ошибка при определении шлюза: {e}")
        return None

def load_config():
    global SERVER_IP, SERVER_PORT, ADAPTER_NAME, PASSWORD, SHA224_HASH, cipher

    if not os.path.exists(CONFIG_FILE):
        print(f"Файл {CONFIG_FILE} не найден. Создаю шаблон...")
        default_cfg = {
            "server_ip": "163.5.29.66",
            "server_port": 65432,
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
        PASSWORD = cfg.get("password", PASSWORD)
        ADAPTER_NAME = cfg.get("adapter_name", ADAPTER_NAME)

        SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
        key_material = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'trojan-vpn-salt',
            iterations=100000,
        ).derive(PASSWORD.encode())
        cipher = ChaCha20Poly1305(key_material)
        
        print(f"✅ Конфигурация загружена. Сервер: {SERVER_IP}:{SERVER_PORT}")
    except Exception as e:
        print(f"❌ Ошибка чтения {CONFIG_FILE}: {e}")
        sys.exit(1)

class VPNClientProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.adapter = None
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.last_tx = 0
        self.last_rx = 0
        self.last_time = time.time()
        
        # Для рукопожатия
        self.ip_received = asyncio.Event()
        self.tun_ip = None
        self.tun_gw = None

    def connection_made(self, transport):
        self.transport = transport
        print("UDP транспорт готов. Запрос IP-адреса у сервера...")
        self.send_ip_request()

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

                if cmd == CMD_IP_ACK:
                    # Сервер выдал IP!
                    self.tun_ip = socket.inet_ntoa(plaintext[65:69])
                    self.tun_gw = socket.inet_ntoa(plaintext[69:73])
                    print(f"✅ Получен IP от сервера: {self.tun_ip} (Шлюз: {self.tun_gw})")
                    self.ip_received.set() # Разрешаем настройку адаптера

                elif cmd == CMD_DATA:
                    if not self.adapter: return
                    ip_packet = plaintext[65:]
                    self.adapter.write(ip_packet)
                    self.rx_bytes += len(ip_packet)
                
                elif cmd == CMD_PONG:
                    pass 
                    
        except Exception:
            pass

    def send_ip_request(self):
        current_time = struct.pack('!d', time.time())
        req_payload = SHA224_HASH + current_time + struct.pack('B', CMD_IP_REQ)
        nonce = os.urandom(12)
        ciphertext = cipher.encrypt(nonce, req_payload, None)
        self.transport.sendto(nonce + ciphertext, (SERVER_IP, SERVER_PORT))

    def error_received(self, exc):
        print(f"UDP ошибка: {exc}")

def setup_wintun(tun_ip, tun_gw):
    if not is_admin():
        print("❌ ОШИБКА: VPN требует прав администратора!")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

    print("Поиск шлюза по умолчанию...")
    local_gw = get_default_gateway()
    if not local_gw:
        print("❌ ОШИБКА: Не удалось определить IP вашего роутера.")
        sys.exit(1)
    print(f"✅ Локальный шлюз: {local_gw}")

    print(f"Создание адаптера {ADAPTER_NAME}...")
    adapter = TunTapDevice(name=ADAPTER_NAME)
    adapter.mtu = MTU
    adapter.up()

    print(f"Назначение IP {tun_ip} и шлюза {tun_gw}...")
    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" static {tun_ip} {NETMASK} {tun_gw}', shell=True)
    subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True)
    subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)

    print("Ожидание применения настроек Windows (3 сек)...")
    time.sleep(3)

    print("Очистка старых маршрутов...")
    subprocess.run(f'route delete {SERVER_IP}', shell=True)
    subprocess.run(f'route delete 0.0.0.0', shell=True)

    print(f"Добавление исключения для VPN-сервера {SERVER_IP} через {local_gw}...")
    subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {local_gw} metric 5', shell=True)

    print("Перенаправление трафика через VPN...")
    subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {tun_gw} metric 1', shell=True)

    print("✅ VPN Активен!")
    return adapter

def format_speed(bps):
    mbps = bps * 8 / 1000000
    return f"{mbps:.2f} Mbps"

async def print_metrics(protocol):
    while True:
        await asyncio.sleep(2)
        if not protocol.adapter: continue
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
        if not protocol.tun_ip: continue
        try:
            current_time = struct.pack('!d', time.time())
            vip_bytes = socket.inet_aton(protocol.tun_ip)
            ping_payload = SHA224_HASH + current_time + struct.pack('B', CMD_PING) + vip_bytes
            
            nonce = os.urandom(12)
            ciphertext = cipher.encrypt(nonce, ping_payload, None)
            frame = nonce + ciphertext
            
            protocol.transport.sendto(frame, (SERVER_IP, SERVER_PORT))
        except Exception:
            pass

async def ip_request_loop(protocol):
    # Если пакет с IP потеряется, запрашиваем повторно каждые 3 секунды
    while not protocol.tun_ip:
        await asyncio.sleep(3)
        if not protocol.tun_ip:
            print("Повторный запрос IP...")
            protocol.send_ip_request()

async def read_from_wintun(adapter, transport, protocol):
    loop = asyncio.get_event_loop()
    while True:
        try:
            # Изменили MTU на 65535 для буфера чтения WinTUN
            packet = await loop.run_in_executor(None, adapter.read, 65535) 
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
    load_config()

    loop = asyncio.get_running_loop()
    
    # Создаем кастомный UDP сокет с БОЛЬШИМИ буферами (8 МБ)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024 * 8) # 8 MB на прием
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024 * 8) # 8 MB на отправку
    sock.bind(('0.0.0.0', 0)) # Случайный исходящий порт
    
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VPNClientProtocol(),
        sock=sock # Передаем наш настроенный сокет
    )

    # Запускаем цикл запроса IP
    asyncio.create_task(ip_request_loop(protocol))

    print("Ожидание ответа от сервера...")
    await protocol.ip_received.wait()

    # Получили IP! Настраиваем Windows
    adapter = setup_wintun(protocol.tun_ip, protocol.tun_gw)
    protocol.adapter = adapter

    # Запускаем основные циклы
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