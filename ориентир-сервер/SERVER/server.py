#!/usr/bin/env python3
"""
VPN-сервер с ИНТЕГРИРОВАННОЙ ANTI-DPI ЗАЩИТОЙ - ИСПРАВЛЕННАЯ ВЕРСИЯ
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

# Импорт улучшенного anti-DPI модуля
try:
    from anti_dpi_engine import AntiDPIEngine, get_optimal_sni, get_engine_stats
    ANTI_DPI_AVAILABLE = True
    print("[+] Enhanced Anti-DPI Engine loaded")
except ImportError:
    ANTI_DPI_AVAILABLE = False
    print("[!] Enhanced Anti-DPI module not found, using basic protection")

# Конфигурация
HOST = "0.0.0.0"
PORT = 443
ALT_PORTS = [8443, 2053, 2083, 2096, 8080, 9443, 4443]
PASSWORD = "mysecretpassword123"
CERTFILE = "server.crt"
KEYFILE = "server.key"
TUN_NAME = "VPNServer"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"

# Настройка логирования
class SafeLogger:
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
        except:
            safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
            self.logger.info(safe_msg)
    
    def warning(self, msg):
        try:
            self.logger.warning(msg)
        except:
            safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
            self.logger.warning(safe_msg)
    
    def error(self, msg):
        try:
            self.logger.error(msg)
        except:
            safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
            self.logger.error(safe_msg)

logger = SafeLogger()


class CryptoEngine:
    """Криптографический движок с ротацией ключей"""
    
    def __init__(self, password: str):
        self.password = password
        self.key_rotation_time = 3600
        self.last_rotation = time.time()
        self.current_key = None
        self.old_keys = []
        self.derive_key()
        logger.info("[+] Crypto engine initialized")
    
    def derive_key(self):
        """Генерация ключа из пароля"""
        salt = b'vpn_salt_2026'  # Фиксированная соль для совместимости с клиентом
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
            if len(self.old_keys) > 5:
                self.old_keys.pop(0)
        
        self.current_key = new_key
        self.cipher = AESGCM(new_key)
    
    def rotate_key(self):
        """Ротация ключей"""
        self.derive_key()
        logger.info("[*] Key rotated")
    
    def encrypt(self, data: bytes, nonce: bytes) -> bytes:
        """Шифрование данных"""
        if time.time() - self.last_rotation > self.key_rotation_time:
            self.rotate_key()
            self.last_rotation = time.time()
        
        return self.cipher.encrypt(nonce, data, None)
    
    def decrypt(self, data: bytes, nonce: bytes) -> bytes:
        """Дешифрование данных"""
        try:
            return self.cipher.decrypt(nonce, data, None)
        except:
            for old_key in self.old_keys:
                try:
                    old_cipher = AESGCM(old_key)
                    return old_cipher.decrypt(nonce, data, None)
                except:
                    continue
            raise Exception("Failed to decrypt data")


class TrojanAuth:
    """Trojan аутентификация"""
    
    def __init__(self, password: str):
        self.expected_hash = hashlib.sha224(password.encode()).hexdigest()
    
    def authenticate(self, sock) -> bool:
        """Проверка аутентификации"""
        try:
            # Читаем 58 байт аутентификации
            auth_data = b''
            while len(auth_data) < 58:
                chunk = sock.recv(58 - len(auth_data))
                if not chunk:
                    return False
                auth_data += chunk
            
            # Проверяем формат
            if auth_data[56:58] != b'\r\n':
                return False
            
            received_hash = auth_data[:56].decode()
            return received_hash == self.expected_hash
            
        except Exception as e:
            logger.error(f"Auth error: {e}")
            return False


class Wintun:
    """Обертка для wintun.dll"""
    
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
            raise FileNotFoundError(f"wintun.dll not found")
        
        logger.info(f"Loading wintun.dll from: {found_path}")
        self.dll = ctypes.WinDLL(found_path)
        
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR]
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        self.WintunCloseAdapter.restype = None
        
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [wintypes.LPCWSTR]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        
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


class TUNInterface:
    """TUN интерфейс"""
    
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
        
        self.handle = self.wintun.WintunCreateAdapter(name, "Wintun", None)
        
        if not self.handle or self.handle == 0:
            logger.info("Adapter not created, opening existing...")
            self.handle = self.wintun.WintunOpenAdapter(name)
            
            if not self.handle or self.handle == 0:
                raise Exception(f"Failed to create/open adapter {name}")
        
        logger.info(f"[+] Virtual adapter ready: {name}")
        
        try:
            import subprocess
            result = subprocess.run(
                f'netsh interface ip set address "{name}" static {ip} {netmask}',
                capture_output=True,
                text=True,
                shell=True
            )
            if result.returncode == 0:
                logger.info(f"[+] IP assigned: {ip}/{netmask}")
        except Exception as e:
            logger.error(f"IP config error: {e}")
    
    def read(self, size: int = 65536) -> bytes:
        """Чтение из TUN"""
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
        except:
            pass
    
    def close(self):
        """Закрытие TUN"""
        self.running = False
        
        if self.handle:
            try:
                self.wintun.WintunCloseAdapter(self.handle)
                logger.info(f"[+] Adapter {self.name} closed")
            except:
                pass
            finally:
                self.handle = None


class ClientHandler:
    """Обработка клиента"""
    
    def __init__(self, sock, addr, vpn_server):
        self.sock = sock
        self.addr = addr
        self.vpn_server = vpn_server
        self.client_ip = None
        self.running = True
        self.auth = TrojanAuth(PASSWORD)
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
            # Аутентификация (после SSL handshake)
            if not self.auth.authenticate(self.sock):
                logger.warning(f"Auth failed from {self.addr}")
                return
            
            # Выделяем IP
            with self.vpn_server.ip_lock:
                self.client_ip = f"10.8.0.{self.vpn_server.next_ip}"
                self.vpn_server.next_ip += 1
            
            # Отправляем IP клиенту
            self.sock.send(self.client_ip.encode())
            logger.info(f"[+] Client {self.addr} authenticated, assigned IP: {self.client_ip}")
            
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
                        if len(header) == 0:
                            break
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
    
    def send_packet(self, packet: bytes):
        """Отправка пакета клиенту"""
        try:
            nonce = os.urandom(12)
            encrypted = self.vpn_server.crypto.encrypt(packet, nonce)
            message = nonce + struct.pack('!H', len(encrypted)) + encrypted
            
            self.sock.send(message)
            self.metrics["bytes_sent"] += len(message)
            self.metrics["packets_sent"] += 1
            self.last_activity = time.time()
            
            return True
        except Exception as e:
            return False
    
    def cleanup(self):
        """Очистка ресурсов"""
        if self.client_ip:
            with self.vpn_server.clients_lock:
                if self.client_ip in self.vpn_server.clients:
                    uptime = (datetime.now() - self.metrics["connected_at"]).total_seconds()
                    logger.info(f"Client {self.client_ip} disconnected. Uptime: {uptime:.0f}s")
                    del self.vpn_server.clients[self.client_ip]
        
        try:
            self.sock.close()
        except:
            pass


class VPNServer:
    """Главный VPN сервер"""
    
    def __init__(self):
        self.running = True
        self.clients = {}
        self.clients_lock = threading.Lock()
        self.ip_lock = threading.Lock()
        self.next_ip = 2
        self.current_port_idx = 0
        self.ports = [PORT] + ALT_PORTS
        
        # Инициализация компонентов
        logger.info("[1/5] Initializing cryptography...")
        self.crypto = CryptoEngine(PASSWORD)
        
        logger.info("[2/5] Initializing TUN interface...")
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        
        logger.info("[3/5] Starting TUN reader...")
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        
        logger.info("[4/5] Starting port hopping...")
        self.port_thread = threading.Thread(target=self._port_hopper, daemon=True)
        self.port_thread.start()
        
        logger.info("[5/5] Starting server...")
        self._start_server()
        
        self._show_banner()
    
    def _start_server(self):
        """Запуск основного сервера"""
        self.servers = []
        
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
            # Создаем SSL контекст
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(CERTFILE, KEYFILE)
            context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:!aNULL:!MD5')
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.maximum_version = ssl.TLSVersion.TLSv1_3
            context.set_alpn_protocols(['h2', 'http/1.1'])
            
            # Создаем TCP сокет
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((HOST, port))
            server_sock.listen(100)
            
            # Оборачиваем в SSL
            secure_server = context.wrap_socket(server_sock, server_side=True)
            
            while self.running:
                try:
                    client_sock, addr = secure_server.accept()
                    logger.info(f"[*] SSL connection from {addr} on port {port}")
                    
                    # Обработка клиента
                    handler = ClientHandler(client_sock, addr, self)
                    thread = threading.Thread(target=handler.run, daemon=True)
                    thread.start()
                    
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
                                client['handler'].send_packet(packet)
                                client['last_activity'] = time.time()
                            except Exception as e:
                                logger.error(f"Send error to {dest_ip}: {e}")
                                del self.clients[dest_ip]
                
                time.sleep(0.001)
                
            except Exception as e:
                if self.running:
                    pass
    
    def _port_hopper(self):
        """Порт-хоппинг"""
        while self.running:
            time.sleep(300)
            
            self.current_port_idx = (self.current_port_idx + 1) % len(self.ports)
            new_port = self.ports[self.current_port_idx]
            
            logger.info(f"[*] Active port is now {new_port} (clients should use this)")
    
    def _show_banner(self):
        """Отображение баннера"""
        banner = f"""
======================================================================
            VPN SERVER - SECURE CONNECTION
======================================================================
  Status:           [OK] RUNNING
  Ports:            {', '.join(map(str, self.ports))}
  VPN network:      10.8.0.0/24
  Server IP:        10.8.0.1
  Active clients:   0
======================================================================
        """
        logger.info(banner)
    
    def stop(self):
        """Остановка сервера"""
        logger.info("\n[!] Stopping server...")
        self.running = False
        
        with self.clients_lock:
            for ip, client in self.clients.items():
                try:
                    client['socket'].close()
                except:
                    pass
            self.clients.clear()
        
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
    print("VPN SERVER")
    print("=" * 50)
    
    if not check_admin():
        print("[!] ERROR: Administrator privileges required!")
        sys.exit(1)
    
    if not os.path.exists("wintun.dll"):
        print("[!] ERROR: wintun.dll not found!")
        sys.exit(1)
    
    generate_ssl_cert()
    
    server = VPNServer()
    
    try:
        while server.running:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()