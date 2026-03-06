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
OFFICE_INTERVAL_SEC  = (2 * 3600, 4 * 3600) # 2-4 ч между координаторскими сессиями
from config import API_TIMEOUT_SCRIPT
SCRIPT_TIMEOUT_SEC   = API_TIMEOUT_SCRIPT    # таймаут на один скрипт агента


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


def _save_office_anchor_sync(user_id: int, agent_name: str, text: str):
    """Создаёт agent_office_update Anchor — AnchorEngine сам решит когда доставить в TG:
    учитывает ночные часы, cooldown, батчинг группы 'integration'.
    Cooldown: один якорь на агента в час (дедупликация по source).
    """
    try:
        from models import Session as _Db, Anchor as _An, AnchorPriority as _AP
        _now = datetime.now(timezone.utc)
        _src = f'office-report:{agent_name}:{_now.strftime("%Y-%m-%d-%H")}'
        _s = _Db()
        try:
            if _s.query(_An).filter_by(user_id=user_id, source=_src).first():
                return  # уже есть в пределах часа
            _s.add(_An(
                user_id=user_id,
                anchor_type='agent_office_update',
                source=_src,
                topic=f'{agent_name}: {text[:80]}',
                priority=_AP.MEDIUM,
                data=json.dumps({'agent': agent_name, 'report': text[:500]}, ensure_ascii=False),
                triggered_at=_now,
                expires_at=_now + timedelta(hours=8),
                cooldown_hours=1,
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
    """
    try:
        import json as _json
        from models import Session as _Db, Interaction
        content = _json.dumps({
            '__agent': {
                'name': agent_name,
                'id': agent_id,
                'avatar_url': avatar_url or '',
            },
            'text': text,
        }, ensure_ascii=False)
        _s = _Db()
        try:
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
    Дедупликация: не создаём дубль если уже есть идентичная запись за 2ч.
    """
    try:
        from models import Session as _Db, AgentActivityLog as _AAL
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _s = _Db()
        try:
            _cutoff = _dt.now(_tz.utc) - _td(hours=2)
            _dup = _s.query(_AAL).filter(
                _AAL.user_id == user_id,
                _AAL.target == f'agent:{agent_name}',
                _AAL.activity_type == 'delegation',
                _AAL.status == 'accepted',
                _AAL.created_at >= _cutoff,
            ).first()
            if _dup and task_title[:40].lower() in (_dup.title or '').lower():
                return
            _s.add(_AAL(
                user_id=user_id,
                activity_type='delegation',
                title=f'Задача поставлена: {agent_name}',
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
        _s = _Db()
        try:
            _s.add(_AAL(
                user_id=user_id,
                activity_type='delegation',
                title=f'Задача выполнена: {agent_name}',
                content=task_title[:500],
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
        return result.stdout[:2000].strip(), result.stderr[:400].strip()
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


def _save_inbox_reply_sync(user_id: int, agent_name: str, stdout: str):
    """Детектирует входящие письма в stdout IMAP-агента и сохраняет в AgentActivityLog.
    AnchorEngine подберёт запись и создаст CRITICAL-якорь agent_inbox_reply.
    """
    if not stdout:
        return
    # Признак: stdout содержит реальные письма (шаблоны Gmail/Yandex/Mail.ru)
    has_from = 'От:' in stdout
    has_subj = 'Тема:' in stdout
    if not (has_from and has_subj):
        return
    # Считаем количество писем (каждое письмо имеет "От:")
    reply_count = stdout.count('От:')
    try:
        from models import Session as _Db, AgentActivityLog
        _s = _Db()
        try:
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
                    _UA.status == 'active',
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
            from models import Session as Db, UserAgent, User as UserModel
            s = Db()
            try:
                rows = (
                    s.query(UserAgent, UserModel)
                    .join(UserModel, UserModel.id == UserAgent.author_id)
                    .filter(
                        UserAgent.status == 'active',
                        UserAgent.python_code.isnot(None),
                        UserModel.telegram_id.isnot(None),
                    )
                    .all()
                )
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
            except Exception:
                pass

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
                service_label = (agent.specialization or agent.name or 'Agent').strip()
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        spawn_integration_anchors,
                        user.id,
                        agent.name or 'Агент',
                        service_label,
                        stdout[:1000],
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
                            agent.avatar_url or '',
                            report,
                            False,  # internal=False: отчёт агента виден в чате
                        )
                        logger.info("[OFFICE-L1] [%s] report saved (visible) for user %d", agent.name, user.id)
                        # Anchor → AnchorEngine доставит в TG с учётом ночного времени и cooldown
                        try:
                            await loop.run_in_executor(
                                None, _save_office_anchor_sync,
                                user.id, agent.name or 'Агент', report,
                            )
                        except Exception:
                            pass
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
                        except Exception:
                            pass
                        # Детектируем входящие письма в stdout (IMAP agents)
                        try:
                            await loop.run_in_executor(
                                None, _save_inbox_reply_sync,
                                user.id, agent.name or 'Агент', stdout,
                            )
                        except Exception:
                            pass
                        # Детектируем BLOCKED-маркер в отчёте агента
                        try:
                            await loop.run_in_executor(
                                None, _save_task_blocked_sync,
                                user.id, agent.name or 'Агент', report,
                            )
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug("[OFFICE-L1] [%s] report/reaction error: %s", agent.name, e)

            elif stderr and 'timeout' not in stderr:
                logger.debug("[OFFICE-L1] [%s] stderr: %s", agent.name, stderr[:150])

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
            except Exception:
                pass
        _ctx_block = f"\n\nКонтекст о пользователе:\n{_user_ctx[:400]}" if _user_ctx else ''

        prompt = (
            f"Ты — {agent_name}, {agent_spec}. Ты только что выполнил мониторинг и получил следующие данные:\n\n"
            f"{clean[:600]}\n\n"
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
                    .filter_by(user_id=prof.user_id, is_active=True)
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
                # Cooldown: не было ли L2-координации за последние 2 часа?
                from models import Anchor
                _now = datetime.now(timezone.utc)
                recent = (
                    s.query(Anchor)
                    .filter(
                        Anchor.user_id == prof.user_id,
                        Anchor.anchor_type == 'agent_office_update',
                        Anchor.source.like('l2-coord:%'),
                        Anchor.created_at >= _now - timedelta(hours=2),
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
        finally:
            s.close()

        prompt = (
            "Ты — ASI-координатор. Проанализируй цели пользователя и его агентов.\n"
            "Если есть конкретное действие, которое агент может сделать прямо сейчас "
            "для продвижения цели — назначь его. Если всё в порядке — скажи wait.\n\n"
            f"ЦЕЛИ:\n{goals_text}\n\n"
            f"АГЕНТЫ:\n{agents_text}\n\n"
            f"АКТИВНОСТЬ ЗА 6Ч:\n{activity_text}\n\n"
            "Ответь JSON (без ```):\n"
            '{"action": "delegate", "agent_name": "имя", "task": "конкретная задача", "goal": "для какой цели"}\n'
            'или {"action": "wait", "reason": "почему"}\n'
            "Не повторяй то, что уже делалось за 6ч. Ответь ТОЛЬКО JSON."
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                    json={"model": DEEPSEEK_MODEL, "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 200, "temperature": 0.3},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("[OFFICE-L2] API error: %d", resp.status)
                        return
                    data = await resp.json()
                    answer = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("[OFFICE-L2] AI call error: %s", e)
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

        if action != 'delegate':
            return

        agent_name = plan.get('agent_name', '')
        task = plan.get('task', '')
        goal_text = plan.get('goal', '')
        if not agent_name or not task:
            return

        # Находим агента
        agent_match = next(
            (a for a in agents if a[1] and a[1].lower() == agent_name.lower()),
            next((a for a in agents if a[1] and agent_name.lower() in a[1].lower()), None)
        )
        if not agent_match:
            logger.debug("[OFFICE-L2] agent not found: %s", agent_name)
            return

        agent_id, agent_name_db, agent_spec, agent_desc = agent_match

        # Создаём задачу-делегирование + якорь + лог
        _auto_delegate_to_agent_sync(user_db_id, agent_id, agent_name_db, task[:200])

        # Якорь координации (cooldown 2ч)
        _now = datetime.now(timezone.utc)
        _save_office_anchor_sync(
            user_db_id, agent_name_db,
            f"L2: поручил «{task[:80]}» для цели «{goal_text[:60]}»"
        )

        # Дополнительный якорь для cooldown-проверки L2
        from models import Session as Db2, Anchor, AnchorPriority
        s2 = Db2()
        try:
            s2.add(Anchor(
                user_id=user_db_id,
                anchor_type='agent_office_update',
                source=f'l2-coord:{_now.strftime("%Y-%m-%d-%H")}',
                topic=f'L2: {agent_name_db} → {task[:60]}',
                priority=AnchorPriority.LOW,
                data=_json.dumps(plan, ensure_ascii=False),
                triggered_at=_now,
                expires_at=_now + timedelta(hours=8),
                cooldown_hours=2,
                batch_group='integration',
            ))
            s2.commit()
        except Exception:
            s2.rollback()
        finally:
            s2.close()

        # Логируем
        _log_agent_activity_sync(
            user_db_id, agent_name_db, agent_id,
            f'L2 координация: {task[:120]}',
            f'Цель: {goal_text}. Задача: {task}',
            activity_type='delegation',
        )

        logger.info("[OFFICE-L2] user=%d: delegated to %s: %s", user_db_id, agent_name_db, task[:60])


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
            print(f'\n[ОДИНОЧНЫЙ ПРОГОН] Агентов к запуску: {cnt}')
        finally:
            _s.close()
    except Exception as e:
        print(f'[WARN] cooldown reset: {e}')

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
            print(f'\n[ИТОГИ] сообщений в чат: {new_msgs} | делегирований: {new_tasks} '
                  f'| email-контактов: {new_emails} | activity log: {new_acts}')
        finally:
            _s2.close()
    except Exception as e:
        print(f'[WARN] summary error: {e}')


if __name__ == '__main__':
    import sys as _sys
    if '--once' in _sys.argv:
        import os as _os
        _os.environ.setdefault('LOCAL', '1')
        _os.environ.setdefault('BOT_TOKEN', 'dummy')
        asyncio.run(_run_once_live())
    else:
        print('Usage: python -m ai_integration.office_engine --once')
