#!/usr/bin/env python3
"""
МОДУЛЬ ПРОТОКОЛА TROJAN
Реализация протокола Trojan для VPN сервера
"""

import hashlib
import socket
import random

class TrojanProtocol:
    """Класс для обработки Trojan протокола"""
    
    def __init__(self, password: str):
        self.password = password
        self.expected_hash = hashlib.sha224(password.encode()).hexdigest()
    
    def authenticate_client(self, sock) -> bool:
        """
        Аутентификация клиента по Trojan протоколу
        Формат: [ХЕШ ПАРОЛЯ (56 байт)] + [\r\n] + [ЗАПРОС] + [\r\n\r\n]
        """
        try:
            # Читаем первые 58 байт (56 байт хеш + \r\n)
            auth_data = sock.recv(58, socket.MSG_PEEK)
            
            if len(auth_data) < 58:
                return False
            
            # Проверяем, заканчивается ли на \r\n
            if auth_data[56:58] != b'\r\n':
                return False
            
            # Извлекаем хеш пароля
            password_hash_hex = auth_data[:56].decode()
            
            # Сравниваем с ожидаемым хешем
            if password_hash_hex == self.expected_hash:
                # Убираем данные из буфера
                sock.recv(58)
                return True
            
            return False
            
        except Exception as e:
            print(f"[-] Ошибка аутентификации Trojan: {e}")
            return False
    
    def read_trojan_request(self, sock) -> bytes:
        """Чтение Trojan запроса после аутентификации"""
        try:
            # Читаем до \r\n\r\n
            data = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if data.endswith(b'\r\n\r\n'):
                    break
            
            # Убираем завершающие \r\n\r\n
            if data.endswith(b'\r\n\r\n'):
                return data[:-4]
            return data
            
        except Exception as e:
            print(f"[-] Ошибка чтения Trojan запроса: {e}")
            return b''
    
    def create_trojan_response(self, data: bytes) -> bytes:
        """Создание Trojan ответа"""
        return data + b'\r\n\r\n'


class FakeWebServer:
    """Класс для имитации веб-сервера"""
    
    def __init__(self):
        self.responses = [
            # Успешные ответы
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n<html><body><h1>Welcome to Corporate Portal</h1></body></html>",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nService is operating normally.",
            
            # Ошибки
            b"HTTP/1.1 404 Not Found\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n<html><body><h1>404 - Page Not Found</h1></body></html>",
            b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n<html><body><h1>403 - Access Denied</h1></body></html>",
            
            # Перенаправления
            b"HTTP/1.1 301 Moved Permanently\r\nLocation: https://www.example.com\r\nConnection: close\r\n\r\n",
            
            # Серверные ошибки
            b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n<html><body><h1>500 - Server Error</h1></body></html>",
            
            # Загрузка файлов
            b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename=\"report.pdf\"\r\nConnection: close\r\n\r\n" + b"X" * 1000
        ]
    
    def serve_fake_response(self, sock):
        """Отправка случайного фейкового HTTP ответа"""
        try:
            # Читаем весь HTTP запрос (чтобы очистить буфер)
            try:
                request = sock.recv(4096)
                print(f"[WEB] Получен HTTP запрос: {request[:100]}...")
            except:
                pass
            
            # Выбираем случайный ответ
            response = random.choice(self.responses)
            sock.send(response)
            print(f"[WEB] Отправлен фейковый ответ ({len(response)} байт)")
            
        except Exception as e:
            print(f"[-] Ошибка веб-сервера: {e}")
        finally:
            try:
                sock.close()
            except:
                pass


def generate_password_hash(password: str) -> str:
    """Генерация хеша пароля для Trojan протокола"""
    return hashlib.sha224(password.encode()).hexdigest()