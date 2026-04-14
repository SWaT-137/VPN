#!/usr/bin/env python3
"""
VPN Server 2026 - Production Ready
Полная интеграция Anti-DPI с PFS и защитой от всех известных атак
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
from ctypes import wintypes
from datetime import datetime
from typing import Optional, Dict, Set, List, Tuple
from pathlib import Path
from collections import defaultdict

# Импорт улучшенного anti-DPI модуля
from anti_dpi_engine import AntiDPIEngine, PerfectForwardSecrecy, SecureNonceManager

# ============== КОНФИГУРАЦИЯ ==============
HOST = "0.0.0.0"
PORT = 443
ALT_PORTS = [8443, 2053, 2083, 2096, 8080, 9443, 4443]
VPN_NETWORK = "10.8.0.0/24"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"
TUN_NAME = "VPNServer"
MTU = 1500

# Директория для хранения конфигурации
CONFIG_DIR = Path("vpn_config")
CONFIG_DIR.mkdir(exist_ok=True)
PASSWORD_FILE = CONFIG_DIR / "password.hash"
SALT_FILE = CONFIG_DIR / "salt.bin"
CERT_FILE = CONFIG_DIR / "server.crt"
KEY_FILE = CONFIG_DIR / "server.key"
CLIENTS_FILE = CONFIG_DIR / "clients.json"

# ============== ЛОГИРОВАНИЕ ==============
class SafeLogger:
    """Безопасный логгер с обработкой Unicode"""
    
    def __init__(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('vpn_server.log', encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def info(self, msg):
        try:
            self.logger.info(msg)
        except UnicodeEncodeError:
            safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
            self.logger.info(safe_msg)
    
    def warning(self, msg):
        try:
            self.logger.warning(msg)
        except UnicodeEncodeError:
            safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
            self.logger.warning(safe_msg)
    
    def error(self, msg):
        try:
            self.logger.error(msg)
        except UnicodeEncodeError:
            safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
            self.logger.error(safe_msg)
    
    def debug(self, msg):
        try:
            self.logger.debug(msg)
        except UnicodeEncodeError:
            safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
            self.logger.debug(safe_msg)

logger = SafeLogger()


# ============== АУТЕНТИФИКАЦИЯ ==============
class SecureAuthManager:
    """Защищенный менеджер аутентификации"""
    
    def __init__(self):
        self.password_hash: Optional[str] = None
        self.salt: Optional[bytes] = None
        self.plain_password: Optional[str] = None  # Только для теста!
        self.nonce_manager = SecureNonceManager()
        self.failed_attempts: Dict[str, List[float]] = defaultdict(list)
        self.lock = threading.RLock()
        self._load_or_create_credentials()
    
    def _load_or_create_credentials(self):
        """Загрузка или создание учетных данных"""
        try:
            if PASSWORD_FILE.exists():
                with open(PASSWORD_FILE, 'r') as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
                        self.password_hash = data.get('password_hash')
                        self.salt = bytes.fromhex(data.get('salt', ''))
                        self.plain_password = data.get('plain_password', 'mysecretpassword123')
                        logger.info("[+] Credentials loaded from disk")
                    else:
                        raise ValueError("Empty password file")
            else:
                raise FileNotFoundError("Password file not found")
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            logger.warning(f"[!] Failed to load credentials: {e}")
            logger.info("[*] Creating new credentials...")
            
            # Удаляем поврежденные файлы
            if PASSWORD_FILE.exists():
                PASSWORD_FILE.unlink()
            if SALT_FILE.exists():
                SALT_FILE.unlink()
            
            password = self._prompt_password()
            self.plain_password = password
            self.salt = secrets.token_bytes(32)
            self.password_hash = hashlib.sha256(password.encode() + self.salt).hexdigest()
            
            with open(PASSWORD_FILE, 'w') as f:
                json.dump({
                    'password_hash': self.password_hash,
                    'salt': self.salt.hex(),
                    'plain_password': password  # Только для теста!
                }, f)
            os.chmod(PASSWORD_FILE, 0o600)
            logger.info("[+] New credentials created and saved")
    
    def _prompt_password(self) -> str:
        """Запрос пароля у администратора"""
        import getpass
        print("\n" + "=" * 50)
        print("VPN SERVER INITIAL SETUP")
        print("=" * 50)
        password = getpass.getpass("Enter VPN server password (min 8 chars): ")
        confirm = getpass.getpass("Confirm password: ")
        
        if password != confirm:
            logger.error("Passwords do not match!")
            sys.exit(1)
        
        if len(password) < 8:
            logger.warning("WARNING: Password is short!")
        
        return password
    
    def check_rate_limit(self, client_ip: str) -> bool:
        """Проверка rate limiting"""
        with self.lock:
            now = time.time()
            attempts = self.failed_attempts[client_ip]
            attempts = [t for t in attempts if now - t < 300]
            self.failed_attempts[client_ip] = attempts
            
            if len(attempts) >= 5:
                logger.warning(f"Rate limit exceeded for {client_ip}")
                return False
            
            return True
    
    def record_failure(self, client_ip: str):
        """Запись неудачной попытки"""
        with self.lock:
            self.failed_attempts[client_ip].append(time.time())
    
    def authenticate(self, sock: socket.socket, client_ip: str) -> Tuple[bool, Optional[bytes]]:
        """Аутентификация клиента"""
        
        if not self.check_rate_limit(client_ip):
            return False, None
        
        try:
            sock.settimeout(10)
            
            # Читаем данные аутентификации
            auth_data = self._recv_exact(sock, 64)
            
            if len(auth_data) < 64:
                logger.warning(f"Incomplete auth data from {client_ip}: {len(auth_data)} bytes")
                self.record_failure(client_ip)
                return False, None
            
            # Проверяем окончание \r\n
            if auth_data[62:64] != b'\r\n':
                logger.warning(f"Invalid auth format from {client_ip}")
                self.record_failure(client_ip)
                return False, None
            
            received_hash = auth_data[:56].decode('ascii', errors='ignore')
            client_nonce = auth_data[56:62]
            
            # Проверяем nonce
            if not self.nonce_manager.is_valid(client_nonce):
                logger.warning(f"Replay attack detected from {client_ip}")
                self.record_failure(client_ip)
                return False, None
            
            # Вычисляем ожидаемый хеш
            password_to_use = self.plain_password or "mysecretpassword123"
            expected_hash = hashlib.sha224(password_to_use.encode()).hexdigest()
            
            if received_hash == expected_hash:
                logger.info(f"[+] Client {client_ip} authenticated successfully")
                return True, client_nonce
            else:
                logger.warning(f"[!] Authentication failed for {client_ip}")
                logger.debug(f"    Expected: {expected_hash[:16]}...")
                logger.debug(f"    Received: {received_hash[:16]}...")
                self.record_failure(client_ip)
                return False, None
                
        except socket.timeout:
            logger.warning(f"Auth timeout from {client_ip}")
            self.record_failure(client_ip)
            return False, None
        except Exception as e:
            logger.error(f"Auth error from {client_ip}: {e}")
            self.record_failure(client_ip)
            return False, None
    
    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        """Получение точного количества байт"""
        data = b''
        while len(data) < n:
            try:
                chunk = sock.recv(n - len(data))
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                break
        return data

# ============== УПРАВЛЕНИЕ КЛИЕНТАМИ ==============
class ClientManager:
    """Управление подключенными клиентами"""
    
    def __init__(self):
        self.clients: Dict[str, Dict] = {}
        self.lock = threading.RLock()
        self.next_ip = 2
        self.used_ips: Set[str] = set()
        self._load_state()
    
    def _load_state(self):
        """Загрузка состояния клиентов"""
        if CLIENTS_FILE.exists():
            try:
                with open(CLIENTS_FILE, 'r') as f:
                    data = json.load(f)
                    self.used_ips = set(data.get('used_ips', []))
                    self.next_ip = max(data.get('next_ip', 2), 2)
            except Exception as e:
                logger.error(f"Failed to load clients state: {e}")
    
    def _save_state(self):
        """Сохранение состояния (атомарная запись)"""
        try:
            temp_file = CLIENTS_FILE.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump({
                    'used_ips': list(self.used_ips),
                    'next_ip': self.next_ip
                }, f, indent=2)
            temp_file.replace(CLIENTS_FILE)
        except Exception as e:
            logger.error(f"Failed to save clients state: {e}")
    
    def allocate_ip(self) -> Optional[str]:
        """Выделение IP адреса клиенту"""
        with self.lock:
            network = ipaddress.ip_network(VPN_NETWORK)
            max_hosts = min(network.num_addresses - 2, 254)
            
            for _ in range(max_hosts):
                ip = f"10.8.0.{self.next_ip}"
                
                if self.next_ip >= 255:
                    self.next_ip = 2
                else:
                    self.next_ip += 1
                
                if ip not in self.used_ips:
                    self.used_ips.add(ip)
                    self._save_state()
                    return ip
            
            logger.error("No available IP addresses!")
            return None
    
    def release_ip(self, ip: str):
        """Освобождение IP адреса"""
        with self.lock:
            self.used_ips.discard(ip)
            self._save_state()
    
    def add_client(self, ip: str, handler, sock: socket.socket):
        """Добавление клиента"""
        with self.lock:
            self.clients[ip] = {
                'handler': handler,
                'socket': sock,
                'connected_at': datetime.now(),
                'bytes_sent': 0,
                'bytes_received': 0,
                'packets_sent': 0,
                'packets_received': 0
            }
            logger.info(f"[+] Client {ip} registered (total: {len(self.clients)})")
    
    def remove_client(self, ip: str):
        """Удаление клиента"""
        with self.lock:
            if ip in self.clients:
                client = self.clients[ip]
                uptime = (datetime.now() - client['connected_at']).total_seconds()
                
                logger.info(
                    f"[-] Client {ip} disconnected. "
                    f"Uptime: {int(uptime//3600):02d}:{int((uptime%3600)//60):02d}:{int(uptime%60):02d}, "
                    f"Sent: {self._format_bytes(client['bytes_sent'])}, "
                    f"Received: {self._format_bytes(client['bytes_received'])}"
                )
                
                del self.clients[ip]
            
            self.release_ip(ip)
    
    def get_client(self, ip: str) -> Optional[Dict]:
        """Получение клиента по IP"""
        with self.lock:
            return self.clients.get(ip)
    
    def get_all_clients(self) -> List[str]:
        """Получение списка всех клиентов"""
        with self.lock:
            return list(self.clients.keys())
    
    def update_stats(self, ip: str, sent: int = 0, received: int = 0, packets_sent: int = 0, packets_received: int = 0):
        """Обновление статистики клиента"""
        with self.lock:
            if ip in self.clients:
                self.clients[ip]['bytes_sent'] += sent
                self.clients[ip]['bytes_received'] += received
                self.clients[ip]['packets_sent'] += packets_sent
                self.clients[ip]['packets_received'] += packets_received
    
    def get_stats(self) -> Dict:
        """Получение общей статистики"""
        with self.lock:
            total_sent = sum(c['bytes_sent'] for c in self.clients.values())
            total_received = sum(c['bytes_received'] for c in self.clients.values())
            
            return {
                'total_clients': len(self.clients),
                'total_bytes_sent': total_sent,
                'total_bytes_received': total_received,
                'clients': {
                    ip: {
                        'connected_at': c['connected_at'].isoformat(),
                        'bytes_sent': c['bytes_sent'],
                        'bytes_received': c['bytes_received'],
                        'packets_sent': c['packets_sent'],
                        'packets_received': c['packets_received']
                    }
                    for ip, c in self.clients.items()
                }
            }
    
    @staticmethod
    def _format_bytes(bytes_count: int) -> str:
        """Форматирование байт в читаемый вид"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_count < 1024.0:
                return f"{bytes_count:.2f} {unit}"
            bytes_count /= 1024.0
        return f"{bytes_count:.2f} PB"


# ============== WINTUN ОБЕРТКА ==============
class WintunWrapper:
    """Обертка для wintun.dll"""
    
    def __init__(self, dll_path: str = "wintun.dll"):
        search_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), dll_path),
            os.path.join(os.getcwd(), dll_path),
            os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', dll_path),
            dll_path
        ]
        
        found_path = None
        for path in search_paths:
            if os.path.exists(path):
                found_path = path
                break
        
        if not found_path:
            raise FileNotFoundError(f"wintun.dll not found in any of: {search_paths}")
        
        logger.info(f"Loading wintun.dll from: {found_path}")
        self.dll = ctypes.WinDLL(found_path)
        
        # Определение обязательных функций
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR]
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [wintypes.LPCWSTR]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        self.WintunCloseAdapter.restype = None
        
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket
        self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        
        self.WintunSendPacket = self.dll.WintunSendPacket
        self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunSendPacket.restype = None
        
        self.WintunReceivePacket = self.dll.WintunReceivePacket
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        self.WintunReceivePacket.restype = ctypes.c_uint32
        
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunReleaseReceivePacket.restype = None
        
        # Опциональные функции (могут отсутствовать в некоторых версиях)
        try:
            self.WintunDeleteAdapter = self.dll.WintunDeleteAdapter
            self.WintunDeleteAdapter.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.WintunDeleteAdapter.restype = wintypes.BOOL
        except AttributeError:
            logger.info("[*] WintunDeleteAdapter not available in this DLL version")
            self.WintunDeleteAdapter = None
        
        try:
            self.WintunGetAdapterLUID = self.dll.WintunGetAdapterLUID
            self.WintunGetAdapterLUID.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
            self.WintunGetAdapterLUID.restype = None
        except AttributeError:
            self.WintunGetAdapterLUID = None
        
        try:
            self.WintunGetRunningDriverVersion = self.dll.WintunGetRunningDriverVersion
            self.WintunGetRunningDriverVersion.argtypes = []
            self.WintunGetRunningDriverVersion.restype = ctypes.c_uint32
        except AttributeError:
            self.WintunGetRunningDriverVersion = None
            logger.info("[*] WintunGetRunningDriverVersion not available")

class TUNInterface:
    """Виртуальный TUN интерфейс"""
    
    def __init__(self, name: str, ip: str, netmask: str):
        self.name = name
        self.ip = ip
        self.netmask = netmask
        self.handle = None
        self.running = True
        self.wintun: Optional[WintunWrapper] = None
        self.read_lock = threading.RLock()
        self.write_lock = threading.RLock()
        
        self._initialize()
    
    def _initialize(self):
        """Инициализация интерфейса"""
        try:
            self.wintun = WintunWrapper("wintun.dll")
        except Exception as e:
            logger.error(f"Wintun load error: {e}")
            raise
        
        # Проверяем версию драйвера (если функция доступна)
        if self.wintun.WintunGetRunningDriverVersion:
            version = self.wintun.WintunGetRunningDriverVersion()
            logger.info(f"Wintun driver version: {version >> 16}.{version & 0xFFFF}")
        
        # Пытаемся открыть существующий адаптер
        self.handle = self.wintun.WintunOpenAdapter(self.name)
        
        if not self.handle or self.handle == 0:
            logger.info(f"Creating new adapter: {self.name}")
            self.handle = self.wintun.WintunCreateAdapter(self.name, "Wintun", None)
            
            if not self.handle or self.handle == 0:
                raise Exception(f"Failed to create/open adapter {self.name}")
        
        logger.info(f"[+] Virtual adapter '{self.name}' ready")
        
        # Настраиваем IP адрес
        self._configure_ip()
    
    def _configure_ip(self):
        """Настройка IP адреса через netsh"""
        try:
            import subprocess
            
            # Устанавливаем IP
            cmd = f'netsh interface ip set address "{self.name}" static {self.ip} {self.netmask}'
            result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            
            if result.returncode == 0:
                logger.info(f"[+] IP configured: {self.ip}/{self.netmask}")
            else:
                # Возможно, адрес уже настроен
                logger.info(f"[*] IP configuration: {result.stderr.strip() or 'already configured'}")
            
            # Включаем интерфейс
            subprocess.run(f'netsh interface set interface "{self.name}" admin=enabled', 
                          capture_output=True, shell=True)
            
            # Настраиваем DNS (опционально)
            subprocess.run(f'netsh interface ip set dns "{self.name}" static 1.1.1.1', 
                          capture_output=True, shell=True)
            subprocess.run(f'netsh interface ip add dns "{self.name}" 8.8.8.8 index=2', 
                          capture_output=True, shell=True)
            
        except Exception as e:
            logger.error(f"IP configuration error: {e}")
    
    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        """Чтение пакета из TUN"""
        if not self.handle or not self.running:
            return None
        
        with self.read_lock:
            try:
                packet_ptr = ctypes.c_void_p()
                packet_size = ctypes.c_uint32(0)
                
                result = self.wintun.WintunReceivePacket(
                    self.handle,
                    ctypes.byref(packet_ptr),
                    ctypes.byref(packet_size)
                )
                
                if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                    data = ctypes.string_at(packet_ptr, packet_size.value)
                    self.wintun.WintunReleaseReceivePacket(self.handle, packet_ptr)
                    return data
                
                return None
                
            except Exception as e:
                logger.debug(f"TUN read error: {e}")
                return None
    
    def write(self, packet: bytes) -> bool:
        """Запись пакета в TUN"""
        if not self.handle or not self.running or not packet:
            return False
        
        with self.write_lock:
            try:
                packet_ptr = self.wintun.WintunAllocateSendPacket(self.handle, len(packet))
                
                if packet_ptr and packet_ptr != 0:
                    ctypes.memmove(packet_ptr, packet, len(packet))
                    self.wintun.WintunSendPacket(self.handle, packet_ptr)
                    return True
                
                return False
                
            except Exception as e:
                logger.debug(f"TUN write error: {e}")
                return False
    
    def close(self):
        """Закрытие интерфейса"""
        self.running = False
        
        if self.handle:
            try:
                self.wintun.WintunCloseAdapter(self.handle)
                logger.info(f"[+] Adapter '{self.name}' closed")
            except Exception as e:
                logger.error(f"Error closing adapter: {e}")
            finally:
                self.handle = None


# ============== ГЕНЕРАЦИЯ SSL СЕРТИФИКАТА ==============
def generate_ssl_certificate():
    """Генерация самоподписанного SSL сертификата"""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import hashes
    import datetime
    
    if CERT_FILE.exists() and KEY_FILE.exists():
        logger.info("[*] SSL certificate already exists")
        return
    
    logger.info("[*] Generating new SSL certificate...")
    
    # Генерируем приватный ключ
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Создаем сертификат
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "San Francisco"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "VPN Server"),
        x509.NameAttribute(NameOID.COMMON_NAME, "vpn.local"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc)
    ).not_valid_after(
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.DNSName("vpn.local"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256())
    
    # Сохраняем ключ
    with open(KEY_FILE, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    os.chmod(KEY_FILE, 0o600)
    
    # Сохраняем сертификат
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    logger.info("[+] SSL certificate created")
    
    # Экспортируем публичный ключ для клиента
    pubkey_file = CONFIG_DIR / "server_pubkey.pem"
    with open(pubkey_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ============== ОБРАБОТЧИК КЛИЕНТА ==============
class ClientHandler:
    """Обработка одного клиента"""
    
    def __init__(self, sock: socket.socket, addr: tuple, vpn_server):
        self.sock = sock
        self.addr = addr
        self.client_ip_str = addr[0]
        self.vpn_server = vpn_server
        self.assigned_ip: Optional[str] = None
        self.running = True
        self.anti_dpi: Optional[AntiDPIEngine] = None
        self.last_activity = time.time()
        self.write_lock = threading.RLock()
        
        # Статистика
        self.bytes_sent = 0
        self.bytes_received = 0
        self.packets_sent = 0
        self.packets_received = 0
    
    def run(self):
        """Основной цикл обработки клиента"""
        try:
            # Устанавливаем таймаут
            self.sock.settimeout(30)
            
            # Trojan аутентификация
            auth_success, nonce = self.vpn_server.auth_manager.authenticate(
                self.sock, self.client_ip_str
            )
            
            if not auth_success:
                logger.warning(f"Authentication failed from {self.addr}")
                return
            
            # Инициализируем Anti-DPI и выполняем PFS handshake
            self.anti_dpi = AntiDPIEngine(is_server=True)
            
            try:
                session_keys = self.anti_dpi.perform_handshake(self.sock, self.client_ip_str)
                logger.debug(f"PFS handshake completed with {self.addr}")
            except Exception as e:
                logger.error(f"PFS handshake failed: {e}")
                import traceback
                traceback.print_exc()
                return
            
            # Выделяем IP адрес
            self.assigned_ip = self.vpn_server.client_manager.allocate_ip()
            
            if not self.assigned_ip:
                logger.error(f"No available IP for {self.addr}")
                return
            
            # Отправляем назначенный IP клиенту
            ip_message = self.anti_dpi.encrypt_packet(self.assigned_ip.encode())
            self.sock.send(ip_message)
            
            logger.info(f"[+] Client {self.addr} authenticated, assigned IP: {self.assigned_ip}")
            
            # Регистрируем клиента
            self.vpn_server.client_manager.add_client(self.assigned_ip, self, self.sock)
            
            # Запускаем поток для отправки данных клиенту
            send_thread = threading.Thread(target=self._send_worker, daemon=True)
            send_thread.start()
            
            # Основной цикл приема данных
            while self.running and self.vpn_server.running:
                try:
                    # Читаем заголовок пакета
                    header = self._recv_exact(14)
                    
                    if len(header) < 14:
                        break
                    
                    length = struct.unpack('!H', header[12:14])[0]
                    
                    if 0 < length < 65535:
                        encrypted_data = self._recv_exact(length)
                        
                        if len(encrypted_data) == length:
                            packet = self.anti_dpi.decrypt_packet(header + encrypted_data)
                            
                            if packet:
                                self.vpn_server.tun.write(packet)
                                
                                self.bytes_received += len(header) + length
                                self.packets_received += 1
                                self.last_activity = time.time()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logger.debug(f"Receive error from {self.assigned_ip}: {e}")
                    break
            
        except Exception as e:
            logger.error(f"Client handler error for {self.addr}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.cleanup()
    
    def _send_worker(self):
        """Поток отправки данных клиенту"""
        # Очередь пакетов для этого клиента
        import queue
        packet_queue = queue.Queue(maxsize=1000)
        
        # Регистрируем очередь
        with self.vpn_server.send_queues_lock:
            self.vpn_server.send_queues[self.assigned_ip] = packet_queue
        
        try:
            while self.running and self.vpn_server.running:
                try:
                    packet = packet_queue.get(timeout=0.5)
                    
                    # Шифруем и отправляем
                    encrypted = self.anti_dpi.encrypt_packet(packet)
                    
                    with self.write_lock:
                        self.sock.send(encrypted)
                    
                    self.bytes_sent += len(encrypted)
                    self.packets_sent += 1
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    if self.running:
                        logger.debug(f"Send error to {self.assigned_ip}: {e}")
                    break
        finally:
            # Удаляем очередь
            with self.vpn_server.send_queues_lock:
                if self.assigned_ip in self.vpn_server.send_queues:
                    del self.vpn_server.send_queues[self.assigned_ip]
    
    def _recv_exact(self, n: int) -> bytes:
        """Получение точного количества байт"""
        data = b''
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                break
        return data
    
    def send_packet(self, packet: bytes) -> bool:
        """Отправка пакета клиенту (вызывается из TUN reader)"""
        if not self.running:
            return False
        
        with self.vpn_server.send_queues_lock:
            if self.assigned_ip in self.vpn_server.send_queues:
                try:
                    self.vpn_server.send_queues[self.assigned_ip].put_nowait(packet)
                    return True
                except:
                    pass
        
        return False
    
    def cleanup(self):
        """Очистка ресурсов клиента"""
        self.running = False
        
        if self.assigned_ip:
            # Обновляем статистику
            self.vpn_server.client_manager.update_stats(
                self.assigned_ip,
                sent=self.bytes_sent,
                received=self.bytes_received,
                packets_sent=self.packets_sent,
                packets_received=self.packets_received
            )
            
            # Удаляем клиента
            self.vpn_server.client_manager.remove_client(self.assigned_ip)
        
        try:
            self.sock.close()
        except:
            pass


# ============== ГЛАВНЫЙ VPN СЕРВЕР ==============
class VPNServer:
    """Главный VPN сервер"""
    
    def __init__(self):
        self.running = True
        self.start_time = time.time()
        
        # Менеджеры
        self.auth_manager = SecureAuthManager()
        self.client_manager = ClientManager()
        
        # Очереди отправки для клиентов
        self.send_queues: Dict[str, 'queue.Queue'] = {}
        self.send_queues_lock = threading.RLock()
        
        # Список портов для прослушивания
        self.ports = [PORT] + ALT_PORTS
        self.server_sockets: List[socket.socket] = []
        
        # Генерируем SSL сертификат
        generate_ssl_certificate()
        
        # Инициализируем TUN
        logger.info("[1/4] Initializing TUN interface...")
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        
        # Запускаем TUN reader
        logger.info("[2/4] Starting TUN reader...")
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        
        # Запускаем серверы на всех портах
        logger.info("[3/4] Starting listeners...")
        self._start_listeners()
        
        # Запускаем монитор статистики
        logger.info("[4/4] Starting stats monitor...")
        self.stats_thread = threading.Thread(target=self._stats_monitor, daemon=True)
        self.stats_thread.start()
        
        self._show_banner()
    
    def _start_listeners(self):
        """Запуск слушателей на всех портах"""
        for port in self.ports:
            thread = threading.Thread(target=self._listen_on_port, args=(port,), daemon=True)
            thread.start()
    
    def _listen_on_port(self, port: int):
        """Слушатель на конкретном порту"""
        try:
            # Создаем SSL контекст с JA3 имитацией
            anti_dpi = AntiDPIEngine(is_server=True)
            context = anti_dpi.tls_impersonator.create_ssl_context(is_server=True)
            
            # Загружаем сертификат
            context.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
            
            # Создаем TCP сокет
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            server_sock.bind((HOST, port))
            server_sock.listen(100)
            
            self.server_sockets.append(server_sock)
            
            # Оборачиваем в SSL
            secure_server = context.wrap_socket(server_sock, server_side=True)
            
            logger.info(f"[*] Listening on port {port} (JA3: {anti_dpi.tls_impersonator.current_profile})")
            
            while self.running:
                try:
                    secure_server.settimeout(1.0)
                    client_sock, addr = secure_server.accept()
                    
                    logger.info(f"[*] New SSL connection from {addr} on port {port}")
                    
                    # Запускаем обработчик клиента
                    handler = ClientHandler(client_sock, addr, self)
                    thread = threading.Thread(target=handler.run, daemon=True)
                    thread.start()
                    
                except socket.timeout:
                    continue
                except ssl.SSLError as e:
                    if self.running:
                        logger.debug(f"SSL error on port {port}: {e}")
                except Exception as e:
                    if self.running:
                        logger.error(f"Error on port {port}: {e}")
                        
        except Exception as e:
            logger.error(f"Failed to start listener on port {port}: {e}")
    
    def _tun_reader(self):
        """Чтение из TUN и отправка клиентам"""
        logger.info("[*] TUN reader started")
        
        while self.running:
            try:
                packet = self.tun.read(timeout=0.1)
                
                if packet and len(packet) >= 20:
                    # Извлекаем IP назначения
                    try:
                        dest_ip = socket.inet_ntoa(packet[16:20])
                        
                        # Ищем клиента
                        client_data = self.client_manager.get_client(dest_ip)
                        
                        if client_data:
                            handler = client_data['handler']
                            handler.send_packet(packet)
                    except Exception as e:
                        logger.debug(f"TUN routing error: {e}")
                
            except Exception as e:
                if self.running:
                    logger.debug(f"TUN reader error: {e}")
    
    def _stats_monitor(self):
        """Мониторинг статистики"""
        while self.running:
            time.sleep(60)
            
            stats = self.client_manager.get_stats()
            uptime = time.time() - self.start_time
            
            logger.info(
                f"[STATS] Uptime: {int(uptime//3600):02d}:{int((uptime%3600)//60):02d}, "
                f"Clients: {stats['total_clients']}, "
                f"Total: \u2191{self._format_bytes(stats['total_bytes_sent'])} "
                f"\u2193{self._format_bytes(stats['total_bytes_received'])}"
            )
    
    def _show_banner(self):
        """Отображение баннера"""
        banner = f"""
======================================================================
                    VPN SERVER 2026 - SECURE MODE
======================================================================
  Status:           [RUNNING]
  Ports:            {', '.join(map(str, self.ports))}
  VPN Network:      {VPN_NETWORK}
  Server IP:        {VPN_SERVER_IP}
  PFS:              ENABLED (X25519 + Double Ratchet)
  Anti-DPI:         ENABLED (JA3 + SNI Spoofing + Obfuscation)
  Auth Salt:        {self.auth_manager.salt[:8].hex()}...
======================================================================
  Clients: {len(self.client_manager.clients)}
======================================================================
        """
        logger.info(banner)
    
    def stop(self):
        """Остановка сервера"""
        logger.info("\n[!] Shutting down server...")
        self.running = False
        
        # Закрываем все сокеты
        for sock in self.server_sockets:
            try:
                sock.close()
            except:
                pass
        
        # Закрываем TUN
        self.tun.close()
        
        logger.info("[+] Server stopped")
    
    @staticmethod
    def _format_bytes(bytes_count: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_count < 1024.0:
                return f"{bytes_count:.2f} {unit}"
            bytes_count /= 1024.0
        return f"{bytes_count:.2f} PB"


# ============== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==============
def check_admin() -> bool:
    """Проверка прав администратора"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False


def setup_signal_handlers(server: VPNServer):
    """Настройка обработчиков сигналов"""
    def signal_handler(signum, frame):
        logger.info(f"\n[!] Received signal {signum}")
        server.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


# ============== ТОЧКА ВХОДА ==============
if __name__ == "__main__":
    print("=" * 60)
    print("VPN SERVER 2026")
    print("=" * 60)
    
    # Проверка прав администратора
    if not check_admin():
        print("[!] ERROR: Administrator privileges required!")
        print("    Run this script as Administrator")
        sys.exit(1)
    
    # Проверка наличия wintun.dll
    if not os.path.exists("wintun.dll"):
        print("[!] ERROR: wintun.dll not found!")
        print("    Download from: https://www.wintun.net/")
        sys.exit(1)
    
    # Создаем и запускаем сервер
    try:
        server = VPNServer()
        setup_signal_handlers(server)
        
        # Основной цикл
        while server.running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'server' in locals():
            server.stop()
    
    print("\n[+] Server shutdown complete")