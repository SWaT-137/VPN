import asyncio
import struct
import subprocess
import hashlib
import time
import os
import urllib.request
import ipaddress 
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
from PySide6.QtWidgets import QApplication, QVBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QMessageBox, QDialog
from PySide6.QtCore import QObject, Signal

# === ОТКАЗОУСТОЙЧИВЫЕ ПУТИ ===
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

WORK_DIR = os.path.join(base_dir, "PyVPN_data")

try:
    os.makedirs(WORK_DIR, exist_ok=True)
    test_file = os.path.join(WORK_DIR, 'test.tmp')
    with open(test_file, 'w') as f: f.write('1')
    os.remove(test_file)
except Exception:
    import tempfile
    WORK_DIR = os.path.join(tempfile.gettempdir(), 'PyVPN_data')
    os.makedirs(WORK_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(WORK_DIR, "config.json")
LOG_FILE = os.path.join(WORK_DIR, "client.log")
BYPASS_FILE = os.path.join(WORK_DIR, "bypass.txt")

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
CMD_DISCONNECT = 0x05

SPLIT_RU_URL = "https://antifilter.download/list/subnet.lst"
# Резервные URL для скачивания
SPLIT_RU_URL_BACKUP = "https://raw.githubusercontent.com/zapret-info/z-i/master/dump.csv"
SPLIT_RU_FILE = os.path.join(WORK_DIR, "split_ru.txt")
SPLIT_TUNNEL_MODE = "off"
bypass_routes = []

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ ===
vpn_loop = None
vpn_thread = None
vpn_protocol = None
is_connected = False
connection_failed = False
tray_icon = None
TUN_GW = '10.0.0.1'
LOCAL_GW = "" 
main_task = None
signals = None
restart_in_progress = False
split_list_downloaded = False  # Флаг успешной загрузки списка

# === ЛОГИРОВАНИЕ И БАЗОВЫЕ ФУНКЦИИ ===
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

def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

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

def init_crypto():
    global SHA224_HASH, cipher
    SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
    SERVER_SECRET = "SWaT_2008" 
    key_material = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b'trojan-vpn-salt', iterations=100000).derive(SERVER_SECRET.encode())
    cipher = ChaCha20Poly1305(key_material)

def load_config():
    global SERVER_IP, SERVER_PORT, ADAPTER_NAME, PASSWORD, SPLIT_TUNNEL_MODE, LOCAL_GW
    
    if not os.path.exists(CONFIG_FILE):
        log_message("config.json не найден. Используем настройки по умолчанию.")
        try:
            init_crypto()
            return True
        except Exception as e2:
            log_message(f"КРИТИЧЕСКАЯ ОШИБКА инициализации шифрования: {e2}")
            return False

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f: cfg = json.load(f)
        SERVER_IP = cfg.get("server_ip", SERVER_IP)
        SERVER_PORT = cfg.get("server_port", SERVER_PORT)
        PASSWORD = cfg.get("password", PASSWORD)
        ADAPTER_NAME = cfg.get("adapter_name", ADAPTER_NAME)
        SPLIT_TUNNEL_MODE = cfg.get("split_tunnel", SPLIT_TUNNEL_MODE)
        LOCAL_GW = cfg.get("local_gw", "")
        
        init_crypto()
        log_message("Конфигурация загружена.")
        return True
    except Exception as e:
        log_message(f"ОШИБКА чтения config.json: {e}. Используем настройки по умолчанию.")
        try:
            init_crypto()
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

# === РАЗДЕЛЕНИЕ ТРАФИКА С ПОДДЕРЖКОЙ ОФЛАЙН ===
def download_split_list():
    """Скачивание списка с поддержкой резервных URL и кэша"""
    global split_list_downloaded
    
    if SPLIT_TUNNEL_MODE == "off":
        split_list_downloaded = True
        return
    
    log_message(f"Скачивание списка обхода (режим: {SPLIT_TUNNEL_MODE})...")
    
    # Пробуем скачать список
    urls_to_try = [SPLIT_RU_URL, SPLIT_RU_URL_BACKUP]
    downloaded = False
    
    for url in urls_to_try:
        try:
            # Используем таймаут и User-Agent
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8', errors='ignore')
                # Парсим содержимое для антифильтра
                if 'subnet.lst' in url:
                    with open(SPLIT_RU_FILE, 'w', encoding='utf-8') as f:
                        f.write(content)
                else:
                    # Для резервного URL парсим IP-адреса
                    import re
                    ips = re.findall(r'\d+\.\d+\.\d+\.\d+/\d+', content)
                    with open(SPLIT_RU_FILE, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(ips))
                downloaded = True
                log_message(f"✅ Список подсетей успешно скачан с {url}")
                break
        except Exception as e:
            log_message(f"⚠️ Не удалось скачать с {url}: {e}")
            continue
    
    if not downloaded and os.path.exists(SPLIT_RU_FILE):
        log_message("ℹ️ Используем кэшированный список.")
        split_list_downloaded = True
    elif not downloaded:
        log_message("❌ Не удалось скачать список. Разделение трафика может не работать.")
        # Создаем пустой файл, чтобы не было ошибок
        with open(SPLIT_RU_FILE, 'w', encoding='utf-8') as f:
            f.write("")
        split_list_downloaded = False
    else:
        split_list_downloaded = True

def add_bypass_routes(local_gw):
    global bypass_routes; bypass_routes = []; count = 0
    
    # Обход Windows NCSI
    ncsi_ips = [
        "131.107.255.255", 
        "13.107.4.52", 
        "13.107.4.53", 
        "13.107.6.152", 
        "204.79.197.200"
    ]
    for ip in ncsi_ips:
        try:
            subprocess.run(f'route add {ip} mask 255.255.255.255 {local_gw} metric 5', shell=True)
            bypass_routes.append((ip, "255.255.255.255")); count += 1
        except: pass

    for cidr in ["192.168.0.0/16", "172.16.0.0/12", "10.0.0.0/8"]:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            subprocess.run(f'route add {net.network_address} mask {net.netmask} {local_gw} metric 5', shell=True)
            bypass_routes.append((str(net.network_address), str(net.netmask))); count += 1
        except: pass

    if SPLIT_TUNNEL_MODE != "off" and os.path.exists(SPLIT_RU_FILE):
        try:
            with open(SPLIT_RU_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    try:
                        net = ipaddress.ip_network(line, strict=False)
                        subprocess.run(f'route add {net.network_address} mask {net.netmask} {local_gw} metric 5', shell=True)
                        bypass_routes.append((str(net.network_address), str(net.netmask))); count += 1
                    except:
                        pass
        except Exception as e:
            log_message(f"Ошибка чтения списка разделения: {e}")

    if os.path.exists(BYPASS_FILE):
        with open(BYPASS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                cidr = line.strip()
                if not cidr or cidr.startswith('#'): continue
                try:
                    net = ipaddress.ip_network(cidr, strict=False)
                    subprocess.run(f'route add {net.network_address} mask {net.netmask} {local_gw} metric 5', shell=True)
                    bypass_routes.append((str(net.network_address), str(net.netmask))); count += 1
                except: pass
    log_message(f"✅ Добавлено {count} маршрутов в обход VPN.")

def setup_wintun(tun_ip, tun_gw):
    if LOCAL_GW:
        local_gw = LOCAL_GW
        log_message(f"Использование шлюза из config.json: {local_gw}")
    else:
        local_gw = get_default_gateway()
        if local_gw:
            log_message(f"Автоматически найден шлюз: {local_gw}")
            
    if not local_gw: 
        log_message("ОШИБКА: Локальный шлюз не найден!")
        return None

    # СКАЧИВАЕМ СПИСОК ДО СОЗДАНИЯ АДАПТЕРА (пока есть интернет)
    download_split_list()

    # Проверяем существует ли адаптер и удаляем его
    try:
        subprocess.run(f'netsh interface ip delete interface "{ADAPTER_NAME}"', shell=True, timeout=3)
        time.sleep(1)
    except: pass

    adapter = TunTapDevice(name=ADAPTER_NAME)
    adapter.mtu = MTU
    adapter.up()
    time.sleep(2)
    
    # Отключаем IPv6
    subprocess.run(f'netsh interface ipv6 set interface "{ADAPTER_NAME}" disable', shell=True)
    
    # Сбрасываем настройки адаптера
    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" dhcp', shell=True)
    time.sleep(1)
    
    # Применяем статический IP
    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" static {tun_ip} {NETMASK} {tun_gw}', shell=True)
    subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True)
    subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)
    time.sleep(2)

    # Очищаем старые маршруты ТОЛЬКО для VPN (не трогаем локальный 0.0.0.0!)
    subprocess.run(f'route delete 0.0.0.0 mask 0.0.0.0 {tun_gw}', shell=True, timeout=2)
    time.sleep(0.5)
    subprocess.run(f'route delete {SERVER_IP} mask 255.255.255.255', shell=True, timeout=2)
    time.sleep(0.5)
    
    # Добавляем маршрут к серверу через локальный шлюз
    subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {local_gw} metric 1', shell=True)
    
    # Добавляем маршруты обхода
    add_bypass_routes(local_gw)

    # Добавляем default маршрут через VPN (с метрикой 1, он перекроет локальный)
    subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {tun_gw} metric 1', shell=True)
    log_message(f"WinTUN поднят. IP: {tun_ip}, Шлюз: {tun_gw}")
    return adapter

# === АСИНХРОННЫЕ ЗАДАЧИ ===
async def send_ping(protocol):
    global is_connected
    while True:
        await asyncio.sleep(15)
        if protocol.tun_ip:
            if time.time() - protocol.last_recv_time > 60 and is_connected:
                is_connected = False
                update_tray_status()
                log_message("ОШИБКА: Сервер не отвечает.")
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
                log_message("ОШИБКА: Сервер недоступен, неверный пароль или проблемы с сетью.")

async def log_metrics(protocol):
    while True:
        await asyncio.sleep(5)
        if protocol.adapter:
            now, elapsed = time.time(), time.time() - protocol.last_time
            if elapsed > 0:
                log_message(f"TX: {format_speed((protocol.tx_bytes - protocol.last_tx)/elapsed)} | RX: {format_speed((protocol.rx_bytes - protocol.last_rx)/elapsed)}")
                protocol.last_tx, protocol.last_rx, protocol.last_time = protocol.tx_bytes, protocol.rx_bytes, now

async def vpn_main():
    global vpn_protocol, TUN_GW, main_task, restart_in_progress
    main_task = asyncio.current_task()
    log_message("Запуск VPN цикла...")
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
    
    await asyncio.sleep(1)
    
    adapter = setup_wintun(protocol.tun_ip, protocol.tun_gw_local)
    if not adapter:
        log_message("ОШИБКА: Не удалось поднять WinTUN адаптер!")
        return
    
    protocol.adapter = adapter
    log_message("VPN успешно подключен и работает.")
    asyncio.create_task(read_from_wintun(adapter, transport, protocol))
    asyncio.create_task(send_ping(protocol))
    asyncio.create_task(log_metrics(protocol))
    
    try:
        while True: await asyncio.sleep(3600)
    except asyncio.CancelledError:
        log_message("VPN цикл отменен для перезапуска.")
        if adapter:
            try: adapter.down()
            except: pass

def start_vpn_thread():
    global vpn_loop, restart_in_progress
    vpn_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(vpn_loop)
    try:
        vpn_loop.run_until_complete(vpn_main())
    except Exception as e:
        log_message(f"VPN поток завершился с ошибкой: {e}")
    finally:
        restart_in_progress = False
        try: vpn_loop.close()
        except: pass

# === УПРАВЛЕНИЕ VPN ===
def cleanup_vpn():
    global TUN_GW, bypass_routes
    log_message("Очистка маршрутов и завершение...")
    if vpn_protocol and vpn_protocol.adapter:
        try: 
            vpn_protocol.adapter.down()
            time.sleep(0.5)
        except: pass

    if vpn_protocol and vpn_protocol.transport and is_connected:
        try:
            disc_payload = SHA224_HASH + struct.pack('!d', time.time()) + struct.pack('B', CMD_DISCONNECT)
            nonce = os.urandom(12)
            for _ in range(3): vpn_protocol.transport.sendto(nonce + cipher.encrypt(nonce, disc_payload, None), (SERVER_IP, SERVER_PORT))
        except: pass

    # Удаление маршрутов
    try:
        # Удаляем ТОЛЬКО маршрут VPN, чтобы не отрубить локальный интернет!
        if TUN_GW:
            subprocess.run(f'route delete 0.0.0.0 mask 0.0.0.0 {TUN_GW}', shell=True, timeout=3)
        
        time.sleep(0.3)
        subprocess.run(f'route delete {SERVER_IP} mask 255.255.255.255', shell=True, timeout=3)
        time.sleep(0.3)
        
        for net_addr, mask in bypass_routes: 
            subprocess.run(f'route delete {net_addr} mask {mask}', shell=True, timeout=3)
        
        # Очищаем список, чтобы при следующем запуске добавить заново
        bypass_routes = [] 
    except Exception as e:
        log_message(f"Ошибка при очистке маршрутов: {e}")
    
    # НЕ УДАЛЯЕМ АДАПТЕР ПРИ ПЕРЕЗАПУСКЕ, чтобы сохранить доступ к интернету
    # Только при полном выходе

def restart_vpn():
    """Мягкий перезапуск VPN без закрытия приложения"""
    global vpn_loop, vpn_thread, vpn_protocol, is_connected, connection_failed, main_task, restart_in_progress
    
    if restart_in_progress:
        log_message("Перезапуск уже выполняется...")
        return
    
    restart_in_progress = True
    log_message("Перезапуск VPN (Soft Restart)...")
    
    # Останавливаем текущий VPN
    if vpn_loop and main_task and not main_task.done():
        try:
            vpn_loop.call_soon_threadsafe(main_task.cancel)
        except: pass
    
    # Ждем завершения потока
    if vpn_thread:
        vpn_thread.join(timeout=5)
    
    # Очищаем состояние, НО НЕ УДАЛЯЕМ АДАПТЕР
    # Просто сбрасываем маршруты
    cleanup_vpn()
    
    # Даем системе время
    time.sleep(2)
    
    is_connected = False
    connection_failed = False
    vpn_protocol = None
    update_tray_status()
    
    load_config()
    
    # Создаем новый поток
    vpn_thread = threading.Thread(target=start_vpn_thread, daemon=True)
    vpn_thread.start()
    
    time.sleep(2)
    restart_in_progress = False

# === ГРАФИЧЕСКОЕ ОКНО PySide6 ===
class VPNSignals(QObject):
    show_settings = Signal()

def open_settings_window(icon, item):
    if signals:
        signals.show_settings.emit()

# === ТРЕЙ ===
def create_static_icon():
    image = Image.new('RGB', (64, 64), (30, 30, 30))
    ImageDraw.Draw(image).rectangle([8, 8, 56, 56], fill=(0, 120, 215), outline='white', width=2)
    return image

def update_tray_status():
    if tray_icon:
        if is_connected:
            tray_icon.title = "PyVPN: Подключено"
            try: tray_icon.notify("VPN успешно подключен", "PyVPN")
            except: pass
        elif connection_failed:
            tray_icon.title = "PyVPN: Ошибка подключения"
            try: tray_icon.notify("Сервер недоступен или неверный пароль", "PyVPN Ошибка")
            except: pass
        else: tray_icon.title = "PyVPN: Подключение..."

def on_exit(icon, item):
    # При выходе полностью удаляем адаптер
    try:
        subprocess.run(f'netsh interface ip delete interface "{ADAPTER_NAME}"', shell=True, timeout=3)
    except: pass
    cleanup_vpn()
    icon.stop()
    os._exit(0)

def open_log(icon, item):
    if os.path.exists(LOG_FILE): os.startfile(LOG_FILE)
    else: open(LOG_FILE, 'w').close(); os.startfile(LOG_FILE)

def open_config_folder(icon, item):
    if os.path.exists(WORK_DIR): os.startfile(WORK_DIR)

def get_status_text(item):
    if is_connected: return "Статус: Подключено ✅"
    elif connection_failed: return "Статус: Сервер недоступен ❌"
    else: return "Статус: Подключение..."

def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem(get_status_text, None, enabled=False),
        pystray.MenuItem("Настройки", open_settings_window),
        pystray.MenuItem("Открыть папку", open_config_folder),
        pystray.MenuItem("Открыть лог", open_log),
        pystray.MenuItem("Выход", on_exit))
    tray_icon = pystray.Icon("PyVPN", create_static_icon(), "PyVPN: Запуск...", menu=menu)
    tray_icon.run()

if __name__ == '__main__':
    try:
        if not is_admin():
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            sys.exit(0)
        if not load_config(): sys.exit(1)
        
        app = QApplication([])
        signals = VPNSignals()
        
        dialog = QDialog()
        dialog.setWindowTitle("Настройки PyVPN")
        dialog.setFixedSize(400, 320)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("IP Сервера:"))
        ip_input = QLineEdit()
        layout.addWidget(ip_input)

        layout.addWidget(QLabel("Порт:"))
        port_input = QLineEdit()
        layout.addWidget(port_input)

        layout.addWidget(QLabel("Пароль (UUID):"))
        pass_input = QLineEdit()
        layout.addWidget(pass_input)

        layout.addWidget(QLabel("Локальный шлюз (оставьте пустым для авто):"))
        gw_input = QLineEdit()
        layout.addWidget(gw_input)

        layout.addWidget(QLabel("Разделение трафика:"))
        split_input = QComboBox()
        split_input.addItems(["off", "ru"])
        layout.addWidget(split_input)

        def update_inputs():
            ip_input.setText(SERVER_IP)
            port_input.setText(str(SERVER_PORT))
            pass_input.setText(PASSWORD)
            gw_input.setText(LOCAL_GW)
            split_input.setCurrentText(SPLIT_TUNNEL_MODE)

        def save_and_restart():
            global SERVER_IP, SERVER_PORT, PASSWORD, LOCAL_GW, SPLIT_TUNNEL_MODE
            
            SERVER_IP = ip_input.text()
            try: SERVER_PORT = int(port_input.text())
            except:
                QMessageBox.warning(dialog, "Ошибка", "Порт должен быть числом!")
                return
            PASSWORD = pass_input.text()
            LOCAL_GW = gw_input.text()
            SPLIT_TUNNEL_MODE = split_input.currentText()

            cfg_data = {
                "server_ip": SERVER_IP, "server_port": SERVER_PORT,
                "password": PASSWORD, "adapter_name": ADAPTER_NAME,
                "split_tunnel": SPLIT_TUNNEL_MODE, "local_gw": LOCAL_GW
            }
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(cfg_data, f, indent=4)
            except Exception:
                pass
            
            dialog.hide()
            # Запускаем перезапуск с задержкой
            threading.Thread(target=lambda: (time.sleep(1), restart_vpn()), daemon=True).start()

        save_btn = QPushButton("Сохранить и перезапустить")
        save_btn.clicked.connect(save_and_restart)
        layout.addWidget(save_btn)
        dialog.setLayout(layout)
        
        def on_dialog_close(event):
            dialog.hide()
            event.ignore()

        dialog.closeEvent = on_dialog_close

        def show_settings_dialog():
            try:
                update_inputs()
                if dialog.isVisible():
                    dialog.raise_()
                    dialog.activateWindow()
                else:
                    dialog.show()
                    dialog.raise_()
                    dialog.activateWindow()
            except Exception as e:
                log_message(f"Ошибка показа окна настроек: {e}")
                try:
                    dialog.show()
                except Exception as e2:
                    log_message(f"Критическая ошибка показа окна: {e2}")

        signals.show_settings.connect(show_settings_dialog)

        vpn_thread = threading.Thread(target=start_vpn_thread, daemon=True)
        vpn_thread.start()
        
        tray_thread = threading.Thread(target=setup_tray, daemon=True)
        tray_thread.start()
        
        app.exec()
        
    except Exception as e:
        with open("error.log", "w") as f:
            import traceback; f.write(traceback.format_exc())