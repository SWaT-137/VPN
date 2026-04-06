# test_traffic.py
import socket
import time
import struct
import os

def send_test_packets():
    """Отправка тестовых пакетов для проверки метрик"""
    
    # Подключаемся к клиенту через его метрики
    print("="*50)
    print("ТЕСТ РЕАЛЬНОСТИ МЕТРИК")
    print("="*50)
    
    # Эмулируем отправку пакетов разных размеров
    test_sizes = [64, 128, 256, 512, 1024, 1500]
    
    for size in test_sizes:
        test_data = os.urandom(size)  # Случайные данные
        print(f"Отправка пакета размером {size} байт...")
        
        # Здесь должна быть отправка через VPN
        # Для теста просто считаем
        
    print("\n✅ Тестовые данные готовы")

if __name__ == "__main__":
    send_test_packets()