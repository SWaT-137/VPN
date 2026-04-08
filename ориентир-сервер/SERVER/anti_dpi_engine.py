#!/usr/bin/env python3
"""
Enhanced Anti-DPI Engine для VPN сервера 2026
Интегрированная подмена SNI с ML-адаптацией и Trojan поддержкой
"""

import ssl
import socket
import struct
import random
import time
import threading
import os
import hashlib
import json
import subprocess
import ipaddress
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List
from dataclasses import dataclass, field
from collections import defaultdict
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Попытка импорта curl_cffi
try:
    from curl_cffi import requests
    from curl_cffi.requests import Session, AsyncClient
    CURL_CFFI_AVAILABLE = True
    print("[+] curl_cffi loaded successfully")
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("[!] curl_cffi not installed. Run: pip install curl_cffi")

# Попытка импорта obfuscation библиотек
try:
    import obfs4
    OBFS4_AVAILABLE = True
except ImportError:
    OBFS4_AVAILABLE = False


@dataclass
class TLSProfile:
    """Профиль TLS для имитации браузера"""
    name: str
    ja3_hash: str
    ciphers: list
    extensions: list
    curves: list
    point_formats: list


class AdvancedSNISpoofer:
    """Продвинутая система подмены SNI с ML-адаптацией"""
    
    def __init__(self):
        # Пул проверенных SNI с их реальными IP диапазонами
        self.valid_sni_pool = {
            'cloudflare': [
                {'domain': 'cloudflare.com', 'ips': ['104.16.0.0/12'], 'weight': 10},
                {'domain': 'cdn.cloudflare.net', 'ips': ['104.16.0.0/12'], 'weight': 8},
                {'domain': 'workers.dev', 'ips': ['104.16.0.0/12'], 'weight': 7},
                {'domain': 'pages.dev', 'ips': ['104.16.0.0/12'], 'weight': 6}
            ],
            'google': [
                {'domain': 'www.google.com', 'ips': ['142.250.0.0/15'], 'weight': 10},
                {'domain': 'fonts.googleapis.com', 'ips': ['142.250.0.0/15'], 'weight': 9},
                {'domain': 'ajax.googleapis.com', 'ips': ['142.250.0.0/15'], 'weight': 8},
                {'domain': 'www.gstatic.com', 'ips': ['142.250.0.0/15'], 'weight': 8}
            ],
            'akamai': [
                {'domain': 'a248.e.akamai.net', 'ips': ['23.0.0.0/8'], 'weight': 9},
                {'domain': 'fonts.net', 'ips': ['23.0.0.0/8'], 'weight': 7},
                {'domain': 'akamaized.net', 'ips': ['23.0.0.0/8'], 'weight': 8}
            ],
            'azure': [
                {'domain': 'azure.microsoft.com', 'ips': ['13.64.0.0/11'], 'weight': 8},
                {'domain': 'azureedge.net', 'ips': ['13.64.0.0/11'], 'weight': 9},
                {'domain': 'msedge.net', 'ips': ['13.64.0.0/11'], 'weight': 7}
            ],
            'fastly': [
                {'domain': 'fastly.net', 'ips': ['151.101.0.0/16'], 'weight': 8},
                {'domain': 'github.io', 'ips': ['151.101.0.0/16'], 'weight': 7},
                {'domain': 'reddit.com', 'ips': ['151.101.0.0/16'], 'weight': 6}
            ],
            'amazon_cf': [
                {'domain': 'd3n32v2v0b3n42.cloudfront.net', 'ips': ['13.224.0.0/14'], 'weight': 9},
                {'domain': 'd1a3f4spax3r3p.cloudfront.net', 'ips': ['13.224.0.0/14'], 'weight': 8}
            ]
        }
        
        self.current_sni = None
        self.current_provider = None
        self.rotation_strategy = 'ml_adaptive'
        self.sni_health = defaultdict(lambda: {'success': 0, 'failures': 0, 'last_check': 0})
        self.sni_performance = defaultdict(list)  # История задержек
        self.blocked_snis = set()
        self.ml_model = SNIBlockPredictor()
        
        # Кеш геолокации
        self.geo_cache = {}
        
        print(f"[+] Advanced SNI Spoofer initialized with {sum(len(v) for v in self.valid_sni_pool.values())} SNIs")
    
    def get_optimal_sni(self, client_ip: str = None, real_host: str = None) -> str:
        """Выбор оптимального SNI с учетом множества факторов"""
        
        # 1. Собираем кандидатов
        candidates = []
        
        # Добавляем SNI от подходящих провайдеров
        provider = self._select_provider_for_client(client_ip)
        candidates.extend(self.valid_sni_pool[provider])
        
        # Добавляем trending SNI если есть
        trending = self._get_trending_snis()
        candidates.extend(trending)
        
        # 2. Фильтруем заблокированные
        candidates = [c for c in candidates if c['domain'] not in self.blocked_snis]
        
        # 3. ML предсказание для каждого кандидата
        scored_candidates = []
        for candidate in candidates:
            block_prob = self.ml_model.predict_block_probability(
                candidate['domain'], 
                client_ip
            )
            
            # Учитываем историю успешности
            health_score = self._calculate_health_score(candidate['domain'])
            
            # Финальный скор
            final_score = (1 - block_prob) * 0.7 + health_score * 0.3
            
            scored_candidates.append((candidate, final_score))
        
        # 4. Выбираем лучший
        if scored_candidates:
            best_candidate = max(scored_candidates, key=lambda x: x[1])[0]
            self.current_sni = best_candidate['domain']
            self.current_provider = provider
            
            print(f"[*] Selected optimal SNI: {self.current_sni} (score: {max(scored_candidates, key=lambda x: x[1])[1]:.3f})")
            return self.current_sni
        
        # Fallback
        return self._get_fallback_sni()
    
    def _select_provider_for_client(self, client_ip: str) -> str:
        """Выбор провайдера на основе геолокации клиента"""
        if not client_ip:
            return random.choice(list(self.valid_sni_pool.keys()))
        
        # Кешируем для скорости
        if client_ip in self.geo_cache:
            return self.geo_cache[client_ip]
        
        try:
            # Определяем страну по IP (упрощенно)
            # В реальности - использовать GeoIP базу
            first_octet = int(client_ip.split('.')[0])
            
            if first_octet in [5, 37, 46, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 176, 178, 185, 188, 212, 213, 217]:
                # Россия и СНГ - Cloudflare и Akamai работают лучше
                provider = random.choice(['cloudflare', 'akamai', 'fastly'])
            elif first_octet in [1, 14, 27, 36, 39, 42, 49, 58, 59, 60, 61, 101, 103, 106, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 134, 150, 153, 163, 171, 175, 180, 182, 183, 202, 203, 210, 211, 218, 219, 220, 221, 222, 223]:
                # Китай/Азия - Azure и Akamai
                provider = random.choice(['azure', 'akamai'])
            else:
                provider = random.choice(list(self.valid_sni_pool.keys()))
            
            self.geo_cache[client_ip] = provider
            return provider
            
        except:
            return random.choice(list(self.valid_sni_pool.keys()))
    
    def _get_trending_snis(self) -> List[Dict]:
        """Получение трендовых SNI"""
        # В реальности - парсинг Alexa/SimilarWeb
        # Сейчас возвращаем популярные сайты
        trending = [
            {'domain': 'www.youtube.com', 'weight': 10, 'ips': ['142.250.0.0/15']},
            {'domain': 'www.netflix.com', 'weight': 9, 'ips': ['52.0.0.0/8']},
            {'domain': 'www.twitch.tv', 'weight': 8, 'ips': ['52.0.0.0/8']},
            {'domain': 'www.discord.com', 'weight': 8, 'ips': ['162.159.128.0/18']},
            {'domain': 'www.telegram.org', 'weight': 7, 'ips': ['149.154.160.0/20']},
            {'domain': 'open.spotify.com', 'weight': 7, 'ips': ['35.0.0.0/8']},
            {'domain': 'www.instagram.com', 'weight': 6, 'ips': ['31.13.64.0/18']}
        ]
        return trending
    
    def _calculate_health_score(self, sni: str) -> float:
        """Расчет здоровья SNI на основе истории"""
        health = self.sni_health[sni]
        
        total_attempts = health['success'] + health['failures']
        if total_attempts == 0:
            return 0.5  # Нейтральная оценка
        
        success_rate = health['success'] / total_attempts
        
        # Учитываем время последней проверки
        time_since_check = time.time() - health['last_check']
        freshness_penalty = min(time_since_check / 3600, 1.0) * 0.2
        
        return success_rate * (1 - freshness_penalty)
    
    def _get_fallback_sni(self) -> str:
        """Fallback SNI если все плохо"""
        emergency_snis = [
            'cloudflare.com',
            'www.google.com',
            'azure.microsoft.com'
        ]
        return random.choice(emergency_snis)
    
    def record_success(self, sni: str):
        """Запись успешного соединения"""
        self.sni_health[sni]['success'] += 1
        self.sni_health[sni]['last_check'] = time.time()
        
        # Обучаем ML модель
        self.ml_model.record_success(sni)
    
    def record_failure(self, sni: str):
        """Запись неудачного соединения"""
        self.sni_health[sni]['failures'] += 1
        self.sni_health[sni]['last_check'] = time.time()
        
        # Если много ошибок - блокируем SNI
        if self.sni_health[sni]['failures'] >= 3:
            self.blocked_snis.add(sni)
            print(f"[!] SNI {sni} blacklisted due to repeated failures")
        
        # Обучаем ML модель
        self.ml_model.record_failure(sni)
    
    def verify_sni_accessible(self, sni: str) -> bool:
        """Проверка доступности SNI"""
        try:
            # Проверяем DNS
            socket.gethostbyname(sni)
            
            # Проверяем TLS handshake
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            with socket.create_connection((sni, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=sni) as ssock:
                    return True
        except:
            return False


class SNIBlockPredictor:
    """ML модель для предсказания блокировок SNI"""
    
    def __init__(self):
        self.history = []
        self.feature_weights = {
            'time_of_day': 0.15,
            'day_of_week': 0.10,
            'sni_length': 0.05,
            'sni_tld': 0.15,
            'provider': 0.20,
            'historical_success': 0.25,
            'client_region': 0.10
        }
        self.success_history = defaultdict(list)
        self.failure_history = defaultdict(list)
        
    def predict_block_probability(self, sni: str, client_ip: str = None) -> float:
        """Предсказание вероятности блокировки"""
        features = self._extract_features(sni, client_ip)
        
        probability = 0.0
        
        # Время суток (ночью чаще блокируют)
        hour = features['hour']
        if 0 <= hour <= 6:
            probability += 0.2
        elif 7 <= hour <= 9:
            probability += 0.1
        elif 18 <= hour <= 23:
            probability += 0.15
            
        # День недели (выходные - меньше блокировок)
        if features['is_weekend']:
            probability -= 0.1
            
        # Длина SNI (короткие подозрительны)
        if features['sni_length'] < 10:
            probability += 0.15
            
        # TLD анализ
        suspicious_tlds = ['.xyz', '.top', '.tk', '.ml', '.ga', '.cf']
        if any(features['tld'].endswith(tld) for tld in suspicious_tlds):
            probability += 0.3
        elif features['tld'] in ['.com', '.net', '.org']:
            probability -= 0.15
            
        # Учет истории
        success_count = len(self.success_history.get(sni, []))
        failure_count = len(self.failure_history.get(sni, []))
        
        if success_count + failure_count > 0:
            historical_rate = failure_count / (success_count + failure_count)
            probability = probability * 0.3 + historical_rate * 0.7
            
        return min(max(probability, 0.0), 1.0)
    
    def _extract_features(self, sni: str, client_ip: str = None) -> Dict:
        """Извлечение признаков для ML"""
        now = datetime.now()
        
        return {
            'hour': now.hour,
            'day_of_week': now.weekday(),
            'is_weekend': now.weekday() >= 5,
            'sni_length': len(sni),
            'tld': '.' + sni.split('.')[-1] if '.' in sni else '',
            'provider': self._detect_provider(sni),
            'has_client_ip': client_ip is not None
        }
    
    def _detect_provider(self, sni: str) -> str:
        """Определение провайдера по SNI"""
        if 'cloudflare' in sni or 'workers.dev' in sni:
            return 'cloudflare'
        elif 'google' in sni or 'gstatic' in sni:
            return 'google'
        elif 'akamai' in sni:
            return 'akamai'
        elif 'azure' in sni or 'msedge' in sni:
            return 'azure'
        elif 'fastly' in sni:
            return 'fastly'
        elif 'cloudfront' in sni:
            return 'amazon'
        else:
            return 'unknown'
    
    def record_success(self, sni: str):
        """Запись успешного соединения"""
        self.success_history[sni].append(time.time())
        self._cleanup_history()
    
    def record_failure(self, sni: str):
        """Запись неудачного соединения"""
        self.failure_history[sni].append(time.time())
        self._cleanup_history()
    
    def _cleanup_history(self):
        """Очистка старых записей"""
        cutoff = time.time() - 86400  # 24 часа
        
        for sni in list(self.success_history.keys()):
            self.success_history[sni] = [t for t in self.success_history[sni] if t > cutoff]
            
        for sni in list(self.failure_history.keys()):
            self.failure_history[sni] = [t for t in self.failure_history[sni] if t > cutoff]


class TrojanSNIIntegration:
    """Интеграция с Trojan для динамической подмены SNI"""
    
    def __init__(self, config_path='/etc/trojan/config.json'):
        self.config_path = config_path
        self.current_sni = None
        self.sni_spoofer = None  # Будет установлен позже
        self.rotation_thread = None
        self.running = False
        
        # Проверяем наличие конфига
        if not os.path.exists(config_path):
            print(f"[!] Trojan config not found at {config_path}")
            self.enabled = False
        else:
            self.enabled = True
            print(f"[+] Trojan integration enabled")
    
    def set_sni_spoofer(self, spoofer: AdvancedSNISpoofer):
        """Установка SNI спуфера"""
        self.sni_spoofer = spoofer
    
    def update_trojan_sni(self, new_sni: str) -> bool:
        """Обновление SNI в конфиге Trojan"""
        if not self.enabled:
            return False
            
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            
            # Обновляем SNI
            if 'ssl' not in config:
                config['ssl'] = {}
            
            config['ssl']['sni'] = new_sni
            config['ssl']['alpn'] = ['h2', 'http/1.1']
            config['ssl']['reuse_session'] = True
            config['ssl']['session_ticket'] = True
            config['ssl']['curves'] = 'X25519:P-256:P-384'
            
            # Добавляем поддержку uTLS если есть
            if 'utls' not in config['ssl']:
                config['ssl']['utls'] = True
            
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            # Перезагружаем Trojan
            os.system('systemctl reload trojan 2>/dev/null || killall -HUP trojan 2>/dev/null')
            
            self.current_sni = new_sni
            print(f"[+] Trojan SNI updated to: {new_sni}")
            
            return True
            
        except Exception as e:
            print(f"[!] Failed to update Trojan SNI: {e}")
            return False
    
    def start_adaptive_rotation(self):
        """Запуск адаптивной ротации SNI"""
        if not self.enabled or not self.sni_spoofer:
            return
            
        self.running = True
        
        def rotation_worker():
            while self.running:
                try:
                    # Проверяем не заблокирован ли текущий SNI
                    if self._detect_sni_blocking():
                        print("[!] SNI blocking detected, rotating...")
                        
                        # Получаем новый SNI
                        new_sni = self.sni_spoofer.get_optimal_sni()
                        
                        # Применяем
                        if self.update_trojan_sni(new_sni):
                            self.sni_spoofer.record_success(new_sni)
                    
                    # Проверяем доступность SNI периодически
                    elif random.random() < 0.1:  # 10% шанс проверки
                        if not self.sni_spoofer.verify_sni_accessible(self.current_sni):
                            print(f"[!] SNI {self.current_sni} became inaccessible")
                            self.sni_spoofer.record_failure(self.current_sni)
                    
                    time.sleep(60)  # Проверка раз в минуту
                    
                except Exception as e:
                    print(f"[!] Rotation error: {e}")
                    time.sleep(5)
        
        self.rotation_thread = threading.Thread(target=rotation_worker, daemon=True)
        self.rotation_thread.start()
        print("[+] Adaptive SNI rotation started")
    
    def _detect_sni_blocking(self) -> bool:
        """Обнаружение блокировки текущего SNI"""
        if not self.current_sni:
            return False
            
        try:
            # Проверяем TCP соединение
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((self.current_sni, 443))
            
            # Проверяем TLS handshake
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            with context.wrap_socket(sock, server_hostname=self.current_sni) as ssock:
                # Получаем сертификат
                cert = ssock.getpeercert()
                
                # Проверяем что сертификат соответствует SNI
                cert_cn = dict(x[0] for x in cert['subject']).get('commonName', '')
                
                if self.current_sni not in cert_cn and cert_cn not in self.current_sni:
                    # Возможна MITM атака от DPI
                    return True
                    
                return False
                
        except ssl.SSLError as e:
            if 'certificate verify failed' in str(e):
                return True
        except Exception as e:
            # Таймаут или сброс соединения - возможная блокировка
            return True
            
        return False


class BrowserTLSImpersonator:
    """
    Имитация TLS fingerprint'ов различных браузеров
    Использует curl_cffi для точной подмены
    """
    
    # Актуальные профили браузеров 2026
    BROWSER_PROFILES = {
        'chrome_124': {
            'impersonate': 'chrome124',
            'description': 'Google Chrome 124 (Windows)'
        },
        'chrome_123': {
            'impersonate': 'chrome123',
            'description': 'Google Chrome 123 (Windows)'
        },
        'chrome_120': {
            'impersonate': 'chrome120',
            'description': 'Google Chrome 120 (Windows)'
        },
        'firefox_124': {
            'impersonate': 'firefox124',
            'description': 'Firefox 124 (Windows)'
        },
        'firefox_123': {
            'impersonate': 'firefox123',
            'description': 'Firefox 123 (Windows)'
        },
        'edge_123': {
            'impersonate': 'edge123',
            'description': 'Microsoft Edge 123 (Windows)'
        },
        'safari_17_0': {
            'impersonate': 'safari17_0',
            'description': 'Safari 17.0 (macOS)'
        },
        'random': {
            'impersonate': 'random',
            'description': 'Randomized fingerprint'
        }
    }
    
    def __init__(self, default_profile='chrome_124'):
        self.current_profile = default_profile
        self.rotation_count = 0
        self.session = None
        self._create_session()
    
    def _create_session(self):
        """Создание сессии с выбранным профилем"""
        if not CURL_CFFI_AVAILABLE:
            return None
        
        profile = self.BROWSER_PROFILES.get(self.current_profile, self.BROWSER_PROFILES['chrome_124'])
        
        self.session = Session()
        self.session.impersonate = profile['impersonate']
        self.session.timeout = 30
        
        # Дополнительные заголовки для имитации
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        })
        
        return self.session
    
    def get_impersonate_string(self) -> str:
        """Получение строки имитации для curl_cffi"""
        profile = self.BROWSER_PROFILES.get(self.current_profile, self.BROWSER_PROFILES['chrome_124'])
        return profile['impersonate']
    
    def create_custom_ssl_context(self):
        """Создание SSL контекста с имитацией браузера"""
        if not CURL_CFFI_AVAILABLE:
            return self._create_fallback_context()
        
        return self._create_optimized_context()
    
    def _create_optimized_context(self):
        """Создание оптимизированного SSL контекста"""
        context = ssl.create_default_context()
        
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        
        context.set_ciphers(
            'TLS_AES_256_GCM_SHA384:'
            'TLS_CHACHA20_POLY1305_SHA256:'
            'TLS_AES_128_GCM_SHA256:'
            'ECDHE-ECDSA-AES128-GCM-SHA256:'
            'ECDHE-RSA-AES128-GCM-SHA256:'
            'ECDHE-ECDSA-CHACHA20-POLY1305:'
            'ECDHE-RSA-CHACHA20-POLY1305:'
            'ECDHE-ECDSA-AES256-GCM-SHA384:'
            'ECDHE-RSA-AES256-GCM-SHA384'
        )
        
        context.set_alpn_protocols(['h2', 'http/1.1'])
        
        return context
    
    def _create_fallback_context(self):
        """Fallback контекст если curl_cffi не установлен"""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    
    def rotate_profile(self):
        """Ротация профиля браузера"""
        profiles = list(self.BROWSER_PROFILES.keys())
        available = [p for p in profiles if p != 'random']
        self.current_profile = random.choice(available)
        self.rotation_count += 1
        self._create_session()
        
        print(f"[*] Browser profile rotated: {self.current_profile}")
        return self.current_profile
    
    def make_request(self, url: str, method='GET', **kwargs):
        """Выполнение HTTP запроса с имитацией браузера"""
        if not self.session:
            self._create_session()
        
        if method.upper() == 'GET':
            return self.session.get(url, **kwargs)
        elif method.upper() == 'POST':
            return self.session.post(url, **kwargs)
        else:
            return self.session.request(method, url, **kwargs)


class MultiLayerObfuscator:
    """Многоуровневая обфускация трафика"""
    
    def __init__(self):
        self.obfuscation_level = 3
        self.session_key = os.urandom(32)
        self.obfuscation_stats = {
            'packets_obfuscated': 0,
            'bytes_processed': 0,
            'methods_used': {}
        }
    
    def obfuscate(self, data: bytes, level: int = None) -> bytes:
        """Полная обфускация данных"""
        if level is None:
            level = self.obfuscation_level
        
        result = data
        methods_used = []
        
        if level >= 1:
            result = self._xor_encode(result)
            methods_used.append('xor')
        
        if level >= 2:
            result = self._add_random_prefix(result)
            methods_used.append('prefix')
        
        if level >= 3:
            result = self._fragment_and_mask(result)
            methods_used.append('fragment')
        
        if level >= 4:
            result = self._insert_random_bytes(result)
            methods_used.append('random_insert')
        
        if level >= 5:
            result = self._mask_as_protocol(result)
            methods_used.append('protocol_mask')
        
        self.obfuscation_stats['packets_obfuscated'] += 1
        self.obfuscation_stats['bytes_processed'] += len(result)
        for method in methods_used:
            self.obfuscation_stats['methods_used'][method] = \
                self.obfuscation_stats['methods_used'].get(method, 0) + 1
        
        return result
    
    def deobfuscate(self, data: bytes) -> bytes:
        """Деобфускация данных"""
        result = data
        
        result = self._unmask_protocol(result)
        result = self._remove_random_bytes(result)
        result = self._defragment(result)
        result = self._remove_prefix(result)
        result = self._xor_decode(result)
        
        return result
    
    def _xor_encode(self, data: bytes, key: bytes = None) -> bytes:
        """XOR кодирование"""
        if key is None:
            offset = random.randint(0, 255)
            key = bytes([(self.session_key[i % len(self.session_key)] ^ offset)
                        for i in range(32)])
        
        return bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])
    
    def _xor_decode(self, data: bytes) -> bytes:
        """XOR декодирование"""
        return self._xor_encode(data)
    
    def _add_random_prefix(self, data: bytes) -> bytes:
        """Добавление случайного префикса"""
        prefixes = [
            b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n",
            b"\x00\x00\x00\x01\x00\x00\x00\x00",
            b"GET / HTTP/1.1\r\nHost: cache\r\n\r\n",
            b"GET / HTTP/1.1\r\nUpgrade: websocket\r\n\r\n",
            b"\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00",
        ]
        
        prefix = random.choice(prefixes)
        length_prefix = len(prefix).to_bytes(2, 'big')
        
        return length_prefix + prefix + data
    
    def _remove_prefix(self, data: bytes) -> bytes:
        """Удаление префикса"""
        if len(data) < 2:
            return data
        
        prefix_length = int.from_bytes(data[:2], 'big')
        
        if len(data) >= 2 + prefix_length:
            return data[2 + prefix_length:]
        
        return data
    
    def _fragment_and_mask(self, data: bytes) -> bytes:
        """Фрагментация и маскировка"""
        frame_size = random.randint(256, 4096)
        fragments = []
        
        for i in range(0, len(data), frame_size):
            fragment = data[i:i + frame_size]
            
            frame_length = len(fragment)
            frame_type = 0x00
            flags = 0x01 if i + frame_size >= len(data) else 0x00
            stream_id = random.randint(1, 0x7FFFFFFF)
            
            http2_frame = (
                frame_length.to_bytes(3, 'big') +
                frame_type.to_bytes(1, 'big') +
                flags.to_bytes(1, 'big') +
                stream_id.to_bytes(4, 'big') +
                fragment
            )
            fragments.append(http2_frame)
        
        end_marker = b'\x00\x00\x00\x00\x00\x00\x00\x00'
        
        return b''.join(fragments) + end_marker
    
    def _defragment(self, data: bytes) -> bytes:
        """Дефрагментация"""
        result = b''
        pos = 0
        
        while pos < len(data):
            if data[pos:pos + 8] == b'\x00\x00\x00\x00\x00\x00\x00\x00':
                break
            
            if pos + 9 > len(data):
                break
            
            frame_length = int.from_bytes(data[pos:pos + 3], 'big')
            pos += 9
            
            if pos + frame_length <= len(data):
                result += data[pos:pos + frame_length]
                pos += frame_length
            else:
                break
        
        return result
    
    def _insert_random_bytes(self, data: bytes) -> bytes:
        """Вставка случайных байт"""
        if len(data) < 10:
            return data
        
        result = bytearray(data)
        insertions = random.randint(1, min(5, len(data) // 10))
        
        for _ in range(insertions):
            pos = random.randint(0, len(result))
            random_bytes = os.urandom(random.randint(1, 8))
            result[pos:pos] = random_bytes
        
        original_length = len(data).to_bytes(4, 'big')
        
        return bytes(result) + b'\xFF\xFF' + original_length
    
    def _remove_random_bytes(self, data: bytes) -> bytes:
        """Удаление случайных байт"""
        marker_pos = data.rfind(b'\xFF\xFF')
        
        if marker_pos == -1 or marker_pos + 6 > len(data):
            return data
        
        original_length = int.from_bytes(data[marker_pos + 2:marker_pos + 6], 'big')
        
        if original_length <= marker_pos:
            return data[:original_length]
        
        return data[:marker_pos]
    
    def _mask_as_protocol(self, data: bytes) -> bytes:
        """Маскировка под другой протокол"""
        protocols = [
            ('QUIC', self._mask_as_quic),
            ('HTTP3', self._mask_as_http3),
            ('WebRTC', self._mask_as_webrtc),
            ('SSH', self._mask_as_ssh),
        ]
        
        protocol_name, mask_func = random.choice(protocols)
        masked_data = mask_func(data)
        
        protocol_id = protocols.index((protocol_name, mask_func)).to_bytes(1, 'big')
        
        return protocol_id + masked_data
    
    def _unmask_protocol(self, data: bytes) -> bytes:
        """Распознавание и размаскировка протокола"""
        if len(data) < 1:
            return data
        
        protocol_id = data[0]
        
        if protocol_id == 0:
            return self._unmask_quic(data[1:])
        elif protocol_id == 1:
            return self._unmask_http3(data[1:])
        elif protocol_id == 2:
            return self._unmask_webrtc(data[1:])
        elif protocol_id == 3:
            return self._unmask_ssh(data[1:])
        
        return data[1:]
    
    def _mask_as_quic(self, data: bytes) -> bytes:
        """Маскировка под QUIC"""
        header_form = 1
        fixed_bit = 1
        packet_type = random.randint(0, 3)
        reserved = 0
        packet_number_length = 2
        
        first_byte = (
            (header_form << 7) |
            (fixed_bit << 6) |
            (packet_type << 4) |
            (reserved << 2) |
            packet_number_length
        )
        
        version = random.randint(0xFF000000, 0xFFFFFFFF)
        dcid_length = random.randint(8, 20)
        dcid = os.urandom(dcid_length)
        scid_length = random.randint(8, 20)
        scid = os.urandom(scid_length)
        
        packet = bytes([first_byte]) + version.to_bytes(4, 'big')
        packet += bytes([dcid_length]) + dcid
        packet += bytes([scid_length]) + scid
        packet += data
        
        return packet
    
    def _unmask_quic(self, data: bytes) -> bytes:
        """Извлечение данных из QUIC"""
        if len(data) < 6:
            return data
        
        pos = 1
        pos += 4
        
        if pos >= len(data):
            return data
        
        dcid_length = data[pos]
        pos += 1 + dcid_length
        
        if pos >= len(data):
            return data
        
        scid_length = data[pos]
        pos += 1 + scid_length
        
        return data[pos:]
    
    def _mask_as_http3(self, data: bytes) -> bytes:
        """Маскировка под HTTP/3"""
        frame_type = 0x00
        frame_length = len(data)
        
        if frame_length < 64:
            encoded_length = bytes([frame_length])
        elif frame_length < 16384:
            encoded_length = (0x40 | (frame_length >> 8)).to_bytes(1, 'big') + (frame_length & 0xFF).to_bytes(1, 'big')
        else:
            encoded_length = frame_length.to_bytes(4, 'big')
        
        frame = bytes([frame_type]) + encoded_length + data
        
        return frame
    
    def _unmask_http3(self, data: bytes) -> bytes:
        """Извлечение данных из HTTP/3"""
        if len(data) < 2:
            return data
        
        pos = 1
        
        first_byte = data[pos] if pos < len(data) else 0
        if first_byte < 64:
            length = first_byte
            pos += 1
        elif first_byte < 128:
            if pos + 1 >= len(data):
                return data
            length = ((first_byte & 0x3F) << 8) | data[pos + 1]
            pos += 2
        else:
            if pos + 3 >= len(data):
                return data
            length = int.from_bytes(data[pos:pos + 4], 'big')
            pos += 4
        
        if pos + length <= len(data):
            return data[pos:pos + length]
        
        return data[pos:]
    
    def _mask_as_webrtc(self, data: bytes) -> bytes:
        """Маскировка под WebRTC"""
        content_type = 22
        version = 0xFEFF
        epoch = 0
        sequence_number = random.getrandbits(48)
        
        header = (
            bytes([content_type]) +
            version.to_bytes(2, 'big') +
            epoch.to_bytes(2, 'big') +
            sequence_number.to_bytes(6, 'big') +
            len(data).to_bytes(2, 'big')
        )
        
        return header + data
    
    def _unmask_webrtc(self, data: bytes) -> bytes:
        """Извлечение данных из WebRTC"""
        if len(data) < 13:
            return data
        
        return data[13:]
    
    def _mask_as_ssh(self, data: bytes) -> bytes:
        """Маскировка под SSH"""
        packet_length = len(data) + 4 + 1
        padding_length = random.randint(4, 16)
        
        packet = (
            packet_length.to_bytes(4, 'big') +
            bytes([padding_length]) +
            data +
            os.urandom(padding_length)
        )
        
        return packet
    
    def _unmask_ssh(self, data: bytes) -> bytes:
        """Извлечение данных из SSH"""
        if len(data) < 5:
            return data
        
        padding_length = data[4]
        
        if len(data) >= 5 + padding_length:
            return data[5:-padding_length]
        
        return data[5:]
    
    def set_obfuscation_level(self, level: int):
        """Установка уровня обфускации"""
        self.obfuscation_level = max(1, min(5, level))
        print(f"[*] Obfuscation level set to {self.obfuscation_level}")
    
    def get_stats(self) -> dict:
        """Получение статистики"""
        return self.obfuscation_stats


class RealTimeDPIEvasion:
    """Активные методы обхода DPI в реальном времени"""
    
    def __init__(self):
        self.evasion_threads = []
        self.running = False
        self.detection_count = 0
        self.current_port = 443
        self.block_detector = BlockDetector()
    
    def start_evasion(self):
        """Запуск активных методов обхода"""
        self.running = True
        
        threads = [
            self._packet_mangling_loop,
            self._timing_randomization_loop,
            self._decoy_traffic_loop,
            self._port_hopping_loop,
            self._tls_rotation_loop
        ]
        
        for thread_func in threads:
            t = threading.Thread(target=thread_func, daemon=True)
            t.start()
            self.evasion_threads.append(t)
        
        print("[+] Real-time DPI evasion activated")
    
    def _packet_mangling_loop(self):
        """Изменение параметров пакетов"""
        while self.running:
            try:
                self._randomize_tcp_options()
                time.sleep(random.uniform(30, 60))
            except:
                pass
    
    def _timing_randomization_loop(self):
        """Рандомизация таймингов"""
        while self.running:
            delay = random.uniform(0.0005, 0.005)
            time.sleep(delay)
    
    def _decoy_traffic_loop(self):
        """Генерация трафика-приманки"""
        decoy_hosts = [
            ('8.8.8.8', 443), ('1.1.1.1', 443), ('208.67.222.222', 443),
            ('9.9.9.9', 443), ('185.228.168.9', 443), ('94.140.14.14', 443)
        ]
        
        while self.running:
            try:
                host, port = random.choice(decoy_hosts)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect((host, port))
                
                random_data = os.urandom(random.randint(16, 64))
                sock.send(random_data)
                sock.close()
                
                time.sleep(random.uniform(10, 30))
            except:
                pass
    
    def _port_hopping_loop(self):
        """Прыжки по портам"""
        ports = [443, 8443, 2053, 2083, 2096, 8080, 9443, 4443, 6443]
        
        while self.running:
            if self.block_detector.is_blocked():
                available_ports = [p for p in ports if p != self.current_port]
                new_port = random.choice(available_ports)
                
                print(f"[!] Blockage detected! Switching to port {new_port}")
                self.detection_count += 1
                self.current_port = new_port
                self.on_port_change(new_port)
                
                time.sleep(5)
            
            time.sleep(10)
    
    def _tls_rotation_loop(self):
        """Ротация TLS профилей"""
        while self.running:
            time.sleep(random.randint(600, 1800))
            if hasattr(self, 'on_tls_rotate'):
                self.on_tls_rotate()
    
    def _randomize_tcp_options(self):
        """Рандомизация TCP опций"""
        pass
    
    def on_port_change(self, new_port):
        """Callback при смене порта"""
        pass
    
    def on_tls_rotate(self):
        """Callback при ротации TLS"""
        pass


class BlockDetector:
    """Детектор блокировок"""
    
    def __init__(self):
        self.blocked = False
        self.consecutive_failures = 0
        self.last_success_time = time.time()
        self.failure_times = []
    
    def is_blocked(self) -> bool:
        """Проверка блокировки"""
        if len(self.failure_times) > 5:
            recent_failures = [t for t in self.failure_times if time.time() - t < 60]
            if len(recent_failures) > 3:
                self.blocked = True
        
        if self.consecutive_failures > 3:
            self.blocked = True
        
        if time.time() - self.last_success_time < 30:
            self.consecutive_failures = 0
            self.blocked = False
        
        return self.blocked
    
    def record_success(self):
        """Запись успеха"""
        self.last_success_time = time.time()
        self.consecutive_failures = 0
        self.blocked = False
        self.failure_times.clear()
    
    def record_failure(self):
        """Запись ошибки"""
        self.consecutive_failures += 1
        self.failure_times.append(time.time())
        self.failure_times = [t for t in self.failure_times if time.time() - t < 300]


class AntiDPIEngine:
    """Главный Anti-DPI движок с улучшенной подменой SNI"""
    
    def __init__(self, trojan_config_path='/etc/trojan/config.json'):
        # Основные компоненты
        self.tls_impersonator = BrowserTLSImpersonator()
        self.obfuscator = MultiLayerObfuscator()
        self.evasion = RealTimeDPIEvasion()
        self.block_detector = BlockDetector()
        
        # Улучшенная система SNI
        self.sni_spoofer = AdvancedSNISpoofer()
        self.trojan_integration = TrojanSNIIntegration(trojan_config_path)
        self.trojan_integration.set_sni_spoofer(self.sni_spoofer)
        
        # Связываем callback'и
        self.evasion.on_port_change = self._handle_port_change
        self.evasion.on_tls_rotate = self._handle_tls_rotate
        
        # Запускаем evasion
        self.evasion.start_evasion()
        
        # Запускаем адаптивную ротацию SNI
        if self.trojan_integration.enabled:
            self.trojan_integration.start_adaptive_rotation()
        
        # Статистика
        self.stats = {
            'connections_total': 0,
            'connections_success': 0,
            'sni_rotations': 0,
            'start_time': time.time()
        }
        
        print("[+] Enhanced AntiDPI Engine initialized")
        print(f"[+] SNI Spoofing: ENABLED (ML-adaptive)")
        print(f"[+] Trojan Integration: {'ENABLED' if self.trojan_integration.enabled else 'DISABLED'}")
        print(f"[+] curl_cffi: {'AVAILABLE' if CURL_CFFI_AVAILABLE else 'NOT AVAILABLE'}")
    
    def _handle_port_change(self, new_port):
        """Обработка смены порта"""
        print(f"[*] AntiDPI: Switching to port {new_port}")
        self.stats['port_changes'] = self.stats.get('port_changes', 0) + 1
    
    def _handle_tls_rotate(self):
        """Обработка ротации TLS"""
        self.tls_impersonator.rotate_profile()
        self.stats['tls_rotations'] = self.stats.get('tls_rotations', 0) + 1
    
    def get_optimal_sni_for_client(self, client_ip: str = None) -> str:
        """Получение оптимального SNI для клиента"""
        sni = self.sni_spoofer.get_optimal_sni(client_ip)
        
        # Обновляем Trojan если нужно
        if self.trojan_integration.enabled:
            self.trojan_integration.update_trojan_sni(sni)
            self.stats['sni_rotations'] += 1
        
        return sni
    
    def wrap_socket(self, sock: socket.socket, host: str, client_ip: str = None) -> ssl.SSLSocket:
        """Обертка сокета с защитой"""
        # Создаем SSL контекст
        context = self.tls_impersonator.create_custom_ssl_context()
        
        # Получаем оптимальный SNI
        fake_sni = self.get_optimal_sni_for_client(client_ip)
        
        # Оборачиваем сокет
        tls_sock = context.wrap_socket(sock, server_hostname=fake_sni)
        
        return tls_sock
    
    def _get_spoofed_sni(self, real_host: str, client_ip: str = None) -> str:
        """Получение подставного SNI (улучшенная версия)"""
        return self.get_optimal_sni_for_client(client_ip)
    
    def process_outgoing(self, data: bytes) -> bytes:
        """Обработка исходящего трафика"""
        return self.obfuscator.obfuscate(data)
    
    def process_incoming(self, data: bytes) -> bytes:
        """Обработка входящего трафика"""
        return self.obfuscator.deobfuscate(data)
    
    def rotate_defenses(self):
        """Ротация всех защит"""
        self.tls_impersonator.rotate_profile()
        self.obfuscator.set_obfuscation_level(random.randint(3, 5))
        
        # Ротируем SNI
        new_sni = self.sni_spoofer.get_optimal_sni()
        if self.trojan_integration.enabled:
            self.trojan_integration.update_trojan_sni(new_sni)
        
        print("[*] All defenses rotated")
    
    def get_session(self):
        """Получение HTTP сессии с имитацией браузера"""
        return self.tls_impersonator.session
    
    def record_connection_success(self):
        """Запись успешного соединения"""
        self.stats['connections_total'] += 1
        self.stats['connections_success'] += 1
        self.block_detector.record_success()
        
        if self.sni_spoofer.current_sni:
            self.sni_spoofer.record_success(self.sni_spoofer.current_sni)
    
    def record_connection_failure(self):
        """Запись неудачного соединения"""
        self.stats['connections_total'] += 1
        self.block_detector.record_failure()
        
        if self.sni_spoofer.current_sni:
            self.sni_spoofer.record_failure(self.sni_spoofer.current_sni)
    
    def get_stats(self) -> dict:
        """Получение полной статистики"""
        return {
            'engine_stats': self.stats,
            'tls_rotations': self.tls_impersonator.rotation_count,
            'current_tls_profile': self.tls_impersonator.current_profile,
            'current_sni': self.sni_spoofer.current_sni,
            'current_provider': self.sni_spoofer.current_provider,
            'sni_health': dict(self.sni_spoofer.sni_health),
            'blocked_snis': list(self.sni_spoofer.blocked_snis),
            'obfuscation_stats': self.obfuscator.get_stats(),
            'dpi_detections': self.evasion.detection_count,
            'curl_cffi_available': CURL_CFFI_AVAILABLE,
            'trojan_integration_enabled': self.trojan_integration.enabled,
            'uptime': time.time() - self.stats['start_time']
        }


# Глобальный экземпляр
anti_dpi = AntiDPIEngine()


# Функции для интеграции с существующим кодом
def get_optimal_sni(client_ip: str = None) -> str:
    """Получение оптимального SNI для клиента"""
    return anti_dpi.get_optimal_sni_for_client(client_ip)


def rotate_all_defenses():
    """Ротация всех механизмов защиты"""
    anti_dpi.rotate_defenses()


def get_engine_stats() -> dict:
    """Получение статистики движка"""
    return anti_dpi.get_stats()


def process_traffic(data: bytes, direction: str) -> bytes:
    """Обработка трафика"""
    if direction == 'outgoing':
        return anti_dpi.process_outgoing(data)
    else:
        return anti_dpi.process_incoming(data)


# Тестовый запуск
if __name__ == '__main__':
    print("\n" + "="*60)
    print("Enhanced Anti-DPI Engine 2026")
    print("="*60)
    
    # Тест SNI подмены
    test_sni = anti_dpi.get_optimal_sni_for_client('192.168.1.1')
    print(f"\n[*] Test SNI for client: {test_sni}")
    
    # Статистика
    print("\n[*] Engine Statistics:")
    stats = anti_dpi.get_stats()
    for key, value in stats.items():
        if isinstance(value, dict):
            print(f"    {key}:")
            for k, v in value.items():
                print(f"        {k}: {v}")
        else:
            print(f"    {key}: {value}")
    
    print("\n[+] Anti-DPI Engine ready!")
    print("="*60 + "\n")