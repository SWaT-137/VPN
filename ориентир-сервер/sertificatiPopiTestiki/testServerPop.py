import hashlib
import ssl
import asyncio
import cryptography
import hmac
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

port = 443
password = "chipopka42"
buffer_size = 8192
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
                processed_data = crypto.decrypt(data)
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
    client_addr = writer.get_extra_info('peername')
    print(f"Клиент туты: {client_addr}")
    crypto = Crypto("chipopka42")

    try:
        password_has = await reader.readexactly(32)
        expected_hash = hashlib.sha256(password_has).digest()
        if not hmac.compare_digest(expected_hash, password_has):
            print(f"Неправильный пароль от {client_addr}")
            return
        addr_len_bytes = await reader.readexactly(1)
        addr_len = addr_len_bytes[0]

        addres_bytes = await reader.readexactly(addr_len)
        address = addres_bytes.decode('utf-8')

        port_bytes = await reader.readexactly(2)
        port = int.from_bytes(port_bytes, 'big')

        print(f"запрос к {address}:{port}")

        target_reader, target_writer = await asyncio.open_connection(address, port)

        print(f"soedineneie s {address}:{port} установленно")

        zadanka1 = asyncio.create_task(
            proxy_data(reader, target_writer, crypto, direction="client->target")
        )
        zadanka2 = asyncio.create_task(
            proxy_data(target_reader, writer, crypto, direction="target->clien")
        )

        await asyncio.wait([zadanka1, zadanka2], return_when=asyncio.FIRST_COMPLETED)

        zadanka1.cancel()
        zadanka2.cancel()

    except asyncio.IncompleteReadError:
        print(f" {client_addr} ливнул при чтении заголовка")
    except Exception as e:
        print(f"Ошибка при обработке {client_addr}: {e}")
    finally:
        try:
            target_writer.close()
            await target_writer.wait_closed()
        except:
            pass
        writer.close()
        await writer.wait_closed()
        print(f"чел ливнул {client_addr}")



async def main():
    ssl_context = create_ssl_context()

    server = await asyncio.start_server(
        handle_client,
        '0.0.0.0',
        port,
        ssl = ssl_context
    )

    addrs = ', '.join(str(sock.getsockname()) for sock in server.sockets)
    print(f"сервер работает на: {addrs}")
    print(f"Пароль для клиента: {password}")


    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nВсе, устал(")