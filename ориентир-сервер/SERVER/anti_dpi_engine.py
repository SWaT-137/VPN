#!/usr/bin/env python3
"""
Lean Anti-DPI Engine - TLS Camouflage for Self-Signed Certs
"""
import ssl
import socket
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Используем реальный домен с большим легитимным трафиком.
# Этот же домен будет вшит в самоподписанный сертификат!
SNI_DOMAIN = "speed.cloudflare.com"

class AntiDPIEngine:
    def __init__(self, is_server: bool = False):
        self.is_server = is_server
        logger.info(f"[+] AntiDPI init (server={is_server})")

    def create_ssl_context(self):
        """Создает контекст без ALPN и с правильными шифрами"""
        if self.is_server:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            from server import CERT_FILE, KEY_FILE
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        else:
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)  # ИСПРАВЛЕНО
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        
        # Базовые требования
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.options |= ssl.OP_NO_COMPRESSION
        
        # ❌ НЕТ ALPN! Мы не притворяемся браузером, мы притворяемся неизвестным приложением.
        # Это спасает от необходимости отправлять HTTP/2 префейс.
        
        # Стандартный современный набор шифров
        ctx.set_ciphers(
            'TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:'
            'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256'
        )
        return ctx

    def wrap_socket(self, sock: socket.socket):
        ctx = self.create_ssl_context()
        logger.info(f"[*] SNI: {SNI_DOMAIN} (ALPN: None)")
        # SNI всегда совпадает с тем, что генерится в сертификате
        return ctx.wrap_socket(sock, server_hostname=SNI_DOMAIN)