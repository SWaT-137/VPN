#!/usr/bin/env python3
"""
ПРОСТОЙ ТЕСТОВЫЙ КЛИЕНТ
"""

import socket
import ssl
import hashlib

HOST = "127.0.0.1"
PORT = 443
PASSWORD = "mysecretpassword123"

def main():
    print("=" * 50)
    print("ПРОСТОЙ ТЕСТОВЫЙ КЛИЕНТ")
    print("=" * 50)
    
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    
    print("[1] Подключение...")
    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_sock.connect((HOST, PORT))
    
    print("[2] SSL handshake...")
    ssl_sock = context.wrap_socket(raw_sock, server_hostname=HOST)
    print("[+] SSL соединение установлено")
    
    print("[3] Отправка хэша пароля...")
    password_hash = hashlib.sha256(PASSWORD.encode()).digest()
    ssl_sock.send(password_hash)
    print(f"    Отправлено {len(password_hash)} байт")
    
    print("[4] Ожидание ответа...")
    response = ssl_sock.recv(64)
    print(f"[+] Получен IP: {response.decode()}")
    
    print("[5] Отправка тестового сообщения...")
    ssl_sock.send(b"Hello VPN Server!")
    
    print("[6] Ожидание эхо-ответа...")
    echo = ssl_sock.recv(1024)
    print(f"[+] Получен ответ: {echo.decode()}")
    
    ssl_sock.close()
    print("\n✅ Тест пройден!")

if __name__ == "__main__":
    main()