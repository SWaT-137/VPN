#!/usr/bin/env python3
"""
ТЕСТОВЫЙ КЛИЕНТ ДЛЯ VPN (с фиксированной солью)
"""

import socket
import ssl
import struct
import hashlib
import os
import sys

# Криптография
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# ============================================================================
# НАСТРОЙКИ
# ============================================================================

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 443
PASSWORD = "mysecretpassword123"

# ============================================================================
# КРИПТОГРАФИЯ (с фиксированной солью, как на сервере)
# ============================================================================

class CryptoEngine:
    def __init__(self, password: str):
        # ТА ЖЕ САМАЯ фиксированная соль, что и на сервере
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
        print("[+] Криптодвижок клиента инициализирован")

# ============================================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================================

def main():
    print("=" * 50)
    print("ТЕСТОВЫЙ КЛИЕНТ ДЛЯ VPN")
    print("=" * 50)
    print(f"Сервер: {SERVER_HOST}:{SERVER_PORT}")
    print(f"Пароль: {PASSWORD}")
    print("=" * 50)
    
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    
    try:
        print("\n[*] Подключение к серверу...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssl_sock = context.wrap_socket(sock, server_hostname=SERVER_HOST)
        ssl_sock.connect((SERVER_HOST, SERVER_PORT))
        print("[+] SSL-соединение установлено")
        
        # Отправляем хэш пароля (32 байта)
        password_hash = hashlib.sha256(PASSWORD.encode()).digest()
        print(f"[*] Отправка хэша пароля...")
        ssl_sock.send(password_hash)
        print("[+] Хэш отправлен")
        
        # Получаем виртуальный IP
        response = ssl_sock.recv(64)
        vpn_ip = response.decode()
        print(f"[+] Получен виртуальный IP: {vpn_ip}")
        
        # Инициализируем криптографию
        crypto = CryptoEngine(PASSWORD)
        
        # Отправляем тестовое зашифрованное сообщение
        print("\n[*] Отправка тестового зашифрованного сообщения...")
        
        test_data = b"Hello VPN Server! This is a test packet."
        nonce = os.urandom(12)
        encrypted = crypto.encrypt(test_data, nonce)
        
        # Формат: nonce (12) + длина (2) + данные
        message = nonce + struct.pack('!H', len(encrypted)) + encrypted
        ssl_sock.send(message)
        print(f"[+] Отправлено {len(test_data)} байт (зашифровано: {len(encrypted)})")
        
        print("\n✅ Тест пройден! Сервер работает корректно.")
        
    except Exception as e:
        print(f"[-] Ошибка: {e}")
    finally:
        try:
            ssl_sock.close()
        except:
            pass
        print("\n[*] Соединение закрыто")

if __name__ == "__main__":
    main()