#!/usr/bin/env python3
import socket, ssl, struct, threading, hashlib, os, time, sys, fcntl, secrets, json, logging, signal, subprocess, queue, select
from typing import Optional, Dict, List
from pathlib import Path
from collections import defaultdict
from anti_dpi_engine import AntiDPIEngine
from database import VpnDatabase 

HOST = "0.0.0.0" # ИСПРАВЛЕНО (было 127.0.0.0.0)
PORT = 1443
VPN_SERVER_IP = "10.8.0.1"
TUN_NAME = "tun0"
CONFIG_DIR = Path("vpn_config")
CONFIG_DIR.mkdir(exist_ok=True)
CLIENTS_FILE = CONFIG_DIR / "clients_ips.json"
CLIENTS_KEYS_DB = CONFIG_DIR / "clients_keys.json"
NET_KEY_FILE = CONFIG_DIR / "network_key.bin"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler('vpn_server.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

FLAG_RAW_INTERNET = 0x00
FLAG_E2E_LAN = 0x01

def get_server_public_ips() -> List[str]:
    """Безопасное получение реальных внешних IP сервера"""
    ips_list = []
    try:
        res = subprocess.run("ip -4 -j addr show", shell=True, capture_output=True, text=True)
        if res.stdout:
            try:
                data = json.loads(res.stdout)
                for item in data:
                    if item.get('addr_info'):
                        for addr in item['addr_info']:
                            if addr.get('scope') == "global":
                                ip = addr['address']
                                if ip and not ip.startswith("127.") and not ip.startswith("10.") and not ip.startswith("172.16.") and not ip.startswith("192.168."):
                                    ips_list.append(ip)
            except Exception:
                pass

    if not ips_list:
        try:
            res = subprocess.run("ip -4 addr show | grep -oP '\\b\\d+\\.\\d+\\.\\d+\\.\\d+\\b'", shell=True, capture_output=True, text=True)
            for ip_str in res.stdout.strip().splitlines():
                ip = ip_str.strip()
                if ip and not ip.startswith("127.") and not ip.startswith("10.") and not ip.startswith("172.16.") and not ip.startswith("192.168."):
                    ips_list.append(ip)
        except Exception:
            pass
    return ips_list

class ClientKeysManager:
    def __init__(self):
        self.db = VpnDatabase()
        self.lock = threading.RLock()
        self.keys = self._load_json()
    
    def _load_json(self):
        if CLIENTS_KEYS_DB.exists():
            try: return json.load(open(CLIENTS_KEYS_DB, 'r'))
            except: pass
        return {}

    def add_client(self, name: str) -> Optional[str]:
        with self.lock:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            self.keys[token_hash] = name
            with open(CLIENTS_KEYS_DB, 'w') as f: json.dump(self.keys, f, indent=4)
            self.db.add_user(name, token_hash) # Синхронизация с БД для веб-панели
            return token
    
    def remove_client(self, name: str) -> bool:
        with self.lock:
            hashes = [h for h, n in self.keys.items() if n.lower() == name.lower()]
            if hashes:
                for h in hashes: del self.keys[h]
                with open(CLIENTS_KEYS_DB, 'w') as f: json.dump(self.keys, f, indent=4)
                self.db.delete_user(name)
                return True
        return False
    
    def get_client_name(self, token_hash: str) -> Optional[str]:
        return self.keys.get(token_hash)


class ClientManager:
    def __init__(self):
        self.clients = {}; self.lock = threading.RLock(); self.next_ip = 2; self.used_ips = set()
        if CLIENTS_FILE.exists():
            try:
                d = json.load(open(CLIENTS_FILE, 'r'))
                self.used_ips = set(d.get('ips', [])); self.next_ip = max(d.get('next', 2), 2)
            except Exception: pass
    def _save(self):
        with open(CLIENTS_FILE, 'w') as f: json.dump({'ips': list(self.used_ips), 'next': self.next_ip}, f)
    def allocate_ip(self):
        with self.lock:
            for _ in range(254):
                ip = f"10.8.0.{self.next_ip}"; self.next_ip = (self.next_ip % 253) + 2
                if ip not in self.used_ips: self.used_ips.add(ip); self._save(); return ip
    def release_ip(self, ip):
        with self.lock: self.used_ips.discard(ip); self._save()
    def add_client(self, ip, handler, sock, name):
        with self.lock: self.clients[ip] = {'handler': handler, 'socket': sock, 'name': name}
    def remove_client(self, ip):
        with self.lock:
            if ip in self.clients: del self.clients[ip]
            self.release_ip(ip)
    def get_client(self, ip):
        with self.lock: return self.clients.get(ip)


class LinuxTUN:
    def __init__(self, name, ip, public_ips=None): # ИСПРАВЛЕНО: добавлен public_ips
        self.name = name; self.ip = ip; self.fd = None; self.running = True
        public_ips = public_ips or []
        
        subprocess.run(f'ip link set down {self.name} 2>/dev/null', shell=True, capture_output=True)
        time.sleep(0.1)
        subprocess.run(f'ip link delete {self.name} 2>/dev/null', shell=True, capture_output=True)
        time.sleep(0.1)
        
        self.fd = os.open("/dev/net/tun", os.O_RDWR)
        if not self.fd:
            logger.error("[!] Не удалось открыть /dev/net/tun.")
            return
        ifr = struct.pack("16sH", self.name.encode(), 0x0001 | 0x1000)
        fcntl.ioctl(self.fd, 0x400454ca, ifr)
        logger.info(f"[+] TUN {self.name} создан")
        
        subprocess.run('sysctl -w net.ipv4.ip_forward=1', shell=True, capture_output=True)
        subprocess.run(f'ip addr add {self.ip}/24 dev {self.name}', shell=True, capture_output=True)
        subprocess.run(f'ip link set {self.name} up', shell=True, capture_output=True)
        subprocess.run(f'ip link set {self.name} mtu 1420', shell=True, capture_output=True)
        
        main_iface = None
        res = subprocess.run("ip route show default", shell=True, capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if "dev " in line:
                parts = line.split()
                iface = parts[parts.index("dev") + 1]
                if not any(x in iface for x in ["docker", "br-", "veth", "lo", self.name]):
                    main_iface = iface; break
        
        if not main_iface:
            logger.error("[!] КРИТИЧЕСКАЯ ОШИБКА: Не найден реальный интерфейс!"); return
            
        logger.info(f"[+] Реальный интерфейс: {main_iface}")
        subprocess.run(f'iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o {main_iface} -j MASQUERADE 2>/dev/null', shell=True, capture_output=True)
        subprocess.run(f'iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o {main_iface} -j MASQUERADE', shell=True, capture_output=True)
        subprocess.run(f'iptables -I FORWARD 1 -i {self.name} -j ACCEPT', shell=True, capture_output=True)
        subprocess.run(f'iptables -I FORWARD 2 -o {self.name} -m state --state ESTABLISHED,RELATED -j ACCEPT', shell=True, capture_output=True)
        
        for pub_ip in public_ips:
            subprocess.run(f'iptables -I INPUT -i {self.name} -d {pub_ip} -j DROP', shell=True, capture_output=True)
            
        subprocess.run(f'iptables -t mangle -A POSTROUTING -o {main_iface} -p tcp -m tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu', shell=True, capture_output=True)
        logger.info(f"[+] NAT и маршрутизация настроены")

    def read(self, timeout=0.5):
        if not self.running or not self.fd: return None
        try:
            r, _, _ = select.select([self.fd], [], [], timeout)
            if r: return os.read(self.fd, 65535)
        except: pass
        return None

    def write(self, packet):
        if not self.running or not self.fd or not packet: return False
        try: os.write(self.fd, packet); return True
        except Exception as e: logger.error(f"[!] ОШИБКА ЗАПИСИ В TUN0: {e}"); return False
            
    def close(self):
        self.running = False
        if self.fd: os.close(self.fd)
        subprocess.run(f'ip link set down {self.name} 2>/dev/null', shell=True, capture_output=True)
        subprocess.run(f'ip link delete {self.name} 2>/dev/null', shell=True, capture_output=True)


class SecureAuthManager:
    def __init__(self, km): self.km = km
    def _recv_exact(self, sock, n):
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk: break
            data += chunk
        return data
    
    def authenticate(self, sock, ip):
        orig = sock.gettimeout()
        try:
            sock.settimeout(10)
            h = self._recv_exact(sock, 68)
            if len(h) != 68 or h[0:2] != b'\r\n' or h[66:68] != b'\r\n': return None
            name = self.km.get_client_name(h[2:66].decode('ascii', errors='ignore'))
            return name
        except: return None
        finally:
            try: sock.settimeout(orig)
            except: pass


class ClientHandler:
    def __init__(self, sock, addr, server):
        self.sock, self.addr, self.server = sock, addr, server
        self.client_ip = addr[0]; self.assigned_ip = None; self.client_name = "Unknown"; self.running = True; self.write_lock = threading.RLock()
        
    def run(self):
        try:
            self.sock.settimeout(30.0)
            name = self.server.auth_manager.authenticate(self.sock, self.client_ip)
            if not name: return
            self.client_name = name
            self.assigned_ip = self.server.client_manager.allocate_ip()
            if not self.assigned_ip: return
            
            # ИСПРАВЛЕНО: Убрана лишняя точка
            self.sock.sendall(struct.pack('!H', len(self.assigned_ip)) + self.assigned_ip.encode())
            
            # ИСПРАВЛЕНО: Отправка публичных IP (Ожидал клиент)
            public_ips_str = ",".join(self.server.public_ips)
            self.sock.sendall(struct.pack('!H', len(public_ips_str)) + public_ips_str.encode())
            
            self.sock.sendall(self.server.network_key)
            self.server.client_manager.add_client(self.assigned_ip, self, self.sock, self.client_name)
            
            # Запись в БД для веб-панели
            self.server.db.create_session(self.client_name, self.assigned_ip)
            
            logger.info(f"[+] {self.client_name} подключился -> {self.assigned_ip}")
            threading.Thread(target=self._send_worker, daemon=True).start()
            
            while self.running and self.server.running:
                try:
                    header = self._recv_exact(2)
                    if not header or len(header) < 2: break
                    length = struct.unpack('!H', header)[0]
                    if not (0 < length < 65535): break
                    data = self._recv_exact(length)
                    if not data or len(data) != length: break
                    
                    if len(data) < 4: continue
                    
                    inner_len = struct.unpack('!I', data[:4])[0]
                    inner_payload = data[4:4+inner_len]
                    if not inner_payload: continue
                    
                    flag, payload = inner_payload[0], inner_payload[1:]
                    
                    if flag == FLAG_RAW_INTERNET:
                        self.server.tun.write(payload)
                        # Статистика в БД (Входящий трафик)
                        self.server.db.update_stats(self.assigned_ip, length, 0)
                    elif flag == FLAG_E2E_LAN and len(payload) >= 4:
                        c = self.server.client_manager.get_client(socket.inet_ntoa(payload[:4]))
                        if c and c['handler']: c['handler'].send_e2e_packet(payload[4:])
                except socket.timeout: continue
                except: break
        except Exception as e: 
            logger.error(f"Err: {e}")
        finally: 
            self.cleanup()
            
    def _send_worker(self):
        q = queue.Queue(maxsize=1000)
        with self.server.send_queues_lock: self.server.send_queues[self.assigned_ip] = q
        try:
            while self.running and self.server.running:
                try: inner = q.get(timeout=0.5)
                except queue.Empty: continue
                pad = secrets.randbelow(32)
                payload = struct.pack('!I', len(inner)) + inner + secrets.token_bytes(pad)
                
                pkt_size = len(payload) + 2 # Размер фрейма для статистики
                with self.write_lock: 
                    self.sock.sendall(struct.pack('!H', len(payload)) + payload)
                
                # Статистика в БД (Исходящий трафик)
                self.server.db.update_stats(self.assigned_ip, 0, pkt_size)
                
        except Exception as e:
            logger.error(f"[!] ОШИБКА ОТПРАВКИ ДЛЯ {self.assigned_ip}: {e}")
        finally:
            with self.server.send_queues_lock: self.server.send_queues.pop(self.assigned_ip, None)
            
    def _recv_exact(self, n):
        data = b''
        while len(data) < n and self.running:
            try:
                chunk = self.sock.recv(n - len(data))
                if not chunk: return b''
                data += chunk
            except socket.timeout: continue
            except: return b''
        return data
        
    def send_packet(self, pkt):
        if not self.running: return False
        with self.server.send_queues_lock:
            q = self.server.send_queues.get(self.assigned_ip)
            if q:
                try: q.put_nowait(bytes([FLAG_RAW_INTERNET]) + pkt); return True
                except: pass
        return False
    
    def send_e2e_packet(self, data):
        if not self.running: return False
        with self.server.send_queues_lock:
            q = self.server.send_queues.get(self.assigned_ip)
            if q:
                try: q.put_nowait(bytes([FLAG_E2E_LAN]) + data); return True
                except: pass
        return False
    
    def cleanup(self):
        self.running = False
        logger.info(f"[-] {self.client_name} отключился")
        if self.assigned_ip: 
            self.server.db.remove_session(self.assigned_ip) # Удаление сессии из БД
            self.server.client_manager.remove_client(self.assigned_ip)
        try: self.sock.close()
        except: pass


class VPNServer:
    def __init__(self):
        self.running = True; self.listen_sock = None
        self.auth_manager = SecureAuthManager(ClientKeysManager())
        self.client_manager = ClientManager()
        self.send_queues = {}; self.send_queues_lock = threading.RLock()
        self.db = VpnDatabase() 
        
        self.public_ips = get_server_public_ips()
        logger.info(f"[+] Публичные IP сервера: {', '.join(self.public_ips) if self.public_ips else 'Не найдено'}")
        
        if NET_KEY_FILE.exists():
            with open(NET_KEY_FILE, 'rb') as f: self.network_key = f.read()
        else:
            self.network_key = secrets.token_bytes(32)
            with open(NET_KEY_FILE, 'wb') as f: f.write(self.network_key)
            
        self.tun = LinuxTUN(TUN_NAME, VPN_SERVER_IP, self.public_ips)
        threading.Thread(target=self._tun_reader, daemon=True).start()
        self._start_listener()
        
    def _start_listener(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((HOST, PORT)); sock.listen(100)
            self.listen_sock = sock
            logger.info(f"[+] ✅ GHOST SERVER ЗАПУЩЕН НА {HOST}:{PORT}")
            while self.running:
                try:
                    sock.settimeout(1.0)
                    c, a = sock.accept()
                    ssl_conn = AntiDPIEngine(is_server=True).wrap_socket(c)
                    threading.Thread(target=ClientHandler(ssl_conn, a, self).run, daemon=True).start()
                except socket.timeout: continue
                except Exception as e: logger.error(f"Err: {e}")
        except Exception as e: logger.error(f"Err: {e}")
        
    def _tun_reader(self):
        while self.running:
            pkt = self.tun.read(timeout=0.5)
            if pkt and len(pkt) >= 20:
                try:
                    dst_ip_bytes = pkt[16:20]
                    src_ip_bytes = pkt[12:16]

                    if not src_ip_bytes.startswith(b'\x0a\x08\x00'): 
                        continue

                    c = self.client_manager.get_client(socket.inet_ntoa(dst_ip_bytes))
                    if c and c['handler']: c['handler'].send_packet(pkt)
                except Exception:
                    pass
                    
    def stop(self):
        self.running = False
        if self.listen_sock:
            try: self.listen_sock.close()
            except: pass
        with self.client_manager.lock:
            for ip, info in list(self.client_manager.clients.items()):
                try: info['socket'].close()
                except: pass
        self.tun.close()

if __name__ == "__main__":
    km = ClientKeysManager()
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "--add-client" and len(sys.argv) == 3:
            token = km.add_client(sys.argv[2])
            if token: print(f"\n[+] Токен для '{sys.argv[2]}':\n{token}\n")
        elif cmd == "--del-client" and len(sys.argv) == 3:
            if km.remove_client(sys.argv[2]): print(f"[+] Удален.")
        elif cmd == "--list-clients":
            for h, n in km.keys.items(): print(f" - {n}")
        else: print("Используйте --add-client, --del-client, --list-clients")
        sys.exit(0)
    if len(km.keys) == 0:
        print("\n[!] База пуста! Добавьте: python3 server.py --add-client Имя\n"); sys.exit(1)
    server = VPNServer()
    try:
        signal.signal(signal.SIGINT, lambda s,f: server.stop())
        while server.running: time.sleep(1)
    except KeyboardInterrupt: pass
    finally: server.stop()