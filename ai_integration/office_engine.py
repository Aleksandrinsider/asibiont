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
import random
import subprocess
import sys
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ── Интервалы ────────────────────────────────────────────────────────────────
MONITOR_INTERVAL_SEC = (25 * 60, 45 * 60)   # 25-45 мин между прогонами скриптов
OFFICE_INTERVAL_SEC  = (2 * 3600, 4 * 3600) # 2-4 ч между координаторскими сессиями
SCRIPT_TIMEOUT_SEC   = 18                    # таймаут на один скрипт агента


# ── Изолированный запуск скрипта ─────────────────────────────────────────────

def _exec_agent_script_sync(code: str) -> tuple:
    """Запускает python_code агента в отдельном subprocess (sync).
    Возвращает (stdout: str, stderr: str).
    Безопасно: изолировано от серверного процесса.
    """
    try:
        result = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SEC,
        )
        return result.stdout[:2000].strip(), result.stderr[:400].strip()
    except subprocess.TimeoutExpired:
        return '', 'timeout'
    except Exception as e:
        return '', str(e)[:200]


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
        await asyncio.sleep(120)  # дать серверу прогреться
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
        """Выполняет скрипт одного агента, создаёт якорь если есть данные."""
        py_code = (agent.python_code or '').strip()
        if not py_code:
            return

        async with self._script_sem:
            try:
                from ai_integration.autonomous_agent import _wrap_agent_code, spawn_integration_anchors
                wrapped = _wrap_agent_code(py_code)

                loop = asyncio.get_event_loop()
                stdout, stderr = await loop.run_in_executor(
                    None, _exec_agent_script_sync, wrapped
                )
            except Exception as e:
                logger.debug("[OFFICE-L1] [%s] exec error: %s", agent.name, e)
                return

            if stdout:
                service_label = (agent.specialization or agent.name or 'Agent').strip()
                try:
                    loop = asyncio.get_event_loop()
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
            elif stderr and 'timeout' not in stderr:
                logger.debug("[OFFICE-L1] [%s] stderr: %s", agent.name, stderr[:150])

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
                    agents_text = "\n".join(
                        f"- {a.name} ({a.specialization or 'Агент'}): {(a.description or '')[:120]}"
                        for a in agents
                    )
                finally:
                    s.close()
            except Exception as e:
                logger.warning("[OFFICE-L2] DB for user %d: %s", user_id, e)
                return

            # AI-вызов (короткий — max 120 токенов)
            plan = await self._ask_asi_for_plan(goals_text, agents_text)
            if not plan:
                return

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
