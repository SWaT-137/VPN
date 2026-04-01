#!/usr/bin/env python3
"""
VPN-сервер для Windows (ИСПРАВЛЕННАЯ ВЕРСИЯ)
Правильный порядок SSL handshake: сначала accept, потом wrap_socket
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

# Криптография
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# Trojan протокол
from protocol import TrojanProtocol, FakeWebServer

# Нитры
HOST = "0.0.0.0"
PORT = 443
PASSWORD = "mysecretpassword123"
CERTFILE = "server.crt"
KEYFILE = "server.key"
TUN_NAME = "VPNServer"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"
# загрузка WINTUN.DLL
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

# крипта,деньги

class CryptoEngine:
    def __init__(self, password: str):
        # фиксированая соль, 
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

# TUN хуйня
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

# обрабатываем клиента
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
    
    def _trojan_authenticate(self) -> bool:
        """Аутентификация по Trojan протоколу"""
        try:
            auth_success, web_data = self.trojan_protocol.authenticate_client(self.ssl_sock)
            
            if auth_success:
                print(f"[+] Trojan аутентификация успешна от {self.addr}")
                return True
            else:
                print(f"[WEB] Неавторизованный запрос от {self.addr}, перенаправляем на веб-сервер")
                # Если есть данные для веб-сервера, передаем их
                if web_data:
                    # Создаем временный сокет для передачи данных
                    self.fake_web_server.serve_fake_response_with_data(self.ssl_sock, web_data)
                else:
                    self.fake_web_server.serve_fake_response(self.ssl_sock)
                return False
        except Exception as e:
            print(f"[-] Ошибка Trojan аутентификации: {e}")
            return False
    
    def run(self):
        """Основной метод обработки клиента"""
        try:
            #TROJAN аунт
            if not self._trojan_authenticate():
                return
            
            print(f"[+] Аутентификация успешна от {self.addr}")
            
            # выделка IP
            with self.vpn_server.ip_lock:
                self.client_ip = f"10.8.0.{self.vpn_server.next_ip}"
                self.vpn_server.next_ip += 1
            
            # регистрация
            with self.vpn_server.clients_lock:
                self.vpn_server.clients[self.client_ip] = {
                    'socket': self.ssl_sock,
                    'nonce': os.urandom(12),
                    'last_activity': time.time()
                }
            
            # отправляем IP клиентику
            self.ssl_sock.send(self.client_ip.encode())
            print(f"[+] Клиент {self.addr[0]}:{self.addr[1]} -> {self.client_ip}")
            
            # основной цикл приемки
            while self.running and self.vpn_server.running:
                try:
                    self.ssl_sock.settimeout(60)
                    
                    # Читаем заголовок: nonce (12) + длина (2)
                    header = self.ssl_sock.recv(14)
                    if len(header) < 14:
                        print(f"[-] Клиент {self.client_ip} отключился")
                        break
                    
                    nonce = header[:12]
                    length = struct.unpack('!H', header[12:14])[0]
                    
                    # Читаем данные
                    encrypted = self.ssl_sock.recv(length)
                    if len(encrypted) < length:
                        break
                    
                    # Расшифровываем
                    packet = self.vpn_server.crypto.decrypt(encrypted, nonce)
                    
                    # Отправляем в TUN
                    self.vpn_server.tun.write(packet)
                    
                    # Обновляем активность
                    with self.vpn_server.clients_lock:
                        if self.client_ip in self.vpn_server.clients:
                            self.vpn_server.clients[self.client_ip]['last_activity'] = time.time()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[-] Ошибка обработки клиента {self.client_ip}: {e}")
                    break
        
        except Exception as e:
            print(f"[-] Ошибка в потоке клиента {self.addr}: {e}")
        
        finally:
            # Очистка
            if self.client_ip:
                with self.vpn_server.clients_lock:
                    if self.client_ip in self.vpn_server.clients:
                        del self.vpn_server.clients[self.client_ip]
                print(f"[-] Клиент отключён: {self.client_ip}")
            
            try:
                self.ssl_sock.close()
            except:
                pass

# важная часть
class VPNServer:
    def __init__(self):
        self.running = True
        self.clients = {}
        self.clients_lock = threading.Lock()
        self.ip_lock = threading.Lock()
        self.next_ip = 2
        
        print("[1/4] Инициализация криптографии...")
        self.crypto = CryptoEngine(PASSWORD)
        
        print("[2/4] Инициализация TUN-интерфейса...")
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        
        print("[3/4] Запуск потока чтения из TUN...")
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        
        print("[4/4] Запуск SSL-сервера...")
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
        # Создаём SSL контекст
        self.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.context.load_cert_chain(CERTFILE, KEYFILE)
        
        # Создаём обычный TCP сокет
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((HOST, PORT))
        self.server_sock.listen(5)
    
    def _tun_reader(self):
        """Поток чтения из TUN"""
        print("[*] Поток чтения TUN запущен")
        
        while self.running:
            try:
                packet = self.tun.read()
                if len(packet) < 20:
                    continue
                
                dest_ip = socket.inet_ntoa(packet[16:20])
                
                with self.clients_lock:
                    if dest_ip in self.clients:
                        client = self.clients[dest_ip]
                        encrypted = self.crypto.encrypt(packet, client['nonce'])
                        message = client['nonce'] + struct.pack('!H', len(encrypted)) + encrypted
                        
                        try:
                            client['socket'].send(message)
                            self.clients[dest_ip]['nonce'] = os.urandom(12)
                        except:
                            del self.clients[dest_ip]
            except:
                if self.running:
                    time.sleep(0.01)
    
    def run(self):
        """Основной цикл приёма клиентов"""
        print(f"[*] Сервер слушает {HOST}:{PORT}")
        
        while self.running:
            try:
                # 1. Принимаем TCP соединение
                raw_sock, addr = self.server_sock.accept()
                print(f"[*] Принято соединение от {addr}, выполняем SSL handshake...")
                
                # 2. выполняем ssl рукопожатие (ВАЖНО: после accept!)
                ssl_sock = self.context.wrap_socket(raw_sock, server_side=True)
                print(f"[+] SSL handshake завершён для {addr}")
                
                # 3. Создаём обработчик клиента в отдельном потоке
                handler = ClientHandler(ssl_sock, addr, self)
                thread = threading.Thread(target=handler.run, daemon=True)
                thread.start()
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                if self.running:
                    print(f"[-] Ошибка принятия соединения: {e}")
    
    def stop(self):
        """Остановка сервера"""
        print("\n[*] Остановка сервера...")
        self.running = False
        
        # Закрываем все клиентские соединения
        with self.clients_lock:
            for ip, client in self.clients.items():
                try:
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
# ГЕНЕРАЦИЯ SSL СЕРТИФИКАТА

def generate_ssl_cert():
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


# ============================================================================
# проверка прав
# ============================================================================

def check_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


# ============================================================================
# ЗАПУСК
# ============================================================================

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