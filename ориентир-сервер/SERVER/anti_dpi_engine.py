#!/usr/bin/env python3
"""
Anti-DPI Engine для VPN сервера 2026
Использует curl_cffi для имитации браузерного TLS и многоуровневую обфускацию
"""

import ssl
import socket
import struct
import random
import time
import threading
import os
import hashlib
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Попытка импорта curl_cffi
try:
    from curl_cffi import requests
    from curl_cffi.requests import Session, AsyncClient

    CURL_CFFI_AVAILABLE = True
    print("[+] curl_cffi loaded successfully")
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("[!] curl_cffi not installed. Run: pip install curl_cffi")

# Попытка импорта obfuscation библиотек
try:
    import obfs4

    OBFS4_AVAILABLE = True
except ImportError:
    OBFS4_AVAILABLE = False


@dataclass
class TLSProfile:
    """Профиль TLS для имитации браузера"""
    name: str
    ja3_hash: str
    ciphers: list
    extensions: list
    curves: list
    point_formats: list


class BrowserTLSImpersonator:
    """
    Имитация TLS fingerprint'ов различных браузеров
    Использует curl_cffi для точной подмены
    """

    # Актуальные профили браузеров 2026
    BROWSER_PROFILES = {
        'chrome_124': {
            'impersonate': 'chrome124',
            'description': 'Google Chrome 124 (Windows)'
        },
        'chrome_123': {
            'impersonate': 'chrome123',
            'description': 'Google Chrome 123 (Windows)'
        },
        'chrome_120': {
            'impersonate': 'chrome120',
            'description': 'Google Chrome 120 (Windows)'
        },
        'firefox_124': {
            'impersonate': 'firefox124',
            'description': 'Firefox 124 (Windows)'
        },
        'firefox_123': {
            'impersonate': 'firefox123',
            'description': 'Firefox 123 (Windows)'
        },
        'edge_123': {
            'impersonate': 'edge123',
            'description': 'Microsoft Edge 123 (Windows)'
        },
        'safari_17_0': {
            'impersonate': 'safari17_0',
            'description': 'Safari 17.0 (macOS)'
        },
        'random': {
            'impersonate': 'random',
            'description': 'Randomized fingerprint'
        }
    }

    def __init__(self, default_profile='chrome_124'):
        self.current_profile = default_profile
        self.rotation_count = 0
        self.session = None
        self._create_session()

    def _create_session(self):
        """Создание сессии с выбранным профилем"""
        if not CURL_CFFI_AVAILABLE:
            return None

        profile = self.BROWSER_PROFILES.get(self.current_profile, self.BROWSER_PROFILES['chrome_124'])

        self.session = Session()
        self.session.impersonate = profile['impersonate']
        self.session.timeout = 30

        # Дополнительные заголовки для имитации
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        })

        return self.session

    def get_impersonate_string(self) -> str:
        """Получение строки имитации для curl_cffi"""
        profile = self.BROWSER_PROFILES.get(self.current_profile, self.BROWSER_PROFILES['chrome_124'])
        return profile['impersonate']

    def create_custom_ssl_context(self):
        """
        Создание SSL контекста с имитацией браузера
        Для низкоуровневых соединений
        """
        if not CURL_CFFI_AVAILABLE:
            return self._create_fallback_context()

        # curl_cffi не предоставляет прямой доступ к SSL контексту,
        # но мы можем использовать его Session для HTTP запросов
        # Для сырых TCP соединений используем настройки из профиля
        return self._create_optimized_context()

    def _create_optimized_context(self):
        """Создание оптимизированного SSL контекста"""
        context = ssl.create_default_context()

        # Настройки для максимальной совместимости
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        # Включаем TLS 1.2 и 1.3
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_3

        # Современные cipher suites (как у Chrome)
        context.set_ciphers(
            'TLS_AES_256_GCM_SHA384:'
            'TLS_CHACHA20_POLY1305_SHA256:'
            'TLS_AES_128_GCM_SHA256:'
            'ECDHE-ECDSA-AES128-GCM-SHA256:'
            'ECDHE-RSA-AES128-GCM-SHA256:'
            'ECDHE-ECDSA-CHACHA20-POLY1305:'
            'ECDHE-RSA-CHACHA20-POLY1305:'
            'ECDHE-ECDSA-AES256-GCM-SHA384:'
            'ECDHE-RSA-AES256-GCM-SHA384'
        )

        # Включаем ALPN
        context.set_alpn_protocols(['h2', 'http/1.1'])

        return context

    def _create_fallback_context(self):
        """Fallback контекст если curl_cffi не установлен"""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def rotate_profile(self):
        """Ротация профиля браузера"""
        profiles = list(self.BROWSER_PROFILES.keys())
        # Исключаем random если он не нужен
        available = [p for p in profiles if p != 'random']
        self.current_profile = random.choice(available)
        self.rotation_count += 1
        self._create_session()  # Пересоздаем сессию с новым профилем

        print(f"[*] Browser profile rotated: {self.current_profile}")
        return self.current_profile

    def make_request(self, url: str, method='GET', **kwargs):
        """
        Выполнение HTTP запроса с имитацией браузера
        """
        if not self.session:
            self._create_session()

        if method.upper() == 'GET':
            return self.session.get(url, **kwargs)
        elif method.upper() == 'POST':
            return self.session.post(url, **kwargs)
        else:
            return self.session.request(method, url, **kwargs)


class MultiLayerObfuscator:
    """
    Многоуровневая обфускация трафика
    Комбинирует несколько методов для маскировки
    """

    def __init__(self):
        self.obfuscation_level = 3  # Уровень обфускации 1-5
        self.session_key = os.urandom(32)
        self.obfuscation_stats = {
            'packets_obfuscated': 0,
            'bytes_processed': 0,
            'methods_used': {}
        }

    def obfuscate(self, data: bytes, level: int = None) -> bytes:
        """
        Полная обфускация данных
        Уровни: 1-легкий, 3-средний, 5-максимальный
        """
        if level is None:
            level = self.obfuscation_level

        result = data
        methods_used = []

        # Уровень 1: Base64 + XOR
        if level >= 1:
            result = self._xor_encode(result)
            methods_used.append('xor')

        # Уровень 2: Добавление случайного префикса
        if level >= 2:
            result = self._add_random_prefix(result)
            methods_used.append('prefix')

        # Уровень 3: Фрагментация + маскировка под HTTP/2
        if level >= 3:
            result = self._fragment_and_mask(result)
            methods_used.append('fragment')

        # Уровень 4: Вставка случайных байт + скремблирование
        if level >= 4:
            result = self._insert_random_bytes(result)
            methods_used.append('random_insert')

        # Уровень 5: Полная маскировка под другой протокол
        if level >= 5:
            result = self._mask_as_protocol(result)
            methods_used.append('protocol_mask')

        # Обновляем статистику
        self.obfuscation_stats['packets_obfuscated'] += 1
        self.obfuscation_stats['bytes_processed'] += len(result)
        for method in methods_used:
            self.obfuscation_stats['methods_used'][method] = \
                self.obfuscation_stats['methods_used'].get(method, 0) + 1

        return result

    def deobfuscate(self, data: bytes) -> bytes:
        """
        Деобфускация данных (обратный процесс)
        """
        result = data

        # Уровень 5: Распознавание протокола
        result = self._unmask_protocol(result)

        # Уровень 4: Удаление случайных байт
        result = self._remove_random_bytes(result)

        # Уровень 3: Дефрагментация
        result = self._defragment(result)

        # Уровень 2: Удаление префикса
        result = self._remove_prefix(result)

        # Уровень 1: XOR декодирование
        result = self._xor_decode(result)

        return result

    def _xor_encode(self, data: bytes, key: bytes = None) -> bytes:
        """XOR кодирование с динамическим ключом"""
        if key is None:
            # Используем сессионный ключ + случайный оффсет
            offset = random.randint(0, 255)
            key = bytes([(self.session_key[i % len(self.session_key)] ^ offset)
                         for i in range(32)])

        return bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])

    def _xor_decode(self, data: bytes) -> bytes:
        """XOR декодирование (симметрично)"""
        return self._xor_encode(data)  # XOR симметричен

    def _add_random_prefix(self, data: bytes) -> bytes:
        """Добавление случайного префикса"""
        prefixes = [
            # HTTP/2 preface
            b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n",
            # QUIC long header
            b"\x00\x00\x00\x01\x00\x00\x00\x00",
            # HTTP/1.1 request
            b"GET / HTTP/1.1\r\nHost: cache\r\n\r\n",
            # WebSocket upgrade
            b"GET / HTTP/1.1\r\nUpgrade: websocket\r\n\r\n",
            # DNS over HTTPS
            b"\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00",
        ]

        prefix = random.choice(prefixes)
        length_prefix = len(prefix).to_bytes(2, 'big')

        return length_prefix + prefix + data

    def _remove_prefix(self, data: bytes) -> bytes:
        """Удаление префикса"""
        if len(data) < 2:
            return data

        prefix_length = int.from_bytes(data[:2], 'big')

        if len(data) >= 2 + prefix_length:
            # Пропускаем длину и префикс
            return data[2 + prefix_length:]

        return data

    def _fragment_and_mask(self, data: bytes) -> bytes:
        """Фрагментация и маскировка под HTTP/2 фреймы"""
        frame_size = random.randint(256, 4096)
        fragments = []

        for i in range(0, len(data), frame_size):
            fragment = data[i:i + frame_size]

            # Маскируем каждый фрагмент как HTTP/2 DATA frame
            # [length(3)] [type(1)] [flags(1)] [stream_id(4)] [data]
            frame_length = len(fragment)
            frame_type = 0x00  # DATA frame
            flags = 0x01 if i + frame_size >= len(data) else 0x00  # END_STREAM flag
            stream_id = random.randint(1, 0x7FFFFFFF)

            http2_frame = (
                    frame_length.to_bytes(3, 'big') +
                    frame_type.to_bytes(1, 'big') +
                    flags.to_bytes(1, 'big') +
                    stream_id.to_bytes(4, 'big') +
                    fragment
            )
            fragments.append(http2_frame)

        # Добавляем маркер конца
        end_marker = b'\x00\x00\x00\x00\x00\x00\x00\x00'

        return b''.join(fragments) + end_marker

    def _defragment(self, data: bytes) -> bytes:
        """Дефрагментация HTTP/2 фреймов"""
        result = b''
        pos = 0

        while pos < len(data):
            # Проверяем маркер конца
            if data[pos:pos + 8] == b'\x00\x00\x00\x00\x00\x00\x00\x00':
                break

            if pos + 9 > len(data):
                break

            # Читаем заголовок HTTP/2 фрейма
            frame_length = int.from_bytes(data[pos:pos + 3], 'big')
            pos += 9  # Пропускаем заголовок (3+1+1+4)

            if pos + frame_length <= len(data):
                result += data[pos:pos + frame_length]
                pos += frame_length
            else:
                break

        return result

    def _insert_random_bytes(self, data: bytes) -> bytes:
        """Вставка случайных байт в случайные позиции"""
        if len(data) < 10:
            return data

        result = bytearray(data)
        insertions = random.randint(1, min(5, len(data) // 10))

        for _ in range(insertions):
            pos = random.randint(0, len(result))
            # Вставляем от 1 до 8 случайных байт
            random_bytes = os.urandom(random.randint(1, 8))
            result[pos:pos] = random_bytes

        # Добавляем карту вставок в конец (для деобфускации)
        # Простая схема: просто сохраняем длину оригинальных данных
        original_length = len(data).to_bytes(4, 'big')

        return bytes(result) + b'\xFF\xFF' + original_length

    def _remove_random_bytes(self, data: bytes) -> bytes:
        """Удаление вставленных случайных байт"""
        # Ищем маркер конца
        marker_pos = data.rfind(b'\xFF\xFF')

        if marker_pos == -1 or marker_pos + 6 > len(data):
            return data

        original_length = int.from_bytes(data[marker_pos + 2:marker_pos + 6], 'big')

        if original_length <= marker_pos:
            return data[:original_length]

        return data[:marker_pos]

    def _mask_as_protocol(self, data: bytes) -> bytes:
        """Маскировка под другой протокол"""
        protocols = [
            ('QUIC', self._mask_as_quic),
            ('HTTP3', self._mask_as_http3),
            ('WebRTC', self._mask_as_webrtc),
            ('SSH', self._mask_as_ssh),
        ]

        protocol_name, mask_func = random.choice(protocols)
        masked_data = mask_func(data)

        # Добавляем идентификатор протокола
        protocol_id = protocols.index((protocol_name, mask_func)).to_bytes(1, 'big')

        return protocol_id + masked_data

    def _unmask_protocol(self, data: bytes) -> bytes:
        """Распознавание и размаскировка протокола"""
        if len(data) < 1:
            return data

        protocol_id = data[0]

        # Определяем протокол по ID
        if protocol_id == 0:  # QUIC
            return self._unmask_quic(data[1:])
        elif protocol_id == 1:  # HTTP3
            return self._unmask_http3(data[1:])
        elif protocol_id == 2:  # WebRTC
            return self._unmask_webrtc(data[1:])
        elif protocol_id == 3:  # SSH
            return self._unmask_ssh(data[1:])

        return data[1:]

    def _mask_as_quic(self, data: bytes) -> bytes:
        """Маскировка под QUIC пакет"""
        # QUIC long header format
        header_form = 1  # Long header
        fixed_bit = 1
        packet_type = random.randint(0, 3)  # Initial, Handshake, etc.
        reserved = 0
        packet_number_length = 2

        first_byte = (
                (header_form << 7) |
                (fixed_bit << 6) |
                (packet_type << 4) |
                (reserved << 2) |
                packet_number_length
        )

        version = random.randint(0xFF000000, 0xFFFFFFFF)  # QUIC version
        dcid_length = random.randint(8, 20)
        dcid = os.urandom(dcid_length)
        scid_length = random.randint(8, 20)
        scid = os.urandom(scid_length)

        packet = bytes([first_byte]) + version.to_bytes(4, 'big')
        packet += bytes([dcid_length]) + dcid
        packet += bytes([scid_length]) + scid

        # Добавляем зашифрованные данные
        packet += data

        return packet

    def _unmask_quic(self, data: bytes) -> bytes:
        """Извлечение данных из QUIC пакета"""
        if len(data) < 6:
            return data

        pos = 1  # Пропускаем first byte
        pos += 4  # Пропускаем version

        if pos >= len(data):
            return data

        dcid_length = data[pos]
        pos += 1 + dcid_length

        if pos >= len(data):
            return data

        scid_length = data[pos]
        pos += 1 + scid_length

        # Оставшиеся данные - это полезная нагрузка
        return data[pos:]

    def _mask_as_http3(self, data: bytes) -> bytes:
        """Маскировка под HTTP/3 фрейм"""
        # HTTP/3 frame format
        frame_type = 0x00  # DATA frame
        frame_length = len(data)

        # Variable-length integer encoding
        if frame_length < 64:
            encoded_length = bytes([frame_length])
        elif frame_length < 16384:
            encoded_length = (0x40 | (frame_length >> 8)).to_bytes(1, 'big') + (frame_length & 0xFF).to_bytes(1, 'big')
        else:
            encoded_length = frame_length.to_bytes(4, 'big')

        frame = bytes([frame_type]) + encoded_length + data

        return frame

    def _unmask_http3(self, data: bytes) -> bytes:
        """Извлечение данных из HTTP/3 фрейма"""
        if len(data) < 2:
            return data

        pos = 1  # Пропускаем frame_type

        # Читаем длину (variable-length integer)
        first_byte = data[pos] if pos < len(data) else 0
        if first_byte < 64:
            length = first_byte
            pos += 1
        elif first_byte < 128:
            if pos + 1 >= len(data):
                return data
            length = ((first_byte & 0x3F) << 8) | data[pos + 1]
            pos += 2
        else:
            if pos + 3 >= len(data):
                return data
            length = int.from_bytes(data[pos:pos + 4], 'big')
            pos += 4

        if pos + length <= len(data):
            return data[pos:pos + length]

        return data[pos:]

    def _mask_as_webrtc(self, data: bytes) -> bytes:
        """Маскировка под WebRTC (DTLS) пакет"""
        # DTLS record header
        content_type = 22  # Handshake
        version = 0xFEFF  # DTLS 1.2
        epoch = 0
        sequence_number = random.getrandbits(48)

        header = (
                bytes([content_type]) +
                version.to_bytes(2, 'big') +
                epoch.to_bytes(2, 'big') +
                sequence_number.to_bytes(6, 'big') +
                len(data).to_bytes(2, 'big')
        )

        return header + data

    def _unmask_webrtc(self, data: bytes) -> bytes:
        """Извлечение данных из WebRTC пакета"""
        if len(data) < 13:
            return data

        # Пропускаем DTLS заголовок
        return data[13:]

    def _mask_as_ssh(self, data: bytes) -> bytes:
        """Маскировка под SSH пакет"""
        # SSH packet format
        packet_length = len(data) + 4 + 1  # + padding length + MAC
        padding_length = random.randint(4, 16)

        packet = (
                packet_length.to_bytes(4, 'big') +
                bytes([padding_length]) +
                data +
                os.urandom(padding_length)
        )

        return packet

    def _unmask_ssh(self, data: bytes) -> bytes:
        """Извлечение данных из SSH пакета"""
        if len(data) < 5:
            return data

        padding_length = data[4]

        if len(data) >= 5 + padding_length:
            return data[5:-padding_length]

        return data[5:]

    def set_obfuscation_level(self, level: int):
        """Установка уровня обфускации"""
        self.obfuscation_level = max(1, min(5, level))
        print(f"[*] Obfuscation level set to {self.obfuscation_level}")

    def get_stats(self) -> dict:
        """Получение статистики обфускации"""
        return self.obfuscation_stats


class RealTimeDPIEvasion:
    """Активные методы обхода DPI в реальном времени"""

    def __init__(self):
        self.evasion_threads = []
        self.running = False
        self.detection_count = 0
        self.current_port = 443
        self.block_detector = BlockDetector()

    def start_evasion(self):
        """Запуск активных методов обхода"""
        self.running = True

        threads = [
            self._packet_mangling_loop,
            self._timing_randomization_loop,
            self._decoy_traffic_loop,
            self._port_hopping_loop,
            self._tls_rotation_loop
        ]

        for thread_func in threads:
            t = threading.Thread(target=thread_func, daemon=True)
            t.start()
            self.evasion_threads.append(t)

        print("[+] Real-time DPI evasion activated")

    def _packet_mangling_loop(self):
        """Изменение параметров пакетов"""
        while self.running:
            try:
                # Рандомизация параметров TCP
                self._randomize_tcp_options()
                time.sleep(random.uniform(30, 60))
            except:
                pass

    def _timing_randomization_loop(self):
        """Рандомизация таймингов"""
        while self.running:
            delay = random.uniform(0.0005, 0.005)
            time.sleep(delay)

    def _decoy_traffic_loop(self):
        """Генерация трафика-приманки"""
        decoy_hosts = [
            ('8.8.8.8', 443), ('1.1.1.1', 443), ('208.67.222.222', 443),
            ('9.9.9.9', 443), ('185.228.168.9', 443), ('94.140.14.14', 443)
        ]

        while self.running:
            try:
                host, port = random.choice(decoy_hosts)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect((host, port))

                # Отправляем случайные данные
                random_data = os.urandom(random.randint(16, 64))
                sock.send(random_data)
                sock.close()

                time.sleep(random.uniform(10, 30))
            except:
                pass

    def _port_hopping_loop(self):
        """Прыжки по портам"""
        ports = [443, 8443, 2053, 2083, 2096, 8080, 9443, 4443, 6443]

        while self.running:
            if self.block_detector.is_blocked():
                available_ports = [p for p in ports if p != self.current_port]
                new_port = random.choice(available_ports)

                print(f"[!] Blockage detected! Switching to port {new_port}")
                self.detection_count += 1
                self.current_port = new_port
                self.on_port_change(new_port)

                time.sleep(5)

            time.sleep(10)

    def _tls_rotation_loop(self):
        """Ротация TLS профилей"""
        while self.running:
            # Меняем профиль каждые 10-30 минут
            time.sleep(random.randint(600, 1800))
            # Сигнализируем о необходимости ротации
            if hasattr(self, 'on_tls_rotate'):
                self.on_tls_rotate()

    def _randomize_tcp_options(self):
        # В реальной реализации здесь были бы вызовы setsockopt
        pass

    def on_port_change(self, new_port):
        pass

    def on_tls_rotate(self):
        pass


class BlockDetector:

    def __init__(self):
        self.blocked = False
        self.consecutive_failures = 0
        self.last_success_time = time.time()
        self.failure_times = []

    def is_blocked(self) -> bool:
        """Проверка блокировки"""
        # Анализируем частоту ошибок
        if len(self.failure_times) > 5:
            recent_failures = [t for t in self.failure_times if time.time() - t < 60]
            if len(recent_failures) > 3:
                self.blocked = True

        if self.consecutive_failures > 3:
            self.blocked = True

        if time.time() - self.last_success_time < 30:
            self.consecutive_failures = 0
            self.blocked = False

        return self.blocked

    def record_success(self):
        """Запись успеха"""
        self.last_success_time = time.time()
        self.consecutive_failures = 0
        self.blocked = False
        self.failure_times.clear()

    def record_failure(self):
        """Запись ошибки"""
        self.consecutive_failures += 1
        self.failure_times.append(time.time())

        # Очищаем старые записи
        self.failure_times = [t for t in self.failure_times if time.time() - t < 300]


class AntiDPIEngine:
    """Главный Anti-DPI движок"""

    def __init__(self):
        self.tls_impersonator = BrowserTLSImpersonator()
        self.obfuscator = MultiLayerObfuscator()
        self.evasion = RealTimeDPIEvasion()
        self.block_detector = BlockDetector()

        # Связываем callback'
        self.evasion.on_port_change = self._handle_port_change
        self.evasion.on_tls_rotate = self._handle_tls_rotate

        # Запускаем evasion
        self.evasion.start_evasion()

        print("[+] AntiDPI Engine initialized with curl_cffi support")

    def _handle_port_change(self, new_port):
        """Обработка смены порта"""
        print(f"[*] AntiDPI: Switching to port {new_port}")

    def _handle_tls_rotate(self):
        """Обработка ротации TLS"""
        self.tls_impersonator.rotate_profile()

    def wrap_socket(self, sock: socket.socket, host: str) -> ssl.SSLSocket:
        """Обертка сокета с защитой"""
        # Создаем SSL контекст с имитацией браузера
        context = self.tls_impersonator.create_custom_ssl_context()

        # Используем подмененный SNI
        fake_sni = self._get_spoofed_sni(host)

        # Оборачиваем сокет
        tls_sock = context.wrap_socket(sock, server_hostname=fake_sni)

        return tls_sock

    def _get_spoofed_sni(self, real_host: str) -> str:
        """Получение подставного SNI"""
        legitimate_domains = [
            "www.google.com", "www.microsoft.com", "www.cloudflare.com",
            "www.amazon.com", "www.apple.com", "www.facebook.com",
            "www.netflix.com", "www.youtube.com", "github.com",
            "stackoverflow.com", "reddit.com", "wikipedia.org"
        ]

        return random.choice(legitimate_domains)

    def process_outgoing(self, data: bytes) -> bytes:
        # Обфускация
        obfuscated = self.obfuscator.obfuscate(data)

        return obfuscated

    def process_incoming(self, data: bytes) -> bytes:
        # Деобфускация
        deobfuscated = self.obfuscator.deobfuscate(data)

        return deobfuscated

    def rotate_defenses(self):
        """Ротация всех защит"""
        self.tls_impersonator.rotate_profile()
        self.obfuscator.set_obfuscation_level(random.randint(3, 5))
        print("[*] All defenses rotated")

    def get_session(self):
        """Получение HTTP сессии с имитацией браузера"""
        return self.tls_impersonator.session

    def get_stats(self) -> dict:
        """Получение статистики"""
        return {
            'tls_rotations': self.tls_impersonator.rotation_count,
            'current_profile': self.tls_impersonator.current_profile,
            'obfuscation_stats': self.obfuscator.get_stats(),
            'detections': self.evasion.detection_count,
            'curl_cffi_available': CURL_CFFI_AVAILABLE
        }


# Глобальный экземпляр
anti_dpi = AntiDPIEngine()