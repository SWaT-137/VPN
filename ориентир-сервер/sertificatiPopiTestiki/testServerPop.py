import hashlib
import ssl
import asyncio
import cryptography
import hmac
import os
import sys
import pytun_pmd3 as pytun
import socket
import threading
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

port = 443
password = "chipopka42"
buffer_size = 8192
tun_device = None
VPN_SERVER_IP = "10.8.0.1"
VPN_CLIENT_NETWORK = "10.8.0.0"
VPN_NETMASK = "255.255.255.0"
VPN_MTU = 1400

class Crypto:
    def __init__(self, password: str):
        self.password = password

    def _dervive_key(self, salt: bytes):
        password_bytes = self.password.encode('utf-8')

        keyTaike = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length = 32,
            salt = salt,
            iterations = 100000,
            backend = default_backend()
        )
        keyPop = keyTaike.derive(password_bytes)
        return keyPop

    def encrypt(self, plaintext: bytes):
        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')

        iv = os.urandom(12)
        salt = os.urandom(16)
        key = self._dervive_key(salt)
        shifr = Cipher(
            algorithms.AES(key),
            modes.GCM(iv),
            backend=default_backend()
        )
        rashifrovka = shifr.encryptor()
        ciphertext = rashifrovka.update(plaintext) + rashifrovka.finalize()
        tag = rashifrovka.tag
        result = salt + iv + ciphertext + tag
        return result

    def decrypt(self, data: bytes):
        if len(data) < 44:
            return b''
        salt = data[:16]
        iv = data[16:28]
        tag = data[-16:]
        ciphertext = data[28:-16]
        key = self._dervive_key(salt)
        shifrRashifrovki = Cipher(
            algorithms.AES(key),
            modes.GCM(iv, tag),
            backend = default_backend()
        )

        decrupter = shifrRashifrovki.decryptor()

        try:
            plaintext = decrupter.update(ciphertext) + decrupter.finalize()
            return plaintext
        except Exception as e:
            print(f"Очибка: {e}")
            return b''

def tunTunTUnsahuyInterface():
    global tun_device
    try:
        tun = pytun.TunTapDevice(flags = pytun.IFF_TUN | pytun.IFF_NO_PI)
        tun.addr = VPN_SERVER_IP
        tun.netmask = VPN_NETMASK
        tun.mtu = VPN_MTU
        tun.up()

        iface_name = tun.name
        print(iface_name)
        print(VPN_SERVER_IP,VPN_NETMASK)
        print(VPN_MTU)

        tun_device = tun

        return tun

    except Exception as e:
        print(f"очибка {e}")
        raise

def setup_routing():
    import platform
    import subprocess

    system = platform.system()
    print(f"Маршрутизируем {system}")

    if system == "Linux":
        result = subprocess.run(
            ["sysctl", "-w", "net.ipv4.ip_forward=1"],
            capture_output=True,
            text=True
        )
        print(f"   IP forwarding: {result.stdout.strip()}")

        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True
        )

        external_iface = None
        for line in result.stdout.split('\n'):
            if 'default' in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == 'dev' and i + 1 < len(parts):
                        external_iface = parts[i + 1]
                        break

        if not external_iface:
            external_iface = "eth0"
            print(f"   ⚠️ Не удалось определить интерфейс, используем {external_iface}")
        else:
            print(f"   Внешний интерфейс: {external_iface}")

        subprocess.run([
            "iptables", "-t", "nat", "-A", "POSTROUTING",
            "-s", VPN_CLIENT_NETWORK,
            "-o", external_iface,
            "-j", "MASQUERADE"
        ], check=False)
        print(f"   ✅ NAT настроен (MASQUERADE через {external_iface})")

        subprocess.run([
            "iptables", "-A", "FORWARD",
            "-s", VPN_CLIENT_NETWORK,
            "-j", "ACCEPT"
        ], check=False)

        subprocess.run([
            "iptables", "-A", "FORWARD",
            "-d", VPN_CLIENT_NETWORK,
            "-j", "ACCEPT"
        ], check=False)

        print("   ✅ Правила iptables добавлены")

    elif system == "Windows":
        try:
            subprocess.run([
                "netsh", "interface", "ipv4", "set", "global",
                "forwarding=enabled"
            ], check=True, capture_output=True)
            print("   ✅ IP forwarding включен")
        except subprocess.CalledProcessError as e:
            print(f"   ⚠️ Не удалось включить IP forwarding: {e}")

        try:
            result = subprocess.run(
                ["netsh", "interface", "ipv4", "show", "interfaces"],
                capture_output=True,
                text=True
            )

            external_iface = None
            for line in result.stdout.split('\n'):
                if "Connected" in line and "Loopback" not in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part.isdigit() and i + 1 < len(parts):
                            iface_name = parts[i + 1]
                            external_iface = iface_name
                            break
                    if external_iface:
                        break

            if external_iface:
                print(f"   Внешний интерфейс: {external_iface}")
            else:
                print("   ⚠️ Не удалось определить внешний интерфейс")
        except Exception as e:
            print(f"   ⚠️ Ошибка при определении интерфейса: {e}")

        try:
            subprocess.run([
                "netsh", "routing", "ip", "nat", "add", "interface",
                "VPN_INTERFACE", "private"
            ], check=False, capture_output=True)
            print("   ✅ VPN интерфейс добавлен как private")
        except Exception:
            pass

        try:
            subprocess.run([
                "netsh", "routing", "ip", "nat", "add", "interface",
                external_iface, "public"
            ], check=False, capture_output=True)
            print(f"   ✅ {external_iface} добавлен как public")
        except Exception:
            pass

        try:
            subprocess.run([
                "netsh", "routing", "ip", "nat", "add", "address",
                external_iface, "0.0.0.0", "0.0.0.0"
            ], check=False, capture_output=True)
            print("   ✅ NAT адресация настроена")
        except Exception:
            pass

        try:
            subprocess.run([
                "reg", "add",
                "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters",
                "/v", "IPEnableRouter",
                "/t", "REG_DWORD",
                "/d", "1",
                "/f"
            ], check=True, capture_output=True)
            print("   ✅ Реестр настроен для IP маршрутизации")
        except subprocess.CalledProcessError as e:
            print(f"   ⚠️ Не удалось настроить реестр: {e}")

        print("   ℹ️ Для применения настроек требуется перезагрузка Windows")
        print("   ℹ️ Или вручную включите Routing and Remote Access (RRAS)")

async def client_to_tun(reader, tun, crypto):
    try:
        packet_count = 0
        bytes_total = 0
        while True:
            encrypted_data = await reader.read(buffer_size)
            if not encrypted_data:
                print(f"клиент ушел")
                break
            ip_packet = crypto.decrypt(encrypted_data)
            if not ip_packet:
                continue
            await asyncio.to_thread(tun.write, ip_packet)

            packet_count += 1
            bytes_total += len(ip_packet)

            if packet_count % 100 == 0:
                print(f"client->tun: {packet_count} пакеты, {bytes_total} байты")
    except asyncio.CancelledError:
        print(f"clientTun отменнеена")
        raise
    except ConnectionError as e:
        print(f"ошибка соединения {e}")
    except Exception as e:
        print(f"Очибка {e}")

async def tun_to_client(tun, writer, crypto):
    try:
        packet_count = 0
        bytes_total = 0
        while True:
            ip_packet = await asyncio.to_thread(tun.read, tun.mtu)
            if not ip_packet:
                print("Интерйфейс закрыт")
                break
            encrypted = crypto.encrypt(ip_packet)
            writer.write(encrypted)
            await writer.drain()

            packet_count += 1
            bytes_total += len(encrypted)

            if packet_count % 100 == 0:
                print(f"client<-tun: {packet_count} пакеты, {bytes_total} байты")
    except asyncio.CancelledError:
        print(f"clientTun отменнеена")
        raise
    except ConnectionError as e:
        print(f"ошибка соединения {e}")
    except Exception as e:
        print(f"Очибка {e}")



'''
# Тестирование шифрования и расширофания
if __name__ == '__main__':
    crypto = Crypto("chipopka42")

    original = input()
    print(f"Исходное: {original}")

    encrypted = crypto.encrypt(original)
    print(f"Зашифрованное: {encrypted.hex()}")

    decrypted = crypto.decrypt(encrypted)
    print(f"Расшифрованное: {decrypted}")

    assert original == decrypted.decode('utf-8), "Ошибка шифрования!"
    print("✅ Тест пройден!")
'''

def create_ssl_context():
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain('cert.pem', 'key.pem')
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(['http/1.1'])
    return context
''' базовая хуйня 
async def handle_client(reader, writer):
    client_addr = writer.get_extra_info('peername')
    print(f"Клинт тута: {client_addr}")
    crypto = Crypto("chipopka42")

    try:
        while True:
            encrypted_data = await reader.read(buffer_size)
            if not encrypted_data:
                break
            decrypted_data = crypto.decrypt(encrypted_data)
            try:
                text = decrypted_data.decode('utf-8')
                print(f"Получил: {text}")
            except:
                print(f"получил байты: {decrypted_data.hex()}")

            response_text = f"Echo: {text}" if text else f"Echo: {decrypted_data.hex()}"
            response_bytes = response_text.encode('utf-8')

            encrypted_response = crypto.encrypt(response_bytes)

            writer.write(encrypted_response)
            await writer.drain()

            print(F"Отправленно: {response_text}")

    except ConnectionError as e:
        print(e)
    except Exception as e:
        print(e)
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"Клиент нетута: {client_addr}")
'''
async def proxy_data(src_reader, dst_writer, crypto, direction):
    try:
        while True:
            data = await src_reader.read(buffer_size)
            if not data:
                break
            if direction == "client->target":
                processed_data = crypto.decrypt(data)
                print(f"расшифровано колво байт от клиента {len(processed_data)}")
            else:
                processed_data = crypto.encrypt(data)
                print(f"{len(processed_data)} зашифровано для клиента")

            dst_writer.write(processed_data)
            await dst_writer.drain()

    except ConnectionError:
        print(f"Соединение закрыто при прокси {direction}")
    except asyncio.CancelledError:
        print(f"Прокси-задача {direction}")
    except Exception as e:
        print(e)

async def handle_client(reader, writer):
    global tun_device
    client_addr = writer.get_extra_info('peername')
    print(f"Пользователь тут {client_addr}")
    crypto = Crypto(password)
        try:
            password_hash = await reader.readexactly(32)
            expected_hash = hashlib.sha256(password.encode()).digest()

            if not hmac.compare_digest(expected_hash, password_hash):
                print(f"неверный пароль от {client_addr}")
                writer.write(b"Auth failed")
                await writer.drain()
                return
            print(f"клиент туа {client_addr}")

            if tun_device is None:
                print("Создание тун интерфейса")
                tun_device = setup_tun_interface()

                setup_routing()
            else:
                print(f"{tun_device.name} - этот интерфейс юзаем")

            print(f"запуск сессии для {client_addr}")
            print(f"клиент получает ip из {VPN_CLIENT_NETWORK}/{VPN_NETMASK}")

            task_client_to_tun = asyncio.create_task(
                client_to_tun(reader, tun_device, crypto)
            )
            task_tun_to_client = asyncio.create_task(
                tun_to_client(tun_device,writer, crypto)
            )

            done, pending = await asyncio.wait(
                [task_client_to_tun, task_tun_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            print(f"сесия закрыта для {client_addr}")
        except asyncio.IncompleteReadError:
            print(f"пользовал ливнул в овремя авторизации")

        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

            print(f"клиент ливнул{client_addr}")







async def main():
    ssl_context = create_ssl_context()
    server = await asyncio.start_server(
        handle_client,
        '0.0.0.0',
        port,
        ssl = ssl_context
    )

    addrs = ', '.join(str(sock.getsockname()) for sock in server.sockets)
    print(addrs)
    print(port)
    print(password)
    print(VPN_CLIENT_NETWORK, VPN_NETMASK)
    print(VPN_SERVER_IP)


    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nВсе, устал(")