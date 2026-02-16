"""
Живой диалог: 20 шагов, DeepSeek играет за пользователя.
Проверяем: общение, выполнение запросов, работу с БД.
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

# Промпт для DeepSeek-пользователя
USER_SYSTEM_PROMPT = """Ты — Денис, 30 лет, Пермь. Разработчик (Python, React), стартап AutoPlanner, хочешь запустить MVP.

Пиши как в мессенджере — коротко, живо, 1-3 предложения. Реагируй на ответы ассистента. Если он спрашивает — отвечай.

Живи диалог естественно. Вот что тебе нужно за 20 сообщений, но не по порядку — как пойдёт:
- Познакомиться, рассказать о себе
- Попросить создать пару задач (названия придумай сам)
- Спросить совет или мнение по работе
- Сказать что устал
- Попросить создать цель
- Сказать что одну задачу сделал
- Попросить удалить задачу
- Поговорить на свободную тему
- Попрощаться

Каждое сообщение — ДРУГАЯ тема или развитие предыдущей. НИКОГДА не повторяй одно и то же. Если тема исчерпана — переключайся.
"""


async def generate_user_message(dialog_history, turn):
    """DeepSeek генерирует сообщение за пользователя."""
    messages = [{"role": "system", "content": USER_SYSTEM_PROMPT}]
    
    # Добавляем историю диалога (инвертируя роли — для deepseek user = assistant бота)
    for entry in dialog_history:
        if entry['role'] == 'user':
            messages.append({"role": "assistant", "content": entry['content']})
        else:
            messages.append({"role": "user", "content": entry['content']})
    
    # Подсказка для текущего хода
    hint = f"Ход {turn}/20. Напиши следующее сообщение. Только текст, без кавычек и пояснений."
    
    # Антиповтор: если последние 2 сообщения похожи — жёстко потребовать смену темы
    user_msgs = [e['content'].lower() for e in dialog_history if e['role'] == 'user']
    if len(user_msgs) >= 2:
        words_last = set(user_msgs[-1].split()[:15])
        words_prev = set(user_msgs[-2].split()[:15])
        overlap = len(words_last & words_prev) / max(len(words_last), 1)
        if overlap > 0.4:
            hint += "\n\nТы повторяешься! Смени тему полностью. Скажи что-то совершенно новое."
    
    # Если уже прощался — не прощайся снова
    farewell_count = sum(1 for e in dialog_history 
        if e['role'] == 'user' and any(w in e['content'].lower() for w in ['пока', 'до связи', 'до свидания']))
    if farewell_count >= 1 and turn < 19:
        hint += "\n\nТы уже прощался — НЕ прощайся снова. Вернись с новой темой."
    
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
        # Минимальный профиль — только город
        self.db.add(UserProfile(user_id=u.id, city='Пермь'))
        self.db.commit()
        logger.info(f'[SETUP] user id={u.id} tg={TEST_TG_ID}')

    async def run(self):
        MAX_TURNS = 20
        print(f'\n{"="*70}')
        print(f'  🧪 ЖИВОЙ ДИАЛОГ: {MAX_TURNS} шагов, DeepSeek = пользователь')
        print(f'{"="*70}\n')

        for turn in range(1, MAX_TURNS + 1):
            # ═══ DeepSeek генерирует сообщение пользователя ═══
            if turn == 1:
                user_msg = "Привет!"
            else:
                try:
                    user_msg = await generate_user_message(self.dialog, turn)
                except Exception as e:
                    user_msg = f"Продолжай, расскажи ещё"
                    logger.error(f"[USER_GEN] Error: {e}")

            print(f'  👤 [{turn:2d}] {user_msg}')
            self.dialog.append({'role': 'user', 'content': user_msg, 'turn': turn})

            # ═══ Агент отвечает ═══
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
                print(f'  ❌ {err}\n')
                continue

            elapsed = (datetime.now() - t0).total_seconds()
            self.timings.append(elapsed)

            if not resp or 'response' not in resp:
                err = f'NO RESPONSE turn {turn}: {resp}'
                self.errors.append(err)
                print(f'  ❌ {err}\n')
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

            # Вывод
            preview = text.replace('\n', ' ')[:200]
            tool_s = f'  🔧 {", ".join(names)}' if names else ''
            print(f'  🤖 [{turn:2d}] {preview}')
            if len(text) > 200:
                print(f'        ...({len(text)} символов)')
            print(f'        ({elapsed:.1f}s){tool_s}\n')

            self.dialog.append({'role': 'assistant', 'content': text,
                                'tool_calls': tools, 'turn': turn})
            
            await asyncio.sleep(0.5)

        self._analyze()

    def _analyze(self):
        """Анализ качества диалога и состояния БД."""
        agent_msgs = [m for m in self.dialog if m['role'] == 'assistant']
        user_msgs = [m for m in self.dialog if m['role'] == 'user']
        n = len(agent_msgs)

        print(f'\n{"="*70}')
        print(f'  📊 АНАЛИЗ ДИАЛОГА ({n} ответов агента)')
        print(f'{"="*70}')

        # ═══ 1. СТАБИЛЬНОСТЬ ═══
        crashes = len(self.errors)
        print(f'\n  🔒 СТАБИЛЬНОСТЬ')
        print(f'     Крэши:              {crashes} {"✅" if crashes == 0 else "❌"}')
        valid_t = [t for t in self.timings if t > 0]
        avg_t = sum(valid_t) / len(valid_t) if valid_t else 0
        slow = sum(1 for t in valid_t if t > 15)
        print(f'     Среднее время:      {avg_t:.1f}s')
        print(f'     Медленных (>15s):   {slow}')

        # ═══ 2. КАЧЕСТВО ОТВЕТОВ ═══
        print(f'\n  💬 КАЧЕСТВО ОТВЕТОВ')
        lengths = [len(m['content']) for m in agent_msgs]
        avg_len = sum(lengths) / max(len(lengths), 1)
        too_long = sum(1 for l in lengths if l > 800)
        too_short = sum(1 for l in lengths if l < 30)
        print(f'     Средняя длина:      {avg_len:.0f} символов')
        print(f'     Слишком длинных:    {too_long}/{n}')
        print(f'     Слишком коротких:   {too_short}/{n}')

        # Повторяющиеся начала
        starts = [' '.join(m['content'].split()[:3]).lower().rstrip('!.,') for m in agent_msgs]
        repetitive = {k: v for k, v in Counter(starts).items() if v >= 3}
        print(f'     Повторные начала:   {"✅ нет" if not repetitive else "❌ " + str(repetitive)}')

        # Множественные пустые строки
        multi_blank = sum(1 for m in agent_msgs if '\n\n\n' in m['content'])
        print(f'     Тройные пробелы:    {multi_blank} {"✅" if multi_blank == 0 else "❌"}')

        # Пустые секции
        empty_sections = sum(1 for m in agent_msgs 
            if re.search(r':\s*\n\s*\n\s*[А-ЯA-Z]', m['content']))
        print(f'     Пустые секции:      {empty_sections} {"✅" if empty_sections == 0 else "❌"}')

        # ═══ 3. ИНСТРУМЕНТЫ ═══
        print(f'\n  🔧 ИНСТРУМЕНТЫ')
        if self.tool_log:
            tc = Counter(self.tool_log)
            for name, count in tc.most_common():
                flag = ' ⚠️ слишком много!' if name == 'research_topic' and count > 3 else ''
                print(f'     {name}: {count}x{flag}')
            research_count = tc.get('research_topic', 0)
            update_profile_count = tc.get('update_profile', 0)
            print(f'\n     📊 research_topic: {research_count} (норма: 0-3)')
            print(f'     📊 update_profile: {update_profile_count} (ожидалось: >= 2)')
        else:
            print(f'     Ни одного ❌')

        # ═══ 4. АНТИГАЛЛЮЦИНАЦИЯ ═══
        print(f'\n  🛡️ АНТИГАЛЛЮЦИНАЦИЯ')
        # Ложные @username (исключаем реальных контактов из БД)
        from models import User as UserModel
        db_usernames = set()
        try:
            all_users = self.db.query(UserModel).all()
            for u in all_users:
                if u.username:
                    db_usernames.add(f'@{u.username}'.lower())
        except Exception:
            pass
        allowed_usernames = db_usernames | {'@denis_dev', '@channel'}
        fake_users = sum(1 for m in agent_msgs
            for u in re.findall(r'@\w+', m['content'])
            if u.lower() not in allowed_usernames)
        print(f'     Ложные @username:   {fake_users} {"✅" if fake_users == 0 else "❌"}')

        # Технические утечки
        tech_patterns = ['tool_call', 'function_call', '```json', '```python',
                         'user_id=', 'traceback', 'Exception:', 'session.query']
        tech_leak = sum(1 for m in agent_msgs
            if any(p.lower() in m['content'].lower() for p in tech_patterns))
        print(f'     Тех. утечки:        {tech_leak} {"✅" if tech_leak == 0 else "❌"}')

        # ═══ 5. СОСТОЯНИЕ БД ═══
        print(f'\n  🗄️ СОСТОЯНИЕ БД ПОСЛЕ ДИАЛОГА')
        try:
            user = self.db.query(User).filter_by(telegram_id=TEST_TG_ID).first()
            if user:
                profile = self.db.query(UserProfile).filter_by(user_id=user.id).first()
                tasks = self.db.query(Task).filter_by(user_id=user.id).all()
                goals = self.db.query(Goal).filter_by(user_id=user.id).all()

                print(f'     Пользователь:       ✅ {user.username}')
                
                # Профиль
                if profile:
                    fields = ['city', 'company', 'position', 'goals', 'skills', 'interests']
                    filled = [f for f in fields if getattr(profile, f, None)]
                    empty = [f for f in fields if not getattr(profile, f, None)]
                    print(f'     Профиль заполнен:   {len(filled)}/6 полей')
                    if filled:
                        for f in filled:
                            val = getattr(profile, f)
                            print(f'       ✅ {f}: {str(val)[:60]}')
                    if empty:
                        print(f'       ❌ пусто: {", ".join(empty)}')
                else:
                    print(f'     Профиль:            ❌ не создан')
                
                # Задачи
                pending = [t for t in tasks if t.status == 'pending']
                completed = [t for t in tasks if t.status == 'completed']
                print(f'     Задачи:             {len(tasks)} (✅ завершено: {len(completed)}, ⏳ ожидает: {len(pending)})')
                for t in tasks[:5]:
                    status = '✅' if t.status == 'completed' else '⏳'
                    time_str = ''
                    if t.reminder_time:
                        import pytz
                        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
                        local_t = t.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz) if t.reminder_time.tzinfo is None else t.reminder_time.astimezone(user_tz)
                        time_str = f' ({local_t.strftime("%d.%m %H:%M")} {user_tz.zone})'
                    notes = f' | notes: {t.completion_notes[:40]}' if t.completion_notes else ''
                    print(f'       {status} {t.title}{time_str}{notes}')

                # Цели
                print(f'     Цели:               {len(goals)}')
                for g in goals[:3]:
                    print(f'       🎯 {g.title} ({g.progress_percentage}%)')
            else:
                print(f'     ❌ Пользователь не найден!')
        except Exception as e:
            print(f'     ❌ Ошибка чтения БД: {e}')

        # ═══ 6. ПРОВЕРКА ОЖИДАЕМЫХ ДЕЙСТВИЙ ═══
        print(f'\n  📋 ОЖИДАЕМЫЕ ДЕЙСТВИЯ (по сценарию)')
        tc = Counter(self.tool_log)
        
        expected_actions = [
            ('update_profile', 'Обновление профиля (компания, навыки, интересы)', 2),
            ('add_task', 'Создание задач', 2),
            ('complete_task', 'Завершение задачи с результатом', 1),
            ('delete_task', 'Удаление задачи', 1),
            ('create_goal', 'Создание цели', 1),
        ]
        
        action_score = 0
        for tool_name, desc, min_count in expected_actions:
            actual = tc.get(tool_name, 0)
            ok = actual >= min_count
            action_score += 1 if ok else 0
            status = '✅' if ok else '❌'
            print(f'     {status} {desc}: {actual}x (мин. {min_count})')
        
        print(f'     Выполнено: {action_score}/{len(expected_actions)}')

        # ═══ ИТОГО ═══
        score = 0
        max_score = 0

        # Стабильность (15 баллов)
        score += 15 if crashes == 0 else max(0, 15 - crashes * 5)
        max_score += 15

        # Инструменты использованы (15 баллов)
        unique_tools = len(set(self.tool_log))
        score += min(15, unique_tools * 3)
        max_score += 15

        # Качество ответов (15 баллов)
        score += 5 if avg_len < 600 else 3 if avg_len < 800 else 0
        score += 5 if too_long <= 2 else 0
        score += 5 if not repetitive else 0
        max_score += 15

        # БД заполненность (15 баллов)
        try:
            user = self.db.query(User).filter_by(telegram_id=TEST_TG_ID).first()
            profile = self.db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
            tasks = self.db.query(Task).filter_by(user_id=user.id).all() if user else []
            goals = self.db.query(Goal).filter_by(user_id=user.id).all() if user else []
            
            if profile:
                filled = sum(1 for f in ['city','company','position','goals','skills','interests'] 
                           if getattr(profile, f, None))
                score += min(6, filled)  # до 6 баллов за поля
            score += min(5, len(tasks) * 2)  # до 5 за задачи
            score += min(4, len(goals) * 4)  # до 4 за цели
        except Exception:
            pass
        max_score += 15

        # Ожидаемые действия (25 баллов) — самый важный блок
        score += action_score * 5
        max_score += len(expected_actions) * 5

        # Безопасность (15 баллов)
        score += 8 if fake_users == 0 else 0
        score += 7 if tech_leak == 0 else 0
        max_score += 15

        pct = 100 * score / max_score if max_score else 0
        grade = ('A+' if pct >= 95 else 'A' if pct >= 85 else 'B' if pct >= 70
                 else 'C' if pct >= 55 else 'D')

        print(f'\n{"="*70}')
        print(f'  🏆 РЕЗУЛЬТАТ: {score}/{max_score} ({pct:.0f}%) — {grade}')
        print(f'{"="*70}')
        print(f'\n  📄 Подробный лог: test_live_dialog.log')

        self.db.close()


async def main():
    t = LiveDialogTester()
    await t.run()

if __name__ == '__main__':
    asyncio.run(main())
