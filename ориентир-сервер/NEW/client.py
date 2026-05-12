import asyncio
import struct
import subprocess
from pytun_pmd3 import TunTapDevice

# Настройки подключения и сети
SERVER_IP = '163.5.29.66' # Замените на реальный IP сервера
SERVER_PORT = 65432
TUN_IP = '10.0.0.2'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1400  # СНИЖАЕМ MTU ДЛЯ СТАБИЛЬНОСТИ
ADAPTER_NAME = "PyVPN"

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
            f'static {TUN_IP} {NETMASK} {TUN_GW}',
            shell=True,
            capture_output=True,
        )

                # ---------------------------------------------------------
        # ПРАВИЛЬНАЯ МАРШРУТИЗАЦИЯ ДЛЯ ВСЕГО ТРАФИКА
        # ---------------------------------------------------------
        LOCAL_GW = "192.168.1.1" # ЗАМЕНИТЕ НА IP ВАШЕГО РОУТЕРА (шлюз по умолчанию)
        
        print(f"Добавление исключения для VPN-сервера {SERVER_IP}...")
        # Говорим Windows: пакеты до реального IP сервера шли через обычный роутер
        subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {LOCAL_GW} metric 5', shell=True, capture_output=True)
        
        print("Перенаправление всего трафика (0.0.0.0/0) через туннель...")
        # Теперь направляем весь остальной интернет в туннель
        subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {TUN_GW} metric 10', shell=True, capture_output=True)
        print("WinTun настроен.")

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
                
                # БЕЗОПАСНАЯ ЗАПИСЬ В WINTUN
                try:
                    self.adapter.write(packet)
                except Exception as e:
                    # Если буфер полон или пакет кривой, просто игнорируем
                    # TCP внутри туннеля сам восстановит потерю
                    pass
        except asyncio.IncompleteReadError:
            print("Соединение с сервером разорвано.")
        except Exception as e:
            print(f"Ошибка TCP: {e}")
        finally:
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
                    
            except Exception as e:
                pass # Игнорируем ошибки чтения, чтобы туннель не падал

async def main():
    client = VPNClient()
    await client.tcp_client()

if __name__ == '__main__':
    asyncio.run(main())