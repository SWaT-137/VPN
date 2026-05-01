#!/usr/bin/env python3
"""
VPN Client 2026 - Trojan Protocol
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
from typing import Optional
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from anti_dpi_engine import AntiDPIEngine

# ============== КОНФИГУРАЦИЯ ==============
SERVER_HOST = "blog.infoblink.ru" 
SERVER_PORT = 443
TUN_ADAPTER_NAME = "GhostVPN" 
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
        
        self.WintunCreateAdapter = self.dll.WintunCreateAdapter
        self.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p] 
        self.WintunCreateAdapter.restype = ctypes.c_void_p
        
        self.WintunOpenAdapter = self.dll.WintunOpenAdapter
        self.WintunOpenAdapter.argtypes = [ctypes.c_wchar_p]
        self.WintunOpenAdapter.restype = ctypes.c_void_p
        
        self.WintunCloseAdapter = self.dll.WintunCloseAdapter
        self.WintunCloseAdapter.argtypes = [ctypes.c_void_p]
        self.WintunCloseAdapter.restype = None
        
        self.WintunStartSession = self.dll.WintunStartSession
        self.WintunStartSession.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.WintunStartSession.restype = ctypes.c_void_p
        
        self.WintunEndSession = self.dll.WintunEndSession
        self.WintunEndSession.argtypes = [ctypes.c_void_p]
        self.WintunEndSession.restype = None
        
        self.WintunGetReadWaitEvent = self.dll.WintunGetReadWaitEvent
        self.WintunGetReadWaitEvent.argtypes = [ctypes.c_void_p]
        self.WintunGetReadWaitEvent.restype = ctypes.c_void_p
        
        self.WintunReceivePacket = self.dll.WintunReceivePacket
        self.WintunReceivePacket.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.WintunReceivePacket.restype = ctypes.c_void_p 
        
        self.WintunReleaseReceivePacket = self.dll.WintunReleaseReceivePacket
        self.WintunReleaseReceivePacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunReleaseReceivePacket.restype = None
        
        self.WintunAllocateSendPacket = self.dll.WintunAllocateSendPacket
        self.WintunAllocateSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.WintunAllocateSendPacket.restype = ctypes.c_void_p
        
        self.WintunSendPacket = self.dll.WintunSendPacket
        self.WintunSendPacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.WintunSendPacket.restype = None
        
        try: 
            self.WintunFreeAdapter = self.dll.WintunFreeAdapter
            self.WintunFreeAdapter.argtypes = [ctypes.c_void_p]
            self.WintunFreeAdapter.restype = None
        except: 
            self.WintunFreeAdapter = None

class TunInterface:
    def __init__(self, name: str = TUN_ADAPTER_NAME):
        self.name = name; self.wintun = WintunWrapper(); self.adapter_handle = None; self.session_handle = None; self.running = False; self.read_event = None
        
    def create(self):
        old_handle = self.wintun.WintunOpenAdapter(self.name)
        if old_handle:
            logger.info(f"[*] Найден старый адаптер {self.name}, удаляем...")
            self.wintun.WintunCloseAdapter(old_handle)
            if self.wintun.WintunFreeAdapter: self.wintun.WintunFreeAdapter(old_handle) 
            time.sleep(0.5)
        
        self.adapter_handle = self.wintun.WintunCreateAdapter(self.name, "VPN", None)
        if not self.adapter_handle: raise Exception(f"Failed to create adapter {self.name}")
        
        self.session_handle = self.wintun.WintunStartSession(self.adapter_handle, 0x400000)
        self.read_event = self.wintun.WintunGetReadWaitEvent(self.session_handle)
        self.running = True
        logger.info(f"[+] ✅ Wintun Session запущена!")
        return True

    def set_ip(self, ip: str, server_host: str, server_public_ips: list = None):
        import subprocess
        import re
        
        # 1. Назначаем IP БЕЗ ШЛЮЗА
                # 1. Назначаем IP И ШЛЮЗ (Чтобы Windows разрешила TCP-соединения!)
        subprocess.run(f'netsh interface ip set address "{self.name}" static {ip} 255.255.255.0 10.8.0.1', shell=True, capture_output=True)
        
        # 2. MTU 1380
        subprocess.run(f'netsh interface ipv4 set subinterface "{self.name}" mtu=1380 store=persistent', shell=True, capture_output=True)
        
        # 3. Метрика = 1
        subprocess.run(f'netsh interface ipv4 set interface "{self.name}" metric=1', shell=True, capture_output=True)
        
        # 4. ОТКЛЮЧАЕМ IPv6
        subprocess.run(f'netsh interface ipv6 set interface "{self.name}" disable', shell=True, capture_output=True)
        
        # 5. ОТКЛЮЧАЕМ ВСЮ РАЗГРУЗКУ НА WINTUN (КРИТИЧЕСКИ ВАЖНО ДЛЯ TCP)
        subprocess.run(f'powershell -Command "Disable-NetAdapterChecksumOffload -Name \'{self.name}\' -ErrorAction SilentlyContinue"', shell=True, capture_output=True)
        subprocess.run(f'powershell -Command "Set-NetAdapterAdvancedProperty -Name \'{self.name}\' -DisplayName \'*Large*Send*Offload*\' -DisplayValue \'Disabled\' -ErrorAction SilentlyContinue"', shell=True, capture_output=True)
        subprocess.run(f'powershell -Command "Set-NetAdapterAdvancedProperty -Name \'{self.name}\' -DisplayName \'*Receive*Side*Scaling*\' -DisplayValue \'Disabled\' -ErrorAction SilentlyContinue"', shell=True, capture_output=True)
        
        logger.info(f"[+] IP {ip}, MTU 1380, Offloading DISABLED на {self.name}")
        
        if server_host not in ['127.0.0.1', 'localhost']:
            logger.info("[*] Remote server detected. Routing ALL traffic.")
            try: server_ip = socket.gethostbyname(server_host)
            except Exception as e: logger.error(f"[!] ОШИБКА резолвинга: {e}"); return False
            
            # --- Брандмауэр ---
            subprocess.run(f'netsh advfirewall firewall delete rule name="GhostVPN_In"', shell=True, capture_output=True)
            subprocess.run(f'netsh advfirewall firewall delete rule name="GhostVPN_Out"', shell=True, capture_output=True)
            subprocess.run(f'netsh advfirewall firewall add rule name="GhostVPN_In" dir=in action=allow interface="{self.name}"', shell=True, capture_output=True)
            subprocess.run(f'netsh advfirewall firewall add rule name="GhostVPN_Out" dir=out action=allow interface="{self.name}"', shell=True, capture_output=True)
            subprocess.run(f'powershell -Command "Set-NetConnectionProfile -InterfaceAlias \'{self.name}\' -NetworkCategory Private"', shell=True, capture_output=True)
            
                        # --- ПОИСК ИНДЕКСА АДАПТЕРА (СВЕРХНАДЕЖНЫЙ МЕТОД) ---
            # Мы только что назначили адаптеру IP. Спрашиваем Windows, какому индексу он принадлежит.
            idx_res = subprocess.run(
                f'powershell -Command "(Get-NetIPAddress -IPAddress {ip}).InterfaceIndex"', 
                shell=True, capture_output=True, text=True
            )
            iface_idx = idx_res.stdout.strip()
            
            if not iface_idx or not iface_idx.isdigit():
                logger.error("[!] ФАТАЛ: Не удалось определить индекс адаптера!")
                return False
            
            logger.info(f"[*] Найден индекс Wintun по IP: {iface_idx}")
            
            # --- МАРШРУТИЗАЦИЯ ---
            gateway = None
            result = subprocess.run('route print -4 0.0.0.0', shell=True, capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if '0.0.0.0' in line:
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] == '0.0.0.0' and parts[2] != '0.0.0.0': 
                        gateway = parts[2]; break
            
            if gateway:
                subprocess.run(f'route delete {server_ip}', shell=True, capture_output=True)
                subprocess.run(f'route add {server_ip} mask 255.255.255.255 {gateway} metric 1', shell=True, capture_output=True)
                logger.info(f"[+] Маршрут к серверу {server_ip} через {gateway}")
                
                if server_public_ips:
                    for real_ip in server_public_ips:
                        if real_ip and real_ip != "0.0.0.0" and real_ip != server_ip:
                            subprocess.run(f'route delete {real_ip}', shell=True, capture_output=True)
                            subprocess.run(f'route add {real_ip} mask 255.255.255.255 {gateway} metric 1', shell=True, capture_output=True)
            else: 
                logger.warning("[!] Не найден шлюз провайдера!")
            
            subprocess.run('route delete 0.0.0.0 mask 128.0.0.0', shell=True, capture_output=True)
            subprocess.run('route delete 128.0.0.0 mask 128.0.0.0', shell=True, capture_output=True)
            
            res1 = subprocess.run(f'route add 0.0.0.0 mask 128.0.0.0 0.0.0.0 metric 1 if {iface_idx}', shell=True, capture_output=True, text=True)
            res2 = subprocess.run(f'route add 128.0.0.0 mask 128.0.0.0 0.0.0.0 metric 1 if {iface_idx}', shell=True, capture_output=True, text=True)
            
            if res1.returncode != 0: logger.error(f"[!] ОШИБКА маршрута 0.0.0.0/1: {res1.stderr.strip()}")
            else: logger.info(f"[+] ✅ Маршрут 0.0.0.0/1 привязан к интерфейсу {iface_idx}")
            
            if res2.returncode != 0: logger.error(f"[!] ОШИБКА маршрута 128.0.0.0/1: {res2.stderr.strip()}")
            else: logger.info(f"[+] ✅ Маршрут 128.0.0.0/1 привязан к интерфейсу {iface_idx}")
            
            subprocess.run(f'netsh interface ipv4 set dns "{self.name}" static 1.1.1.1 primary', shell=True, capture_output=True)
            subprocess.run(f'netsh interface ipv4 add dns "{self.name}" 8.8.8.8 index=2', shell=True, capture_output=True)
        return True

    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        if not self.session_handle or not self.running: return None
        try:
            wait = ctypes.windll.kernel32.WaitForSingleObject(self.read_event, int(timeout * 1000))
            if wait != 0: return None
            packet_size = ctypes.c_uint32(0)
            packet_ptr = self.wintun.WintunReceivePacket(self.session_handle, ctypes.byref(packet_size))
            if packet_ptr and packet_size.value > 0:
                data = ctypes.string_at(packet_ptr, packet_size.value)
                self.wintun.WintunReleaseReceivePacket(self.session_handle, packet_ptr)
                return data
            return None
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
            
            header_srv = self._recv_exact(2)
            if len(header_srv) < 2: raise ConnectionError("Failed to receive Server Public IPs")
            length_srv = struct.unpack('!H', header_srv)[0]
            server_public_ips = self._recv_exact(length_srv).decode().split(',')
            
            net_key = self._recv_exact(32)
            if len(net_key) != 32: raise ConnectionError("Failed to receive E2E Key")
            self.aesgcm = AESGCM(net_key)
            
            self.tun = TunInterface()
            if not self.tun.create(): raise Exception("Не удалось создать адаптер")
            if not self.tun.set_ip(self.assigned_ip, self.host, server_public_ips): raise Exception("Не удалось назначить IP")
            
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
                    src_ip_bytes = packet[12:16]
                    dst_ip_bytes = packet[16:20]
                    dst_ip = socket.inet_ntoa(dst_ip_bytes)
                    
                    if dst_ip.startswith("10.8.0."):
                        nonce = os.urandom(12)
                        cipher = self.aesgcm.encrypt(nonce, packet, None)
                        inner_payload = bytes([FLAG_E2E_LAN]) + dst_ip_bytes + nonce + cipher
                    elif src_ip_bytes.startswith(b'\x0a\x08\x00'): 
                        inner_payload = bytes([FLAG_RAW_INTERNET]) + packet
                    else: continue 

                    inner_len_bytes = struct.pack('!I', len(inner_payload))
                    pad_len = secrets.randbelow(32)
                    padding = secrets.token_bytes(pad_len)
                    
                    frame_payload = inner_len_bytes + inner_payload + padding
                    frame = struct.pack('!H', len(frame_payload)) + frame_payload
                    self.sock.sendall(frame)
                    
                except (ConnectionResetError, BrokenPipeError, OSError):
                    logger.warning("[!] Server connection lost"); self.running = False; break
                except Exception as e:
                    logger.error(f"[!] TUN err: {e}"); continue # Не убиваем VPN из-за мусора
                    

    def _network_reader_loop(self):
        while self.running:
            try:
                header = self._recv_exact(2)
                if not header: logger.warning("[!] EOF"); self.running = False; break
                length = struct.unpack('!H', header)[0]
                if not (0 < length < 65535): self.running = False; break
                
                data = self._recv_exact(length)
                if not data or len(data) != length: self.running = False; break
                
                if len(data) < 4: continue
                inner_len = struct.unpack('!I', data[:4])[0]
                inner_payload = data[4:4+inner_len]
                if not inner_payload: continue
                
                flag = inner_payload[0]
                payload = inner_payload[1:]
                
                if flag == FLAG_RAW_INTERNET:
                    if not self.tun.write(payload): pass
                elif flag == FLAG_E2E_LAN:
                    if len(payload) >= 12:
                        nonce = payload[:12]
                        cipher = payload[12:]
                        try:
                            decrypted_packet = self.aesgcm.decrypt(nonce, cipher, None)
                            self.tun.write(decrypted_packet)
                        except Exception as e: logger.error(f"[!] Decrypt err: {e}")
            except socket.timeout: continue
            except Exception as e: 
                logger.error(f"Read loop err: {e}")
                continue # ИСПРАВЛЕНО: Не убиваем VPN

    def _recv_exact(self, length: int) -> bytes:
        data = b''
        while len(data) < length:
            try:
                chunk = self.sock.recv(length - len(data))
                if not chunk: return b''  
                data += chunk
            except socket.timeout: continue # ИСПРАВЛЕНО: Ждем остаток пакета
            except OSError: return b''  
        return data

def check_admin() -> bool:
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return False

def main():
    if not check_admin(): print("[!] Run as Admin"); return
    client = VPNClient()
    try:
        if client.connect():
            while client.running: time.sleep(1)
        else: print("[!] Failed to connect")
    except KeyboardInterrupt: pass
    finally: client.disconnect()

if __name__ == "__main__":
    main()