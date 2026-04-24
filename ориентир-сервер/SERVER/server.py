#!/usr/bin/env python3
"""
VPN Server 2026 - Trojan Protocol (Self-Signed Adaptation)
Radmin-style VPN: Виртуальная локалка + выход в интернет (NAT)
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
VPN_NETWORK = "10.8.0.0/24"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"
TUN_NAME = "VPNServer"
CONFIG_DIR = Path("vpn_config")
CONFIG_DIR.mkdir(exist_ok=True)
PASSWORD_FILE = CONFIG_DIR / "password.hash"
CERT_FILE = CONFIG_DIR / "server.crt"
KEY_FILE = CONFIG_DIR / "server.key"
CLIENTS_FILE = CONFIG_DIR / "clients.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler('vpn_server.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

# ============== НАСТРОЙКА NAT ДЛЯ ИНТЕРНЕТА ==============
def setup_server_nat():
    """Включает IP Forwarding и настраивает NAT для выхода клиентов в интернет"""
    logger.info("[*] Configuring Internet Sharing (NAT)...")
    
    # 1. Включаем форвардинг пакетов в реестре Windows
    try:
        subprocess.run(
            'reg add "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters" /v IPEnableRouter /t REG_DWORD /d 1 /f',
            shell=True, capture_output=True, check=True
        )
    except Exception as e:
        logger.error(f"Failed to enable IP forwarding: {e}")
        return False

    # 2. Настраиваем NAT через PowerShell (работает на Windows 10/11 Pro/Enterprise)
    # Удаляем старый NAT если он был
    subprocess.run('powershell -Command "Remove-NetNat -Name VpnNat -Confirm:$false -ErrorAction SilentlyContinue"', shell=True, capture_output=True)
    
    # Создаем новый NAT для нашей подсети 10.8.0.0/24
    result = subprocess.run(
        'powershell -Command "New-NetNat -Name VpnNat -InternalIPInterfaceAddressPrefix 10.8.0.0/24"',
        shell=True, capture_output=True, text=True
    )
    
    if result.returncode == 0:
        logger.info("[+] NAT successfully configured! Clients have internet access.")
        return True
    else:
        logger.warning(f"[-] NAT setup failed (Clients will only see VPN LAN): {result.stderr.strip()}")
        return False

# ============== АУТЕНТИФИКАЦИЯ ==============
class SecureAuthManager:
    def __init__(self):
        self.password_hash: Optional[str] = None
        self.salt: Optional[bytes] = None
        self.plain_password: Optional[str] = None
        self.failed_attempts: Dict[str, List[float]] = defaultdict(list)
        self.lock = threading.RLock()
        self._load_or_create_credentials()
    
    def _load_or_create_credentials(self):
        try:
            if PASSWORD_FILE.exists():
                with open(PASSWORD_FILE, 'r') as f:
                    data = json.loads(f.read().strip())
                    self.password_hash = data.get('password_hash')
                    self.salt = bytes.fromhex(data.get('salt', ''))
                    self.plain_password = data.get('plain_password', 'mysecretpassword123')
                    logger.info("[+] Credentials loaded")
                    return
        except Exception: pass
        
        logger.info("[*] Creating new credentials...")
        password = "mysecretpassword123" # По умолчанию, поменяйте при первом запуске
        self.plain_password = password
        self.salt = secrets.token_bytes(32)
        self.password_hash = hashlib.sha256(password.encode() + self.salt).hexdigest()
        with open(PASSWORD_FILE, 'w') as f:
            json.dump({'password_hash': self.password_hash, 'salt': self.salt.hex(), 'plain_password': password}, f)

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk: break
            data += chunk
        return data
    
    def check_rate_limit(self, ip: str) -> bool:
        with self.lock:
            now = time.time()
            self.failed_attempts[ip] = [t for t in self.failed_attempts[ip] if now - t < 300]
            return len(self.failed_attempts[ip]) < 5
    
    def record_failure(self, ip: str):
        with self.lock: self.failed_attempts[ip].append(time.time())
    
    def authenticate(self, sock: socket.socket, client_ip: str) -> bool:
        if not self.check_rate_limit(client_ip): return False
        original_timeout = sock.gettimeout()
        try:
            sock.settimeout(10)
            
            # Строгий формат Trojan: 60 байт
            handshake = self._recv_exact(sock, 60)
            if len(handshake) != 60:
                self.record_failure(client_ip)
                return False
                
            # Проверка границ CRLF
            if handshake[0:2] != b'\r\n' or handshake[58:60] != b'\r\n':
                logger.warning(f"Invalid Trojan format from {client_ip}")
                self.record_failure(client_ip)
                return False
                
            received_hash = handshake[2:58].decode('ascii', errors='ignore')
            expected = hashlib.sha224((self.plain_password).encode()).hexdigest()
            
            if received_hash == expected:
                logger.info(f"[+] Client {client_ip} authenticated via Trojan")
                return True
                
            logger.warning(f"[!] Auth failed for {client_ip}")
            self.record_failure(client_ip)
            return False
        except Exception as e:
            logger.error(f"Auth error: {e}")
            return False
        finally:
            try: sock.settimeout(original_timeout)
            except: pass

# ============== УПРАВЛЕНИЕ КЛИЕНТАМИ ==============
class ClientManager:
    def __init__(self):
        self.clients: Dict[str, Dict] = {}
        self.lock = threading.RLock()
        self.next_ip = 2
        self.used_ips: Set[str] = set()
        self._load_state()
    
    def _load_state(self):
        if CLIENTS_FILE.exists():
            try:
                with open(CLIENTS_FILE, 'r') as f: data = json.load(f)
                self.used_ips = set(data.get('used_ips', []))
                self.next_ip = max(data.get('next_ip', 2), 2)
            except: pass
    
    def _save_state(self):
        try:
            with open(CLIENTS_FILE, 'w') as f: json.dump({'used_ips': list(self.used_ips), 'next_ip': self.next_ip}, f)
        except: pass
    
    def allocate_ip(self) -> Optional[str]:
        with self.lock:
            for _ in range(254):
                ip = f"10.8.0.{self.next_ip}"
                self.next_ip = (self.next_ip % 253) + 2
                if ip not in self.used_ips:
                    self.used_ips.add(ip)
                    self._save_state()
                    return ip
            return None
    
    def release_ip(self, ip: str):
        with self.lock:
            self.used_ips.discard(ip)
            self._save_state()
    
    def add_client(self, ip, handler, sock):
        with self.lock: self.clients[ip] = {'handler': handler, 'socket': sock, 'connected_at': datetime.now()}
    
    def remove_client(self, ip):
        with self.lock:
            if ip in self.clients: del self.clients[ip]
            self.release_ip(ip)
            
    def get_client(self, ip):
        with self.lock: return self.clients.get(ip)

# ============== WINTUN ОБЕРТКА ==============
class WintunWrapper:
    def __init__(self, dll_path="wintun.dll"):
        path = next((p for p in [os.path.join(os.path.dirname(__file__), dll_path), os.path.join(os.getcwd(), dll_path), dll_path] if os.path.exists(p)), None)
        if not path: raise FileNotFoundError("wintun.dll not found")
        self.dll = ctypes.WinDLL(path)
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter; self.WintunCreateAdapter.argtypes = [wintypes.LPCWSTR]*3; self.WintunCreateAdapter.restype = ctypes.c_void_p
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter; self.WintunOpenAdapter.argtypes = [wintypes.LPCWSTR]; self.WintunOpenAdapter.restype = ctypes.c_void_p
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter; self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]; self.WintunCloseAdapter.restype = None
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket; self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]; self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        self.WintunSendPacket = self.dll.WintunSendPacket; self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; self.WintunSendPacket.restype = None
        self.WintunReceivePacket = self.dll.WintunReceivePacket; self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]; self.WintunReceivePacket.restype = ctypes.c_uint32
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket; self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; self.WintunReleaseReceivePacket.restype = None

class TUNInterface:
    def __init__(self, name, ip, netmask):
        self.name, self.ip, self.netmask = name, ip, netmask
        self.handle = None; self.running = True; self.wintun = WintunWrapper(); self.lock = threading.RLock()
        self.handle = self.wintun.WintunOpenAdapter(name)
        if not self.handle:
            self.handle = self.wintun.WintunCreateAdapter(name, "Wintun", None)
            if not self.handle: raise Exception("Failed to create adapter")
        self._configure_ip()
    
    def _configure_ip(self):
        subprocess.run(f'netsh interface ip set address "{self.name}" static {self.ip} {self.netmask}', shell=True, capture_output=True)
        subprocess.run(f'netsh interface set interface "{self.name}" admin=enabled', shell=True, capture_output=True)
    
    def read(self, timeout=0.1):
        if not self.handle or not self.running: return None
        with self.lock:
            try:
                ptr, size = ctypes.c_void_p(), ctypes.c_uint32(0)
                if self.wintun.WintunReceivePacket(self.handle, ctypes.byref(ptr), ctypes.byref(size)) == 0 and ptr and size.value > 0:
                    data = ctypes.string_at(ptr, size.value)
                    self.wintun.WintunReleaseReceivePacket(self.handle, ptr)
                    return data
            except: pass
            return None
    
    def write(self, packet):
        if not self.handle or not self.running or not packet: return False
        with self.lock:
            try:
                ptr = self.wintun.WintunAllocateSendPacket(self.handle, len(packet))
                if ptr: ctypes.memmove(ptr, packet, len(packet)); self.wintun.WintunSendPacket(self.handle, ptr); return True
            except: pass
            return False
    
    def close(self):
        self.running = False
        if self.handle:
            try: self.wintun.WintunCloseAdapter(self.handle)
            except: pass

# ============== SSL СЕРТИФИКАТЫ ==============
def generate_ssl_certificate():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    
    if CERT_FILE.exists() and KEY_FILE.exists(): return
    
    logger.info(f"[*] Generating Self-Signed Cert for {SNI_DOMAIN}...")
    key = rsa.generate_private_key(65537, 2048)
    # ВАЖНО: CN и SAN совпадают с SNI из anti_dpi_engine.py!
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SNI_DOMAIN)])
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc)).not_valid_after(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=3650)).add_extension(x509.SubjectAlternativeName([x509.DNSName(SNI_DOMAIN)]), False).sign(key, hashes.SHA256())
    
    with open(KEY_FILE, "wb") as f: f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with open(CERT_FILE, "wb") as f: f.write(cert.public_bytes(serialization.Encoding.PEM))

# ============== ОБРАБОТЧИК КЛИЕНТА ==============
class ClientHandler:
    def __init__(self, sock, addr, server):
        self.sock, self.addr, self.server = sock, addr, server
        self.client_ip = addr[0]; self.assigned_ip = None; self.running = True; self.last_activity = time.time()
        self.write_lock = threading.RLock()
    
    def run(self):
        try:
            self.sock.settimeout(30.0)
            if not self.server.auth_manager.authenticate(self.sock, self.client_ip): return
            
            self.assigned_ip = self.server.client_manager.allocate_ip()
            if not self.assigned_ip: return
            
            # Отправляем IP (простой фрейм)
            self.sock.sendall(struct.pack('!H', len(self.assigned_ip)) + self.assigned_ip.encode())
            self.server.client_manager.add_client(self.assigned_ip, self, self.sock)
            logger.info(f"[+] {self.addr} -> {self.assigned_ip}")
            
            threading.Thread(target=self._send_worker, daemon=True).start()
            
            while self.running and self.server.running:
                try:
                    header = self._recv_exact(2)
                    if not header or len(header) < 2: break
                    length = struct.unpack('!H', header)[0]
                    if 0 < length < 65535:
                        data = self._recv_exact(length)
                        if not data or len(data) != length: break
                        
                        # Извлекаем реальную длину IP-пакета из наших 4 байт
                        if len(data) < 4: continue
                        real_pkt_len = struct.unpack('!I', data[:4])[0]
                        
                        if real_pkt_len > 0 and real_pkt_len <= len(data) - 4:
                            packet = data[4:4+real_pkt_len]
                            self.server.tun.write(packet)
                            self.last_activity = time.time()
                except socket.timeout: continue
                except: break
        except Exception as e: logger.error(f"Handler error: {e}")
        finally: self.cleanup()
    
    def _send_worker(self):
        q = queue.Queue(maxsize=1000)
        with self.server.send_queues_lock: self.server.send_queues[self.assigned_ip] = q
        try:
            while self.running and self.server.running:
                pkt = q.get(timeout=0.5)
                # Фрейминг: [2 байта общая длина][4 байта длина IP пакета][IP пакет][Рандомный паддинг]
                pad_len = secrets.randbelow(32) # 0-31 байт мусора против фингерпринтинга
                padding = secrets.token_bytes(pad_len)
                payload = struct.pack('!I', len(pkt)) + pkt + padding
                frame = struct.pack('!H', len(payload)) + payload
                
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
                try: q.put_nowait(pkt); return True
                except: pass
        return False
    
    def cleanup(self):
        self.running = False
        if self.assigned_ip: self.server.client_manager.remove_client(self.assigned_ip)
        try: self.sock.close()
        except: pass

# ============== ЯДРО СЕРВЕРА ==============
class VPNServer:
    def __init__(self):
        self.running = True; self.auth_manager = SecureAuthManager()
        self.client_manager = ClientManager(); self.send_queues = {}; self.send_queues_lock = threading.RLock()
        
        generate_ssl_certificate()
        
        # ВКЛЮЧАЕМ NAT ДЛЯ ВЫХОДА В ИНТЕРНЕТ
        setup_server_nat()
        
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        threading.Thread(target=self._tun_reader, daemon=True).start()
        
        logger.info(f"[*] Starting TLS Listener on {PORT}...")
        self._start_listener()
        logger.info(f"\n{'='*60}\nVPN SERVER (RADMIN-STYLE MODE)\n{'='*60}")
    
    def _start_listener(self):
        try:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM')
            ctx.options |= ssl.OP_NO_COMPRESSION
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((HOST, PORT))
            sock.listen(100)
            
            logger.info(f"[+] ✅ СЕРВЕР ЗАПУЩЕН И ЖДЕТ ПОДКЛЮЧЕНИЙ НА ПОРТУ {PORT}")
            
            while self.running:
                try:
                    sock.settimeout(1.0)
                    c, a = sock.accept()  # Принимаем обычный сокет
                    
                    try:
                        ssl_conn = ctx.wrap_socket(c, server_side=True, do_handshake_on_connect=True)
                    except Exception as e:
                        logger.warning(f"SSL handshake failed: {e}")
                        c.close()
                        continue
                    
                    threading.Thread(target=ClientHandler(ssl_conn, a, self).run, daemon=True).start()
                except socket.timeout: 
                    continue
                except Exception as e:
                    logger.error(f"Accept error: {e}")
                    continue
        except Exception as e: 
            logger.error(f"Listen error: {e}")

    
    def _tun_reader(self):
        # Читает пакеты из виртуалки (от клиента или от NAT в ответ на интернет-запрос)
        while self.running:
            pkt = self.tun.read(0.1)
            if pkt and len(pkt) >= 20:
                try:
                    dst_ip = socket.inet_ntoa(pkt[16:20])
                    
                    # Если пакет предназначен другому клиенту виртуалки (10.8.0.x)
                    c = self.client_manager.get_client(dst_ip)
                    if c and c['handler']: 
                        c['handler'].send_packet(pkt)
                    # Иначе это интернет-трафик (NAT сам отправит его через физ. интерфейс сервера)
                    # Пакеты от интернета обратно клиенту вернутся сюда через NAT с dst_ip = 10.8.0.x
                except: pass
    
    def stop(self):
        self.running = False; self.tun.close()

def check_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return False

if __name__ == "__main__":
    if not check_admin(): print("[!] Run as Admin"); sys.exit(1)
    if not os.path.exists("wintun.dll"): print("[!] wintun.dll missing"); sys.exit(1)
    
    server = VPNServer()
    try:
        signal.signal(signal.SIGINT, lambda s,f: server.stop())
        while server.running: time.sleep(1)
    except KeyboardInterrupt: pass
    finally: server.stop()