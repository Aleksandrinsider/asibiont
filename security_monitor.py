"""
Security monitoring and rate limiting system
Отслеживает аномальную активность пользователей для защиты от атак
"""

import logging
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional
import json

logger = logging.getLogger(__name__)


@dataclass
class RequestLog:
    """Лог запроса пользователя"""
    timestamp: datetime
    ip: str
    user_agent: str
    path: str
    method: str
    user_id: Optional[int] = None
    status_code: Optional[int] = None


class SecurityMonitor:
    """Мониторинг безопасности и защита от атак"""
    
    def __init__(self):
        # Хранилище логов за последние 10 минут
        self.request_logs: List[RequestLog] = []
        
        # Счетчики запросов по IP за разные интервалы
        self.requests_per_ip: Dict[str, List[datetime]] = defaultdict(list)
        
        # Заблокированные IP
        self.blocked_ips: Dict[str, datetime] = {}
        
        # Подозрительные IP
        self.suspicious_ips: Dict[str, int] = defaultdict(int)
        
        # Лимиты запросов
        self.RATE_LIMITS = {
            'per_second': 10,      # Макс 10 запросов в секунду
            'per_minute': 100,     # Макс 100 запросов в минуту
            'per_10_minutes': 500  # Макс 500 запросов за 10 минут
        }
        
        # Время блокировки
        self.BLOCK_DURATION = timedelta(hours=1)
        
        # Автоматическая очистка старых логов
        self.last_cleanup = datetime.now()
        
    def log_request(self, ip: str, user_agent: str, path: str, method: str, 
                   user_id: Optional[int] = None, status_code: Optional[int] = None):
        """Логирование запроса"""
        now = datetime.now()
        
        # Создаем лог запроса
        log_entry = RequestLog(
            timestamp=now,
            ip=ip,
            user_agent=user_agent,
            path=path,
            method=method,
            user_id=user_id,
            status_code=status_code
        )
        
        self.request_logs.append(log_entry)
        self.requests_per_ip[ip].append(now)
        
        # Периодическая очистка старых данных
        if (now - self.last_cleanup).total_seconds() > 300:  # Каждые 5 минут
            self.cleanup_old_data()
            self.last_cleanup = now
        
        # Проверка на превышение лимитов
        if self.is_rate_limited(ip):
            self.suspicious_ips[ip] += 1
            logger.warning(f"🚨 RATE LIMIT EXCEEDED: IP {ip} | Path: {path} | Suspicious count: {self.suspicious_ips[ip]}")
            
            # Блокировка при многократном превышении
            if self.suspicious_ips[ip] >= 5:
                self.block_ip(ip)
                logger.error(f"🔒 IP BLOCKED: {ip} due to repeated rate limit violations")
            
            return False
        
        return True
    
    def is_rate_limited(self, ip: str) -> bool:
        """Проверка на превышение лимитов запросов"""
        now = datetime.now()
        requests = self.requests_per_ip[ip]
        
        # Удаляем старые запросы (старше 10 минут)
        requests = [ts for ts in requests if (now - ts).total_seconds() < 600]
        self.requests_per_ip[ip] = requests
        
        # Проверка лимита за последнюю секунду
        last_second = [ts for ts in requests if (now - ts).total_seconds() < 1]
        if len(last_second) > self.RATE_LIMITS['per_second']:
            return True
        
        # Проверка лимита за последнюю минуту
        last_minute = [ts for ts in requests if (now - ts).total_seconds() < 60]
        if len(last_minute) > self.RATE_LIMITS['per_minute']:
            return True
        
        # Проверка лимита за последние 10 минут
        if len(requests) > self.RATE_LIMITS['per_10_minutes']:
            return True
        
        return False
    
    def is_blocked(self, ip: str) -> bool:
        """Проверка, заблокирован ли IP"""
        if ip in self.blocked_ips:
            block_time = self.blocked_ips[ip]
            if datetime.now() - block_time < self.BLOCK_DURATION:
                return True
            else:
                # Разблокировка после истечения времени
                del self.blocked_ips[ip]
                self.suspicious_ips[ip] = 0
                logger.info(f"🔓 IP UNBLOCKED: {ip} (block duration expired)")
        return False
    
    def block_ip(self, ip: str):
        """Блокировка IP"""
        self.blocked_ips[ip] = datetime.now()
    
    def cleanup_old_data(self):
        """Очистка старых логов"""
        cutoff_time = datetime.now() - timedelta(minutes=10)
        
        # Очистка логов
        self.request_logs = [log for log in self.request_logs if log.timestamp > cutoff_time]
        
        # Очистка счетчиков запросов
        for ip in list(self.requests_per_ip.keys()):
            self.requests_per_ip[ip] = [ts for ts in self.requests_per_ip[ip] if ts > cutoff_time]
            if not self.requests_per_ip[ip]:
                del self.requests_per_ip[ip]
    
    def get_statistics(self) -> dict:
        """Получение статистики по безопасности"""
        now = datetime.now()
        
        # Топ-10 IP по количеству запросов
        top_ips = sorted(
            [(ip, len(timestamps)) for ip, timestamps in self.requests_per_ip.items()],
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        # Статистика по эндпоинтам
        endpoint_stats = defaultdict(int)
        for log in self.request_logs:
            endpoint_stats[log.path] += 1
        
        top_endpoints = sorted(endpoint_stats.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Последние логи
        recent_logs = sorted(self.request_logs, key=lambda x: x.timestamp, reverse=True)[:50]
        
        return {
            'total_requests': len(self.request_logs),
            'unique_ips': len(self.requests_per_ip),
            'blocked_ips': len(self.blocked_ips),
            'suspicious_ips': len([ip for ip, count in self.suspicious_ips.items() if count > 0]),
            'top_ips': [{'ip': ip, 'requests': count} for ip, count in top_ips],
            'top_endpoints': [{'path': path, 'requests': count} for path, count in top_endpoints],
            'blocked_ips_list': list(self.blocked_ips.keys()),
            'recent_logs': [
                {
                    'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    'ip': log.ip,
                    'method': log.method,
                    'path': log.path,
                    'user_agent': log.user_agent[:50] if log.user_agent else 'Unknown',
                    'status': log.status_code
                }
                for log in recent_logs
            ]
        }
    
    def get_ip_info(self, ip: str) -> dict:
        """Получение детальной информации об IP"""
        requests = self.requests_per_ip.get(ip, [])
        logs = [log for log in self.request_logs if log.ip == ip]
        
        return {
            'ip': ip,
            'total_requests': len(requests),
            'is_blocked': self.is_blocked(ip),
            'suspicious_count': self.suspicious_ips.get(ip, 0),
            'recent_requests': [
                {
                    'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    'method': log.method,
                    'path': log.path,
                    'status': log.status_code
                }
                for log in sorted(logs, key=lambda x: x.timestamp, reverse=True)[:20]
            ]
        }


# Глобальный экземпляр монитора
security_monitor = SecurityMonitor()
