#!/usr/bin/env python3
"""
VPN Client 2026 - Production Ready
Полная интеграция Anti-DPI с PFS и Certificate Pinning
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
import queue
from datetime import datetime
from typing import Optional, Dict, Tuple
from pathlib import Path

from anti_dpi_engine import AntiDPIEngine
from cryptography import x509
from cryptography.hazmat.primitives import serialization

# ============== КОНФИГУРАЦИЯ ==============
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 443
TUN_ADAPTER_NAME = "VPNClient"
VPN_SERVER_IP = "10.8.0.1"
CONFIG_DIR = Path("vpn_client_config")
PASSWORD_FILE = CONFIG_DIR / "client_password.txt"
SERVER_CERT_PIN_FILE = CONFIG_DIR / "server_cert_pin.txt"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============== УПРАВЛЕНИЕ УЧЕТНЫМИ ДАННЫМИ ==============
class CredentialManager:
    def __init__(self):
        self.password: Optional[str] = None
        self.server_cert_pin: Optional[str] = None
        self._load_credentials()

    def _load_credentials(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        if PASSWORD_FILE.exists():
            with open(PASSWORD_FILE, 'r') as f:
                self.password = f.read().strip()
        else:
            self.password = self._prompt_password()
            with open(PASSWORD_FILE, 'w') as f:
                f.write(self.password)
            os.chmod(PASSWORD_FILE, 0o600)

        if SERVER_CERT_PIN_FILE.exists():
            with open(SERVER_CERT_PIN_FILE, 'r') as f:
                raw_pin = f.read().strip()
                if raw_pin:
                    self.server_cert_pin = raw_pin
                    logger.info("[+] Certificate pin loaded")
                else:
                    self.server_cert_pin = None
        else:
            self.server_cert_pin = None

    def _prompt_password(self) -> str:
        print("\n" + "="*60)
        print("  FIRST RUN - ENTER NEW PASSWORD")
        print("="*60)
        while True:
            pwd = input("Enter new VPN password: ")
            confirm = input("Confirm password: ")
            if pwd == confirm and len(pwd) >= 8:
                return pwd
            print("[!] Passwords don't match or too short (min 8 chars). Try again.")

    def verify_certificate(self, cert_der: bytes) -> bool:
        try:
            cert_obj = x509.load_der_x509_certificate(cert_der)
            cert_pem = cert_obj.public_bytes(encoding=serialization.Encoding.PEM).decode('utf-8')
            cert_hash = hashlib.sha256(cert_pem.encode('utf-8')).hexdigest()
            if self.server_cert_pin:
                if cert_hash == self.server_cert_pin:
                    logger.info("[+] Certificate pin verified")
                    return True
                logger.error("[!] Certificate pin mismatch!")
                return False
            logger.warning("[!] No certificate pin configured (first connection)")
            self.set_certificate_pin_by_hash(cert_hash)
            logger.info(f"[+] Certificate pin saved: {cert_hash[:16]}...")
            return True
        except Exception as e:
            logger.error(f"[!] Certificate verification error: {e}")
            return False

    def set_certificate_pin_by_hash(self, cert_hash: str):
        with open(SERVER_CERT_PIN_FILE, 'w') as f:
            f.write(cert_hash)
        os.chmod(SERVER_CERT_PIN_FILE, 0o600)
        self.server_cert_pin = cert_hash

# ============== WINTUN ОБЕРТКА ==============
class WintunWrapper:
    def __init__(self, dll_path: str = "wintun.dll"):
        search_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), dll_path),
            os.path.join(os.getcwd(), dll_path),
            dll_path
        ]
        found_path = next((p for p in search_paths if os.path.exists(p)), None)
        if not found_path:
            raise FileNotFoundError("wintun.dll not found")
        logger.info(f"Loading wintun.dll from: {found_path}")
        self.dll = ctypes.WinDLL(found_path)
        
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [ctypes.c_wchar_p]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        self.WintunCloseAdapter.restype = None
        
        self.WintunStartSession = self.dll.WintunStartSession
        self.WintunStartSession.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        self.WintunStartSession.restype = ctypes.c_void_p
        
        self.WintunEndSession = self.dll.WintunEndSession
        self.WintunEndSession.argtypes = [ctypes.c_void_p]
        self.WintunEndSession.restype = None
        
        self.WintunGetReadWaitEvent = self.dll.WintunGetReadWaitEvent
        self.WintunGetReadWaitEvent.argtypes = [ctypes.c_void_p]
        self.WintunGetReadWaitEvent.restype = ctypes.c_void_p
        
        self.WintunReceivePacket = self.dll.WintunReceivePacket
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        self.WintunReceivePacket.restype = ctypes.c_uint32
        
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunReleaseReceivePacket.restype = None
        
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket
        self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        
        self.WintunSendPacket = self.dll.WintunSendPacket
        self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunSendPacket.restype = None
        
        # Опциональные функции с безопасной загрузкой
        try:
            self.WintunFreeAdapter = self.dll.WintunFreeAdapter
            self.WintunFreeAdapter.argtypes = [ctypes.c_void_p]
            self.WintunFreeAdapter.restype = None
        except AttributeError:
            logger.warning("[*] WintunFreeAdapter not available")
            self.WintunFreeAdapter = None
            
        try:
            self.WintunDeleteAdapter = self.dll.WintunDeleteAdapter
            self.WintunDeleteAdapter.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.POINTER(ctypes.c_wchar_p)]
            self.WintunDeleteAdapter.restype = ctypes.c_bool
        except AttributeError:
            logger.warning("[*] WintunDeleteAdapter not available")
            self.WintunDeleteAdapter = None

    def create_adapter(self, name: str) -> ctypes.c_void_p:
        handle = self.WintunCreateAdapter(name, "VPN", None)
        if not handle:
            raise RuntimeError(f"Failed to create adapter: {ctypes.GetLastError()}")
        return handle

    def open_adapter(self, name: str) -> ctypes.c_void_p:
        handle = self.WintunOpenAdapter(name)
        if not handle:
            raise RuntimeError(f"Failed to open adapter: {ctypes.GetLastError()}")
        return handle

    def start_session(self, handle: ctypes.c_void_p, capacity: int) -> ctypes.c_void_p:
        session = self.WintunStartSession(handle, capacity, None)
        if not session:
            raise RuntimeError(f"Failed to start session: {ctypes.GetLastError()}")
        return session

    def close_adapter(self, handle: ctypes.c_void_p):
        if handle:
            self.WintunCloseAdapter(handle)

    def free_adapter(self, handle: ctypes.c_void_p):
        if handle and self.WintunFreeAdapter:
            self.WintunFreeAdapter(handle)

class TunInterface:
    def __init__(self, name: str = TUN_ADAPTER_NAME):
        self.name = name
        self.wintun = WintunWrapper()
        self.adapter_handle = None
        self.session_handle = None
        self.running = False
        self.read_event = None

    def create(self):
        logger.info(f"[1/3] Creating TUN adapter: {self.name}...")
        try:
            self.adapter_handle = self.wintun.create_adapter(self.name)
            logger.info(f"[+] Virtual adapter '{self.name}' created")
        except Exception:
            logger.info("[-] Adapter might exist, trying to open...")
            self.adapter_handle = self.wintun.open_adapter(self.name)
            logger.info(f"[+] Virtual adapter '{self.name}' opened")
        
        logger.info("[2/3] Starting TUN session...")
        self.session_handle = self.wintun.start_session(self.adapter_handle, 0x400000)
        self.read_event = self.wintun.WintunGetReadWaitEvent(self.session_handle)
        self.running = True
        logger.info("[+] TUN session started")

    def set_ip(self, ip: str):
        logger.info("[3/3] Configuring IP address...")
        try:
            import subprocess
            subprocess.run(f'netsh interface ipv4 delete address "{self.name}"', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 add address "{self.name}" {ip} 255.255.255.0', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 add route 10.8.0.0/24 "{self.name}" metric=1', shell=True, capture_output=True)
            logger.info(f"[+] IP configured: {ip}/24")
        except Exception as e:
            logger.error(f"IP configuration error: {e}")

    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        if not self.session_handle or not self.running:
            return None
        try:
            packet_ptr = ctypes.c_void_p()
            packet_size = ctypes.c_uint32(0)
            result = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
            if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                data = ctypes.string_at(packet_ptr, packet_size.value)
                self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                return data
            if result == 232:
                wait = ctypes.windll.kernel32.WaitForSingleObject(self.read_event, int(timeout * 1000))
                if wait == 0:
                    result = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
                    if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                        data = ctypes.string_at(packet_ptr, packet_size.value)
                        self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                        return data
        except Exception:
            pass
        return None

    def write(self, packet: bytes) -> bool:
        if not self.session_handle or not self.running or not packet:
            return False
        try:
            ptr = self.wintun.WintunAllocateSendPacket(self.session_handle, len(packet))
            if ptr and ptr != 0:
                ctypes.memmove(ptr, packet, len(packet))
                self.wintun.WintunSendPacket(self.session_handle, ptr)
                return True
        except Exception:
            pass
        return False

    def close(self):
        self.running = False
        if self.session_handle:
            self.wintun.WintunEndSession(self.session_handle)
        if self.adapter_handle:
            self.wintun.close_adapter(self.adapter_handle)
            self.wintun.free_adapter(self.adapter_handle)
        try:
            import subprocess
            subprocess.run(f'netsh interface ipv4 delete route 10.8.0.0/24 "{self.name}"', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 delete address "{self.name}"', shell=True, capture_output=True)
        except Exception:
            pass

# ============== КЛИЕНТ ==============
class VPNClient:
    def __init__(self, host: str = SERVER_HOST, port: int = SERVER_PORT):
        self.host = host
        self.port = port
        self.sock: Optional[ssl.SSLSocket] = None
        self.tun: Optional[TunInterface] = None
        self.running = False
        self.anti_dpi: Optional[AntiDPIEngine] = None
        self.cred_manager = CredentialManager()
        self.assigned_ip: Optional[str] = None
        self.stats = {'bytes_sent': 0, 'bytes_received': 0, 'packets_sent': 0, 'packets_received': 0, 'uptime': 0}
        self.stats_lock = threading.Lock()

    def connect(self):
        logger.info(f"Connecting to {self.host}:{self.port}...")
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(10)
        try:
            raw_sock.connect((self.host, self.port))
            logger.info(f"Connected to {self.host}:{self.port}")
            
            # 1. Оборачиваем в TLS (ALPN, SNI и шифры теперь настраиваются внутри AntiDPI)
            self.anti_dpi = AntiDPIEngine(is_server=False)
            self.sock = self.anti_dpi.wrap_socket(raw_sock)
            self.sock.settimeout(30.0)
            
            logger.info("SSL connection established, checking certificate pin...")
            cert_der = self.sock.getpeercert(binary_form=True)
            if not self.cred_manager.verify_certificate(cert_der):
                raise ssl.SSLError("Certificate pin mismatch")
            
            # 2. Авторизация (НОВЫЙ ФОРМАТ: рандомный размер, чтобы не светить паттерн Trojan)
            password = self.cred_manager.password
            password_hash = hashlib.sha224(password.encode()).hexdigest()
            random_pad = secrets.token_bytes(secrets.randbelow(64)) # 0-63 байта мусора
            auth_payload = random_pad + password_hash.encode() + b'\r\n'
            
            # Отправляем: 1 байт длины полезной нагрузки + сама полезная нагрузка
            self.sock.sendall(bytes([len(auth_payload)]) + auth_payload)
            logger.info("[+] Authentication sent")
            
            # 3. X25519 PFS хэндшейк УДАЛЕН (TLS 1.3 уже обеспечивает PFS)
            
            # 4. Получение IP (НОВЫЙ ФОРМАТ: 2 байта длины + данные)
            logger.info("[+] Waiting for IP assignment...")
            header = self._recv_exact(2)
            if len(header) < 2:
                raise ConnectionError("Failed to receive IP header")
            
            length = struct.unpack('!H', header)[0]
            ip_data = self._recv_exact(length)
            if len(ip_data) != length:
                raise ConnectionError("Failed to receive IP packet")
            
            # Дешифрация убрана, читаем как есть
            self.assigned_ip = ip_data.decode()
            logger.info(f"[+] Assigned IP: {self.assigned_ip}")
            
            # 5. Настройка TUN адаптера
            self.tun = TunInterface()
            self.tun.create()
            self.tun.set_ip(self.assigned_ip)
            
            # 6. Запуск потоков (Heartbeat УДАЛЕН — пустые пакеты маячили для DPI)
            self.running = True
            threading.Thread(target=self._tun_reader_loop, daemon=True).start()
            threading.Thread(target=self._network_reader_loop, daemon=True).start()
            threading.Thread(target=self._stats_monitor, daemon=True).start()
            
            logger.info(f"[+] Connected to VPN server at {self.host}:{self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            logger.exception(e)
            return False

    def disconnect(self):
        logger.info("Disconnecting...")
        self.running = False
        if self.sock:
            try: self.sock.close()
            except: pass
        if self.tun:
            self.tun.close()
        logger.info("Disconnected")

    def _tun_reader_loop(self):
        while self.running:
            packet = self.tun.read(timeout=0.05)
            if packet:
                try:
                    # Простой фрейминг вместо тяжелого шифрования
                    framed = self.anti_dpi.encrypt_packet(packet)
                    self.sock.sendall(framed)
                    with self.stats_lock:
                        self.stats['bytes_sent'] += len(framed)
                        self.stats['packets_sent'] += 1
                except Exception:
                    if self.running: break

    def _network_reader_loop(self):
        while self.running:
            try:
                # ✅ Читаем 2 байта заголовка
                header = self._recv_exact(2)
                if len(header) == 0:
                    logger.warning("[!] Server closed connection (EOF)")
                    break
                if len(header) < 2:
                    continue
                length = struct.unpack('!H', header)[0]
                if 0 < length < 65535:
                    encrypted_data = self._recv_exact(length)
                    if len(encrypted_data) == length:
                        # Дешифрация убрана
                        packet = encrypted_data
                        if packet and self.tun:
                            self.tun.write(packet)
                            with self.stats_lock:
                                self.stats['bytes_received'] += len(header) + length
                                self.stats['packets_received'] += 1
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError) as e:
                if self.running:
                    logger.warning(f"[!] Connection error: {e}")
                    break
            except Exception as e:
                if self.running:
                    logger.error(f"[!] Reader error: {e}")
                    break

    def _recv_exact(self, length: int) -> bytes:
        """Безопасное получение байт с корректной обработкой таймаутов и EOF"""
        data = b''
        while len(data) < length:
            try:
                chunk = self.sock.recv(length - len(data))
                if not chunk:
                    return b''  # Настоящий EOF (сервер закрыл соединение)
                data += chunk
            except socket.timeout:
                if not data:
                    raise  # Пробрасываем таймаут, чтобы цикл мог продолжить ожидание
                return data  # Частичные данные (если таймаут сработал посередине чтения)
            except (ConnectionResetError, OSError):
                return b''
        return data

    

    def _stats_monitor(self):
        start = time.time()
        while self.running:
            time.sleep(1)
            with self.stats_lock:
                self.stats['uptime'] = time.time() - start
                if int(self.stats['uptime']) % 10 == 0:
                    logger.info(f"[STATS] Uptime: {int(self.stats['uptime']):02d}s, Tx: {self.stats['bytes_sent']}B, Rx: {self.stats['bytes_received']}B")

def check_admin() -> bool:
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return False

def main():
    if not check_admin():
        print("[!] This script requires administrator privileges.")
        return
    print("="*60)
    print("VPN CLIENT 2026")
    print("="*60)
    print(f"Server: {SERVER_HOST}:{SERVER_PORT}")
    print("="*60)
    client = VPNClient()
    try:
        if client.connect():
            while client.running: time.sleep(1)
        else:
            print("[!] Failed to connect to server")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()