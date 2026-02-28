"""
service_health.py — глобальный реестр ошибок внешних сервисов.

Агент может вызвать get_system_status() чтобы узнать текущее состояние сервисов
и грамотно объяснить пользователю почему что-то не работает.

Используется прозрачно: компоненты вызывают record_error() / clear_error(),
агент читает через get_status() / format_for_agent().
"""

import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ─── Реестр ─────────────────────────────────────────────────────────────────
# { service_name: { status, code, message, detail, blocked_until, last_error_ts, error_count } }
_registry: Dict[str, Dict[str, Any]] = {}

# Человекочитаемые названия сервисов и что они делают
_SERVICE_LABELS = {
    'newsapi':   'Новостная лента (NewsAPI)',
    'ddg':       'Веб-поиск (DuckDuckGo)',
    'deepseek':  'AI-модель (DeepSeek)',
    'resend':    'Отправка email (Resend)',
    'openweathermap': 'Погода (OpenWeatherMap)',
    'github':    'Поиск контактов GitHub',
    'payments':  'Платёжная система (YooKassa)',
    'telegram':  'Telegram API',
    'redis':     'Кэш (Redis)',
    'database':  'База данных',
}

# Советы по каждому сервису — что сказать пользователю
_RECOVERY_HINTS = {
    'newsapi': (
        'NewsAPI исчерпала дневной лимит запросов. '
        'Новости сейчас загружаются через DuckDuckGo — качество чуть ниже. '
        'Лимит сбросится через ~12 часов.'
    ),
    'ddg': (
        'DuckDuckGo временно недоступен (возможно блокировка IP или перегрузка). '
        'Попробуй немного позже — обычно восстанавливается за несколько минут.'
    ),
    'deepseek': (
        'AI-модель DeepSeek временно не отвечает или исчерпан лимит токенов. '
        'Попробуй повторить запрос через 1-2 минуты.'
    ),
    'resend': (
        'Сервис отправки email (Resend) вернул ошибку. '
        'Возможные причины: исчерпан дневной лимит отправки, домен не верифицирован, '
        'или проблема с API ключом. Проверь настройки на resend.com.'
    ),
    'openweathermap': (
        'Сервис погоды временно недоступен. Попробуй позже.'
    ),
    'github': (
        'GitHub API временно ограничил запросы (rate limit). '
        'Поиск email контактов восстановится через ~1 час.'
    ),
    'payments': (
        'Платёжная система (YooKassa) вернула ошибку. '
        'Проверь настройки YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY в конфигурации.'
    ),
    'telegram': (
        'Telegram Bot API вернул ошибку. '
        'Возможные причины: бот заблокирован пользователем, превышен rate limit, '
        'или проблема с токеном бота.'
    ),
    'redis': (
        'Redis недоступен — работаем без кэша (in-memory). '
        'Это может замедлить ответы при повторных запросах, но всё остальное работает.'
    ),
    'database': (
        'Проблема с базой данных. Это серьёзная ошибка — обратись к администратору.'
    ),
}


def record_error(
    service: str,
    message: str,
    code: Optional[int] = None,
    detail: Optional[str] = None,
    blocked_until: Optional[float] = None,
):
    """Зафиксировать ошибку сервиса.

    Args:
        service: Имя сервиса ('newsapi', 'resend', 'deepseek', ...)
        message: Краткое описание ошибки
        code: HTTP-код или код ошибки (429, 403, 500, ...)
        detail: Подробности (тело ответа, traceback)
        blocked_until: Unix-timestamp до которого сервис заблокирован
    """
    prev = _registry.get(service, {})
    _registry[service] = {
        'status': 'error',
        'code': code,
        'message': message,
        'detail': (detail or '')[:500],
        'blocked_until': blocked_until,
        'last_error_ts': time.time(),
        'error_count': prev.get('error_count', 0) + 1,
    }
    logger.debug(f"[SERVICE_HEALTH] {service} → error: {message}")


def clear_error(service: str):
    """Отметить сервис как восстановленный (после успешного вызова)."""
    if service in _registry and _registry[service].get('status') == 'error':
        _registry[service] = {
            'status': 'ok',
            'code': None,
            'message': None,
            'detail': None,
            'blocked_until': None,
            'last_error_ts': None,
            'error_count': 0,
        }
        logger.debug(f"[SERVICE_HEALTH] {service} → recovered")


def get_status() -> Dict[str, Dict]:
    """Вернуть текущее состояние всех сервисов с ошибками."""
    now = time.time()
    result = {}
    for svc, info in _registry.items():
        if info.get('status') != 'ok':
            # Проверяем не истёк ли backoff
            bu = info.get('blocked_until')
            if bu and now >= bu:
                result[svc] = {**info, 'status': 'recovering', 'blocked_until': None}
            else:
                result[svc] = info
    return result


def format_for_agent(user_id: int = None) -> str:
    """Форматировать статус сервисов для AI-агента.

    Returns:
        Строка с описанием текущих проблем. Пустая строка если всё ок.
    """
    now = time.time()
    issues = []

    for svc, info in _registry.items():
        if info.get('status') == 'ok':
            continue

        bu = info.get('blocked_until')
        if bu and now >= bu:
            # Backoff истёк — больше не блокируем
            continue

        label = _SERVICE_LABELS.get(svc, svc)
        hint = _RECOVERY_HINTS.get(svc, '')
        code_str = f" (код {info['code']})" if info.get('code') else ''
        msg = info.get('message', 'неизвестная ошибка')

        remaining_min = None
        if bu:
            remaining_min = max(1, int((bu - now) / 60))

        line = f"• {label}: {msg}{code_str}"
        if remaining_min:
            line += f" — восстановится через ~{remaining_min} мин"
        if hint:
            line += f"\n  ℹ️ {hint}"

        issues.append(line)

    if not issues:
        return ''

    return "⚠️ Активные проблемы с сервисами:\n" + "\n".join(issues)


def get_all_services_report(user_id: int = None) -> dict:
    """Полный отчёт для инструмента get_system_status.

    Returns структуру которую агент получает как результат tool call.
    """
    now = time.time()
    services = {}

    # Собираем все известные сервисы
    all_services = set(_SERVICE_LABELS.keys()) | set(_registry.keys())
    for svc in all_services:
        info = _registry.get(svc, {})
        status = info.get('status', 'ok')
        bu = info.get('blocked_until')

        # Backoff истёк → фактически ok
        if bu and now >= bu:
            status = 'ok'

        label = _SERVICE_LABELS.get(svc, svc)
        entry = {
            'label': label,
            'status': status,
        }

        if status != 'ok':
            entry['message'] = info.get('message', '')
            entry['code'] = info.get('code')
            entry['hint'] = _RECOVERY_HINTS.get(svc, '')
            if bu and now < bu:
                entry['blocked_for_min'] = max(1, int((bu - now) / 60))
            entry['error_count'] = info.get('error_count', 1)

        services[svc] = entry

    # Дополнительно: квоты email (нужен user_id для персонального лимита)
    email_quota = _get_email_quota(user_id) if user_id else None

    has_issues = any(s.get('status') != 'ok' for s in services.values())

    return {
        'overall': 'degraded' if has_issues else 'ok',
        'services': services,
        'email_quota': email_quota,
        'summary': format_for_agent() or 'Все сервисы работают нормально ✅',
    }


def _get_email_quota(user_id: int) -> Optional[dict]:
    """Получить статус дневной квоты email для пользователя."""
    try:
        from models import Session, User, EmailOutreach
        from sqlalchemy import func, distinct
        from datetime import datetime, timezone
        import pytz

        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return None

            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = session.query(
                func.count(distinct(EmailOutreach.recipient_email))
            ).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.sent_at >= today_start,
                EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
            ).scalar() or 0

            DAILY_LIMIT = 50
            return {
                'sent_today': sent_today,
                'daily_limit': DAILY_LIMIT,
                'remaining': max(0, DAILY_LIMIT - sent_today),
                'exhausted': sent_today >= DAILY_LIMIT,
            }
        finally:
            session.close()
    except Exception as e:
        logger.debug(f"[SERVICE_HEALTH] email quota check failed: {e}")
        return None
