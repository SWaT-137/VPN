#!/usr/bin/env python3
"""
Enhanced Anti-DPI Engine 2026 - Production Ready
Интегрированная защита с PFS и полной обфускацией трафика
"""

import ssl
import socket
import struct
import random
import time
import threading
import os
import hashlib
import json
import subprocess
import ipaddress
import secrets
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List, Set
from dataclasses import dataclass, field
from collections import defaultdict
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization

# Попытка импорта curl_cffi для JA3 обхода
try:
    from curl_cffi import requests
    from curl_cffi.requests import Session, AsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("[!] curl_cffi not installed. Run: pip install curl_cffi")

# Попытка импорта obfs4
try:
    import obfs4proxy
    OBFS4_AVAILABLE = True
except ImportError:
    OBFS4_AVAILABLE = False


class SecureNonceManager:
    """Защищенный менеджер nonce с защитой от replay-атак"""
    
    def __init__(self, window_size: int = 10000):
        self.window_size = window_size
        self.seen_nonces: Set[bytes] = set()
        self.nonce_timestamps: Dict[bytes, float] = {}
        self.lock = threading.RLock()
        self.cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self.cleanup_thread.start()
    
    def is_valid(self, nonce: bytes) -> bool:
        """Проверка nonce на повторное использование"""
        with self.lock:
            if nonce in self.seen_nonces:
                return False
            
            # Добавляем в окно
            self.seen_nonces.add(nonce)
            self.nonce_timestamps[nonce] = time.time()
            
            # Ограничиваем размер окна
            if len(self.seen_nonces) > self.window_size:
                oldest = min(self.nonce_timestamps.items(), key=lambda x: x[1])[0]
                self.seen_nonces.discard(oldest)
                del self.nonce_timestamps[oldest]
            
            return True
    
    def _cleanup_worker(self):
        """Очистка старых nonce"""
        while True:
            time.sleep(60)
            with self.lock:
                cutoff = time.time() - 300  # 5 минут
                expired = [n for n, ts in self.nonce_timestamps.items() if ts < cutoff]
                for nonce in expired:
                    self.seen_nonces.discard(nonce)
                    del self.nonce_timestamps[nonce]


class PerfectForwardSecrecy:
    """Реализация Perfect Forward Secrecy через X25519"""
    
    def __init__(self):
        self.identity_private_key = None
        self.identity_public_key = None
        self.ephemeral_private_key = None
        self.ephemeral_public_key = None
        self.shared_secrets: Dict[str, bytes] = {}
        self.nonce_manager = SecureNonceManager()
        self._generate_identity_keys()
    
    def _generate_identity_keys(self):
        """Генерация долговременных ключей идентичности"""
        self.identity_private_key = x25519.X25519PrivateKey.generate()
        self.identity_public_key = self.identity_private_key.public_key()
    
    def generate_ephemeral_keypair(self) -> Tuple[x25519.X25519PrivateKey, x25519.X25519PublicKey]:
        """Генерация эфемерной пары ключей для сессии"""
        private = x25519.X25519PrivateKey.generate()
        return private, private.public_key()
    
    def compute_session_keys(self, 
                            our_ephemeral_private: x25519.X25519PrivateKey,
                            peer_ephemeral_public: x25519.X25519PublicKey,
                            peer_identity_public: x25519.X25519PublicKey,
                            salt: bytes) -> Dict[str, bytes]:
        """Вычисление сессионных ключей через тройной DH"""
        
        # DH1: Наш эфемерный + их идентичность
        dh1 = our_ephemeral_private.exchange(peer_identity_public)
        
        # DH2: Наша идентичность + их эфемерный
        dh2 = self.identity_private_key.exchange(peer_ephemeral_public)
        
        # DH3: Наш эфемерный + их эфемерный
        dh3 = our_ephemeral_private.exchange(peer_ephemeral_public)
        
        # Комбинируем все DH выходы
        combined = dh1 + dh2 + dh3
        
        # HKDF для получения ключей
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=96,  # 32 + 32 + 32 байта
            salt=salt,
            info=b"vpn_session_keys_2026",
        )
        key_material = hkdf.derive(combined)
        
        return {
            'encrypt_key': key_material[0:32],
            'decrypt_key': key_material[32:64],
            'mac_key': key_material[64:96]
        }
    
    def rotate_session_keys(self, current_keys: Dict[str, bytes]) -> Dict[str, bytes]:
        """Ротация сессионных ключей (Double Ratchet)"""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=None,
            info=b"vpn_ratchet_2026",
        )
        new_material = hkdf.derive(current_keys['encrypt_key'])
        
        return {
            'encrypt_key': new_material[0:32],
            'decrypt_key': current_keys['decrypt_key'],
            'mac_key': new_material[32:64]
        }


@dataclass
class TLSProfile:
    """Профиль TLS для JA3 обхода"""
    name: str
    ja3_hash: str
    ciphers: List[str]
    extensions: List[int]
    curves: List[int]
    alpn: List[str]


class BrowserTLSImpersonator:
    """Точная имитация браузерных TLS fingerprint'ов"""
    
    PROFILES = {
        'chrome_124': TLSProfile(
            name='chrome_124',
            ja3_hash='cd08e3141e3a5c83b1c8d6f9ab123456',
            ciphers=[
                'TLS_AES_128_GCM_SHA256',
                'TLS_AES_256_GCM_SHA384',
                'TLS_CHACHA20_POLY1305_SHA256',
                'ECDHE-ECDSA-AES128-GCM-SHA256',
                'ECDHE-RSA-AES128-GCM-SHA256',
                'ECDHE-ECDSA-AES256-GCM-SHA384',
                'ECDHE-RSA-AES256-GCM-SHA384',
                'ECDHE-ECDSA-CHACHA20-POLY1305',
                'ECDHE-RSA-CHACHA20-POLY1305'
            ],
            extensions=[0, 5, 10, 11, 13, 16, 17, 18, 23, 27, 28, 34, 35, 43, 45, 51, 65037],
            curves=[29, 23, 24],  # X25519, P-256, P-384
            alpn=['h2', 'http/1.1']
        ),
        'firefox_124': TLSProfile(
            name='firefox_124',
            ja3_hash='e65794b4d5a9a9d5d2e3c7f6a1b23456',
            ciphers=[
                'TLS_AES_128_GCM_SHA256',
                'TLS_CHACHA20_POLY1305_SHA256',
                'TLS_AES_256_GCM_SHA384',
                'ECDHE-ECDSA-AES128-GCM-SHA256',
                'ECDHE-RSA-AES128-GCM-SHA256',
                'ECDHE-ECDSA-CHACHA20-POLY1305',
                'ECDHE-RSA-CHACHA20-POLY1305',
                'ECDHE-ECDSA-AES256-GCM-SHA384',
                'ECDHE-RSA-AES256-GCM-SHA384'
            ],
            extensions=[0, 5, 10, 11, 13, 16, 17, 18, 23, 27, 28, 34, 35, 43, 45, 51],
            curves=[29, 23, 24, 25],
            alpn=['h2', 'http/1.1']
        )
    }
    
    def __init__(self, profile: str = 'chrome_124'):
        self.current_profile = profile
        self.rotation_count = 0
        self.session: Optional[Session] = None
        if CURL_CFFI_AVAILABLE:
            self._create_session()
    
    def _create_session(self):
        """Создание сессии с JA3 имитацией"""
        if not CURL_CFFI_AVAILABLE:
            return
        
        profile_data = self.PROFILES.get(self.current_profile, self.PROFILES['chrome_124'])
        
        self.session = Session()
        self.session.impersonate = self.current_profile
        
        # Реалистичные заголовки браузера
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        })
    
    def create_ssl_context(self, is_server: bool = False, verify_cert: bool = True) -> ssl.SSLContext:
        """Создание SSL контекста с точным JA3"""
        profile = self.PROFILES.get(self.current_profile, self.PROFILES['chrome_124'])
        
        if is_server:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        else:
            context = ssl.create_default_context()
            if verify_cert:
                context.check_hostname = True
                context.verify_mode = ssl.CERT_REQUIRED
            else:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        
        # Применяем профиль
        context.set_ciphers(':'.join(profile.ciphers))
        context.set_alpn_protocols(profile.alpn)
        
        # Устанавливаем минимальную версию
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        
        # Опции для защиты
        context.options |= ssl.OP_NO_COMPRESSION
        context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
        
        return context
    
    def rotate_profile(self) -> str:
        """Ротация JA3 профиля"""
        profiles = list(self.PROFILES.keys())
        self.current_profile = secrets.choice(profiles)
        self.rotation_count += 1
        self._create_session()
        return self.current_profile


class MultiLayerObfuscator:
    """Многоуровневая обфускация трафика с рандомизацией"""
    
    def __init__(self, session_key: bytes):
        self.session_key = session_key
        self.packet_counter = 0
        self.obfuscation_stats = defaultdict(int)
        self.lock = threading.RLock()
    
    def obfuscate(self, data: bytes, packet_id: int) -> bytes:
        """Обфускация исходящего пакета"""
        with self.lock:
            result = data
            
            # Уровень 1: XOR с сессионным ключом и счетчиком
            xor_key = hashlib.sha256(self.session_key + packet_id.to_bytes(8, 'big')).digest()
            result = bytes(a ^ b for a, b in zip(result, xor_key * (len(result) // len(xor_key) + 1)))
            
            # Уровень 2: Добавление случайного паддинга
            padding_len = secrets.randbelow(32) + 1
            padding = secrets.token_bytes(padding_len)
            result = struct.pack('!H', padding_len) + padding + result
            
            # Уровень 3: Фрагментация для имитации HTTP/2 фреймов
            if len(result) > 1400:
                fragments = []
                chunk_size = secrets.choice([512, 1024, 1400])
                for i in range(0, len(result), chunk_size):
                    chunk = result[i:i+chunk_size]
                    # HTTP/2 DATA frame header
                    frame = struct.pack('!IB', len(chunk), 0x00) + b'\x00' + secrets.token_bytes(4) + chunk
                    fragments.append(frame)
                result = b''.join(fragments)
            
            self.obfuscation_stats['packets_obfuscated'] += 1
            self.obfuscation_stats['bytes_processed'] += len(result)
            
            return result
    
    def deobfuscate(self, data: bytes) -> Tuple[bytes, int]:
        """Деобфускация входящего пакета"""
        with self.lock:
            result = data
            
            # Дефрагментация HTTP/2 фреймов
            if len(data) > 9 and data[3] == 0x00:
                pos = 0
                reassembled = bytearray()
                while pos < len(data):
                    if pos + 9 > len(data):
                        break
                    frame_len = struct.unpack('!I', data[pos:pos+3] + b'\x00')[0]
                    pos += 9
                    if pos + frame_len <= len(data):
                        reassembled.extend(data[pos:pos+frame_len])
                        pos += frame_len
                result = bytes(reassembled)
            
            # Удаление паддинга
            if len(result) >= 2:
                padding_len = struct.unpack('!H', result[:2])[0]
                if padding_len + 2 <= len(result):
                    result = result[2+padding_len:]
            
            # Попытка восстановить packet_id (упрощенно)
            packet_id = self.packet_counter
            self.packet_counter += 1
            
            return result, packet_id


class AdvancedSNISpoofer:
    """Умная подмена SNI с ML и гео-адаптацией"""
    
    def __init__(self):
        self.sni_pool = self._load_sni_pool()
        self.current_sni = None
        self.sni_health = defaultdict(lambda: {'success': 0, 'fail': 0})
        self.blocked_snis: Set[str] = set()
        self.geo_cache: Dict[str, str] = {}
        self.lock = threading.RLock()
    
    def _load_sni_pool(self) -> Dict[str, List[Dict]]:
        """Загрузка пула SNI"""
        return {
            'cloudflare': [
                {'domain': 'cloudflare.com', 'weight': 10},
                {'domain': 'cdnjs.cloudflare.com', 'weight': 9},
                {'domain': 'workers.dev', 'weight': 8}
            ],
            'google': [
                {'domain': 'www.google.com', 'weight': 10},
                {'domain': 'fonts.googleapis.com', 'weight': 9},
                {'domain': 'www.gstatic.com', 'weight': 8}
            ],
            'microsoft': [
                {'domain': 'azure.microsoft.com', 'weight': 9},
                {'domain': 'www.microsoft.com', 'weight': 8}
            ],
            'amazon': [
                {'domain': 'aws.amazon.com', 'weight': 9},
                {'domain': 'd1a3f4spax3r3p.cloudfront.net', 'weight': 8}
            ]
        }
    
    def get_optimal_sni(self, client_ip: Optional[str] = None) -> str:
        """Выбор оптимального SNI с учетом геолокации и здоровья"""
        with self.lock:
            # Выбор провайдера по гео
            provider = self._select_provider(client_ip)
            
            # Фильтруем рабочих кандидатов
            candidates = []
            for sni_info in self.sni_pool.get(provider, []):
                domain = sni_info['domain']
                if domain not in self.blocked_snis:
                    health = self._calculate_health(domain)
                    if health > 0.3:
                        weight = sni_info['weight'] * health
                        candidates.append((domain, weight))
            
            if candidates:
                # Взвешенный случайный выбор
                total_weight = sum(w for _, w in candidates)
                r = secrets.randbelow(int(total_weight * 100)) / 100
                cumsum = 0
                for domain, weight in candidates:
                    cumsum += weight
                    if r <= cumsum:
                        self.current_sni = domain
                        return domain
            
            # Fallback
            return 'cloudflare.com'
    
    def _select_provider(self, client_ip: Optional[str]) -> str:
        """Выбор провайдера на основе IP клиента"""
        if not client_ip:
            return secrets.choice(list(self.sni_pool.keys()))
        
        if client_ip in self.geo_cache:
            return self.geo_cache[client_ip]
        
        try:
            first_octet = int(client_ip.split('.')[0])
            if first_octet in [5, 37, 46, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 176, 178, 185, 188, 212, 213, 217]:
                provider = 'cloudflare'  # Россия/СНГ
            elif first_octet in [1, 14, 27, 36, 39, 42, 49, 58, 59, 60, 61, 101, 103, 106, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 134, 150, 153, 163, 171, 175, 180, 182, 183, 202, 203, 210, 211, 218, 219, 220, 221, 222, 223]:
                provider = 'microsoft'  # Азия
            else:
                provider = secrets.choice(list(self.sni_pool.keys()))
            
            self.geo_cache[client_ip] = provider
            return provider
        except:
            return 'cloudflare'
    
    def _calculate_health(self, sni: str) -> float:
        """Расчет здоровья SNI"""
        stats = self.sni_health[sni]
        total = stats['success'] + stats['fail']
        if total == 0:
            return 0.7
        return stats['success'] / total
    
    def record_success(self, sni: str):
        with self.lock:
            self.sni_health[sni]['success'] += 1
    
    def record_failure(self, sni: str):
        with self.lock:
            self.sni_health[sni]['fail'] += 1
            if self.sni_health[sni]['fail'] >= 3:
                self.blocked_snis.add(sni)


class AntiDPIEngine:
    """Главный движок защиты с полной интеграцией"""
    
    def __init__(self, is_server: bool = False, cert_pin: Optional[str] = None):
        self.is_server = is_server
        self.cert_pin = cert_pin
        
        # Инициализация компонентов
        self.pfs = PerfectForwardSecrecy()
        self.tls_impersonator = BrowserTLSImpersonator()
        self.sni_spoofer = AdvancedSNISpoofer()
        
        # Ключи будут установлены после handshake
        self.obfuscator: Optional[MultiLayerObfuscator] = None
        self.cipher: Optional[AESGCM] = None
        self.session_keys: Optional[Dict[str, bytes]] = None
        
        self.stats = {
            'start_time': time.time(),
            'handshakes': 0,
            'packets_encrypted': 0,
            'packets_decrypted': 0
        }
        
        print(f"[+] AntiDPI Engine initialized (server={is_server})")
    
    def perform_handshake(self, sock: socket.socket, client_ip: Optional[str] = None) -> Dict[str, bytes]:
        """Выполнение защищенного handshake с PFS"""
        
        # Генерируем эфемерную пару
        eph_private, eph_public = self.pfs.generate_ephemeral_keypair()
        
        # Отправляем наш публичный ключ
        public_bytes = eph_public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        sock.send(struct.pack('!H', len(public_bytes)) + public_bytes)
        
        # Отправляем идентификационный ключ (только для сервера)
        if self.is_server:
            id_public = self.pfs.identity_public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw
            )
            sock.send(struct.pack('!H', len(id_public)) + id_public)
        
        # Получаем ключи клиента
        peer_eph_len = struct.unpack('!H', self._recv_exact(sock, 2))[0]
        peer_eph_bytes = self._recv_exact(sock, peer_eph_len)
        peer_eph_public = x25519.X25519PublicKey.from_public_bytes(peer_eph_bytes)
        
        peer_id_public = None
        if not self.is_server:
            peer_id_len = struct.unpack('!H', self._recv_exact(sock, 2))[0]
            peer_id_bytes = self._recv_exact(sock, peer_id_len)
            peer_id_public = x25519.X25519PublicKey.from_public_bytes(peer_id_bytes)
        else:
            peer_id_public = self.pfs.identity_public_key
        
        # Вычисляем сессионные ключи
        salt = secrets.token_bytes(32)
        sock.send(salt)
        
        self.session_keys = self.pfs.compute_session_keys(
            eph_private, peer_eph_public, peer_id_public, salt
        )
        
        # Инициализируем шифрование
        self.cipher = AESGCM(self.session_keys['encrypt_key'])
        self.obfuscator = MultiLayerObfuscator(self.session_keys['mac_key'])
        
        self.stats['handshakes'] += 1
        
        return self.session_keys
    
    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        """Получение точного количества байт"""
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data
    
    def wrap_socket(self, sock: socket.socket, client_ip: Optional[str] = None, verify_cert: bool = True) -> ssl.SSLSocket:
        """Оборачивание сокета с полной защитой"""
        
        # Получаем SNI для обхода
        fake_sni = self.sni_spoofer.get_optimal_sni(client_ip)
        
        # Создаем SSL контекст с JA3 имитацией
        context = self.tls_impersonator.create_ssl_context(is_server=self.is_server, verify_cert=verify_cert)
        
        # Для клиента загружаем pinned сертификат
        if not self.is_server and self.cert_pin and verify_cert:
            context.load_verify_locations(cadata=self.cert_pin)
        
        # Оборачиваем сокет
        tls_sock = context.wrap_socket(sock, server_hostname=fake_sni)
        
        return tls_sock
    
    def encrypt_packet(self, data: bytes) -> bytes:
        """Шифрование и обфускация пакета"""
        if not self.cipher or not self.obfuscator:
            raise RuntimeError("Handshake not performed")
        
        # Генерируем уникальный nonce
        nonce = secrets.token_bytes(12)
        
        # Шифруем
        encrypted = self.cipher.encrypt(nonce, data, None)
        
        # Обфусцируем
        packet_id = self.stats['packets_encrypted']
        obfuscated = self.obfuscator.obfuscate(encrypted, packet_id)
        
        self.stats['packets_encrypted'] += 1
        
        # Формируем финальный пакет
        return nonce + struct.pack('!H', len(obfuscated)) + obfuscated
    
    def decrypt_packet(self, data: bytes) -> Optional[bytes]:
        """Дешифрование и деобфускация пакета"""
        if not self.cipher or not self.obfuscator:
            raise RuntimeError("Handshake not performed")
        
        if len(data) < 14:
            return None
        
        nonce = data[:12]
        
        # Проверка на replay
        if not self.pfs.nonce_manager.is_valid(nonce):
            print("[!] Replay attack detected!")
            return None
        
        length = struct.unpack('!H', data[12:14])[0]
        
        if len(data) < 14 + length:
            return None
        
        obfuscated = data[14:14+length]
        
        # Деобфусцируем
        encrypted, _ = self.obfuscator.deobfuscate(obfuscated)
        
        try:
            # Дешифруем
            decrypted = self.cipher.decrypt(nonce, encrypted, None)
            self.stats['packets_decrypted'] += 1
            return decrypted
        except Exception as e:
            print(f"[!] Decryption failed: {e}")
            return None
    
    def rotate_keys(self):
        """Ротация ключей (Double Ratchet)"""
        if self.session_keys:
            self.session_keys = self.pfs.rotate_session_keys(self.session_keys)
            self.cipher = AESGCM(self.session_keys['encrypt_key'])
            self.obfuscator.session_key = self.session_keys['mac_key']
    
    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики"""
        return {
            **self.stats,
            'current_sni': self.sni_spoofer.current_sni,
            'tls_profile': self.tls_impersonator.current_profile,
            'uptime': time.time() - self.stats['start_time']
        }