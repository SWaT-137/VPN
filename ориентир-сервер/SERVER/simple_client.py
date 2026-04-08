#!/usr/bin/env python3
"""
TROJAN VPN КЛИЕНТ - ИСПРАВЛЕННАЯ ВЕРСИЯ
"""

import socket
import ssl
import hashlib
import time
import threading
import signal
import sys
import random
import struct
import os
import logging
import ctypes
from datetime import datetime
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
SERVER_HOST = "127.0.0.1"  # ЗАМЕНИТЕ НА IP ВАШЕГО СЕРВЕРА
SERVER_PORT = 443
PASSWORD = "mysecretpassword123"
TUN_NAME = "VPNClient"


class CryptoEngine:
    """Криптографический движок клиента"""
    
    def __init__(self, password: str):
        self.password = password
        self.current_key = None
        self.derive_key()
        logger.info("[+] Crypto engine initialized")
    
    def derive_key(self):
        """Генерация ключа из пароля"""
        salt = b'vpn_salt_2026'  # Фиксированная соль для совместимости
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        self.current_key = kdf.derive(self.password.encode())
        self.cipher = AESGCM(self.current_key)
    
    def encrypt(self, data: bytes, nonce: bytes) -> bytes:
        return self.cipher.encrypt(nonce, data, None)
    
    def decrypt(self, data: bytes, nonce: bytes) -> bytes:
        return self.cipher.decrypt(nonce, data, None)


def generate_password_hash(password: str) -> str:
    """Генерация хеша пароля для Trojan"""
    return hashlib.sha224(password.encode()).hexdigest()


class Wintun:
    """Обертка для wintun.dll"""
    
    def __init__(self, dll_path="wintun.dll"):
        search_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), dll_path),
            os.path.join(os.getcwd(), dll_path),
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
        self.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket
        self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        
        self.WintunSendPacket = self.dll.WintunSendPacket
        self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        
        self.WintunReceivePacket = self.dll.WintunReceivePacket
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        self.WintunReceivePacket.restype = ctypes.c_uint32
        
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


class TUNInterface:
    """TUN интерфейс для клиента"""
    
    def __init__(self, name: str):
        self.name = name
        self.handle = None
        self.running = True
        
        try:
            self.wintun = Wintun("wintun.dll")
        except Exception as e:
            logger.error(f"Wintun load error: {e}")
            raise
        
        self.handle = self.wintun.WintunCreateAdapter(name, "Wintun", None)
        
        if not self.handle or self.handle == 0:
            raise Exception(f"Failed to create adapter {name}")
        
        logger.info(f"[+] Virtual adapter created: {name}")
    
    def set_ip(self, ip: str):
        """Установка IP адреса"""
        try:
            import subprocess
            cmd = f'netsh interface ip set address "{self.name}" static {ip} 255.255.255.0'
            subprocess.run(cmd, shell=True, capture_output=True)
            logger.info(f"[+] IP assigned: {ip}")
        except Exception as e:
            logger.error(f"IP config error: {e}")
    
    def read(self) -> bytes:
        """Чтение пакета из TUN"""
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
            
            if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                data = ctypes.string_at(packet_ptr, packet_size.value)
                self.wintun.WintunReleaseReceivePacket(self.handle, packet_ptr)
                return data
            
            return b''
            
        except Exception as e:
            return b''
    
    def write(self, packet: bytes):
        """Запись пакета в TUN"""
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


class TrojanVPNClient:
    """Полный VPN клиент"""
    
    def __init__(self, server_host: str, server_port: int, password: str):
        self.server_host = server_host
        self.server_port = server_port
        self.password = password
        self.password_hash = generate_password_hash(password)
        
        self.sock = None
        self.running = False
        self.client_ip = None
        
        self.crypto = CryptoEngine(password)
        self.tun = None
        
        self.tun_thread = None
        self.network_thread = None
        
        self.metrics = {
            "bytes_sent": 0,
            "bytes_received": 0,
            "packets_sent": 0,
            "packets_received": 0,
            "connected_at": None,
            "reconnects": 0,
            "status": "disconnected"
        }
        
        logger.info("[+] Trojan VPN Client initialized")
    
    def connect(self) -> bool:
        """Подключение к серверу"""
        try:
            logger.info(f"Connecting to {self.server_host}:{self.server_port}...")
            
            # Создаем TCP сокет
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(15)
            raw_sock.connect((self.server_host, self.server_port))
            
            # SSL контекст
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            self.sock = context.wrap_socket(raw_sock, server_hostname=self.server_host)
            logger.info("[+] SSL connection established")
            
            # Аутентификация Trojan
            auth_data = self.password_hash.encode() + b'\r\n'
            self.sock.send(auth_data)
            logger.info("[+] Authentication sent")
            
            # Получаем назначенный IP
            self.sock.settimeout(5)
            response = self.sock.recv(1024)
            self.client_ip = response.decode().strip()
            logger.info(f"[+] Assigned IP: {self.client_ip}")
            
            # Создаем TUN интерфейс
            self.tun = TUNInterface(TUN_NAME)
            self.tun.set_ip(self.client_ip)
            
            self.metrics["connected_at"] = datetime.now()
            self.metrics["status"] = "connected"
            
            return True
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def start(self):
        """Запуск клиента"""
        if self.running:
            return False
        
        if not self.connect():
            logger.error("Failed to connect")
            return False
        
        self.running = True
        
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        
        self.network_thread = threading.Thread(target=self._network_reader, daemon=True)
        self.network_thread.start()
        
        logger.info("[+] Client started")
        return True
    
    def _tun_reader(self):
        """Чтение из TUN и отправка на сервер"""
        logger.info("[*] TUN reader started")
        
        while self.running and self.sock:
            try:
                packet = self.tun.read()
                
                if packet and len(packet) >= 20:
                    nonce = os.urandom(12)
                    encrypted = self.crypto.encrypt(packet, nonce)
                    message = nonce + struct.pack('!H', len(encrypted)) + encrypted
                    
                    self.sock.send(message)
                    
                    self.metrics["bytes_sent"] += len(message)
                    self.metrics["packets_sent"] += 1
                
                time.sleep(0.001)
                
            except Exception as e:
                if self.running:
                    logger.error(f"TUN reader error: {e}")
                    break
        
        if self.running:
            self._reconnect()
    
    def _network_reader(self):
        """Чтение из сети и отправка в TUN"""
        logger.info("[*] Network reader started")
        
        while self.running and self.sock:
            try:
                self.sock.settimeout(1)
                
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
                        packet = self.crypto.decrypt(encrypted, nonce)
                        
                        if packet and self.tun:
                            self.tun.write(packet)
                            
                            self.metrics["bytes_received"] += len(encrypted) + 14
                            self.metrics["packets_received"] += 1
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Network reader error: {e}")
                    break
        
        if self.running:
            self._reconnect()
    
    def _reconnect(self):
        """Переподключение при разрыве"""
        logger.info("[*] Connection lost, reconnecting...")
        self.metrics["reconnects"] += 1
        self.close()
        time.sleep(5)
        
        if self.running:
            self.connect()
            if self.sock:
                self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
                self.tun_thread.start()
                self.network_thread = threading.Thread(target=self._network_reader, daemon=True)
                self.network_thread.start()
    
    def stop(self):
        """Остановка клиента"""
        logger.info("Stopping client...")
        self.running = False
        
        if self.tun:
            self.tun.close()
        
        self.close()
        
        logger.info("Client stopped")
    
    def close(self):
        """Закрытие соединения"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            finally:
                self.sock = None
                self.metrics["status"] = "disconnected"
    
    def get_metrics(self) -> dict:
        """Получение метрик"""
        uptime = None
        if self.metrics["connected_at"]:
            uptime = (datetime.now() - self.metrics["connected_at"]).total_seconds()
        
        return {
            "status": self.metrics["status"],
            "client_ip": self.client_ip,
            "uptime_seconds": uptime,
            "bytes_sent": self.metrics["bytes_sent"],
            "bytes_received": self.metrics["bytes_received"],
            "packets_sent": self.metrics["packets_sent"],
            "packets_received": self.metrics["packets_received"],
            "reconnects": self.metrics["reconnects"]
        }
    
    def display_metrics(self):
        """Отображение метрик"""
        m = self.get_metrics()
        
        print("\n" + "=" * 50)
        print("CLIENT METRICS")
        print("=" * 50)
        print(f"Status: {m['status'].upper()}")
        print(f"Client IP: {m['client_ip'] or 'Not assigned'}")
        
        if m['uptime_seconds']:
            uptime = m['uptime_seconds']
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)
            seconds = int(uptime % 60)
            print(f"Uptime: {hours:02d}:{minutes:02d}:{seconds:02d}")
        
        print(f"\nTraffic:")
        print(f"  Sent: {self._format_bytes(m['bytes_sent'])} ({m['packets_sent']} packets)")
        print(f"  Received: {self._format_bytes(m['bytes_received'])} ({m['packets_received']} packets)")
        print(f"\nReconnects: {m['reconnects']}")
        print("=" * 50)
    
    @staticmethod
    def _format_bytes(bytes_count: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_count < 1024.0:
                return f"{bytes_count:.2f} {unit}"
            bytes_count /= 1024.0
        return f"{bytes_count:.2f} TB"


def check_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def main():
    print("=" * 50)
    print("TROJAN VPN CLIENT")
    print("=" * 50)
    
    if not check_admin():
        print("[!] ERROR: Administrator privileges required!")
        sys.exit(1)
    
    if not os.path.exists("wintun.dll"):
        print("[!] ERROR: wintun.dll not found!")
        sys.exit(1)
    
    print(f"Server: {SERVER_HOST}:{SERVER_PORT}")
    print("=" * 50)
    
    client = TrojanVPNClient(SERVER_HOST, SERVER_PORT, PASSWORD)
    
    if not client.start():
        print("[!] Failed to start client")
        return 1
    
    print("\n[+] Client connected successfully!")
    print("\nCommands: status, quit\n")
    
    try:
        while client.running:
            cmd = input().strip().lower()
            if cmd == "status":
                client.display_metrics()
            elif cmd == "quit":
                break
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())