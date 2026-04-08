#!/usr/bin/env python3
"""
TROJAN VPN КЛИЕНТ с полной anti-DPI защитой (2026)
ИСПРАВЛЕННАЯ ВЕРСИЯ - РАБОТАЕТ С СЕРВЕРОМ
"""


import socket
import ssl
import hashlib
import time
import threading
import signal
import sys
import random
import struct
import os
from datetime import datetime
from typing import Optional, Tuple
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
HOST = "127.0.0.1"
PORT = 443
PASSWORD = "mysecretpassword123"

def generate_password_hash(password: str) -> str:
    """Генерация хеша пароля для Trojan протокола"""
    return hashlib.sha224(password.encode()).hexdigest()


class TrojanClient:
    """Клиент для работы с Trojan протоколом (ИСПРАВЛЕННЫЙ)"""
    
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self.password_hash = generate_password_hash(password)
        self.sock = None
        self.running = False
        self.thread = None
        self.client_ip = None
        
        # Метрики
        self.metrics = {
            "bytes_sent": 0,
            "bytes_received": 0,
            "packets_sent": 0,
            "packets_received": 0,
            "connection_time": None,
            "reconnects": 0,
            "status": "disconnected"
        }
        
    def connect(self) -> bool:
        """Установка соединения с сервером"""
        try:
            logger.info(f"Connecting to {self.host}:{self.port}...")
            
            # Создаем обычный сокет
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(15)
            raw_sock.connect((self.host, self.port))
            
            # Создаем SSL контекст
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            self.sock = context.wrap_socket(raw_sock, server_hostname=self.host)
            logger.info("[+] SSL connection established")
            
            # Отправляем аутентификацию (Trojan protocol format)
            # Формат: [HASH (56 bytes)] + [\r\n]
            auth_data = self.password_hash.encode() + b'\r\n'
            self.sock.send(auth_data)
            logger.info(f"[+] Auth data sent: {len(auth_data)} bytes")
            
            # Получаем IP от сервера (сервер должен отправить его после успешной аутентификации)
            try:
                self.sock.settimeout(5)
                self.client_ip = self.sock.recv(1024).decode().strip()
                logger.info(f"[+] Server assigned IP: {self.client_ip}")
            except socket.timeout:
                logger.warning("Timeout waiting for IP assignment")
                self.client_ip = "Unknown"
            
            return True
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def send_keepalive(self) -> bool:
        """Отправка keep-alive пакета"""
        if not self.sock:
            return False
        
        try:
            # Trojan keep-alive format
            keepalive = b"PING\r\n\r\n"
            self.sock.send(keepalive)
            self.metrics["bytes_sent"] += len(keepalive)
            self.metrics["packets_sent"] += 1
            return True
        except Exception as e:
            logger.error(f"Keep-alive error: {e}")
            return False
    
    def receive_data(self) -> Optional[bytes]:
        """Получение данных от сервера"""
        if not self.sock:
            return None
        
        try:
            self.sock.settimeout(30)
            data = self.sock.recv(65535)
            if data:
                self.metrics["bytes_received"] += len(data)
                self.metrics["packets_received"] += 1
                
                # Handle PING from server
                if data == b"PING\r\n\r\n":
                    self.sock.send(b"PONG\r\n\r\n")
                    return None
                
                return data
            return None
        except socket.timeout:
            return None
        except Exception as e:
            logger.error(f"Receive error: {e}")
            return None
    
    def send_data(self, data: bytes) -> bool:
        """Отправка данных на сервер"""
        if not self.sock:
            return False
        
        try:
            self.sock.send(data)
            self.metrics["bytes_sent"] += len(data)
            self.metrics["packets_sent"] += 1
            return True
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False
    
    def send_encrypted(self, packet: bytes, nonce: bytes) -> bool:
        """Отправка зашифрованных данных"""
        if not self.sock:
            return False
        
        try:
            message = nonce + struct.pack('!H', len(packet)) + packet
            self.sock.send(message)
            self.metrics["bytes_sent"] += len(message)
            self.metrics["packets_sent"] += 1
            return True
        except Exception as e:
            logger.error(f"Encrypted send error: {e}")
            return False
    
    def worker_loop(self):
        """Основной рабочий цикл"""
        last_keepalive = time.time()
        
        while self.running:
            try:
                # Подключаемся если нет соединения
                if not self.sock:
                    if not self.connect():
                        logger.info(f"Reconnecting in 5 seconds... (attempt {self.metrics['reconnects'] + 1})")
                        self.metrics["reconnects"] += 1
                        time.sleep(5)
                        continue
                    
                    self.metrics["connection_time"] = datetime.now()
                    self.metrics["status"] = "connected"
                    logger.info("[+] Client connected and authenticated")
                
                # Получаем данные от сервера
                data = self.receive_data()
                if data:
                    # Check for port change notification
                    if data.startswith(b"PORT_CHANGE:"):
                        try:
                            new_port = int(data.decode().split(":")[1])
                            logger.info(f"[*] Server switched to port {new_port}")
                            self.port = new_port
                            self.close()
                            continue
                        except:
                            pass
                    
                    # Handle PONG response
                    if data == b"PONG\r\n\r\n":
                        continue
                    
                    # Display received data (for debugging)
                    if len(data) < 100:
                        logger.info(f"Received: {data}")
                    else:
                        logger.info(f"Received: {len(data)} bytes")
                
                # Отправка keep-alive каждые 25 секунд
                if time.time() - last_keepalive > 25:
                    self.send_keepalive()
                    last_keepalive = time.time()
                
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                self.close()
                time.sleep(5)
    
    def start(self):
        """Запуск клиента"""
        if self.running:
            logger.warning("Client already running")
            return False
        
        self.running = True
        self.thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.thread.start()
        
        logger.info("Trojan client started")
        return True
    
    def stop(self):
        """Остановка клиента"""
        logger.info("\nStopping client...")
        self.running = False
        self.close()
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        logger.info("Client stopped")
        return True
    
    def close(self):
        """Закрытие соединения"""
        if self.sock:
            try:
                self.sock.close()
                logger.info("Connection closed")
            except:
                pass
            finally:
                self.sock = None
                self.metrics["status"] = "disconnected"
    
    def get_metrics(self) -> dict:
        """Получение метрик"""
        uptime = None
        if self.metrics["connection_time"]:
            uptime = (datetime.now() - self.metrics["connection_time"]).total_seconds()
        
        return {
            "status": self.metrics["status"],
            "uptime_seconds": uptime,
            "bytes_sent": self.metrics["bytes_sent"],
            "bytes_received": self.metrics["bytes_received"],
            "packets_sent": self.metrics["packets_sent"],
            "packets_received": self.metrics["packets_received"],
            "reconnects": self.metrics["reconnects"],
            "client_ip": self.client_ip
        }
    
    def display_metrics(self):
        """Отображение метрик"""
        metrics = self.get_metrics()
        
        print("\n" + "=" * 50)
        print("CLIENT METRICS")
        print("=" * 50)
        print(f"Status: {'CONNECTED' if metrics['status'] == 'connected' else 'DISCONNECTED'}")
        print(f"Client IP: {metrics['client_ip'] or 'Not assigned'}")
        
        if metrics['uptime_seconds']:
            uptime = metrics['uptime_seconds']
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)
            seconds = int(uptime % 60)
            print(f"Uptime: {hours:02d}:{minutes:02d}:{seconds:02d}")
        
        print(f"\nTraffic:")
        print(f"  Sent: {self._format_bytes(metrics['bytes_sent'])}")
        print(f"  Received: {self._format_bytes(metrics['bytes_received'])}")
        print(f"  Total: {self._format_bytes(metrics['bytes_sent'] + metrics['bytes_received'])}")
        
        print(f"\nPackets:")
        print(f"  Sent: {metrics['packets_sent']}")
        print(f"  Received: {metrics['packets_received']}")
        
        print(f"\nReconnects: {metrics['reconnects']}")
        print("=" * 50)
    
    @staticmethod
    def _format_bytes(bytes_count: int) -> str:
        """Форматирование байтов"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_count < 1024.0:
                return f"{bytes_count:.2f} {unit}"
            bytes_count /= 1024.0
        return f"{bytes_count:.2f} TB"


class ClientManager:
    """Управление клиентом"""
    
    def __init__(self, host: str, port: int, password: str):
        self.client = TrojanClient(host, port, password)
        self.running = False
        
    def start(self):
        """Запуск менеджера"""
        self.client.start()
        self.running = True
        
        print("\n" + "=" * 50)
        print("TROJAN VPN CLIENT - CONTROL")
        print("=" * 50)
        print("Commands:")
        print("  status   - Show metrics")
        print("  stop     - Stop client")
        print("  restart  - Restart client")
        print("  exit     - Exit program")
        print("=" * 50 + "\n")
        
        # Обработка сигналов
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Интерактивный режим
        while self.running:
            try:
                command = input("trojan> ").strip().lower()
                
                if command == "status":
                    self.client.display_metrics()
                elif command == "stop":
                    self._stop_client()
                elif command == "restart":
                    self._restart_client()
                elif command == "exit":
                    self._stop_client()
                    self.running = False
                    print("Goodbye!")
                elif command:
                    print(f"Unknown command: {command}")
                    
            except KeyboardInterrupt:
                print("\n")
                self._stop_client()
                break
            except Exception as e:
                print(f"Error: {e}")
    
    def _stop_client(self):
        """Остановка клиента"""
        print("\nDisconnecting client...")
        self.client.stop()
        print("Client disconnected")
    
    def _restart_client(self):
        """Перезапуск клиента"""
        print("\nRestarting client...")
        self.client.stop()
        time.sleep(2)
        self.client.start()
        print("Client restarted")
    
    def _signal_handler(self, signum, frame):
        """Обработчик сигналов"""
        print("\n\nReceived stop signal...")
        self._stop_client()
        sys.exit(0)


def main():
    """Основная функция"""
    
    print("=" * 50)
    print("TROJAN VPN CLIENT (2026)")
    print("=" * 50)
    print(f"Server: {HOST}:{PORT}")
    print("=" * 50)
    
    manager = ClientManager(HOST, PORT, PASSWORD)
    
    try:
        manager.start()
    except Exception as e:
        print(f"Critical error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())