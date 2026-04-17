#!/usr/bin/env python3
"""
VPN Server 2026 - Production Ready
"""
import socket
import ssl
import struct
import threading
import hashlib
import os
import time
import sys
import ctypes
import secrets
import json
import logging
import signal
import ipaddress
import queue
from ctypes import wintypes
from datetime import datetime
from typing import Optional, Dict, Set, List, Tuple
from pathlib import Path
from collections import defaultdict
from anti_dpi_engine import AntiDPIEngine, PerfectForwardSecrecy, SecureNonceManager

HOST = "0.0.0.0"
PORT = 443
ALT_PORTS = [8443, 2053, 2083, 2096, 8080, 9443, 4443]
VPN_NETWORK = "10.8.0.0/24"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"
TUN_NAME = "VPNServer"
CONFIG_DIR = Path("vpn_config")
CONFIG_DIR.mkdir(exist_ok=True)
PASSWORD_FILE = CONFIG_DIR / "password.hash"
CERT_FILE = CONFIG_DIR / "server.crt"
KEY_FILE = CONFIG_DIR / "server.key"
CLIENTS_FILE = CONFIG_DIR / "clients.json"

class SafeLogger:
    def __init__(self):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                            handlers=[logging.FileHandler('vpn_server.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)])
        self.logger = logging.getLogger(__name__)
    def _log(self, level, msg):
        try: getattr(self.logger, level)(msg)
        except UnicodeEncodeError: getattr(self.logger, level)(msg.encode('ascii', 'ignore').decode('ascii'))
    def info(self, msg): self._log('info', msg)
    def warning(self, msg): self._log('warning', msg)
    def error(self, msg): self._log('error', msg)
    def debug(self, msg): self._log('debug', msg)

logger = SafeLogger()

class SecureAuthManager:
    def __init__(self):
        self.password_hash: Optional[str] = None
        self.salt: Optional[bytes] = None
        self.plain_password: Optional[str] = None
        self.nonce_manager = SecureNonceManager()
        self.failed_attempts: Dict[str, List[float]] = defaultdict(list)
        self.lock = threading.RLock()
        self._load_or_create_credentials()

    def _load_or_create_credentials(self):
        try:
            if PASSWORD_FILE.exists():
                with open(PASSWORD_FILE, 'r') as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
                        self.password_hash = data.get('password_hash')
                        self.salt = bytes.fromhex(data.get('salt', ''))
                        self.plain_password = data.get('plain_password', 'mysecretpassword123')
                        logger.info("[+] Credentials loaded from disk")
                    else: raise ValueError("Empty file")
            else: raise FileNotFoundError
        except Exception as e:
            logger.warning(f"[!] Failed to load credentials: {e}")
            logger.info("[*] Creating new credentials...")
            if PASSWORD_FILE.exists(): PASSWORD_FILE.unlink()
            password = self._prompt_password()
            self.plain_password = password
            self.salt = secrets.token_bytes(32)
            self.password_hash = hashlib.sha256(password.encode() + self.salt).hexdigest()
            with open(PASSWORD_FILE, 'w') as f:
                json.dump({'password_hash': self.password_hash, 'salt': self.salt.hex(), 'plain_password': password}, f)
            os.chmod(PASSWORD_FILE, 0o600)
            logger.info("[+] New credentials created")

    def _prompt_password(self) -> str:
        import getpass
        print("\n" + "="*50 + "\nVPN SERVER INITIAL SETUP\n" + "="*50)
        while True:
            pwd = getpass.getpass("Enter password (min 8 chars): ")
            if pwd == getpass.getpass("Confirm: ") and len(pwd) >= 8: return pwd
            print("[!] Mismatch or too short")

    def check_rate_limit(self, ip: str) -> bool:
        with self.lock:
            now = time.time()
            self.failed_attempts[ip] = [t for t in self.failed_attempts[ip] if now - t < 300]
            return len(self.failed_attempts[ip]) < 5

    def record_failure(self, ip: str):
        with self.lock: self.failed_attempts[ip].append(time.time())

    def authenticate(self, sock: socket.socket, client_ip: str) -> Tuple[bool, Optional[bytes]]:
        if not self.check_rate_limit(client_ip): return False, None
        original_timeout = sock.gettimeout()
        try:
            sock.settimeout(10)
            auth_data = self._recv_exact(sock, 64)
            if len(auth_data) < 64:
                logger.warning(f"Incomplete auth from {client_ip}")
                self.record_failure(client_ip)
                return False, None
            if auth_data[62:64] != b'\r\n':
                logger.warning(f"Invalid auth format from {client_ip}")
                self.record_failure(client_ip)
                return False, None
            received_hash = auth_data[:56].decode('ascii', errors='ignore')
            client_nonce = auth_data[56:62]
            if not self.nonce_manager.is_valid(client_nonce):
                logger.warning(f"Replay attack from {client_ip}")
                self.record_failure(client_ip)
                return False, None
            expected = hashlib.sha224((self.plain_password or "mysecretpassword123").encode()).hexdigest()
            if received_hash == expected:
                logger.info(f"[+] Client {client_ip} authenticated")
                return True, client_nonce
            logger.warning(f"[!] Auth failed for {client_ip}")
            self.record_failure(client_ip)
            return False, None
        except socket.timeout:
            logger.warning(f"Auth timeout from {client_ip}")
            self.record_failure(client_ip)
            return False, None
        except Exception as e:
            logger.error(f"Auth error from {client_ip}: {e}")
            self.record_failure(client_ip)
            return False, None
        finally:
            try: sock.settimeout(original_timeout)
            except: pass

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
        """Безопасное получение байт — только 2 параметра"""
        data = b''
        original_timeout = sock.gettimeout()
        try:
            sock.settimeout(10.0)  # Фиксированный таймаут
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    break
                data += chunk
        except (socket.timeout, ConnectionResetError, OSError):
            pass  # Возвращаем то, что успели получить
        finally:
            try:
                sock.settimeout(original_timeout)
            except:
                pass
        return data
class ClientManager:
    def __init__(self):
        self.clients: Dict[str, Dict] = {}
        self.lock = threading.RLock()
        self.next_ip = 2
        self.used_ips: Set[str] = set()
        self._load_state()
    def _load_state(self):
        if CLIENTS_FILE.exists():
            try:
                with open(CLIENTS_FILE, 'r') as f:
                    data = json.load(f)
                    self.used_ips = set(data.get('used_ips', []))
                    self.next_ip = max(data.get('next_ip', 2), 2)
            except: pass
    def _save_state(self):
        try:
            tmp = CLIENTS_FILE.with_suffix('.tmp')
            with open(tmp, 'w') as f: json.dump({'used_ips': list(self.used_ips), 'next_ip': self.next_ip}, f)
            tmp.replace(CLIENTS_FILE)
        except: pass
    def allocate_ip(self) -> Optional[str]:
        with self.lock:
            for _ in range(254):
                ip = f"10.8.0.{self.next_ip}"
                self.next_ip = (self.next_ip % 253) + 2
                if ip not in self.used_ips:
                    self.used_ips.add(ip)
                    self._save_state()
                    return ip
            return None
    def release_ip(self, ip: str):
        with self.lock:
            self.used_ips.discard(ip)
            self._save_state()
    def add_client(self, ip: str, handler, sock):
        with self.lock:
            self.clients[ip] = {'handler': handler, 'socket': sock, 'connected_at': datetime.now(), 'bytes_sent': 0, 'bytes_received': 0}
            logger.info(f"[+] Client {ip} registered (total: {len(self.clients)})")
    def remove_client(self, ip: str):
        with self.lock:
            if ip in self.clients:
                c = self.clients[ip]
                uptime = (datetime.now() - c['connected_at']).total_seconds()
                logger.info(f"[-] Client {ip} disconnected. Uptime: {int(uptime)}s")
                del self.clients[ip]
                self.release_ip(ip)
    def get_client(self, ip: str):
        with self.lock: return self.clients.get(ip)
    def update_stats(self, ip: str, sent=0, received=0):
        with self.lock:
            if ip in self.clients:
                self.clients[ip]['bytes_sent'] += sent
                self.clients[ip]['bytes_received'] += received
    def get_stats(self):
        with self.lock:
            return {'total_clients': len(self.clients), 'total_bytes_sent': sum(c['bytes_sent'] for c in self.clients.values()), 'total_bytes_received': sum(c['bytes_received'] for c in self.clients.values())}

class WintunWrapper:
    def __init__(self, dll_path="wintun.dll"):
        paths = [os.path.join(os.path.dirname(__file__), dll_path), os.path.join(os.getcwd(), dll_path), dll_path]
        path = next((p for p in paths if os.path.exists(p)), None)
        if not path: raise FileNotFoundError("wintun.dll not found")
        logger.info(f"Loading wintun.dll from: {path}")
        self.dll = ctypes.WinDLL(path)
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [wintypes.LPCWSTR]*3
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [wintypes.LPCWSTR]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        self.WintunCloseAdapter.restype = None
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket
        self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        self.WintunSendPacket = self.dll.WintunSendPacket
        self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunSendPacket.restype = None
        self.WintunReceivePacket = self.dll.WintunReceivePacket
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        self.WintunReceivePacket.restype = ctypes.c_uint32
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunReleaseReceivePacket.restype = None
        try:
            self.WintunGetRunningDriverVersion = self.dll.WintunGetRunningDriverVersion
            self.WintunGetRunningDriverVersion.restype = ctypes.c_uint32
        except: self.WintunGetRunningDriverVersion = None

class TUNInterface:
    def __init__(self, name, ip, netmask):
        self.name, self.ip, self.netmask = name, ip, netmask
        self.handle = None
        self.running = True
        self.wintun = WintunWrapper()
        self.lock = threading.RLock()
        if self.wintun.WintunGetRunningDriverVersion:
            v = self.wintun.WintunGetRunningDriverVersion()
            logger.info(f"Wintun driver version: {v>>16}.{v&0xFFFF}")
        self.handle = self.wintun.WintunOpenAdapter(name)
        if not self.handle:
            logger.info(f"Creating adapter: {name}")
            self.handle = self.wintun.WintunCreateAdapter(name, "Wintun", None)
            if not self.handle: raise Exception("Failed to create adapter")
        logger.info(f"[+] Adapter '{name}' ready")
        self._configure_ip()
    def _configure_ip(self):
        try:
            import subprocess
            subprocess.run(f'netsh interface ip set address "{self.name}" static {self.ip} {self.netmask}', shell=True, capture_output=True)
            subprocess.run(f'netsh interface set interface "{self.name}" admin=enabled', shell=True, capture_output=True)
            logger.info(f"[+] IP configured: {self.ip}")
        except Exception as e: logger.error(f"IP config error: {e}")
    def read(self, timeout=0.1):
        if not self.handle or not self.running: return None
        with self.lock:
            try:
                ptr, size = ctypes.c_void_p(), ctypes.c_uint32(0)
                if self.wintun.WintunReceivePacket(self.handle, ctypes.byref(ptr), ctypes.byref(size)) == 0 and ptr and size.value > 0:
                    data = ctypes.string_at(ptr, size.value)
                    self.wintun.WintunReleaseReceivePacket(self.handle, ptr)
                    return data
            except: pass
        return None
    def write(self, packet):
        if not self.handle or not self.running or not packet: return False
        with self.lock:
            try:
                ptr = self.wintun.WintunAllocateSendPacket(self.handle, len(packet))
                if ptr:
                    ctypes.memmove(ptr, packet, len(packet))
                    self.wintun.WintunSendPacket(self.handle, ptr)
                    return True
            except: pass
        return False
    def close(self):
        self.running = False
        if self.handle:
            try: self.wintun.WintunCloseAdapter(self.handle)
            except: pass

def generate_ssl_certificate():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    if CERT_FILE.exists() and KEY_FILE.exists():
        logger.info("[*] SSL cert exists"); return
    logger.info("[*] Generating SSL cert...")
    key = rsa.generate_private_key(65537, 2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vpn.local")])
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc)).not_valid_after(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=3650)).add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]), False).sign(key, hashes.SHA256())
    with open(KEY_FILE, "wb") as f: f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())); os.chmod(KEY_FILE, 0o600)
    with open(CERT_FILE, "wb") as f: f.write(cert.public_bytes(serialization.Encoding.PEM))
    logger.info("[+] SSL cert created")

class ClientHandler:
    def __init__(self, sock, addr, server):
        self.sock, self.addr, self.server = sock, addr, server
        self.client_ip = addr[0]
        self.assigned_ip = None
        self.running = True
        self.anti_dpi = None
        self.last_activity = time.time()
        self.write_lock = threading.RLock()
        self.bytes_sent = self.bytes_received = 0

    def run(self):
        try:
            self.sock.settimeout(30.0)
            ok, _ = self.server.auth_manager.authenticate(self.sock, self.client_ip)
            if not ok:
                logger.warning(f"Auth failed from {self.addr}"); return
            self.anti_dpi = AntiDPIEngine(is_server=True)
            try: self.anti_dpi.perform_handshake(self.sock, self.client_ip)
            except Exception as e: logger.error(f"PFS failed: {e}"); return
            self.assigned_ip = self.server.client_manager.allocate_ip()
            if not self.assigned_ip: logger.error("No IP"); return
            self.sock.sendall(self.anti_dpi.encrypt_packet(self.assigned_ip.encode()))
            logger.info(f"[+] Client {self.addr} assigned IP: {self.assigned_ip}")
            self.server.client_manager.add_client(self.assigned_ip, self, self.sock)
            threading.Thread(target=self._send_worker, daemon=True).start()
            while self.running and self.server.running:
                try:
                    header = self._recv_exact(14)
                    if len(header) == 0: break
                    if len(header) < 14: continue
                    length = struct.unpack('!H', header[12:14])[0]
                    if 0 < length < 65535:
                        data = self._recv_exact(length)
                        if len(data) == length:
                            packet = self.anti_dpi.decrypt_packet(header + data)
                            if packet:
                                if len(packet) == 0:
                                    self.last_activity = time.time()
                                    continue
                                self.server.tun.write(packet)
                                self.bytes_received += len(header) + length
                                self.last_activity = time.time()
                except socket.timeout: continue
                except Exception as e:
                    if self.running: logger.debug(f"Recv error: {e}"); break
        except Exception as e: logger.error(f"Handler error: {e}")
        finally: self.cleanup()

    def _send_worker(self):
        q = queue.Queue(maxsize=1000)
        with self.server.send_queues_lock: self.server.send_queues[self.assigned_ip] = q
        try:
            while self.running and self.server.running:
                try:
                    pkt = q.get(timeout=0.5)
                    enc = self.anti_dpi.encrypt_packet(pkt)
                    with self.write_lock: self.sock.sendall(enc); self.bytes_sent += len(enc)
                except queue.Empty: continue
                except: break
        finally:
            with self.server.send_queues_lock: self.server.send_queues.pop(self.assigned_ip, None)

    def _recv_exact(self, n):
        data = b''
        while len(data) < n and self.running:
            try:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    return b''
                data += chunk
            except socket.timeout:
                if not data:
                    raise
                return data
            except (ConnectionResetError, OSError):
                return b''
        return data

    def send_packet(self, pkt):
        if not self.running: return False
        with self.server.send_queues_lock:
            q = self.server.send_queues.get(self.assigned_ip)
            if q:
                try: q.put_nowait(pkt); return True
                except: pass
        return False

    def cleanup(self):
        self.running = False
        if self.assigned_ip:
            self.server.client_manager.update_stats(self.assigned_ip, self.bytes_sent, self.bytes_received)
            self.server.client_manager.remove_client(self.assigned_ip)
        try: self.sock.close()
        except: pass

class VPNServer:
    def __init__(self):
        self.running = True
        self.start_time = time.time()
        self.auth_manager = SecureAuthManager()
        self.client_manager = ClientManager()
        self.send_queues = {}
        self.send_queues_lock = threading.RLock()
        self.ports = [PORT] + ALT_PORTS
        self.sockets = []
        generate_ssl_certificate()
        logger.info("[1/4] TUN...")
        self.tun = TUNInterface(TUN_NAME, VPN_SERVER_IP, VPN_NETMASK)
        logger.info("[2/4] TUN reader...")
        threading.Thread(target=self._tun_reader, daemon=True).start()
        logger.info("[3/4] Listeners...")
        self._start_listeners()
        logger.info("[4/4] Stats...")
        threading.Thread(target=self._stats_monitor, daemon=True).start()
        logger.info(f"\n{'='*60}\nVPN SERVER 2026\nPorts: {self.ports}\n{'='*60}")

    def _start_listeners(self):
        for port in self.ports:
            threading.Thread(target=self._listen, args=(port,), daemon=True).start()

    def _listen(self, port):
        try:
            anti_dpi = AntiDPIEngine(is_server=True)
            context = anti_dpi.tls_impersonator.create_ssl_context(is_server=True)
            context.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((HOST, port))
            sock.listen(100)
            self.sockets.append(sock)
            ssl_sock = context.wrap_socket(sock, server_side=True)
            logger.info(f"[*] Listening on {port}")
            while self.running:
                try:
                    ssl_sock.settimeout(1.0)
                    c, a = ssl_sock.accept()
                    logger.info(f"[*] Connection from {a}")
                    threading.Thread(target=ClientHandler(c, a, self).run, daemon=True).start()
                except socket.timeout: continue
                except: continue
        except Exception as e: logger.error(f"Listen error {port}: {e}")

    def _tun_reader(self):
        while self.running:
            pkt = self.tun.read(0.1)
            if pkt and len(pkt) >= 20:
                try:
                    ip = socket.inet_ntoa(pkt[16:20])
                    c = self.client_manager.get_client(ip)
                    if c: c['handler'].send_packet(pkt)
                except: pass

    def _stats_monitor(self):
        while self.running:
            time.sleep(60)
            s = self.client_manager.get_stats()
            logger.info(f"[STATS] Uptime: {int(time.time()-self.start_time)//60}m, Clients: {s['total_clients']}")

    def stop(self):
        logger.info("[!] Stopping...")
        self.running = False
        for s in self.sockets:
            try: s.close()
            except: pass
        self.tun.close()
        logger.info("[+] Stopped")

def check_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return False

if __name__ == "__main__":
    if not check_admin(): print("[!] Run as Admin"); sys.exit(1)
    if not os.path.exists("wintun.dll"): print("[!] wintun.dll missing"); sys.exit(1)
    server = None
    try:
        server = VPNServer()
        signal.signal(signal.SIGINT, lambda s,f: server.stop())
        while server.running: time.sleep(1)
    except KeyboardInterrupt: pass
    finally:
        if server: server.stop()