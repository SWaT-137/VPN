#!/usr/bin/env python3
"""
Reality + VLESS Cryptographic Engine
Реализует принципы Reality: X25519 handshake, HKDF key derivation, AEAD, VLESS framing.
"""
import os
import struct
import hashlib
import logging
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

logger = logging.getLogger(__name__)


class RealityEngine:
    SHORT_ID_LEN = 8
    PUB_KEY_LEN = 32
    NONCE_LEN = 12
    VLESS_VERSION = 1
    VLESS_CMD_TCP = 0x01
    VLESS_ADDR_IPv4 = 0x01

    def __init__(self, private_key_bytes: bytes = None, short_id: bytes = None):
        if private_key_bytes:
            self.private_key = x25519.X25519PrivateKey.from_private_bytes(private_key_bytes)
        else:
            self.private_key = x25519.X25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes()
        self.short_id = short_id or os.urandom(self.SHORT_ID_LEN)
        self.traffic_key = None
        self.cipher = None
        logger.info("[+] Reality engine initialized")

    # --- Handshake (Reality-like) ---
    def client_handshake(self, sock, server_ip: str, server_port: int) -> bool:
        """Клиент: отправляет SHORT_ID + Ephemeral PubKey, получает Server PubKey + Auth Tag"""
        try:
            client_eph = x25519.X25519PrivateKey.generate()
            client_pub = client_eph.public_key().public_bytes()
            sock.sendall(self.short_id + client_pub)

            response = self._recv_exact(sock, self.PUB_KEY_LEN + 32)
            server_pub = response[:self.PUB_KEY_LEN]
            auth_tag = response[self.PUB_KEY_LEN:]

            shared = client_eph.exchange(x25519.X25519PublicKey.from_public_bytes(server_pub))
            expected_tag = hashlib.sha256(client_pub + server_pub + self.short_id).digest()
            if auth_tag != expected_tag:
                logger.error("[!] Reality handshake failed: invalid auth tag")
                return False

            self._derive_traffic_key(shared)
            return True
        except Exception as e:
            logger.error(f"[!] Handshake client error: {e}")
            return False

    def server_handshake(self, sock) -> bool:
        """Сервер: проверяет SHORT_ID, отправляет PubKey + Auth Tag"""
        try:
            client_data = self._recv_exact(sock, self.SHORT_ID_LEN + self.PUB_KEY_LEN)
            short_id_recv = client_data[:self.SHORT_ID_LEN]
            client_pub = client_data[self.SHORT_ID_LEN:]

            if short_id_recv != self.short_id:
                logger.warning("[!] Reality handshake failed: invalid short_id")
                return False

            shared = self.private_key.exchange(x25519.X25519PublicKey.from_public_bytes(client_pub))
            auth_tag = hashlib.sha256(client_pub + self.public_key + self.short_id).digest()
            sock.sendall(self.public_key + auth_tag)

            self._derive_traffic_key(shared)
            return True
        except Exception as e:
            logger.error(f"[!] Handshake server error: {e}")
            return False

    def _derive_traffic_key(self, shared_secret: bytes):
        """HKDF-SHA256 key derivation (Reality-style)"""
        self.traffic_key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=self.short_id, info=b"vless_reality_traffic"
        ).derive(shared_secret)
        self.cipher = ChaCha20Poly1305(self.traffic_key)

    # --- AEAD + Framing ---
    def encrypt_packet(self, data: bytes) -> bytes:
        nonce = os.urandom(self.NONCE_LEN)
        ct = self.cipher.encrypt(nonce, data, None)
        return nonce + ct

    def decrypt_packet(self, data: bytes) -> bytes:
        nonce = data[:self.NONCE_LEN]
        ct = data[self.NONCE_LEN:]
        return self.cipher.decrypt(nonce, ct, None)

    @staticmethod
    def pack_vless_header(dest_ip: str, dest_port: int) -> bytes:
        return struct.pack("!B B H B 4s 2s",
                           RealityEngine.VLESS_VERSION,
                           RealityEngine.VLESS_CMD_TCP,
                           dest_port,
                           RealityEngine.VLESS_ADDR_IPv4,
                           socket.inet_aton(dest_ip),
                           b"\x00\x00"
                           )

    @staticmethod
    def unpack_vless_header(data: bytes):
        if len(data) < 10:
            return None
        ver, cmd, port, addr_type, addr_ip, reserved = struct.unpack("!B B H B 4s 2s", data[:10])
        return ver, cmd, port, addr_type, socket.inet_ntoa(addr_ip), reserved

    def _recv_exact(self, sock, length: int) -> bytes:
        data = b""
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                raise ConnectionError("Connection closed during recv")
            data += chunk
        return data