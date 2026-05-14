import asyncio
import pytun
import hashlib
import os
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

# Тот же ключ шифрования, что и на клиенте
key_material = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=b'trojan-vpn-salt',
    iterations=100000,
).derive(PASSWORD.encode())
cipher = ChaCha20Poly1305(key_material)
# ------------------------

class VPNServerProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.tun = None
        self.client_addr = None 

    def connection_made(self, transport):
        self.transport = transport
        print(f"UDP VPN Сервер слушает порт {SERVER_PORT} (Trojan + Anti-DPI)...")
        
        self.tun = pytun.TunTapDevice(name='tun0', flags=pytun.IFF_TUN | pytun.IFF_NO_PI)
        self.tun.addr = TUN_IP
        self.tun.netmask = NETMASK
        self.tun.mtu = MTU
        self.tun.up()
        print(f"TUN интерфейс tun0 поднят с IP {TUN_IP}")

        asyncio.create_task(self.read_from_tun())

    def datagram_received(self, data, addr):
        if len(data) < 28:
            return

        try:
            nonce = data[:12]
            ciphertext = data[12:]
            
            # Расшифровываем пакет
            plaintext = cipher.decrypt(nonce, ciphertext, None)
            
            # Извлекаем скрытый Trojan-хеш
            recv_hash = plaintext[:56]
            ip_packet = plaintext[56:]

            # Проверка пароля (Trojan authentication)
            if recv_hash == EXPECTED_HASH:
                if self.client_addr != addr:
                    print(f"Обнаружен новый авторизованный клиент: {addr}")
                    self.client_addr = addr
                
                try:
                    self.tun.write(ip_packet)
                except Exception:
                    pass
        except Exception:
            # Не удалось расшифровать - чужой пакет
            pass

    async def read_from_tun(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                packet = await loop.run_in_executor(None, self.tun.read, MTU)
                
                if packet and self.client_addr:
                    # Формируем ответ по Trojan-стандарту
                    trojan_payload = EXPECTED_HASH + packet
                    
                    nonce = os.urandom(12)
                    ciphertext = cipher.encrypt(nonce, trojan_payload, None)
                    frame = nonce + ciphertext
                    
                    self.transport.sendto(frame, self.client_addr)
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