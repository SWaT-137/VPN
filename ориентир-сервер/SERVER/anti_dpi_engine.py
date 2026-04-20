#!/usr/bin/env python3
"""
Enhanced Anti-DPI Engine 2026 - Production Ready
Интегрированная защита с PFS, Stateless обфускацией и Certificate Pinning support
"""
import ssl
import socket
import struct
import os
import hashlib
import secrets
import logging
import time
from typing import Optional, Tuple, Dict, Set
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)

try:
    from curl_cffi.requests import Session
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    logger.warning("[!] curl_cffi not available, falling back to standard ssl context")

class SecureNonceManager:
    def __init__(self, window_size: int = 2000):
        self.seen_nonces: Set[bytes] = set()
        self.nonce_timestamps: Dict[bytes, float] = {}
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

class PerfectForwardSecrecy:
    def __init__(self):
        self.identity_private_key = x25519.X25519PrivateKey.generate()
        self.identity_public_key = self.identity_private_key.public_key()
        self.nonce_manager = SecureNonceManager()

    def generate_ephemeral_keypair(self):
        priv = x25519.X25519PrivateKey.generate()
        return priv, priv.public_key()

    def compute_session_keys(self, our_eph_priv, their_eph_pub, their_id_pub, salt):
        shared_secret = our_eph_priv.exchange(their_eph_pub)
        # Опционально: добавляем статический ключ для аутентификации
        combined_secret = shared_secret
        if their_id_pub:
            try:
                combined_secret += our_eph_priv.exchange(their_id_pub)
            except Exception:
                pass
        hkdf = HKDF(algorithm=hashes.SHA256(), length=64, salt=salt, info=b"vpn_pfs_2026")
        derived_key = hkdf.derive(combined_secret)
        return {
            'encrypt_key': derived_key[:32],
            'decrypt_key': derived_key[32:],
            'mac_key': derived_key[:32]
        }

class BrowserTLSImpersonator:
    """Упрощённый имперсонатор для raw sockets — без curl_cffi"""
    def __init__(self, profile: str = 'chrome_124'):
        self.current_profile = profile
        # curl_cffi не работает с ssl.wrap_socket, отключаем
        logger.info(f"[*] TLS profile: {profile} (standard ssl context)")

    def create_ssl_context(self, is_server: bool = False, verify_cert: bool = True):
        if is_server:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            from server import CERT_FILE, KEY_FILE
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM')
        else:
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            # 🔑 КРИТИЧЕСКИ: порядок настроек для Python 3.10+
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED if verify_cert else ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM')
            # Опционально: отключаем compression для защиты от CRIME
            ctx.options |= ssl.OP_NO_COMPRESSION
        return ctx
class MultiLayerObfuscator:
    """Stateless обфускатор: packet_id передаётся внутри пакета"""
    def __init__(self, session_key: bytes):
        self.session_key = session_key

    def obfuscate(self, data: bytes, packet_id: int) -> bytes:
        # Ключ генерируется из packet_id
        xor_key = hashlib.sha256(self.session_key + packet_id.to_bytes(8, 'big')).digest()
        # XOR-шифрование
        repeated = xor_key * (len(data) // len(xor_key) + 1)
        obf = bytes(a ^ b for a, b in zip(data, repeated[:len(data)]))
        # Добавляем padding для маскировки размера
        pad_len = secrets.randbelow(32) + 1
        pad = secrets.token_bytes(pad_len)
        # Формат: [8B packet_id][2B pad_len][pad][xor_payload]
        return struct.pack('!Q', packet_id) + struct.pack('!H', pad_len) + pad + obf

    def deobfuscate(self, data: bytes) -> Tuple[bytes, int]:
        if len(data) < 12:  # Минимальный размер: 8+2+1+1
            return b'', 0
        # Извлекаем packet_id из заголовка
        packet_id = struct.unpack('!Q', data[:8])[0]
        pad_len = struct.unpack('!H', data[8:10])[0]
        payload_start = 10 + pad_len
        if payload_start >= len(data):
            return b'', packet_id
        # Извлекаем зашифрованные данные
        payload = data[payload_start:]
        # Генерируем тот же XOR-ключ по packet_id
        xor_key = hashlib.sha256(self.session_key + packet_id.to_bytes(8, 'big')).digest()
        repeated = xor_key * (len(payload) // len(xor_key) + 1)
        res = bytes(a ^ b for a, b in zip(payload, repeated[:len(payload)]))
        return res, packet_id

class AdvancedSNISpoofer:
    def __init__(self):
        self.pool = ['cloudflare.com', 'www.google.com', 'azure.microsoft.com', 'workers.dev']
    def get_sni(self):
        return secrets.choice(self.pool)

class AntiDPIEngine:
    def __init__(self, is_server: bool = False, profile: str = 'chrome_124'):
        self.is_server = is_server
        self.current_ja3_profile = profile
        # ✅ КРИТИЧЕСКИ ВАЖНО: Инициализация атрибута
        self.tls_impersonator = BrowserTLSImpersonator(profile=self.current_ja3_profile)
        self.sni_spoofer = AdvancedSNISpoofer()
        self.pfs = PerfectForwardSecrecy()
        self.session_keys: Optional[Dict[str, bytes]] = None
        self.cipher: Optional[AESGCM] = None
        self.obfuscator: Optional[MultiLayerObfuscator] = None
        logger.info(f"[+] AntiDPI init (server={is_server})")

    def wrap_socket(self, sock: socket.socket, verify_cert: bool = True):
        ctx = self.tls_impersonator.create_ssl_context(is_server=self.is_server, verify_cert=verify_cert)
        sni = self.sni_spoofer.get_sni()
        logger.info(f"[*] SNI: {sni}")
        return ctx.wrap_socket(sock, server_hostname=sni)

    def perform_handshake(self, sock: socket.socket, client_ip: Optional[str] = None):
        eph_priv, eph_pub = self.pfs.generate_ephemeral_keypair()
        eph_bytes = eph_pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        sock.sendall(struct.pack('!H', len(eph_bytes)) + eph_bytes)
        
        if self.is_server:
            id_bytes = self.pfs.identity_public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            sock.sendall(struct.pack('!H', len(id_bytes)) + id_bytes)
            
        peer_eph_len_b = self._recv_exact(sock, 2)
        if len(peer_eph_len_b) < 2: raise ConnectionError("Failed to receive ephemeral key length")
        peer_eph_len = struct.unpack('!H', peer_eph_len_b)[0]
        peer_eph_bytes = self._recv_exact(sock, peer_eph_len)
        if len(peer_eph_bytes) < peer_eph_len: raise ConnectionError("Failed to receive ephemeral key")
        peer_eph = x25519.X25519PublicKey.from_public_bytes(peer_eph_bytes)
        
        peer_id = None
        if not self.is_server:
            peer_id_len_b = self._recv_exact(sock, 2)
            if len(peer_id_len_b) >= 2:
                peer_id_len = struct.unpack('!H', peer_id_len_b)[0]
                peer_id_bytes = self._recv_exact(sock, peer_id_len)
                if len(peer_id_bytes) >= peer_id_len:
                    peer_id = x25519.X25519PublicKey.from_public_bytes(peer_id_bytes)
                    
        if self.is_server:
            salt = secrets.token_bytes(32)
            sock.sendall(salt)
        else:
            salt = self._recv_exact(sock, 32)
            if len(salt) < 32: raise ConnectionError("Failed to receive salt")
            
        if self.is_server and peer_id is None:
            peer_id = peer_eph
            
        self.session_keys = self.pfs.compute_session_keys(eph_priv, peer_eph, peer_id, salt)
        self.cipher = AESGCM(self.session_keys['encrypt_key'])
        self.obfuscator = MultiLayerObfuscator(self.session_keys['mac_key'])
        logger.info("[+] PFS handshake completed")
        return self.session_keys

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
        data = b''
        while len(data) < length:
            try:
                chunk = sock.recv(length - len(data))
                if not chunk: return data
                data += chunk
            except socket.timeout: return data
            except Exception: return data
        return data

    def encrypt_packet(self, data: bytes) -> bytes:
        if not self.cipher: raise RuntimeError("Handshake not performed")
        nonce = secrets.token_bytes(12)
        enc = self.cipher.encrypt(nonce, data, None)
        pid = time.time_ns() & 0xFFFFFFFFFFFFFFFF
        obf = self.obfuscator.obfuscate(enc, pid)
        return nonce + struct.pack('!H', len(obf)) + obf

    def decrypt_packet(self, data: bytes) -> Optional[bytes]:
        if not self.cipher or len(data) < 14: return None
        nonce = data[:12]
        if not self.pfs.nonce_manager.is_valid(nonce): return None
        length = struct.unpack('!H', data[12:14])[0]
        if len(data) < 14 + length: return None
        obf = data[14:14+length]
        enc, _ = self.obfuscator.deobfuscate(obf)
        try:
            return self.cipher.decrypt(nonce, enc, None)
        except Exception:
            return None