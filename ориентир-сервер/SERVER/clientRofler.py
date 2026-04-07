#!/usr/bin/env python3

import socket
import ssl
import struct
import threading
import time
import os
import sys
import ctypes
import subprocess
import hashlib
from ctypes import wintypes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 443
PASSWORD = "mysecretpassword123"
TUN_NAME = "VPNClient"


class Wintun:
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
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                             ctypes.POINTER(wintypes.DWORD)]
        self.WintunReceivePacket.restype = wintypes.DWORD

        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunReleaseReceivePacket.restype = None

    def create_adapter(self, name, tunnel_type="Wintun", requested_guid=None):
        return self.WintunCreateAdapter(name, tunnel_type, requested_guid)

    def close_adapter(self, handle):
        self.WintunCloseAdapter(handle)


class TUNInterface:
    def __init__(self, name: str, ip: str):
        self.name = name
        self.handle = None
        self.original_gateway = None

        if not os.path.exists("wintun.dll"):
            print("[!] ОШИБКА: wintun.dll не найден!")
            sys.exit(1)

        self.wintun = Wintun("wintun.dll")
        self.handle = self.wintun.create_adapter(name)

        if not self.handle:
            raise Exception(f"Не удалось создать адаптер: {name}")

        print(f"[+] Виртуальный адаптер создан: {name}")

        subprocess.run(
            f'netsh interface ip set address "{name}" static {ip} 255.255.255.0',
            capture_output=True,
            shell=True
        )
        print(f"[+] IP-адрес назначен: {ip}")

        subprocess.run(
            f'netsh interface set interface "{name}" admin=enable',
            capture_output=True,
            shell=True
        )

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


class TrojanProtocol:
    def __init__(self, password: str):
        self.password = password
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def authenticate(self, sock) -> bool:
        try:
            auth_line = f"{self.password_hash}\r\n".encode()
            sock.send(auth_line)
            return True
        except Exception as e:
            print(f"[-] Ошибка аутентификации: {e}")
            return False


class VPNClient:
    def __init__(self):
        self.running = True
        self.server_sock = None
        self.tun = None
        self.crypto = None
        self.client_ip = None
        self.nonce = os.urandom(12)
        self.original_gateway = None

    def get_default_gateway(self):
        result = subprocess.run('route print -4 0.0.0.0', capture_output=True, text=True, shell=True)
        lines = result.stdout.split('\n')

        for line in lines:
            if '0.0.0.0' in line and '255.255.255.255' not in line:
                parts = line.split()
                if len(parts) >= 3:
                    gateway = parts[2]
                    if gateway != '0.0.0.0' and gateway != 'On-link':
                        return gateway
        return None

    def connect_to_server(self):
        print(f"[*] Подключение к серверу {SERVER_HOST}:{SERVER_PORT}...")

        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.connect((SERVER_HOST, SERVER_PORT))

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        ssl_sock = context.wrap_socket(raw_sock, server_hostname=SERVER_HOST)
        print(f"[+] TLS соединение установлено")

        trojan = TrojanProtocol(PASSWORD)
        if not trojan.authenticate(ssl_sock):
            print("[-] Аутентификация не удалась")
            return False

        print(f"[+] Аутентификация успешна")

        self.client_ip = ssl_sock.recv(1024).decode().strip()
        print(f"[+] Сервер назначил IP: {self.client_ip}")

        self.server_sock = ssl_sock
        return True

    def setup_routing(self):
        print("[*] Настройка маршрутизации...")

        self.original_gateway = self.get_default_gateway()
        if not self.original_gateway:
            print("[-] Не удалось определить шлюз по умолчанию!")
            return False

        print(f"[*] Оригинальный шлюз: {self.original_gateway}")

        subprocess.run(
            f'route add {SERVER_HOST} mask 255.255.255.255 {self.original_gateway} metric 1',
            capture_output=True,
            shell=True
        )
        print(f"[+] Маршрут до сервера добавлен")

        subprocess.run(
            f'route add 0.0.0.0 mask 0.0.0.0 {self.client_ip} metric 5',
            capture_output=True,
            shell=True
        )
        print(f"[+] Маршрут через VPN добавлен (метрик 5)")

        subprocess.run(
            f'route add 0.0.0.0 mask 0.0.0.0 {self.original_gateway} metric 100',
            capture_output=True,
            shell=True
        )

        print("[+] Маршрутизация настроена! Весь трафик идёт через VPN")

    def restore_routing(self):
        print("[*] Восстановление маршрутов...")

        subprocess.run(f'route delete {self.client_ip}', capture_output=True, shell=True)
        subprocess.run(f'route delete {SERVER_HOST}', capture_output=True, shell=True)

        if self.original_gateway:
            subprocess.run(f'route delete 0.0.0.0 mask 0.0.0.0 {self.original_gateway}', capture_output=True,
                           shell=True)

        print("[+] Маршруты восстановлены")

    def tun_to_server(self):
        print("[*] Поток TUN → Сервер запущен")
        while self.running:
            try:
                packet = self.tun.read()
                if len(packet) < 20:
                    continue

                self.nonce = os.urandom(12)
                encrypted = self.crypto.encrypt(packet, self.nonce)
                message = self.nonce + struct.pack('!H', len(encrypted)) + encrypted
                self.server_sock.send(message)
            except Exception as e:
                if self.running:
                    print(f"[-] Ошибка в TUN→Сервер: {e}")
                break

    def server_to_tun(self):
        print("[*] Поток Сервер → TUN запущен")
        while self.running:
            try:
                self.server_sock.settimeout(1.0)
                header = self.server_sock.recv(14)
                if len(header) < 14:
                    continue
                nonce = header[:12]
                length = struct.unpack('!H', header[12:14])[0]
                encrypted = self.server_sock.recv(length)
                if len(encrypted) < length:
                    continue
                packet = self.crypto.decrypt(encrypted, nonce)
                self.tun.write(packet)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[-] Ошибка в Сервер→TUN: {e}")
                break

    def run(self):
        print("=" * 50)
        print("VPN КЛИЕНТ ЗАПУЩЕН")
        print("=" * 50)

        print("[*] Проверка связи с сервером...")
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(5)
            test_sock.connect((SERVER_HOST, SERVER_PORT))
            test_sock.close()
            print("[+] Сервер доступен")
        except:
            print("[-] Сервер НЕ ДОСТУПЕН! Проверь IP и что сервер запущен")
            input("\nНажми Enter для выхода...")
            return

        if not self.connect_to_server():
            print("[-] Не удалось подключиться к серверу")
            input("\nНажми Enter для выхода...")
            return

        self.crypto = CryptoEngine(PASSWORD)

        print("[*] Создание виртуального адаптера...")
        self.tun = TUNInterface(TUN_NAME, self.client_ip)

        time.sleep(2)

        self.setup_routing()

        t2s_thread = threading.Thread(target=self.tun_to_server, daemon=True)
        s2t_thread = threading.Thread(target=self.server_to_tun, daemon=True)
        t2s_thread.start()
        s2t_thread.start()

        print("\n" + "=" * 50)
        print("VPN РАБОТАЕТ!")
        print(f"Ваш IP в VPN: {self.client_ip}")
        print("Для остановки нажмите Ctrl+C")
        print("=" * 50 + "\n")

        print("[*] Тестирование VPN соединения...")
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(5)
            test_sock.connect(("8.8.8.8", 53))
            test_sock.close()
            print("[+] VPN работает! Интернет доступен через туннель")
        except:
            print("[!] Предупреждение: Проверьте что сервер настроен правильно")

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        print("\n[*] Остановка VPN-клиента...")
        self.running = False
        self.restore_routing()
        if self.tun:
            self.tun.close()
        if self.server_sock:
            try:
                self.server_sock.close()
            except:
                pass
        print("[+] VPN-клиент остановлен")


def check_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def elevate_privileges():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()


if __name__ == "__main__":
    print("=" * 50)
    print("VPN КЛИЕНТ ДЛЯ WINDOWS")
    print("=" * 50)

    if not check_admin():
        print("[!] Требуются права администратора!")
        elevate_privileges()

    if not os.path.exists("wintun.dll"):
        print("\n[!] ОШИБКА: wintun.dll не найден!")
        print("Скачайте wintun.dll с https://www.wintun.net/")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

    client = VPNClient()
    try:
        client.run()
    except KeyboardInterrupt:
        pass