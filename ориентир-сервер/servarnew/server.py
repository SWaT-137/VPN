#!/usr/bin/env python3
"""
SECURE ASYNC VPN SERVER с мониторингом и HARDENED WINTUN
"""
import asyncio
import ssl
import struct
import os
import sys
import ctypes
import hashlib
import logging
import time
from datetime import datetime
from collections import deque
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

HOST, PORT = "0.0.0.0", 443
PASSWORD = "mysecretpassword123"
CERTFILE, KEYFILE = "server.crt", "server.key"
TUN_NAME = "VPNServer"
VPN_IP, VPN_MASK = "10.8.0.1", "255.255.255.0"
MAX_CLIENTS = 100
IDLE_TIMEOUT = 60

class NonceTracker:
    def __init__(self, window_size=1024):
        self.window = deque(maxlen=window_size)
    def is_duplicate(self, nonce: bytes) -> bool:
        if nonce in self.window:
            return True
        self.window.append(nonce)
        return False

class SessionCrypto:
    def __init__(self, password: str, salt: bytes):
        self.salt = salt
        kdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"vpn-session-2026")
        self.key = kdf.derive(password.encode())
        self.cipher = ChaCha20Poly1305(self.key)
        self.nonce_tracker = NonceTracker()
    def encrypt(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self.cipher.encrypt(nonce, data, None)
    def decrypt(self, data: bytes) -> bytes:
        nonce, ct = data[:12], data[12:]
        if self.nonce_tracker.is_duplicate(nonce):
            raise ValueError("Replay attack detected")
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
        # ⚠️ КРИТИЧНО: Правильные сигнатуры для 64-bit Windows
        d.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        d.WintunCreateAdapter.restype = ctypes.c_void_p

        d.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        d.WintunCloseAdapter.restype = None

        d.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        d.WintunAllocateSendPacket.restype = ctypes.c_void_p

        d.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.WintunSendPacket.restype = None

        # ✅ ИСПРАВЛЕНО: WintunReceivePacket принимает ТОЛЬКО 2 аргумента и возвращает указатель
        d.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        d.WintunReceivePacket.restype = ctypes.c_void_p

        d.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.WintunReleaseReceivePacket.restype = None

    def create(self, name, ip, mask):
        self.handle = self.dll.WintunCreateAdapter(name, "Wintun", None)
        if not self.handle:
            raise RuntimeError(f"Failed to create TUN adapter: {name}")
        os.system(f'netsh interface ip set address "{name}" static {ip} {mask} >nul 2>&1')
        logger.info(f"[+] TUN {name} created: {ip}")

    async def read(self) -> bytes:
        size = ctypes.c_uint32(0)
        # WintunReceivePacket НЕ блокирует, сразу возвращает NULL если нет пакетов
        pkt_ptr = await asyncio.to_thread(
            self.dll.WintunReceivePacket, self.handle, ctypes.byref(size)
        )
        if pkt_ptr and size.value > 0:
            data = ctypes.string_at(pkt_ptr, size.value)
            self.dll.WintunReleaseReceivePacket(self.handle, pkt_ptr)
            return data
        # Добавляем микросон чтобы event loop не крутился на 100% CPU
        await asyncio.sleep(0.001)
        return b''

    async def write(self, data: bytes):
        if not data: return
        pkt_ptr = self.dll.WintunAllocateSendPacket(self.handle, len(data))
        if pkt_ptr:
            ctypes.memmove(pkt_ptr, data, len(data))
            self.dll.WintunSendPacket(self.handle, pkt_ptr)

    def close(self):
        if self.handle:
            self.dll.WintunCloseAdapter(self.handle)
            self.handle = None

class ClientSession:
    def __init__(self, addr, crypto, writer):
        self.addr = addr
        self.crypto = crypto
        self.writer = writer
        self.last_activity = time.time()
        self.bytes_in = self.bytes_out = 0
        self.pkts_in = self.pkts_out = 0
        self.connected_at = time.time()

class MetricsDashboard:
    def __init__(self, server):
        self.server = server
    async def run(self):
        while self.server.running:
            await asyncio.sleep(2)
            self._draw()
    def _draw(self):
        total_in = sum(c.bytes_in for c in self.server.clients.values())
        total_out = sum(c.bytes_out for c in self.server.clients.values())
        total_pkts = sum(c.pkts_in + c.pkts_out for c in self.server.clients.values())
        uptime = time.time() - self.server.start_time
        h, m, s = int(uptime)//3600, int(uptime)%3600//60, int(uptime)%60
        sys.stdout.write("\033[H\033[J")
        sys.stdout.write("="*60 + "\n")
        sys.stdout.write(f"🛡️  SECURE VPN SERVER | UPTIME: {h:02}:{m:02}:{s:02}\n")
        sys.stdout.write(f"👥 ACTIVE CLIENTS: {len(self.server.clients)} / {MAX_CLIENTS}\n")
        sys.stdout.write(f"📊 TRAFFIC: ↑ {self._fmt(total_out)} ↓ {self._fmt(total_in)} | 📦 {total_pkts} pkts\n")
        sys.stdout.write("="*60 + "\n")
        sys.stdout.write(f"{'ADDR':<22} {'IP':<15} {'↑/↓ (B)':<20} {'UPTIME':<10}\n")
        sys.stdout.write("-"*60 + "\n")
        for ip, c in self.server.clients.items():
            t_uptime = time.time() - c.connected_at
            sys.stdout.write(f"{c.addr[0]:<22} {ip:<15} {self._fmt(c.bytes_out)}/{self._fmt(c.bytes_in):<12} {int(t_uptime):<10}s\n")
        sys.stdout.flush()
    @staticmethod
    def _fmt(b):
        for u in ['B','KB','MB','GB','TB']:
            if b < 1024.0: return f"{b:.2f} {u}"
            b /= 1024.0
        return f"{b:.2f} PB"

class AsyncVPNServer:
    def __init__(self):
        self.clients = {}
        self.lock = asyncio.Lock()
        self.next_ip = 2
        self.wintun = WintunAsync()
        self.wintun.create(TUN_NAME, VPN_IP, VPN_MASK)
        self.ssl_ctx = self._build_ssl_context()
        self.running = True
        self.start_time = time.time()
        self.dashboard = MetricsDashboard(self)

    def _build_ssl_context(self):
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(CERTFILE, KEYFILE)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:!aNULL:!MD5')
        ctx.set_alpn_protocols(['h2', 'http/1.1'])
        return ctx

    async def start(self):
        server = await asyncio.start_server(self._handle_client, HOST, PORT, ssl=self.ssl_ctx)
        logger.info(f"[*] Secure VPN Server listening on {HOST}:{PORT}")
        try:
            await asyncio.gather(server.serve_forever(), self._tun_router(), self.dashboard.run())
        except asyncio.CancelledError:
            pass
        finally:
            server.close(); await server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        client_ip = None
        try:
            salt = await self._read_exact(reader, 16)
            crypto = SessionCrypto(PASSWORD, salt)

            auth = await self._read_exact(reader, 58)
            expected = hashlib.sha256(PASSWORD.encode() + salt).hexdigest().encode() + b'\r\n'
            if auth != expected:
                writer.close(); return

            async with self.lock:
                if len(self.clients) >= MAX_CLIENTS:
                    writer.close(); return
                client_ip = f"10.8.0.{self.next_ip}"
                self.next_ip = (self.next_ip % 254) + 2
                session = ClientSession(addr, crypto, writer)
                self.clients[client_ip] = session
                writer.write(client_ip.encode().ljust(16)); await writer.drain()

            logger.info(f"[+] Client {addr} → {client_ip}")

            while self.running:
                hdr = await self._read_exact(reader, 2)
                length = struct.unpack('!H', hdr)[0]
                if not (0 < length < 65535): break
                enc = await self._read_exact(reader, length)
                pkt = crypto.decrypt(enc)
                if pkt and len(pkt) >= 20:
                    await self.wintun.write(pkt)
                    session.bytes_in += len(pkt) + 14
                    session.pkts_in += 1
                    session.last_activity = time.time()

        except Exception as e:
            logger.debug(f"Client {client_ip or addr} disconnected: {e}")
        finally:
            async with self.lock:
                if client_ip in self.clients:
                    del self.clients[client_ip]
            try: writer.close()
            except: pass

    async def _tun_router(self):
        logger.info("[*] TUN → Clients router started")
        while self.running:
            pkt = await self.wintun.read()
            if not pkt or len(pkt) < 20: continue
            try:
                dest_ip = ".".join(str(b) for b in pkt[16:20])
                async with self.lock:
                    client = self.clients.get(dest_ip)
                if client and time.time() - client.last_activity < IDLE_TIMEOUT:
                    enc = client.crypto.encrypt(pkt)
                    client.writer.write(struct.pack('!H', len(enc)) + enc)
                    await client.writer.drain()
                    client.bytes_out += len(enc) + 2
                    client.pkts_out += 1
            except Exception:
                pass

    async def _read_exact(self, reader: asyncio.StreamReader, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = await reader.read(n - len(data))
            if not chunk: raise ConnectionError("EOF")
            data += chunk
        return data

def check_admin(): return ctypes.windll.shell32.IsUserAnAdmin()
def gen_cert():
    if os.path.exists(CERTFILE) and os.path.exists(KEYFILE): return
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    import datetime
    key = rsa.generate_private_key(65537, 2048)
    cert = x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])).issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.utcnow()).not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365)).sign(key, hashes.SHA256())
    open(KEYFILE, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    open(CERTFILE, "wb").write(cert.public_bytes(serialization.Encoding.PEM))

if __name__ == "__main__":
    if not check_admin(): logger.error("[!] Run as Administrator"); sys.exit(1)
    gen_cert()
    srv = AsyncVPNServer()
    try: asyncio.run(srv.start())
    except KeyboardInterrupt: srv.running = False