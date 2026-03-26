# simple_client.py
import asyncio
import ssl
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
    """Класс шифрования (полностью совместим с серверным)"""

    def __init__(self, password: str):
        self.password = password
        self.salt = b'salt1234567890ab'
        self.key = self._derive_key()

    def _derive_key(self):
        password_bytes = self.password.encode('utf-8')

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=100000,
            backend=default_backend()
        )
        return kdf.derive(password_bytes)

    def encrypt(self, plaintext: bytes):
        """Шифрование с GCM режимом"""
        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')

        iv = os.urandom(12)
        cipher = Cipher(
            algorithms.AES(self.key),
            modes.GCM(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        tag = encryptor.tag
        return iv + ciphertext + tag

    def decrypt(self, data: bytes):
        """Расшифровка с проверкой тега"""
        if len(data) < 28:
            return b''

        iv = data[:12]
        tag = data[-16:]
        ciphertext = data[12:-16]

        cipher = Cipher(
            algorithms.AES(self.key),
            modes.GCM(iv, tag),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()

        try:
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            return plaintext
        except Exception as e:
            print(f"Ошибка расшифровки: {e}")
            return b''


async def send_message():
    """Отправить одно сообщение и получить ответ"""

    # Создаем SSL контекст (игнорируем самоподписанный сертификат)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    ssl_context.set_alpn_protocols(['http/1.1'])

    try:
        # Подключаемся к серверу
        print(f"Подключение к {host}:{port}...")
        reader, writer = await asyncio.open_connection(
            host,
            port,
            ssl=ssl_context
        )

        print("✅ Подключено к серверу!")

        # Создаем объект для шифрования
        crypto = Crypto(password)

        # Ввод сообщения
        message = input("\nВведите сообщение для отправки: ").strip()

        if not message:
            print("Сообщение не может быть пустым")
            return

        # Шифруем и отправляем
        print(f"\n📤 Отправка: {message}")
        encrypted = crypto.encrypt(message.encode('utf-8'))
        writer.write(encrypted)
        await writer.drain()

        # Получаем ответ
        print("⏳ Ожидание ответа...")
        encrypted_response = await reader.read(buffer_size)

        if encrypted_response:
            decrypted_response = crypto.decrypt(encrypted_response)
            if decrypted_response:
                try:
                    response_text = decrypted_response.decode('utf-8')
                    print(f"📥 Получен ответ: {response_text}")
                except UnicodeDecodeError:
                    print(f"📥 Получен ответ (hex): {decrypted_response.hex()}")
            else:
                print("❌ Не удалось расшифровать ответ")
        else:
            print("❌ Сервер не ответил")

        # Закрываем соединение
        writer.close()
        await writer.wait_closed()
        print("\nСоединение закрыто")

    except ConnectionRefusedError:
        print(f"❌ Ошибка: Сервер не доступен на {host}:{port}")
        print("Убедитесь, что сервер запущен и порт правильный")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    print("=" * 50)
    print("Клиент для отправки одного сообщения")
    print("=" * 50)
    asyncio.run(send_message())