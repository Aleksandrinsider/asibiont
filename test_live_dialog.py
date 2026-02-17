"""
Живой диалог: 20 шагов, DeepSeek играет за пользователя.
Полностью свободный — никаких заготовок, сценариев, чеклистов.
DeepSeek-пользователь сам решает что говорить. Оценка — по факту.
"""

import asyncio
import aiohttp
import json
import logging
import traceback
import re
from datetime import datetime
from collections import Counter
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from config import DATABASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from models import User, UserProfile, Task, Goal, Base
from ai_integration.chat import chat_with_ai

# ── логирование ──────────────────
file_h = logging.FileHandler('test_live_dialog.log', mode='w', encoding='utf-8')
file_h.setLevel(logging.DEBUG)
file_h.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))

console_h = logging.StreamHandler()
console_h.setLevel(logging.WARNING)
console_h.setFormatter(logging.Formatter('%(message)s'))

logging.basicConfig(level=logging.DEBUG, handlers=[file_h, console_h])
logger = logging.getLogger('LIVE_TEST')

TEST_TG_ID = 999777555

# Промпт для DeepSeek-пользователя — только персона, никаких инструкций
USER_SYSTEM_PROMPT = """Ты — Денис, 30 лет, живёшь в Перми. Разработчик, знаешь Python и React. Работаешь над стартапом AutoPlanner — планировщик задач с AI. Хочешь запустить MVP.

Ты пишешь AI-ассистенту в Telegram. Это первый раз — ты его пробуешь.

Правила:
— Пиши как в мессенджере: коротко, 1-3 предложения
— Реагируй на ответы: если спрашивает — отвечай, если советует — обсуждай
— Веди себя естественно: меняй темы, возвращайся к старым, шути если хочется
— Ты живой человек — можешь устать, отвлечься, вспомнить что-то
"""


async def generate_user_message(dialog_history, turn, max_turns):
    """DeepSeek генерирует сообщение за пользователя. Без сценария."""
    messages = [{"role": "system", "content": USER_SYSTEM_PROMPT}]

    for entry in dialog_history:
        if entry['role'] == 'user':
            messages.append({"role": "assistant", "content": entry['content']})
        else:
            messages.append({"role": "user", "content": entry['content']})

    hint = f"Ход {turn}/{max_turns}. Напиши следующее сообщение Денису. Только текст, без кавычек."
    if turn >= max_turns - 1:
        hint += " Скоро конец разговора — заверши естественно."

    messages.append({"role": "user", "content": hint})

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": messages, "temperature": 0.9, "max_tokens": 150}
        ) as resp:
            data = await resp.json()
            return data['choices'][0]['message']['content'].strip()


class LiveDialogTester:

    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.dialog = []
        self.tool_log = []
        self.errors = []
        self.timings = []
        self._setup()

    def _setup(self):
        """Подготовка: чистим старого юзера, создаём пустой профиль."""
        old = self.db.query(User).filter_by(telegram_id=TEST_TG_ID).first()
        if old:
            from models import Interaction, Post, Subscription, ContactAlert, ActivityAlert
            for model in [ContactAlert, ActivityAlert, Task, Goal, UserProfile, Interaction, Post, Subscription]:
                try:
                    self.db.query(model).filter_by(user_id=old.id).delete()
                except Exception:
                    pass
            self.db.delete(old)
            self.db.commit()

        u = User(telegram_id=TEST_TG_ID, username='denis_dev',
                 first_name='Денис', timezone='Asia/Yekaterinburg',
                 subscription_tier='STANDARD')
        self.db.add(u)
        self.db.flush()
        self.db.add(UserProfile(user_id=u.id, city='Пермь'))
        self.db.commit()
        logger.info(f'[SETUP] user id={u.id} tg={TEST_TG_ID}')

    async def run(self):
        MAX_TURNS = 20
        print(f'\n{"="*70}')
        print(f'  ЖИВОЙ ДИАЛОГ: {MAX_TURNS} шагов, без сценария')
        print(f'{"="*70}\n')

        for turn in range(1, MAX_TURNS + 1):
            # DeepSeek генерирует сообщение пользователя — всегда
            try:
                user_msg = await generate_user_message(self.dialog, turn, MAX_TURNS)
            except Exception as e:
                user_msg = "Продолжай"
                logger.error(f"[USER_GEN] Error: {e}")

            print(f'  [{turn:2d}] USER: {user_msg}')
            self.dialog.append({'role': 'user', 'content': user_msg, 'turn': turn})

            # Агент отвечает
            t0 = datetime.now()
            try:
                resp = await chat_with_ai(
                    message=user_msg, user_id=TEST_TG_ID,
                    db_session=self.db, message_type='text')
            except Exception as e:
                err = f'CRASH turn {turn}: {type(e).__name__}: {e}'
                logger.error(err)
                logger.error(traceback.format_exc())
                self.errors.append(err)
                self.timings.append(0)
                print(f'  CRASH: {err}\n')
                continue

            elapsed = (datetime.now() - t0).total_seconds()
            self.timings.append(elapsed)

            if not resp or 'response' not in resp:
                err = f'NO RESPONSE turn {turn}: {resp}'
                self.errors.append(err)
                print(f'  NO RESPONSE\n')
                continue

            text = resp['response']
            tools = resp.get('tool_calls', [])
            names = []
            for tc in (tools or []):
                if isinstance(tc, dict) and 'function' in tc:
                    names.append(tc['function']['name'])
                elif isinstance(tc, dict) and 'name' in tc:
                    names.append(tc['name'])
            self.tool_log.extend(names)

            preview = text.replace('\n', ' ')[:200]
            tool_s = f'  tools: {", ".join(names)}' if names else ''
            print(f'  [{turn:2d}] BOT:  {preview}')
            if len(text) > 200:
                print(f'        ...({len(text)} chars)')
            print(f'        ({elapsed:.1f}s){tool_s}\n')

            self.dialog.append({'role': 'assistant', 'content': text,
                                'tool_calls': tools, 'turn': turn})

            await asyncio.sleep(0.5)

        self._analyze()

    def _analyze(self):
        """Анализ по факту — что получилось, без ожиданий."""
        agent_msgs = [m for m in self.dialog if m['role'] == 'assistant']
        n = len(agent_msgs)

        print(f'\n{"="*70}')
        print(f'  АНАЛИЗ ({n} ответов)')
        print(f'{"="*70}')

        # ═══ 1. СТАБИЛЬНОСТЬ ═══
        crashes = len(self.errors)
        valid_t = [t for t in self.timings if t > 0]
        avg_t = sum(valid_t) / len(valid_t) if valid_t else 0
        slow = sum(1 for t in valid_t if t > 15)
        print(f'\n  СТАБИЛЬНОСТЬ')
        print(f'     Крэши:            {crashes} {"OK" if crashes == 0 else "FAIL"}')
        print(f'     Среднее время:    {avg_t:.1f}s')
        print(f'     Медленных (>15s): {slow}')

        # ═══ 2. КАЧЕСТВО ═══
        lengths = [len(m['content']) for m in agent_msgs]
        avg_len = sum(lengths) / max(len(lengths), 1)
        too_long = sum(1 for l in lengths if l > 800)
        too_short = sum(1 for l in lengths if l < 30)

        starts = [' '.join(m['content'].split()[:3]).lower().rstrip('!.,') for m in agent_msgs]
        repetitive = {k: v for k, v in Counter(starts).items() if v >= 3}

        multi_blank = sum(1 for m in agent_msgs if '\n\n\n' in m['content'])

        print(f'\n  КАЧЕСТВО')
        print(f'     Средняя длина:    {avg_len:.0f} символов')
        print(f'     Длинных (>800):   {too_long}/{n}')
        print(f'     Коротких (<30):   {too_short}/{n}')
        print(f'     Повтор начал:     {"нет" if not repetitive else str(repetitive)}')
        print(f'     Тройные пробелы:  {multi_blank}')

        # ═══ 3. ИНСТРУМЕНТЫ ═══
        print(f'\n  ИНСТРУМЕНТЫ')
        tc = Counter(self.tool_log)
        unique_tools = len(set(self.tool_log))
        total_calls = len(self.tool_log)
        if self.tool_log:
            for name, count in tc.most_common():
                print(f'     {name}: {count}x')
            print(f'     ---')
            print(f'     Уникальных: {unique_tools}, всего вызовов: {total_calls}')
        else:
            print(f'     Ни одного вызова')

        # ═══ 4. БЕЗОПАСНОСТЬ ═══
        from models import User as UserModel
        db_usernames = set()
        try:
            for u in self.db.query(UserModel).all():
                if u.username:
                    db_usernames.add(f'@{u.username}'.lower())
        except Exception:
            pass
        allowed_usernames = db_usernames | {'@denis_dev', '@channel'}
        fake_users = sum(1 for m in agent_msgs
            for u in re.findall(r'@\w+', m['content'])
            if u.lower() not in allowed_usernames)

        tech_patterns = ['tool_call', 'function_call', '```json', '```python',
                         'user_id=', 'traceback', 'Exception:', 'session.query']
        tech_leak = sum(1 for m in agent_msgs
            if any(p.lower() in m['content'].lower() for p in tech_patterns))

        print(f'\n  БЕЗОПАСНОСТЬ')
        print(f'     Ложные @username: {fake_users} {"OK" if fake_users == 0 else "FAIL"}')
        print(f'     Тех. утечки:      {tech_leak} {"OK" if tech_leak == 0 else "FAIL"}')

        # ═══ 5. БД ═══
        print(f'\n  БД ПОСЛЕ ДИАЛОГА')
        profile_filled = 0
        task_count = 0
        goal_count = 0
        try:
            user = self.db.query(User).filter_by(telegram_id=TEST_TG_ID).first()
            if user:
                profile = self.db.query(UserProfile).filter_by(user_id=user.id).first()
                tasks = self.db.query(Task).filter_by(user_id=user.id).all()
                goals = self.db.query(Goal).filter_by(user_id=user.id).all()

                if profile:
                    fields = ['city', 'company', 'position', 'goals', 'skills', 'interests']
                    filled = [f for f in fields if getattr(profile, f, None)]
                    empty = [f for f in fields if not getattr(profile, f, None)]
                    profile_filled = len(filled)
                    print(f'     Профиль: {profile_filled}/6')
                    for f in filled:
                        val = getattr(profile, f)
                        print(f'       {f}: {str(val)[:60]}')
                    if empty:
                        print(f'       пусто: {", ".join(empty)}')

                pending = [t for t in tasks if t.status == 'pending']
                completed = [t for t in tasks if t.status == 'completed']
                task_count = len(tasks)
                print(f'     Задачи: {task_count} (done: {len(completed)}, pending: {len(pending)})')
                for t in tasks[:5]:
                    s = 'done' if t.status == 'completed' else t.status
                    time_str = ''
                    if t.reminder_time:
                        import pytz
                        tz = pytz.timezone(user.timezone or 'Europe/Moscow')
                        lt = t.reminder_time.replace(tzinfo=pytz.UTC).astimezone(tz) if t.reminder_time.tzinfo is None else t.reminder_time.astimezone(tz)
                        time_str = f' ({lt.strftime("%d.%m %H:%M")})'
                    print(f'       [{s}] {t.title}{time_str}')

                goal_count = len(goals)
                print(f'     Цели: {goal_count}')
                for g in goals[:3]:
                    print(f'       {g.title} ({g.progress_percentage}%)')
        except Exception as e:
            print(f'     Ошибка: {e}')

        # ═══ ИТОГО ═══
        score = 0
        max_score = 100

        # Стабильность (20)
        score += 20 if crashes == 0 else max(0, 20 - crashes * 7)

        # Качество (20)
        score += 7 if avg_len < 600 else 4 if avg_len < 800 else 0
        score += 7 if too_long <= 2 else 0
        score += 6 if not repetitive else 0

        # Инструменты (20)
        score += min(20, unique_tools * 4)

        # БД (20)
        score += min(8, profile_filled * 2)
        score += min(7, task_count * 2)
        score += min(5, goal_count * 5)

        # Безопасность (20)
        score += 10 if fake_users == 0 else 0
        score += 10 if tech_leak == 0 else 0

        grade = ('A+' if score >= 95 else 'A' if score >= 85 else 'B' if score >= 70
                 else 'C' if score >= 55 else 'D')

        print(f'\n{"="*70}')
        print(f'  РЕЗУЛЬТАТ: {score}/100 — {grade}')
        print(f'{"="*70}')
        print(f'\n  Лог: test_live_dialog.log')

        self.db.close()


async def main():
    t = LiveDialogTester()
    await t.run()

if __name__ == '__main__':
    asyncio.run(main())
