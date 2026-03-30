import socket
import ssl
import socketserver
import struct
import threading
import hashlib
import os
import time
import sys
import ctypes
from ctypes import wintypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# НАСТРОЙКИ
HOST = "0.0.0.0"                    # Слушаем все сетевые интерфейсы
PORT = 443                           # Порт HTTPS (для маскировки)
PASSWORD = "mysecretpassword123"     # Пароль для подключения клиентов
CERTFILE = "server.crt"              # Файл SSL-сертификата
KEYFILE = "server.key"               # Файл приватного ключа SSL
TUN_NAME = "VPNServer"               # Имя виртуального сетевого адаптера
VPN_SERVER_IP = "10.8.0.1"           # IP-адрес сервера в VPN-сети
VPN_NETMASK = "255.255.255.0"        # Маска подсети (24 бита)

# загрузка wintun.dll 
class Wintun:
    """Обёртка для работы с wintun.dll через ctypes"""
    
    def __init__(self, dll_path="wintun.dll"):
        # Если указан относительный путь, ищем файл в разных местах
        if not os.path.isabs(dll_path):
            # Сначала ищем в папке со скриптом
            script_dir = os.path.dirname(os.path.abspath(__file__))
            full_path = os.path.join(script_dir, dll_path)
            
            # Если не нашли, ищем в текущей рабочей папке
            if not os.path.exists(full_path):
                full_path = os.path.join(os.getcwd(), dll_path)
            
            # Если всё ещё не нашли, пробуем в System32
            if not os.path.exists(full_path):
                sys32_path = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', dll_path)
                if os.path.exists(sys32_path):
                    full_path = sys32_path
                else:
                    raise FileNotFoundError(f"Не удалось найти {dll_path} в папке со скриптом или System32")
            
            dll_path = full_path
        
        print(f"Загрузка wintun.dll из: {dll_path}")
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
        """Создаёт виртуальный сетевой адаптер"""
        return self.WintunCreateAdapter(name, tunnel_type, requested_guid)
    
    def close_adapter(self, handle):
        """Закрывает адаптер"""
        self.WintunCloseAdapter(handle)
# КРИПТОГРАФИЯ 
class CryptoEngine:
    """Движок шифрования AES-256-GCM"""
    
    def __init__(self, password: str):
        # Генерируем случайную соль (уникальную для этого экземпляра)
        self.salt = os.urandom(16)
        
        # PBKDF2 — превращает пароль в криптостойкий ключ
        # 100 000 итераций защищают от перебора паролей
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,  # 32 байта = 256 бит для AES-256
            salt=self.salt,
            iterations=100000,
            backend=default_backend()
        )
        self.key = kdf.derive(password.encode())  # Получаем ключ из пароля
        self.cipher = AESGCM(self.key)            # Создаём шифр
        print("Криптодвижок инициализирован")
    def encrypt(self, data: bytes, nonce: bytes) -> bytes:
        """Шифрует данные с указанным nonce"""
        return self.cipher.encrypt(nonce, data, None)
    def decrypt(self, data: bytes, nonce: bytes) -> bytes:
        """Расшифровывает данные с указанным nonce"""
        return self.cipher.decrypt(nonce, data, None)
# tun интерфейс
class TUNInterface:
    """Управление TUN-интерфейсом через wintun"""
    def __init__(self, name: str, ip: str, netmask: str):
        self.name = name
        self.handle = None      
        # Проверяем наличие wintun.dll
        if not os.path.exists("wintun.dll"):
            print("wintun.dll не найден!")
            sys.exit(1)
        # Загружаем wintun
        self.wintun = Wintun("wintun.dll")
        # Создание виртуального адаптера
        self.handle = self.wintun.create_adapter(name)
        if not self.handle:
            raise Exception(f"Не удалось создать адаптер: {name}")
        print(f"Виртуальный адаптер создан: {name}")
        # Назначаем IP-адрес через netsh (команда Windows)
        import subprocess
        subprocess.run(
            f'netsh interface ip set address "{name}" static {ip} {netmask}',
            capture_output=True,
            shell=True
        )
        print(f"IP-адрес назначен: {ip}/{netmask}")
    def read(self, size: int = 2000) -> bytes:
        """Читает IP-пакет из виртуального интерфейса"""
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
        """Записывает IP-пакет в виртуальный интерфейс"""
        if not self.handle:
            return
        packet_ptr = self.wintun.WintunAllocateSendPacket(self.handle, len(packet))
        if packet_ptr:
            ctypes.memmove(packet_ptr, packet, len(packet))
            self.wintun.WintunSendPacket(self.handle, packet_ptr)
    def close(self):
        """Закрывает виртуальный интерфейс"""
        if self.handle:
            self.wintun.WintunCloseAdapter(self.handle)
            self.handle = None
# ОБРАБОТЧИК КЛИЕНТА 
class VPNHandler(socketserver.StreamRequestHandler):
    """Обрабатывает подключение одного VPN-клиента"""
    
    def setup(self):
        """Вызывается при подключении нового клиента"""
        self.vpn_server = self.server.vpn_server
        
        #Аутентификация — клиент должен прислать SHA256(пароль)
        password_hash = self.request.recv(32)
        expected_hash = hashlib.sha256(PASSWORD.encode()).digest()
        if password_hash != expected_hash:
            print(f"[-] Ошибка аутентификации: {self.client_address}")
            self.request.close()
            raise Exception("Ошибка аутентификации")
        # Выделяем уникальный IP-адрес клиенту
        with self.vpn_server.ip_lock:
            client_ip = f"10.8.0.{self.vpn_server.next_ip}"
            self.vpn_server.next_ip += 1
            self.client_ip = client_ip
        #Регистрируем клиента в словаре активных соединений
        with self.vpn_server.clients_lock:
            self.vpn_server.clients[client_ip] = {
                'socket': self.request,
                'nonce': os.urandom(12),           # Уникальное число для шифрования
                'last_activity': time.time()       # Время последней активности
            }
        # Отправляем клиенту его виртуальный IP
        self.request.send(client_ip.encode())
        
        print(f"Клиент подключён: {self.client_address[0]}:{self.client_address[1]} -> {client_ip}")
    
    def handle(self):
        """Основной цикл приёма данных от клиента"""
        while True:
            try:
                # Читаем заголовок: 12 байт nonce + 2 байта длины
                header = self.request.recv(14)
                if len(header) < 14:
                    break
                
                nonce = header[:12]                     # Уникальное число для расшифровки
                length = struct.unpack('!H', header[12:14])[0]  # Длина зашифрованных данных
                
                # Читаем зашифрованные данные
                encrypted = self.request.recv(length)
                if len(encrypted) < length:
                    break
                
                # Расшифровываем IP-пакет
                packet = self.vpn_server.crypto.decrypt(encrypted, nonce)
                # Отправляем расшифрованный пакет в виртуальный интерфейс
                # Ядро Windows само отправит его в интернет
                self.vpn_server.tun.write(packet)
                # Обновляем время последней активности
                with self.vpn_server.clients_lock:
                    if self.client_ip in self.vpn_server.clients:
                        self.vpn_server.clients[self.client_ip]['last_activity'] = time.time()
                
            except Exception as e:
                print(f"Ошибка клиента: {e}")
                break
    
    def finish(self):
        """Вызывается при отключении клиента"""
        with self.vpn_server.clients_lock:
            if self.client_ip in self.vpn_server.clients:
                del self.vpn_server.clients[self.client_ip]
        print(f"Клиент отключён: {self.client_ip}")

# SSL-СЕРВЕР 
class SSLVPNServer(socketserver.ThreadingMixIn, socketserver.TCPServer): #ThreadingMixIn создаёт отдельный поток для каждого клиента.
    def __init__(self, server_address, handler_class, vpn_server):
        # Создаём обычный TCP-сокет
        self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_socket.bind(server_address)
        self.tcp_socket.listen(5)
        # Настраиваем SSL/TLS
        self.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.context.load_cert_chain(CERTFILE, KEYFILE)
        # Оборачиваем TCP-сокет в SSL
        self.socket = self.context.wrap_socket(
            self.tcp_socket,
            server_side=True
        )
        self.vpn_server = vpn_server
        self.allow_reuse_address = True
        # Вызываем конструктор родительского класса
        super().__init__(server_address, handler_class)
# главный класс сервера
class VPNServer:
    def __init__(self):
        self.running = True  # Флаг работы сервера
        print("[1/4] Инициализация криптографии...")
        self.crypto = CryptoEngine(PASSWORD)
        print("[2/4] Инициализация TUN-интерфейса...")
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        # Хранилище активных клиентов
        self.clients = {}
        self.clients_lock = threading.Lock()  # Защита от одновременного доступа
        self.ip_lock = threading.Lock()       # Защита при выдаче IP
        self.next_ip = 2  # 10.8.0.1 занят сервером, начинаем с 2
        print("[3/4] Запуск потока чтения из TUN...")
        self.tun_thread = threading.Thread(target=self._tun_reader, daemon=True)
        self.tun_thread.start()
        print("[4/4] Запуск SSL-сервера...")
        self.server = SSLVPNServer((HOST, PORT), VPNHandler, self)
        print("\n" + "" * 50)
        print("VPN СЕРВЕР ГОТОВ К РАБОТЕ!")
        print("" * 50)
        print(f"Адрес:     {HOST}:{PORT}")
        print(f"VPN-сеть:  10.8.0.0/24")
        print(f"IP сервера: {VPN_SERVER_IP}")
        print("" * 50)
    
    def _tun_reader(self): #Фоновый поток, читающий пакеты из TUN.
        print("Поток чтения TUN запущен")
        while self.running:
            try:
                # Читаем IP-пакет из виртуального интерфейса
                packet = self.tun.read()
                if len(packet) < 20:
                    continue
                # Извлекаем IP-адрес назначения из заголовка (байты 16-19)
                dest_ip = socket.inet_ntoa(packet[16:20])
                # Ищем клиента с таким IP
                with self.clients_lock:
                    if dest_ip in self.clients:
                        client = self.clients[dest_ip]
                        encrypted = self.crypto.encrypt(packet, client['nonce'])
                        # Формат: nonce (12) + длина (2) + зашифрованные данные
                        message = client['nonce'] + struct.pack('!H', len(encrypted)) + encrypted
                        try:
                            client['socket'].send(message)
                            # Генерируем новый nonce для следующего пакета
                            self.clients[dest_ip]['nonce'] = os.urandom(12)
                        except:
                            # Клиент отключился, удаляем его
                            del self.clients[dest_ip]
            except:
                pass  # Игнорируем временные ошибки
    def run(self):
        
        print("\nVPN-сервер работает. Нажмите Ctrl+C для остановки.\n")
        try:
            self.server.serve_forever()  # Бесконечный цикл приёма клиентов
        except KeyboardInterrupt:
            self.stop()
    def stop(self):
        print("\nОстановка VPN-сервера...")
        self.running = False
        # Закрываем сервер
        self.server.shutdown()
        self.server.server_close()
        # Закрываем виртуальный интерфейс
        self.tun.close()
        print("VPN-сервер остановлен")
# ГЕНЕРАЦИЯ SSL-СЕРТИФИКАТА 
def generate_ssl_cert():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    
    # Если сертификат уже существует, ничего не делаем
    if os.path.exists(CERTFILE) and os.path.exists(KEYFILE):
        print("сертификат уже существует")
        return
    
    print("создание сертификата")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # информация о владельце сертификата
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
    ])
    # Создаём сертификат
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
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(u"localhost")]),
        critical=False,
    ).sign(private_key, hashes.SHA256(), default_backend())
    
    # Сохраняем приватный ключ
    with open(KEYFILE, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    # Сохраняем сертификат
    with open(CERTFILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    print("сертификат создан")
# проверка прав
def check_admin():
    # проверяет запущен ли файл от имени администратора
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False
    
if __name__ == "__main__":
    print("=" * 50)
    print("VPN СЕРВЕР (Windows + wintun)")
    print("=" * 50)
    
    # Проверка прав администратора 
    if not check_admin():
        print("требуются права админа")
        sys.exit(1)
    # Проверка наличия wintun.dll
    if not os.path.exists("wintun.dll"):
        print("wintun.dll не найден!") 
        sys.exit(1)
    # Генерация SSL-сертификата
    generate_ssl_cert()
    # Создание и запуск сервера
    server = VPNServer()
    try:
        server.run()
    except KeyboardInterrupt:
        server.stop()