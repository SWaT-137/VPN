import asyncio
import pytun
import hashlib

# Настройки сети туннеля
TUN_IP = '10.0.0.1'
NETMASK = '255.255.255.0'
MTU = 1280
SERVER_PORT = 65432

# --- НАСТРОЙКИ TROJAN ---
PASSWORD = "SWaT_2008" # СОВПАДАЕТ С КЛИЕНТОМ!
EXPECTED_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
# ------------------------

class VPNServerProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.tun = None
        self.client_addr = None # Запоминаем, кому отправлять данные

    def connection_made(self, transport):
        self.transport = transport
        print(f"UDP VPN Сервер слушает порт {SERVER_PORT}...")
        
        # Создаем TUN интерфейс
        self.tun = pytun.TunTapDevice(name='tun0', flags=pytun.IFF_TUN | pytun.IFF_NO_PI)
        self.tun.addr = TUN_IP
        self.tun.netmask = NETMASK
        self.tun.mtu = MTU
        self.tun.up()
        print(f"TUN интерфейс tun0 поднят с IP {TUN_IP}")

        # Запускаем чтение из TUN
        asyncio.create_task(self.read_from_tun())

    def datagram_received(self, data, addr):
        # Пакет от клиента. Проверяем длину (минимум 56 байт хеш + заголовок IP)
        if len(data) < 60:
            return

        recv_hash = data[:56]
        payload = data[56:]

        # Проверка пароля
        if recv_hash == EXPECTED_HASH:
            # Запоминаем/обновляем адрес клиента
            if self.client_addr != addr:
                print(f"Обнаружен новый клиент: {addr}")
                self.client_addr = addr
            
            # Отправляем чистый IP-пакет в TUN
            try:
                self.tun.write(payload)
            except Exception:
                pass

    async def read_from_tun(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                # Читаем IP-пакет из ядра
                packet = await loop.run_in_executor(None, self.tun.read, MTU)
                
                # Если знаем клиента, отправляем ему пакет с хешем
                if packet and self.client_addr:
                    frame = EXPECTED_HASH + packet
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
    
    # Создаем UDP эндпоинт
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VPNServerProtocol(),
        local_addr=('0.0.0.0', SERVER_PORT)
    )
    
    try:
        await asyncio.Future() # Бесконечный запуск
    finally:
        transport.close()

if __name__ == '__main__':
    asyncio.run(main())
