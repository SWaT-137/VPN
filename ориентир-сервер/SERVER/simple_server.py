#!/usr/bin/env python3
"""
ПРОСТОЙ ТЕСТОВЫЙ СЕРВЕР — правильный порядок SSL handshake
"""

import socket
import ssl
import hashlib
import threading

HOST = "0.0.0.0"
PORT = 443
PASSWORD = "mysecretpassword123"
CERTFILE = "server.crt"
KEYFILE = "server.key"

def handle_client(client_sock, addr):
    """Обрабатывает клиента после завершения SSL handshake"""
    print(f"[+] Подключение от {addr}")
    
    try:
        # Теперь SSL handshake уже завершён, можно читать данные
        password_hash = client_sock.recv(32)
        expected_hash = hashlib.sha256(PASSWORD.encode()).digest()
        
        print(f"[DEBUG] Получено {len(password_hash)} байт")
        print(f"[DEBUG] Полученный хэш: {password_hash.hex()[:32]}...")
        print(f"[DEBUG] Ожидаемый хэш: {expected_hash.hex()[:32]}...")
        
        if password_hash == expected_hash:
            print(f"[+] Аутентификация УСПЕШНА!")
            client_sock.send(b"10.8.0.2")
        else:
            print(f"[-] Аутентификация НЕ УСПЕШНА!")
            client_sock.close()
            return
        
        # Эхо-режим
        while True:
            data = client_sock.recv(4096)
            if not data:
                break
            print(f"[*] Получено {len(data)} байт")
            client_sock.send(data)
            
    except Exception as e:
        print(f"[-] Ошибка: {e}")
    finally:
        client_sock.close()
        print(f"[-] Отключение {addr}")

def main():
    print("=" * 50)
    print("ПРОСТОЙ ТЕСТОВЫЙ СЕРВЕР")
    print("=" * 50)
    print(f"Адрес: {HOST}:{PORT}")
    print(f"Пароль: {PASSWORD}")
    print("=" * 50)
    
    # Создаём SSL контекст
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(CERTFILE, KEYFILE)
    
    # Обычный TCP сокет
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    
    print(f"[+] Сервер запущен")
    
    while True:
        try:
            raw_sock, addr = server_sock.accept()
            print(f"[*] Принято соединение от {addr}, выполняем SSL handshake...")
            
            # ВАЖНО: wrap_socket вызываем ПОСЛЕ accept
            ssl_sock = context.wrap_socket(raw_sock, server_side=True)
            print(f"[+] SSL handshake завершён")
            
            thread = threading.Thread(target=handle_client, args=(ssl_sock, addr))
            thread.daemon = True
            thread.start()
            
        except KeyboardInterrupt:
            print("\n[+] Остановка...")
            break
        except Exception as e:
            print(f"[-] Ошибка: {e}")

if __name__ == "__main__":
    main()