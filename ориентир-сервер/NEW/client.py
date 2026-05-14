import asyncio
import struct
import subprocess
from pytun_pmd3 import TunTapDevice

SERVER_IP = '163.5.29.66' # Ваш IP
SERVER_PORT = 65432
TUN_IP = '10.0.0.2'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1400
ADAPTER_NAME = "PyVPN"
LOCAL_GW = "192.168.1.1" # УБЕДИТЕСЬ, ЧТО ЗДЕСЬ IP ВАШЕГО РОУТЕРА!

class VPNClient:
    def __init__(self):
        self.tcp_writer = None
        self.adapter = None

    def setup_wintun(self):
        print(f"Создание адаптера {ADAPTER_NAME}...")
        self.adapter = TunTapDevice(name=ADAPTER_NAME)
        self.adapter.mtu = MTU

        print("Поднятие WinTun-адаптера...")
        self.adapter.up()

        print(f"Назначение IP {TUN_IP} и шлюза {TUN_GW}...")
        subprocess.run(
            f'netsh interface ip set address name="{ADAPTER_NAME}" '
            f'static {TUN_IP} {NETMASK} {TUN_GW}', shell=True
        )

        print("Настройка DNS (1.1.1.1) для адаптера...")
        subprocess.run(
            f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True
        )

        print("Установка метрики 1 (высший приоритет) для VPN-адаптера...")
        subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)

        # --- ОЧИСТКА И НАСТРОЙКА МАРШРУТОВ ---
        # 1. Сначала удаляем старые маршруты, если они зависли
        subprocess.run(f'route delete {SERVER_IP}', shell=True, capture_output=True)
        subprocess.run(f'route delete 0.0.0.0', shell=True, capture_output=True)

        # 2. Добавляем исключение для VPN-сервера
        print(f"Добавление исключения для VPN-сервера {SERVER_IP}...")
        subprocess.run(
            f'route add {SERVER_IP} mask 255.255.255.255 {LOCAL_GW} metric 5', shell=True
        )

        # 3. Перенаправляем весь трафик в туннель
        print("Перенаправление всего трафика (0.0.0.0/0) через туннель...")
        subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {TUN_GW} metric 10', shell=True)
        
        print("WinTun настроен. Весь трафик должен идти через Нидерланды.")

    def cleanup_routes(self):
        """Очистка маршрутов при выходе"""
        print("\nОчистка маршрутов...")
        subprocess.run(f'route delete {SERVER_IP}', shell=True, capture_output=True)
        subprocess.run(f'route delete 0.0.0.0', shell=True, capture_output=True)

    async def tcp_client(self):
        reader, self.tcp_writer = await asyncio.open_connection(SERVER_IP, SERVER_PORT)
        print("Подключено к VPN серверу!")

        self.setup_wintun()
        asyncio.create_task(self.read_from_wintun())

        try:
            while True:
                length_data = await reader.readexactly(2)
                length = struct.unpack('!H', length_data)[0]
                packet = await reader.readexactly(length)
                try:
                    self.adapter.write(packet)
                except Exception:
                    pass
        except asyncio.IncompleteReadError:
            print("Соединение с сервером разорвано.")
        except Exception as e:
            print(f"Ошибка TCP: {e}")
        finally:
            self.cleanup_routes() # Убираем за собой
            if self.adapter:
                self.adapter.down()
                self.adapter.close()

    async def read_from_wintun(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                packet = await loop.run_in_executor(None, self.adapter.read)
                if packet and self.tcp_writer:
                    length = len(packet)
                    frame = struct.pack('!H', length) + packet
                    self.tcp_writer.write(frame)
            except Exception:
                pass

async def main():
    client = VPNClient()
    try:
        await client.tcp_client()
    except KeyboardInterrupt:
        print("\nПрограмма остановлена пользователем.")
    finally:
        client.cleanup_routes()

if __name__ == '__main__':
    asyncio.run(main())