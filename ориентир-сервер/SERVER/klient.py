import socket
import ssl
import json

#SSL контекст для клиента
context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE  # Для самоподписанного сертификата

# Подключаемся к серверу
with socket.create_connection(('localhost', 8443)) as sock:
    with context.wrap_socket(sock, server_hostname='localhost') as ssock:
        print(f"Подключено. SSL версия: {ssock.version()}")
        
        # Отправляем команду
        cmd = {"type": "echo", "message": "Hello"}
        ssock.send((json.dumps(cmd) + "\n").encode())
        
        # Получаем ответ
        response = ssock.recv(1024)
        print(f"Ответ: {response.decode()}")