# tcp_fragmenter.py
"""
МОДУЛЬ ФРАГМЕНТАЦИИ TCP ПАКЕТОВ ДЛЯ ОБХОДА DPI
Реализует методы фрагментации для обхода систем глубокого анализа пакетов
"""

import random
import struct
import time
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

class FragmentStrategy(Enum):
    """Стратегии фрагментации"""
    NONE = 0                     # Без фрагментации
    SMALL_CHUNKS = 1             # Мелкие куски (1-50 байт)
    MEDIUM_CHUNKS = 2            # Средние куски (50-200 байт)
    RANDOM_SIZE = 3              # Случайные размеры
    TIMING_BASED = 4             # Фрагментация с задержками
    OUT_OF_ORDER = 5             # Переупорядоченные фрагменты
    DUPLICATE_FIRST = 6          # Дублирование первого пакета
    HEADER_SPLIT = 7             # Разделение HTTP-заголовков

@dataclass
class Fragment:
    """Фрагмент пакета"""
    data: bytes
    sequence: int
    delay_ms: float = 0.0
    is_last: bool = False

class TCPFragmenter:
    """
    Класс для фрагментации TCP пакетов
    Использует различные техники для обхода DPI
    """
    
    def __init__(self, strategy: FragmentStrategy = FragmentStrategy.RANDOM_SIZE):
        self.strategy = strategy
        self.mtu = 1500
        self.min_fragment_size = 1
        self.max_fragment_size = 200
        
        # Настройки для разных стратегий
        self.small_chunk_sizes = [1, 2, 4, 8, 16, 32, 64]
        self.medium_chunk_sizes = [50, 100, 150, 200, 250, 300]
        
        # Для обхода TLS fingerprint
        self.tls_record_sizes = [64, 128, 256, 512, 1024, 2048]
        
        # Задержки между фрагментами (микросекунды)
        self.delays_us = [0, 100, 200, 500, 1000, 2000, 5000]
        
    def fragment_data(self, data: bytes) -> List[Fragment]:
        """
        Фрагментация данных согласно выбранной стратегии
        """
        if not data:
            return []
        
        if self.strategy == FragmentStrategy.NONE:
            return [Fragment(data=data, sequence=0, is_last=True)]
        
        elif self.strategy == FragmentStrategy.SMALL_CHUNKS:
            return self._fragment_small_chunks(data)
        
        elif self.strategy == FragmentStrategy.MEDIUM_CHUNKS:
            return self._fragment_medium_chunks(data)
        
        elif self.strategy == FragmentStrategy.RANDOM_SIZE:
            return self._fragment_random_sizes(data)
        
        elif self.strategy == FragmentStrategy.TIMING_BASED:
            return self._fragment_with_timing(data)
        
        elif self.strategy == FragmentStrategy.OUT_OF_ORDER:
            return self._fragment_out_of_order(data)
        
        elif self.strategy == FragmentStrategy.DUPLICATE_FIRST:
            return self._fragment_with_duplicate(data)
        
        elif self.strategy == FragmentStrategy.HEADER_SPLIT:
            return self._fragment_headers(data)
        
        else:
            return [Fragment(data=data, sequence=0, is_last=True)]
    
    def _fragment_small_chunks(self, data: bytes) -> List[Fragment]:
        """Фрагментация на очень мелкие куски (обходит DPI, ищущие паттерны)"""
        fragments = []
        offset = 0
        seq = 0
        
        while offset < len(data):
            chunk_size = random.choice(self.small_chunk_sizes)
            chunk_size = min(chunk_size, len(data) - offset)
            
            if chunk_size > 0:
                fragments.append(Fragment(
                    data=data[offset:offset + chunk_size],
                    sequence=seq,
                    delay_ms=0
                ))
                offset += chunk_size
                seq += 1
        
        if fragments:
            fragments[-1].is_last = True
        
        return fragments
    
    def _fragment_medium_chunks(self, data: bytes) -> List[Fragment]:
        """Фрагментация на средние куски"""
        fragments = []
        offset = 0
        seq = 0
        
        while offset < len(data):
            chunk_size = random.choice(self.medium_chunk_sizes)
            chunk_size = min(chunk_size, len(data) - offset)
            
            if chunk_size > 0:
                fragments.append(Fragment(
                    data=data[offset:offset + chunk_size],
                    sequence=seq
                ))
                offset += chunk_size
                seq += 1
        
        if fragments:
            fragments[-1].is_last = True
        
        return fragments
    
    def _fragment_random_sizes(self, data: bytes) -> List[Fragment]:
        """Фрагментация со случайными размерами (наиболее непредсказуемая)"""
        fragments = []
        offset = 0
        seq = 0
        
        while offset < len(data):
            # Случайный размер от 1 до 200 байт
            chunk_size = random.randint(self.min_fragment_size, self.max_fragment_size)
            chunk_size = min(chunk_size, len(data) - offset)
            
            # Иногда делаем очень маленькие пакеты
            if random.random() < 0.1:  # 10% шанс на супер-маленький пакет
                chunk_size = min(4, chunk_size)
            
            if chunk_size > 0:
                fragments.append(Fragment(
                    data=data[offset:offset + chunk_size],
                    sequence=seq
                ))
                offset += chunk_size
                seq += 1
        
        if fragments:
            fragments[-1].is_last = True
        
        return fragments
    
    def _fragment_with_timing(self, data: bytes) -> List[Fragment]:
        """Фрагментация с временными задержками между пакетами"""
        fragments = []
        offset = 0
        seq = 0
        
        while offset < len(data):
            chunk_size = random.randint(50, 150)
            chunk_size = min(chunk_size, len(data) - offset)
            
            # Случайная задержка
            delay = random.choice(self.delays_us) / 1000.0  # конвертация в мс
            
            if chunk_size > 0:
                fragments.append(Fragment(
                    data=data[offset:offset + chunk_size],
                    sequence=seq,
                    delay_ms=delay
                ))
                offset += chunk_size
                seq += 1
        
        if fragments:
            fragments[-1].is_last = True
        
        return fragments
    
    def _fragment_out_of_order(self, data: bytes) -> List[Fragment]:
        """Фрагментация с нарушением порядка пакетов"""
        fragments = self._fragment_medium_chunks(data)
        
        if len(fragments) > 2:
            # Перемешиваем средние фрагменты
            middle = fragments[1:-1]
            random.shuffle(middle)
            fragments = [fragments[0]] + middle + [fragments[-1]]
        
        return fragments
    
    def _fragment_with_duplicate(self, data: bytes) -> List[Fragment]:
        """Фрагментация с дублированием первого пакета (обманывает некоторые DPI)"""
        fragments = self._fragment_small_chunks(data)
        
        if fragments:
            # Дублируем первый фрагмент
            first_copy = Fragment(
                data=fragments[0].data,
                sequence=-1,  # Отрицательный номер для дубликата
                delay_ms=0
            )
            fragments.insert(0, first_copy)
        
        return fragments
    
    def _fragment_headers(self, data: bytes) -> List[Fragment]:
        """Специальная фрагментация для HTTP/HTTPS заголовков"""
        fragments = []
        
        # Ищем HTTP заголовки
        if b'\r\n\r\n' in data:
            header_end = data.find(b'\r\n\r\n') + 4
            headers = data[:header_end]
            body = data[header_end:]
            
            # Фрагментируем заголовки побайтово
            for i, byte in enumerate(headers):
                fragments.append(Fragment(
                    data=bytes([byte]),
                    sequence=i,
                    delay_ms=0.5 if i > 0 else 0  # небольшая задержка
                ))
            
            # Фрагментируем тело
            body_fragments = self._fragment_medium_chunks(body)
            for frag in body_fragments:
                frag.sequence = len(fragments) + frag.sequence
                fragments.append(frag)
        else:
            # Нет заголовков, используем стандартную фрагментацию
            return self._fragment_random_sizes(data)
        
        return fragments


class GoodbyeDPIFragmenter:
    """
    Расширенный фрагментатор для обхода DPI
    Комбинирует несколько техник для максимальной эффективности
    """
    
    def __init__(self):
        self.fragmenters = {
            'http': TCPFragmenter(FragmentStrategy.HEADER_SPLIT),
            'tls': TCPFragmenter(FragmentStrategy.SMALL_CHUNKS),
            'trojan': TCPFragmenter(FragmentStrategy.RANDOM_SIZE),
            'mixed': TCPFragmenter(FragmentStrategy.TIMING_BASED)
        }
        
        # Статистика
        self.stats = {
            'total_fragmented': 0,
            'total_bytes': 0,
            'fragment_count': 0,
            'avg_fragment_size': 0
        }
        
    def fragment_for_dpi_bypass(self, data: bytes, protocol: str = 'trojan') -> Tuple[List[bytes], List[float]]:
        """
        Фрагментирует данные для обхода DPI
        
        Args:
            data: Исходные данные
            protocol: Тип протокола ('http', 'tls', 'trojan', 'mixed')
        
        Returns:
            Tuple[List[bytes], List[float]]: (список фрагментов, список задержек)
        """
        if not data:
            return [], []
        
        # Выбираем фрагментатор
        fragmenter = self.fragmenters.get(protocol, self.fragmenters['trojan'])
        
        # Фрагментируем
        fragments = fragmenter.fragment_data(data)
        
        # Обновляем статистику
        self.stats['total_fragmented'] += 1
        self.stats['total_bytes'] += len(data)
        self.stats['fragment_count'] += len(fragments)
        self.stats['avg_fragment_size'] = self.stats['total_bytes'] / max(1, self.stats['fragment_count'])
        
        # Извлекаем данные и задержки
        fragment_data = [f.data for f in fragments]
        delays = [f.delay_ms for f in fragments]
        
        return fragment_data, delays
    
    def send_fragmented(self, sock, data: bytes, protocol: str = 'trojan'):
        """
        Отправка фрагментированных данных через сокет
        """
        fragments, delays = self.fragment_for_dpi_bypass(data, protocol)
        
        for i, (fragment, delay) in enumerate(zip(fragments, delays)):
            try:
                sock.send(fragment)
                if delay > 0:
                    time.sleep(delay / 1000.0)  # конвертация в секунды
            except Exception as e:
                print(f"[-] Ошибка отправки фрагмента {i}: {e}")
                return False
        
        return True
    
    def get_stats(self) -> dict:
        """Получение статистики фрагментации"""
        return {
            **self.stats,
            'fragmentation_ratio': self.stats['fragment_count'] / max(1, self.stats['total_fragmented'])
        }


class AdaptiveDPIFragmenter:
    """
    Адаптивный фрагментатор, который подбирает оптимальную стратегию
    на основе ответов сервера
    """
    
    def __init__(self):
        self.strategies = list(FragmentStrategy)
        self.current_strategy = FragmentStrategy.RANDOM_SIZE
        self.success_rate = {s: 1.0 for s in self.strategies}
        self.attempts = {s: 0 for s in self.strategies}
        
    def try_strategy(self, data: bytes, strategy: FragmentStrategy) -> bool:
        """
        Тестирование стратегии на сервере
        """
        fragmenter = TCPFragmenter(strategy)
        fragments = fragmenter.fragment_data(data)
        
        # Симуляция отправки и проверки ответа
        # В реальном использовании здесь будет проверка ответа сервера
        
        return True  # Заглушка
    
    def select_best_strategy(self, data: bytes) -> FragmentStrategy:
        """
        Выбор лучшей стратегии на основе истории успехов
        """
        # Находим стратегию с наибольшим рейтингом успеха
        best_strategy = max(self.success_rate, key=self.success_rate.get)
        return best_strategy
    
    def update_success(self, strategy: FragmentStrategy, success: bool):
        """
        Обновление рейтинга успеха стратегии
        """
        self.attempts[strategy] += 1
        
        if success:
            self.success_rate[strategy] = (self.success_rate[strategy] * 0.9) + 0.1
        else:
            self.success_rate[strategy] = self.success_rate[strategy] * 0.9