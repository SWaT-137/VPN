#!/usr/bin/env python3
"""
TROJAN VPN CLIENT - ПОЛНОСТЬЮ ИСПРАВЛЕННЫЙ
Исправления: сигнатуры методов, порядок аутентификации, синхронизация соли
"""
import asyncio
import ssl
import struct
import os
import sys
import ctypes
import hashlib
import logging
from datetime import datetime
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 443
PASSWORD = "mysecretpassword123"
SNI_HOST = "www.google.com"
TUN_NAME = "VPNClient"
CLIENT_IP = "10.8.0.2"

class CryptoEngine:
    """Исправленный крипто-движок"""
    def __init__(self, password: str, salt: bytes):
        # Используем ту же соль, что и сервер
        self.salt = salt
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000
        )
        self.key = kdf.derive(password.encode())
        self.cipher = AESGCM(self.key)

    # ✅ ИСПРАВЛЕНО: параметр data: bytes, а не просто bytes
    def encrypt(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self.cipher.encrypt(nonce, data, None)

    def decrypt(self, data: bytes) -> bytes:
        nonce = data[:12]
        ct = data[12:]
        return self.cipher.decrypt(nonce, ct, None)

class WintunAsync:
    def __init__(self):
        dll_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wintun.dll")
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"wintun.dll not found at {dll_path}")
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(os.path.dirname(dll_path))
        self.dll = ctypes.WinDLL(dll_path)
        self._setup_api()
        self.handle = None

    def _setup_api(self):
        d = self.dll
        d.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p] * 3
        d.WintunCreateAdapter.restype = ctypes.c_void_p
        d.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        d.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        d.WintunAllocateSendPacket.restype = ctypes.c_void_p
        d.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        d.WintunReceivePacket.restype = ctypes.c_void_p
        d.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    def create_adapter(self, name: str):
        self.handle = self.dll.WintunCreateAdapter(name, "Wintun", None)
        if not self.handle: raise RuntimeError(f"Failed to create {name}")
        os.system(f'netsh interface ip set address "{name}" static {CLIENT_IP} 255.255.255.0 >nul 2>&1')
        logger.info(f"[+] TUN {name} created: {CLIENT_IP}")

    async def read(self) -> bytes:
        ptr = ctypes.c_void_p()
        size = ctypes.c_uint32(0)
        pkt_ptr = await asyncio.to_thread(
            self.dll.WintunReceivePacket, self.handle, ctypes.byref(ptr), ctypes.byref(size)
        )
        if pkt_ptr and size.value > 0:
            data = ctypes.string_at(pkt_ptr, size.value)
            self.dll.WintunReleaseReceivePacket(self.handle, pkt_ptr)
            return data
        await asyncio.sleep(0.001)
        return b''

    async def write(self, data: bytes):
        if not data: return
        ptr = self.dll.WintunAllocateSendPacket(self.handle, len(data))
        if ptr:
            ctypes.memmove(ptr, data, len(data))
            self.dll.WintunSendPacket(self.handle, ptr)

    def close(self):
        if self.handle:
            self.dll.WintunCloseAdapter(self.handle)
            self.handle = None

class AsyncVPNClient:
    def __init__(self):
        self.wintun = WintunAsync()
        self.reader = self.writer = None
        self.crypto = None
        self.running = False
        self.server_host = SERVER_HOST
        self.server_port = SERVER_PORT
        self.ssl_ctx = self._build_ssl_context()

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM')
        ctx.set_alpn_protocols(['h2', 'http/1.1'])
        return ctx

    async def connect(self) -> bool:
        try:
            logger.info(f"[*] Connecting to {self.server_host}:{self.server_port} (SNI: {SNI_HOST})")
            self.reader, self.writer = await asyncio.open_connection(
                self.server_host, self.server_port, ssl=self.ssl_ctx, server_hostname=SNI_HOST
            )
            logger.info("[+] TLS handshake completed")

            # 🔑 1. Сначала читаем соль ОТ СЕРВЕРА (16 байт)
            # Сервер генерирует соль и отправляет её клиенту ПЕРЕД аутентификацией
            salt = await self._read_exact(16)
            logger.info("[+] Received salt from server")

            # 🔐 2. Аутентификация: sha224(password) + \r\n (как в сервере)
            pwd_hash = hashlib.sha224(PASSWORD.encode()).hexdigest().encode() + b'\r\n'
            self.writer.write(pwd_hash)
            await self.writer.drain()
            logger.info("[+] Authentication sent")

            # 📬 3. Получаем назначенный IP (16 байт)
            ip_bytes = await self._read_exact(16)
            assigned_ip = ip_bytes.decode().strip()
            logger.info(f"[+] Auth OK. Assigned IP: {assigned_ip}")

            # 🔑 4. Инициализируем крипто с ПОЛУЧЕННОЙ солью
            self.crypto = CryptoEngine(PASSWORD, salt)
            self.wintun.create_adapter(TUN_NAME)
            self.running = True
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def run(self):
        if not await self.connect(): return
        logger.info("[+] Client started. Press Ctrl+C to stop.")
        await asyncio.gather(
            self._tun_to_network(),
            self._network_to_tun(),
            return_exceptions=True
        )

    async def _tun_to_network(self):
        while self.running:
            pkt = await self.wintun.read()
            if pkt and len(pkt) >= 20:
                try:
                    enc = self.crypto.encrypt(pkt)
                    self.writer.write(struct.pack('!H', len(enc)) + enc)
                    await self.writer.drain()
                except Exception: break
            await asyncio.sleep(0.001)

    async def _network_to_tun(self):
        while self.running:
            try:
                hdr = await self._read_exact(2)
                length = struct.unpack('!H', hdr)[0]
                if not (0 < length < 65535): continue
                enc = await self._read_exact(length)
                pkt = self.crypto.decrypt(enc)
                if pkt: await self.wintun.write(pkt)
            except Exception: break

    async def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = await self.reader.read(n - len(data))
            if not chunk: raise ConnectionError("Stream closed (Server dropped connection)")
            data += chunk
        return data

    def stop(self):
        self.running = False
        self.wintun.close()
        if self.writer: self.writer.close()

if __name__ == "__main__":
    if not ctypes.windll.shell32.IsUserAnAdmin():
        logger.error("[!] Run as Administrator")
        sys.exit(1)
    client = AsyncVPNClient()
    try: asyncio.run(client.run())
    except KeyboardInterrupt: pass
    finally: client.stop()