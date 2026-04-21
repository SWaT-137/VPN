#!/usr/bin/env python3
"""
Lean Anti-DPI Engine - Minimal footprint, pure TLS reliance
"""
import ssl
import socket
import struct
import secrets
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)
class SecureNonceManager:
    def __init__(self, window_size: int = 2000):
        self.seen_nonces = set()
        self.nonce_timestamps = {}
        self.window_size = window_size
        self.lock = __import__('threading').RLock()

    def is_valid(self, nonce: bytes) -> bool:
        with self.lock:
            if nonce in self.seen_nonces:
                return False
            self.seen_nonces.add(nonce)
            self.nonce_timestamps[nonce] = time.time()
            if len(self.seen_nonces) > self.window_size:
                oldest = min(self.nonce_timestamps.items(), key=lambda x: x[1])[0]
                self.seen_nonces.discard(oldest)
                del self.nonce_timestamps[oldest]
            return True
class AdvancedSNISpoofer:
    def __init__(self):
        # Список доменов с реальным трафиком
        self.pool = ['workers.dev', 'www.google.com', 'azure.microsoft.com', 'speed.cloudflare.com']
    def get_sni(self):
        return secrets.choice(self.pool)

class AntiDPIEngine:
    def __init__(self, is_server: bool = False):
        self.is_server = is_server
        self.sni_spoofer = AdvancedSNISpoofer()
        logger.info(f"[+] AntiDPI init (server={is_server})")

    def create_ssl_context(self):
        """Создает контекст, максимально похожий на Chrome"""
        if self.is_server:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            from server import CERT_FILE, KEY_FILE
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        else:
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE # Отключаем проверку (т.к. нет реального сертификата по ТЗ)
        
        # Базовые требования
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3 if ssl.HAS_TLSv1_3 else ssl.TLSVersion.TLSv1_2
        ctx.options |= ssl.OP_NO_COMPRESSION
        
        # ✅ КРИТИЧЕСКИ ВАЖНО ДЛЯ DPI: ALPN. Без него 100% детект как "не браузер"
        ctx.set_alpn_protocols(['h2', 'http/1.1'])
        
        # Порядок шифров, близкий к Chrome (приоритет AESGCM)
        ctx.set_ciphers(
            'TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:'
            'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256'
        )
        return ctx

    def wrap_socket(self, sock: socket.socket):
        ctx = self.create_ssl_context()
        sni = self.sni_spoofer.get_sni()
        logger.info(f"[*] SNI: {sni} (ALPN: h2, http/1.1)")
        return ctx.wrap_socket(sock, server_hostname=sni)

    def encrypt_packet(self, data: bytes) -> bytes:
        """Минимальный фрейминг: 2 байта длины + payload"""
        return struct.pack('!H', len(data)) + data

    def decrypt_packet(self, data: bytes) -> Optional[bytes]:
        """Извлекает payload из фрейма"""
        if len(data) < 2:
            return None
        length = struct.unpack('!H', data[:2])[0]
        if len(data) < 2 + length:
            return None
        return data[2:2+length]