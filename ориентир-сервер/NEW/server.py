import asyncio
import pytun
import hashlib
import os
import socket
import struct
import time
import json
import sys # Добавлено для проверки терминала
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Настройки сети туннеля
TUN_IP = '10.0.0.1'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1280
SERVER_PORT = 65432
USERS_FILE = "users.json"

# --- НАСТРОЙКИ ШИФРОВАНИЯ ---
# Ключ шифрования теперь НЕ зависит от пароля пользователя!
# Это фиксированный ключ сервера для защиты от DPI. 
# (Пароли пользователей проверяются внутри расшифрованного пакета).
SERVER_SECRET = "SWaT_2008"
key_material = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=b'trojan-vpn-salt',
    iterations=100000,
).derive(SERVER_SECRET.encode())
cipher = ChaCha20Poly1305(key_material)
# ------------------------

# Словарь: Хеш UUID -> Имя пользователя
allowed_users = {}

CMD_DATA = 0x00
CMD_PING = 0x01
CMD_PONG = 0x02
CMD_IP_REQ = 0x03
CMD_IP_ACK = 0x04

def load_users():
    global allowed_users
    if not os.path.exists(USERS_FILE):
        print(f"❌ Файл {USERS_FILE} не найден!")
        return False
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            users_dict = json.load(f)
        
        allowed_users.clear()
        for uuid, username in users_dict.items():
            # Хешируем UUID так же, как клиент хеширует пароль
            user_hash = hashlib.sha224(uuid.encode()).hexdigest().encode()
            allowed_users[user_hash] = username
        print(f"✅ Загружено {len(allowed_users)} пользователей из {USERS_FILE}")
        return True
    except Exception as e:
        print(f"❌ Ошибка чтения {USERS_FILE}: {e}")
        return False

def extract_ips(raw_packet):
    if len(raw_packet) < 20: return None, None
    version = (raw_packet[0] >> 4) & 0x0F
    if version != 4: return None, None
    src_ip = socket.inet_ntoa(raw_packet[12:16])
    dst_ip = socket.inet_ntoa(raw_packet[16:20])
    return src_ip, dst_ip

class VPNServerProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.tun = None
        self.clients = {}       # {'10.0.0.2': ('1.2.3.4', 12345, 'username')}
        self.udp_to_vip = {}    # {('1.2.3.4', 12345): '10.0.0.2'}
        self.connection_times = {}
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.last_tx = 0
        self.last_rx = 0
        self.last_time = time.time()

    def connection_made(self, transport):
        self.transport = transport
        print(f"UDP VPN Сервер слушает порт {SERVER_PORT} (Multi-User)...")
        
        self.tun = pytun.TunTapDevice(name='tun0', flags=pytun.IFF_TUN | pytun.IFF_NO_PI)
        self.tun.addr = TUN_IP
        self.tun.netmask = NETMASK
        self.tun.mtu = MTU
        self.tun.up()
        print(f"TUN интерфейс tun0 поднят с IP {TUN_IP}")

        asyncio.create_task(self.read_from_tun())
        asyncio.create_task(self.print_metrics())
        
        if sys.stdin.isatty():
            admin = AdminConsole(self)
            admin.start()

    def get_available_ip(self, addr, username):
        if addr in self.udp_to_vip:
            return self.udp_to_vip[addr]

        for i in range(2, 255):
            ip = f"10.0.0.{i}"
            if ip not in self.clients:
                self.clients[ip] = (addr[0], addr[1], username)
                self.udp_to_vip[addr] = ip
                self.connection_times[ip] = time.strftime("%Y-%m-%d %H:%M:%S")
                return ip
        return None

    def datagram_received(self, data, addr):
        if len(data) < 28: return

        try:
            nonce = data[:12]
            ciphertext = data[12:]
            plaintext = cipher.decrypt(nonce, ciphertext, None)
            
            recv_hash = plaintext[:56]
            recv_time_bytes = plaintext[56:64]
            cmd = plaintext[64]

            # Проверка пароля (UUID хеш)
            if recv_hash not in allowed_users:
                return # Неверный пароль - отбрасываем
            
            username = allowed_users[recv_hash]

            pkt_time = struct.unpack('!d', recv_time_bytes)[0]
            if abs(time.time() - pkt_time) > 30: return

            if cmd == CMD_DATA:
                if addr not in self.udp_to_vip: return
                
                ip_packet = plaintext[65:]
                src_ip, _ = extract_ips(ip_packet)
                
                expected_vip = self.udp_to_vip[addr]
                if src_ip != expected_vip: return
                
                if self.clients.get(expected_vip)[0:2] != addr:
                    print(f"[Маршрут] Клиент '{username}' ({expected_vip}) сменил адрес на {addr}")
                    self.clients[expected_vip] = (addr[0], addr[1], username)
                    self.udp_to_vip[addr] = expected_vip
                
                try:
                    self.tun.write(ip_packet)
                    self.rx_bytes += len(ip_packet)
                except Exception: pass
            
            elif cmd == CMD_PING:
                if addr not in self.udp_to_vip: return
                expected_vip = self.udp_to_vip[addr]
                
                if self.clients.get(expected_vip)[0:2] != addr:
                    print(f"[Keep-Alive] Клиент '{username}' ({expected_vip}) сменил адрес на {addr}")
                    self.clients[expected_vip] = (addr[0], addr[1], username)
                    self.udp_to_vip[addr] = expected_vip

                current_time = struct.pack('!d', time.time())
                pong_payload = recv_hash + current_time + struct.pack('B', CMD_PONG) # Используем хеш клиента!
                nonce = os.urandom(12)
                ciphertext = cipher.encrypt(nonce, pong_payload, None)
                self.transport.sendto(nonce + ciphertext, addr)

            elif cmd == CMD_IP_REQ:
                assigned_ip = self.get_available_ip(addr, username)
                if assigned_ip:
                    print(f"✅ Пользователь '{username}' получил IP {assigned_ip} ({addr})")
                    current_time = struct.pack('!d', time.time())
                    ack_payload = recv_hash + current_time + struct.pack('B', CMD_IP_ACK) + socket.inet_aton(assigned_ip) + socket.inet_aton(TUN_GW)
                    nonce = os.urandom(12)
                    ciphertext = cipher.encrypt(nonce, ack_payload, None)
                    self.transport.sendto(nonce + ciphertext, addr)

        except Exception: pass

    async def read_from_tun(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                packet = await loop.run_in_executor(None, self.tun.read, 65535)
                if packet and self.clients:
                    _, dst_ip = extract_ips(packet)
                    if dst_ip in self.clients:
                        client_data = self.clients[dst_ip]
                        client_addr = (client_data[0], client_data[1])
                        # Нам нужно отправить клиенту ЕГО хеш. Найдем его.
                        username = client_data[2]
                        user_hash = [k for k, v in allowed_users.items() if v == username][0]
                        
                        current_time = struct.pack('!d', time.time())
                        trojan_payload = user_hash + current_time + struct.pack('B', CMD_DATA) + packet
                        
                        nonce = os.urandom(12)
                        ciphertext = cipher.encrypt(nonce, trojan_payload, None)
                        frame = nonce + ciphertext
                        
                        self.transport.sendto(frame, client_addr)
                        self.tx_bytes += len(packet)
            except OSError as e:
                if e.errno == 9: break
            except Exception: break

    def format_speed(self, bps):
        mbps = bps * 8 / 1000000
        return f"{mbps:.2f} Mbps"

    async def print_metrics(self):
        while True:
            await asyncio.sleep(5)
            now = time.time()
            elapsed = now - self.last_time
            if elapsed > 0:
                tx_speed = (self.tx_bytes - self.last_tx) / elapsed
                rx_speed = (self.rx_bytes - self.last_rx) / elapsed
                online = len(self.udp_to_vip)
                print(f"[Метрики] TX: {self.format_speed(tx_speed)} | RX: {self.format_speed(rx_speed)} | Онлайн: {online}")
                self.last_tx = self.tx_bytes
                self.last_rx = self.rx_bytes
                self.last_time = now

class AdminConsole:
    def __init__(self, protocol):
        self.protocol = protocol

    def start(self):
        thread = threading.Thread(target=self.run_console, daemon=True)
        thread.start()

    def run_console(self):
        time.sleep(1)
        print("\n🛡️  VPN Admin Console запущена. Введите 'help'.")
        while True:
            try:
                cmd = input("VPN> ").strip().lower()
                if not cmd: continue
                if cmd == 'help': self.show_help()
                elif cmd == 'users' or cmd == 'list': self.show_users()
                elif cmd == 'count': print(f"\nОнлайн: {len(self.protocol.udp_to_vip)}\n")
                elif cmd.startswith('find '): self.find_user(cmd.split(' ')[1])
                elif cmd == 'reload': 
                    if load_users():
                        print("✅ База пользователей перезагружена.")
                elif cmd == 'exit':
                    import os; os._exit(0)
            except Exception: pass

    def show_help(self):
        print("\n--- Команды ---")
        print("  users       - Список онлайн")
        print("  find <ip>   - Инфо по IP")
        print("  reload      - Перезагрузить users.json без рестарта сервера")
        print("  exit        - Стоп сервер\n")

    def show_users(self):
        p = self.protocol
        if not p.udp_to_vip: print("Нет клиентов."); return
        print(f"\n--- Онлайн ({len(p.udp_to_vip)}) ---")
        for real_addr, vip in p.udp_to_vip.items():
            client_data = p.clients.get(vip)
            username = client_data[2] if client_data else "Unknown"
            conn_time = p.connection_times.get(vip, "N/A")
            print(f"  [{username}] IP: {vip} | Реальный: {real_addr[0]}:{real_addr[1]} | С: {conn_time}")
        print("------------------------\n")

    def find_user(self, vip):
        p = self.protocol
        client_data = p.clients.get(vip)
        if client_data:
            print(f"\n--- Клиент {vip} ---")
            print(f"  Имя: {client_data[2]}")
            print(f"  Реальный IP: {client_data[0]}:{client_data[1]}\n")
        else: print(f"\nКлиент {vip} не найден.\n")

async def main():
    if not load_users():
        return # Не запускаемся без юзеров
        
    loop = asyncio.get_running_loop()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024 * 8)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024 * 8)
    sock.bind(('0.0.0.0', SERVER_PORT))
    
    transport, protocol = await loop.create_datagram_endpoint(lambda: VPNServerProtocol(), sock=sock)
    
    try:
        await asyncio.Future()
    finally:
        transport.close()

if __name__ == '__main__':
    asyncio.run(main())
