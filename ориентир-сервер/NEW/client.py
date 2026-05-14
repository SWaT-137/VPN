import asyncio
import struct
import subprocess
import hashlib
import time
from pytun_pmd3 import TunTapDevice

# Настройки
SERVER_IP = '163.5.29.66' # Ваш IP сервера
SERVER_PORT = 65432
TUN_IP = '10.0.0.2'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1280
ADAPTER_NAME = "PyVPN"
LOCAL_GW = "192.168.1.1" # IP ВАШЕГО РОУТЕРА!

# --- НАСТРОЙКИ TROJAN ---
PASSWORD = "SWaT_2008" # СОВПАДАЕТ С СЕРВЕРОМ!
SHA224_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
# ------------------------

class VPNClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, tun_device):
        self.transport = None
        self.adapter = tun_device

    def connection_made(self, transport):
        self.transport = transport
        print("UDP транспорт готов.")

    def datagram_received(self, data, addr):
        # Получили ответ от сервера
        if len(data) < 60:
            return
            
        recv_hash = data[:56]
        payload = data[56:]

        # Проверяем, что пакет от нашего сервера
        if recv_hash == SHA224_HASH:
            try:
                # Пишем в WinTUN
                self.adapter.write(payload)
            except Exception:
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

    print("Настройка DNS (1.1.1.1) для адаптера...")
    subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True)

    print("Установка метрики 1 для VPN-адаптера...")
    subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)

    print("Ожидание применения настроек Windows (3 сек)...")
    time.sleep(3)

    print(f"Добавление исключения для VPN-сервера {SERVER_IP}...")
    subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {LOCAL_GW} metric 5', shell=True)

    print("Принудительное перенаправление всего трафика через VPN...")
    subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {TUN_GW} metric 1', shell=True)

    print("WinTun настроен.")
    return adapter

async def read_from_wintun(adapter, transport):
    loop = asyncio.get_event_loop()
    while True:
        try:
            packet = await loop.run_in_executor(None, adapter.read)
            if packet:
                # Формируем пакет: [Хеш 56 байт][IP-пакет]
                frame = SHA224_HASH + packet
                # Отправляем на сервер
                transport.sendto(frame, (SERVER_IP, SERVER_PORT))
        except Exception:
            pass

async def main():
    # Настраиваем сеть
    adapter = setup_wintun()

    # Создаем UDP транспорт
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VPNClientProtocol(adapter),
        local_addr=('0.0.0.0', 0) # Случайный исходящий порт
    )

    print("Запуск цикла чтения WinTUN...")
    # Запускаем чтение из адаптера
    await read_from_wintun(adapter, transport)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановка клиента...")