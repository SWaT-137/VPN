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
from pytun_pmd3 import TunTapDevice # ВАЖНО: Для Windows!
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import pystray
from PIL import Image, ImageDraw

# === НАСТРОЙКИ ПУТЕЙ ===
WORK_DIR = os.path.join(os.getenv('LOCALAPPDATA'), 'PyLAN')
CONFIG_FILE = os.path.join(WORK_DIR, "config.json")
LOG_FILE = os.path.join(WORK_DIR, "client_lan.log")

SERVER_IP = '163.5.29.66'
SERVER_PORT = 65433 # ПОРТ ДЛЯ LAN СЕРВЕРА!
NETMASK = '255.255.255.0'
MTU = 1280
ADAPTER_NAME = "PyLAN" # Имя адаптера для LAN!
PASSWORD = "SWaT_2008"

SHA224_HASH = None
cipher = None

CMD_DATA = 0x00
CMD_PING = 0x01
CMD_PONG = 0x02
CMD_IP_REQ = 0x03
CMD_IP_ACK = 0x04
CMD_DISCONNECT = 0x05

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ ===
vpn_loop = None
vpn_protocol = None
is_connected = False
connection_failed = False
tray_icon = None
TUN_GW = '10.0.1.1'

# === ЛОГИРОВАНИЕ ===
def log_message(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            timestamp = time.strftime("%H:%M:%S")
            f.write(f"[{timestamp}] {msg}\n")
    except: pass

if os.path.exists(LOG_FILE):
    try: os.remove(LOG_FILE)
    except: pass

def format_speed(bps):
    return f"{bps * 8 / 1000000:.2f} Mbps"

# === БАЗОВЫЕ ФУНКЦИИ ===
def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

def setup_working_dir():
    try: os.makedirs(WORK_DIR, exist_ok=True)
    except: pass

def get_default_gateway():
    try:
        result = subprocess.run("route print -4 0.0.0.0", capture_output=True, text=True, shell=True)
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith("0.0.0.0") and "0.0.0.0" in line:
                parts = line.split()
                if len(parts) >= 5:
                    gateway, interface_ip = parts[2], parts[3]
                    if not gateway.startswith("10.0.0") and not interface_ip.startswith("10.0.0"):
                        return gateway
    except: pass
    return None

def load_config():
    global SERVER_IP, SERVER_PORT, ADAPTER_NAME, PASSWORD, SHA224_HASH, cipher
    
    if not os.path.exists(CONFIG_FILE):
        default_cfg = {
            "server_ip": "163.5.29.66", "server_port": 65433,
            "password": "a1d611ba-86d2-409d-84e0-2e5013201189",
            "adapter_name": "PyLAN"
        }
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(default_cfg, f, indent=4)
        except Exception as e:
            log_message(f"ОШИБКА создания config.json: {e}")

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f: cfg = json.load(f)
        SERVER_IP = cfg.get("server_ip", SERVER_IP)
        SERVER_PORT = cfg.get("server_port", SERVER_PORT)
        PASSWORD = cfg.get("password", PASSWORD)
        ADAPTER_NAME = cfg.get("adapter_name", ADAPTER_NAME)

        SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
        SERVER_SECRET = "super_secret_vpn_key_2024"
        key_material = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b'trojan-vpn-salt', iterations=100000).derive(SERVER_SECRET.encode())
        cipher = ChaCha20Poly1305(key_material)
        log_message("Конфигурация LAN загружена.")
        return True
    except Exception as e:
        log_message(f"ОШИБКА чтения config.json: {e}.")
        try:
            SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
            SERVER_SECRET = "super_secret_vpn_key_2024"
            key_material = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b'trojan-vpn-salt', iterations=100000).derive(SERVER_SECRET.encode())
            cipher = ChaCha20Poly1305(key_material)
            return True
        except: return False

# === СЕТЕВАЯ ЛОГИКА ===
class VPNClientProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.adapter = None
        self.tx_bytes = 0; self.rx_bytes = 0
        self.last_tx = 0; self.last_rx = 0; self.last_time = time.time()
        self.ip_received = asyncio.Event()
        self.tun_ip = None; self.tun_gw_local = None
        self.last_recv_time = time.time()

    def connection_made(self, transport):
        self.transport = transport
        self.send_ip_request()

    def datagram_received(self, data, addr):
        global is_connected, connection_failed
        if len(data) < 36: return
        try:
            plaintext = cipher.decrypt(data[:12], data[12:], None)
            recv_hash, recv_time_bytes, cmd = plaintext[:56], plaintext[56:64], plaintext[64]

            if recv_hash == SHA224_HASH:
                pkt_time = struct.unpack('!d', recv_time_bytes)[0]
                if abs(time.time() - pkt_time) > 30: return
                self.last_recv_time = time.time()
                
                if cmd == CMD_IP_ACK:
                    self.tun_ip = socket.inet_ntoa(plaintext[65:69])
                    self.tun_gw_local = socket.inet_ntoa(plaintext[69:73])
                    is_connected, connection_failed = True, False
                    update_tray_status()
                    self.ip_received.set()
                elif cmd == CMD_DATA:
                    if not self.adapter: return
                    self.adapter.write(plaintext[65:])
                    self.rx_bytes += len(plaintext[65:])
                elif cmd == CMD_PONG: pass 
        except Exception: pass

    def send_ip_request(self):
        req_payload = SHA224_HASH + struct.pack('!d', time.time()) + struct.pack('B', CMD_IP_REQ)
        nonce = os.urandom(12)
        self.transport.sendto(nonce + cipher.encrypt(nonce, req_payload, None), (SERVER_IP, SERVER_PORT))

def setup_wintun(tun_ip, tun_gw):
    local_gw = get_default_gateway()
    if not local_gw: 
        log_message("ОШИБКА: Локальный шлюз не найден!")
        return None

    adapter = TunTapDevice(name=ADAPTER_NAME); adapter.mtu = MTU; adapter.up()
    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" dhcp', shell=True)
    time.sleep(1)
    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" static {tun_ip} {NETMASK} {tun_gw}', shell=True)
    subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" dhcp', shell=True)
    subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=10', shell=True)
    time.sleep(3)

    subprocess.run(f'route delete 10.0.1.0', shell=True) 
    subprocess.run(f'route add 10.0.1.0 mask 255.255.255.0 {tun_gw} metric 10', shell=True)
    
    log_message(f"WinTUN поднят (Режим LAN). IP: {tun_ip}, Шлюз: {tun_gw}")
    return adapter

# === АСИНХРОННЫЕ ЗАДАЧИ ===
async def send_ping(protocol):
    global is_connected
    while True:
        await asyncio.sleep(15)
        if protocol.tun_ip:
            if time.time() - protocol.last_recv_time > 60 and is_connected:
                is_connected = False; update_tray_status(); log_message("ОШИБКА: LAN-сервер не отвечает.")
            try:
                ping_payload = SHA224_HASH + struct.pack('!d', time.time()) + struct.pack('B', CMD_PING) + socket.inet_aton(protocol.tun_ip)
                nonce = os.urandom(12)
                protocol.transport.sendto(nonce + cipher.encrypt(nonce, ping_payload, None), (SERVER_IP, SERVER_PORT))
            except: pass

async def read_from_wintun(adapter, transport, protocol):
    loop = asyncio.get_event_loop()
    while True:
        try:
            packet = await loop.run_in_executor(None, adapter.read, 65535)
            if packet:
                trojan_payload = SHA224_HASH + struct.pack('!d', time.time()) + struct.pack('B', CMD_DATA) + packet
                nonce = os.urandom(12)
                transport.sendto(nonce + cipher.encrypt(nonce, trojan_payload, None), (SERVER_IP, SERVER_PORT))
                protocol.tx_bytes += len(packet)
        except OSError: break
        except: break

async def ip_request_loop(protocol):
    global connection_failed
    attempts = 0
    while not protocol.tun_ip:
        await asyncio.sleep(3)
        if not protocol.tun_ip:
            protocol.send_ip_request(); attempts += 1
            if attempts >= 5 and not connection_failed:
                connection_failed = True; update_tray_status()
                log_message("ОШИБКА: LAN-сервер недоступен.")

async def log_metrics(protocol):
    while True:
        await asyncio.sleep(5)
        if protocol.adapter:
            now, elapsed = time.time(), time.time() - protocol.last_time
            if elapsed > 0:
                log_message(f"TX: {format_speed((protocol.tx_bytes - protocol.last_tx)/elapsed)} | RX: {format_speed((protocol.rx_bytes - protocol.last_rx)/elapsed)}")
                protocol.last_tx, protocol.last_rx, protocol.last_time = protocol.tx_bytes, protocol.rx_bytes, now

async def vpn_main():
    global vpn_protocol, TUN_GW
    log_message("Запуск LAN-цикла...")
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024 * 8)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024 * 8)
    sock.bind(('0.0.0.0', 0))
    
    transport, protocol = await loop.create_datagram_endpoint(lambda: VPNClientProtocol(), sock=sock)
    vpn_protocol = protocol
    asyncio.create_task(ip_request_loop(protocol))
    await protocol.ip_received.wait()
    
    TUN_GW = protocol.tun_gw_local
    adapter = setup_wintun(protocol.tun_ip, protocol.tun_gw_local)
    if not adapter: log_message("ОШИБКА: Не удалось поднять WinTUN адаптер!"); return
    
    protocol.adapter = adapter
    log_message("LAN VPN успешно подключен.")
    asyncio.create_task(read_from_wintun(adapter, transport, protocol))
    asyncio.create_task(send_ping(protocol))
    asyncio.create_task(log_metrics(protocol))
    while True: await asyncio.sleep(3600)

# === GUI (ТРЕЙ) ===
def create_static_icon():
    image = Image.new('RGB', (64, 64), (30, 30, 30))
    ImageDraw.Draw(image).rectangle([8, 8, 56, 56], fill=(0, 200, 83), outline='white', width=2)
    return image

def update_tray_status():
    if tray_icon:
        if is_connected:
            tray_icon.title = "PyLAN: В сети"
            try: tray_icon.notify("Вы в виртуальной локальной сети", "PyLAN")
            except: pass
        elif connection_failed:
            tray_icon.title = "PyLAN: Ошибка"
            try: tray_icon.notify("LAN-сервер недоступен", "PyLAN Ошибка")
            except: pass
        else: tray_icon.title = "PyLAN: Подключение..."

def cleanup_vpn():
    log_message("Выход из LAN-сети...")
    if vpn_protocol and vpn_protocol.adapter:
        try: vpn_protocol.adapter.down()
        except: pass
    if vpn_protocol and vpn_protocol.transport and is_connected:
        try:
            disc_payload = SHA224_HASH + struct.pack('!d', time.time()) + struct.pack('B', CMD_DISCONNECT)
            nonce = os.urandom(12)
            for _ in range(3): vpn_protocol.transport.sendto(nonce + cipher.encrypt(nonce, disc_payload, None), (SERVER_IP, SERVER_PORT))
        except: pass
    try:
        subprocess.run(f'route delete 10.0.1.0 mask 255.255.255.0 {TUN_GW}', shell=True, timeout=2)
    except: pass

def on_exit(icon, item):
    cleanup_vpn()
    icon.stop()
    os._exit(0)

def open_log(icon, item):
    if os.path.exists(LOG_FILE): os.startfile(LOG_FILE)
    else: open(LOG_FILE, 'w').close(); os.startfile(LOG_FILE)

def open_config_folder(icon, item):
    if os.path.exists(WORK_DIR): os.startfile(WORK_DIR)

def get_status_text(item):
    if is_connected: return "Статус: В сети ✅"
    elif connection_failed: return "Статус: Нет связи ❌"
    else: return "Статус: Подключение..."

def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem(get_status_text, None, enabled=False),
        pystray.MenuItem("Открыть папку", open_config_folder),
        pystray.MenuItem("Открыть лог", open_log),
        pystray.MenuItem("Выход", on_exit))
    tray_icon = pystray.Icon("PyLAN", create_static_icon(), "PyLAN: Подключение...", menu=menu)
    tray_icon.run()

def start_vpn_thread():
    global vpn_loop
    vpn_loop = asyncio.new_event_loop(); asyncio.set_event_loop(vpn_loop)
    vpn_loop.run_until_complete(vpn_main())

if __name__ == '__main__':
    try:
        setup_working_dir()
        if not is_admin():
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            sys.exit(0)
        if not load_config(): sys.exit(1)
        
        vpn_thread = threading.Thread(target=start_vpn_thread, daemon=True); vpn_thread.start()
        setup_tray()
    except Exception as e:
        with open("error_lan.log", "w") as f:
            import traceback; f.write(traceback.format_exc())