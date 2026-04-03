#!/usr/bin/env python3
"""
TROJAN VPN КЛИЕНТ
Клиент для тестирования VPN сервера с протоколом Trojan
"""

import socket
import ssl
import hashlib
import time
from protocol import generate_password_hash

HOST = "127.0.0.1"
PORT = 443
PASSWORD = "mysecretpassword123"

class TrojanClient:
    """Клиент для работы с Trojan протоколом"""
    
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.password_hash = generate_password_hash(password)
        self.ssl_sock = None
    
    def connect(self):
        """Установка соединения с сервером"""
        print("[1] Подключение...")
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.connect((self.host, self.port))
        
        print("[2] SSL handshake...")
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        self.ssl_sock = context.wrap_socket(raw_sock, server_hostname=self.host)
        print("[+] SSL соединение установлено")
        
        return True
    
    def authenticate(self):
        """Аутентификация по Trojan протоколу"""
        if not self.ssl_sock:
            return False
        
        print("[3] Отправка Trojan аутентификации...")
        # Формат: [ХЕШ ПАРОЛЯ (56 байт)] + [\r\n] + [ЗАПРОС] + [\r\n\r\n]
        auth_data = self.password_hash.encode() + b'\r\n'
        self.ssl_sock.send(auth_data)
        print(f"    Отправлен хеш пароля: {self.password_hash}")
        
        return True
    
    def send_test_data(self, data=b"Hello Trojan VPN Server!"):
        """Отправка тестовых данных"""
        if not self.ssl_sock:
            return False
        
        print("[4] Отправка тестовых данных...")
        # Формат запроса: данные + \r\n\r\n
        request_data = data + b'\r\n\r\n'
        self.ssl_sock.send(request_data)
        print(f"    Отправлено: {data.decode()}")
        
        return True
    
    def receive_response(self, timeout=5):
        """Получение ответа от сервера"""
        if not self.ssl_sock:
            return None
        
        print("[5] Ожидание ответа...")
        self.ssl_sock.settimeout(timeout)
        
        try:
            response = self.ssl_sock.recv(1024)
            if response:
                print(f"[+] Получен ответ: {response.decode()}")
                return response
            else:
                print("[-] Пустой ответ от сервера")
                return None
        except socket.timeout:
            print("[-] Таймаут ожидания ответа")
            return None
    
    


def test_trojan_connection():
    """Тестирование Trojan соединения"""
    print("=" * 50)
    print("TROJAN VPN КЛИЕНТ - ТЕСТ")
    print("=" * 50)
    
    client = TrojanClient(HOST, PORT, PASSWORD)
    
    try:
        # Подключение
        if not client.connect():
            print("[-] Ошибка подключения")
            return False
        
        # Аутентификация
        if not client.authenticate():
            print("[-] Ошибка аутентификации")
            return False
        
        # Получение IP от сервера
        ip_response = client.receive_response()
        if ip_response:
            print(f"[+] Получен IP: {ip_response.decode()}")
        
        # Отправка тестовых данных
        client.send_test_data()
        
        # Получение подтверждения
        time.sleep(1)  # Даем время серверу обработать
        
        print("\n✅ Тест пройден успешно!")
        return True
        
    except Exception as e:
        print(f"[-] Ошибка тестирования: {e}")
        return False
    finally:
        client.close()


def test_incorrect_password():
    """Тестирование с неверным паролем (должен получить веб-страницу)"""
    print("\n" + "=" * 50)
    print("ТЕСТ С НЕВЕРНЫМ ПАРОЛЕМ")
    print("=" * 50)
    
    client = TrojanClient(HOST, PORT, "wrongpassword")
    
    try:
        if client.connect():
            # Отправляем неверный хеш
            wrong_hash = generate_password_hash("wrongpassword")
            auth_data = wrong_hash.encode() + b'\r\n'
            client.ssl_sock.send(auth_data)
            
            # Пытаемся получить веб-ответ
            web_response = client.receive_response()
            if web_response and b"HTTP" in web_response:
                print("[WEB] Получен веб-ответ (как и ожидалось)")
                return True
            else:
                print("[-] Не получен веб-ответ")
                return False
    except Exception as e:
        print(f"[-] Ошибка: {e}")
        return False
    finally:
        client.close()


if __name__ == "__main__":
    # Тест с правильным паролем
    test_trojan_connection()
    
    # Тест с неправильным паролем
    test_incorrect_password()