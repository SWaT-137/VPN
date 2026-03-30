# trojan_client_fixed.py
import asyncio
import ssl

import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
import os

port = 443
host = 'localhost'
password = "chipopka42"
buffer_size = 8192


class Crypto:
    def __init__(self, password: str):
        self.password = password

    def _derive_key(self, salt: bytes):
        password_bytes = self.password.encode('utf-8')
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        return kdf.derive(password_bytes)

    def encrypt(self, plaintext: bytes):
        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')

        iv = os.urandom(12)
        salt = os.urandom(16)
        key = self._derive_key(salt)

        cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        tag = encryptor.tag

        return salt + iv + ciphertext + tag

    def decrypt(self, data: bytes):
        if len(data) < 44:
            return b''

        salt = data[:16]
        iv = data[16:28]
        tag = data[-16:]
        ciphertext = data[28:-16]

        key = self._derive_key(salt)

        cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
        decryptor = cipher.decryptor()

        try:
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            return plaintext
        except Exception as e:
            print(f"Ошибка расшифровки: {e}")
            return b''


async def send_message():
    """Отправить одно сообщение и получить ответ"""

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    ssl_context.set_alpn_protocols(['http/1.1'])

    try:
        print(f"Подключение к {host}:{port}...")
        reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context)
        print("✅ Подключено к серверу!")

        crypto = Crypto(password)

        # ============ ШАГ 1: Отправляем хэш пароля ============
        password_hash = hashlib.sha256(password.encode()).digest()
        print(f"📤 Отправка хэша пароля ({len(password_hash)} байт)")
        writer.write(password_hash)
        await writer.drain()

        # ============ ШАГ 2: Отправляем адрес назначения ============
        # Для теста используем httpbin.org:80
        target_host = "httpbin.org"
        target_port = 80

        addr_bytes = target_host.encode('utf-8')
        addr_len = len(addr_bytes)
        port_bytes = target_port.to_bytes(2, 'big')

        print(f"📤 Отправка адреса: {target_host}:{target_port}")
        writer.write(bytes([addr_len]))  # 1 байт: длина адреса
        writer.write(addr_bytes)  # N байт: сам адрес
        writer.write(port_bytes)  # 2 байта: порт
        await writer.drain()

        # Даем серверу время подключиться
        await asyncio.sleep(0.5)

        # ============ ШАГ 3: Отправляем HTTP запрос ============
        message = input("\nВведите сообщение для отправки (или Enter для HTTP запроса): ").strip()

        if not message:
            # HTTP запрос по умолчанию
            http_request = f"""GET /get HTTP/1.1
Host: {target_host}
Connection: close

"""
            message = http_request
            print(f"\n📤 Отправка HTTP запроса к {target_host}")

        # Шифруем и отправляем
        encrypted = crypto.encrypt(message.encode('utf-8'))
        writer.write(encrypted)
        await writer.drain()
        print(f"📤 Отправлено {len(encrypted)} зашифрованных байт")

        # ============ ШАГ 4: Получаем ответ ============
        print("⏳ Ожидание ответа...")

        # Получаем ответ (может быть несколько пакетов)
        response_data = b""
        try:
            while True:
                encrypted_response = await asyncio.wait_for(reader.read(buffer_size), timeout=5)
                if not encrypted_response:
                    break
                response_data += encrypted_response
        except asyncio.TimeoutError:
            print("⏱️ Таймаут ожидания данных")

        if response_data:
            print(f"\n📥 Получено {len(response_data)} зашифрованных байт")
            decrypted_response = crypto.decrypt(response_data)
            if decrypted_response:
                try:
                    response_text = decrypted_response.decode('utf-8')
                    print("\n" + "=" * 60)
                    print("📥 ПОЛУЧЕН ОТВЕТ:")
                    print("=" * 60)
                    print(response_text[:2000])  # Показываем первые 2000 символов
                    print("=" * 60)
                except UnicodeDecodeError:
                    print(f"📥 Получены байты: {decrypted_response.hex()[:200]}...")
            else:
                print("❌ Не удалось расшифровать ответ")
        else:
            print("❌ Сервер не ответил")

        writer.close()
        await writer.wait_closed()
        print("\nСоединение закрыто")

    except ConnectionRefusedError:
        print(f"❌ Ошибка: Сервер не доступен на {host}:{port}")
        print("Убедитесь, что сервер запущен и порт правильный")
    except ConnectionResetError:
        print("❌ Сервер разорвал соединение. Возможно, неправильный пароль или формат запроса.")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


async def simple_test():
    """Максимально простой тест"""

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    try:
        print(f"Подключение к {host}:{port}...")
        reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context)
        print("✅ Подключено!")

        crypto = Crypto(password)

        # 1. Пароль
        print("📤 Шаг 1: Отправка пароля...")
        writer.write(hashlib.sha256(password.encode()).digest())
        await writer.drain()

        # 2. Адрес
        print("📤 Шаг 2: Отправка адреса httpbin.org:80...")
        addr = b"httpbin.org"
        writer.write(bytes([len(addr)]))
        writer.write(addr)
        writer.write((80).to_bytes(2, 'big'))
        await writer.drain()

        # Ждем подключения
        await asyncio.sleep(1)

        # 3. HTTP запрос
        print("📤 Шаг 3: Отправка HTTP запроса...")
        http_request = b"GET /get HTTP/1.1\r\nHost: httpbin.org\r\nConnection: close\r\n\r\n"
        encrypted = crypto.encrypt(http_request)
        writer.write(encrypted)
        await writer.drain()
        print(f"📤 Отправлено {len(encrypted)} байт")

        # 4. Ответ
        print("⏳ Шаг 4: Ожидание ответа...")
        response = await reader.read(8192)

        if response:
            print(f"📥 Получено {len(response)} байт")
            decrypted = crypto.decrypt(response)
            if decrypted:
                print("\n" + "=" * 50)
                print(decrypted.decode('utf-8', errors='ignore')[:1000])
                print("=" * 50)
            else:
                print("❌ Ошибка расшифровки")
        else:
            print("❌ Нет ответа")

        writer.close()
        await writer.wait_closed()

    except Exception as e:
        print(f"❌ Ошибка: {e}")


if __name__ == '__main__':
    print("=" * 50)
    print("TROJAN КЛИЕНТ (ИСПРАВЛЕННЫЙ)")
    print("=" * 50)
    print("\nВыберите режим:")
    print("1. Полный тест (с вводом сообщения)")
    print("2. Простой тест (HTTP запрос к httpbin.org)")

    choice = input("\nВаш выбор (1/2): ").strip()

    if choice == '2':
        asyncio.run(simple_test())
    else:
        asyncio.run(send_message())