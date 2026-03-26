import socket
import socketserver
import ssl
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
import json
import os
#=========генерация ключей и самоподписаных сертификатов=======================
def get_server_directory():
    # Получаем путь к текущему скрипту
    script_path = os.path.abspath(__file__)
    # Возвращаем директорию скрипта
    return os.path.dirname(script_path)
def get_file_path(filename):
    server_dir = get_server_directory()
    return os.path.join(server_dir, filename)



def generate_keys():
    key_path = get_file_path('server.key')
    pub_path = get_file_path('server.pub')
    cert_path = get_file_path('server.crt')
    
    if os.path.exists(key_path) and os.path.exists(cert_path):
        print(f"Файлы ключей и сертификатов уже существуют в: {get_server_directory()}")
        return cert_path, key_path
    
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048, # сложно взломать,ниже не рекомендуется
        backend=default_backend()
    )
    public_key=private_key.public_key()

    with open(key_path,'wb') as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM, #PEM стандарт серилизации
            format=serialization.PrivateFormat.PKCS8, #RKCS8- формат для приватных ключей 
            encryption_algorithm=serialization.NoEncryption() # NoEncryption - без парольной защиты
        ))
    with open(pub_path,'wb') as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    import datetime

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"RU"), #страна
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Moscow"), #город
        x509.NameAttribute(NameOID.LOCALITY_NAME, u"Moscow"), #локация
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"MyOrg"),  #организация
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"), #IP
    ])
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        public_key
    ).serial_number(
        x509.random_serial_number()  # Уникальный номер сертификата
    ).not_valid_before(
        datetime.datetime.utcnow()  # Начало действия
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=365)  # Срок действия 1 год
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(u"localhost")]),
        critical=False,
    ).sign(private_key, hashes.SHA256(), default_backend())
    
    # Сохраняем сертификат в файл
    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    print("Ключи и сертификаты созданы:")
    print("  - server.key  : приватный ключ сервера")
    print("  - server.crt  : сертификат сервера")
    print("  - server.pub  : публичный ключ сервера")
    
    return 'server.crt', 'server.key'
""" 
SSL context
"""
def create_ssl_context(certfile, keyfile):
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER) #современный tls протокол
    context.load_cert_chain(certfile, keyfile) #certfile - сертификат , keyfile - приватный ключ
    context.verify_mode = ssl.CERT_NONE  # Не требуем сертификат от клиента (проверка)
    context.minimum_version = ssl.TLSVersion.TLSv1_2 #стандартная версия тлс, ниже 1.2 не рекомендуется
    context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20')
    
    return context
#==============Обработчик запросов================
class MytCPhandler(socketserver.StreamRequestHandler):
    """ StreamRequestHandler предоставляет:
    - self.request - SSL сокет клиента
    - self.rfile - файлоподобный объект для чтения (буферизированный)
    - self.wfile - файлоподобный объект для записи (буферизированный)
    - self.client_address - адрес клиента (ip, port) 
    """
    def handle(self):
        client_ip, client_port = self.client_address
        print(f"подключен клиент: {client_ip}:{client_port}")
        try:
            data = self.rfile.readline().strip().decode('utf-8')
            if not data:
                print(f"[-] Клиент {client_ip}:{client_port} не отправил данных")
                return
            print(f"[+] Получено от {client_ip}:{client_port}: {data}")
            response = self.process_command(data)
            self.wfile.write(response.encode('utf-8')) #encode преобразует строки в байты, wfile.write отправляет данные 
            self.wfile.flush()  # Принудительно отправляет в буфер
            print(f"[+] Отправлено клиенту {client_ip}:{client_port}: {response}")
            
        except Exception as e:
            print(f"[!] Ошибка при обработке клиента {client_ip}:{client_port}: {e}")
            # Отправляем сообщение об ошибке
            self.wfile.write(f"ERROR: {str(e)}".encode('utf-8'))
    def process_command(self, command):
        """
        Обрабатывает команду от клиента.
        """
        try:
            # парсим JSON
            cmd_data = json.loads(command)
            cmd_type = cmd_data.get('type')
            
            if cmd_type == 'encrypt':
                # Шифруем данные с помощью RSA
                plaintext = cmd_data.get('data', '').encode('utf-8')
                encrypted = self.encrypt_data(plaintext)
                
                return json.dumps({
                    'status': 'success',
                    'encrypted': encrypted.hex(),
                    'message': 'Данные зашифрованы'
                })
                
            elif cmd_type == 'decrypt':
                # Дешифруем данные
                encrypted_hex = cmd_data.get('data', '')
                encrypted = bytes.fromhex(encrypted_hex)
                decrypted = self.decrypt_data(encrypted)
                
                return json.dumps({
                    'status': 'success',
                    'decrypted': decrypted.decode('utf-8'),
                    'message': 'Данные расшифрованы'
                })
                
            elif cmd_type == 'echo':
                #  эхо
                return json.dumps({
                    'status': 'success',
                    'echo': cmd_data.get('message', ''),
                    'message': 'Echo ответ'
                })
                
            else:
                return json.dumps({
                    'status': 'error',
                    'message': f'Неизвестная команда: {cmd_type}'
                })
                
        except json.JSONDecodeError:
            # Если это не JSON, просто возвращаем эхо
            return f"ECHO: {command}"
        except Exception as e:
            return json.dumps({
                'status': 'error',
                'message': str(e)
            }) 
    def encrypt_data(self, data):
        """
        Шифрование данных с помощью RSA публичного ключа.
        """
        # Загружаем публичный ключ (должно быть у клиента в прил.)
        with open('server.pub', 'rb') as f:
            public_key = serialization.load_pem_public_key(
                f.read(),
                backend=default_backend()
            )
        
        # Шифруем с использованием OAEP (оптимальное асимметричное шифрование)
        # OAEP - современная схема дополнения, предотвращающая атаки
        encrypted = public_key.encrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return encrypted
    def decrypt_data(self, encrypted_data):
        """
        Дешифрование данных с помощью RSA приватного ключа.
        """
        # Загружаем приватный ключ
        with open('server.key', 'rb') as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=None,  # Ключ без пароля
                backend=default_backend()
            )
        
        # Расшифровываем
        decrypted = private_key.decrypt(
            encrypted_data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return decrypted
# ======================SSL СЕРВЕР======================

class SSLTCPServer(socketserver.TCPServer):
    """
    Кастомный TCP сервер с поддержкой SSL.
    Наследуемся от TCPServer и переопределяем метод get_request().
    """
    
    def __init__(self, server_address, handler_class, certfile, keyfile):
        # Инициализация SSL сервера.
        # Параметры:
        #- server_address: кортеж (host, port)
        #- handler_class: класс обработчика
        #- certfile: путь к файлу сертификата
        #- keyfile: путь к файлу приватного ключа
        # Создаем SSL контекст
        self.ssl_context = create_ssl_context(certfile, keyfile)
        
        # конструктор родительского класса
        # allow_reuse_address=True позволяет переиспользовать порт после перезапуска
        socketserver.TCPServer.allow_reuse_address = True
        super().__init__(server_address, handler_class)
    
    def get_request(self):
        """
        Возвращает:
        - SSL сокет (защищенный)
        - адрес клиента
        """
        # получение обычного TCP сокета от родительского класса
        sock, addr = super().get_request()
        
        try:
            # Оборачиваем обычный сокет в SSL/TLS
            # server_side=True - это серверная сторона
            # do_handshake_on_connect=True - выполняем handshake сразу
            ssl_sock = self.ssl_context.wrap_socket(
                sock,
                server_side=True,
                do_handshake_on_connect=True
            )
            return ssl_sock, addr
        except ssl.SSLError as e:
            print(f"SSL Handshake error: {e}")
            sock.close()
            raise


# ======================Запуск======================

def main():
    # Генерация ключей и сертификатов (если их нет)
    if not (os.path.exists('server.crt') and os.path.exists('server.key')):
        print("Ключи и сертификаты не найдены. Генерируем новые...")
        certfile, keyfile = generate_keys()
    else:
        certfile = 'server.crt'
        keyfile = 'server.key'
        print("Найдены существующие ключи и сертификаты")
    
    # Настройка сервера
    HOST = 'localhost'  # Слушаем все интерфейсы: '0.0.0.0' или конкретный 'localhost'
    PORT = 8443         # Порт для SSL соединений (обычно 443 для HTTPS, 8443 для тестов)
    
    # Запуск
    server_address = (HOST, PORT)
    
    print(f"\n[*] Запуск SSL сервера на {HOST}:{PORT}")
    print("[*] Сервер готов к приему защищенных соединений")
    print("[*] Нажмите Ctrl+C для остановки\n")
    
    try:
        # Создаем экземпляр сервера
        # Передаем адрес, класс обработчика, и пути к сертификатам
        server = SSLTCPServer(server_address, MytCPhandler, certfile, keyfile)
        
        # Запускаем бесконечный цикл обработки запросов
        # serve_forever() блокирует выполнение и обрабатывает подключения
        server.serve_forever()
        
    except KeyboardInterrupt:
        print("\n[!] Сервер остановлен пользователем")
    except Exception as e:
        print(f"[!] Ошибка сервера: {e}")
    finally:
        # закрытие сервера
        if 'server' in locals():
            server.server_close()
            print("[*] Сервер закрыт")


if __name__ == "__main__":
    main()