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
import threading
from pytun_pmd3 import TunTapDevice
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import pystray
from PIL import Image, ImageDraw

# === НАСТРОЙКИ ===
WORK_DIR = os.path.join(os.getenv('LOCALAPPDATA'), 'PyVPN')
CONFIG_FILE = os.path.join(WORK_DIR, "config.json")
LOG_FILE = os.path.join(WORK_DIR, "client.log")
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
CMD_DISCONNECT = 0x05 # ДОБАВИТЬ ЭТО

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ ===
vpn_loop = None
vpn_protocol = None
is_connected = False
connection_failed = False # Новая переменная: сервер не отвечает
tray_icon = None
TUN_GW = '10.0.0.1'

# === ЛОГИРОВАНИЕ ===
def log_message(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = time.strftime("%H:%M:%S")
        f.write(f"[{timestamp}] {msg}\n")

if os.path.exists(LOG_FILE):
    try: os.remove(LOG_FILE)
    except: pass

def format_speed(bps):
    mbps = bps * 8 / 1000000
    return f"{mbps:.2f} Mbps"

# === ФУНКЦИИ СЕТИ ===
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False
def setup_working_dir():
    """Создает папку в AppData для конфигов и логов"""
    try:
        os.makedirs(WORK_DIR, exist_ok=True)
    except Exception:
        pass # Если не удалось, файлы создадутся рядом с exe
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
    except Exception:
        return None

def load_config():
    global SERVER_IP, SERVER_PORT, ADAPTER_NAME, PASSWORD, SHA224_HASH, cipher
    
    if not os.path.exists(CONFIG_FILE):
        default_cfg = {
            "server_ip": "163.5.29.66",
            "server_port": 65432,
            "password": "a1d611ba-86d2-409d-84e0-2e5013201189", # Замени на свой UUID
            "adapter_name": "PyVPN"
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_cfg, f, indent=4)

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        
        SERVER_IP = cfg.get("server_ip", SERVER_IP)
        SERVER_PORT = cfg.get("server_port", SERVER_PORT)
        PASSWORD = cfg.get("password", PASSWORD)
        ADAPTER_NAME = cfg.get("adapter_name", ADAPTER_NAME)

        SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
        
        SERVER_SECRET = "SWaT_2008" # <--- ДОЛЖЕН СОВПАДАТЬ С СЕРВЕРОМ!
        key_material = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'trojan-vpn-salt',
            iterations=100000,
        ).derive(SERVER_SECRET.encode())
        cipher = ChaCha20Poly1305(key_material)
        
        log_message("Конфигурация загружена.")
        return True
    except Exception as e:
        log_message(f"ОШИБКА чтения config.json: {e}")
        return False

class VPNClientProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.adapter = None
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.last_tx = 0
        self.last_rx = 0
        self.last_time = time.time()
        self.ip_received = asyncio.Event()
        self.tun_ip = None
        self.tun_gw_local = None
        self.last_recv_time = time.time()

    def connection_made(self, transport):
        self.transport = transport
        self.send_ip_request()

    def datagram_received(self, data, addr):
        global is_connected
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

                self.last_recv_time = time.time()
                
                if cmd == CMD_IP_ACK:
                    self.tun_ip = socket.inet_ntoa(plaintext[65:69])
                    self.tun_gw_local = socket.inet_ntoa(plaintext[69:73])
                    is_connected = True
                    connection_failed = False # Сервер ответил, сбрасываем ошибку!
                    update_tray_status()
                    self.ip_received.set()
                elif cmd == CMD_DATA:
                    if not self.adapter: return
                    self.adapter.write(plaintext[65:])
                    self.rx_bytes += len(plaintext[65:])
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

def setup_wintun(tun_ip, tun_gw):
    local_gw = get_default_gateway()
    if not local_gw: 
        log_message("ОШИБКА: Локальный шлюз не найден!")
        return None

    adapter = TunTapDevice(name=ADAPTER_NAME)
    adapter.mtu = MTU
    adapter.up()

    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" dhcp', shell=True)
    time.sleep(1)

    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" static {tun_ip} {NETMASK} {tun_gw}', shell=True)
    subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True)
    subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)
    time.sleep(3)

    subprocess.run(f'route delete {SERVER_IP}', shell=True)
    subprocess.run(f'route delete 0.0.0.0 mask 0.0.0.0 {tun_gw}', shell=True)
    subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {local_gw} metric 5', shell=True)
    subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {tun_gw} metric 1', shell=True)
    
    log_message(f"WinTUN поднят. IP: {tun_ip}, Шлюз: {tun_gw}")
    return adapter

async def send_ping(protocol):
    global is_connected
    while True:
        await asyncio.sleep(15)
        if protocol.tun_ip:
            if time.time() - protocol.last_recv_time > 60:
                if is_connected:
                    is_connected = False
                    update_tray_status()
                    log_message("ОШИБКА: Сервер не отвечает дольше 60 секунд.")
            
            try:
                current_time = struct.pack('!d', time.time())
                vip_bytes = socket.inet_aton(protocol.tun_ip)
                ping_payload = SHA224_HASH + current_time + struct.pack('B', CMD_PING) + vip_bytes
                nonce = os.urandom(12)
                ciphertext = cipher.encrypt(nonce, ping_payload, None)
                protocol.transport.sendto(nonce + ciphertext, (SERVER_IP, SERVER_PORT))
            except Exception: pass

async def read_from_wintun(adapter, transport, protocol):
    loop = asyncio.get_event_loop()
    while True:
        try:
            packet = await loop.run_in_executor(None, adapter.read, 65535)
            if packet:
                current_time = struct.pack('!d', time.time())
                trojan_payload = SHA224_HASH + current_time + struct.pack('B', CMD_DATA) + packet
                nonce = os.urandom(12)
                ciphertext = cipher.encrypt(nonce, trojan_payload, None)
                transport.sendto(nonce + ciphertext, (SERVER_IP, SERVER_PORT))
                protocol.tx_bytes += len(packet)
        except Exception: pass

async def ip_request_loop(protocol):
    global connection_failed
    attempts = 0
    while not protocol.tun_ip:
        await asyncio.sleep(3)
        if not protocol.tun_ip:
            protocol.send_ip_request()
            attempts += 1
            # Если сделали 5 попыток (15 секунд), а ответа нет:
            if attempts >= 5 and not connection_failed:
                connection_failed = True
                update_tray_status() # Обновляем текст в трее
                log_message("ОШИБКА: Сервер недоступен, неверный пароль или проблемы с сетью.")

async def log_metrics(protocol):
    while True:
        await asyncio.sleep(5)
        if protocol.adapter:
            now = time.time()
            elapsed = now - protocol.last_time
            if elapsed > 0:
                tx_speed = (protocol.tx_bytes - protocol.last_tx) / elapsed
                rx_speed = (protocol.rx_bytes - protocol.last_rx) / elapsed
                log_message(f"TX: {format_speed(tx_speed)} | RX: {format_speed(rx_speed)}")
                protocol.last_tx = protocol.tx_bytes
                protocol.last_rx = protocol.rx_bytes
                protocol.last_time = now

async def vpn_main():
    global vpn_protocol, TUN_GW
    log_message("Запуск VPN цикла...")
    loop = asyncio.get_running_loop()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024 * 8)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024 * 8)
    sock.bind(('0.0.0.0', 0))
    
    transport, protocol = await loop.create_datagram_endpoint(lambda: VPNClientProtocol(), sock=sock)
    vpn_protocol = protocol

    asyncio.create_task(ip_request_loop(protocol))
    log_message("Ожидание выдачи IP от сервера...")
    await protocol.ip_received.wait()
    log_message(f"Получен IP: {protocol.tun_ip}")

    TUN_GW = protocol.tun_gw_local

    adapter = setup_wintun(protocol.tun_ip, protocol.tun_gw_local)
    if not adapter:
        log_message("ОШИБКА: Не удалось поднять WinTUN адаптер!")
        return
    
    protocol.adapter = adapter
    log_message("VPN успешно подключен и работает.")
    
    asyncio.create_task(read_from_wintun(adapter, transport, protocol))
    asyncio.create_task(send_ping(protocol))
    asyncio.create_task(log_metrics(protocol))
    
    while True:
        await asyncio.sleep(3600)


# === ЛОГИКА GUI (ТРЕЙ) ===

def create_static_icon():
    # Рисуем простую синюю иконку, которая никогда не меняется
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), (30, 30, 30))
    dc = ImageDraw.Draw(image)
    dc.rectangle([8, 8, width-8, height-8], fill=(0, 120, 215), outline='white', width=2)
    return image

def update_tray_status():
    if tray_icon:
        if is_connected:
            tray_icon.title = "PyVPN: Подключено"
            try:
                tray_icon.notify("VPN успешно подключен", "PyVPN")
            except Exception: pass
        elif connection_failed:
            tray_icon.title = "PyVPN: Ошибка подключения"
            try:
                # Показываем красное уведомление об ошибке
                tray_icon.notify("Сервер недоступен или неверный пароль", "PyVPN Ошибка")
            except Exception: pass
        else:
            tray_icon.title = "PyVPN: Подключение..."



def cleanup_vpn():
    global is_connected
    log_message("Очистка маршрутов и завершение...")
    
    # 1. Отправляем серверу пакет об отключении (если есть транспорт)
    if vpn_protocol and vpn_protocol.transport and is_connected:
        try:
            current_time = struct.pack('!d', time.time())
            # Формируем пакет отключения
            disc_payload = SHA224_HASH + current_time + struct.pack('B', CMD_DISCONNECT)
            nonce = os.urandom(12)
            ciphertext = cipher.encrypt(nonce, disc_payload, None)
            # Отправляем 3 раза для надежности (UDP может потерять пакет)
            for _ in range(3):
                vpn_protocol.transport.sendto(nonce + ciphertext, (SERVER_IP, SERVER_PORT))
            log_message("Сервер уведомлен об отключении.")
        except Exception as e:
            log_message(f"Не удалось отправить пакет отключения: {e}")

    # 2. Очищаем маршруты
    try:
        subprocess.run(f'route delete 0.0.0.0 mask 0.0.0.0 {TUN_GW}', shell=True)
        subprocess.run(f'route delete {SERVER_IP}', shell=True)
        if vpn_protocol and vpn_protocol.adapter:
            vpn_protocol.adapter.down()
    except Exception as e:
        log_message(f"Ошибка при очистке маршрутов: {e}")

def on_exit(icon, item):
    cleanup_vpn()
    if vpn_loop:
        vpn_loop.call_soon_threadsafe(vpn_loop.stop)
    icon.stop()

def open_log(icon, item):
    if os.path.exists(LOG_FILE):
        os.startfile(LOG_FILE)
    else:
        open(LOG_FILE, 'w').close()
        os.startfile(LOG_FILE)

def open_config_folder(icon, item):
    """Открывает папку, в которой лежит config.json"""
    if os.path.exists(WORK_DIR):
        os.startfile(WORK_DIR)
    else:
        log_message(f"ОШИБКА: Папка {WORK_DIR} не найдена!")

def get_status_text(item):
    if is_connected:
        return "Статус: Подключено ✅"
    elif connection_failed:
        return "Статус: Сервер недоступен ❌"
    else:
        return "Статус: Подключение..."

def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem(get_status_text, None, enabled=False),
        pystray.MenuItem("Открыть папку", open_config_folder), # НОВАЯ КНОПКА
        pystray.MenuItem("Открыть лог", open_log),
        pystray.MenuItem("Выход", on_exit)
    )
    tray_icon = pystray.Icon("PyVPN", create_static_icon(), "PyVPN: Запуск...", menu=menu)
    tray_icon.run()

def start_vpn_thread():
    global vpn_loop
    vpn_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(vpn_loop)
    vpn_loop.run_until_complete(vpn_main())


if __name__ == '__main__':
    try:
        # ШАГ 1: Создаем рабочую папку в AppData
        setup_working_dir()

        # ШАГ 2: Проверка прав админа
        if not is_admin():
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            sys.exit(0)

        # ШАГ 3: Загрузка конфига
        if not load_config():
            sys.exit(1)

        # ШАГ 4: Запуск VPN
        vpn_thread = threading.Thread(target=start_vpn_thread, daemon=True)
        vpn_thread.start()

        # ШАГ 5: Запуск статичного трея
        setup_tray()
        
    except Exception as e:
        with open("error.log", "w") as f:
            import traceback
            f.write(traceback.format_exc())