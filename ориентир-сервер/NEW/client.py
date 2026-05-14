import asyncio
import struct
import subprocess
import hashlib
import time
import os
from pytun_pmd3 import TunTapDevice
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Настройки
SERVER_IP = '163.5.29.66'
SERVER_PORT = 65432
TUN_IP = '10.0.0.2'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1280
ADAPTER_NAME = "PyVPN"
LOCAL_GW = "192.168.1.1" # IP ВАШЕГО РОУТЕРА!

# --- НАСТРОЙКИ TROJAN ---
PASSWORD = "SWaT_2008"
SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()

# Генерируем ключ шифрования из пароля для защиты от DPI
# В реальном Trojan ключом выступает TLS, здесь мы используем ChaCha20
key_material = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=b'trojan-vpn-salt', # Статическая соль для совпадения ключей
    iterations=100000,
).derive(PASSWORD.encode())
cipher = ChaCha20Poly1305(key_material)
# ------------------------

class VPNClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, tun_device):
        self.transport = None
        self.adapter = tun_device

    def connection_made(self, transport):
        self.transport = transport
        print("UDP транспорт готов (Trojan + Anti-DPI Encryption).")

    def datagram_received(self, data, addr):
        # Структура: [Nonce 12b][Encrypted([SHA224][IP-пакет]) + Tag 16b]
        if len(data) < 28: # 12 + 16 минимум
            return
            
        try:
            nonce = data[:12]
            ciphertext = data[12:]
            
            # Расшифровываем
            plaintext = cipher.decrypt(nonce, ciphertext, None)
            
            # Проверяем Trojan-хеш (который теперь скрыт внутри шифрования!)
            recv_hash = plaintext[:56]
            ip_packet = plaintext[56:]

            if recv_hash == SHA224_HASH:
                self.adapter.write(ip_packet)
        except Exception:
            # Ошибка расшифровки - пакет не наш
            pass

    def error_received(self, exc):
        print(f"UDP ошибка: {exc}")

def setup_wintun():
    print(f"Создание адаптера {ADAPTER_NAME}...")
    adapter = TunTapDevice(name=ADAPTER_NAME)
    adapter.mtu = MTU

    print("Поднятие WinTun-адаптера...")
    adapter.up()

    print(f"Назначение IP {TUN_IP} и шлюза {TUN_GW}...")
    subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" static {TUN_IP} {NETMASK} {TUN_GW}', shell=True)
    subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True)
    
    # Устанавливаем метрику 1 для VPN-адаптера, чтобы Windows предпочитала его
    subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)

    print("Ожидание применения настроек Windows (3 сек)...")
    time.sleep(3)

    # --- ОЧИСТКА СТАРЫХ МАРШРУТОВ (чтобы не было ошибки "Объект уже существует") ---
    print("Очистка старых маршрутов...")
    subprocess.run(f'route delete {SERVER_IP}', shell=True)
    subprocess.run(f'route delete 0.0.0.0', shell=True) # Удаляет дефолтный шлюз, чтобы прописать свой
    # -------------------------------------------------------------------------------

    print(f"Добавление исключения для VPN-сервера {SERVER_IP}...")
    # ВАЖНО: Указываем метрику 5, чтобы этот маршрут был приоритетнее для сервера, чем VPN-туннель
    subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {LOCAL_GW} metric 5', shell=True)

    print("Принудительное перенаправление всего трафика через VPN...")
    # ВАЖНО: Указываем метрику 1, чтобы весь интернет шел через TUN
    subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {TUN_GW} metric 1', shell=True)

    print("WinTun настроен.")
    return adapter
async def read_from_wintun(adapter, transport):
    loop = asyncio.get_event_loop()
    while True:
        try:
            packet = await loop.run_in_executor(None, adapter.read)
            if packet:
                # Формируем Trojan-подобный пакет: [Хеш 56 байт][IP-пакет]
                trojan_payload = SHA224_HASH + packet
                
                # Шифруем его, чтобы DPI не видел статичный хеш
                nonce = os.urandom(12)
                ciphertext = cipher.encrypt(nonce, trojan_payload, None)
                
                # Отправляем: [Nonce][Зашифрованный Trojan-пакет]
                frame = nonce + ciphertext
                transport.sendto(frame, (SERVER_IP, SERVER_PORT))
        except Exception:
            pass

async def main():
    adapter = setup_wintun()
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VPNClientProtocol(adapter),
        local_addr=('0.0.0.0', 0)
    )

    print("Запуск цикла чтения WinTUN...")
    await read_from_wintun(adapter, transport)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановка клиента...")