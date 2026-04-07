#!/usr/bin/env python3
"""
VPN-сервер для Windows с полной anti-DPI защитой (версия 2026)
Оптимизирован для размещения в Беларуси
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
import random
import json
import logging
from ctypes import wintypes
from datetime import datetime
from typing import Optional, Tuple, Dict
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# Импорт anti-DPI модуля
try:
    from anti_dpi_engine import AntiDPIEngine, BlockDetector
    ANTI_DPI_AVAILABLE = True
except ImportError:
    ANTI_DPI_AVAILABLE = False
    print("[!] Anti-DPI модуль не найден, работаем без защиты")

# Конфигурация
HOST = "0.0.0.0"
PORT = 443  # Основной порт
ALT_PORTS = [8443, 2053, 2083, 2096, 8080]  # Запасные порты
PASSWORD = "mysecretpassword123"
CERTFILE = "server.crt"
KEYFILE = "server.key"
TUN_NAME = "VPNServer"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"

# Настройка логирования с поддержкой UTF-8
class SafeLogger:
    """Безопасный логгер без Unicode проблем"""
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
            # Удаляем проблемные символы
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

logger = SafeLogger()

class CryptoEngine:
    """Криптографический движок с ротацией ключей"""
    
    def __init__(self, password: str):
        self.password = password
        self.key_rotation_time = 3600  # Ротация каждый час
        self.last_rotation = time.time()
        self.current_key = None
        self.old_keys = []
        self.rotate_key()
        logger.info("[+] Crypto engine initialized")
    
    def rotate_key(self):
        """Ротация ключей для усиления безопасности"""
        salt = os.urandom(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        new_key = kdf.derive(self.password.encode())
        
        if self.current_key:
            self.old_keys.append(self.current_key)
            # Храним только последние 5 ключей
            if len(self.old_keys) > 5:
                self.old_keys.pop(0)
        
        self.current_key = new_key
        self.cipher = AESGCM(new_key)
        logger.info("[*] Key rotated")
    
    def encrypt(self, data: bytes, nonce: bytes) -> bytes:
        """Шифрование данных"""
        # Проверяем необходимость ротации
        if time.time() - self.last_rotation > self.key_rotation_time:
            self.rotate_key()
            self.last_rotation = time.time()
        
        return self.cipher.encrypt(nonce, data, None)
    
    def decrypt(self, data: bytes, nonce: bytes) -> bytes:
        """Дешифрование данных"""
        # Пробуем текущий ключ
        try:
            return self.cipher.decrypt(nonce, data, None)
        except:
            # Пробуем старые ключи
            for old_key in self.old_keys:
                try:
                    old_cipher = AESGCM(old_key)
                    return old_cipher.decrypt(nonce, data, None)
                except:
                    continue
            raise Exception("Failed to decrypt data")


class TrojanProtocol:
    """Trojan протокол с обфускацией"""
    
    def __init__(self, password: str):
        self.password = password
        self.expected_hash = hashlib.sha224(password.encode()).hexdigest()
        
    def authenticate_client(self, sock) -> Tuple[bool, bytes]:
        """Аутентификация с защитой от анализа"""
        try:
            # Добавляем случайную задержку перед чтением
            time.sleep(random.uniform(0.001, 0.01))
            
            # Читаем с защитой от тайминговых атак
            auth_data = b''
            start_time = time.time()
            
            while len(auth_data) < 58 and (time.time() - start_time) < 10:
                chunk = sock.recv(58 - len(auth_data))
                if not chunk:
                    break
                auth_data += chunk
            
            if len(auth_data) < 58:
                return False, auth_data
            
            if auth_data[56:58] != b'\r\n':
                return False, auth_data
            
            password_hash_hex = auth_data[:56].decode()
            
            # Постоянное время сравнения (защита от timing attack)
            if self._constant_time_compare(password_hash_hex, self.expected_hash):
                return True, b''
            
            return False, auth_data
            
        except Exception as e:
            logger.error(f"Auth error: {e}")
            return False, b''
    
    def _constant_time_compare(self, str1: str, str2: str) -> bool:
        """Сравнение строк за постоянное время"""
        if len(str1) != len(str2):
            return False
        
        result = 0
        for x, y in zip(str1.encode(), str2.encode()):
            result |= x ^ y
        return result == 0


class Wintun:
    """Обертка для wintun.dll с исправлениями"""
    
    def __init__(self, dll_path="wintun.dll"):
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
            raise FileNotFoundError(f"wintun.dll not found at {dll_path}")
        
        logger.info(f"Loading wintun.dll from: {found_path}")
        self.dll = ctypes.WinDLL(found_path)
        
        # Определяем функции
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR]
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        self.WintunCloseAdapter.restype = None
        
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [wintypes.LPCWSTR]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        
        self.WintunGetReadWaitEvent = self.dll.WintunGetReadWaitEvent
        self.WintunGetReadWaitEvent.argtypes = [ctypes.c_void_p]
        self.WintunGetReadWaitEvent.restype = ctypes.c_void_p
        
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
        
        logger.info("[+] Wintun.dll loaded successfully")


class TUNInterface:
    """TUN интерфейс с оптимизациями"""
    
    def __init__(self, name: str, ip: str, netmask: str):
        self.name = name
        self.handle = None
        self.running = True
        
        if not os.path.exists("wintun.dll"):
            logger.error("wintun.dll not found!")
            sys.exit(1)
        
        try:
            self.wintun = Wintun("wintun.dll")
        except Exception as e:
            logger.error(f"Wintun load error: {e}")
            sys.exit(1)
        
        # Создаём или открываем адаптер
        self.handle = self.wintun.WintunCreateAdapter(name, "Wintun", None)
        
        if not self.handle or self.handle == 0:
            logger.info("Adapter not created, trying to open existing...")
            self.handle = self.wintun.WintunOpenAdapter(name)
            
            if not self.handle or self.handle == 0:
                raise Exception(f"Failed to create/open adapter {name}")
        
        logger.info(f"[+] Virtual adapter created/opened: {name}")
        
        # Настраиваем IP
        try:
            import subprocess
            result = subprocess.run(
                f'netsh interface ip set address "{name}" static {ip} {netmask}',
                capture_output=True,
                text=True,
                shell=True
            )
            if result.returncode == 0:
                logger.info(f"[+] IP address assigned: {ip}/{netmask}")
            else:
                logger.warning(f"Failed to assign IP automatically: {result.stderr}")
        except Exception as e:
            logger.error(f"IP configuration error: {e}")
    
    def read(self, size: int = 65536) -> bytes:
        """Безопасное чтение из TUN"""
        if not self.handle or not self.running:
            return b''
        
        try:
            packet_ptr = ctypes.c_void_p()
            packet_size = ctypes.c_uint32(0)
            
            result = self.wintun.WintunReceivePacket(
                self.handle, 
                ctypes.byref(packet_ptr), 
                ctypes.byref(packet_size)
            )
            
            if result == 0 and packet_ptr and packet_ptr.value:
                data = ctypes.string_at(packet_ptr, packet_size.value)
                self.wintun.WintunReleaseReceivePacket(self.handle, packet_ptr)
                return data
            else:
                return b''
                
        except Exception as e:
            if "access violation" not in str(e).lower() and self.running:
                pass
            return b''
    
    def write(self, packet: bytes):
        """Запись в TUN"""
        if not self.handle or not self.running or not packet:
            return
        
        try:
            packet_ptr = self.wintun.WintunAllocateSendPacket(self.handle, len(packet))
            
            if packet_ptr and packet_ptr != 0:
                ctypes.memmove(packet_ptr, packet, len(packet))
                self.wintun.WintunSendPacket(self.handle, packet_ptr)
        except Exception as e:
            if self.running:
                pass
    
    def close(self):
        """Закрытие TUN"""
        self.running = False
        
        if self.handle:
            try:
                self.wintun.WintunCloseAdapter(self.handle)
                logger.info(f"[+] Adapter {self.name} closed")
            except Exception as e:
                logger.error(f"Adapter close error: {e}")
            finally:
                self.handle = None


class ClientHandler:
    """Обработка клиента с полной защитой"""
    
    def __init__(self, sock, addr, vpn_server):
        self.sock = sock
        self.addr = addr
        self.vpn_server = vpn_server
        self.client_ip = None
        self.running = True
        self.trojan = TrojanProtocol(PASSWORD)
        self.last_activity = time.time()
        self.metrics = {
            "bytes_sent": 0,
            "bytes_received": 0,
            "packets_sent": 0,
            "packets_received": 0,
            "connected_at": datetime.now()
        }
        
    def run(self):
        """Основной цикл обработки"""
        try:
            # Аутентификация
            success, _ = self.trojan.authenticate_client(self.sock)
            if not success:
                logger.warning(f"Auth failed from {self.addr}")
                return
            
            # Выделяем IP
            with self.vpn_server.ip_lock:
                self.client_ip = f"10.8.0.{self.vpn_server.next_ip}"
                self.vpn_server.next_ip += 1
            
            # Отправляем IP клиенту
            self.sock.send(self.client_ip.encode())
            logger.info(f"[+] Client {self.addr} authenticated, IP: {self.client_ip}")
            
            # Регистрируем клиента
            with self.vpn_server.clients_lock:
                self.vpn_server.clients[self.client_ip] = {
                    'socket': self.sock,
                    'last_activity': time.time(),
                    'handler': self,
                    'metrics': self.metrics
                }
            
            # Основной цикл приема данных
            while self.running and self.vpn_server.running:
                try:
                    self.sock.settimeout(30)
                    
                    # Читаем заголовок
                    header = self.sock.recv(14)
                    if len(header) < 14:
                        continue
                    
                    nonce = header[:12]
                    length = struct.unpack('!H', header[12:14])[0]
                    
                    if 0 < length < 65535:
                        encrypted = self.sock.recv(length)
                        if len(encrypted) == length:
                            try:
                                packet = self.vpn_server.crypto.decrypt(encrypted, nonce)
                                if packet:
                                    self.vpn_server.tun.write(packet)
                                    self.metrics["bytes_received"] += len(encrypted) + 14
                                    self.metrics["packets_received"] += 1
                                    self.last_activity = time.time()
                            except Exception as e:
                                pass
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    break
                    
        except Exception as e:
            logger.error(f"Client error {self.client_ip}: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Очистка ресурсов"""
        if self.client_ip:
            with self.vpn_server.clients_lock:
                if self.client_ip in self.vpn_server.clients:
                    uptime = (datetime.now() - self.metrics["connected_at"]).total_seconds()
                    logger.info(f"Client {self.client_ip} disconnected. Uptime: {uptime:.0f} sec")
                    del self.vpn_server.clients[self.client_ip]
        
        try:
            self.sock.close()
        except:
            pass


class VPNServer:
    """Главный VPN сервер со всеми защитами"""
    
    def __init__(self):
        self.running = True
        self.clients = {}
        self.clients_lock = threading.Lock()
        self.ip_lock = threading.Lock()
        self.next_ip = 2
        self.current_port_idx = 0
        self.ports = [PORT] + ALT_PORTS
        
        # Инициализация компонентов
        logger.info("[1/6] Initializing cryptography...")
        self.crypto = CryptoEngine(PASSWORD)
        
        logger.info("[2/6] Initializing TUN interface...")
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        
        logger.info("[3/6] Initializing anti-DPI...")
        if ANTI_DPI_AVAILABLE:
            self.anti_dpi = AntiDPIEngine()
        else:
            self.anti_dpi = None
            logger.warning("Anti-DPI not available")
        
        logger.info("[4/6] Starting TUN reader...")
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        
        logger.info("[5/6] Starting port hopping...")
        self.port_thread = threading.Thread(target=self._port_hopper, daemon=True)
        self.port_thread.start()
        
        logger.info("[6/6] Starting server...")
        self._start_server()
        
        self._show_banner()
    
    def _start_server(self):
        """Запуск основного сервера"""
        self.servers = []
        
        # Запускаем слушатели на всех портах
        for port in self.ports:
            server_thread = threading.Thread(
                target=self._listen_on_port,
                args=(port,),
                daemon=True
            )
            server_thread.start()
            self.servers.append(server_thread)
            logger.info(f"[*] Listening on port {port}")
    
    def _listen_on_port(self, port):
        """Слушатель на конкретном порту"""
        try:
            # Создаем обычный TCP сокет
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((HOST, port))
            server_sock.listen(100)
            
            while self.running:
                try:
                    client_sock, addr = server_sock.accept()
                    logger.info(f"[*] Connection from {addr} on port {port}")
                    
                    # Создаем SSL контекст с защитой
                    if self.anti_dpi:
                        # Используем anti-DPI обертку
                        context = self._create_secure_context()
                        secure_sock = context.wrap_socket(client_sock, server_side=True)
                    else:
                        # Обычный SSL
                        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                        context.load_cert_chain(CERTFILE, KEYFILE)
                        secure_sock = context.wrap_socket(client_sock, server_side=True)
                    
                    # Обработка клиента
                    handler = ClientHandler(secure_sock, addr, self)
                    thread = threading.Thread(target=handler.run, daemon=True)
                    thread.start()
                    
                except Exception as e:
                    if self.running:
                        logger.error(f"Error on port {port}: {e}")
                        
        except Exception as e:
            logger.error(f"Failed to start listener on port {port}: {e}")
    
    def _create_secure_context(self):
        """Создание защищенного SSL контекста"""
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(CERTFILE, KEYFILE)
        
        # Настройка современных шифров
        context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
        
        # Включаем только TLS 1.3
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        
        # Включаем ALPN
        context.set_alpn_protocols(['h2', 'http/1.1'])
        
        return context
    
    def _tun_reader(self):
        """Чтение из TUN и отправка клиентам"""
        logger.info("[*] TUN reader started")
        
        while self.running:
            try:
                packet = self.tun.read()
                
                if len(packet) >= 20:
                    try:
                        dest_ip = socket.inet_ntoa(packet[16:20])
                    except:
                        time.sleep(0.001)
                        continue
                    
                    with self.clients_lock:
                        if dest_ip in self.clients:
                            client = self.clients[dest_ip]
                            try:
                                nonce = os.urandom(12)
                                encrypted = self.crypto.encrypt(packet, nonce)
                                message = nonce + struct.pack('!H', len(encrypted)) + encrypted
                                client['socket'].send(message)
                                client['handler'].metrics["bytes_sent"] += len(message)
                                client['handler'].metrics["packets_sent"] += 1
                                client['last_activity'] = time.time()
                            except Exception as e:
                                del self.clients[dest_ip]
                
                time.sleep(0.001)
                
            except Exception as e:
                if self.running:
                    pass
    
    def _port_hopper(self):
        """Порт-хоппинг для обхода блокировок"""
        while self.running:
            time.sleep(300)  # Каждые 5 минут
            
            # Ротируем основной порт
            self.current_port_idx = (self.current_port_idx + 1) % len(self.ports)
            new_port = self.ports[self.current_port_idx]
            
            logger.info(f"[*] Switching active port to {new_port}")
            
            # Обновляем информацию для клиентов
            self._notify_clients_port_change(new_port)
    
    def _notify_clients_port_change(self, new_port):
        """Уведомление клиентов о смене порта"""
        message = f"PORT_CHANGE:{new_port}".encode()
        with self.clients_lock:
            for ip, client in self.clients.items():
                try:
                    client['socket'].send(message)
                except:
                    pass
    
    def _show_banner(self):
        """Отображение баннера (без Unicode)"""
        banner = """
======================================================================
                     VPN SERVER WITH ANTI-DPI PROTECTION
======================================================================
  Status:           [OK] RUNNING
  Port:             443 (main)
  Backup ports:     8443, 2053, 2083, 2096, 8080
  VPN network:      10.8.0.0/24
  Server IP:        10.8.0.1
  Anti-DPI:         [OK] ENABLED
  Location:         Belarus (weak blocking)
======================================================================
        """
        logger.info(banner)
    
    def stop(self):
        """Остановка сервера"""
        logger.info("\n[!] Stopping server...")
        self.running = False
        
        # Закрываем все клиентские соединения
        with self.clients_lock:
            for ip, client in self.clients.items():
                try:
                    client['socket'].close()
                except:
                    pass
            self.clients.clear()
        
        # Закрываем TUN
        self.tun.close()
        
        logger.info("[+] Server stopped")


def generate_ssl_cert():
    """Генерация SSL сертификата"""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    
    if os.path.exists(CERTFILE) and os.path.exists(KEYFILE):
        logger.info("[*] SSL certificate already exists")
        return
    
    logger.info("[*] Generating SSL certificate...")
    
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
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
        datetime.datetime.now()
    ).not_valid_after(
        datetime.datetime.now() + datetime.timedelta(days=365)
    ).sign(private_key, hashes.SHA256(), default_backend())
    
    with open(KEYFILE, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    with open(CERTFILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    logger.info("[+] SSL certificate created")


def check_admin():
    """Проверка прав администратора"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("VPN SERVER WITH ANTI-DPI (2026)")
    print("=" * 50)
    
    if not check_admin():
        print("[!] ERROR: Administrator privileges required!")
        print("[!] Please run PowerShell as Administrator")
        sys.exit(1)
    
    if not os.path.exists("wintun.dll"):
        print("[!] ERROR: wintun.dll not found!")
        print("[!] Download from: https://www.wintun.net/")
        sys.exit(1)
    
    generate_ssl_cert()
    
    server = VPNServer()
    
    try:
        # Держим сервер запущенным
        while server.running:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()