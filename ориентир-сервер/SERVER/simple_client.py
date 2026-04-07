"""
TROJAN VPN КЛИЕНТ - Полноценный клиент с серверным режимом
Клиент для тестирования VPN сервера с протоколом Trojan
"""

import socket
import ssl
import hashlib
import time
import threading
import signal
import sys
import json
from datetime import datetime
from collections import deque
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
        self.running = False
        self.thread = None
        self.ip_received = None  # Добавляем атрибут для IP
        self.metrics = {
            "bytes_sent": 0,
            "bytes_received": 0,
            "packets_sent": 0,
            "packets_received": 0,
            "connection_time": None,
            "reconnects": 0,
            "last_error": None,
            "status": "disconnected"
        }
        self.history = deque(maxlen=100)
        
    def connect(self):
        """Установка соединения с сервером"""
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Подключение...")
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(10)
            raw_sock.connect((self.host, self.port))
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SSL handshake...")
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            self.ssl_sock = context.wrap_socket(raw_sock, server_hostname=self.host)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SSL соединение установлено")
            
            return True
        except Exception as e:
            self.metrics["last_error"] = str(e)
            print(f"[-] Ошибка подключения: {e}")
            return False
    
    def authenticate(self):
        """Аутентификация по Trojan протоколу"""
        if not self.ssl_sock:
            return False
        
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Отправка аутентификации...")
            auth_data = self.password_hash.encode() + b'\r\n'
            self.ssl_sock.send(auth_data)
            self.metrics["bytes_sent"] += len(auth_data)
            self.metrics["packets_sent"] += 1
            print(f"    Отправлен хеш пароля: {self.password_hash}")
            
            return True
        except Exception as e:
            self.metrics["last_error"] = str(e)
            print(f"[-] Ошибка аутентификации: {e}")
            return False
    
    def send_keepalive(self):
        """Отправка keep-alive пакета"""
        if not self.ssl_sock:
            return False
        
        try:
            keepalive = b"PING\r\n\r\n"
            self.ssl_sock.send(keepalive)
            self.metrics["bytes_sent"] += len(keepalive)
            self.metrics["packets_sent"] += 1
            print(f"[DEBUG] Отправлен PING")
            return True
        except Exception as e:
            print(f"[-] Ошибка отправки keep-alive: {e}")
            return False
    
    def receive_data(self):
        """Получение данных от сервера"""
        if not self.ssl_sock:
            return None
        
        try:
            self.ssl_sock.settimeout(30)
            data = self.ssl_sock.recv(4096)
            if data:
                self.metrics["bytes_received"] += len(data)
                self.metrics["packets_received"] += 1
                return data
            return None
        except socket.timeout:
            return None
        except Exception as e:
            self.metrics["last_error"] = str(e)
            return None
    
    def send_data(self, data):
        """Отправка данных на сервер"""
        if not self.ssl_sock:
            return False
        
        try:
            if isinstance(data, str):
                data = data.encode()
            
            request_data = data + b'\r\n\r\n'
            self.ssl_sock.send(request_data)
            self.metrics["bytes_sent"] += len(request_data)
            self.metrics["packets_sent"] += 1
            return True
        except Exception as e:
            self.metrics["last_error"] = str(e)
            return False
    
    def worker_loop(self):
        """Основной рабочий цикл клиента"""
        while self.running:
            try:
                # Подключение если нет соединения
                if not self.ssl_sock:
                    if not self.connect():
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Переподключение через 5 секунд...")
                        self.metrics["reconnects"] += 1
                        time.sleep(5)
                        continue
                    
                    if not self.authenticate():
                        self.close()
                        time.sleep(5)
                        continue
                    
                    self.metrics["connection_time"] = datetime.now()
                    self.metrics["status"] = "connected"
                    self.ip_received = None  # Сбрасываем IP при новом подключении
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Клиент подключен и аутентифицирован")
                
                # Получение данных от сервера
                data = self.receive_data()
                if data:
                    # Проверяем, не keep-alive ли это
                    if data == b"PING":
                        self.send_data(b"PONG")
                        print("[DEBUG] Получен PING, отправлен PONG")
                        continue
                    
                    if data == b"PONG":
                        print("[DEBUG] Получен PONG")
                        continue
                    
                    # Обработка IP адреса при первом подключении
                    if self.ip_received is None:
                        try:
                            self.ip_received = data.decode().strip()
                            print(f"[+] Получен IP от сервера: {self.ip_received}")
                        except:
                            print(f"[DEBUG] Получены данные: {data[:100]}")
                    else:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Получено: {data[:100]}")
                        # Отправляем подтверждение (опционально)
                        # self.send_data(b"ACK: Data received")
                
                # Отправка keep-alive каждые 25 секунд
                time.sleep(25)
                self.send_keepalive()
                
                # Сохраняем метрики
                self.save_metrics_snapshot()
                
            except Exception as e:
                self.metrics["last_error"] = str(e)
                self.metrics["status"] = "error"
                print(f"[-] Ошибка в рабочем цикле: {e}")
                self.close()
                time.sleep(5)
    
    def save_metrics_snapshot(self):
        """Сохранение снимка метрик в историю"""
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "bytes_sent": self.metrics["bytes_sent"],
            "bytes_received": self.metrics["bytes_received"],
            "packets_sent": self.metrics["packets_sent"],
            "packets_received": self.metrics["packets_received"],
            "status": self.metrics["status"]
        }
        self.history.append(snapshot)
    
    def start(self):
        """Запуск клиента в фоновом режиме"""
        if self.running:
            print("Клиент уже запущен")
            return False
        
        self.running = True
        self.thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.thread.start()
        print("🚀 Trojan клиент запущен")
        return True
    
    def stop(self):
        """Остановка клиента и закрытие соединения"""
        print("\n🛑 Остановка клиента...")
        self.running = False
        self.close()
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        print("✅ Клиент остановлен")
        return True
    
    def close(self):
        """Закрытие соединения"""
        if self.ssl_sock:
            try:
                self.ssl_sock.close()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Соединение закрыто")
            except:
                pass
            finally:
                self.ssl_sock = None
                self.metrics["status"] = "disconnected"
    
    def get_metrics(self):
        """Получение текущих метрик"""
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
            "last_error": self.metrics["last_error"],
            "bytes_per_second_sent": self.calculate_bandwidth("sent"),
            "bytes_per_second_received": self.calculate_bandwidth("received")
        }
    
    def calculate_bandwidth(self, direction):
        """Расчет пропускной способности"""
        if len(self.history) < 2:
            return 0
        
        latest = self.history[-1]
        oldest = self.history[0]
        
        time_diff = (datetime.fromisoformat(latest["timestamp"]) - 
                    datetime.fromisoformat(oldest["timestamp"])).total_seconds()
        
        if time_diff <= 0:
            return 0
        
        if direction == "sent":
            bytes_diff = latest["bytes_sent"] - oldest["bytes_sent"]
        else:
            bytes_diff = latest["bytes_received"] - oldest["bytes_received"]
        
        return bytes_diff / time_diff if bytes_diff > 0 else 0
    
    def display_metrics(self):
        """Отображение метрик в консоли"""
        metrics = self.get_metrics()
        
        print("\n" + "="*50)
        print("📊 МЕТРИКИ TROJAN КЛИЕНТА")
        print("="*50)
        print(f"Статус: {'✅ Подключен' if metrics['status'] == 'connected' else '❌ Отключен'}")
        
        if metrics['uptime_seconds']:
            uptime = metrics['uptime_seconds']
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)
            seconds = int(uptime % 60)
            print(f"Аптайм: {hours:02d}:{minutes:02d}:{seconds:02d}")
        
        print(f"\n📦 Трафик:")
        print(f"  Отправлено: {self.format_bytes(metrics['bytes_sent'])}")
        print(f"  Получено: {self.format_bytes(metrics['bytes_received'])}")
        print(f"  Всего: {self.format_bytes(metrics['bytes_sent'] + metrics['bytes_received'])}")
        
        print(f"\n📨 Пакеты:")
        print(f"  Отправлено: {metrics['packets_sent']}")
        print(f"  Получено: {metrics['packets_received']}")
        
        print(f"\n⚡ Пропускная способность:")
        print(f"  Отправка: {self.format_bandwidth(metrics['bytes_per_second_sent'])}")
        print(f"  Получение: {self.format_bandwidth(metrics['bytes_per_second_received'])}")
        
        print(f"\n🔄 Переподключения: {metrics['reconnects']}")
        
        if metrics['last_error']:
            print(f"⚠️ Последняя ошибка: {metrics['last_error']}")
        
        print("="*50)
    
    @staticmethod
    def format_bytes(bytes_count):
        """Форматирование байтов в человеко-читаемый формат"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_count < 1024.0:
                return f"{bytes_count:.2f} {unit}"
            bytes_count /= 1024.0
        return f"{bytes_count:.2f} TB"
    
    @staticmethod
    def format_bandwidth(bps):
        """Форматирование пропускной способности"""
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024*1024:
            return f"{bps/1024:.1f} KB/s"
        else:
            return f"{bps/(1024*1024):.1f} MB/s"


class TrojanClientManager:
    """Менеджер для управления клиентом"""
    
    def __init__(self, host, port, password):
        self.client = TrojanClient(host, port, password)
        self.running = False
        
    def start(self):
        """Запуск менеджера с интерактивной консолью"""
        self.client.start()
        self.running = True
        
        print("\n" + "="*50)
        print("TROJAN VPN КЛИЕНТ - УПРАВЛЕНИЕ")
        print("="*50)
        print("Доступные команды:")
        print("  status   - Показать метрики")
        print("  stop     - Остановить клиент и закрыть соединение")
        print("  restart  - Перезапустить клиент")
        print("  exit     - Выйти из программы")
        print("="*50 + "\n")
        
        # Обработка сигналов
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Интерактивный режим
        while self.running:
            try:
                command = input("trojan> ").strip().lower()
                
                if command == "status":
                    self.client.display_metrics()
                elif command == "stop":
                    self.stop_client()
                elif command == "restart":
                    self.restart_client()
                elif command == "exit":
                    self.stop_client()
                    self.running = False
                    print("До свидания!")
                elif command:
                    print(f"Неизвестная команда: {command}")
                    
            except KeyboardInterrupt:
                print("\n")
                self.stop_client()
                break
            except Exception as e:
                print(f"Ошибка: {e}")
    
    def stop_client(self):
        """Остановка клиента - ФУНКЦИЯ ОТКЛЮЧЕНИЯ"""
        print("\n🔌 Выполняется отключение клиента...")
        self.client.stop()
        print("✅ Клиент успешно отключен")
    
    def restart_client(self):
        """Перезапуск клиента"""
        print("\n🔄 Перезапуск клиента...")
        self.client.stop()
        time.sleep(2)
        self.client.start()
        print("✅ Клиент перезапущен")
    
    def signal_handler(self, signum, frame):
        """Обработчик системных сигналов"""
        print("\n\nПолучен сигнал остановки...")
        self.stop_client()
        sys.exit(0)


def main():
    """Основная функция"""
    # Создание и запуск менеджера
    manager = TrojanClientManager(HOST, PORT, PASSWORD)
    
    try:
        manager.start()
    except Exception as e:
        print(f"Критическая ошибка: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
    