#!/usr/bin/env python3
"""
VPN Client 2026 - Production Ready
Полная интеграция Anti-DPI с PFS и Certificate Pinning
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
import queue
from datetime import datetime
from typing import Optional, Dict, Tuple
from pathlib import Path

# Импорт модулей
from anti_dpi_engine import AntiDPIEngine
from cryptography import x509
from cryptography.hazmat.primitives import serialization

# ============== КОНФИГУРАЦИЯ ==============
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 443
TUN_ADAPTER_NAME = "VPNClient"
VPN_SERVER_IP = "10.8.0.1"
CONFIG_DIR = Path("vpn_client_config")
PASSWORD_FILE = CONFIG_DIR / "client_password.txt"
SERVER_CERT_PIN_FILE = CONFIG_DIR / "server_cert_pin.txt"

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============== УПРАВЛЕНИЕ УЧЕТНЫМИ ДАННЫМИ ==============
class CredentialManager:
    def __init__(self):
        self.password: Optional[str] = None
        self.server_cert_pin: Optional[str] = None
        self._load_credentials()

    def _load_credentials(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        if PASSWORD_FILE.exists():
            with open(PASSWORD_FILE, 'r') as f:
                self.password = f.read().strip()
        else:
            self.password = self._prompt_password()
            with open(PASSWORD_FILE, 'w') as f:
                f.write(self.password)
            os.chmod(PASSWORD_FILE, 0o600)

        if SERVER_CERT_PIN_FILE.exists():
            with open(SERVER_CERT_PIN_FILE, 'r') as f:
                raw_pin = f.read().strip()
                if raw_pin:
                    self.server_cert_pin = raw_pin
                    logger.info("[+] Certificate pin loaded")
                else:
                    self.server_cert_pin = None
        else:
            self.server_cert_pin = None

    def _prompt_password(self) -> str:
        print("\n" + "="*60)
        print("  FIRST RUN - ENTER NEW PASSWORD")
        print("="*60)
        while True:
            pwd = input("Enter new VPN password: ")
            confirm = input("Confirm password: ")
            if pwd == confirm and len(pwd) >= 8:
                return pwd
            else:
                print("[!] Passwords don't match or too short (min 8 chars). Try again.")

    def verify_certificate(self, cert_der: bytes) -> bool:
        try:
            cert_obj = x509.load_der_x509_certificate(cert_der)
            cert_pem = cert_obj.public_bytes(encoding=serialization.Encoding.PEM).decode('utf-8')
            cert_hash = hashlib.sha256(cert_pem.encode('utf-8')).hexdigest()
            if self.server_cert_pin:
                if cert_hash == self.server_cert_pin:
                    logger.info("[+] Certificate pin verified")
                    return True
                else:
                    logger.error("[!] Certificate pin mismatch!")
                    return False
            else:
                logger.warning("[!] No certificate pin configured (first connection)")
                self.set_certificate_pin_by_hash(cert_hash)
                logger.info(f"[+] Certificate pin saved: {cert_hash[:16]}...")
                return True
        except Exception as e:
            logger.error(f"[!] Certificate verification error: {e}")
            return False

    def set_certificate_pin_by_hash(self, cert_hash: str):
        with open(SERVER_CERT_PIN_FILE, 'w') as f:
            f.write(cert_hash)
        os.chmod(SERVER_CERT_PIN_FILE, 0o600)
        self.server_cert_pin = cert_hash

# ============== WINTUN ОБЕРТКА ==============
class WintunWrapper:
    def __init__(self, dll_path: str = "wintun.dll"):
        search_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), dll_path),
            os.path.join(os.getcwd(), dll_path),
            dll_path
        ]
        found_path = None
        for path in search_paths:
            if os.path.exists(path):
                found_path = path
                break
        if not found_path:
            raise FileNotFoundError(f"wintun.dll not found")
        logger.info(f"Loading wintun.dll from: {found_path}")
        self.dll = ctypes.WinDLL(found_path)
        
        # Обязательные функции
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [ctypes.c_wchar_p]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        self.WintunCloseAdapter.restype = None
        
        self.WintunStartSession = self.dll.WintunStartSession
        self.WintunStartSession.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        self.WintunStartSession.restype = ctypes.c_void_p
        
        self.WintunEndSession = self.dll.WintunEndSession
        self.WintunEndSession.argtypes = [ctypes.c_void_p]
        self.WintunEndSession.restype = None
        
        self.WintunGetReadWaitEvent = self.dll.WintunGetReadWaitEvent
        self.WintunGetReadWaitEvent.argtypes = [ctypes.c_void_p]
        self.WintunGetReadWaitEvent.restype = ctypes.c_void_p
        
        self.WintunReceivePacket = self.dll.WintunReceivePacket
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        self.WintunReceivePacket.restype = ctypes.c_uint32
        
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunReleaseReceivePacket.restype = None
        
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket
        self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        
        self.WintunSendPacket = self.dll.WintunSendPacket
        self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunSendPacket.restype = None
        
        # Опциональные функции (с безопасной загрузкой)
        try:
            self.WintunFreeAdapter = self.dll.WintunFreeAdapter
            self.WintunFreeAdapter.argtypes = [ctypes.c_void_p]
            self.WintunFreeAdapter.restype = None
        except AttributeError:
            logger.warning("[*] WintunFreeAdapter not available in this DLL version")
            self.WintunFreeAdapter = None
        
        try:
            self.WintunDeleteAdapter = self.dll.WintunDeleteAdapter
            self.WintunDeleteAdapter.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.POINTER(ctypes.c_wchar_p)]
            self.WintunDeleteAdapter.restype = ctypes.c_bool
        except AttributeError:
            logger.warning("[*] WintunDeleteAdapter not available in this DLL version")
            self.WintunDeleteAdapter = None

    def create_adapter(self, name: str) -> ctypes.c_void_p:
        handle = self.WintunCreateAdapter(name, "VPN", None)
        if not handle:
            raise RuntimeError(f"Failed to create Wintun adapter: {ctypes.GetLastError()}")
        return handle

    def open_adapter(self, name: str) -> ctypes.c_void_p:
        handle = self.WintunOpenAdapter(name)
        if not handle:
            raise RuntimeError(f"Failed to open Wintun adapter: {ctypes.GetLastError()}")
        return handle

    def start_session(self, handle: ctypes.c_void_p, capacity: int) -> ctypes.c_void_p:
        session = self.WintunStartSession(handle, capacity, None)
        if not session:
            raise RuntimeError(f"Failed to start session: {ctypes.GetLastError()}")
        return session

    def close_adapter(self, handle: ctypes.c_void_p):
        if handle:
            self.WintunCloseAdapter(handle)

    def free_adapter(self, handle: ctypes.c_void_p):
        if handle and self.WintunFreeAdapter:
            self.WintunFreeAdapter(handle)

class TunInterface:
    def __init__(self, name: str = TUN_ADAPTER_NAME):
        self.name = name
        self.wintun = WintunWrapper()
        self.adapter_handle = None
        self.session_handle = None
        self.running = False
        self.read_event = None

    def create(self):
        logger.info(f"[1/3] Creating TUN adapter: {self.name}...")
        try:
            self.adapter_handle = self.wintun.create_adapter(self.name)
            logger.info(f"[+] Virtual adapter '{self.name}' created")
        except Exception:
            logger.info(f"[-] Adapter might exist, trying to open...")
            self.adapter_handle = self.wintun.open_adapter(self.name)
            logger.info(f"[+] Virtual adapter '{self.name}' opened")
        
        logger.info(f"[2/3] Starting TUN session...")
        self.session_handle = self.wintun.start_session(self.adapter_handle, 0x400000)
        self.read_event = self.wintun.WintunGetReadWaitEvent(self.session_handle)
        self.running = True
        logger.info(f"[+] TUN session started")

    def set_ip(self, ip: str):
        logger.info(f"[3/3] Configuring IP address...")
        try:
            import subprocess
            subprocess.run(f'netsh interface ipv4 delete address "{self.name}"', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 add address "{self.name}" {ip} 255.255.255.0', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 add route 10.8.0.0/24 "{self.name}" metric=1', shell=True, capture_output=True)
            logger.info(f"[+] IP configured: {ip}/24")
        except Exception as e:
            logger.error(f"IP configuration error: {e}")

    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        if not self.session_handle or not self.running:
            return None
        try:
            packet_ptr = ctypes.c_void_p()
            packet_size = ctypes.c_uint32(0)
            result = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
            if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                data = ctypes.string_at(packet_ptr, packet_size.value)
                self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                return data
            elif result == 232:
                wait_result = ctypes.windll.kernel32.WaitForSingleObject(self.read_event, int(timeout * 1000))
                if wait_result == 0:
                    result = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
                    if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                        data = ctypes.string_at(packet_ptr, packet_size.value)
                        self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                        return data
            return None
        except Exception:
            return None

    def write(self, packet: bytes) -> bool:
        if not self.session_handle or not self.running or not packet:
            return False
        try:
            packet_ptr = self.wintun.WintunAllocateSendPacket(self.session_handle, len(packet))
            if packet_ptr and packet_ptr != 0:
                ctypes.memmove(packet_ptr, packet, len(packet))
                self.wintun.WintunSendPacket(self.session_handle, packet_ptr)
                return True
            return False
        except Exception:
            return False

    def close(self):
        self.running = False
        if self.session_handle:
            self.wintun.WintunEndSession(self.session_handle)
        if self.adapter_handle:
            self.wintun.WintunCloseAdapter(self.adapter_handle)
            if self.wintun.WintunFreeAdapter:
                self.wintun.WintunFreeAdapter(self.adapter_handle)
        try:
            import subprocess
            subprocess.run(f'netsh interface ipv4 delete route 10.8.0.0/24 "{self.name}"', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 delete address "{self.name}"', shell=True, capture_output=True)
        except Exception:
            pass

# ============== ОСНОВНОЙ КЛИЕНТ ==============
class VPNClient:
    def __init__(self, host: str = SERVER_HOST, port: int = SERVER_PORT):
        self.host = host
        self.port = port
        self.sock: Optional[ssl.SSLSocket] = None
        self.tun: Optional[TunInterface] = None
        self.running = False
        self.anti_dpi: Optional[AntiDPIEngine] = None
        self.cred_manager = CredentialManager()
        self.assigned_ip: Optional[str] = None
        self.stats = {'bytes_sent': 0, 'bytes_received': 0, 'packets_sent': 0, 'packets_received': 0, 'uptime': 0}
        self.stats_lock = threading.Lock()

    def connect(self):
        logger.info(f"Connecting to {self.host}:{self.port}...")
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(10)
        try:
            raw_sock.connect((self.host, self.port))
            logger.info(f"Connected to {self.host}:{self.port}")
            
            self.anti_dpi = AntiDPIEngine(is_server=False)
            self.sock = self.anti_dpi.wrap_socket(raw_sock, verify_cert=False)
            # Сбрасываем таймаут после wrap_socket
            self.sock.settimeout(None)
            
            logger.info("SSL connection established, checking certificate pin...")
            cert_der = self.sock.getpeercert(binary_form=True)
            if not self.cred_manager.verify_certificate(cert_der):
                logger.error("[!] Certificate pin verification failed!")
                raise ssl.SSLError("Certificate pin mismatch")
            logger.info("[+] Certificate pin verified")
            
            password = self.cred_manager.password
            password_hash = hashlib.sha224(password.encode()).hexdigest()
            client_nonce = secrets.token_bytes(6)
            auth_data = password_hash.encode() + client_nonce + b'\r\n'
            self.sock.send(auth_data)
            logger.info("[+] Authentication sent")
            
            logger.info("[+] Performing PFS handshake...")
            self.anti_dpi.perform_handshake(self.sock)
            logger.info("[+] PFS handshake completed")
            
            logger.info("[+] Waiting for IP assignment...")
            header = self._recv_exact(14)
            if len(header) < 14:
                raise ConnectionError("Failed to receive IP header")
            length = struct.unpack('!H', header[12:14])[0]
            encrypted_data = self._recv_exact(length)
            if len(encrypted_data) == length:
                decrypted_ip = self.anti_dpi.decrypt_packet(header + encrypted_data)
                if decrypted_ip:
                    self.assigned_ip = decrypted_ip.decode()
                    logger.info(f"[+] Assigned IP: {self.assigned_ip}")
                else:
                    raise ConnectionError("Failed to decrypt assigned IP")
            else:
                raise ConnectionError("Failed to receive complete IP packet")
            
            self.tun = TunInterface()
            self.tun.create()
            self.tun.set_ip(self.assigned_ip)
            
            self.running = True
            threading.Thread(target=self._tun_reader_loop, daemon=True).start()
            threading.Thread(target=self._network_reader_loop, daemon=True).start()
            threading.Thread(target=self._stats_monitor, daemon=True).start()
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()
            
            logger.info(f"[+] Connected to VPN server at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Connection error: {e}")
            logger.exception(e)
            return False

    def disconnect(self):
        logger.info("Disconnecting...")
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        if self.tun:
            self.tun.close()
        logger.info("Disconnected")

    def _tun_reader_loop(self):
        while self.running:
            packet = self.tun.read(timeout=0.05)
            if packet:
                try:
                    encrypted = self.anti_dpi.encrypt_packet(packet)
                    self.sock.send(encrypted)
                    with self.stats_lock:
                        self.stats['bytes_sent'] += len(encrypted)
                        self.stats['packets_sent'] += 1
                except Exception:
                    if self.running:
                        break

    def _network_reader_loop(self):
        while self.running:
            try:
                header = self._recv_exact(14)
                if len(header) == 0:
                    logger.warning("[!] Server closed connection")
                    break
                if len(header) < 14:
                    try:
                        self.sock.setblocking(False)
                        peek = self.sock.recv(1, socket.MSG_PEEK)
                        self.sock.setblocking(True)
                        if peek == b'':
                            logger.warning("[!] Connection closed by server")
                            break
                        continue
                    except (BlockingIOError, socket.timeout):
                        self.sock.setblocking(True)
                        continue
                    except:
                        self.sock.setblocking(True)
                        break
                
                length = struct.unpack('!H', header[12:14])[0]
                if 0 < length < 65535:
                    encrypted_data = self._recv_exact(length)
                    if len(encrypted_data) == length:
                        packet = self.anti_dpi.decrypt_packet(header + encrypted_data)
                        if packet and self.tun:
                            self.tun.write(packet)
                            with self.stats_lock:
                                self.stats['bytes_received'] += len(header) + length
                                self.stats['packets_received'] += 1
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError) as e:
                if self.running:
                    logger.warning(f"[!] Connection error: {e}")
                    break
            except Exception as e:
                if self.running:
                    logger.error(f"[!] Reader error: {e}")
                    break

    def _recv_exact(self, length: int) -> bytes:
        data = b''
        while len(data) < length:
            try:
                chunk = self.sock.recv(length - len(data))
                if not chunk:
                    return data
                data += chunk
            except socket.timeout:
                return data
            except (ConnectionResetError, OSError) as e:
                logger.warning(f"[!] Connection error: {e}")
                return data
        return data

    def _heartbeat_loop(self):
        while self.running:
            time.sleep(25)
            if self.running and self.sock and self.anti_dpi:
                try:
                    heartbeat = self.anti_dpi.encrypt_packet(b'')
                    self.sock.send(heartbeat)
                    logger.debug("[♥] Heartbeat sent")
                except:
                    break

    def _stats_monitor(self):
        start = time.time()
        while self.running:
            time.sleep(1)
            with self.stats_lock:
                self.stats['uptime'] = time.time() - start
                if int(self.stats['uptime']) % 10 == 0:
                    logger.info(f"[STATS] Uptime: {int(self.stats['uptime']):02d}s, Tx: {self.stats['bytes_sent']}B, Rx: {self.stats['bytes_received']}B")

def check_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False

def main():
    if not check_admin():
        print("[!] This script requires administrator privileges.")
        return
    print("="*60)
    print("VPN CLIENT 2026")
    print("="*60)
    print(f"Server: {SERVER_HOST}:{SERVER_PORT}")
    print("="*60)
    client = VPNClient()
    try:
        if client.connect():
            while client.running:
                time.sleep(1)
        else:
            print("[!] Failed to connect to server")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()