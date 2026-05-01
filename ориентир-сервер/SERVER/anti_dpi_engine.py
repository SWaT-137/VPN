#!/usr/bin/env python3
import ssl
import socket
import logging

logger = logging.getLogger(__name__)

# ТЕПЕРЬ МЫ ПРИТВОРЯЕМСЯ ЛЕГИТИМНЫМ САЙТОМ!
SNI_DOMAIN = "blog.infoblink.ru"
class AntiDPIEngine:
    def __init__(self, is_server: bool = False):
        self.is_server = is_server

    def create_ssl_context(self):
        if self.is_server:
            # НА СЕРВЕРЕ ПОДГРУЖАЕМ РЕАЛЬНЫЕ СЕРТИФИКАТЫ LET'S ENCRYPT
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain('/etc/letsencrypt/live/blog.infoblink.ru/fullchain.pem', '/etc/letsencrypt/live/blog.infoblink.ru/privkey.pem')
        else:
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.options |= ssl.OP_NO_COMPRESSION
        ctx.set_ciphers('TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256')
        return ctx
    def wrap_socket(self, sock: socket.socket):
        ctx = self.create_ssl_context()
        logger.info(f"[*] SNI setup: {SNI_DOMAIN}")
        
        if self.is_server:
            # СЕРВЕР: Не отправляем SNI, а только читаем его из пакета клиента.
            # Обязательно указываем server_side=True, так как нас вызывает Nginx
            return ctx.wrap_socket(sock, server_side=True)
        else:
            # КЛИЕНТ: Отправляем SNI для обмана провайдера
            return ctx.wrap_socket(sock, server_hostname=SNI_DOMAIN)

