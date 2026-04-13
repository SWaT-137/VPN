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

# Импорт улучшенного anti-DPI модуля
from anti_dpi_engine import AntiDPIEngine, PerfectForwardSecrecy

# ============== КОНФИГУРАЦИЯ ==============
SERVER_HOST = "127.0.0.1"  # ЗАМЕНИТЕ НА IP ВАШЕГО СЕРВЕРА
SERVER_PORT = 443
TUN_NAME = "VPNClient"
MTU = 1500

# Директория для хранения конфигурации
CONFIG_DIR = Path("vpn_client_config")
CONFIG_DIR.mkdir(exist_ok=True)
PASSWORD_FILE = CONFIG_DIR / "password.txt"
SERVER_PUBKEY_FILE = CONFIG_DIR / "server_pubkey.pem"
SERVER_CERT_PIN_FILE = CONFIG_DIR / "server_cert_pin.txt"

# ============== ЛОГИРОВАНИЕ ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('vpn_client.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ============== УПРАВЛЕНИЕ УЧЕТНЫМИ ДАННЫМИ ==============
class CredentialManager:
    """Управление учетными данными клиента"""
    
    def __init__(self):
        self.password: Optional[str] = None
        self.server_cert_pin: Optional[str] = None
        self._load_credentials()
    
    def _load_credentials(self):
        """Загрузка учетных данных"""
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
                self.server_cert_pin = f.read().strip()
            logger.info("[+] Certificate pin loaded")
    
    def _prompt_password(self) -> str:
        """Запрос пароля"""
        import getpass
        print("\n" + "=" * 50)
        print("VPN CLIENT SETUP")
        print("=" * 50)
        password = getpass.getpass("Enter VPN password: ")
        return password
    
    def set_certificate_pin(self, cert_pem: str):
        """Установка пина сертификата сервера"""
        # Вычисляем SHA256 хеш сертификата
        cert_hash = hashlib.sha256(cert_pem.encode()).hexdigest()
        self.server_cert_pin = cert_hash
        
        with open(SERVER_CERT_PIN_FILE, 'w') as f:
            f.write(cert_hash)
        os.chmod(SERVER_CERT_PIN_FILE, 0o600)
        
        logger.info(f"[+] Certificate pin set: {cert_hash[:16]}...")
    
    def verify_certificate(self, cert_der: bytes) -> bool:
        """Проверка сертификата по пину"""
        # Вычисляем SHA256 хеш сертификата
        cert_hash = hashlib.sha256(cert_der).hexdigest()
        
        if not self.server_cert_pin:
            logger.warning("[!] No certificate pin configured (first connection)")
            # Сохраняем пин при первом подключении
            self.set_certificate_pin_by_hash(cert_hash)
            return True
        
        if cert_hash != self.server_cert_pin:
            logger.error(f"[!] CERTIFICATE PIN MISMATCH!")
            logger.error(f"    Expected: {self.server_cert_pin[:16]}...")
            logger.error(f"    Got:      {cert_hash[:16]}...")
            logger.error(f"    This could indicate a MITM attack!")
            return False
        
        return True
    
    def set_certificate_pin_by_hash(self, cert_hash: str):
        """Установка пина по хешу сертификата"""
        self.server_cert_pin = cert_hash
        
        with open(SERVER_CERT_PIN_FILE, 'w') as f:
            f.write(cert_hash)
        os.chmod(SERVER_CERT_PIN_FILE, 0o600)
        
        logger.info(f"[+] Certificate pin saved: {cert_hash[:16]}...")


# ============== WINTUN ОБЕРТКА ==============
class WintunWrapper:
    """Обертка для wintun.dll"""
    
    def __init__(self, dll_path: str = "wintun.dll"):
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
        
        # Обязательные функции
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [ctypes.c_wchar_p]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        
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
        
        # Опциональные функции
        try:
            self.WintunDeleteAdapter = self.dll.WintunDeleteAdapter
            self.WintunDeleteAdapter.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            self.WintunDeleteAdapter.restype = ctypes.c_bool
        except AttributeError:
            self.WintunDeleteAdapter = None


class TUNInterface:
    """Виртуальный TUN интерфейс для клиента"""
    
    def __init__(self, name: str):
        self.name = name
        self.handle = None
        self.running = True
        self.wintun: Optional[WintunWrapper] = None
        self.ip: Optional[str] = None
        
        self._initialize()
    
    def _initialize(self):
        """Инициализация интерфейса"""
        try:
            self.wintun = WintunWrapper("wintun.dll")
        except Exception as e:
            logger.error(f"Wintun load error: {e}")
            raise
        
        # Открываем или создаем адаптер
        self.handle = self.wintun.WintunOpenAdapter(self.name)
        
        if not self.handle or self.handle == 0:
            logger.info(f"Creating new adapter: {self.name}")
            self.handle = self.wintun.WintunCreateAdapter(self.name, "Wintun", None)
            
            if not self.handle or self.handle == 0:
                raise Exception(f"Failed to create adapter {self.name}")
        
        logger.info(f"[+] Virtual adapter '{self.name}' ready")
    
    def set_ip(self, ip: str):
        """Установка IP адреса"""
        self.ip = ip
        
        try:
            import subprocess
            
            # Устанавливаем IP
            cmd = f'netsh interface ip set address "{self.name}" static {ip} 255.255.255.0'
            result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            
            if result.returncode == 0:
                logger.info(f"[+] IP configured: {ip}/24")
            
            # Включаем интерфейс
            subprocess.run(f'netsh interface set interface "{self.name}" admin=enabled',
                          capture_output=True, shell=True)
            
            # Настраиваем DNS
            subprocess.run(f'netsh interface ip set dns "{self.name}" static 1.1.1.1',
                          capture_output=True, shell=True)
            subprocess.run(f'netsh interface ip add dns "{self.name}" 8.8.8.8 index=2',
                          capture_output=True, shell=True)
            
            # Добавляем маршрут для VPN трафика
            subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {ip} metric 1',
                          capture_output=True, shell=True)
            
        except Exception as e:
            logger.error(f"IP configuration error: {e}")
    
    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        """Чтение пакета из TUN"""
        if not self.handle or not self.running:
            return None
        
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
            return None
    
    def write(self, packet: bytes) -> bool:
        """Запись пакета в TUN"""
        if not self.handle or not self.running or not packet:
            return False
        
        try:
            packet_ptr = self.wintun.WintunAllocateSendPacket(self.handle, len(packet))
            
            if packet_ptr and packet_ptr != 0:
                ctypes.memmove(packet_ptr, packet, len(packet))
                self.wintun.WintunSendPacket(self.handle, packet_ptr)
                return True
            
            return False
            
        except Exception as e:
            return False
    
    def close(self):
        """Закрытие интерфейса"""
        self.running = False
        
        # Удаляем маршрут
        try:
            import subprocess
            subprocess.run('route delete 0.0.0.0', capture_output=True, shell=True)
        except:
            pass
        
        if self.handle:
            try:
                self.wintun.WintunCloseAdapter(self.handle)
                logger.info(f"[+] Adapter '{self.name}' closed")
            except:
                pass
            finally:
                self.handle = None


# ============== VPN КЛИЕНТ ==============
class VPNClient:
    """Главный VPN клиент"""
    
    def __init__(self, server_host: str, server_port: int):
        self.server_host = server_host
        self.server_port = server_port
        
        self.sock: Optional[socket.socket] = None
        self.running = False
        self.assigned_ip: Optional[str] = None
        
        self.cred_manager = CredentialManager()
        self.anti_dpi: Optional[AntiDPIEngine] = None
        self.tun: Optional[TUNInterface] = None
        
        self.tun_thread: Optional[threading.Thread] = None
        self.network_thread: Optional[threading.Thread] = None
        self.reconnect_thread: Optional[threading.Thread] = None
        
        # Статистика
        self.stats = {
            'connected_at': None,
            'bytes_sent': 0,
            'bytes_received': 0,
            'packets_sent': 0,
            'packets_received': 0,
            'reconnects': 0,
            'status': 'disconnected'
        }
        self.stats_lock = threading.RLock()
        
        # Очередь для отправки пакетов
        self.send_queue = queue.Queue(maxsize=1000)
        
        logger.info("[+] VPN Client initialized")
    
    def connect(self) -> bool:
        """Подключение к серверу"""
        try:
            logger.info(f"Connecting to {self.server_host}:{self.server_port}...")
            
            # Создаем TCP сокет
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            raw_sock.settimeout(15)
            raw_sock.connect((self.server_host, self.server_port))
            
            # Инициализируем Anti-DPI
            
            # Для первого подключения (если нет пина) отключаем проверку сертификата
            verify_cert = self.cred_manager.server_cert_pin is not None
            if not verify_cert:
                logger.warning("[!] First connection - certificate verification disabled")
                logger.warning("[!] Certificate pin will be saved for future connections")
            
            self.anti_dpi = AntiDPIEngine(is_server=False)
            self.sock = self.anti_dpi.wrap_socket(raw_sock, verify_cert=verify_cert)
            
            logger.info("[+] SSL connection established")
            logger.info(f"[*] TLS Profile: {self.anti_dpi.tls_impersonator.current_profile}")
            logger.info(f"[*] SNI Spoofed: {self.anti_dpi.sni_spoofer.current_sni}")
            
            # Получаем сертификат сервера и проверяем/сохраняем пин
            cert_der = self.sock.getpeercert(binary_form=True)
            if not self.cred_manager.verify_certificate(cert_der):
                logger.error("[!] Certificate pin verification failed! Possible MITM attack!")
                return False
            
            logger.info("[+] Certificate pin verified")
            
            # Trojan аутентификация
            password = self.cred_manager.password
            
            # Генерируем хеш пароля
            password_hash = hashlib.sha224(password.encode()).hexdigest()
            
            # Добавляем nonce для защиты от replay
            client_nonce = secrets.token_bytes(6)
            
            auth_data = password_hash.encode() + client_nonce + b'\r\n'
            self.sock.send(auth_data)
            logger.info("[+] Authentication sent")
            
            # Выполняем PFS handshake
            session_keys = self.anti_dpi.perform_handshake(self.sock)
            logger.info("[+] PFS handshake completed")
            
            # Получаем назначенный IP
            # Сначала читаем заголовок
            header = self._recv_exact(14)
            if len(header) < 14:
                logger.error("Failed to receive IP header")
                return False
            
            length = struct.unpack('!H', header[12:14])[0]
            encrypted_data = self._recv_exact(length)
            
            if len(encrypted_data) == length:
                decrypted_ip = self.anti_dpi.decrypt_packet(header + encrypted_data)
                
                if decrypted_ip:
                    self.assigned_ip = decrypted_ip.decode()
                    logger.info(f"[+] Assigned IP: {self.assigned_ip}")
                else:
                    logger.error("Failed to decrypt assigned IP")
                    return False
            else:
                logger.error("Failed to receive complete IP packet")
                return False
            
            # Создаем TUN интерфейс
            self.tun = TUNInterface(TUN_NAME)
            self.tun.set_ip(self.assigned_ip)
            
            with self.stats_lock:
                self.stats['connected_at'] = datetime.now()
                self.stats['status'] = 'connected'
            
            return True
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
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
    
    def start(self) -> bool:
        """Запуск клиента"""
        if self.running:
            return False
        
        if not self.connect():
            logger.error("Failed to connect")
            return False
        
        self.running = True
        
        # Запускаем потоки
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        
        self.network_thread = threading.Thread(target=self._network_reader, daemon=True)
        self.network_thread.start()
        
        self.send_thread = threading.Thread(target=self._send_worker, daemon=True)
        self.send_thread.start()
        
        self.reconnect_thread = threading.Thread(target=self._reconnect_monitor, daemon=True)
        self.reconnect_thread.start()
        
        logger.info("[+] Client started")
        return True
    
    def _tun_reader(self):
        """Чтение из TUN и отправка на сервер"""
        logger.info("[*] TUN reader started")
        
        while self.running and self.sock:
            try:
                packet = self.tun.read(timeout=0.1)
                
                if packet and len(packet) >= 20:
                    self.send_queue.put_nowait(packet)
                    
            except queue.Full:
                logger.debug("Send queue full, dropping packet")
            except Exception as e:
                if self.running:
                    logger.debug(f"TUN reader error: {e}")
    
    def _send_worker(self):
        """Отправка пакетов на сервер"""
        logger.info("[*] Send worker started")
        
        while self.running and self.sock:
            try:
                packet = self.send_queue.get(timeout=0.5)
                
                # Шифруем и отправляем
                encrypted = self.anti_dpi.encrypt_packet(packet)
                self.sock.send(encrypted)
                
                with self.stats_lock:
                    self.stats['bytes_sent'] += len(encrypted)
                    self.stats['packets_sent'] += 1
                
            except queue.Empty:
                continue
            except Exception as e:
                if self.running:
                    logger.debug(f"Send error: {e}")
                    break
    
    def _network_reader(self):
        """Чтение из сети и отправка в TUN"""
        logger.info("[*] Network reader started")
        
        while self.running and self.sock:
            try:
                self.sock.settimeout(0.5)
                
                # Читаем заголовок
                header = self._recv_exact(14)
                
                if len(header) < 14:
                    if len(header) == 0:
                        break
                    continue
                
                length = struct.unpack('!H', header[12:14])[0]
                
                if 0 < length < 65535:
                    encrypted_data = self._recv_exact(length)
                    
                    if len(encrypted_data) == length:
                        # Дешифруем пакет
                        packet = self.anti_dpi.decrypt_packet(header + encrypted_data)
                        
                        if packet and self.tun:
                            self.tun.write(packet)
                            
                            with self.stats_lock:
                                self.stats['bytes_received'] += len(header) + length
                                self.stats['packets_received'] += 1
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.debug(f"Network reader error: {e}")
                    break
        
        # Если соединение разорвано и мы все еще работаем - переподключаемся
        if self.running:
            logger.warning("[!] Connection lost")
            with self.stats_lock:
                self.stats['status'] = 'reconnecting'
            self._reconnect()
    
    def _reconnect_monitor(self):
        """Мониторинг состояния соединения"""
        while self.running:
            time.sleep(5)
            
            if self.sock is None and self.running:
                self._reconnect()
    
    def _reconnect(self):
        """Переподключение к серверу"""
        with self.stats_lock:
            self.stats['reconnects'] += 1
            self.stats['status'] = 'reconnecting'
        
        logger.info(f"[*] Reconnecting (attempt #{self.stats['reconnects']})...")
        
        # Закрываем старое соединение
        self._close_socket()
        
        # Ждем перед переподключением
        time.sleep(min(5 + self.stats['reconnects'] * 2, 30))
        
        if self.running:
            if self.connect():
                with self.stats_lock:
                    self.stats['status'] = 'connected'
                logger.info("[+] Reconnected successfully")
            else:
                logger.warning("[!] Reconnect failed, will retry")
    
    def _close_socket(self):
        """Закрытие сокета"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            finally:
                self.sock = None
    
    def stop(self):
        """Остановка клиента"""
        logger.info("Stopping client...")
        self.running = False
        
        with self.stats_lock:
            self.stats['status'] = 'disconnected'
        
        # Закрываем сокет
        self._close_socket()
        
        # Закрываем TUN
        if self.tun:
            self.tun.close()
        
        logger.info("Client stopped")
    
    def get_stats(self) -> Dict:
        """Получение статистики"""
        with self.stats_lock:
            stats = self.stats.copy()
            
            if stats['connected_at']:
                uptime = (datetime.now() - stats['connected_at']).total_seconds()
                stats['uptime_seconds'] = int(uptime)
            
            if self.anti_dpi:
                stats['anti_dpi'] = self.anti_dpi.get_stats()
            
            return stats
    
    def display_stats(self):
        """Отображение статистики"""
        s = self.get_stats()
        
        print("\n" + "=" * 60)
        print("VPN CLIENT STATUS")
        print("=" * 60)
        print(f"Status:        {s['status'].upper()}")
        print(f"Server:        {self.server_host}:{self.server_port}")
        print(f"Assigned IP:   {self.assigned_ip or 'N/A'}")
        
        if s.get('uptime_seconds'):
            uptime = s['uptime_seconds']
            hours = uptime // 3600
            minutes = (uptime % 3600) // 60
            seconds = uptime % 60
            print(f"Uptime:        {hours:02d}:{minutes:02d}:{seconds:02d}")
        
        print(f"\nTraffic:")
        print(f"  Sent:        {self._format_bytes(s['bytes_sent'])} ({s['packets_sent']} packets)")
        print(f"  Received:    {self._format_bytes(s['bytes_received'])} ({s['packets_received']} packets)")
        print(f"\nReconnects:    {s['reconnects']}")
        
        if 'anti_dpi' in s:
            ad = s['anti_dpi']
            print(f"\nAnti-DPI:")
            print(f"  TLS Profile: {ad.get('tls_profile', 'N/A')}")
            print(f"  SNI:         {ad.get('current_sni', 'N/A')}")
            print(f"  Handshakes:  {ad.get('handshakes', 0)}")
        
        print("=" * 60)
    
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


def setup_signal_handlers(client: VPNClient):
    """Настройка обработчиков сигналов"""
    def signal_handler(signum, frame):
        logger.info(f"\n[!] Received signal {signum}")
        client.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


# ============== ТОЧКА ВХОДА ==============
def main():
    print("=" * 60)
    print("VPN CLIENT 2026")
    print("=" * 60)
    
    # Проверка прав администратора
    if not check_admin():
        print("[!] ERROR: Administrator privileges required!")
        print("    Run this script as Administrator")
        return 1
    
    # Проверка наличия wintun.dll
    if not os.path.exists("wintun.dll"):
        print("[!] ERROR: wintun.dll not found!")
        print("    Download from: https://www.wintun.net/")
        return 1
    
    print(f"Server: {SERVER_HOST}:{SERVER_PORT}")
    print("=" * 60)
    
    # Создаем и запускаем клиент
    client = VPNClient(SERVER_HOST, SERVER_PORT)
    setup_signal_handlers(client)
    
    if not client.start():
        print("[!] Failed to connect to server")
        return 1
    
    print("\n[+] Connected successfully!")
    print("\nCommands: status, stats, quit\n")
    
    try:
        while client.running:
            try:
                cmd = input().strip().lower()
                
                if cmd in ('status', 'stats'):
                    client.display_stats()
                elif cmd == 'quit':
                    break
                elif cmd:
                    print("Unknown command. Use: status, stats, quit")
                    
            except EOFError:
                time.sleep(0.1)
                
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
    
    print("\n[+] Client shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())