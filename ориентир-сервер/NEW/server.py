import socket
import ssl
import hashlib
import struct
import threading
import sys

PASSWORD = "mysecret"
PASSWORD_HASH_HEX = hashlib.sha224(PASSWORD.encode()).hexdigest()
print(f"[DEBUG] Сервер ожидает хеш: {PASSWORD_HASH_HEX}") # <-- ДОБАВИТЬ ЭТО
DEST_BACKEND_HOST = "127.0.0.1"   # "заглушка" для не-Trojan трафика (можно: "localhost", порт 80)
DEST_BACKEND_PORT = 80

HOST = "0.0.0.0"
PORT = 8443
CERT = "/etc/letsencrypt/live/blog.infoblink.ru/fullchain.pem"
KEY  = "/etc/letsencrypt/live/blog.infoblink.ru/privkey.pem"

def forward(src, dst):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try: src.close()
        except Exception: pass
        try: dst.close()
        except Exception: pass

def relay(conn1, conn2):
    t1 = threading.Thread(target=forward, args=(conn1, conn2), daemon=True)
    t2 = threading.Thread(target=forward, args=(conn2, conn1), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

def parse_and_handle(tls_conn, first_data):
    """Проверяем пароль и Trojan-запрос в first_data, запускаем туннель к цели или к backend."""
    # Минимальный размер: 56 (hash) + 2 (CRLF) + 1 (CMD) + 1 (ATYP) + 1 (мин. ADDR) + 2 (PORT) + 2 (CRLF) = 65
    if len(first_data) < 65:
        raise ValueError("too short")

    pw_hex = first_data[:56]
    if pw_hex != PASSWORD_HASH_HEX:
        raise ValueError("invalid password")

    crlf1 = first_data[56:58]
    if crlf1 != b"\r\n":
        raise ValueError("no CRLF after password")

    rest = first_data[58:]

    # Найдем второй CRLF — он заканчивает Trojan Request
    idx = rest.find(b"\r\n")
    if idx < 0:
        raise ValueError("no CRLF after trojan request")

    trojan_req = rest[:idx]
    payload_after = rest[idx+2:]  # после CRLF

    # Парсим Trojan Request: CMD (1), ATYP (1), DST.ADDR, DST.PORT (2)
    if len(trojan_req) < 4:
        raise ValueError("trojan request too short")

    cmd = trojan_req[0]
    atyp = trojan_req[1]

    if cmd != 0x01:      # сейчас только CONNECT (0x01)
        raise ValueError("unsupported cmd")

    if atyp == 0x01:      # IPv4
        if len(trojan_req) < 1 + 1 + 4 + 2:
            raise ValueError("bad ipv4 request")
        dst_addr = socket.inet_ntoa(trojan_req[2:6])
        dst_port = struct.unpack("!H", trojan_req[6:8])[0]
    elif atyp == 0x03:    # домен
        if len(trojan_req) < 1 + 1 + 1 + 2:
            raise ValueError("bad domain request")
        domain_len = trojan_req[2]
        dst_addr = trojan_req[3:3+domain_len].decode("utf-8", errors="replace")
        dst_port = struct.unpack("!H", trojan_req[3+domain_len:3+domain_len+2])[0]
    else:
        raise ValueError("unsupported atyp")

    print(f"[+] Trojan CONNECT to {dst_addr}:{dst_port}")

    remote = socket.create_connection((dst_addr, dst_port), timeout=10)

    # Если в первом пакете была полезная нагрузка — отправляем её в туннель сразу
    if payload_after:
        remote.sendall(payload_after)

    relay(tls_conn, remote)

def handle_client(tls_conn):
    try:
        first_data = tls_conn.recv(4096)
        if not first_data:
            return

        parse_and_handle(tls_conn, first_data)
    except Exception as e:
        print(f"[!] Not Trojan / error: {e} -> fallback to backend {DEST_BACKEND_HOST}:{DEST_BACKEND_PORT}")
        try:
            backend = socket.create_connection((DEST_BACKEND_HOST, DEST_BACKEND_PORT), timeout=10)
        except Exception:
            tls_conn.close()
            return
        relay(tls_conn, backend)
    finally:
        try:
            tls_conn.close()
        except Exception:
            pass

def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)

    bindsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    bindsocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bindsocket.bind((HOST, PORT))
    bindsocket.listen(50)

    print(f"[+] Trojan server listening on {HOST}:{PORT} (TLS)")

    while True:
        conn, addr = bindsocket.accept()
        print(f"[+] New connection from {addr}")
        try:
            tls_conn = ctx.wrap_socket(conn, server_side=True)
        except Exception:
            conn.close()
            continue
        threading.Thread(target=handle_client, args=(tls_conn,), daemon=True).start()

if __name__ == "__main__":
    main()