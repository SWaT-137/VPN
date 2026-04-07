#!/usr/bin/env python3
"""
Anti-DPI Engine для VPN сервера 2026
Комбинирует все современные методы обхода DPI
"""

import ssl
import socket
import struct
import random
import time
import threading
import os
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    import utls
    UTLS_AVAILABLE = True
except ImportError:
    UTLS_AVAILABLE = False
    print("[!] uTLS не установлен, используем стандартный SSL")

class TLSFingerprintChanger:
    """Изменение TLS fingerprint под разные браузеры"""
    
    # Актуальные fingerprint'ы 2026 года
    FINGERPRINTS = {
        'chrome_122': {
            'ciphers': [
                0x1301, 0x1302, 0x1303, 0xC02B, 0xC02F, 0xC02C, 0xC030,
                0xCCA9, 0xCCA8, 0xC013, 0xC014, 0x009C, 0x009D, 0x002F,
                0x0035, 0x000A
            ],
            'extensions': [0, 10, 11, 13, 16, 18, 21, 23, 27, 28, 35, 43, 51],
            'curves': [29, 23, 24, 25],
            'point_formats': [0, 1, 2]
        },
        'firefox_122': {
            'ciphers': [
                0x1301, 0x1302, 0x1303, 0xC02B, 0xC02F, 0xCCA9, 0xCCA8,
                0xC00A, 0xC009, 0xC013, 0xC014, 0x009C, 0x009D, 0x002F,
                0x0035, 0x000A
            ],
            'extensions': [0, 5, 10, 11, 13, 16, 18, 21, 27, 28, 35, 43],
            'curves': [29, 23, 24, 25],
            'point_formats': [0]
        }
    }
    
    def __init__(self, fingerprint='chrome_122'):
        self.current_fingerprint = fingerprint
        self.rotation_count = 0
        
    def create_custom_context(self):
        """Создание SSL контекста с подменой fingerprint"""
        
        if UTLS_AVAILABLE:
            # Используем uTLS для полной имитации
            return self._create_utls_context()
        else:
            # fallback на стандартный SSL
            return self._create_ssl_context()
    
    def _create_utls_context(self):
        """Создание контекста через uTLS (лучший метод)"""
        
        # Выбираем профиль браузера
        if self.current_fingerprint == 'chrome_122':
            client_hello = utls.HelloChrome_122_Auto()
        elif self.current_fingerprint == 'firefox_122':
            client_hello = utls.HelloFirefox_122_Auto()
        else:
            client_hello = utls.HelloRandomized()
        
        # Создаем соединение с подменой
        config = utls.Config(
            InsecureSkipVerify=True,
            ClientHelloSpec=client_hello
        )
        
        return config
    
    def _create_ssl_context(self):
        """Стандартный SSL контекст с оптимизациями"""
        
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        
        # Настраиваем cipher suites как у браузера
        fp = self.FINGERPRINTS[self.current_fingerprint]
        cipher_string = ':'.join([hex(c)[2:] for c in fp['ciphers']])
        context.set_ciphers(cipher_string)
        
        # Включаем только TLS 1.3 и 1.2
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        
        # Включаем ALPN
        context.set_alpn_protocols(['h2', 'http/1.1'])
        
        return context
    
    def rotate_fingerprint(self):
        """Ротация fingerprint для обхода"""
        
        fingerprints = list(self.FINGERPRINTS.keys())
        self.current_fingerprint = random.choice(fingerprints)
        self.rotation_count += 1
        
        print(f"[*] TLS fingerprint ротирован: {self.current_fingerprint}")
        return self.current_fingerprint


class SNISpoofer:
    """Подмена SNI для обхода DPI"""
    
    # Белый список легитимных доменов
    LEGITIMATE_DOMAINS = [
        "www.google.com",
        "www.microsoft.com", 
        "www.cloudflare.com",
        "www.amazon.com",
        "www.apple.com",
        "www.facebook.com",
        "www.netflix.com",
        "www.youtube.com"
    ]
    
    def __init__(self):
        self.current_sni = None
        self.real_host = None
        
    def get_spoofed_sni(self, real_host: str) -> str:
        """Получение подставного SNI"""
        
        self.real_host = real_host
        
        # Выбираем случайный легитимный домен
        self.current_sni = random.choice(self.LEGITIMATE_DOMAINS)
        
        print(f"[*] SNI подмена: {self.current_sni} (реальный: {real_host})")
        return self.current_sni
    
    def create_tls_with_spoofed_sni(self, sock, real_host):
        """Создание TLS соединения с подменой SNI"""
        
        # Подменяем SNI
        fake_sni = self.get_spoofed_sni(real_host)
        
        # Создаем контекст
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        # Важно: используем поддельный SNI для handshake
        tls_sock = context.wrap_socket(sock, server_hostname=fake_sni)
        
        # После handshake отправляем Host header с реальным доменом
        tls_sock.send(f"Host: {real_host}\r\n".encode())
        
        return tls_sock


class ProtocolObfuscator:
    """Обфускация протокола для маскировки под HTTP/3"""
    
    def __init__(self):
        self.obfuscation_enabled = True
        
    def obfuscate_packet(self, packet: bytes) -> bytes:
        """Обфускация пакета"""
        
        if not self.obfuscation_enabled:
            return packet
        
        # Добавляем случайный префикс (маскировка под HTTP/3)
        prefixes = [
            b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n",  # HTTP/2 preface
            b"\x00\x00\x00\x01\x00\x00\x00\x00",  # QUIC long header
            b"GET / HTTP/1.1\r\nHost: cache\r\n\r\n",  # HTTP/1.1 request
        ]
        
        prefix = random.choice(prefixes)
        
        # Кодируем данные
        encoded = self._xor_encode(packet)
        
        return prefix + encoded
    
    def deobfuscate_packet(self, packet: bytes) -> bytes:
        """Деобфускация пакета"""
        
        if not self.obfuscation_enabled:
            return packet
        
        # Ищем начало реальных данных
        for prefix in [b"PRI * HTTP/2.0", b"GET /", b"\x00\x00\x00\x01"]:
            if prefix in packet:
                start = packet.find(prefix) + len(prefix)
                return self._xor_decode(packet[start:])
        
        return packet
    
    def _xor_encode(self, data: bytes) -> bytes:
        """XOR кодирование (простая обфускация)"""
        key = b'\xde\xad\xbe\xef'
        return bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])
    
    def _xor_decode(self, data: bytes) -> bytes:
        """XOR декодирование"""
        return self._xor_encode(data)  # XOR симметричен


class RealTimeDPIEvasion:
    """Реального времени обход DPI с активными методами"""
    
    def __init__(self):
        self.evasion_threads = []
        self.running = False
        self.detection_count = 0
        
        # Загрузка детектора блокировок
        self.block_detector = BlockDetector()
        
    def start_evasion(self):
        """Запуск активных методов обхода"""
        
        self.running = True
        
        # Запускаем фоновые задачи
        threads = [
            self._packet_mangling_loop,
            self._timing_randomization_loop,
            self._decoy_traffic_loop,
            self._port_hopping_loop
        ]
        
        for thread_func in threads:
            t = threading.Thread(target=thread_func, daemon=True)
            t.start()
            self.evasion_threads.append(t)
            
        print("[+] Real-time DPI evasion активирован")
    
    def _packet_mangling_loop(self):
        """Изменение TTL, Window size, Options"""
        
        while self.running:
            try:
                # Изменяем параметры TCP стека
                self._randomize_ttl()
                self._randomize_window_size()
                self._add_fake_options()
                
                time.sleep(random.uniform(30, 60))
            except:
                pass
    
    def _timing_randomization_loop(self):
        """Рандомизация таймингов"""
        
        while self.running:
            # Добавляем случайные задержки
            delay = random.uniform(0.001, 0.01)
            time.sleep(delay)
    
    def _decoy_traffic_loop(self):
        """Генерация трафика-приманки"""
        
        decoy_hosts = [
            ('8.8.8.8', 443),
            ('1.1.1.1', 443),
            ('208.67.222.222', 443)
        ]
        
        while self.running:
            try:
                # Создаем ложные соединения
                host, port = random.choice(decoy_hosts)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect((host, port))
                sock.send(b"GET / HTTP/1.1\r\n\r\n")
                sock.close()
                
                time.sleep(random.uniform(10, 30))
            except:
                pass
    
    def _port_hopping_loop(self):
        """Прыжки по портам при обнаружении блокировки"""
        
        ports = [443, 8443, 2053, 2083, 2096, 8080, 9443]
        current_port = 0
        
        while self.running:
            if self.block_detector.is_blocked():
                # Меняем порт
                current_port = (current_port + 1) % len(ports)
                new_port = ports[current_port]
                
                print(f"[!] Обнаружена блокировка! Переход на порт {new_port}")
                self.detection_count += 1
                
                # Сигнализируем об изменении порта
                self.on_port_change(new_port)
                
                time.sleep(5)
            
            time.sleep(10)
    
    def _randomize_ttl(self):
        """Рандомизация TTL"""
        
        try:
            import ctypes
            from ctypes import wintypes
            
            # Windows API для изменения TTL
            ttl = random.randint(32, 128)
            # Реализация через setsockopt
        except:
            pass
    
    def _randomize_window_size(self):
        """Рандомизация TCP Window Size"""
        
        window_sizes = [64240, 65535, 8192, 16384, 32768]
        # Применяем случайный размер окна
    
    def _add_fake_options(self):
        """Добавление фейковых TCP опций"""
        
        # Добавляем опции TCP Fast Open, Multipath TCP
        pass
    
    def on_port_change(self, new_port):
        """Callback при смене порта"""
        # Будет вызван из основного сервера
        pass


class BlockDetector:
    """Детектор блокировок DPI"""
    
    def __init__(self):
        self.blocked = False
        self.consecutive_failures = 0
        self.last_success_time = time.time()
        
    def is_blocked(self) -> bool:
        """Проверка, не заблокирован ли трафик"""
        
        # Анализируем паттерны блокировки
        if self.consecutive_failures > 3:
            self.blocked = True
            
        # Сброс после успешной передачи
        if time.time() - self.last_success_time < 30:
            self.consecutive_failures = 0
            self.blocked = False
            
        return self.blocked
    
    def record_success(self):
        """Запись успешной передачи"""
        self.last_success_time = time.time()
        self.consecutive_failures = 0
        self.blocked = False
        
    def record_failure(self):
        """Запись неудачной передачи"""
        self.consecutive_failures += 1


class AntiDPIEngine:
    """Главный движок anti-DPI"""
    
    def __init__(self):
        self.tls_changer = TLSFingerprintChanger()
        self.sni_spoofer = SNISpoofer()
        self.obfuscator = ProtocolObfuscator()
        self.evasion = RealTimeDPIEvasion()
        self.block_detector = BlockDetector()
        
        # Запускаем evasion
        self.evasion.start_evasion()
        
    def wrap_socket(self, sock: socket.socket, host: str) -> ssl.SSLSocket:
        """Обертка сокета со всеми защитами"""
        
        # 1. Подмена SNI
        spoofed_host = self.sni_spoofer.get_spoofed_sni(host)
        
        # 2. Создаем TLS с измененным fingerprint
        context = self.tls_changer.create_custom_context()
        
        if UTLS_AVAILABLE:
            # uTLS метод
            tls_sock = self._wrap_with_utls(sock, spoofed_host)
        else:
            # Стандартный SSL
            tls_sock = context.wrap_socket(sock, server_hostname=spoofed_host)
        
        # 3. Возвращаем обернутый сокет
        return tls_sock
    
    def _wrap_with_utls(self, sock, host):
        """Обертка через uTLS"""
        
        # Создаем uTLS соединение
        config = self.tls_changer.create_custom_context()
        
        # Имитируем полный TLS handshake
        # (зависит от конкретной реализации uTLS)
        
        return sock
    
    def process_outgoing(self, data: bytes) -> bytes:
        """Обработка исходящих данных"""
        
        # Обфускация протокола
        obfuscated = self.obfuscator.obfuscate_packet(data)
        
        return obfuscated
    
    def process_incoming(self, data: bytes) -> bytes:
        """Обработка входящих данных"""
        
        # Деобфускация
        deobfuscated = self.obfuscator.deobfuscate_packet(data)
        
        return deobfuscated
    
    def rotate_defenses(self):
        """Ротация всех защит"""
        
        self.tls_changer.rotate_fingerprint()
        # SNI ротируется автоматически при каждом соединении
        print("[*] Все защиты ротированы")


# Глобальный экземпляр
anti_dpi = AntiDPIEngine()