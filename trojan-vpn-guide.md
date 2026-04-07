# Создание VPN типа Trojan - Полное руководство

## Что такое Trojan VPN?

Trojan - это современный протокол для обхода цензуры и блокировок, который маскирует VPN-трафик под обычный HTTPS-трафик. В отличие от традиционных VPN протоколов, Trojan выглядит как обычный веб-сервер для систем DPI (Deep Packet Inspection).

## Архитектура Trojan VPN

### 1. Серверная часть
```
[Интернет] ←→ [Trojan Server (порт 443)]
        ↓
[Веб-сервер для маскировки]   [VPN туннель]
        ↓                       ↓
[Фейковые ответы]          [Настоящий VPN трафик]
```

### 2. Клиентская часть  
```
[Пользователь] ←→ [Trojan Client] ←→ [Интернет]
        ↓               ↓
[GUI интерфейс]   [Шифрование/Маскировка]
```

## Реализация протокола Trojan

### Формат пакета Trojan

```python
# Формат аутентификации:
[ХЕШ ПАРОЛЯ (56 байт SHA224)] + [\r\n] + [ДАННЫЕ] + [\r\n\r\n]

# Пример реализации:
password_hash = hashlib.sha224(password.encode()).hexdigest()
auth_data = password_hash.encode() + b'\r\n' + request_data + b'\r\n\r\n'
```

### Ключевые компоненты

#### 1. Модуль протокола (`protocol.py`)
```python
class TrojanProtocol:
    def authenticate_client(self, sock):
        # Чтение 58 байт: 56 байт хеша + \r\n
        auth_data = sock.recv(58)
        if auth_data[56:58] == b'\r\n':
            password_hash = auth_data[:56].decode()
            return password_hash == self.expected_hash
        return False
```

#### 2. Фейковый веб-сервер
```python
class FakeWebServer:
    def serve_fake_response(self, sock):
        # Отправка случайного HTTP ответа
        responses = [
            "HTTP/1.1 200 OK...",
            "HTTP/1.1 404 Not Found...", 
            "HTTP/1.1 301 Moved Permanently..."
        ]
        response = random.choice(responses)
        sock.send(response.encode())
```

## Пошаговая реализация

### Шаг 1: Настройка сервера

#### 1.1 Генерация SSL сертификата
```python
def generate_ssl_cert():
    from cryptography import x509
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # ... создание самоподписанного сертификата
```

#### 1.2 Инициализация TUN интерфейса
```python
class TUNInterface:
    def __init__(self, name, ip, netmask):
        self.wintun = Wintun("wintun.dll")
        self.handle = self.wintun.create_adapter(name)
        # Назначение IP адреса
        subprocess.run(f'netsh interface ip set address "{name}" static {ip} {netmask}')
```

### Шаг 2: Реализация аутентификации

#### 2.1 Проверка пароля
```python
def _trojan_authenticate(self):
    auth_success, web_data = self.trojan_protocol.authenticate_client(self.ssl_sock)
    if auth_success:
        return True  # VPN режим
    else:
        self.fake_web_server.serve_fake_response(self.ssl_sock)  # Веб-режим
        return False
```

#### 2.2 Обработка клиентов
```python
class ClientHandler:
    def run(self):
        # Аутентификация Trojan
        if not self._trojan_authenticate():
            return
        
        # Выделение IP клиенту
        self.client_ip = f"10.8.0.{self.vpn_server.next_ip}"
        self.vpn_server.next_ip += 1
        
        # Регистрация клиента
        self.vpn_server.clients[self.client_ip] = {
            'socket': self.ssl_sock,
            'nonce': os.urandom(12),
            'last_activity': time.time()
        }
```

### Шаг 3: Шифрование трафика

#### 3.1 Криптографический движок
```python
class CryptoEngine:
    def __init__(self, password):
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=self.salt)
        self.key = kdf.derive(password.encode())
        self.cipher = AESGCM(self.key)
    
    def encrypt(self, data, nonce):
        return self.cipher.encrypt(nonce, data, None)
    
    def decrypt(self, data, nonce):
        return self.cipher.decrypt(nonce, data, None)
```

#### 3.2 Формат шифрованных пакетов
```
[nonce (12 байт)] + [длина данных (2 байта)] + [шифрованные данные]
```

### Шаг 4: Клиентская реализация

#### 4.1 Подключение клиента
```python
class TrojanClient:
    def connect(self):
        # Установка SSL соединения
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.connect((self.host, self.port))
        self.ssl_sock = ssl.wrap_socket(raw_sock)
    
    def authenticate(self):
        # Отправка Trojan аутентификации
        auth_data = self.password_hash.encode() + b'\r\n'
        self.ssl_sock.send(auth_data)
```

## Конфигурация

### Серверная конфигурация
```python
HOST = "0.0.0.0"
PORT = 443
PASSWORD = "mysecretpassword123"
CERTFILE = "server.crt"
KEYFILE = "server.key"
TUN_NAME = "VPNServer"
VPN_SERVER_IP = "10.8.0.1"
VPN_NETMASK = "255.255.255.0"
```

### Клиентская конфигурация
```python
HOST = "127.0.0.1"  # или IP сервера
PORT = 443
PASSWORD = "mysecretpassword123"
```

## Запуск системы

### 1. Подготовка сервера
```bash
# Установка зависимостей
pip install cryptography pywin32

# Генерация SSL сертификатов
python server.py  # автоматически генерирует cert и key файлы

# Запуск сервера (требует админских прав)
python server.py
```

### 2. Подготовка клиента
```bash
# Запуск тестового клиента
python simple_client.py
```

## Особенности реализации

### 1. Маскировка под HTTPS
- Все соединения идут через порт 443
- Неавторизованные запросы получают фейковые HTTP ответы
- Для DPI система выглядит как обычный веб-сервер

### 2. Безопасность
- TLS 1.3 для шифрования соединения
- AES-GCM для шифрования пакетов
- Аутентификация по SHA224 хешу пароля
- Защита от повторного использования nonce

### 3. Производительность  
- Многопоточная обработка клиентов
- Асинхронное чтение/запись TUN интерфейса
- Буферизация пакетов для минимизации задержек

## Тестирование

### Тест 1: Правильный пароль
```python
# Клиент подключается и получает VPN IP
client = TrojanClient(HOST, PORT, "correct_password")
client.connect()
client.authenticate()
# → Успешное подключение, получение IP 10.8.0.2
```

### Тест 2: Неправильный пароль
```python  
# Клиент получает фейковый HTTP ответ
client = TrojanClient(HOST, PORT, "wrong_password")
client.connect()
client.authenticate()
# → Получение HTTP ответа вместо VPN подключения
```

## Преимущества Trojan VPN

1. **Обход блокировок** - трафик выглядит как обычный HTTPS
2. **Высокая скорость** - минимальные накладные расходы
3. **Надежность** - устойчивость к DPI системам
4. **Простота использования** - автоматическая настройка
5. **Кроссплатформенность** - работает на Windows/Linux

## Рекомендации по развертыванию

1. Используйте Let's Encrypt для реальных сертификатов
2. Настройте брандмауэр для порта 443
3. Реализуйте систему лимитов трафика
4. Добавьте мониторинг и логирование
5. Используйте надежные пароли для аутентификации

## Команды
(открытие портов) powershell от имени админа
- netsh advfirewall firewall add rule name="VPN Server" dir=in action=allow protocol=TCP localport=443 
(облегчение)
- cd C:\Users\Андрей\VPN\ориентир-сервер\SERVER