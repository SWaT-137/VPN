#!/usr/bin/env python3
"""
VPN Client 2026 - Trojan Protocol
E2E ENCRYPTION + Мгновенное обнаружение отключения сервера
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
import logging
import signal
import queue
from typing import Optional
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from anti_dpi_engine import AntiDPIEngine

# ============== КОНФИГУРАЦИЯ ==============
SERVER_HOST = "127.0.0.1" 
SERVER_PORT = 1443
TUN_ADAPTER_NAME = "VPNClient"
CONFIG_DIR = Path("vpn_client_config")
TOKEN_FILE = CONFIG_DIR / "client_token.txt"

FLAG_RAW_INTERNET = 0x00
FLAG_E2E_LAN = 0x01

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============== УПРАВЛЕНИЕ ТОКЕНОМ ==============
class CredentialManager:
    def __init__(self):
        self.token: Optional[str] = None
        CONFIG_DIR.mkdir(exist_ok=True)
        if TOKEN_FILE.exists():
            with open(TOKEN_FILE, 'r') as f: self.token = f.read().strip()
            if len(self.token) > 10: return
        print("\n" + "="*65)
        print("  ВСТАВЬТЕ ВАШ УНИКАЛЬНЫЙ ТОКЕН ОТ АДМИНИСТРАТОРА:")
        print("="*65)
        while True:
            t = input("Токен: ").strip()
            if len(t) > 10:
                self.token = t
                with open(TOKEN_FILE, 'w') as f: f.write(self.token)
                logger.info("[+] Token saved")
                break
            else: print("[!] Токен слишком короткий.")

# ============== WINTUN ==============
class WintunWrapper:
    def __init__(self, dll_path: str = "wintun.dll"):
        found_path = next((p for p in [os.path.join(os.path.dirname(os.path.abspath(__file__)), dll_path), os.path.join(os.getcwd(), dll_path), dll_path] if os.path.exists(p)), None)
        if not found_path: raise FileNotFoundError("wintun.dll not found")
        self.dll = ctypes.WinDLL(found_path)
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter; self.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]; self.WintunCreateAdapter.restype = ctypes.c_void_p
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter; self.WintunOpenAdapter.argtypes = [ctypes.c_wchar_p]; self.WintunOpenAdapter.restype = ctypes.c_void_p
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter; self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]; self.WintunCloseAdapter.restype = None
        self.WintunStartSession = self.dll.WintunStartSession; self.WintunStartSession.argtypes = [ctypes.c_void_p, ctypes.c_uint32]; self.WintunStartSession.restype = ctypes.c_void_p
        self.WintunEndSession = self.dll.WintunEndSession; self.WintunEndSession.argtypes = [ctypes.c_void_p]; self.WintunEndSession.restype = None
        self.WintunGetReadWaitEvent = self.dll.WintunGetReadWaitEvent; self.WintunGetReadWaitEvent.argtypes = [ctypes.c_void_p]; self.WintunGetReadWaitEvent.restype = ctypes.c_void_p
        self.WintunReceivePacket = self.dll.WintunReceivePacket; self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]; self.WintunReceivePacket.restype = ctypes.c_uint32
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket; self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; self.WintunReleaseReceivePacket.restype = None
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket; self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]; self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        self.WintunSendPacket = self.dll.WintunSendPacket; self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; self.WintunSendPacket.restype = None
        try: self.WintunFreeAdapter = self.dll.WintunFreeAdapter; self.WintunFreeAdapter.argtypes = [ctypes.c_void_p]; self.WintunFreeAdapter.restype = None
        except: self.WintunFreeAdapter = None

class TunInterface:
    def __init__(self, name: str = TUN_ADAPTER_NAME):
        self.name = name; self.wintun = WintunWrapper(); self.adapter_handle = None; self.session_handle = None; self.running = False; self.read_event = None
    def create(self):
        try: self.adapter_handle = self.wintun.WintunCreateAdapter(self.name, "VPN", None)
        except: self.adapter_handle = self.wintun.WintunOpenAdapter(self.name)
        self.session_handle = self.wintun.WintunStartSession(self.adapter_handle, 0x400000)
        self.read_event = self.wintun.WintunGetReadWaitEvent(self.session_handle)
        self.running = True
    def set_ip(self, ip: str, server_host: str):
        import subprocess
        subprocess.run(f'netsh interface ipv4 delete address "{self.name}"', shell=True, capture_output=True)
        subprocess.run(f'netsh interface ipv4 set address "{self.name}" {ip} 255.255.255.0', shell=True, capture_output=True)
        is_local_test = server_host in ['127.0.0.1', 'localhost']
        if is_local_test:
            logger.info("[*] Localhost detected. Routing ONLY VPN subnet.")
            subprocess.run(f'netsh interface ipv4 add route 10.8.0.0/24 "{self.name}" metric=10', shell=True, capture_output=True)
        else:
            logger.info("[*] Remote server detected. Routing ALL traffic.")
            gateway = None
            result = subprocess.run('route print -4 0.0.0.0', shell=True, capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if '0.0.0.0' in line:
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] != '0.0.0.0': gateway = parts[2]; break
            if gateway:
                subprocess.run(f'route add {server_host} mask 255.255.255.255 {gateway}', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 add route 0.0.0.0/0 "{self.name}" metric=10', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 set dns "{self.name}" static 8.8.8.8', shell=True, capture_output=True)
    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        if not self.session_handle or not self.running: return None
        try:
            packet_ptr = ctypes.c_void_p(); packet_size = ctypes.c_uint32(0)
            result = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
            if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                data = ctypes.string_at(packet_ptr, packet_size.value)
                self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                return data
            if result == 232:
                wait = ctypes.windll.kernel32.WaitForSingleObject(self.read_event, int(timeout * 1000))
                if wait == 0:
                    result = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_ptr), ctypes.byref(packet_size))
                    if result == 0 and packet_ptr and packet_ptr.value and packet_size.value > 0:
                        data = ctypes.string_at(packet_ptr, packet_size.value)
                        self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                        return data
        except: pass
        return None
    def write(self, packet: bytes) -> bool:
        if not self.session_handle or not self.running or not packet: return False
        try:
            ptr = self.wintun.WintunAllocateSendPacket(self.session_handle, len(packet))
            if ptr and ptr != 0: ctypes.memmove(ptr, packet, len(packet)); self.wintun.WintunSendPacket(self.session_handle, ptr); return True
        except: pass
        return False
    def close(self):
        self.running = False
        if self.session_handle: self.wintun.WintunEndSession(self.session_handle)
        if self.adapter_handle: self.wintun.WintunCloseAdapter(self.adapter_handle)
        if self.wintun.WintunFreeAdapter and self.adapter_handle: self.wintun.WintunFreeAdapter(self.adapter_handle)

# ============== КЛИЕНТ ==============
class VPNClient:
    def __init__(self, host: str = SERVER_HOST, port: int = SERVER_PORT):
        self.host = host; self.port = port; self.sock: Optional[ssl.SSLSocket] = None
        self.tun: Optional[TunInterface] = None; self.running = False
        self.anti_dpi: Optional[AntiDPIEngine] = None; self.cred_manager = CredentialManager()
        self.assigned_ip: Optional[str] = None
        self.aesgcm: Optional[AESGCM] = None

    def connect(self):
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Включаем пинг сервера каждые 5 сек
        raw_sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 5000, 1000))
        raw_sock.settimeout(10)
        try:
            raw_sock.connect((self.host, self.port))
            self.anti_dpi = AntiDPIEngine(is_server=False)
            self.sock = self.anti_dpi.wrap_socket(raw_sock)
            self.sock.settimeout(30.0)
            
            token_hash = hashlib.sha256(self.cred_manager.token.encode()).hexdigest()
            self.sock.sendall(b'\r\n' + token_hash.encode('ascii') + b'\r\n')
            
            header = self._recv_exact(2)
            if len(header) < 2: raise ConnectionError("Failed to receive IP")
            length = struct.unpack('!H', header)[0]
            self.assigned_ip = self._recv_exact(length).decode()
            
            net_key = self._recv_exact(32)
            if len(net_key) != 32: raise ConnectionError("Failed to receive E2E Key")
            self.aesgcm = AESGCM(net_key)
            logger.info("[+] E2E Encryption Key received")
            
            self.tun = TunInterface()
            self.tun.create()
            self.tun.set_ip(self.assigned_ip, self.host)
            
            self.running = True
            threading.Thread(target=self._tun_reader_loop, daemon=True).start()
            threading.Thread(target=self._network_reader_loop, daemon=True).start()
            
            logger.info(f"[+] Connected! IP: {self.assigned_ip}")
            return True
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    def disconnect(self):
        self.running = False
        if self.sock:
            try: self.sock.close()
            except: pass
        if self.tun: self.tun.close()

    def _tun_reader_loop(self):
        while self.running:
            packet = self.tun.read(timeout=0.05)
            if packet and len(packet) >= 20:
                try:
                    dst_ip_bytes = packet[16:20]
                    dst_ip = socket.inet_ntoa(dst_ip_bytes)
                    
                    if dst_ip.startswith("10.8.0."):
                        nonce = os.urandom(12)
                        cipher = self.aesgcm.encrypt(nonce, packet, None)
                        inner_payload = bytes([FLAG_E2E_LAN]) + dst_ip_bytes + nonce + cipher
                    else:
                        inner_payload = bytes([FLAG_RAW_INTERNET]) + packet

                    # Фрейминг: отделяем данные от мусора
                    inner_len_bytes = struct.pack('!I', len(inner_payload))
                    pad_len = secrets.randbelow(32)
                    padding = secrets.token_bytes(pad_len)
                    
                    frame_payload = inner_len_bytes + inner_payload + padding
                    frame = struct.pack('!H', len(frame_payload)) + frame_payload
                    
                    self.sock.sendall(frame)
                    
                except (ConnectionResetError, BrokenPipeError, OSError):
                    logger.warning("[!] Server connection lost while sending packet")
                    self.running = False # Сигнализируем главному потоку об отключении
                    break
                except Exception as e:
                    logger.error(f"[!] Error processing TUN packet: {e}")
                    self.running = False
                    break

    def _network_reader_loop(self):
        while self.running:
            try:
                header = self._recv_exact(2)
                if not header: 
                    logger.warning("[!] Server closed connection (EOF)")
                    self.running = False # Сигнализируем
                    break
                length = struct.unpack('!H', header)[0]
                if not (0 < length < 65535): 
                    self.running = False
                    break
                
                data = self._recv_exact(length)
                if not data or len(data) != length: 
                    logger.warning("[!] Incomplete data received")
                    self.running = False # Сигнализируем
                    break
                
                if len(data) < 4: continue
                
                # Безопасно извлекаем inner_payload, отсекая padding
                inner_len = struct.unpack('!I', data[:4])[0]
                inner_payload = data[4:4+inner_len]
                
                if not inner_payload: continue
                flag = inner_payload[0]
                payload = inner_payload[1:]
                
                if flag == FLAG_RAW_INTERNET:
                    self.tun.write(payload)
                elif flag == FLAG_E2E_LAN:
                    if len(payload) >= 12:
                        nonce = payload[:12]
                        cipher = payload[12:]
                        decrypted_packet = self.aesgcm.decrypt(nonce, cipher, None)
                        self.tun.write(decrypted_packet)
            except socket.timeout: 
                continue
            except Exception as e: 
                logger.error(f"Read loop error: {e}")
                self.running = False # Сигнализируем
                break

    def _recv_exact(self, length: int) -> bytes:
        data = b''
        while len(data) < length:
            try:
                chunk = self.sock.recv(length - len(data))
                if not chunk: return b''  
                data += chunk
            except socket.timeout:
                if not data: raise  
                return b''  
            except OSError: 
                return b''  
        return data

def check_admin() -> bool:
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return False

def main():
    if not check_admin(): print("[!] Run as Admin"); return
    client = VPNClient()
    try:
        if client.connect():
            while client.running: time.sleep(1) # Теперь этот цикл корректно прервется!
        else: print("[!] Failed to connect")
    except KeyboardInterrupt: pass
    finally: client.disconnect()

if __name__ == "__main__":
    main()