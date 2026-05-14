import asyncio
import struct
import pytun
import ssl
import hashlib

# Настройки сети туннеля
TUN_IP = '10.0.0.1'
CLIENT_IP = '10.0.0.2'
NETMASK = '255.255.255.0'
MTU = 1400
SERVER_PORT = 65432

# --- НАСТРОЙКИ TROJAN ---
PASSWORD = "SWaT_2008" # ЗАДАЙТЕ СВОЙ ПАРОЛЬ!
EXPECTED_HASH = hashlib.sha224(PASSWORD.encode()).hexdigest().encode()
# FALLBACK_PORT больше не нужен!
# ------------------------

class VPNServer:
    def __init__(self):
        self.tcp_writer = None
        self.tun = None

    async def handle_client(self, reader, writer):
        peername = writer.get_extra_info('peername')
        print(f"Новое TLS-подключение от {peername}. Ожидание аутентификации...")

        try:
            header = await reader.readexactly(59)
            recv_hash = header[:56]
            command = header[56]
            crlf = header[57:59]

            if recv_hash == EXPECTED_HASH and command == 0x01 and crlf == b'\r\n':
                print(f"Успешная аутентификация от {peername}! Запуск VPN.")
                await self.start_vpn(reader, writer)
            else:
                print(f"Неверный пароль от {peername}. Отправка фейкового HTTP ответа...")
                # Упрощенный вызов без initial_data
                await self.handle_fallback(writer)

        except asyncio.IncompleteReadError:
            print(f"Клиент {peername} отключился до отправки заголовка.")
            writer.close()

    # --- НОВЫЙ МЕТОД: ВСТРОЕННЫЙ FALLBACK ---
    async def handle_fallback(self, client_writer):
        # Формируем ответ, имитирующий обычный веб-сервер (Nginx 404 Not Found)
        http_response = (
            b"HTTP/1.1 404 Not Found\r\n"
            b"Server: nginx\r\n"
            b"Content-Type: text/html\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"<html><head><title>404 Not Found</title></head>"
            b"<body><center><h1>404 Not Found</h1></center></body></html>"
        )
        try:
            # Отправляем фейковый ответ и закрываем соединение
            client_writer.write(http_response)
            await client_writer.drain()
        except Exception:
            pass
        finally:
            client_writer.close()
    # -----------------------------------------

    async def start_vpn(self, reader, writer):
        self.tcp_writer = writer
        self.tun = pytun.TunTapDevice(name='tun0', flags=pytun.IFF_TUN | pytun.IFF_NO_PI)
        self.tun.addr = TUN_IP
        self.tun.netmask = NETMASK
        self.tun.mtu = MTU
        self.tun.up()
        print(f"TUN интерфейс tun0 поднят с IP {TUN_IP}")

        asyncio.create_task(self.read_from_tun())

        try:
            while True:
                length_data = await reader.readexactly(2)
                length = struct.unpack('!H', length_data)[0]
                packet = await reader.readexactly(length)
                self.tun.write(packet)
        except asyncio.IncompleteReadError:
            print("VPN-клиент отключился.")
        except Exception as e:
            print(f"Ошибка при чтении из TCP: {e}")
        finally:
            if self.tun:
                self.tun.close()
            writer.close()

    async def read_from_tun(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                packet = await loop.run_in_executor(None, self.tun.read, MTU)
                if packet and self.tcp_writer:
                    length = len(packet)
                    frame = struct.pack('!H', length) + packet
                    self.tcp_writer.write(frame)
            except OSError as e:
                if e.errno == 9:
                    print("Клиент отключился (TUN закрыт).")
                    break
            except Exception as e:
                print(f"Ошибка чтения из TUN: {e}")
                break

async def main():
    server = VPNServer()
    
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    try:
        ssl_context.load_cert_chain(certfile="cert.pem", keyfile="key.pem")
        print("SSL контекст загружен.")
    except FileNotFoundError:
        print("ОШИБКА: Файлы cert.pem и key.pem не найдены!")
        return

    srv = await asyncio.start_server(
        server.handle_client, 
        '0.0.0.0', 
        SERVER_PORT, 
        ssl=ssl_context
    )
    print(f"VPN Сервер слушает порт {SERVER_PORT} (TLS + Trojan + Встроенный Fallback)...")
    
    async with srv:
        await srv.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
