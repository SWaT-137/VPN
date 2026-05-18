import asyncio
import pytun
import hashlib
import os
import socket
import struct
import time
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Настройки сети туннеля
TUN_IP = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1280
SERVER_PORT = 65432

# --- НАСТРОЙКИ TROJAN ---
PASSWORD = "SWaT_2008"
EXPECTED_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()

key_material = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=b'trojan-vpn-salt',
    iterations=100000,
).derive(PASSWORD.encode())
cipher = ChaCha20Poly1305(key_material)
# ------------------------

def extract_ips(raw_packet):
    if len(raw_packet) < 20:
        return None, None
    version = (raw_packet[0] >> 4) & 0x0F
    if version != 4:
        return None, None
    src_ip = socket.inet_ntoa(raw_packet[12:16])
    dst_ip = socket.inet_ntoa(raw_packet[16:20])
    return src_ip, dst_ip

class VPNServerProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.tun = None
        self.clients = {} 

    def connection_made(self, transport):
        self.transport = transport
        print(f"UDP VPN Сервер слушает порт {SERVER_PORT} (Anti-Replay Active Probing)...")
        
        self.tun = pytun.TunTapDevice(name='tun0', flags=pytun.IFF_TUN | pytun.IFF_NO_PI)
        self.tun.addr = TUN_IP
        self.tun.netmask = NETMASK
        self.tun.mtu = MTU
        self.tun.up()
        print(f"TUN интерфейс tun0 поднят с IP {TUN_IP}")

        asyncio.create_task(self.read_from_tun())

    def datagram_received(self, data, addr):
        if len(data) < 36:
            return

        try:
            nonce = data[:12]
            ciphertext = data[12:]
            
            plaintext = cipher.decrypt(nonce, ciphertext, None)
            
            recv_hash = plaintext[:56]
            recv_time_bytes = plaintext[56:64]
            ip_packet = plaintext[64:]

            if recv_hash == EXPECTED_HASH:
                # Проверяем метку времени (Анти-Replay)
                try:
                    pkt_time = struct.unpack('!d', recv_time_bytes)[0]
                    if abs(time.time() - pkt_time) > 30:
                        # Слишком старый пакет - это Replay атака от DPI!
                        return
                except:
                    return

                src_ip, _ = extract_ips(ip_packet)
                
                if src_ip:
                    if self.clients.get(src_ip) != addr:
                        print(f"Клиент {src_ip} подключился/обновил адрес: {addr}")
                        self.clients[src_ip] = addr
                
                try:
                    self.tun.write(ip_packet)
                except Exception:
                    pass
        except Exception:
            pass

    async def read_from_tun(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                packet = await loop.run_in_executor(None, self.tun.read, MTU)
                
                if packet and self.clients:
                    _, dst_ip = extract_ips(packet)
                    
                    if dst_ip in self.clients:
                        client_addr = self.clients[dst_ip]
                        
                        # Добавляем метку времени для ответа
                        current_time = struct.pack('!d', time.time())
                        trojan_payload = EXPECTED_HASH + current_time + packet
                        
                        nonce = os.urandom(12)
                        ciphertext = cipher.encrypt(nonce, trojan_payload, None)
                        frame = nonce + ciphertext
                        
                        self.transport.sendto(frame, client_addr)
            except OSError as e:
                if e.errno == 9:
                    print("TUN закрыт.")
                    break
            except Exception as e:
                print(f"Ошибка TUN: {e}")
                break

async def main():
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VPNServerProtocol(),
        local_addr=('0.0.0.0', SERVER_PORT)
    )
    
    try:
        await asyncio.Future()
    finally:
        transport.close()

if __name__ == '__main__':
    asyncio.run(main())
