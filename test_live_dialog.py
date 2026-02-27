"""
Живой диалог: 20 шагов, DeepSeek играет за пользователя.
Полностью свободный — никаких заготовок, сценариев, чеклистов.
DeepSeek-пользователь сам решает что говорить. Оценка — по факту.
"""

import asyncio
import sys
import os

# Windows asyncio fix — SelectorEventLoop стабильнее для HTTP-клиентов
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Загрузка .env файла если есть
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                os.environ.setdefault(key.strip(), value.strip())

os.environ.setdefault("LOCAL", "1")

import aiohttp
import json
import logging
import traceback
import re
from datetime import datetime
from collections import Counter
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from config import DATABASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, TELEGRAM_BOT_USERNAME
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

TEST_TG_ID_RU = 999888666
TEST_TG_ID_EN = 999888667

# Промпт для DeepSeek-пользователя (RU) — только персона, полная свобода
USER_SYSTEM_PROMPT_RU = """Ты — Артём, 28 лет, живёшь в Казани. Маркетолог, знаешь таргет и контент-маркетинг. Работаешь в агентстве, но хочешь уйти на фриланс. Запускаешь свой онлайн-курс по SMM.

Цель — набрать первых 50 учеников на курс за 2 месяца. На следующей неделе вебинар-презентация курса. Интересуешься нейросетями для маркетинга.

Ты пишешь AI-ассистенту в Telegram. Это первый раз — ты его пробуешь.

Правила:
— Пиши как в мессенджере: коротко, 1-3 предложения
— Реагируй на ответы бота: если спрашивает — отвечай, если советует — обсуждай, если предлагает задачу — соглашайся или отказывай
— Веди себя естественно: меняй темы, возвращайся к старым, делись эмоциями
— Ты живой человек — можешь вспомнить что-то, похвалить или покритиковать
— КАТЕГОРИЧЕСКИ НЕ повторяй одно и то же сообщение. Каждый ход — новая тема или новый вопрос. НЕ прощайся раньше времени.
— Обязательно упомяни в разговоре что ты работаешь в маркетинговом агентстве
— Отвечай СТРОГО на последнее сообщение бота в контексте диалога
"""

# Промпт для DeepSeek-пользователя (EN) — английская персона
USER_SYSTEM_PROMPT_EN = """You are Mike, 30 years old, living in Austin, Texas. You're a digital marketer specializing in paid ads and content strategy. You work at a marketing agency but want to go freelance. You're launching your own online course on social media marketing.

Goal — get your first 50 students within 2 months. Next week you have a webinar to present the course. You're interested in AI tools for marketing.

You're writing to an AI assistant on Telegram. This is your first time trying it.

Rules:
— Write like in a messenger: short, 1-3 sentences
— React to bot replies: if it asks — answer, if it advises — discuss, if it suggests a task — accept or decline
— Be natural: change topics, return to old ones, share emotions
— You're a real person — recall things, praise or criticize
— NEVER repeat the same message. Each turn — a new topic or question. Do NOT say goodbye early.
— Make sure to mention that you work at a marketing agency
— Reply STRICTLY to the bot's last message in context of the dialog
— Write ONLY in English
"""


async def generate_user_message(dialog_history, turn, max_turns, lang='ru'):
    """DeepSeek генерирует сообщение за пользователя. Полностью свободный диалог."""
    prompt = USER_SYSTEM_PROMPT_RU if lang == 'ru' else USER_SYSTEM_PROMPT_EN
    messages = [{"role": "system", "content": prompt}]

    # Конвертируем историю: user = assistant для DeepSeek, bot = user
    for entry in dialog_history:
        if entry['role'] == 'user':
            messages.append({"role": "assistant", "content": entry['content']})
        else:
            messages.append({"role": "user", "content": entry['content']})

    # Без подсказок — только напоминание продолжать
    if lang == 'ru':
        if turn == 1:
            hint = "Напиши первое сообщение боту."
        elif turn >= max_turns - 1:
            hint = "Это последний ход."
        else:
            hint = "Продолжай диалог."
    else:
        if turn == 1:
            hint = "Write your first message to the bot."
        elif turn >= max_turns - 1:
            hint = "This is the last turn."
        else:
            hint = "Continue the dialog."

    messages.append({"role": "user", "content": hint})

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": messages, "temperature": 0.9, "max_tokens": 150}
        ) as resp:
            data = await resp.json()
            if 'choices' not in data:
                logger.error(f"[USER_GEN] Unexpected response: {json.dumps(data, ensure_ascii=False)[:200]}")
                return "Продолжай"
            return data['choices'][0]['message']['content'].strip()


class LiveDialogTester:

    def __init__(self, lang='ru'):
        self.lang = lang
        self.tg_id = TEST_TG_ID_RU if lang == 'ru' else TEST_TG_ID_EN
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
        old = self.db.query(User).filter_by(telegram_id=self.tg_id).first()
        if old:
            from models import Interaction, Post, Subscription, ContactAlert, ActivityAlert, TokenTransaction, AnchorDeliveryLog, UserMessage, Anchor, EmailOutreach, EmailCampaign, AgentActivityLog
            # Удаляем сообщения где пользователь — отправитель или получатель
            for msg_filter in [UserMessage.sender_id == old.id, UserMessage.recipient_id == old.id]:
                try:
                    self.db.query(UserMessage).filter(msg_filter).delete()
                except Exception:
                    pass
            for model in [EmailOutreach, EmailCampaign, AgentActivityLog, Anchor, AnchorDeliveryLog, TokenTransaction, ContactAlert, ActivityAlert, Task, Goal, UserProfile, Interaction, Post, Subscription]:
                try:
                    self.db.query(model).filter_by(user_id=old.id).delete()
                except Exception:
                    pass
            self.db.delete(old)
            self.db.commit()

        if self.lang == 'ru':
            u = User(telegram_id=self.tg_id, username='artem_smm',
                     first_name='Артём', timezone='Europe/Moscow',
                     language='ru',
                     subscription_tier='STANDARD', token_balance=50000)
        else:
            u = User(telegram_id=self.tg_id, username='mike_marketing',
                     first_name='Mike', timezone='America/Chicago',
                     language='en',
                     subscription_tier='STANDARD', token_balance=50000)
        self.db.add(u)
        self.db.flush()
        self.db.add(UserProfile(user_id=u.id))
        self.db.commit()
        logger.info(f'[SETUP:{self.lang.upper()}] user id={u.id} tg={self.tg_id} tokens=50000')

    async def run(self):
        MAX_TURNS = 20
        GLOBAL_TIMEOUT = MAX_TURNS * 120  # 120s per turn max
        lang_label = 'РУССКИЙ' if self.lang == 'ru' else 'ENGLISH'
        print(f'\n{"="*70}')
        print(f'  ЖИВОЙ ДИАЛОГ [{lang_label}]: {MAX_TURNS} шагов')
        print(f'{"="*70}\n')

        try:
            await asyncio.wait_for(self._dialog_loop(MAX_TURNS), timeout=GLOBAL_TIMEOUT)
        except asyncio.TimeoutError:
            print(f'\n  [Глобальный таймаут {GLOBAL_TIMEOUT}s — анализирую что есть]')
        except (KeyboardInterrupt, asyncio.CancelledError):
            print(f'\n  [Прервано на ходу {len([m for m in self.dialog if m["role"]=="assistant"])}]')
        finally:
            self._analyze()

    async def _dialog_loop(self, MAX_TURNS):
        for turn in range(1, MAX_TURNS + 1):
            # DeepSeek генерирует сообщение пользователя
            try:
                user_msg = await generate_user_message(self.dialog, turn, MAX_TURNS, self.lang)
            except Exception as e:
                user_msg = "Продолжай" if self.lang == 'ru' else "Continue"
                logger.error(f"[USER_GEN] Error: {e}")

            print(f'  [{turn:2d}] USER: {user_msg}')
            self.dialog.append({'role': 'user', 'content': user_msg, 'turn': turn})

            # Агент отвечает
            t0 = datetime.now()
            try:
                resp = await asyncio.wait_for(
                    chat_with_ai(
                        message=user_msg, user_id=self.tg_id,
                        db_session=self.db, message_type='text'),
                    timeout=120)
            except asyncio.TimeoutError:
                err = f'TIMEOUT turn {turn}: >120s'
                logger.error(err)
                self.errors.append(err)
                self.timings.append(120)
                print(f'  TIMEOUT: {err}\n')
                continue
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
        repetitive = {k: v for k, v in Counter(starts).items() if v >= 4}

        multi_blank = sum(1 for m in agent_msgs if '\n\n\n' in m['content'])

        # Проверка на списки (запрещённый формат)
        list_pattern = re.compile(r'(^\s*[-—•●]\s|^\s*\d+\.\s|\*\*[^*]+\*\*|^##\s)', re.MULTILINE)
        list_violations = sum(1 for m in agent_msgs if list_pattern.search(m['content']))

        print(f'\n  КАЧЕСТВО')
        print(f'     Средняя длина:    {avg_len:.0f} символов')
        print(f'     Длинных (>800):   {too_long}/{n}')
        print(f'     Коротких (<30):   {too_short}/{n}')
        print(f'     Повтор начал:     {"нет" if not repetitive else str(repetitive)}')
        print(f'     Списки/жирный:    {list_violations}/{n} {"OK" if list_violations == 0 else "FAIL"}')
        print(f'     Тройные пробелы:  {multi_blank}')

        # ═══ 3. ИНСТРУМЕНТЫ ═══
        print(f'\n  ИНСТРУМЕНТЫ')
        tc = Counter(self.tool_log)
        unique_tools = len(set(self.tool_log))
        total_calls = len(self.tool_log)
        
        profile_tools = ['update_profile']
        task_tools = ['add_task', 'list_tasks', 'complete_task', 'edit_task', 'delete_task']
        goal_tools = ['create_goal', 'list_goals', 'update_goal_progress', 'delete_goal']
        research_tools = ['research_topic', 'find_relevant_contacts_for_task', 'get_news_trends']
        social_tools = ['create_post', 'get_posts', 'send_message_to_user', 'find_and_message_relevant_users']
        
        profile_calls = sum(tc.get(t, 0) for t in profile_tools)
        task_calls = sum(tc.get(t, 0) for t in task_tools)
        goal_calls = sum(tc.get(t, 0) for t in goal_tools)
        research_calls = sum(tc.get(t, 0) for t in research_tools)
        social_calls = sum(tc.get(t, 0) for t in social_tools)
        
        if self.tool_log:
            for name, count in tc.most_common():
                print(f'     {name}: {count}x')
            print(f'     ---')
            print(f'     Профиль: {profile_calls}, Задачи: {task_calls}, Цели: {goal_calls}')
            print(f'     Исследование: {research_calls}, Социальные: {social_calls}')
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
        bot_name = '@' + (TELEGRAM_BOT_USERNAME.lstrip('@').lower() if TELEGRAM_BOT_USERNAME else 'asibiont_bot')
        # Placeholder-паттерны которые бот НЕ должен использовать как реальные ники
        placeholder_patterns = {'@username', '@yourusername', '@yourname', '@example',
                                '@user', '@channel_name', '@yourhandle', '@handle'}
        allowed_usernames = db_usernames | {'@artem_smm', '@channel', bot_name,
                                            '@asibiont_bot', '@username'} | placeholder_patterns
        fake_set = set()
        for m in agent_msgs:
            for u in re.findall(r'@\w+', m['content']):
                ul = u.lower()
                # Разрешаем: DB-юзеры, бот, плейсхолдеры, реальные каналы(≥5 симв, только латиница/цифры)
                if ul in allowed_usernames:
                    continue
                # Если ник выглядит как реальный канал из поиска — пропускаем
                if re.match(r'^@[a-z0-9][a-z0-9_]{3,}$', ul):
                    continue
                fake_set.add(u)
        fake_list = list(fake_set)
        fake_users = len(fake_list)

        tech_patterns = ['tool_call', 'function_call', '```json', '```python',
                         'user_id=', 'traceback', 'Exception:', 'session.query']
        tech_leak = sum(1 for m in agent_msgs
            if any(p.lower() in m['content'].lower() for p in tech_patterns))

        print(f'\n  БЕЗОПАСНОСТЬ')
        print(f'     Ложные @username: {fake_users} {"OK" if fake_users == 0 else "FAIL"}{" — " + ", ".join(fake_list) if fake_list else ""}')
        print(f'     Тех. утечки:      {tech_leak} {"OK" if tech_leak == 0 else "FAIL"}')

        # ═══ 5. БД ═══
        print(f'\n  БД ПОСЛЕ ДИАЛОГА')
        profile_filled = 0
        task_count = 0
        goal_count = 0
        try:
            user = self.db.query(User).filter_by(telegram_id=self.tg_id).first()
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

        # ═══ 6. ДИАЛОГИЧЕСКОЕ КАЧЕСТВО ═══
        print(f'\n  ДИАЛОГ')
        # Проверка что бот задаёт вопросы
        questions = sum(1 for m in agent_msgs if '?' in m['content'])
        # Проверка что вопрос в конце сообщения (последние 100 символов)
        question_at_end = sum(1 for m in agent_msgs if '?' in m['content'][-100:])
        print(f'     Вопросов: {questions}/{n}')
        print(f'     Вопрос в конце:   {question_at_end}/{questions if questions > 0 else 1}')

        # ═══ ИТОГО ═══
        score = 0

        # Стабильность (20)
        score += 20 if crashes == 0 else max(0, 20 - crashes * 7)

        # Качество (20)
        score += 7 if avg_len < 700 else 4 if avg_len < 900 else 0
        score += 7 if too_long <= 3 else 0
        score += 6 if not repetitive else 0

        # Инструменты (20)
        score += min(10, profile_calls * 2)
        score += min(5, task_calls * 2)
        score += min(5, research_calls * 5)

        # БД (20)
        score += min(8, profile_filled * 2)
        score += min(7, task_count * 2)
        score += min(5, goal_count * 5)

        # Безопасность (20)
        score += 10 if fake_users == 0 else (5 if fake_users <= 2 else 0)
        score += 10 if tech_leak == 0 else 0

        grade = ('A+' if score >= 95 else 'A' if score >= 85 else 'B' if score >= 70
                 else 'C' if score >= 55 else 'D')

        print(f'\n{"="*70}')
        print(f'  РЕЗУЛЬТАТ: {score}/100 — {grade}')
        print(f'{"="*70}')
        print(f'\n  Лог: test_live_dialog.log')

        self.db.close()


async def main():
    # Прогон 1: Русский язык
    print('\n' + '=' * 70)
    print('  PROGON 1/2 --- RUSSKIJ JAZYK')
    print('=' * 70)
    t_ru = LiveDialogTester(lang='ru')
    await t_ru.run()

    # Прогон 2: Английский язык
    print('\n' + '=' * 70)
    print('  PROGON 2/2 --- ENGLISH')
    print('=' * 70)
    t_en = LiveDialogTester(lang='en')
    await t_en.run()

    print('\n' + '=' * 70)
    print('  OBA PROGONA ZAVERSHENY')
    print('=' * 70 + '\n')

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print('\n\n  [Прервано — показываю частичные результаты]\n')
    finally:
        # Graceful cleanup
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
