"""
Living Office Engine — агенты живут своей жизнью.

Уровень 1 (Мониторинг):
  Каждые 25-45 мин запускает python_code каждого активного агента в фоне.
  Если скрипт вернул что-то интересное — создаёт integration_alert якорь.
  AnchorEngine сам доставит пользователю с умным throttling.

Уровень 2 (Координатор):
  Каждые 2-4 часа АСИ смотрит на открытые цели пользователя + возможности агентов.
  Назначает конкретное действие одному агенту, создаёт agent_office_update якорь.
  Пользователь получает уведомление: «Кристина готова сделать X для цели Y».

Якоря:
  - integration_alert  (уже есть в AnchorEngine) — для фоновых скриптов
  - agent_office_update (новый)                  — для координаторских заданий
"""

import asyncio
import json
import logging
import os
import random
import subprocess
import sys
from datetime import datetime, timezone, timedelta

try:
    from ai_integration.autonomous_agent import _build_user_context_sync, _parse_agent_integrations
except Exception:  # циклический импорт или ещё не загружен — lazy fallback
    _build_user_context_sync = None  # type: ignore
    _parse_agent_integrations = None  # type: ignore

logger = logging.getLogger(__name__)

# ── Интервалы ────────────────────────────────────────────────────────────────
MONITOR_INTERVAL_SEC = (25 * 60, 45 * 60)   # legacy, сохранён для совместимости
L1_TICK_SEC = 15 * 60                        # тик планировщика L1: каждые 15 мин проверяем due-агентов
L1_DEFAULT_INTERVAL_SEC = 60 * 60           # дефолтный интервал агента если run_interval_minutes не задан
OFFICE_INTERVAL_SEC  = (30 * 60, 60 * 60) # 30-60 мин между координаторскими сессиями
from config import API_TIMEOUT_SCRIPT
SCRIPT_TIMEOUT_SEC   = API_TIMEOUT_SCRIPT    # таймаут на один скрипт агента

# ── Дедупликация stdout: если скрипт вернул то же самое — не обрабатываем ─────
_STDOUT_DEDUP: dict[tuple, str] = {}  # (user_id, agent_id) → hash последнего stdout


# ── Telegram bot reference (инъектируется из main.py) ───────────────────────

_tg_bot = None  # aiogram Bot, задаётся через set_tg_bot()


def set_tg_bot(b):
    """Инжектирует ссылку на aiogram Bot, чтобы OfficeEngine мог слать сообщения в Telegram."""
    global _tg_bot
    _tg_bot = b


async def _send_tg(telegram_id, text: str):
    """Отправляет одно сообщение пользователю в Telegram. Тихо падает при ошибке."""
    if not _tg_bot or not telegram_id:
        return
    try:
        await _tg_bot.send_message(chat_id=telegram_id, text=text[:4000])
    except Exception as _e:
        logger.debug('[TG_SEND] error: %s', _e)


def _is_user_night(user) -> bool:
    """True если сейчас ночные часы для пользователя (22:00 – 10:00 по его TZ).
    Fallback: Europe/Moscow.
    """
    try:
        import pytz as _pytz
        _tz_str = getattr(user, 'timezone', None) or 'Europe/Moscow'
        _tz = _pytz.timezone(_tz_str)
        _user_now = datetime.now(timezone.utc).astimezone(_tz)
        return _user_now.hour >= 22 or _user_now.hour < 10
    except Exception:
        return False


def _save_office_anchor_sync(user_id: int, agent_name: str, text: str,
                              interval_minutes: int | None = None):
    """Создаёт agent_office_update Anchor — AnchorEngine сам решит когда доставить в TG:
    учитывает ночные часы, cooldown, батчинг группы 'integration'.
    Окно дедупликации и cooldown адаптируются к interval_minutes агента.
    """
    try:
        from models import Session as _Db, Anchor as _An, AnchorPriority as _AP
        _now = datetime.now(timezone.utc)
        _iv = max(interval_minutes or 60, 15)          # мин. 15 мин
        _window = _iv if _iv <= 60 else 60              # окно дедупликации ≤ 60 мин
        _slot = (_now.minute // _window) * _window      # квант: 0..59
        _src = f'office-report:{agent_name}:{_now.strftime("%Y-%m-%d-%H")}-{_slot:02d}'
        _cooldown_h = _iv / 60                          # cooldown = интервал агента
        _s = _Db()
        try:
            if _s.query(_An).filter_by(user_id=user_id, source=_src).first():
                return  # уже есть в пределах окна
            _s.add(_An(
                user_id=user_id,
                anchor_type='agent_office_update',
                source=_src,
                topic=f'{agent_name}: {text[:80]}',
                priority=_AP.MEDIUM,
                data=json.dumps({'agent': agent_name, 'report': text[:500]}, ensure_ascii=False),
                triggered_at=_now,
                expires_at=_now + timedelta(hours=8),
                cooldown_hours=_cooldown_h,
                batch_group='integration',
            ))
            _s.commit()
        finally:
            _s.close()
    except Exception as _e:
        logger.debug('[OFFICE] office_anchor create error: %s', _e)


# ── Сохранение сообщения агента в историю чата ──────────────────────────────

def _save_chat_message_sync(user_id: int, agent_name: str, agent_id: int, avatar_url: str, text: str, internal: bool = False):
    """Сохраняет сообщение агента в Interaction.
    internal=True  → message_type='agent_report' (скрыто из чата, только для внутреннего контекста).
    internal=False → message_type='ai' (показывается в чате как реплика ASI).
    Race-condition guard: если этот агент уже записал сообщение для пользователя менее 2 минут назад
    с похожим текстом (первые 60 символов совпадают) — пропускаем (дубль из параллельных тиков).
    """
    try:
        import json as _json
        from models import Session as _Db, Interaction
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        content = _json.dumps({
            '__agent': {
                'name': agent_name,
                'id': agent_id,
                # Никогда не сохраняем base64 data URI — только proxy URL или пусто
                'avatar_url': (f'/api/arena/agent_avatar/{agent_id}' if agent_id and (not avatar_url or avatar_url.startswith('data:')) else (avatar_url or '')),
            },
            'text': text,
        }, ensure_ascii=False)
        _s = _Db()
        try:
            if not internal:
                # Дедупликация по первым 60 символам текста в окне 2 минут
                _cutoff = _dt.now(_tz.utc) - _td(minutes=2)
                _prefix = text.strip()[:60]
                _recent = (
                    _s.query(Interaction)
                    .filter(
                        Interaction.user_id == user_id,
                        Interaction.message_type == 'ai',
                        Interaction.created_at >= _cutoff,
                        Interaction.content.like(f'%{agent_name}%'),
                    )
                    .order_by(Interaction.created_at.desc())
                    .limit(5)
                    .all()
                )
                for _r in _recent:
                    try:
                        _d = _json.loads(_r.content)
                        if _d.get('__agent', {}).get('name') == agent_name:
                            if (_d.get('text') or '').strip()[:60] == _prefix:
                                logger.warning('[OFFICE] dedup: skip duplicate msg from %s for user %d', agent_name, user_id)
                                return
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
            _s.add(Interaction(
                user_id=user_id,
                message_type='agent_report' if internal else 'ai',
                content=content,
            ))
            _s.commit()
            logger.debug('[OFFICE] chat msg saved for user %d (%s)', user_id, agent_name)
        finally:
            _s.close()
    except Exception as e:
        logger.debug('[OFFICE] chat msg save error: %s', e)


def _auto_delegate_to_agent_sync(user_id: int, agent_id: int, agent_name: str, task_title: str):
    """Логирует поручение агенту в AgentActivityLog (без создания Task).
    Дедупликация: не создаём дубль если уже есть похожая запись за 2ч.
    """
    try:
        from models import Session as _Db, AgentActivityLog as _AAL
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _s = _Db()
        try:
            _cutoff = _dt.now(_tz.utc) - _td(minutes=45)
            _recent = _s.query(_AAL).filter(
                _AAL.user_id == user_id,
                _AAL.target == f'agent:{agent_name}',
                _AAL.activity_type == 'agent_task',
                _AAL.created_at >= _cutoff,
            ).order_by(_AAL.created_at.desc()).limit(5).all()
            _task_key = task_title[:40].lower().strip()
            for _ex in _recent:
                # Проверяем и title, и content (там полный task_title)
                _ex_text = ((_ex.title or '') + ' ' + (_ex.content or '')).lower()
                if _task_key in _ex_text:
                    return  # похожее поручение уже есть
            _s.add(_AAL(
                user_id=user_id,
                activity_type='agent_task',
                title=f'Поручено {agent_name}',
                content=task_title[:500],
                target=f'agent:{agent_name}',
                status='accepted',
            ))
            _s.commit()
            logger.debug('[OFFICE] agent delegation logged: user=%d agent=%s "%s"', user_id, agent_name, task_title[:60])
        finally:
            _s.close()
    except Exception as e:
        logger.debug('[OFFICE] agent delegation log error: %s', e)


def _auto_complete_agent_task_sync(user_id: int, agent_id: int, agent_name: str, task_title: str):
    """Логирует завершение работы агента в AgentActivityLog."""
    try:
        from models import Session as _Db, AgentActivityLog as _AAL
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _s = _Db()
        try:
            # Дедупликация: не логируем дубль завершения за 45 мин
            _cutoff = _dt.now(_tz.utc) - _td(minutes=45)
            _recent = _s.query(_AAL).filter(
                _AAL.user_id == user_id,
                _AAL.target == f'agent:{agent_name}',
                _AAL.activity_type == 'agent_task',
                _AAL.status == 'completed',
                _AAL.created_at >= _cutoff,
            ).order_by(_AAL.created_at.desc()).limit(3).all()
            _task_key = task_title[:40].lower().strip()
            for _ex in _recent:
                _ex_text = ((_ex.title or '') + ' ' + (_ex.content or '')).lower()
                if _task_key in _ex_text:
                    return  # уже залогировано
            _s.add(_AAL(
                user_id=user_id,
                activity_type='agent_task',
                title=f'{agent_name}: выполнено',
                content=task_title[:500],
                result=task_title[:500],
                target=f'agent:{agent_name}',
                status='completed',
            ))
            _s.commit()
            logger.debug('[OFFICE] agent task completed: user=%d agent=%s', user_id, agent_name)
        finally:
            _s.close()
    except Exception as e:
        logger.debug('[OFFICE] agent task complete error: %s', e)


import re as _re
_EMAIL_RE = _re.compile(r'[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9.\-]{2,}')
_GENERIC_DOMAINS = {'noreply', 'no-reply', 'donotreply', 'mailer', 'support', 'info',
                    'admin', 'webmaster', 'postmaster', 'sales', 'hello', 'contact'}


def _auto_extract_email_contacts_sync(user_id: int, stdout: str, agent_name: str):
    """Парсит stdout агента на email-адреса и сохраняет новые в EmailContact.
    Пропускает родовые/безличные адреса (info@, noreply@ и т.д.).
    Сохраняет ТОЛЬКО если контекст указывает на активную переписку:
    ключевые слова: ответ, reply, новое письмо, входящие, responded.
    """
    # Проверяем признаки реальной переписки, а не просто упоминания email
    _REPLY_MARKERS = ('ответ', 'reply', 'replied', 'responded', 'новое письмо', 'входящ', 'incoming', 'от:', 'from:')
    _stdout_lc = stdout.lower()
    if not any(m in _stdout_lc for m in _REPLY_MARKERS):
        return  # Просто сканирование / упоминание — не создаём контакт
    try:
        emails_found = _EMAIL_RE.findall(stdout)
        if not emails_found:
            return
        from models import Session as _Db, EmailContact as _EC, User as _U
        _s = _Db()
        try:
            _user = _s.query(_U).filter_by(id=user_id).first()
            if not _user:
                return
            saved = 0
            seen: set = set()
            for raw in emails_found:
                email = raw.lower().strip('.')
                if email in seen or saved >= 5:
                    break
                seen.add(email)
                local = email.split('@')[0]
                if local in _GENERIC_DOMAINS or any(g in local for g in _GENERIC_DOMAINS):
                    continue
                _dup = _s.query(_EC).filter_by(user_id=_user.id, email=email).first()
                if _dup:
                    continue
                _s.add(_EC(
                    user_id=_user.id,
                    email=email,
                    source=f'agent:{agent_name}',
                ))
                saved += 1
            if saved:
                _s.commit()
                logger.debug('[OFFICE] email contacts extracted: user=%d saved=%d', user_id, saved)
        finally:
            _s.close()
    except Exception as e:
        logger.debug('[OFFICE] email contact extract error: %s', e)


def _log_agent_activity_sync(user_id: int, agent_name: str, agent_id: int, title: str, content: str,
                             activity_type: str = 'other'):
    """Пишет произвольную запись в AgentActivityLog (SSE-поток дашборда).
    Для ключевых событий используйте activity_type='delegation'.
    """
    try:
        from models import Session as _Db, AgentActivityLog as _AAL
        _s = _Db()
        try:
            _s.add(_AAL(
                user_id=user_id,
                activity_type=activity_type,
                title=title[:295],
                content=content[:2000],
                target=agent_name,
                status='completed',
                ref_id=agent_id,
            ))
            _s.commit()
            logger.debug('[OFFICE] activity log saved: user=%d agent=%s type=%s', user_id, agent_name, activity_type)
        finally:
            _s.close()
    except Exception as e:
        logger.debug('[OFFICE] activity log save error: %s', e)


# ── Изолированный запуск скрипта ─────────────────────────────────────────────

def _exec_agent_script_sync(code: str, env: dict | None = None) -> tuple:
    """Запускает python_code агента в отдельном subprocess (sync).
    Возвращает (stdout: str, stderr: str).
    Безопасно: изолировано от серверного процесса.
    env — словарь переменных окружения (user_api_keys). Если None — наследует ОС.
    """
    try:
        kwargs: dict = dict(capture_output=True, text=True, timeout=SCRIPT_TIMEOUT_SEC)
        if env is not None:
            kwargs['env'] = env
        result = subprocess.run([sys.executable, '-c', code], **kwargs)
        return result.stdout[:10000].strip(), result.stderr[:400].strip()
    except subprocess.TimeoutExpired:
        return '', 'timeout'
    except Exception as e:
        return '', str(e)[:200]


def _build_agent_env(user_api_keys: str) -> dict:
    """Строит безопасное окружение subprocess: OS-пути + user_api_keys.
    Пробелы из App Passwords убираются автоматически.
    """
    env = {
        'PYTHONIOENCODING': 'utf-8',
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
    }
    if sys.platform != 'win32':
        env['HOME'] = os.environ.get('HOME', '/tmp')
    else:
        for wk in ('SystemRoot', 'SystemDrive', 'TEMP', 'TMP', 'WINDIR',
                   'COMSPEC', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH'):
            if wk in os.environ:
                env[wk] = os.environ[wk]
    for line in (user_api_keys or '').splitlines():
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, _, v = line.partition('=')
            v = v.strip()
            # Gmail App Passwords и другие пароли имеют пробелы между группами
            if 'PASS' in k.upper() or 'PASSWORD' in k.upper():
                v = v.replace(' ', '')
            env[k.strip()] = v
    return env


# ── Долгосрочная память агентов ─────────────────────────────────────────────

def _save_agent_outcome_memory_sync(
    user_id: int,
    agent_name: str,
    task: str,
    result: str,
    success: bool = True,
):
    """Сохраняет итог работы агента в AgentActivityLog как 'outcome_memory'.
    Агент учитывает эту историю при следующих задачах — не повторяет провалы.
    """
    try:
        from models import Session as _Db, AgentActivityLog
        _s = _Db()
        try:
            _s.add(AgentActivityLog(
                user_id=user_id,
                activity_type='outcome_memory',
                title=f'{agent_name}: {task[:120]}',
                content=result[:800],
                target=f'agent:{agent_name}',
                status='completed' if success else 'failed',
            ))
            _s.commit()
        finally:
            _s.close()
    except Exception as _e:
        logger.debug('[MEMORY] save error: %s', _e)


def _extract_from_subject_set(text: str) -> set:
    """Извлекает множество (From, Subject) пар из stdout IMAP-агента.
    Используется для дедупликации: одно и то же письмо = та же пара.
    """
    pairs = set()
    lines = text.splitlines()
    current_from = ''
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('От:'):
            current_from = stripped[3:].strip()[:100]
        elif stripped.startswith('Тема:') and current_from:
            subj = stripped[5:].strip()[:100]
            pairs.add((current_from, subj))
            current_from = ''
    return pairs


def _save_inbox_reply_sync(user_id: int, agent_name: str, stdout: str):
    """Детектирует входящие письма в stdout IMAP-агента и сохраняет в AgentActivityLog.
    AnchorEngine подберёт запись и создаст CRITICAL-якорь agent_inbox_reply.
    Дедупликация: сравниваем пары (From, Subject), не сырой текст.
    """
    if not stdout:
        return
    # Признак: stdout содержит реальные письма (шаблоны Gmail/Yandex/Mail.ru)
    has_from = 'От:' in stdout
    has_subj = 'Тема:' in stdout
    if not (has_from and has_subj):
        return
    # Извлекаем (From, Subject) пары для robust дедупликации
    new_pairs = _extract_from_subject_set(stdout)
    if not new_pairs:
        return
    reply_count = len(new_pairs)
    try:
        from models import Session as _Db, AgentActivityLog
        _s = _Db()
        try:
            # Дедупликация: проверяем все inbox_reply за 3ч — если те же From+Subject уже есть
            _cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
            _existing_recs = _s.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user_id,
                AgentActivityLog.target == f'agent:{agent_name}',
                AgentActivityLog.activity_type == 'inbox_reply',
                AgentActivityLog.created_at > _cutoff,
            ).order_by(AgentActivityLog.created_at.desc()).limit(5).all()
            for _ex in _existing_recs:
                old_pairs = _extract_from_subject_set(_ex.content or '')
                # Если все новые пары уже были — дубль
                if new_pairs and old_pairs and new_pairs.issubset(old_pairs):
                    return  # те же письма — не дублируем
            _s.add(AgentActivityLog(
                user_id=user_id,
                activity_type='inbox_reply',
                title=f'{agent_name}: {reply_count} входящих',
                content=stdout[:800],
                target=f'agent:{agent_name}',
                status='new',
            ))
            _s.commit()
        finally:
            _s.close()
    except Exception as _e:
        logger.debug('[INBOX] save error: %s', _e)


def _save_task_blocked_sync(user_id: int, agent_name: str, report: str):
    """Детектирует маркер BLOCKED в отчёте агента и сохраняет в AgentActivityLog.
    AnchorEngine создаст HIGH-якорь agent_task_blocked.
    AI-агент выводит BLOCKED: <причина> когда ему нужно решение человека.
    """
    if not report:
        return
    _lower = report.lower()
    _blocked_markers = [
        'blocked:', 'нужно ваше решение', 'нужна ваша помощь',
        'требует вашего подтверждения', 'нужен доступ', 'нужно разрешение',
        'нужно ваше подтверждение', 'жду вашего решения',
    ]
    if not any(m in _lower for m in _blocked_markers):
        return
    try:
        from models import Session as _Db, AgentActivityLog
        _s = _Db()
        try:
            _s.add(AgentActivityLog(
                user_id=user_id,
                activity_type='task_blocked',
                title=f'{agent_name}: нужно решение',
                content=report[:600],
                target=f'agent:{agent_name}',
                status='new',
            ))
            _s.commit()
        finally:
            _s.close()
    except Exception as _e:
        logger.debug('[BLOCKED] save error: %s', _e)


def _load_agent_outcome_memory_sync(user_id: int, agent_name: str, limit: int = 5) -> str:
    """Загружает последние N итогов работы агента.
    Возвращает текстовый блок для системного промпта.
    """
    try:
        from models import Session as _Db, AgentActivityLog
        _s = _Db()
        try:
            rows = (
                _s.query(AgentActivityLog)
                .filter_by(user_id=user_id, target=f'agent:{agent_name}', activity_type='outcome_memory')
                .order_by(AgentActivityLog.created_at.desc())
                .limit(limit)
                .all()
            )
            if not rows:
                return ''
            lines = []
            for r in reversed(rows):
                status_mark = '✓' if r.status == 'completed' else '✗'
                lines.append(f'{status_mark} {r.title[:100]}: {(r.content or "")[:200]}')
            return 'ИСТОРИЯ ПРЕДЫДУЩИХ ЗАДАЧ АГЕНТА:\n' + '\n'.join(lines)
        finally:
            _s.close()
    except Exception as _e:
        logger.debug('[MEMORY] load error: %s', _e)
        return ''


# ── Автономный анализатор результатов агента ─────────────────────────────────
# После того как агент принёс данные, ASI решает: передать следующему агенту
# (безопасные шаги) или спросить пользователя (действия с последствиями).

_DECISION_GATE_KEYWORDS = (
    'публик', 'publish', 'отправ', 'send', 'рассыл', 'оплат', 'оплач',
    'купи', 'buy', 'подпис', 'запуст', 'launch', 'deploy', 'удали', 'delete',
    'перевод', 'transfer', 'подтверд', 'confirm', 'заключ', 'договор',
)

_NEXT_STEP_PROMPT = """Ты — ASI, автономный директор офиса. Агент "{agent_name}" только что выполнил задачу и вернул результат.

РЕЗУЛЬТАТ АГЕНТА:
{result}

ДРУГИЕ ДОСТУПНЫЕ АГЕНТЫ:
{other_agents}

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
{user_context}

Реши что делать дальше. Варианты:
1. NEXT — результат подготовительный (исследование, анализ, черновик) и есть логичный следующий шаг для другого агента. Укажи имя агента и задачу.
2. ASK — следующий шаг = необратимое внешнее действие: массовая рассылка незнакомым людям (send_campaign) ИЛИ денежный платёж ИЛИ удаление данных. Задай КОНКРЕТНЫЙ вопрос с вариантами.
3. DONE — результат финальный/информационный, действий больше не нужно.

Ответь СТРОГО в формате (одна строка):
NEXT|имя_агента|задача для агента (с контекстом из предыдущего результата)
ASK|вопрос пользователю (кратко, с вариантами действий)
DONE

Правила:
- НЕ передавай задачу тому же агенту который только что её выполнил
- Передавай ТОЛЬКО если другой агент реально нужен для следующего шага
- Если результат — готовый отчёт/данные/мониторинг без продолжения → DONE
- Создание задачи в системе, сохранение заметки, составление черновика письма → DONE (это внутренние подготовительные действия, НЕ ASK)
- Отправка ответного письма КОНКРЕТНОМУ человеку (не массовая рассылка) → DONE через NEXT или просто DONE
- ASK ТОЛЬКО для: массовой рассылки новым незнакомым адресатам, денежных операций, необратимого удаления данных
- Если результат — периодический отчёт (проверка почты, мониторинг) и информация уже была доставлена → DONE"""


async def _autonomous_analyze_result(
    user_id: int,
    user_db_id: int,
    agent_name: str,
    agent_id: int,
    report: str,
    all_agents: list,
    user_context: str,
) -> dict:
    """Анализирует результат агента и решает следующий шаг.
    Возвращает: {'action': 'next'|'ask'|'done', 'target': str, 'task': str}
    """
    if not report or len(report.strip()) < 30:
        return {'action': 'done'}

    # Собираем других агентов (исключаем текущего)
    other = []
    for a in all_agents:
        if a.get('id') == agent_id or a.get('name', '').lower() == agent_name.lower():
            continue
        _desc = (a.get('description') or '')[:100]
        _intg = ''
        if _parse_agent_integrations:
            _caps = _parse_agent_integrations(
                a.get('user_api_keys', ''), a.get('python_code', ''),
                a.get('tools_allowed', ''),
            )
            if _caps:
                _intg = f" [интеграции: {', '.join(_caps[:4])}]"
        other.append(f"- {a.get('name', '?')} ({a.get('specialization', 'агент')}): {_desc}{_intg}")
    if not other:
        return {'action': 'done'}  # нет других агентов — некому передать

    prompt = _NEXT_STEP_PROMPT.format(
        agent_name=agent_name,
        result=report[:600],
        other_agents='\n'.join(other[:6]),
        user_context=(user_context or 'нет данных')[:400],
    )

    try:
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp as _aio
        async with _aio.ClientSession() as sess:
            async with sess.post(
                'https://api.deepseek.com/chat/completions',
                headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': DEEPSEEK_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 150,
                    'temperature': 0.3,
                },
                timeout=_aio.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return {'action': 'done'}
                data = await resp.json()
                answer = (data.get('choices', [{}])[0].get('message', {}).get('content', '') or '').strip()
    except Exception as _e:
        logger.debug('[AUTONOMY] AI call error: %s', _e)
        return {'action': 'done'}

    if not answer:
        return {'action': 'done'}

    # Парсим ответ
    answer_line = answer.splitlines()[0].strip()

    if answer_line.startswith('NEXT|'):
        parts = answer_line.split('|', 2)
        if len(parts) >= 3:
            return {'action': 'next', 'target': parts[1].strip(), 'task': parts[2].strip()}

    if answer_line.startswith('ASK|'):
        parts = answer_line.split('|', 1)
        if len(parts) >= 2:
            return {'action': 'ask', 'question': parts[1].strip()}

    return {'action': 'done'}


# ═══════════════════════════════════════════════════════════════════════════════
class OfficeEngine:
    """
    Фоновый мониторинг агентов: запускает python_code каждого агента по расписанию.
    Координация (поручения, реакции, межагентная шина) — через живой чат с ИИ (delegate_task inline).
    """

    def __init__(self):
        self.running = False
        self._script_sem = asyncio.Semaphore(4)
        self._ai_sem = asyncio.Semaphore(6)
        # Per-agent inflight guard: предотвращает параллельный запуск одного агента двумя тиками.
        # asyncio однопоточный → set достаточен (add/check атомарны между await-точками).
        self._agent_inflight: set[int] = set()

    async def start(self):
        self.running = True
        logger.info("[OFFICE] Living Office Engine started (L1 + L2)")
        asyncio.create_task(self._level2_coordinator_loop())
        await self._level1_monitor_loop()

    async def _level1_monitor_loop(self):
        """Тикает каждые 15 мин и запускает агентов, у которых истёк их индивидуальный run_interval_minutes.
        Каждый агент работает со своей частотой (15 мин / 30 мин / 1 ч / 12 ч / 1 день).
        """
        await asyncio.sleep(30)  # минимальный прогрев сервера
        # Умная стартовая задержка: не запускаем агентов сразу после деплоя,
        # если они уже запускались недавно — ждём оставшееся время до следующего цикла.
        try:
            from models import Session as _Db, UserAgent as _UA
            _s = _Db()
            try:
                _last_run = _s.query(_UA.last_office_run_at).filter(
                    _UA.status.in_(['active', 'paused']),
                    _UA.python_code.isnot(None),
                    _UA.last_office_run_at.isnot(None),
                ).order_by(_UA.last_office_run_at.desc()).first()
            finally:
                _s.close()
            if _last_run and _last_run[0]:
                import time as _time
                from datetime import timezone as _tz
                _last_ts = _last_run[0]
                if _last_ts.tzinfo is None:
                    _last_ts = _last_ts.replace(tzinfo=_tz.utc)
                _elapsed = (datetime.now(timezone.utc) - _last_ts).total_seconds()
                _min_interval = L1_TICK_SEC  # ждём минимум один тик (15 мин)
                if _elapsed < _min_interval:
                    _remaining = _min_interval - _elapsed
                    logger.info(
                        "[OFFICE-L1] Last agent run %.0f min ago — waiting %.0f min before first tick",
                        _elapsed / 60, _remaining / 60,
                    )
                    await asyncio.sleep(_remaining)
                else:
                    await asyncio.sleep(90)  # обычный прогрев если агенты давно не запускались
            else:
                await asyncio.sleep(90)  # нет данных — стандартный прогрев
        except Exception as _e:
            logger.debug("[OFFICE-L1] startup cooldown check error: %s", _e)
            await asyncio.sleep(90)
        while self.running:
            try:
                await self._run_all_agent_scripts()
            except Exception as e:
                logger.error("[OFFICE-L1] loop error: %s", e)
            logger.info("[OFFICE-L1] next tick in %.0f min", L1_TICK_SEC / 60)
            await asyncio.sleep(L1_TICK_SEC)

    async def _run_all_agent_scripts(self):
        """Грузит всех активных агентов с python_code и запускает их в пакетах."""
        try:
            from models import Session as Db, UserAgent, User as UserModel, AgentSubscription as ASub
            s = Db()
            try:
                _sub_pairs = {
                    (r.user_id, r.agent_id) for r in s.query(ASub).all()
                }
                rows = (
                    s.query(UserAgent, UserModel)
                    .join(UserModel, UserModel.id == UserAgent.author_id)
                    .filter(
                        UserAgent.status.in_(['active', 'paused']),  # paused = arena-paused, still active in personal chat
                        UserAgent.python_code.isnot(None),
                        UserModel.telegram_id.isnot(None),
                    )
                    .all()
                )
                rows = [
                    (agent, user) for agent, user in rows
                    if (user.id, agent.id) in _sub_pairs
                ]
            finally:
                s.close()
        except Exception as e:
            logger.warning("[OFFICE-L1] DB load error: %s", e)
            return

        if not rows:
            return

        logger.info("[OFFICE-L1] Running scripts for %d agents", len(rows))
        tasks = [self._run_one_agent_script(agent, user) for agent, user in rows]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_one_agent_script(self, agent, user):
        """Выполняет скрипт одного агента, создаёт якорь если есть данные.
        После интересного результата — ASI проактивно реагирует в чат.
        """
        py_code = (agent.python_code or '').strip()
        if not py_code:
            return

        # Per-agent inflight guard: если другой тик уже обрабатывает этот агент — пропускаем.
        if agent.id in self._agent_inflight:
            logger.debug("[OFFICE-L1] [%s] already running in another tick — skip", agent.name)
            return
        self._agent_inflight.add(agent.id)
        try:
            await self._run_one_agent_script_inner(agent, user)
        finally:
            self._agent_inflight.discard(agent.id)

    async def _run_one_agent_script_inner(self, agent, user):
        """Внутренняя логика запуска агента (вызывается под inflight guard)."""
        # Per-agent cooldown: каждый агент имеет свой интервал run_interval_minutes.
        # L1 тикает каждые 15 мин и проверяет, пора ли конкретному агенту запускаться.
        _agent_interval_sec = (agent.run_interval_minutes or 0) * 60 or L1_DEFAULT_INTERVAL_SEC
        if agent.last_office_run_at is not None:
            try:
                _last = agent.last_office_run_at
                if _last.tzinfo is None:
                    _last = _last.replace(tzinfo=timezone.utc)
                _elapsed = (datetime.now(timezone.utc) - _last).total_seconds()
                if _elapsed < _agent_interval_sec:
                    logger.debug(
                        "[OFFICE-L1] [%s] skipped (ran %.0f min ago, interval %.0f min)",
                        agent.name, _elapsed / 60, _agent_interval_sec / 60,
                    )
                    return
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

        async with self._script_sem:
            try:
                from ai_integration.autonomous_agent import _wrap_agent_code, spawn_integration_anchors
                wrapped = _wrap_agent_code(py_code)
                agent_env = _build_agent_env(agent.user_api_keys or '')

                loop = asyncio.get_running_loop()
                stdout, stderr = await loop.run_in_executor(
                    None, _exec_agent_script_sync, wrapped, agent_env
                )

                # Сразу фиксируем время запуска в БД — даже если stdout пустой.
                # Это предотвращает лавину повторных запусков после рестарта деплоя.
                def _update_last_run(agent_id: int):
                    try:
                        from models import Session as _Db, UserAgent as _UA
                        _s = _Db()
                        try:
                            _s.query(_UA).filter_by(id=agent_id).update(
                                {'last_office_run_at': datetime.now(timezone.utc)},
                                synchronize_session=False,
                            )
                            _s.commit()
                        finally:
                            _s.close()
                    except Exception as _ue:
                        logger.debug("[OFFICE-L1] last_office_run_at update error: %s", _ue)
                await loop.run_in_executor(None, _update_last_run, agent.id)
            except Exception as e:
                logger.debug("[OFFICE-L1] [%s] exec error: %s", agent.name, e)
                return

            if stdout:
                # ── Дедупликация: если stdout не изменился — пропускаем ─────
                # Хеш хранится и в памяти, и в БД (переживает рестарт)
                import hashlib as _hl
                _dedup_key = (user.id, agent.id)
                _stdout_hash = _hl.md5(stdout.strip()[:500].encode()).hexdigest()[:16]
                # In-memory check
                if _STDOUT_DEDUP.get(_dedup_key) == _stdout_hash:
                    logger.debug("[OFFICE-L1] [%s] dedup: output unchanged (mem), skipping", agent.name)
                    return
                # DB-persistent check (survives restart)
                if agent.last_stdout_hash and agent.last_stdout_hash == _stdout_hash:
                    _STDOUT_DEDUP[_dedup_key] = _stdout_hash
                    logger.debug("[OFFICE-L1] [%s] dedup: output unchanged (db), skipping", agent.name)
                    return
                _STDOUT_DEDUP[_dedup_key] = _stdout_hash
                # Persist hash to DB
                try:
                    def _save_hash(_aid, _h):
                        from models import Session as _Db, UserAgent as _UA
                        _s = _Db()
                        try:
                            _s.query(_UA).filter_by(id=_aid).update({'last_stdout_hash': _h})
                            _s.commit()
                        finally:
                            _s.close()
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, _save_hash, agent.id, _stdout_hash)
                except Exception:
                    pass

                service_label = (agent.specialization or agent.name or 'Agent').strip()
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        spawn_integration_anchors,
                        user.id,
                        agent.name or 'Агент',
                        service_label,
                        stdout[:3000],
                    )
                    logger.debug("[OFFICE-L1] [%s] anchored (%d chars)", agent.name, len(stdout))
                except Exception as e:
                    logger.debug("[OFFICE-L1] [%s] anchor error: %s", agent.name, e)

                # Пишем отчёт агента — виден в чате с аватаркой агента
                try:
                    async with self._ai_sem:
                        report = await self._format_agent_report(
                            agent.name or 'Агент',
                            agent.specialization or 'агент',
                            stdout,
                            user.id,
                        )
                    if report:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(
                            None,
                            _save_chat_message_sync,
                            user.id,
                            agent.name or 'Агент',
                            agent.id,
                            (f'/api/arena/agent_avatar/{agent.id}' if agent.id else ''),  # никогда не base64
                            report,
                            False,  # internal=False: отчёт агента виден в чате
                        )
                        logger.info("[OFFICE-L1] [%s] report saved (visible) for user %d", agent.name, user.id)
                        # Anchor → AnchorEngine доставит в TG с учётом ночного времени и cooldown
                        try:
                            await loop.run_in_executor(
                                None, _save_office_anchor_sync,
                                user.id, agent.name or 'Агент', report,
                                agent.run_interval_minutes,
                            )
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                        # Создаём задачу-делегирование в дашборд (видна как делегирование агенту)
                        try:
                            _task_title = (report.splitlines()[0].strip()[:120]
                                           if report else f'{agent.name}: мониторинг')
                            await loop.run_in_executor(
                                None,
                                _auto_delegate_to_agent_sync,
                                user.id, agent.id, agent.name, _task_title,
                            )
                        except Exception as _de:
                            logger.debug("[OFFICE-L1] [%s] delegation create error: %s", agent.name, _de)
                        # Парсим email-контакты из stdout → EmailContact
                        try:
                            await loop.run_in_executor(
                                None,
                                _auto_extract_email_contacts_sync,
                                user.id, stdout, agent.name or 'Агент',
                            )
                        except Exception as _ee:
                            logger.debug("[OFFICE-L1] [%s] email extract error: %s", agent.name, _ee)
                        # Закрываем задачу и логируем «Задача выполнена» → dashboard Activities
                        try:
                            _complete_title = (report.splitlines()[0].strip()[:200]
                                               if report else f'{agent.name}: работа завершена')
                            await loop.run_in_executor(
                                None,
                                _auto_complete_agent_task_sync,
                                user.id, agent.id, agent.name or 'Агент', _complete_title,
                            )
                        except Exception as _le:
                            logger.debug("[OFFICE-L1] [%s] task complete error: %s", agent.name, _le)
                        # Сохраняем итог в долгосрочную память агента
                        try:
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(
                                None,
                                _save_agent_outcome_memory_sync,
                                user.id, agent.name or 'Агент', 'фоновый мониторинг',
                                report[:400], True,
                            )
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                        # Детектируем входящие письма в stdout (IMAP agents)
                        try:
                            await loop.run_in_executor(
                                None, _save_inbox_reply_sync,
                                user.id, agent.name or 'Агент', stdout,
                            )
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                        # Детектируем BLOCKED-маркер в отчёте агента
                        try:
                            await loop.run_in_executor(
                                None, _save_task_blocked_sync,
                                user.id, agent.name or 'Агент', report,
                            )
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                        # ── Автономный следующий шаг ──────────────────────────
                        # ASI анализирует результат и решает: передать другому
                        # агенту (безопасный шаг) или спросить пользователя.
                        try:
                            await self._autonomous_next_step(
                                agent, user, report,
                            )
                        except Exception as _ans_e:
                            logger.debug("[OFFICE-L1] [%s] autonomy error: %s", agent.name, _ans_e)
                except Exception as e:
                    logger.debug("[OFFICE-L1] [%s] report/reaction error: %s", agent.name, e)

            elif stderr and 'timeout' not in stderr:
                logger.debug("[OFFICE-L1] [%s] stderr: %s", agent.name, stderr[:150])

    async def _autonomous_next_step(self, agent, user, report: str):
        """После завершения агента — ASI решает следующий шаг автономно.
        Может выстроить цепочку до 3 агентов без участия пользователя.
        Безопасные шаги (исследование, анализ) → передаёт другому агенту.
        Шаги с последствиями (публикация, отправка) → спрашивает пользователя.
        Каждый шаг виден в чате как живой диалог ASI↔агенты.
        """
        if not report or len(report.strip()) < 40:
            return

        # Загружаем всех агентов пользователя (только с активной подпиской)
        try:
            from models import Session as _Db, UserAgent as _UA, AgentSubscription as _ASub
            _s = _Db()
            try:
                _sub_ids = {
                    r.agent_id for r in _s.query(_ASub).filter_by(user_id=user.id).all()
                }
                _rows = (
                    _s.query(_UA)
                    .filter(_UA.author_id == user.id, _UA.status.in_(['active', 'paused']))
                    .all()
                )
                all_agents = [
                    {
                        'id': a.id,
                        'name': a.name or 'Агент',
                        'specialization': a.specialization or '',
                        'description': (a.description or '')[:150],
                        'avatar_url': f'/api/arena/agent_avatar/{a.id}',  # никогда не base64
                    }
                    for a in _rows if a.id in _sub_ids
                ]
            finally:
                _s.close()
        except Exception:
            return

        if len(all_agents) < 2:
            return  # один агент — некому передавать

        # Контекст пользователя
        _user_ctx = ''
        if _build_user_context_sync:
            try:
                loop = asyncio.get_running_loop()
                _user_ctx = await loop.run_in_executor(None, _build_user_context_sync, user.id)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

        # Цепочка: до 3 автономных шагов (агент → агент → агент → ask/done)
        _MAX_CHAIN = 3
        _current_report = report
        _current_agent_name = agent.name or 'Агент'
        _current_agent_id = agent.id
        _used_agents = {agent.id}  # не используем одного агента дважды подряд

        # Inject: недавние отправленные email (чтобы не дублировать)
        try:
            from models import Session as _MailDb, AgentActivityLog as _MailLog
            _ms = _MailDb()
            try:
                _sent = _ms.query(_MailLog).filter(
                    _MailLog.user_id == user.id,
                    _MailLog.activity_type == 'email',
                    _MailLog.status == 'sent',
                    _MailLog.created_at > datetime.now(timezone.utc) - timedelta(hours=24),
                ).order_by(_MailLog.created_at.desc()).limit(5).all()
                if _sent:
                    _sent_lines = [f'- {s.target} ({s.title})' for s in _sent]
                    _user_ctx += '\n\nНЕДАВНО ОТПРАВЛЕННЫЕ EMAIL (НЕ ДУБЛИРУЙ!):\n' + '\n'.join(_sent_lines)
            finally:
                _ms.close()
        except Exception:
            pass

        for _step in range(_MAX_CHAIN):
            async with self._ai_sem:
                decision = await _autonomous_analyze_result(
                    user_id=user.telegram_id,
                    user_db_id=user.id,
                    agent_name=_current_agent_name,
                    agent_id=_current_agent_id,
                    report=_current_report,
                    all_agents=all_agents,
                    user_context=_user_ctx,
                )

            action = decision.get('action', 'done')

            if action == 'next':
                target_name = decision.get('target', '')
                task_text = decision.get('task', '')
                if not target_name or not task_text:
                    break
                target_agent = next(
                    (a for a in all_agents if a['name'].lower() == target_name.lower()),
                    next((a for a in all_agents if target_name.lower() in a['name'].lower()), None),
                )
                if not target_agent or target_agent['id'] in _used_agents:
                    break

                logger.info("[AUTONOMY] step %d: %s → %s: %s",
                           _step + 1, _current_agent_name, target_name, task_text[:80])

                # ASI обращается к следующему агенту — видно в чате
                _director_msg = f"{target_agent['name']}, {task_text[:300]}"
                try:
                    from ai_integration.autonomous_agent import _save_interaction_for_director
                    _save_interaction_for_director(user.telegram_id, _director_msg)
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                # Уведомление в Telegram
                await _send_tg(user.telegram_id, _director_msg)

                # Выполняем задачу
                try:
                    from ai_integration.autonomous_agent import _exec_agent_for_director
                    _raw_result = await asyncio.wait_for(
                        _exec_agent_for_director(target_agent, task_text, user.telegram_id),
                        timeout=45,
                    )
                    result = _raw_result[0] if isinstance(_raw_result, (tuple, list)) else _raw_result
                except asyncio.TimeoutError:
                    logger.warning("[AUTONOMY] agent %s timeout at step %d — skip silently", target_name, _step + 1)
                    continue  # не сохраняем placeholder в чат, продолжаем цепочку
                except Exception as _e:
                    logger.debug("[AUTONOMY] exec error step %d: %s", _step + 1, _e)
                    break

                if not result or not result.strip():
                    break

                # Результат агента — в чат с аватаркой
                _save_chat_message_sync(
                    user.id,
                    target_agent['name'],
                    target_agent['id'],
                    target_agent.get('avatar_url', ''),
                    result[:800],
                    False,
                )
                # Уведомление в Telegram
                _tg_result = f"{target_agent['name']}:\n{result[:600]}"
                await _send_tg(user.telegram_id, _tg_result)
                # Activity log
                _log_agent_activity_sync(
                    user.id,
                    target_agent['name'],
                    target_agent['id'],
                    f"Автономно: {task_text[:120]}",
                    result[:500],
                    'delegation',
                )

                logger.info("[AUTONOMY] step %d done: %s (%d chars)",
                           _step + 1, target_agent['name'], len(result))

                # Подготовка к следующей итерации
                _used_agents.add(target_agent['id'])
                _current_report = result
                _current_agent_name = target_agent['name']
                _current_agent_id = target_agent['id']
                await asyncio.sleep(1)  # небольшая пауза между шагами
                continue  # анализируем результат этого агента → может быть ещё шаг

            elif action == 'ask':
                # Сохраняем вопрос только в чат (dashboard), НЕ отправляем в Telegram
                question = decision.get('question', 'Готово. Действуем?')
                ask_msg = question
                try:
                    from ai_integration.autonomous_agent import _save_interaction_for_director
                    _save_interaction_for_director(user.telegram_id, ask_msg)
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                logger.info("[AUTONOMY] ASK user %d at step %d: %s",
                          user.telegram_id, _step + 1, question[:80])
                break  # ждём ответа пользователя

            else:  # done
                break

    async def _format_agent_report(self, agent_name: str, agent_spec: str, stdout: str,
                                      user_db_id: int = 0) -> str:
        """Превращает сырой stdout скрипта в человеческую живую реплику агента.
        Как в арене: code_output → AI → чистое сообщение без логов и трейсбэков.
        """
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp

        # Убираем очевидные технические строки прямо здесь — на случай если AI не справится
        lines = [l for l in stdout.splitlines()
                 if not l.strip().startswith(('Traceback', 'File "', '  File ', 'DEBUG', 'INFO ', 'WARNING', 'ERROR'))
                 and 'Traceback' not in l]
        clean = '\n'.join(lines).strip()
        if not clean:
            return ""

        # Контекст профиля пользователя — чтобы агент говорил релевантно его бизнесу
        _user_ctx = ''
        if user_db_id and _build_user_context_sync:
            try:
                loop = asyncio.get_running_loop()
                _user_ctx = await loop.run_in_executor(None, _build_user_context_sync, user_db_id)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
        _ctx_block = f"\n\nКонтекст о пользователе:\n{_user_ctx[:400]}" if _user_ctx else ''

        # Fair-share: каждая секция интеграции получает равный бюджет
        from ai_integration.autonomous_agent import _parse_integration_sections
        _sections = _parse_integration_sections(clean, agent_name)
        if len(_sections) > 1:
            _per = max(300, 4000 // len(_sections))
            _parts = []
            for _sn, _sv in _sections:
                if len(_sv) > _per:
                    _sv = _sv[:_per - 20] + '\n[…сокращено…]'
                _parts.append(f'=== {_sn} ===\n{_sv}')
            _clean_budget = '\n\n'.join(_parts)
        else:
            _clean_budget = clean[:4000]

        prompt = (
            f"Ты — {agent_name}, {agent_spec}. Ты только что выполнил мониторинг и получил следующие данные:\n\n"
            f"{_clean_budget}\n\n"
            "Напиши одно короткое сообщение (2-3 предложения) в чат пользователю — как живой человек в мессенджере.\n"
            "Что нашёл, что важного, если нужно — одно действие. Без технических деталей, без логов, без списков.\n"
            f"Только суть. Если данных нет или ничего интересного — напиши одно предложение об этом.{_ctx_block}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 150,
                        "temperature": 0.7,
                    },
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug("[OFFICE-L1] format_report AI error: %s", e)
        # Fallback: берём первые 2 значимые строки из clean
        fallback = '. '.join(lines[:2])
        return fallback[:300] if fallback else ""

    # ─── Диалог: агент отвечает на реакцию ASI ──────────────────────────────

    # ═══════════════════════════════════════════════════════════════════════════
    # Level 2 — Координатор: АСИ смотрит на цели пользователя и назначает задачи агентам
    # ═══════════════════════════════════════════════════════════════════════════

    async def _level2_coordinator_loop(self):
        """Каждые 2-4 часа ASI смотрит цели каждого пользователя,
        сопоставляет с возможностями агентов и назначает конкретные задачи."""
        await asyncio.sleep(300)  # 5 мин прогрев
        while self.running:
            try:
                await self._run_coordinator_cycle()
            except Exception as e:
                logger.error("[OFFICE-L2] cycle error: %s", e)
            _interval = random.randint(OFFICE_INTERVAL_SEC[0], OFFICE_INTERVAL_SEC[1])
            logger.info("[OFFICE-L2] next cycle in %.0f hours", _interval / 3600)
            await asyncio.sleep(_interval)

    async def _run_coordinator_cycle(self):
        """Один цикл L2: находит пользователей с активными целями + агентами."""
        from models import (
            Session as Db, User as UserModel, UserProfile,
            Goal, UserAgent, AgentSubscription, AgentActivityLog,
        )
        s = Db()
        try:
            # Находим пользователей с auto_delegation_enabled + активными целями
            users_with_goals = []
            profiles = (
                s.query(UserProfile)
                .filter(UserProfile.auto_delegation_enabled == True)
                .limit(50)
                .all()
            )
            for prof in profiles:
                user = s.query(UserModel).filter_by(id=prof.user_id).first()
                if not user or not user.telegram_id:
                    continue
                if _is_user_night(user):
                    continue
                goals = (
                    s.query(Goal)
                    .filter_by(user_id=prof.user_id, status='active')
                    .limit(5)
                    .all()
                )
                if not goals:
                    continue
                # Агенты с подпиской
                sub_ids = [
                    row.agent_id for row in
                    s.query(AgentSubscription)
                    .filter_by(user_id=prof.user_id)
                    .all()
                ]
                agents = (
                    s.query(UserAgent)
                    .filter(
                        UserAgent.id.in_(sub_ids) if sub_ids else UserAgent.author_id == prof.user_id,
                        UserAgent.status.in_(['active', 'paused']),
                    )
                    .limit(10)
                    .all()
                ) if sub_ids else (
                    s.query(UserAgent)
                    .filter(UserAgent.author_id == prof.user_id, UserAgent.status.in_(['active', 'paused']))
                    .limit(10)
                    .all()
                )
                if not agents:
                    continue
                # Cooldown: не было ли L2-координации за последние 30 минут?
                from models import Anchor
                _now = datetime.now(timezone.utc)
                recent = (
                    s.query(Anchor)
                    .filter(
                        Anchor.user_id == prof.user_id,
                        Anchor.anchor_type == 'agent_office_update',
                        Anchor.source.like('l2-coord:%'),
                        Anchor.created_at >= _now - timedelta(minutes=30),
                    )
                    .first()
                )
                if recent:
                    continue
                users_with_goals.append({
                    'user': user,
                    'user_db_id': prof.user_id,
                    'goals': [(g.id, g.title, g.progress_percentage, g.target_date) for g in goals],
                    'agents': [(a.id, a.name, a.specialization, a.description) for a in agents],
                })
        finally:
            s.close()

        if not users_with_goals:
            logger.info("[OFFICE-L2] no eligible users")
            return

        logger.info("[OFFICE-L2] processing %d users", len(users_with_goals))
        for entry in users_with_goals:
            try:
                await self._coordinate_user_goals(entry)
            except Exception as e:
                logger.warning("[OFFICE-L2] user %d error: %s", entry['user_db_id'], e)
            await asyncio.sleep(3)

    async def _coordinate_user_goals(self, entry: dict):
        """ASI анализирует цели и назначает задачу одному агенту."""
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp

        user = entry['user']
        user_db_id = entry['user_db_id']
        goals = entry['goals']
        agents = entry['agents']

        goals_text = "\n".join(
            f"- {title} [{progress or 0}%]"
            + (f" (дедлайн {target_date.strftime('%d.%m.%Y')})" if target_date else "")
            for gid, title, progress, target_date in goals
        )
        agents_text = "\n".join(
            f"- {name} ({spec or 'специалист'}): {(desc or '')[:100]}"
            for aid, name, spec, desc in agents
        )

        # Загрузим недавнюю активность чтобы не дублировать
        from models import Session as Db, AgentActivityLog
        s = Db()
        try:
            _now = datetime.now(timezone.utc)
            recent_acts = (
                s.query(AgentActivityLog)
                .filter(
                    AgentActivityLog.user_id == user_db_id,
                    AgentActivityLog.created_at >= _now - timedelta(hours=6),
                )
                .order_by(AgentActivityLog.created_at.desc())
                .limit(8)
                .all()
            )
            activity_text = "\n".join(
                f"- {a.target}: {a.title[:60]} ({a.status})"
                for a in recent_acts
            ) if recent_acts else "(нет недавней активности)"

            # ── Счётчик действий vs анализа за 24ч ──
            _action_stats_l2 = ''
            try:
                _stats_cutoff = _now - timedelta(hours=24)
                _all_acts_24 = (
                    s.query(AgentActivityLog)
                    .filter(
                        AgentActivityLog.user_id == user_db_id,
                        AgentActivityLog.created_at >= _stats_cutoff,
                    )
                    .all()
                )
                _emails_sent = 0
                _posts_made = 0
                _notes_saved = 0
                _research_done = 0
                _crm_done = 0
                _dm_sent = 0
                _campaigns = 0
                _other_actions = 0
                for _act in _all_acts_24:
                    _t = ((_act.title or '') + ' ' + (_act.content or '')).lower()
                    if any(kw in _t for kw in ('отправила письмо', 'отправил письмо', 'send_email', 'outreach')):
                        _emails_sent += 1
                    elif any(kw in _t for kw in ('опубликовала', 'опубликовал', 'publish_to', 'пост в')):
                        _posts_made += 1
                    elif any(kw in _t for kw in ('amocrm', 'сделку', 'контакт в crm')):
                        _crm_done += 1
                    elif any(kw in _t for kw in ('find_and_message', 'message_relevant', 'написал пользовател', 'dm ')):
                        _dm_sent += 1
                    elif any(kw in _t for kw in ('start_email_campaign', 'start_content_campaign', 'кампани')):
                        _campaigns += 1
                    elif any(kw in _t for kw in ('сохранил', 'сохранила', 'save_note', 'заметк')):
                        _notes_saved += 1
                    elif any(kw in _t for kw in ('проанализировал', 'исследовал', 'research', 'анализ')):
                        _research_done += 1
                    else:
                        _other_actions += 1
                _total_24 = len(_all_acts_24)
                _real_actions = _emails_sent + _posts_made + _crm_done + _dm_sent + _campaigns
                # ── Каналы: сколько задействовано из доступных ──
                _channels_used = sum(1 for c in [_emails_sent, _posts_made, _crm_done, _dm_sent, _campaigns] if c > 0)
                _action_stats_l2 = (
                    f"📊 ТВОЯ СТАТИСТИКА 24Ч (изучи и сделай выводы):\n"
                    f"  Всего действий: {_total_24}\n"
                    f"  📧 Email: {_emails_sent}  📝 Посты: {_posts_made}  💼 CRM: {_crm_done}\n"
                    f"  💬 DM (платформа): {_dm_sent}  🚀 Кампании: {_campaigns}\n"
                    f"  📋 Заметки: {_notes_saved}  🔍 Анализ: {_research_done}\n"
                    f"  Каналов задействовано: {_channels_used}/5\n"
                )
                # Самообучение: вместо директив — рефлексия
                if _total_24 > 5 and _real_actions < _total_24 * 0.3:
                    _action_stats_l2 += (
                        "🤔 РЕФЛЕКСИЯ: большинство действий = анализ/заметки. Подумай:\n"
                        "  — Хватит ли данных чтобы СДЕЛАТЬ что-то конкретное (отправить, опубликовать, написать)?\n"
                        "  — Какой канал принесёт результат быстрее всего?\n"
                    )
                if _channels_used <= 1 and _total_24 > 3:
                    _action_stats_l2 += (
                        "🤔 РЕФЛЕКСИЯ: используется только 1 канал. Подумай:\n"
                        "  — Какой ДРУГОЙ канал может дополнить текущую стратегию?\n"
                        "  — find_and_message_relevant_users = бесплатно, без лимитов\n"
                        "  — start_email_campaign = авто-поиск + до 100 писем/день одной командой\n"
                        "  — publish_to_telegram/create_post = контент привлекает аудиторию\n"
                    )
                _action_stats_l2 += "\n"
            except Exception:
                pass

            # Провалившиеся задачи за 24ч — координатор должен избегать повторов
            _failed_tasks_l2 = ''
            try:
                from models import Task as _Task_l2f
                _fail_c_l2 = _now - timedelta(hours=24)
                _ftasks = s.query(_Task_l2f).filter(
                    _Task_l2f.user_id == user_db_id,
                    _Task_l2f.source == 'agent',
                    _Task_l2f.status.in_(['cancelled', 'failed']),
                    _Task_l2f.created_at >= _fail_c_l2,
                ).order_by(_Task_l2f.created_at.desc()).limit(8).all()
                if _ftasks:
                    _fl = ['КОНТЕКСТ — ПРОВАЛИВШИЕСЯ ЗАДАЧИ 24ч (учти при планировании):']
                    for _ft in _ftasks:
                        _fl.append(f"  - {_ft.delegated_to_username or '?'}: {(_ft.title or '')[:70]}")
                    _failed_tasks_l2 = '\n'.join(_fl) + '\n'
            except Exception:
                pass

            # Также берём failed из AgentActivityLog (AAL) — они часто НЕ отражены в Task.status
            try:
                _fail_aal_cutoff = _now - timedelta(hours=12)
                _failed_aal = s.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == user_db_id,
                    AgentActivityLog.status == 'failed',
                    AgentActivityLog.created_at >= _fail_aal_cutoff,
                ).order_by(AgentActivityLog.created_at.desc()).limit(10).all()
                if _failed_aal:
                    _fl2 = ['КОНТЕКСТ — ПРОВАЛИВШИЕСЯ ДЕЙСТВИЯ 12ч (почему не сработало? что делать иначе?):']
                    for _fa in _failed_aal:
                        _agent_t = (_fa.target or '').replace('agent:', '')
                        _fl2.append(f"  - {_agent_t}: {(_fa.title or '')[:80]}")
                    _failed_tasks_l2 += '\n'.join(_fl2) + '\n'
            except Exception:
                pass
        finally:
            s.close()

        # Собираем реальные инструменты каждого агента
        _agents_caps = []
        _all_agent_keys = ''  # собираем ключи ВСЕХ агентов для проверки интеграций
        for aid, aname, aspec, adesc in agents:
            _line = f"- {aname} ({aspec or 'специалист'})"
            # Инferируем возможности из api_keys
            try:
                from models import UserAgent as _UA_coord
                _ua_c = s.query(_UA_coord).filter_by(id=aid).first()
                if _ua_c:
                    _raw_keys = getattr(_ua_c, 'user_api_keys', '') or ''
                    try:
                        from config import decrypt_token as _dt_coord
                        _keys_c = (_dt_coord(_raw_keys) if _raw_keys.startswith(('enc:', 'obf:')) else _raw_keys).lower()
                    except Exception:
                        _keys_c = _raw_keys.lower()
                    _all_agent_keys += ' ' + _keys_c
                    _caps_c = []
                    if any(k in _keys_c for k in ('smtp_', 'resend_', 'sendgrid_', 'mailgun_', 'gmail_', 'yandex_', 'mailru_')):
                        _caps_c.append('отправка email')
                    if any(k in _keys_c for k in ('imap_', 'gmail_')):
                        _caps_c.append('чтение почты')
                    if 'telegram' in _keys_c:
                        _caps_c.append('Telegram')
                    if 'github' in _keys_c:
                        _caps_c.append('GitHub search_users→save_email_contact→send_outreach_email')
                    if any(k in _keys_c for k in ('rss_', 'rss')):
                        _caps_c.append('RSS-мониторинг')
                    if any(k in _keys_c for k in ('amo_', 'amocrm')):
                        _caps_c.append('AmoCRM (сделки, контакты)')
                    if any(k in _keys_c for k in ('alphavantage', 'alpha_vantage')):
                        _caps_c.append('биржевые данные (Alpha Vantage)')
                    if any(k in _keys_c for k in ('slack', 'slack_bot')):
                        _caps_c.append('Slack')
                    if any(k in _keys_c for k in ('notion', 'notion_token')):
                        _caps_c.append('Notion')
                    if any(k in _keys_c for k in ('linkedin', 'li_at')):
                        _caps_c.append('LinkedIn')
                    if any(k in _keys_c for k in ('vk_', 'vkontakte')):
                        _caps_c.append('ВКонтакте')
                    if any(k in _keys_c for k in ('twitter', 'x_api')):
                        _caps_c.append('Twitter/X')
                    if any(k in _keys_c for k in ('youtube', 'yt_api')):
                        _caps_c.append('YouTube')
                    if any(k in _keys_c for k in ('jira', 'atlassian')):
                        _caps_c.append('Jira')
                    if any(k in _keys_c for k in ('trello',)):
                        _caps_c.append('Trello')
                    if any(k in _keys_c for k in ('hubspot',)):
                        _caps_c.append('HubSpot')
                    if any(k in _keys_c for k in ('google_sheets', 'gsheets')):
                        _caps_c.append('Google Sheets')
                    if _caps_c:
                        _line += f" [{', '.join(_caps_c)}]"
                    else:
                        _line += " [веб-поиск, исследования, создание постов]"
            except Exception:
                pass
            _agents_caps.append(_line)
        agents_caps_text = "\n".join(_agents_caps)

        # Определяем подключённые интеграции пользователя
        _user_obj = entry.get('user')
        _connected = []
        _missing = []
        if _user_obj:
            if getattr(_user_obj, 'telegram_channel', None):
                _connected.append('Telegram-канал')
            else:
                _missing.append('Telegram-канал')
            if getattr(_user_obj, 'discord_webhook', None):
                _connected.append('Discord')
            else:
                _missing.append('Discord')
            if getattr(_user_obj, 'google_oauth_token', None):
                _connected.append('Google/Gmail OAuth')
            else:
                _missing.append('Google/Gmail OAuth')
        # Динамически определяем недоступные сервисы — только те, что НЕ найдены нигде
        _truly_unavailable = ['Calendly', 'Apollo.io', 'Sales Navigator']  # эти платформа не поддерживает
        _conditionally_available = {
            'LinkedIn': ('linkedin', 'li_at'),
            'Slack': ('slack',),
            'Notion': ('notion',),
            'ВКонтакте': ('vk_',),
            'Twitter/X': ('twitter', 'x_api'),
            'YouTube': ('youtube', 'yt_api'),
        }
        for _svc_name, _svc_keys in _conditionally_available.items():
            if any(k in _all_agent_keys for k in _svc_keys):
                if _svc_name not in _connected:
                    _connected.append(_svc_name)
            else:
                _missing.append(_svc_name)
        _missing.extend(_truly_unavailable)
        _integrations_block = ''
        if _missing:
            _integrations_block = (
                "⛔ НЕДОСТУПНЫЕ СЕРВИСЫ (НЕ назначай задачи с ними): "
                + ", ".join(_missing) + "\n"
            )
        if _connected:
            _integrations_block += (
                "✅ Подключено: " + ", ".join(_connected) + "\n"
            )
        _integrations_block += "\n"

        # ── Правила пользователя из memory['rules'] ───
        _user_rules_block = ''
        try:
            from ai_integration.memory import decrypt_data as _dec_rules_coord
            _mem_raw_coord = _dec_rules_coord(user.memory) if user.memory else '{}'
            _mem_dict_coord = json.loads(_mem_raw_coord) if _mem_raw_coord else {}
            _u_rules = _mem_dict_coord.get('rules', [])
            if _u_rules:
                _rules_lines = '\n'.join(f'  • {r}' for r in _u_rules[:10])
                _user_rules_block = f"📌 ПРАВИЛА ПОЛЬЗОВАТЕЛЯ (обязательны к исполнению):\n{_rules_lines}\n\n"
        except Exception:
            pass

        prompt = (
            "Ты — ASI-координатор. Назначаешь агентам КОНКРЕТНЫЕ микро-задачи, которые двигают цели вперёд.\n\n"
            f"{_user_rules_block}"
            f"ЦЕЛИ:\n{goals_text}\n\n"
            f"АГЕНТЫ (и их реальные возможности):\n{agents_caps_text}\n\n"
            f"{_integrations_block}"
            f"{_action_stats_l2}"
            f"АКТИВНОСТЬ ЗА 6Ч:\n{activity_text}\n\n"
            f"{_failed_tasks_l2}"

            "🧠 САМОПРОВЕРКА (выполни ПЕРЕД назначением):\n"
            "Прежде чем давать задачу — изучи АКТИВНОСТЬ ЗА 6Ч и ответь себе:\n"
            "  1. Какие задачи УЖЕ давались? Какой был результат?\n"
            "  2. Прогресс целей ВЫРОС за последние циклы? Если нет — прошлый подход НЕ РАБОТАЕТ.\n"
            "  3. Одна и та же формулировка ('найди контакты на...') повторялась 2+ раз? → Это зацикливание. Предложи ПРИНЦИПИАЛЬНО другое.\n"
            "  4. Чем НОВАЯ задача отличается от предыдущей? Если ответ 'почти ничем' — ты зациклился.\n"
            "Если подход не работает — не повторяй его с другой формулировкой. Смени СТРАТЕГИЮ:\n"
            "  — другая аудитория, другая площадка, другой формат, другой инструмент\n"
            "  — может стоить сделать паузу и выбрать 'wait' с объяснением почему\n\n"

            "КОМАНДНАЯ СТРАТЕГИЯ:\n"
            "Агенты — КОМАНДА. Каждый делает СВОЁ, исходя из ЦЕЛИ пользователя:\n"
            "  БИЗНЕС/OUTREACH-цели:\n"
            "  • Агент С email+GitHub → МАССОВЫЙ OUTREACH одной задачей: «Найди 15-20 профилей через search_users по [критерий цели], сохрани контакты (save_email_contact), отправь каждому персональное письмо (send_outreach_email)». Это ОДНА задача = десятки писем.\n"
            "  • Агент С email (без GitHub) → ЦЕПОЧКИ: check_emails → reply_to_outreach_email → фоллоу-апы всем неответившим. Не «напиши 1 письмо», а «обработай все входящие и отправь фоллоу-апы».\n"
            "  • Агент БЕЗ email → КОНТЕНТ/РЕСЁРЧ: посты, тренды, аналитика, публикации\n"
            "  💡 КОНТЕНТ-СТРАТЕГИЯ: давая задачу на пост/публикацию — УКАЗЫВАЙ ФОРМАТ и ПОДХОД:\n"
            "    — 'research_topic → найди свежую статистику по [тема] → create_post с кейсом и цифрами'\n"
            "    — 'generate_image стиль=photorealistic → create_post с визуалом + короткий вывод'\n"
            "    — 'web_search свежие тренды → пост-сравнение: было/стало или миф/реальность'\n"
            "    Меняй формат каждый цикл: кейс → совет → статистика → история → вопрос аудитории → визуал.\n"
            "    Если последний пост был текстовый — дай задачу с generate_image. И наоборот.\n"
            "  ОБУЧЕНИЕ/РАЗВИТИЕ/ЛИЧНЫЕ цели:\n"
            "  • Один агент → ИССЛЕДОВАНИЕ: research_topic по теме цели, поиск материалов/курсов/экспертов\n"
            "  • Другой агент → ПРАКТИКА: создаёт учебные заметки (save_note), планирует шаги (add_task), трекает прогресс\n"
            "  ЗДОРОВЬЕ/СПОРТ/ХОББИ:\n"
            "  • Один агент → МОНИТОРИНГ: web_search актуальных методик, research_topic для анализа подходов\n"
            "  • Другой агент → ПЛАНИРОВАНИЕ: add_task конкретных действий, set_reminder для регулярных шагов\n"
            "  УНИВЕРСАЛЬНО: задачи должны ДОПОЛНЯТЬ друг друга, не дублировать.\n\n"

            "ДОСТУПНЫЕ КАНАЛЫ:\n"
            "Ты знаешь ТОЛЬКО о подключённых интеграциях — email, Telegram, Discord, web_search, RSS и те что перечислены выше.\n"
            "Если интеграции нет в списке — для тебя она НЕ СУЩЕСТВУЕТ. Не упоминай её в задачах.\n\n"

            "ПРАВИЛА НАЗНАЧЕНИЯ:\n"
            "1. Задача = ОДНО конкретное действие с измеримым результатом (30-60 слов).\n"
            "   МИНИМАЛЬНЫЙ СТАНДАРТ: в задаче ОБЯЗАН быть конкретный инструмент + конкретное действие + ожидаемый результат.\n"
            "   Задача без инструмента = пустышка. «Посмотри что можно сделать» — БЕСПОЛЕЗНО.\n"
            "   ХОРОШО: «Проверь входящие через check_emails. Если есть ответы — ответь через reply_to_outreach_email.»\n"
            "   ХОРОШО: «Исследуй тему X через research_topic, подготовь конспект через save_note.»\n"
            "   ХОРОШО: «Найди через web_search актуальную программу тренировок для цели Y, создай план через add_task.»\n"
            "   ХОРОШО: «Подготовь экспертный пост (research_topic → create_post) и опубликуй через publish_to_telegram.»\n"
            "   ПЛОХО: «Проведи анализ» без конкретного инструмента и результата.\n"
            "2. Называй КОНКРЕТНЫЙ инструмент: web_search, research_topic, create_post, add_task, save_note, set_reminder, check_emails, send_outreach_email, publish_to_telegram,\n"
            "   find_and_message_relevant_users (поиск и рассылка пользователям платформы — БЕСПЛАТНО, без лимитов!),\n"
            "   start_email_campaign (ЛУЧШИЙ инструмент для массового outreach — создаёт кампанию, автоматически ищет контакты и рассылает до 50 писем/день),\n"
            "   negotiate_by_email (многошаговые переговоры по email), start_content_campaign (серия публикаций),\n"
            "   generate_image (визуал для постов), publish_to_discord (публикация в Discord), set_contact_alert (мониторинг контакта).\n"
            "3. Каждый агент получает РАЗНУЮ задачу.\n"
            "4. Агент БЕЗ email-ключей НЕ МОЖЕТ отправлять/читать письма.\n"
            "5. Если цель имеет конкретный дедлайн — urgency=high.\n"
            "6. Если все задачи уже выполняются или прогресс застрял и новый подход не ясен — 'wait' с объяснением.\n"
            "7. Задача ДОЛЖНА быть полной цепочкой до конкретного результата.\n"
            "8. РЕФЛЕКСИЯ КАНАЛОВ: посмотри СТАТИСТИКУ 24Ч выше. Подумай:\n"
            "   — Какие каналы НЕ задействованы? Может ли другой канал дополнить стратегию?\n"
            "   — Много анализа/заметок, но мало реальных действий? Значит данных достаточно — пора действовать.\n"
            "   — Один канал = одна точка отказа. Диверсификация повышает шансы на результат.\n"
            "   💡 Стоимость инструментов: find_and_message_relevant_users = бесплатно, без лимитов; start_email_campaign = 1 вызов → до 100 писем/день; publish_to_telegram = мгновенная публикация.\n"
            "   ⚡ ОТВЕТЫ НА ВХОДЯЩИЕ — ВЫСШИЙ ПРИОРИТЕТ: если есть агент с email → ПЕРВАЯ задача = «check_emails. Если есть ответы — reply_to_outreach_email с персональным предложением.»\n"
            "9. find_and_message_relevant_users — МОЩНЫЙ канал: ищи пользователей платформы по теме цели и пиши им напрямую. Это бесплатно.\n"
            "10. OUTREACH = ПАКЕТАМИ, НЕ ПОШТУЧНО. Никогда не давай задачу «напиши письмо Ивану».\n"
            "   ЛУЧШИЙ ВАРИАНТ: «Запусти email-кампанию (start_email_campaign) по [аудитория] с предложением [оффер]» — одна команда = автоматический поиск + до 50 писем/день.\n"
            "   АЛЬТЕРНАТИВА: «Найди 15-20 контактов через search_users по [критерий] и отправь каждому через send_outreach_email».\n"
            "   Одна задача агента должна генерировать 10-50 писем за цикл, а не одно.\n\n"
            "Ответь JSON (без ```):\n"
            '{"action": "delegate", "agent_name": "имя", "task": "...", "goal": "...", "urgency": "normal"}\n'
            'или {"action": "delegate_multiple", "assignments": [{"agent_name": "...", "task": "...", "goal": "...", "urgency": "normal"}, ...]}\n'
            'или {"action": "wait", "reason": "..."}\n'
            "ТОЛЬКО JSON, без пояснений."
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                    json={"model": DEEPSEEK_MODEL, "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 800, "temperature": 0.4},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("[OFFICE-L2] API error: %d", resp.status)
                        return
                    data = await resp.json()
                    answer = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("[OFFICE-L2] AI call error: %s", str(e) or type(e).__name__)
            return

        import json as _json
        import re as _re
        _jm = _re.search(r'\{[\s\S]*\}', answer)
        try:
            plan = _json.loads(_jm.group(0) if _jm else answer)
        except Exception:
            logger.debug("[OFFICE-L2] JSON parse failed: %s", answer[:100])
            return

        action = plan.get('action', 'wait')
        if action == 'wait':
            logger.info("[OFFICE-L2] user=%d: wait — %s", user_db_id, plan.get('reason', '')[:60])
            return

        # Нормализуем: single delegate и delegate_multiple → общий список
        if action == 'delegate_multiple':
            _assignments = plan.get('assignments', [])[:3]  # max 3 агента за цикл
        elif action == 'delegate':
            _an = (plan.get('agent_name') or plan.get('agent') or '').strip()
            _at = plan.get('task', '').strip()
            _ag = plan.get('goal', '').strip()
            if not _an or not _at:
                return
            _assignments = [{'agent_name': _an, 'task': _at, 'goal': _ag,
                             'urgency': plan.get('urgency', 'normal')}]
        else:
            return

        from models import Session as Db2, Anchor, AnchorPriority
        _now = datetime.now(timezone.utc)

        for _asgn in _assignments:
            _aname = (_asgn.get('agent_name') or _asgn.get('agent') or '').strip()
            _atask = (_asgn.get('task') or '').strip()
            _agoal = (_asgn.get('goal') or '').strip()
            _urgency = _asgn.get('urgency', 'normal')
            if not _aname or not _atask:
                continue

            # ── Safety net: логируем если ИИ ещё упоминает недоступные сервисы (промпт учит не делать этого) ──
            _has_banned = any(s in _atask.lower() for s in ('linkedin', 'calendly', 'apollo.io', 'sales navigator'))
            if _has_banned:
                logger.info("[OFFICE-L2] TEACH-MISS: task mentions unavailable service, stripping: %s", _atask[:80])
                for _bs in ('linkedin', r'calendly', r'apollo\.io', 'sales navigator'):
                    _atask = _re.sub(
                        rf'[^.!?\n]*\b{_bs}\b[^.!?\n]*[.!?]?\s*',
                        '', _atask, flags=_re.IGNORECASE
                    )
                _atask = _atask.strip()
                if not _atask:
                    continue

            # ── Safety net: мягкий антилуп (промпт учит не повторяться, но на всякий случай) ──
            try:
                _task_words = set(w.lower() for w in _atask.split() if len(w) > 4)
                _s_dup = Db2()
                try:
                    _dup_cutoff = _now - timedelta(hours=24)
                    _recent_aal = _s_dup.query(AgentActivityLog).filter(
                        AgentActivityLog.user_id == user_db_id,
                        AgentActivityLog.activity_type == 'agent_task',
                        AgentActivityLog.target == f'agent:{_aname}',
                        AgentActivityLog.created_at >= _dup_cutoff,
                    ).order_by(AgentActivityLog.created_at.desc()).limit(10).all()

                    _high_overlap_count = 0
                    for _aal_row in _recent_aal:
                        _old_text = ((_aal_row.title or '') + ' ' + (_aal_row.content or '')).lower()
                        _old_words = set(w for w in _old_text.split() if len(w) > 4)
                        if _old_words and _task_words:
                            _overlap = len(_task_words & _old_words) / min(len(_task_words), len(_old_words))
                            if _overlap > 0.5:
                                _high_overlap_count += 1
                    if _high_overlap_count >= 2:
                        logger.info("[OFFICE-L2] TEACH-MISS antiloop: user=%d, %s has %d similar tasks in 24h — skipping: %s",
                                    user_db_id, _aname, _high_overlap_count, _atask[:60])
                        continue
                finally:
                    _s_dup.close()
            except Exception as _dup_err:
                logger.debug("[OFFICE-L2] antiloop check failed: %s", _dup_err)

            # Находим агента
            _agent_match = next(
                (a for a in agents if a[1] and a[1].lower() == _aname.lower()),
                next((a for a in agents if a[1] and _aname.lower() in a[1].lower()), None)
            )
            if not _agent_match:
                logger.debug("[OFFICE-L2] agent not found: %s", _aname)
                continue

            _agent_id, _agent_name_db, _agent_spec, _agent_desc = _agent_match

            # Приоритет под urgency
            _anchor_priority = AnchorPriority.HIGH if _urgency == 'high' else AnchorPriority.LOW

            # Создаём задачу-делегирование + лог (без лишних якорей/сообщений)
            # NB: _auto_delegate_to_agent_sync создаёт agent_task(status='accepted').
            # Раньше здесь был _log_agent_activity_sync, который создавал второй agent_task
            # с status='completed' и без result — ложное завершение.
            _auto_delegate_to_agent_sync(
                user_db_id, _agent_id, _agent_name_db,
                f'Цель: {_agoal[:80]}. {_atask[:200]}',
            )

            logger.info("[OFFICE-L2] user=%d: delegated to %s [%s]: %s",
                        user_db_id, _agent_name_db, _urgency, _atask[:60])


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton + публичный API

_engine: OfficeEngine | None = None


def get_office_engine() -> OfficeEngine:
    global _engine
    if _engine is None:
        _engine = OfficeEngine()
    return _engine


def start_office_engine():
    """Запускает Living Office Engine. Вызывается из main.py при старте сервера."""
    engine = get_office_engine()

    async def _supervisor():
        delay = 60
        while True:
            try:
                engine.running = True
                await engine.start()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("[OFFICE] engine crashed: %s, restart in %ds", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)
            else:
                await asyncio.sleep(30)

    asyncio.ensure_future(_supervisor())
    logger.info("[OFFICE] Living Office Engine scheduled")


# ─── Ручной запуск одного цикла: python -m ai_integration.office_engine --once ──

async def _run_once_live():
    """Один боевой прогон office engine с выводом в stdout.
    Сбрасывает cooldown всех агентов → запускает скрипты прямо сейчас.
    """
    import logging as _lg
    _lg.basicConfig(
        level=_lg.INFO,
        format='%(asctime)s [%(name)s] %(message)s',
        datefmt='%H:%M:%S',
    )
    # Verbose режим для office_engine
    _lg.getLogger('ai_integration.office_engine').setLevel(_lg.DEBUG)

    # Сбрасываем cooldown всех агентов пользователя — чтобы не ждать 25 мин
    try:
        from models import Session as _Db, UserAgent as _UA
        _s = _Db()
        try:
            _s.query(_UA).filter(_UA.status.in_(['active', 'paused'])).update(
                {'last_office_run_at': None}, synchronize_session=False
            )
            _s.commit()
            cnt = _s.query(_UA).filter(_UA.status.in_(['active', 'paused'])).count()
            logger.info('[ОДИНОЧНЫЙ ПРОГОН] Агентов к запуску: %d', cnt)
        finally:
            _s.close()
    except Exception as e:
        logger.warning('[WARN] cooldown reset: %s', e)

    engine = OfficeEngine()
    await engine._run_all_agent_scripts()

    # Итоги: что было записано в БД за этот прогон
    try:
        from models import Session as _Db2, Interaction, Task, EmailContact, AgentActivityLog
        _s2 = _Db2()
        try:
            import datetime as _dt
            _since = _dt.datetime.utcnow() - _dt.timedelta(minutes=5)
            new_msgs   = _s2.query(Interaction).filter(Interaction.created_at >= _since).count()
            new_tasks  = _s2.query(Task).filter(
                Task.delegated_to_username.like('agent:%'),
                Task.created_at >= _since
            ).count()
            new_emails = _s2.query(EmailContact).filter(EmailContact.created_at >= _since).count()
            new_acts   = _s2.query(AgentActivityLog).filter(AgentActivityLog.created_at >= _since).count()
            logger.info('[ИТОГИ] сообщений в чат: %d | делегирований: %d '
                  '| email-контактов: %d | activity log: %d', new_msgs, new_tasks, new_emails, new_acts)
        finally:
            _s2.close()
    except Exception as e:
        logger.warning('[WARN] summary error: %s', e)


if __name__ == '__main__':
    import sys as _sys
    if '--once' in _sys.argv:
        import os as _os
        _os.environ.setdefault('LOCAL', '1')
        _os.environ.setdefault('BOT_TOKEN', 'dummy')
        asyncio.run(_run_once_live())
    else:
        print('Usage: python -m ai_integration.office_engine --once')
