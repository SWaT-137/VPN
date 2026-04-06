#!/usr/bin/env python3
"""
VPN-сервер для Windows (ДОРАБОТАННАЯ ВЕРСИЯ)
Поддержка постоянных соединений, метрик и правильной обработки keep-alive
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
from ctypes import wintypes
from datetime import datetime

# Криптография
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# Trojan протокол
from protocol import TrojanProtocol, FakeWebServer

# Конфигурация
HOST = "0.0.0.0"
PORT = 443
PASSWORD = "mysecretpassword123"
CERTFILE = "server.crt"
KEYFILE = "server.key"
TUN_NAME = "VPNServer"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"

class Wintun:
    """Обёртка для работы с wintun.dll через ctypes"""
    
    def __init__(self, dll_path="wintun.dll"):
        if not os.path.isabs(dll_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            full_path = os.path.join(script_dir, dll_path)
            
            if not os.path.exists(full_path):
                full_path = os.path.join(os.getcwd(), dll_path)
            
            if not os.path.exists(full_path):
                sys32_path = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', dll_path)
                if os.path.exists(sys32_path):
                    full_path = sys32_path
                else:
                    raise FileNotFoundError(f"Не удалось найти {dll_path}")
            
            dll_path = full_path
        
        print(f"[*] Загрузка wintun.dll из: {dll_path}")
        self.dll = ctypes.WinDLL(dll_path)
        
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
        self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        
        self.WintunSendPacket = self.dll.WintunSendPacket
        self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunSendPacket.restype = None
        
        self.WintunReceivePacket = self.dll.WintunReceivePacket
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        self.WintunReceivePacket.restype = wintypes.DWORD
        
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunReleaseReceivePacket.restype = None
    
    def create_adapter(self, name, tunnel_type="Wintun", requested_guid=None):
        return self.WintunCreateAdapter(name, tunnel_type, requested_guid)
    
    def close_adapter(self, handle):
        self.WintunCloseAdapter(handle)

class CryptoEngine:
    def __init__(self, password: str):
        self.salt = b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f'
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=100000,
            backend=default_backend()
        )
        self.key = kdf.derive(password.encode())
        self.cipher = AESGCM(self.key)
        print("[+] Криптодвижок инициализирован")
    
    def encrypt(self, data: bytes, nonce: bytes) -> bytes:
        return self.cipher.encrypt(nonce, data, None)
    
    def decrypt(self, data: bytes, nonce: bytes) -> bytes:
        return self.cipher.decrypt(nonce, data, None)

class TUNInterface:
    def __init__(self, name: str, ip: str, netmask: str):
        self.name = name
        self.handle = None
        
        if not os.path.exists("wintun.dll"):
            print("[!] ОШИБКА: wintun.dll не найден!")
            sys.exit(1)
        
        self.wintun = Wintun("wintun.dll")
        self.handle = self.wintun.create_adapter(name)
        
        if not self.handle:
            raise Exception(f"Не удалось создать адаптер: {name}")
        
        print(f"[+] Виртуальный адаптер создан: {name}")
        
        import subprocess
        subprocess.run(
            f'netsh interface ip set address "{name}" static {ip} {netmask}',
            capture_output=True,
            shell=True
        )
        print(f"[+] IP-адрес назначен: {ip}/{netmask}")
    
    def read(self, size: int = 2000) -> bytes:
        if not self.handle:
            return b''
        
        packet_ptr = ctypes.c_void_p()
        packet_size = wintypes.DWORD()
        
        result = self.wintun.WintunReceivePacket(self.handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
        
        if result == 0 and packet_ptr:
            data = ctypes.string_at(packet_ptr, packet_size.value)
            self.wintun.WintunReleaseReceivePacket(self.handle, packet_ptr)
            return data
        
        return b''
    
    def write(self, packet: bytes):
        if not self.handle:
            return
        
        packet_ptr = self.wintun.WintunAllocateSendPacket(self.handle, len(packet))
        if packet_ptr:
            ctypes.memmove(packet_ptr, packet, len(packet))
            self.wintun.WintunSendPacket(self.handle, packet_ptr)
    
    def close(self):
        if self.handle:
            self.wintun.WintunCloseAdapter(self.handle)
            self.handle = None

class ClientHandler:
    """Обрабатывает одного клиента в отдельном потоке"""
    
    def __init__(self, ssl_sock, addr, vpn_server):
        self.ssl_sock = ssl_sock
        self.addr = addr
        self.vpn_server = vpn_server
        self.client_ip = None
        self.running = True
        self.trojan_protocol = TrojanProtocol(PASSWORD)
        self.fake_web_server = FakeWebServer()
        self.last_activity = time.time()
        self.last_keepalive = time.time()
        self.metrics = {
            "bytes_sent": 0,
            "bytes_received": 0,
            "packets_sent": 0,
            "packets_received": 0,
            "connected_at": datetime.now()
        }
    
    def _trojan_authenticate(self) -> bool:
        """Аутентификация по Trojan протоколу"""
        try:
            # Устанавливаем таймаут для чтения аутентификации
            self.ssl_sock.settimeout(10)
            
            # Читаем аутентификационные данные
            auth_success, web_data = self.trojan_protocol.authenticate_client(self.ssl_sock)
            
            if auth_success:
                print(f"[+] Trojan аутентификация успешна от {self.addr}")
                return True
            else:
                print(f"[WEB] Неавторизованный запрос от {self.addr}, перенаправляем на веб-сервер")
                if web_data:
                    self.fake_web_server.serve_fake_response_with_data(self.ssl_sock, web_data)
                else:
                    self.fake_web_server.serve_fake_response(self.ssl_sock)
                return False
        except socket.timeout:
            print(f"[-] Таймаут аутентификации от {self.addr}")
            return False
        except Exception as e:
            print(f"[-] Ошибка Trojan аутентификации: {e}")
            return False
    
    def _handle_keepalive(self):
        """Обработка keep-alive сообщений"""
        current_time = time.time()
        
        # Проверяем, нужно ли отправить keep-alive
        if current_time - self.last_keepalive > 30:
            try:
                # Отправляем PING для проверки
                self.ssl_sock.send(b"PING\r\n\r\n")
                self.last_keepalive = current_time
                print(f"[DEBUG] Отправлен PING клиенту {self.client_ip}")
                return True
            except Exception as e:
                print(f"[-] Ошибка отправки keep-alive: {e}")
                self.running = False
                return False
        return True
    def _check_activity(self):
        """Проверка активности клиента"""
        current_time = time.time()
        if current_time - self.last_activity > 300:  # 5 минут без активности
            print(f"[-] Клиент {self.client_ip} неактивен более 5 минут, отключаем")
            self.running = False
            return False
        return True
    def run(self):
        """Основной метод обработки клиента"""
        try:
            # Trojan аутентификация
            if not self._trojan_authenticate():
                return
            
            print(f"[+] Аутентификация успешна от {self.addr}")
            
            # Выделение IP
            with self.vpn_server.ip_lock:
                self.client_ip = f"10.8.0.{self.vpn_server.next_ip}"
                self.vpn_server.next_ip += 1
            
            # Создаем уникальный nonce для этого клиента
            client_nonce = os.urandom(12)
            
            # Регистрация клиента
            with self.vpn_server.clients_lock:
                self.vpn_server.clients[self.client_ip] = {
                    'socket': self.ssl_sock,
                    'nonce': client_nonce,
                    'last_activity': time.time(),
                    'handler': self,
                    'metrics': self.metrics
                }
            
            # Отправляем IP клиенту
            try:
                self.ssl_sock.send(self.client_ip.encode())
                print(f"[+] Клиент {self.addr[0]}:{self.addr[1]} -> {self.client_ip}")
            except Exception as e:
                print(f"[-] Ошибка отправки IP клиенту: {e}")
                return
            
            self.last_activity = time.time()
            self.last_keepalive = time.time()
            
            # Основной цикл приема данных от клиента
            while self.running and self.vpn_server.running:
                try:
                    # Проверяем активность клиента
                    if not self._check_activity():
                        break
                    
                    # Отправляем keep-alive если нужно
                    self._handle_keepalive()
                    
                    # Устанавливаем таймаут для recv
                    self.ssl_sock.settimeout(5)
                    
                    # Пытаемся прочитать данные от клиента
                    try:
                        # Сначала проверяем, есть ли данные
                        header = self.ssl_sock.recv(14)
                        
                        if len(header) == 0:
                            print(f"[-] Клиент {self.client_ip} закрыл соединение")
                            break
                        
                        # Проверяем, не keep-alive ли это сообщение
                        if header == b"PING":
                            self.ssl_sock.send(b"PONG\r\n\r\n")
                            self.last_keepalive = time.time()
                            self.last_activity = time.time()
                            print(f"[DEBUG] Получен PING от {self.client_ip}, отправлен PONG")
                            continue
                        
                        if header == b"PONG":
                            self.last_keepalive = time.time()
                            self.last_activity = time.time()
                            print(f"[DEBUG] Получен PONG от {self.client_ip}")
                            continue
                        
                        # Если это не keep-alive, проверяем формат
                        if len(header) >= 14:
                            nonce = header[:12]
                            length = struct.unpack('!H', header[12:14])[0]
                            
                            # Читаем зашифрованные данные
                            if length > 0 and length < 65535:  # Максимальный размер пакета
                                encrypted = self.ssl_sock.recv(length)
                                if len(encrypted) == length:
                                    # Обновляем метрики
                                    self.metrics["bytes_received"] += len(encrypted) + 14
                                    self.metrics["packets_received"] += 1
                                    
                                    try:
                                        # Расшифровываем
                                        packet = self.vpn_server.crypto.decrypt(encrypted, nonce)
                                        
                                        # Отправляем в TUN
                                        if len(packet) > 0:
                                            self.vpn_server.tun.write(packet)
                                            self.last_activity = time.time()
                                            print(f"[DEBUG] Получен пакет от {self.client_ip}, размер: {len(packet)} байт")
                                    except Exception as e:
                                        print(f"[-] Ошибка расшифровки от {self.client_ip}: {e}")
                                else:
                                    print(f"[-] Неполучены данные: ожидалось {length}, получено {len(encrypted)}")
                            else:
                                print(f"[-] Некорректная длина пакета: {length}")
                        else:
                            # Неизвестные данные, возможно, просто игнорируем
                            print(f"[DEBUG] Получены неизвестные данные от {self.client_ip}: {header[:50]}")
                            
                    except socket.timeout:
                        # Таймаут - нормально, продолжаем цикл
                        continue
                    except ConnectionResetError:
                        print(f"[-] Соединение сброшено клиентом {self.client_ip}")
                        break
                    except BrokenPipeError:
                        print(f"[-] Соединение разорвано клиентом {self.client_ip}")
                        break
                    
                except Exception as e:
                    if "timed out" not in str(e).lower():
                        print(f"[-] Ошибка в основном цикле для {self.client_ip}: {e}")
                        break
        
        except Exception as e:
            print(f"[-] Критическая ошибка в потоке клиента {self.addr}: {e}")
        
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Очистка ресурсов клиента"""
        if self.client_ip:
            with self.vpn_server.clients_lock:
                if self.client_ip in self.vpn_server.clients:
                    # Выводим финальную статистику
                    uptime = (datetime.now() - self.metrics["connected_at"]).total_seconds()
                    print(f"\n📊 Статистика клиента {self.client_ip}:")
                    print(f"   Время подключения: {uptime:.0f} сек")
                    print(f"   Отправлено: {self.metrics['bytes_sent']} байт ({self.metrics['packets_sent']} пакетов)")
                    print(f"   Получено: {self.metrics['bytes_received']} байт ({self.metrics['packets_received']} пакетов)")
                    del self.vpn_server.clients[self.client_ip]
            print(f"[-] Клиент отключён: {self.client_ip}")
        
        try:
            self.ssl_sock.close()
        except:
            pass
    
    def send_to_client(self, packet: bytes, nonce: bytes):
        """Отправка данных клиенту"""
        try:
            encrypted = self.vpn_server.crypto.encrypt(packet, nonce)
            message = nonce + struct.pack('!H', len(encrypted)) + encrypted
            self.ssl_sock.send(message)
            self.metrics["bytes_sent"] += len(message)
            self.metrics["packets_sent"] += 1
            return True
        except Exception as e:
            print(f"[-] Ошибка отправки клиенту {self.client_ip}: {e}")
            return False

class VPNServer:
    def __init__(self):
        self.running = True
        self.clients = {}
        self.clients_lock = threading.Lock()
        self.ip_lock = threading.Lock()
        self.next_ip = 2
        self.server_metrics = {
            "started_at": datetime.now(),
            "total_connections": 0,
            "active_connections": 0,
            "total_bytes_sent": 0,
            "total_bytes_received": 0
        }
        
        print("[1/5] Инициализация криптографии...")
        self.crypto = CryptoEngine(PASSWORD)
        
        print("[2/5] Инициализация TUN-интерфейса...")
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        
        print("[3/5] Запуск потока чтения из TUN...")
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        
        print("[4/5] Запуск мониторинга клиентов...")
        self.monitor_thread = threading.Thread(target=self._monitor_clients, daemon=True)
        self.monitor_thread.start()
        
        print("[5/5] Запуск SSL-сервера...")
        self._init_server()
        
        print("\n" + "=" * 50)
        print("VPN СЕРВЕР ГОТОВ К РАБОТЕ!")
        print("=" * 50)
        print(f"Адрес:     {HOST}:{PORT}")
        print(f"VPN-сеть:  10.8.0.0/24")
        print(f"IP сервера: {VPN_SERVER_IP}")
        print("=" * 50)
    
    def _init_server(self):
        """Инициализация TCP сервера с SSL"""
        self.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.context.load_cert_chain(CERTFILE, KEYFILE)
        
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((HOST, PORT))
        self.server_sock.listen(5)
    
    def _tun_reader(self):
        """Поток чтения из TUN - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        print("[*] Поток чтения TUN запущен")
        
        while self.running:
            try:
                packet = self.tun.read()
                if len(packet) < 20:
                    time.sleep(0.01)
                    continue
                
                # Определяем IP назначения
                dest_ip = socket.inet_ntoa(packet[16:20])
                
                with self.clients_lock:
                    if dest_ip in self.clients:
                        client = self.clients[dest_ip]
                        # Используем метод отправки клиенту
                        if 'handler' in client:
                            # Обновляем nonce для каждого пакета
                            new_nonce = os.urandom(12)
                            success = client['handler'].send_to_client(packet, new_nonce)
                            if success:
                                client['nonce'] = new_nonce
                                client['last_activity'] = time.time()
                                self.server_metrics["total_bytes_sent"] += len(packet)
                            else:
                                print(f"[-] Ошибка отправки клиенту {dest_ip}, удаляем")
                                del self.clients[dest_ip]
                    elif dest_ip.startswith("10.8.0."):
                        # IP в нашей VPN сети но клиент не найден
                        pass
            except Exception as e:
                if self.running:
                    print(f"[-] Ошибка в TUN reader: {e}")
                    time.sleep(0.1)
    
    def _monitor_clients(self):
        """Мониторинг клиентов и их активности"""
        while self.running:
            time.sleep(60)  # Проверяем каждую минуту
            
            with self.clients_lock:
                current_time = time.time()
                inactive_clients = []
                
                for ip, client in self.clients.items():
                    # Если клиент неактивен более 5 минут
                    if current_time - client.get('last_activity', 0) > 300:
                        inactive_clients.append(ip)
                        print(f"[!] Клиент {ip} неактивен > 5 минут, отключаем")
                        try:
                            client['socket'].close()
                        except:
                            pass
                
                # Удаляем неактивных клиентов
                for ip in inactive_clients:
                    del self.clients[ip]
                
                # Обновляем метрику активных соединений
                self.server_metrics["active_connections"] = len(self.clients)
    
    def display_server_metrics(self):
        """Отображение метрик сервера"""
        uptime = (datetime.now() - self.server_metrics["started_at"]).total_seconds()
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        
        print("\n" + "=" * 60)
        print("📊 МЕТРИКИ VPN СЕРВЕРА")
        print("=" * 60)
        print(f"Аптайм сервера: {hours:02d}:{minutes:02d}:{int(uptime % 60):02d}")
        print(f"\n📡 Соединения:")
        print(f"  Активных: {self.server_metrics['active_connections']}")
        print(f"  Всего подключений: {self.server_metrics['total_connections']}")
        print(f"\n📦 Трафик:")
        print(f"  Отправлено: {self.format_bytes(self.server_metrics['total_bytes_sent'])}")
        print(f"  Получено: {self.format_bytes(self.server_metrics['total_bytes_received'])}")
        
        if self.clients:
            print(f"\n👥 Активные клиенты:")
            for ip, client in self.clients.items():
                metrics = client.get('handler', {}).metrics if 'handler' in client else {}
                uptime_client = (datetime.now() - metrics.get('connected_at', datetime.now())).total_seconds()
                print(f"  {ip} - активен {int(uptime_client)} сек")
        
        print("=" * 60)
    
    @staticmethod
    def format_bytes(bytes_count):
        """Форматирование байтов"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_count < 1024.0:
                return f"{bytes_count:.2f} {unit}"
            bytes_count /= 1024.0
        return f"{bytes_count:.2f} TB"
    
    def run(self):
        """Основной цикл приёма клиентов"""
        print(f"[*] Сервер слушает {HOST}:{PORT}")
        
        # Запускаем поток для отображения метрик по запросу
        def metrics_printer():
            while self.running:
                time.sleep(30)
                # Раскомментируйте для автоматического вывода метрик каждые 30 секунд
                # self.display_server_metrics()
        
        metrics_thread = threading.Thread(target=metrics_printer, daemon=True)
        metrics_thread.start()
        
        while self.running:
            try:
                raw_sock, addr = self.server_sock.accept()
                print(f"[*] Принято соединение от {addr}, выполняем SSL handshake...")
                
                ssl_sock = self.context.wrap_socket(raw_sock, server_side=True)
                print(f"[+] SSL handshake завершён для {addr}")
                
                # Обновляем метрики
                self.server_metrics["total_connections"] += 1
                self.server_metrics["active_connections"] += 1
                
                handler = ClientHandler(ssl_sock, addr, self)
                thread = threading.Thread(target=handler.run, daemon=True)
                thread.start()
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                if self.running:
                    print(f"[-] Ошибка принятия соединения: {e}")
    
    def stop(self):
        """Остановка сервера - ФУНКЦИЯ ОТКЛЮЧЕНИЯ"""
        print("\n🔌 Остановка сервера...")
        self.running = False
        
        # Выводим финальную статистику
        self.display_server_metrics()
        
        # Закрываем все клиентские соединения
        with self.clients_lock:
            for ip, client in self.clients.items():
                try:
                    print(f"  Отключение клиента {ip}...")
                    client['socket'].close()
                except:
                    pass
            self.clients.clear()
        
        # Закрываем серверный сокет
        try:
            self.server_sock.close()
        except:
            pass
        
        # Закрываем TUN
        self.tun.close()
        
        print("[+] Сервер остановлен")

def generate_ssl_cert():
    """Генерация SSL сертификата"""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    
    if os.path.exists(CERTFILE) and os.path.exists(KEYFILE):
        print("[*] SSL сертификат уже существует")
        return
    
    print("[*] Генерация SSL сертификата...")
    
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
    
    print("[+] SSL сертификат создан")

def check_admin():
    """Проверка прав администратора"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("VPN СЕРВЕР (Windows + wintun)")
    print("=" * 50)
    
    if not check_admin():
        print("[!] ОШИБКА: Требуются права администратора!")
        print("[!] Запустите PowerShell от имени администратора")
        sys.exit(1)
    
    if not os.path.exists("wintun.dll"):
        print("[!] ОШИБКА: wintun.dll не найден!")
        print("[!] Скачайте с: https://www.wintun.net/")
        print("[!] Скопируйте wintun.dll в текущую папку")
        sys.exit(1)
    
    generate_ssl_cert()
    
    server = VPNServer()
    
    try:
        server.run()
    except KeyboardInterrupt:
        server.stop()