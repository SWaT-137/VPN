#!/usr/bin/env python3
"""
VPN Server 2026 - Trojan Protocol
E2E ENCRYPTION + Безопасное отключение клиентов
"""
import socket
import ssl
import struct
import threading
import hashlib
import os
import time
import sys
import ctypes
import secrets
import json
import logging
import signal
import ipaddress
import queue
import subprocess
from ctypes import wintypes
from datetime import datetime
from typing import Optional, Dict, Set, List, Tuple
from pathlib import Path
from collections import defaultdict

from anti_dpi_engine import AntiDPIEngine, SNI_DOMAIN

# ============== КОНФИГУРАЦИЯ ==============
HOST = "0.0.0.0"
PORT = 1443
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"
TUN_NAME = "VPNServer"
CONFIG_DIR = Path("vpn_config")
CONFIG_DIR.mkdir(exist_ok=True)
CERT_FILE = CONFIG_DIR / "server.crt"
KEY_FILE = CONFIG_DIR / "server.key"
CLIENTS_FILE = CONFIG_DIR / "clients_ips.json"
CLIENTS_KEYS_DB = CONFIG_DIR / "clients_keys.json"
NET_KEY_FILE = CONFIG_DIR / "network_key.bin"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler('vpn_server.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

FLAG_RAW_INTERNET = 0x00
FLAG_E2E_LAN = 0x01

# ============== УПРАВЛЕНИЕ КЛИЕНТАМИ И КЛЮЧАМИ ==============
class ClientKeysManager:
    def __init__(self):
        self.db: Dict[str, str] = {}
        self.lock = threading.RLock()
        self._load_db()
    def _load_db(self):
        if CLIENTS_KEYS_DB.exists():
            try: self.db = json.load(open(CLIENTS_KEYS_DB, 'r'))
            except: pass
    def _save_db(self):
        with open(CLIENTS_KEYS_DB, 'w') as f: json.dump(self.db, f, indent=4)
    def add_client(self, name: str) -> Optional[str]:
        with self.lock:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            self.db[token_hash] = name
            self._save_db()
            return token
    def remove_client(self, name: str) -> bool:
        with self.lock:
            hashes_to_remove = [h for h, n in self.db.items() if n.lower() == name.lower()]
            if hashes_to_remove:
                for h in hashes_to_remove: del self.db[h]
                self._save_db()
                return True
        return False
    def get_client_name(self, token_hash: str) -> Optional[str]:
        with self.lock: return self.db.get(token_hash)

def setup_server_nat():
    logger.info("[*] Configuring Internet Sharing (NAT)...")
    try: subprocess.run('reg add "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters" /v IPEnableRouter /t REG_DWORD /d 1 /f', shell=True, capture_output=True, check=True)
    except: return False
    subprocess.run('powershell -Command "Remove-NetNat -Name VpnNat -Confirm:$false -ErrorAction SilentlyContinue"', shell=True, capture_output=True)
    result = subprocess.run('powershell -Command "New-NetNat -Name VpnNat -InternalIPInterfaceAddressPrefix 10.8.0.0/24"', shell=True, capture_output=True, text=True)
    return result.returncode == 0

class SecureAuthManager:
    def __init__(self, keys_manager: ClientKeysManager):
        self.keys_manager = keys_manager
        self.failed_attempts: Dict[str, List[float]] = defaultdict(list)
        self.lock = threading.RLock()
    def _recv_exact(self, sock, n):
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk: break
            data += chunk
        return data
    def check_rate_limit(self, ip): 
        with self.lock:
            now = time.time()
            self.failed_attempts[ip] = [t for t in self.failed_attempts[ip] if now - t < 300]
            return len(self.failed_attempts[ip]) < 5
    def record_failure(self, ip): 
        with self.lock: self.failed_attempts[ip].append(time.time())
    def authenticate(self, sock, client_ip):
        if not self.check_rate_limit(client_ip): return None
        orig = sock.gettimeout()
        try:
            sock.settimeout(10)
            h = self._recv_exact(sock, 68)
            if len(h) != 68 or h[0:2] != b'\r\n' or h[66:68] != b'\r\n':
                self.record_failure(client_ip); return None
            rh = h[2:66].decode('ascii', errors='ignore')
            name = self.keys_manager.get_client_name(rh)
            if name: logger.info(f"[+] Client '{name}' authenticated"); return name
            self.record_failure(client_ip); return None
        except: return None
        finally:
            try: sock.settimeout(orig)
            except: pass

class ClientManager:
    def __init__(self):
        self.clients = {}; self.lock = threading.RLock(); self.next_ip = 2; self.used_ips = set()
        if CLIENTS_FILE.exists():
            try:
                d = json.load(open(CLIENTS_FILE, 'r'))
                self.used_ips = set(d.get('ips', [])); self.next_ip = max(d.get('next', 2), 2)
            except: pass
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

# ============== WINTUN ==============
class WintunWrapper:
    def __init__(self, dll_path="wintun.dll"):
        path = next((p for p in [os.path.join(os.path.dirname(__file__), dll_path), os.path.join(os.getcwd(), dll_path), dll_path] if os.path.exists(p)), None)
        if not path: raise FileNotFoundError("wintun.dll not found")
        self.dll = ctypes.WinDLL(path)
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter; self.WintunCreateAdapter.argtypes = [wintypes.LPCWSTR]*3; self.WintunCreateAdapter.restype = ctypes.c_void_p
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter; self.WintunOpenAdapter.argtypes = [wintypes.LPCWSTR]; self.WintunOpenAdapter.restype = ctypes.c_void_p
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter; self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]; self.WintunCloseAdapter.restype = None
        self.WintunStartSession = self.dll.WintunStartSession; self.WintunStartSession.argtypes = [ctypes.c_void_p, ctypes.c_uint32]; self.WintunStartSession.restype = ctypes.c_void_p
        self.WintunEndSession = self.dll.WintunEndSession; self.WintunEndSession.argtypes = [ctypes.c_void_p]; self.WintunEndSession.restype = None
        self.WintunGetReadWaitEvent = self.dll.WintunGetReadWaitEvent; self.WintunGetReadWaitEvent.argtypes = [ctypes.c_void_p]; self.WintunGetReadWaitEvent.restype = ctypes.c_void_p
        self.WintunReceivePacket = self.dll.WintunReceivePacket; self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]; self.WintunReceivePacket.restype = ctypes.c_uint32
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket; self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; self.WintunReleaseReceivePacket.restype = None
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket; self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]; self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        self.WintunSendPacket = self.dll.WintunSendPacket; self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; self.WintunSendPacket.restype = None
        try: self.WintunFreeAdapter = self.dll.WintunFreeAdapter; self.WintunFreeAdapter.argtypes = [ctypes.c_void_p]; self.WintunFreeAdapter.restype = None
        except: self.WintunFreeAdapter = None

class TUNInterface:
    def __init__(self, name, ip, netmask):
        self.name, self.ip, self.netmask = name, ip, netmask
        self.adapter_handle = None; self.session_handle = None; self.read_event = None
        self.running = True; self.wintun = WintunWrapper(); self.lock = threading.RLock()
        self.adapter_handle = self.wintun.WintunOpenAdapter(name)
        if not self.adapter_handle:
            self.adapter_handle = self.wintun.WintunCreateAdapter(name, "Wintun", None)
            if not self.adapter_handle: raise Exception("Failed to create adapter")
        self.session_handle = self.wintun.WintunStartSession(self.adapter_handle, 0x400000)
        if not self.session_handle: raise Exception("Failed to start Wintun session")
        self.read_event = self.wintun.WintunGetReadWaitEvent(self.session_handle)
        self._configure_ip()
    
    def _configure_ip(self):
        subprocess.run(f'netsh interface ip set address "{self.name}" static {self.ip} {self.netmask}', shell=True, capture_output=True)
        subprocess.run(f'netsh interface set interface "{self.name}" admin=enabled', shell=True, capture_output=True)
    
    def read(self, timeout=0.5):
        if not self.session_handle or not self.running: return None
        try:
            wait_result = ctypes.windll.kernel32.WaitForSingleObject(self.read_event, int(timeout * 1000))
            if wait_result == 0:
                packet_ptr = ctypes.c_void_p(); packet_size = ctypes.c_uint32(0)
                res = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
                if res == 0 and packet_ptr.value and packet_size.value > 0:
                    data = ctypes.string_at(packet_ptr, packet_size.value)
                    self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                    return data
        except: pass
        return None
    
    def write(self, packet):
        if not self.session_handle or not self.running or not packet: return False
        try:
            ptr = self.wintun.WintunAllocateSendPacket(self.session_handle, len(packet))
            if ptr and ptr != 0: 
                ctypes.memmove(ptr, packet, len(packet))
                self.wintun.WintunSendPacket(self.session_handle, ptr)
                return True
        except: pass
        return False
    
    def close(self):
        self.running = False
        if self.session_handle: self.wintun.WintunEndSession(self.session_handle)
        if self.adapter_handle: self.wintun.WintunCloseAdapter(self.adapter_handle)
        if self.wintun.WintunFreeAdapter and self.adapter_handle: self.wintun.WintunFreeAdapter(self.adapter_handle)

# ============== SSL ==============
def generate_ssl_certificate():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    if CERT_FILE.exists() and KEY_FILE.exists(): return
    logger.info(f"[*] Generating Self-Signed Cert for {SNI_DOMAIN}...")
    key = rsa.generate_private_key(65537, 2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SNI_DOMAIN)])
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc)).not_valid_after(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=3650)).add_extension(x509.SubjectAlternativeName([x509.DNSName(SNI_DOMAIN)]), False).sign(key, hashes.SHA256())
    with open(KEY_FILE, "wb") as f: f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with open(CERT_FILE, "wb") as f: f.write(cert.public_bytes(serialization.Encoding.PEM))

# ============== ОБРАБОТЧИК КЛИЕНТА ==============
class ClientHandler:
    def __init__(self, sock, addr, server):
        self.sock, self.addr, self.server = sock, addr, server
        self.client_ip = addr[0]; self.assigned_ip = None; self.client_name = "Unknown"; self.running = True
        self.write_lock = threading.RLock()
    
    def run(self):
        try:
            self.sock.settimeout(30.0)
            name = self.server.auth_manager.authenticate(self.sock, self.client_ip)
            if not name: return
            self.client_name = name
            self.assigned_ip = self.server.client_manager.allocate_ip()
            if not self.assigned_ip: return
            
            self.sock.sendall(struct.pack('!H', len(self.assigned_ip)) + self.assigned_ip.encode())
            self.sock.sendall(self.server.network_key)
            
            self.server.client_manager.add_client(self.assigned_ip, self, self.sock, self.client_name)
            logger.info(f"[+] {self.client_name} -> {self.assigned_ip} (E2E Enabled)")
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
                    # Извлекаем точную длину полезной нагрузки (без мусора)
                    inner_len = struct.unpack('!I', data[:4])[0]
                    inner_payload = data[4:4+inner_len]
                    # padding остается за пределами inner_payload и безопасно игнорируется
                    
                    if not inner_payload: continue
                    flag = inner_payload[0]
                    payload = inner_payload[1:]
                    
                    if flag == FLAG_RAW_INTERNET:
                        self.server.tun.write(payload)
                    elif flag == FLAG_E2E_LAN:
                        if len(payload) >= 4:
                            dst_vpn_ip = socket.inet_ntoa(payload[:4])
                            encrypted_data = payload[4:]
                            c = self.server.client_manager.get_client(dst_vpn_ip)
                            if c and c['handler']:
                                c['handler'].send_e2e_packet(encrypted_data)
                except socket.timeout: continue
                except: break
        except Exception as e: logger.error(f"Handler error ({self.client_name}): {e}")
        finally: self.cleanup()
    
    def _send_worker(self):
        q = queue.Queue(maxsize=1000)
        with self.server.send_queues_lock: self.server.send_queues[self.assigned_ip] = q
        try:
            while self.running and self.server.running:
                inner_payload = q.get(timeout=0.5)
                
                # Формируем фрейм: [2 байта длина][4 байта inner_len][inner_payload][Паддинг]
                inner_len_bytes = struct.pack('!I', len(inner_payload))
                pad_len = secrets.randbelow(32)
                padding = secrets.token_bytes(pad_len)
                
                frame_payload = inner_len_bytes + inner_payload + padding
                frame = struct.pack('!H', len(frame_payload)) + frame_payload
                
                with self.write_lock: self.sock.sendall(frame)
        except: pass
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

    def send_e2e_packet(self, encrypted_data):
        if not self.running: return False
        with self.server.send_queues_lock:
            q = self.server.send_queues.get(self.assigned_ip)
            if q:
                try: q.put_nowait(bytes([FLAG_E2E_LAN]) + encrypted_data); return True
                except: pass
        return False
    
    def cleanup(self):
        self.running = False
        logger.info(f"[-] {self.client_name} disconnected")
        if self.assigned_ip: self.server.client_manager.remove_client(self.assigned_ip)
        try: self.sock.close()
        except: pass

# ============== ЯДРО СЕРВЕРА ==============
class VPNServer:
    def __init__(self):
        self.running = True
        self.listen_sock = None # Сохраняем сокет для корректного выключения
        self.keys_manager = ClientKeysManager()
        self.auth_manager = SecureAuthManager(self.keys_manager)
        self.client_manager = ClientManager()
        self.send_queues = {}; self.send_queues_lock = threading.RLock()
        
        self.network_key = self._load_or_generate_net_key()
        
        generate_ssl_certificate()
        setup_server_nat()
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        threading.Thread(target=self._tun_reader, daemon=True).start()
        self._start_listener()

    def _load_or_generate_net_key(self):
        if NET_KEY_FILE.exists():
            with open(NET_KEY_FILE, 'rb') as f: return f.read()
        key = secrets.token_bytes(32)
        with open(NET_KEY_FILE, 'wb') as f: f.write(key)
        logger.info("[+] Generated new Network E2E Key")
        return key

    def _start_listener(self):
        try:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM')
            ctx.options |= ssl.OP_NO_COMPRESSION
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((HOST, PORT)); sock.listen(100)
            self.listen_sock = sock # Сохраняем
            logger.info(f"[+] ✅ СЕРВЕР ЗАПУЩЕН (E2E ENCRYPTED) НА ПОРТУ {PORT}")
            while self.running:
                try:
                    sock.settimeout(1.0)
                    c, a = sock.accept()
                    c.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 5000, 1000))
                    try: ssl_conn = ctx.wrap_socket(c, server_side=True, do_handshake_on_connect=True)
                    except Exception as e: logger.warning(f"SSL fail: {e}"); c.close(); continue
                    threading.Thread(target=ClientHandler(ssl_conn, a, self).run, daemon=True).start()
                except socket.timeout: continue
                except Exception as e: logger.error(f"Accept error: {e}")
        except Exception as e: logger.error(f"Listen error: {e}")

    def _tun_reader(self):
        while self.running:
            pkt = self.tun.read(timeout=0.5)
            if pkt and len(pkt) >= 20:
                try:
                    dst_ip = socket.inet_ntoa(pkt[16:20])
                    c = self.client_manager.get_client(dst_ip)
                    if c and c['handler']: c['handler'].send_packet(pkt)
                except: pass
    
    def stop(self):
        self.running = False
        # 1. Закрываем слушающий сокет, чтобы остановить цикл accept
        if self.listen_sock:
            try: self.listen_sock.close()
            except: pass
        # 2. ЖЕСТКО обрываем все клиентские сокеты, чтобы они мгновенно получили RST
        with self.client_manager.lock:
            for ip, info in list(self.client_manager.clients.items()):
                try: info['socket'].close()
                except: pass
        # 3. Закрываем TUN
        self.tun.close()

def check_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return False

if __name__ == "__main__":
    if not check_admin(): print("[!] Run as Admin"); sys.exit(1)
    if not os.path.exists("wintun.dll"): print("[!] wintun.dll missing"); sys.exit(1)
    
    keys_mgr = ClientKeysManager()
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "--add-client" and len(sys.argv) == 3:
            token = keys_mgr.add_client(sys.argv[2])
            if token: print(f"\n[+] Токен для '{sys.argv[2]}':\n{token}\n")
        elif cmd == "--del-client" and len(sys.argv) == 3:
            if keys_mgr.remove_client(sys.argv[2]): print(f"[+] Удален.")
        elif cmd == "--list-clients":
            for h, n in keys_mgr.db.items(): print(f" - {n}")
        elif cmd == "--regen-key":
            if NET_KEY_FILE.exists(): os.remove(NET_KEY_FILE)
            print("[+] Ключ сети сброшен. Перезапустите сервер.")
        else: print("Используйте --add-client, --del-client, --list-clients, --regen-key")
        sys.exit(0)
        
    if len(keys_mgr.db) == 0:
        print("\n[!] База пуста! Добавьте: python server.py --add-client Имя\n"); sys.exit(1)

    server = VPNServer()
    try:
        signal.signal(signal.SIGINT, lambda s,f: server.stop())
        while server.running: time.sleep(1)
    except KeyboardInterrupt: pass
    finally: server.stop()