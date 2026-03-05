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
MONITOR_INTERVAL_SEC = (25 * 60, 45 * 60)   # 25-45 мин между прогонами скриптов
OFFICE_INTERVAL_SEC  = (2 * 3600, 4 * 3600) # 2-4 ч между координаторскими сессиями
SCRIPT_TIMEOUT_SEC   = 18                    # таймаут на один скрипт агента


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
    """Создаёт Task-делегирование от пользователя к агенту (agent:<name>).
    Агент автоматически принимает — delegation_status='accepted'.
    Дедупликация: не создаём если уже есть активная задача с тем же заголовком.
    """
    try:
        from models import Session as _Db, Task as _Task
        _s = _Db()
        try:
            _dup = _s.query(_Task).filter(
                _Task.user_id == user_id,
                _Task.delegated_to_username == f'agent:{agent_name}',
                _Task.delegation_status.in_(['pending', 'accepted']),
                _Task.title.ilike(f'%{task_title[:40]}%'),
            ).first()
            if _dup:
                return
            _s.add(_Task(
                user_id=user_id,
                title=task_title[:200],
                delegated_by=user_id,
                delegated_to_username=f'agent:{agent_name}',
                delegation_status='accepted',
                status='in_progress',
                delegation_details=f'agent_id:{agent_id}',
            ))
            _s.commit()
            logger.debug('[OFFICE] agent delegation task created: user=%d agent=%s "%s"', user_id, agent_name, task_title[:60])
        finally:
            _s.close()
    except Exception as e:
        logger.debug('[OFFICE] agent delegation create error: %s', e)


import re as _re
_EMAIL_RE = _re.compile(r'[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9.\-]{2,}')
_GENERIC_DOMAINS = {'noreply', 'no-reply', 'donotreply', 'mailer', 'support', 'info',
                    'admin', 'webmaster', 'postmaster', 'sales', 'hello', 'contact'}


def _auto_extract_email_contacts_sync(user_id: int, stdout: str, agent_name: str):
    """Парсит stdout агента на email-адреса и сохраняет новые в EmailContact.
    Пропускает родовые/безличные адреса (info@, noreply@ и т.д.).
    Лимит: 5 адресов за один прогон, чтобы не засорять справочник.
    """
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


def _log_agent_activity_sync(user_id: int, agent_name: str, agent_id: int, title: str, content: str):
    """Пишет запись в AgentActivityLog — отображается в разделе Activities дашборда (SSE-поток)."""
    try:
        from models import Session as _Db, AgentActivityLog as _AAL
        _s = _Db()
        try:
            _s.add(_AAL(
                user_id=user_id,
                activity_type='other',
                title=title[:295],
                content=content[:2000],
                target=agent_name,
                status='completed',
                ref_id=agent_id,
            ))
            _s.commit()
            logger.debug('[OFFICE] activity log saved: user=%d agent=%s', user_id, agent_name)
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


# ═══════════════════════════════════════════════════════════════════════════════
class OfficeEngine:
    """
    Центр управления офисом. Два независимых цикла:
      _level1_monitor_loop  — фоновые скрипты агентов
      _level2_coordinator_loop — АСИ-координатор задач
    """

    def __init__(self):
        self.running = False
        # Не более 4 скриптов одновременно (IO-bound subprocess)
        self._script_sem = asyncio.Semaphore(4)
        # Не более 6 AI-вызовов одновременно
        self._ai_sem = asyncio.Semaphore(6)

    async def start(self):
        self.running = True
        logger.info("[OFFICE] Living Office Engine started")
        await asyncio.gather(
            self._level1_monitor_loop(),
            self._level2_coordinator_loop(),
            return_exceptions=True,
        )

    # ─── Уровень 1: мониторинг скриптов ──────────────────────────────────────

    async def _level1_monitor_loop(self):
        """Запускает python_code всех активных агентов каждые 25-45 мин."""
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
                _min_interval = MONITOR_INTERVAL_SEC[0]  # 25 мин
                if _elapsed < _min_interval:
                    _remaining = _min_interval - _elapsed
                    logger.info(
                        "[OFFICE-L1] Last agent run %.0f min ago — waiting %.0f min before first run",
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
            wait = random.randint(*MONITOR_INTERVAL_SEC)
            logger.info("[OFFICE-L1] next run in %.0f min", wait / 60)
            await asyncio.sleep(wait)

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

        # Персистентный cooldown: пропускаем агента если он запускался < MONITOR_INTERVAL_SEC[0] назад.
        # Защищает от лавины отчётов сразу после рестарта деплоя.
        if agent.last_office_run_at is not None:
            try:
                _last = agent.last_office_run_at
                if _last.tzinfo is None:
                    _last = _last.replace(tzinfo=timezone.utc)
                _elapsed = (datetime.now(timezone.utc) - _last).total_seconds()
                if _elapsed < MONITOR_INTERVAL_SEC[0]:
                    logger.debug(
                        "[OFFICE-L1] [%s] skipped (ran %.0f min ago, cooldown %.0f min)",
                        agent.name, _elapsed / 60, MONITOR_INTERVAL_SEC[0] / 60,
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
                        # Логируем в AgentActivityLog → dashboard Activities feed
                        try:
                            _act_title_raw = (report.splitlines()[0].strip()[:120] if report else f'{agent.name}: мониторинг')
                            _act_title = f'{agent.name}: {_act_title_raw}'
                            await loop.run_in_executor(
                                None,
                                _log_agent_activity_sync,
                                user.id, agent.name or 'Агент', agent.id,
                                _act_title, report[:2000],
                            )
                        except Exception as _le:
                            logger.debug("[OFFICE-L1] [%s] activity log error: %s", agent.name, _le)
                        # ASI реагирует на находку агента — предлагает действие
                        try:
                            await self._asi_react_to_agent_output(agent, user, stdout)
                        except Exception as _re:
                            logger.debug("[OFFICE-L1] [%s] ASI reaction error: %s", agent.name, _re)
                except Exception as e:
                    logger.debug("[OFFICE-L1] [%s] report/reaction error: %s", agent.name, e)

                # Агент отвечает на реакцию ASI — замыкает командный диалог
                try:
                    await self._post_agent_followup(agent, user)
                except Exception as e:
                    logger.debug("[OFFICE-L1] [%s] followup error: %s", agent.name, e)

            elif stderr and 'timeout' not in stderr:
                logger.debug("[OFFICE-L1] [%s] stderr: %s", agent.name, stderr[:150])

    async def _asi_react_to_agent_output(self, agent, user, output: str):
        """ASI анализирует что нашёл агент и предлагает конкретное действие в чат.

        Это event-driven офис: агент находит новость/письмо/заказ →
        ASI тут же говорит «вот что это значит для тебя и что можно сделать».
        """
        # Cooldown: не реагируем чаще раза в 1 час на одного агента
        try:
            from models import Session as _Db, Anchor as _Anch
            _s = _Db()
            try:
                _since = datetime.now(timezone.utc) - timedelta(hours=1)
                _recent = (
                    _s.query(_Anch)
                    .filter(
                        _Anch.user_id == user.id,
                        _Anch.anchor_type == 'asi_reaction',
                        _Anch.source == f'agent:{agent.id}',
                        _Anch.created_at >= _since,
                    )
                    .first()
                )
                if _recent:
                    return
            finally:
                _s.close()
        except Exception:
            pass

        # Загружаем других активных агентов для контекста (кому можно делегировать)
        other_agents = ""
        try:
            from models import Session as _Db2, UserAgent as _UA
            _s2 = _Db2()
            try:
                _others = (
                    _s2.query(_UA)
                    .filter(
                        _UA.author_id == user.id,
                        _UA.status == 'active',
                        _UA.id != agent.id,
                    )
                    .limit(5)
                    .all()
                )
                if _others:
                    other_agents = "\n".join(
                        f"- {a.name} ({a.specialization or 'агент'}): {(a.description or '')[:80]}"
                        for a in _others
                    )
            finally:
                _s2.close()
        except Exception:
            pass

        async with self._ai_sem:
            reaction = await self._ask_asi_reaction(
                agent_name=agent.name or 'Агент',
                agent_spec=agent.specialization or 'агент',
                output=output,
                other_agents=other_agents,
            )

        if not reaction:
            return

        # Сохраняем реакцию ASI в чат (от имени ASI, без аватарки агента)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                _save_chat_message_sync,
                user.id,
                'ASI Biont',
                0,
                '',
                reaction,  # без emoji-префикса 🧠
            )
            logger.info("[OFFICE-L1] ASI reacted to %s output for user %d", agent.name, user.id)
        except Exception as e:
            logger.debug("[OFFICE-L1] ASI reaction save error: %s", e)

        # Сохраняем cooldown якорь
        try:
            from models import Session as _Db3, Anchor as _Anch3, AnchorPriority
            _s3 = _Db3()
            try:
                _now = datetime.now(timezone.utc)
                _s3.add(_Anch3(
                    user_id=user.id,
                    anchor_type='asi_reaction',
                    source=f'agent:{agent.id}',
                    topic=f'ASI отреагировал на {agent.name}',
                    priority=AnchorPriority.LOW,
                    data=json.dumps({'reaction': reaction[:200]}, ensure_ascii=False),
                    triggered_at=_now,
                    expires_at=_now + timedelta(hours=2),
                    cooldown_hours=1,
                    batch_group='office',
                ))
                _s3.commit()
            finally:
                _s3.close()
        except Exception as e:
            logger.debug("[OFFICE-L1] cooldown anchor error: %s", e)

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

    @staticmethod
    def _load_recent_chat_sync(user_id: int, n: int = 6) -> list:
        """Загружает последние n сообщений из Interaction для пользователя.
        Возвращает [{speaker, text}, ...] в хронологическом порядке.
        """
        try:
            import json as _j
            from models import Session as _Db, Interaction
            _s = _Db()
            try:
                rows = (_s.query(Interaction)
                        .filter(Interaction.user_id == user_id)
                        .order_by(Interaction.created_at.desc())
                        .limit(n).all())
                rows = list(reversed(rows))
                result = []
                for r in rows:
                    try:
                        data = _j.loads(r.content or '{}')
                        if isinstance(data, dict) and '__agent' in data:
                            result.append({
                                'speaker': data['__agent'].get('name', 'Агент'),
                                'text': data.get('text', ''),
                            })
                        else:
                            # человеческое сообщение или plain-text AI
                            text = data if isinstance(data, str) else r.content or ''
                            result.append({'speaker': 'Пользователь', 'text': str(text)[:300]})
                    except Exception:
                        result.append({'speaker': 'Пользователь', 'text': (r.content or '')[:300]})
                return result
            finally:
                _s.close()
        except Exception:
            return []

    async def _generate_office_dialogue_reply(self, agent_name: str, agent_spec: str,
                                               agent_personality: str,
                                               history: list,
                                               user_db_id: int = 0) -> str:
        """Агент читает последние сообщения в чате и отвечает естественно — как в арене.
        history: [{speaker, text}, ...]
        """
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp

        history_text = '\n'.join(
            f'[{m["speaker"]}]: {m["text"]}' for m in history[-5:]
        )

        # Контекст профиля пользователя для релевантного ответа
        _user_ctx = ''
        if user_db_id and _build_user_context_sync:
            try:
                loop = asyncio.get_running_loop()
                _user_ctx = await loop.run_in_executor(None, _build_user_context_sync, user_db_id)
            except Exception:
                pass
        _ctx_block = f"\n\nКОНТЕКСТ О ПОЛЬЗОВАТЕЛЕ:\n{_user_ctx[:400]}" if _user_ctx else ''

        asi_identity = (
            "Ты — персональный агент ASI Biont. Мыслящий партнёр, не автоответчик. "
            "Прямой, энергичный, действуешь проактивно. Пишешь живо, как опытный друг в мессенджере. "
            "Ты ДЕЛАЕШЬ, а не просто советуешь. Отвечаешь кратко, без списков и заголовков."
        )
        role_overlay = (
            agent_personality or
            f"Ты действуешь как {agent_name} — {agent_spec}."
        )
        system = f"{asi_identity}\n\nРОЛЬ В ЭТОМ КОНТЕКСТЕ:\n{role_overlay}{_ctx_block}"

        user_content = (
            f"В чате только что написали:\n{history_text}\n\n"
            "Прочитай последнее сообщение и ответь на него — естественно, по-человечески, "
            "как коллега в мессенджере. Учитывай кто этот пользователь и чем он занимается — "
            "отвечай релевантно его контексту. Можешь согласиться, уточнить, предложить следующий шаг "
            "или задать один вопрос. 1-2 предложения. Никаких списков, никакого официоза."
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
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user_content},
                        ],
                        "max_tokens": 120,
                        "temperature": 0.8,
                    },
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug("[OFFICE-L1] dialogue_reply AI error: %s", e)
        return ""

    async def _post_agent_followup(self, agent, user):
        """Агент отвечает на реакцию ASI — замыкает диалог (report → ASI → agent reply)."""
        # Пауза 10-30 сек — чтобы сообщения шли с реалистичной задержкой
        await asyncio.sleep(random.randint(10, 30))

        # Читаем последние 5 сообщений — включая отчёт агента и реакцию ASI
        loop = asyncio.get_running_loop()
        history = await loop.run_in_executor(
            None, self._load_recent_chat_sync, user.id, 5
        )
        if not history:
            return

        # Последнее сообщение должно быть от ASI — иначе нет смысла отвечать
        last = history[-1]
        if last.get('speaker') not in ('ASI Biont', 'ASI'):
            return

        async with self._ai_sem:
            reply = await self._generate_office_dialogue_reply(
                agent_name=agent.name or 'Агент',
                agent_spec=agent.specialization or 'агент',
                agent_personality=agent.personality or '',
                history=history,
                user_db_id=user.id,
            )

        if not reply:
            return

        try:
            await loop.run_in_executor(
                None,
                _save_chat_message_sync,
                user.id,
                agent.name or 'Агент',
                agent.id,
                agent.avatar_url or '',
                reply,
                False,  # internal=False: ответ агента виден в чате
            )
            logger.info("[OFFICE-L1] [%s] dialogue reply saved (visible) for user %d", agent.name, user.id)
        except Exception as e:
            logger.debug("[OFFICE-L1] [%s] followup save error: %s", agent.name, e)

    async def _ask_asi_reaction(self, agent_name: str, agent_spec: str,
                                  output: str, other_agents: str) -> str:
        """Короткий AI-вызов: ASI анализирует находку агента и предлагает действие."""
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp

        delegation_block = (
            f"\n\nДругие агенты которым можно поручить действие:\n{other_agents}"
            if other_agents else ""
        )

        prompt = (
            f"Ты — ASI Biont, директор офиса. Агент '{agent_name}' ({agent_spec}) "
            f"только что выполнил мониторинг и нашёл следующее:\n\n"
            f"{output[:600]}\n\n"
            "Коротко (2-4 предложения) скажи пользователю:\n"
            "1. Что важного нашёл агент\n"
            "2. Одно конкретное действие которое стоит предпринять\n"
            f"3. Если нужно — кому из команды это поручить{delegation_block}\n\n"
            "Говори живо и конкретно. Не повторяй всё что сказал агент — только вывод и следующий шаг."
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
                        "max_tokens": 200,
                        "temperature": 0.7,
                    },
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug("[OFFICE-L1] ASI reaction AI error: %s", e)
        return ""

    # ─── Уровень 2: АСИ-координатор ─────────────────────────────────────────

    async def _level2_coordinator_loop(self):
        """Каждые 2-4 часа АСИ назначает задачи агентам по целям пользователя."""
        await asyncio.sleep(300)  # стартуем через 5 мин
        while self.running:
            try:
                await self._run_office_coordination()
            except Exception as e:
                logger.error("[OFFICE-L2] loop error: %s", e)
            wait = random.randint(*OFFICE_INTERVAL_SEC)
            logger.info("[OFFICE-L2] next coordination in %.0f min", wait / 60)
            await asyncio.sleep(wait)

    async def _run_office_coordination(self):
        """Строит план для каждого пользователя у кого есть цели + агенты."""
        try:
            from models import Session as Db, UserAgent, User as UserModel
            s = Db()
            try:
                # Пользователи у которых есть хотя бы один активный агент
                users = (
                    s.query(UserModel)
                    .join(UserAgent, UserAgent.author_id == UserModel.id)
                    .filter(
                        UserModel.telegram_id.isnot(None),
                        UserAgent.status == 'active',
                    )
                    .distinct()
                    .limit(60)
                    .all()
                )
                user_ids = [u.id for u in users]
            finally:
                s.close()
        except Exception as e:
            logger.warning("[OFFICE-L2] DB load error: %s", e)
            return

        if not user_ids:
            return

        logger.info("[OFFICE-L2] Coordinating office for %d users", len(user_ids))
        tasks = [self._coordinate_one_user(uid) for uid in user_ids]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _coordinate_one_user(self, user_id: int):
        """АСИ строит офисный план для одного пользователя."""
        async with self._ai_sem:
            try:
                from models import Session as Db, UserAgent, Goal, Anchor, AnchorPriority, User as UserModel
                s = Db()
                try:
                    user = s.query(UserModel).filter_by(id=user_id).first()
                    if not user:
                        return

                    goals = (
                        s.query(Goal)
                        .filter(Goal.user_id == user_id, Goal.status == 'active')
                        .order_by(Goal.priority.desc())
                        .limit(5)
                        .all()
                    )
                    agents = (
                        s.query(UserAgent)
                        .filter(UserAgent.author_id == user_id, UserAgent.status == 'active')
                        .limit(10)
                        .all()
                    )

                    if not goals or not agents:
                        return

                    # Cooldown: не генерим план чаще раза в 4 часа
                    recent = (
                        s.query(Anchor)
                        .filter(
                            Anchor.user_id == user_id,
                            Anchor.anchor_type == 'agent_office_update',
                            Anchor.triggered_at >= datetime.now(timezone.utc) - timedelta(hours=4),
                        )
                        .first()
                    )
                    if recent:
                        return

                    goals_text = "\n".join(
                        f"- {g.title} [{g.progress_percentage}%]"
                        + (f" дедлайн {g.target_date.strftime('%d.%m')}" if g.target_date else "")
                        for g in goals
                    )
                    _agents_lines = []
                    for _a in agents:
                        _line = f"- {_a.name} ({_a.specialization or 'Агент'}): {(_a.description or '')[:120]}"
                        if _parse_agent_integrations:
                            try:
                                _intg = _parse_agent_integrations(
                                    _a.user_api_keys or '',
                                    _a.python_code or '',
                                    _a.tools_allowed or '',
                                    _a.search_scope or '',
                                )
                                if _intg:
                                    _line += f"\n  Интеграции: {', '.join(_intg[:5])}"
                            except Exception:
                                pass
                        _agents_lines.append(_line)
                    agents_text = "\n".join(_agents_lines)
                    # Сохраняем имя→инфо для поиска агента после закрытия сессии
                    agents_info = {
                        a.name.lower().strip(): {'id': a.id, 'name': a.name, 'avatar_url': a.avatar_url or ''}
                        for a in agents
                    }
                finally:
                    s.close()
            except Exception as e:
                logger.warning("[OFFICE-L2] DB for user %d: %s", user_id, e)
                return

            # AI-вызов (короткий — max 120 токенов)
            plan = await self._ask_asi_for_plan(goals_text, agents_text)
            if not plan:
                return

            # Определяем агента из плана формата «Имя агента: действие»
            _matched_agent = None
            for _akey, _ainfo in agents_info.items():
                _plan_start = plan.lower().split(':')[0].strip()
                if _plan_start == _akey or _plan_start.startswith(_akey):
                    _matched_agent = _ainfo
                    break
            if _matched_agent is None and agents_info:
                _matched_agent = next(iter(agents_info.values()))

            # Пишем план в чат от имени ASI (L2)
            try:
                _save_chat_message_sync(
                    user_id=user_id,
                    agent_name='ASI Biont',
                    agent_id=0,
                    avatar_url='',
                    text=plan,  # без emoji-префикса 📋
                )
            except Exception as e:
                logger.debug("[OFFICE-L2] chat save error for user %d: %s", user_id, e)

            # Создаём якорь agent_office_update
            try:
                from models import Session as Db2, Anchor, AnchorPriority
                s2 = Db2()
                try:
                    now = datetime.now(timezone.utc)
                    source = f'office:{user_id}:{now.strftime("%Y-%m-%d-%H")}'
                    # Не дублируем якорь с тем же source
                    if s2.query(Anchor).filter_by(user_id=user_id, source=source).first():
                        return
                    s2.add(Anchor(
                        user_id=user_id,
                        anchor_type='agent_office_update',
                        source=source,
                        topic='Офис: план на ближайшее время',
                        priority=AnchorPriority.MEDIUM,
                        data=json.dumps({
                            'plan': plan,
                            'agent_count': len(agents),
                            'goal_count': len(goals),
                        }, ensure_ascii=False),
                        triggered_at=now,
                        expires_at=now + timedelta(hours=6),
                        cooldown_hours=4,
                        batch_group='integration',
                    ))
                    s2.commit()
                    logger.info("[OFFICE-L2] user %d: office plan anchored — %s", user_id, plan[:80])
                finally:
                    s2.close()
            except Exception as e:
                logger.warning("[OFFICE-L2] anchor save error for user %d: %s", user_id, e)

    async def _ask_asi_for_plan(self, goals_text: str, agents_text: str) -> str:
        """Короткий AI-вызов: кто из агентов что делает прямо сейчас."""
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp

        prompt = (
            "Ты — координатор продуктивного офиса.\n\n"
            f"ЦЕЛИ ПОЛЬЗОВАТЕЛЯ:\n{goals_text}\n\n"
            f"ДОСТУПНЫЕ АГЕНТЫ:\n{agents_text}\n\n"
            "Выбери ОДНО конкретное действие, которое ОДИН из этих агентов "
            "может выполнить прямо сейчас чтобы продвинуть самую важную цель. "
            "Формат ответа: «[Имя агента]: [конкретное действие в 1 предложение]». "
            "Только факт — без вопросов, без лишних слов."
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
                        "max_tokens": 120,
                        "temperature": 0.6,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug("[OFFICE-L2] ASI plan error: %s", e)
        return ""


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
