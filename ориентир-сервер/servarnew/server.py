# server.py
#!/usr/bin/env python3
"""
SECURE ASYNC VPN SERVER с мониторингом, Reality и HARDENED WINTUN
"""
import signal
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
from reality_engine import RealityEngine # ✅ Импортируем новый файл

# ✅ ИСПРАВЛЕНО: logger = logging.getLogger(__name__)
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
        d.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        d.WintunCreateAdapter.restype = ctypes.c_void_p
        d.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        d.WintunCloseAdapter.restype = None
        d.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        d.WintunAllocateSendPacket.restype = ctypes.c_void_p
        d.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.WintunSendPacket.restype = None
        # ✅ ПРАВИЛЬНАЯ СИГНАТУРА ДЛЯ WintunReceivePacket - КРИТИЧЕСКИ ВАЖНО
        d.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
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
        pkt_ptr = await asyncio.to_thread(
            self.dll.WintunReceivePacket, self.handle, ctypes.byref(size)
        )
        if pkt_ptr and size.value > 0:
            data = ctypes.string_at(pkt_ptr, size.value)
            self.dll.WintunReleaseReceivePacket(self.handle, pkt_ptr)
            return data
        await asyncio.sleep(0.001) # Prevent busy waiting
        return b''

    async def write(self, data: bytes):
        if not data: return
        pkt_ptr = self.dll.WintunAllocateSendPacket(self.handle, len(data))
        if pkt_ptr:
            ctypes.memmove(pkt_ptr, data, len(data))
            self.dll.WintSendPacket(self.handle, pkt_ptr)

    def close(self):
        if self.handle:
            self.dll.WintunCloseAdapter(self.handle)
            self.handle = None

class ClientSession:
    def __init__(self, addr, crypto, writer, reality_engine=None):
        self.addr = addr
        self.crypto = crypto
        self.writer = writer
        self.reality_engine = reality_engine # Optional for Reality mode
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
        # Initialize Reality Engine for the server
        fixed_private_key_hex = "08279445bcb4d3738c5136162436932bdf3c0006cabea88b2293a9a6160a9c71"  # Подставьте сюда сгенерированный hex приватного ключа
        fixed_short_id_hex = "02a644ff08dd1e5b"  # Подставьте сюда сгенерированный hex short_id
        self.reality_engine = RealityEngine(
            private_key_bytes=bytes.fromhex(fixed_private_key_hex),
            short_id=bytes.fromhex(fixed_short_id_hex)
        )
        self.running = True
        self.start_time = time.time()
        self.dashboard = MetricsDashboard(self)
        # Добавляем атрибуты для хранения задач
        self.tun_task = None
        self.dashboard_task = None
        self.server = None # Добавляем атрибут для сервера

    def _build_ssl_context(self):
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(CERTFILE, KEYFILE)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        # More robust cipher list
        ctx.set_ciphers('ECDHE+AESGCM+ECDH+AESGCM:DHE+AESGCM+ECDH+AESGCM:!aNULL:!MD5:!DSS')
        # Use ALPN to negotiate HTTP/1.1, mimicking common web traffic
        ctx.set_alpn_protocols(['http/1.1'])
        # Optional: Set up OCSP stapling if available
        # ctx.options |= ssl.OP_NO_TICKET # For better DPI resistance
        return ctx

    async def start(self):
        # Создаем серверный объект
        self.server = await asyncio.start_server(self._handle_client, HOST, PORT, ssl=self.ssl_ctx)
        logger.info(f"[*] Secure VPN Server listening on {HOST}:{PORT} ")

        # Создаем задачи
        self.tun_task = asyncio.create_task(self._tun_router())
        self.dashboard_task = asyncio.create_task(self.dashboard.run())

        try:
            # Ожидаем завершения сервера (например, по сигналу остановки)
            await self.server.serve_forever()
        except asyncio.CancelledError:
            logger.info("[!] Server shutdown initiated... ")
        finally:
            # Отменяем все связанные задачи
            logger.info("[!] Cancelling tasks...")
            for task in [self.tun_task, self.dashboard_task]:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task  # Ждем завершения задачи после отмены
                    except asyncio.CancelledError:
                        logger.debug(f"Task {task} was cancelled successfully.")
                    except Exception as e:
                        logger.error(f"Error during task cancellation: {e}")

            # Теперь можно безопасно закрыть ресурсы
            if self.server:
                self.server.close()
                await self.server.wait_closed()
            self.wintun.close()  # Теперь handle закрыт только после завершения всех задач
            logger.info("[!] Server stopped. ")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        client_ip = None
        session_crypto = None
        try:
            # ✅ ПЕРВЫМ ДЕЛОМ ВЫПОЛНЯЕМ HANDSHAKE REALITY
            success = await self.reality_engine.server_handshake(reader, writer)
            if not success:
                logger.warning(f"[!] Reality handshake failed for {addr}")
                writer.close()
                return

            # После успешного handshake Reality, переходим к аутентификации Trojan
            salt = await self._read_exact(reader, 16)
            session_crypto = SessionCrypto(PASSWORD, salt)

            auth = await self._read_exact(reader, 58) # 56 hex chars + \r\n
            expected = hashlib.sha256(PASSWORD.encode() + salt).hexdigest().encode() + b'\r\n'

            if auth != expected:
                logger.warning(f"[!] Invalid password from {addr}")
                writer.close()
                return

            async with self.lock:
                if len(self.clients) >= MAX_CLIENTS:
                    logger.warning(f"[!] Max clients reached, rejecting {addr}")
                    writer.close()
                    return
                client_ip = f"10.8.0.{self.next_ip}"
                self.next_ip = (self.next_ip % 254) + 2
                session = ClientSession(addr, session_crypto, writer, self.reality_engine)
                self.clients[client_ip] = session
                writer.write(client_ip.encode().ljust(16))
                await writer.drain()

            logger.info(f"[+] Client {addr} → {client_ip}")

            while self.running:
                # Read encrypted packet length
                hdr = await self._read_exact(reader, 2)
                length = struct.unpack('!H', hdr)[0]
                if not (0 < length < 65535):
                    logger.warning(f"[!] Invalid packet length from {client_ip}: {length}")
                    break

                enc = await self._read_exact(reader, length)
                pkt = session_crypto.decrypt(enc)

                if pkt and len(pkt) >= 20:
                    await self.wintun.write(pkt)
                    async with self.lock:
                        self.clients[client_ip].bytes_in += len(pkt) + 14
                        self.clients[client_ip].pkts_in += 1
                        self.clients[client_ip].last_activity = time.time()

        except ConnectionResetError:
            logger.debug(f"Client {client_ip or addr} reset connection.")
        except ConnectionError as e:
            logger.debug(f"Client {client_ip or addr} connection error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error handling client {client_ip or addr}: {e}")
        finally:
            async with self.lock:
                if client_ip in self.clients:
                    del self.clients[client_ip]
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug(f"Error closing writer for {client_ip or addr}: {e}")
            logger.info(f"[-] Client {addr} disconnected.")

    async def _tun_router(self):
        logger.info("[*] TUN → Clients router started ")
        while self.running: # Проверяем условие остановки
            try:
                pkt = await self.wintun.read()
                if not pkt or len(pkt) < 20: continue
                try:
                    dest_ip = ".".join(str(b) for b in pkt[16:20]) # Extract destination IP
                    async with self.lock:
                        client_session = self.clients.get(dest_ip)
                        if client_session and time.time() - client_session.last_activity < IDLE_TIMEOUT:
                            # Encrypt packet using the client's session crypto
                            enc_pkt = client_session.crypto.encrypt(pkt)
                            # Send length-prefixed packet over the wire
                            client_session.writer.write(struct.pack('!H', len(enc_pkt)) + enc_pkt)
                            await client_session.writer.drain ()
                            client_session.bytes_out += len(enc_pkt) + 2
                            client_session.pkts_out += 1
                except Exception as e:
                    logger.error(f"Error routing TUN packet: {e} ")
                await asyncio.sleep(0.001) # Prevent busy waiting
            except asyncio.CancelledError:
                logger.info("[*] TUN router task was cancelled.")
                break # Выходим из цикла при отмене задачи
            except Exception as e:
                logger.error(f"Unexpected error in TUN router: {e}") # Ловим другие исключения в цикле
                break # Опционально: выйти из цикла при любой ошибке

    @staticmethod
    async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = await reader.read(n - len(data))
            if not chunk:
                raise ConnectionError("EOF")
            data += chunk
        return data

def check_admin():
    return ctypes.windll.shell32.IsUserAnAdmin()

def gen_cert():
    if os.path.exists(CERTFILE) and os.path.exists(KEYFILE): return
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = x509.CertificateBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    ).issuer_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=365)
    ).sign(key, hashes.SHA256())

    with open(KEYFILE, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    with open(CERTFILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

def main(): # Добавим функцию main
    if not check_admin():
        logger.error("[!] Run as Administrator")
        sys.exit(1)
    gen_cert()
    srv = AsyncVPNServer()

    async def run_server():
        try:
            await srv.start()
        except KeyboardInterrupt:
            logger.info("[!] Shutdown signal received (KeyboardInterrupt)...")
            # srv.running = False # Уже управляется через отмену задач
            # srv.server.close() # Уже делается в finally блоке start()
            pass # asyncio.run автоматически завершит выполнение после выхода из async-функции

    try:
        # Регистрируем обработчик сигнала SIGTERM (или SIGINT) для graceful shutdown
        # На Windows сигналы могут работать иначе, но asyncio.run обычно обрабатывает KeyboardInterrupt
        # Для полной совместимости можно использовать loop.add_signal_handler, но для простоты:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("[!] Main thread interrupted.")


if __name__ == "__main__":
    main() # Вызываем main вместо прямого кода
