#!/usr/bin/env python3
"""
Enhanced Anti-DPI Engine 2026 - Production Ready
Интегрированная защита с PFS и полной обфускацией трафика
"""
import ssl
import socket
import struct
import threading
import os
import hashlib
import secrets
import logging
from typing import Optional, Tuple, Dict, List, Set
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
    def __init__(self, window_size: int = 1000):
        self.seen_nonces: Set[bytes] = set()
        self.nonce_timestamps: Dict[bytes, float] = {}
        self.window_size = window_size
        self.lock = threading.RLock()
        threading.Thread(target=self._cleanup_worker, daemon=True).start()

    def is_valid(self, nonce: bytes) -> bool:
        with self.lock:
            if nonce in self.seen_nonces:
                return False
            self.seen_nonces.add(nonce)
            self.nonce_timestamps[nonce] = __import__('time').time()
            if len(self.seen_nonces) > self.window_size:
                oldest = min(self.nonce_timestamps.items(), key=lambda x: x[1])[0]
                self.seen_nonces.discard(oldest)
                del self.nonce_timestamps[oldest]
            return True

    def _cleanup_worker(self):
        while True:
            __import__('time').sleep(60)
            with self.lock:
                cutoff = __import__('time').time() - 300
                for n in [k for k, v in self.nonce_timestamps.items() if v < cutoff]:
                    self.seen_nonces.discard(n)
                    del self.nonce_timestamps[n]

class PerfectForwardSecrecy:
    def __init__(self):
        self.identity_private_key = x25519.X25519PrivateKey.generate()
        self.identity_public_key = self.identity_private_key.public_key()
        self.nonce_manager = SecureNonceManager()

    def generate_ephemeral_keypair(self):
        priv = x25519.X25519PrivateKey.generate()
        return priv, priv.public_key()

    def compute_session_keys(self, our_eph_priv, their_eph_pub, their_id_pub, salt):
        # Используем только обмен эфемерными ключами
        shared_secret = our_eph_priv.exchange(their_eph_pub)
        
        # their_id_pub игнорируется, чтобы избежать рассинхрона, 
        # так как сервер может не иметь ID клиента
        combined_secret = shared_secret
        
        hkdf = HKDF(algorithm=hashes.SHA256(), length=64, salt=salt, info=b"vpn_pfs_2026")
        derived_key = hkdf.derive(combined_secret)
        
        return {
            'encrypt_key': derived_key[:32],
            'decrypt_key': derived_key[32:],
            'mac_key': derived_key[:32]
        }

class BrowserTLSImpersonator:
    def __init__(self, profile: str = 'chrome_124'):
        self.current_profile = profile
        self.session = None
        if CURL_CFFI_AVAILABLE:
            try:
                self.session = Session()
                self.session.impersonate = profile
                logger.info(f"[*] JA3: {profile}")
            except Exception as e:
                logger.warning(f"[!] JA3 failed: {e}")
                self.session = None

    def create_ssl_context(self, is_server: bool = False, verify_cert: bool = True):
        if is_server:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            from server import CERT_FILE, KEY_FILE
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        else:
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED if verify_cert else ssl.CERT_NONE
        return ctx

class MultiLayerObfuscator:
    def __init__(self, session_key: bytes):
        self.session_key = session_key
        self.packet_counter = 0  # Синхронизированный счётчик
        self.lock = threading.RLock()
    
    def obfuscate(self, data: bytes, packet_id: int = None) -> bytes:
        """Шифрование с синхронизированным счётчиком"""
        with self.lock:
            # Используем внутренний счётчик вместо внешнего packet_id
            xor_key = hashlib.sha256(self.session_key + self.packet_counter.to_bytes(8, 'big')).digest()
            res = bytes(a ^ b for a, b in zip(data, xor_key * (len(data) // len(xor_key) + 1)))
            pad = secrets.token_bytes(secrets.randbelow(32) + 1)
            result = struct.pack('!H', len(pad)) + pad + res
            self.packet_counter += 1  # Инкремент ПОСЛЕ использования
            return result
    
    def deobfuscate(self, data: bytes) -> Tuple[bytes, int]:
        """Дешифрование с синхронизированным счётчиком"""
        with self.lock:
            # ✅ Добавлена проверка и дефолтный возврат
            if len(data) >= 2:
                pl = struct.unpack('!H', data[:2])[0]
                if pl + 2 <= len(data):
                    data = data[2+pl:]
                    xor_key = hashlib.sha256(self.session_key + self.packet_counter.to_bytes(8, 'big')).digest()
                    res = bytes(a ^ b for a, b in zip(data, xor_key * (len(data) // len(xor_key) + 1)))
                    self.packet_counter += 1  # Инкремент ПОСЛЕ использования
                    return res, self.packet_counter
            # ✅ Дефолтный возврат при ошибке
            return b'', 0

class AdvancedSNISpoofer:
    def __init__(self):
        self.pool = ['cloudflare.com', 'www.google.com', 'azure.microsoft.com', 'workers.dev']
    def get_sni(self):
        return secrets.choice(self.pool)

class AntiDPIEngine:
    def __init__(self, is_server: bool = False, profile: str = 'chrome_124'):
        self.is_server = is_server
        self.current_ja3_profile = profile
        self.tls_impersonator = BrowserTLSImpersonator(profile=self.current_ja3_profile)
        self.sni_spoofer = AdvancedSNISpoofer()
        self.pfs = PerfectForwardSecrecy()
        self.session_keys: Optional[Dict[str, bytes]] = None
        self.cipher: Optional[AESGCM] = None
        self.obfuscator: Optional[MultiLayerObfuscator] = None
        logger.info(f"[+] AntiDPI init (server={is_server})")

    def wrap_socket(self, sock: socket.socket, verify_cert: bool = True):
        # ✅ ИСПРАВЛЕНО: verify_cert вместо verify
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
        if len(peer_eph_len_b) < 2:
            raise ConnectionError("Failed to receive ephemeral key length")
        peer_eph_len = struct.unpack('!H', peer_eph_len_b)[0]
        peer_eph_bytes = self._recv_exact(sock, peer_eph_len)
        if len(peer_eph_bytes) < peer_eph_len:
            raise ConnectionError("Failed to receive ephemeral key")
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
            if len(salt) < 32:
                raise ConnectionError("Failed to receive salt")
        
        if self.is_server and peer_id is None:
            peer_id = peer_eph
        
        self.session_keys = self.pfs.compute_session_keys(eph_priv, peer_eph, peer_id, salt)
        self.cipher = AESGCM(self.session_keys['encrypt_key'])
        self.obfuscator = MultiLayerObfuscator(self.session_keys['mac_key'])
        logger.info("[+] PFS handshake completed")
        print(f"DEBUG KEY HASH: {hashlib.sha256(self.session_keys['encrypt_key']).hexdigest()[:16]}")
        return self.session_keys

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
        """Безопасное получение байт — не выбрасывать при таймауте"""
        data = b''
        while len(data) < length:
            try:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    return data
                data += chunk
            except socket.timeout:
                return data
            except Exception:
                return data
        return data

    def encrypt_packet(self, data: bytes) -> bytes:
        if not self.cipher:
            raise RuntimeError("Handshake not performed")
        nonce = secrets.token_bytes(12)
        enc = self.cipher.encrypt(nonce, data, None)
        pid = __import__('time').time_ns()
        obf = self.obfuscator.obfuscate(enc, pid)
        return nonce + struct.pack('!H', len(obf)) + obf

    def decrypt_packet(self, data: bytes) -> Optional[bytes]:
        if not self.cipher or len(data) < 14:
            return None
        nonce = data[:12]
        if not self.pfs.nonce_manager.is_valid(nonce):
            return None
        length = struct.unpack('!H', data[12:14])[0]
        if len(data) < 14 + length:
            return None
        obf = data[14:14+length]
        enc, _ = self.obfuscator.deobfuscate(obf)
        try:
            return self.cipher.decrypt(nonce, enc, None)
        except Exception:
            return None