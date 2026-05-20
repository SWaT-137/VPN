import asyncio
import pytun
import hashlib
import os
import socket
import struct
import time
import json
import sys
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
SERVER_SECRET = "SWaT_2008" # ДОЛЖЕН СОВПАДАТЬ С КЛИЕНТОМ!
key_material = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=b'trojan-vpn-salt',
    iterations=100000,
).derive(SERVER_SECRET.encode())
cipher = ChaCha20Poly1305(key_material)
# ------------------------

CMD_DATA = 0x00
CMD_PING = 0x01
CMD_PONG = 0x02
CMD_IP_REQ = 0x03
CMD_IP_ACK = 0x04
CMD_DISCONNECT = 0x05 # НОВАЯ КОМАНДА!

allowed_users = {}

def load_users():
    global allowed_users
    if not os.path.exists(USERS_FILE): return False
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            users_dict = json.load(f)
        allowed_users.clear()
        for uuid, username in users_dict.items():
            user_hash = hashlib.sha224(uuid.encode()).hexdigest().encode()
            allowed_users[user_hash] = username
        print(f"✅ Загружено {len(allowed_users)} пользователей.")
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
        # Новая структура: {'10.0.0.2': {'addr': ('1.2.3.4', 123), 'name': 'User', 'last_seen': 123456.78}}
        self.clients = {} 
        self.udp_to_vip = {}
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.last_tx = 0
        self.last_rx = 0
        self.last_time = time.time()

    def connection_made(self, transport):
        self.transport = transport
        print(f"UDP VPN Сервер слушает порт {SERVER_PORT} (Multi-User + Timeouts)...")
        self.tun = pytun.TunTapDevice(name='tun0', flags=pytun.IFF_TUN | pytun.IFF_NO_PI)
        self.tun.addr = TUN_IP
        self.tun.netmask = NETMASK
        self.tun.mtu = MTU
        self.tun.up()
        
        asyncio.create_task(self.read_from_tun())
        asyncio.create_task(self.print_metrics())
        asyncio.create_task(self.cleanup_dead_clients()) # Задача очистки
        
        if sys.stdin.isatty():
            AdminConsole(self).start()

    def remove_client(self, vip, reason="Отключение"):
        """Безопасное удаление клиента из всех словарей"""
        if vip in self.clients:
            username = self.clients[vip]['name']
            udp_addr = self.clients[vip]['addr']
            print(f"❌ Клиент '{username}' ({vip}) отключен. Причина: {reason}")
            del self.clients[vip]
            if udp_addr in self.udp_to_vip:
                del self.udp_to_vip[udp_addr]

    def get_available_ip(self, addr, username):
        # Если клиент перезапустился с тем же UDP адресом, очищаем старую сессию
        if addr in self.udp_to_vip:
            old_vip = self.udp_to_vip[addr]
            if old_vip in self.clients:
                self.remove_client(old_vip, "Переподключение")

        # Ищем свободный IP
        for i in range(2, 255):
            ip = f"10.0.0.{i}"
            if ip not in self.clients:
                self.clients[ip] = {'addr': addr, 'name': username, 'last_seen': time.time()}
                self.udp_to_vip[addr] = ip
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

            if recv_hash not in allowed_users: return
            username = allowed_users[recv_hash]
            pkt_time = struct.unpack('!d', recv_time_bytes)[0]
            if abs(time.time() - pkt_time) > 30: return

            if cmd == CMD_DATA:
                if addr not in self.udp_to_vip: return
                ip_packet = plaintext[65:]
                src_ip, _ = extract_ips(ip_packet)
                
                expected_vip = self.udp_to_vip[addr]
                if src_ip != expected_vip: return
                
                # Обновляем время активности
                self.clients[expected_vip]['last_seen'] = time.time()
                if self.clients[expected_vip]['addr'] != addr:
                    print(f"[Маршрут] Клиент '{username}' сменил адрес на {addr}")
                    self.clients[expected_vip]['addr'] = addr
                    self.udp_to_vip[addr] = expected_vip
                
                try:
                    self.tun.write(ip_packet)
                    self.rx_bytes += len(ip_packet)
                except Exception: pass
            
            elif cmd == CMD_PING:
                if addr not in self.udp_to_vip: return
                expected_vip = self.udp_to_vip[addr]
                self.clients[expected_vip]['last_seen'] = time.time()
                
                if self.clients[expected_vip]['addr'] != addr:
                    self.clients[expected_vip]['addr'] = addr
                    self.udp_to_vip[addr] = expected_vip

                current_time = struct.pack('!d', time.time())
                pong_payload = recv_hash + current_time + struct.pack('B', CMD_PONG)
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

            elif cmd == CMD_DISCONNECT:
                # КЛИЕНТ ЯВНО СКАЗАЛ, ЧТО УХОДИТ
                if addr in self.udp_to_vip:
                    vip = self.udp_to_vip[addr]
                    self.remove_client(vip, "Запрос на отключение от клиента")

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
                        client_addr = client_data['addr']
                        username = client_data['name']
                        user_hash = [k for k, v in allowed_users.items() if v == username][0]
                        
                        current_time = struct.pack('!d', time.time())
                        trojan_payload = user_hash + current_time + struct.pack('B', CMD_DATA) + packet
                        
                        nonce = os.urandom(12)
                        ciphertext = cipher.encrypt(nonce, trojan_payload, None)
                        self.transport.sendto(nonce + ciphertext, client_addr)
                        self.tx_bytes += len(packet)
            except OSError as e:
                if e.errno == 9: break
            except Exception: break

    async def cleanup_dead_clients(self):
        """Каждые 20 секунд проверяет, не отвалились ли клиенты"""
        while True:
            await asyncio.sleep(20)
            now = time.time()
            dead_vips = []
            # Ищем мертвых
            for vip, data in self.clients.items():
                if now - data['last_seen'] > 60: # 60 секунд тишины = смерть
                    dead_vips.append(vip)
            # Удаляем
            for vip in dead_vips:
                self.remove_client(vip, "Таймаут (нет пакетов > 60 сек)")

    def format_speed(self, bps):
        return f"{bps * 8 / 1000000:.2f} Mbps"

    async def print_metrics(self):
        while True:
            await asyncio.sleep(5)
            now = time.time()
            elapsed = now - self.last_time
            if elapsed > 0:
                tx_speed = (self.tx_bytes - self.last_tx) / elapsed
                rx_speed = (self.rx_bytes - self.last_rx) / elapsed
                print(f"[Метрики] TX: {self.format_speed(tx_speed)} | RX: {self.format_speed(rx_speed)} | Онлайн: {len(self.clients)}")
                self.last_tx = self.tx_bytes
                self.last_rx = self.rx_bytes
                self.last_time = now

class AdminConsole:
    def __init__(self, protocol): self.protocol = protocol
    def start(self):
        threading.Thread(target=self.run_console, daemon=True).start()
    def run_console(self):
        time.sleep(1)
        print("\n🛡️  VPN Admin Console. Введите 'help'.")
        while True:
            try:
                cmd = input("VPN> ").strip().lower()
                if not cmd: continue
                if cmd == 'help': print("users | find <ip> | reload | exit")
                elif cmd == 'users' or cmd == 'list': 
                    for vip, data in self.protocol.clients.items():
                        print(f"  [{data['name']}] IP: {vip} | Адрес: {data['addr'][0]}:{data['addr'][1]}")
                elif cmd.startswith('find '):
                    vip = cmd.split(' ')[1]
                    data = self.protocol.clients.get(vip)
                    if data: print(f"  {data['name']} @ {data['addr']}")
                    else: print("  Не найден")
                elif cmd == 'reload': load_users()
                elif cmd == 'exit': os._exit(0)
            except Exception: pass

async def main():
    if not load_users(): return
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024 * 8)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024 * 8)
    sock.bind(('0.0.0.0', SERVER_PORT))
    transport, protocol = await loop.create_datagram_endpoint(lambda: VPNServerProtocol(), sock=sock)
    try: await asyncio.Future()
    finally: transport.close()

if __name__ == '__main__':
    asyncio.run(main())
