import asyncio
import struct
import subprocess
import ssl
import hashlib # ДОБАВЛЕНО
from pytun_pmd3 import TunTapDevice

# Настройки
SERVER_IP = '163.5.29.66' # Ваш IP сервера
SERVER_PORT = 65432
TUN_IP = '10.0.0.2'
TUN_GW = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1400
ADAPTER_NAME = "PyVPN"
LOCAL_GW = "192.168.1.1" # УБЕДИТЕСЬ, ЧТО ЗДЕСЬ IP ВАШЕГО РОУТЕРА!

# --- НАСТРОЙКИ TROJAN ---
PASSWORD = "SWaT_2008" # ДОЛЖЕН СОВПАДАТЬ С ПАРОЛЕМ СЕРВЕРА!
# ------------------------

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
        subprocess.run(f'netsh interface ip set address name="{ADAPTER_NAME}" static {TUN_IP} {NETMASK} {TUN_GW}', shell=True)

        print("Настройка DNS (1.1.1.1) для адаптера...")
        subprocess.run(f'netsh interface ip set dns name="{ADAPTER_NAME}" static 1.1.1.1 primary', shell=True)

        print("Установка метрики 1 (высший приоритет) для VPN-адаптера...")
        subprocess.run(f'netsh interface ipv4 set interface "{ADAPTER_NAME}" metric=1', shell=True)

        subprocess.run(f'route delete {SERVER_IP}', shell=True, capture_output=True)
        subprocess.run(f'route delete 0.0.0.0', shell=True, capture_output=True)

        print(f"Добавление исключения для VPN-сервера {SERVER_IP}...")
        subprocess.run(f'route add {SERVER_IP} mask 255.255.255.255 {LOCAL_GW} metric 5', shell=True)

        print("Перенаправление всего трафика (0.0.0.0/0) через туннель...")
        subprocess.run(f'route add 0.0.0.0 mask 0.0.0.0 {TUN_GW} metric 10', shell=True)
        
        print("WinTun настроен.")

    def cleanup_routes(self):
        print("\nОчистка маршрутов...")
        subprocess.run(f'route delete {SERVER_IP}', shell=True, capture_output=True)
        subprocess.run(f'route delete 0.0.0.0', shell=True, capture_output=True)

    async def tcp_client(self):
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        reader, self.tcp_writer = await asyncio.open_connection(
            SERVER_IP, 
            SERVER_PORT, 
            ssl=ssl_context
        )
        print("Подключено к VPN серверу (TLS)! Отправка Trojan-заголовка...")

        # --- ОТПРАВКА TROJAN ЗАГОЛОВКА ---
        sha224_hash = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
        # Формат: [Хеш 56 байт][Команда 0x01][CRLF \r\n]
        header = sha224_hash + b'\x01' + b'\r\n'
        self.tcp_writer.write(header)
        await self.tcp_writer.drain() # Ждем, пока заголовок точно уйдет в сеть
        print("Аутентификация отправлена.")
        # ---------------------------------

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
            self.cleanup_routes()
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