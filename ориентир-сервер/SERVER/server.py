import socket
import ssl
import struct
import threading
import os
import select
import hashlib

# Только эти внешние библиотеки
import pytun
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend


# ============================================================================
# ЧАСТЬ 1: КРИПТОГРАФИЯ
# ============================================================================

class Crypto:
    """Шифрование AES-256-GCM через cryptography"""
    
    def __init__(self, password):
        # Генерация ключа из пароля
        salt = os.urandom(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        self.key = kdf.derive(password.encode())
        self.cipher = AESGCM(self.key)
    
    def encrypt(self, data, nonce):
        """Шифрование данных"""
        return self.cipher.encrypt(nonce, data, None)
    
    def decrypt(self, data, nonce):
        """Расшифровка данных"""
        return self.cipher.decrypt(nonce, data, None)


# ============================================================================
# ЧАСТЬ 2: TUN ИНТЕРФЕЙС
# ============================================================================

class TUNInterface:
    """Управление TUN устройством через pytun"""
    
    def __init__(self, addr, netmask):
        # Создаем TUN интерфейс (L3)
        self.tun = pytun.TunTapDevice(flags=pytun.IFF_TUN)
        self.tun.addr = addr
        self.tun.netmask = netmask
        self.tun.mtu = 1500
        self.tun.up()
        
    def read(self):
        """Чтение IP-пакета из TUN"""
        return self.tun.read(2000)
    
    def write(self, packet):
        """Запись IP-пакета в TUN"""
        self.tun.write(packet)


# ============================================================================
# ЧАСТЬ 3: ОБРАБОТЧИК КЛИЕНТА (socketserver)
# ============================================================================

class VPNHandler(socketserver.StreamRequestHandler):
    """
    Обработчик подключения клиента.
    Используется socketserver для управления соединением.
    """
    
    def setup(self):
        """Вызывается при подключении клиента"""
        # Получаем доступ к серверу
        self.vpn_server = self.server.vpn_server
        
        # Аутентификация клиента
        # Читаем 32 байта хэша пароля
        password_hash = self.request.recv(32)
        expected_hash = hashlib.sha256(
            self.vpn_server.password.encode()
        ).digest()
        
        if password_hash != expected_hash:
            self.request.close()
            raise Exception("Authentication failed")
        
        # Выделяем виртуальный IP клиенту
        self.client_ip = self.vpn_server.assign_ip()
        
        # Сохраняем клиента
        self.vpn_server.clients[self.client_ip] = {
            'socket': self.request,
            'nonce': os.urandom(12)
        }
        
        # Отправляем клиенту его IP
        self.request.send(self.client_ip.encode())
        
        print(f"[+] Client connected: {self.client_address} -> {self.client_ip}")
    
    def handle(self):
        """Основной цикл обработки данных от клиента"""
        while True:
            try:
                # Читаем заголовок: [nonce 12 байт][длина 2 байта]
                header = self.request.recv(14)
                if len(header) < 14:
                    break
                
                # Разбираем заголовок
                nonce = header[:12]
                length = struct.unpack('!H', header[12:14])[0]
                
                # Читаем зашифрованные данные
                encrypted = self.request.recv(length)
                if len(encrypted) < length:
                    break
                
                # Расшифровываем
                crypto = self.vpn_server.crypto
                packet = crypto.decrypt(encrypted, nonce)
                
                # Отправляем в TUN (ядро само отмаршрутизирует)
                self.vpn_server.tun.write(packet)
                
            except Exception as e:
                print(f"[-] Error handling client: {e}")
                break
    
    def finish(self):
        """Вызывается при отключении клиента"""
        if self.client_ip in self.vpn_server.clients:
            del self.vpn_server.clients[self.client_ip]
        print(f"[-] Client disconnected: {self.client_address}")


# ============================================================================
# ЧАСТЬ 4: ОСНОВНОЙ КЛАСС СЕРВЕРА
# ============================================================================

class VPNServer:
    """Главный класс VPN-сервера"""
    
    def __init__(self, host, port, password, certfile, keyfile):
        """
        Инициализация VPN-сервера
        
        Args:
            host: Адрес для прослушивания
            port: Порт для прослушивания
            password: Пароль для аутентификации
            certfile: Путь к SSL сертификату
            keyfile: Путь к SSL ключу
        """
        self.host = host
        self.port = port
        self.password = password
        self.certfile = certfile
        self.keyfile = keyfile
        
        # Криптография
        self.crypto = Crypto(password)
        
        # TUN интерфейс
        self.tun = TUNInterface("10.8.0.1", "255.255.255.0")
        
        # Клиенты: IP -> {socket, nonce}
        self.clients = {}
        self.next_ip = 2  # 10.8.0.1 занят сервером
        self.ip_lock = threading.Lock()
        
        # Создаем SSL контекст
        self.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.context.load_cert_chain(certfile, keyfile)
        
        # Создаем TCP сервер
        self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_server.bind((host, port))
        self.tcp_server.listen(5)
        
        # Оборачиваем в SSL
        self.ssl_server = self.context.wrap_socket(
            self.tcp_server,
            server_side=True
        )
        
        # Запускаем поток для чтения из TUN
        self.running = True
        self.tun_thread = threading.Thread(target=self.read_tun)
        self.tun_thread.daemon = True
        self.tun_thread.start()
    
    def assign_ip(self):
        """Выделение IP из пула 10.8.0.0/24"""
        with self.ip_lock:
            ip = f"10.8.0.{self.next_ip}"
            self.next_ip += 1
            return ip
    
    def read_tun(self):
        """
        Поток чтения из TUN.
        Пересылает IP-пакеты от интернета к клиентам.
        """
        while self.running:
            try:
                # Читаем пакет из TUN
                packet = self.tun.read()
                if len(packet) < 20:
                    continue
                
                # Парсим IP-заголовок (RFC 791)
                # Байты 16-19: IP назначения
                dest_ip = socket.inet_ntoa(packet[16:20])
                
                # Ищем клиента с таким IP
                if dest_ip in self.clients:
                    client = self.clients[dest_ip]
                    sock = client['socket']
                    nonce = client['nonce']
                    
                    # Шифруем пакет
                    encrypted = self.crypto.encrypt(packet, nonce)
                    
                    # Формируем сообщение: [nonce][длина][данные]
                    msg = nonce + struct.pack('!H', len(encrypted)) + encrypted
                    
                    # Отправляем клиенту
                    sock.send(msg)
                    
                    # Обновляем nonce
                    self.clients[dest_ip]['nonce'] = os.urandom(12)
                    
            except Exception as e:
                print(f"[-] TUN read error: {e}")
    
    def serve_forever(self):
        """
        Основной цикл сервера.
        Принимает клиентов и запускает их обработку через socketserver.
        """
        print(f"[+] VPN Server listening on {self.host}:{self.port}")
        
        while self.running:
            try:
                # Принимаем SSL соединение
                client_sock, client_addr = self.ssl_server.accept()
                
                # Создаем поток для обработки клиента
                # Используем socketserver для управления
                # Для простоты создаем отдельный поток вручную
                thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_sock, client_addr)
                )
                thread.daemon = True
                thread.start()
                
            except Exception as e:
                print(f"[-] Accept error: {e}")
    
    def handle_client(self, client_sock, client_addr):
        """
        Обработка отдельного клиента
        """
        try:
            # Аутентификация
            password_hash = client_sock.recv(32)
            expected = hashlib.sha256(self.password.encode()).digest()
            
            if password_hash != expected:
                client_sock.close()
                return
            
            # Выделяем IP
            client_ip = self.assign_ip()
            
            # Сохраняем клиента
            self.clients[client_ip] = {
                'socket': client_sock,
                'nonce': os.urandom(12)
            }
            
            # Отправляем IP клиенту
            client_sock.send(client_ip.encode())
            
            print(f"[+] Client {client_addr} -> {client_ip}")
            
            # Основной цикл обработки данных
            while True:
                # Читаем заголовок
                header = client_sock.recv(14)
                if len(header) < 14:
                    break
                
                nonce = header[:12]
                length = struct.unpack('!H', header[12:14])[0]
                
                # Читаем данные
                encrypted = client_sock.recv(length)
                if len(encrypted) < length:
                    break
                
                # Расшифровываем
                packet = self.crypto.decrypt(encrypted, nonce)
                
                # Отправляем в TUN
                self.tun.write(packet)
                
        except Exception as e:
            print(f"[-] Client error: {e}")
        finally:
            # Удаляем клиента
            for ip, info in list(self.clients.items()):
                if info['socket'] == client_sock:
                    del self.clients[ip]
                    print(f"[-] Client disconnected: {client_addr} -> {ip}")
                    break
            client_sock.close()
    
    def stop(self):
        """Остановка сервера"""
        self.running = False
        self.tcp_server.close()


# ============================================================================
# ЧАСТЬ 5: ЗАПУСК
# ============================================================================

if __name__ == "__main__":
    # Конфигурация
    HOST = "0.0.0.0"
    PORT = 443
    PASSWORD = "mysecretpassword"
    CERTFILE = "server.crt"  # SSL сертификат
    KEYFILE = "server.key"   # SSL ключ
    
    # Создание SSL сертификата (если нет):
    # openssl req -x509 -newkey rsa:4096 -keyout server.key -out server.crt \
    #   -days 365 -nodes -subj "/CN=localhost"
    
    # Запуск сервера
    server = VPNServer(HOST, PORT, PASSWORD, CERTFILE, KEYFILE)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Shutting down...")
        server.stop()