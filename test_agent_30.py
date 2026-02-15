"""
Тест: 10 ходов — агент как живой партнёр.
Фиксированные сообщения покрывают: знакомство, бизнес, эмоции, быт, стратегию.
Цель: неотличимо от живого человека.
"""

import asyncio
import logging
import traceback
import re
from datetime import datetime, timedelta
from collections import Counter
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from config import DATABASE_URL
from models import User, UserProfile, Task, Base
from ai_integration.chat import chat_with_ai

# ── логирование ──────────────────
file_h = logging.FileHandler('test_agent_30.log', mode='w', encoding='utf-8')
file_h.setLevel(logging.DEBUG)
file_h.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))

console_h = logging.StreamHandler()
console_h.setLevel(logging.WARNING)
console_h.setFormatter(logging.Formatter('%(message)s'))

logging.basicConfig(level=logging.DEBUG, handlers=[file_h, console_h])
logger = logging.getLogger('TEST')

TEST_TG_ID = 888888888

# ── 10 фиксированных сообщений: разные сферы жизни ──────────────
USER_MESSAGES = {
    1:  "Привет",
    2:  "Я разрабатываю AI-агента для управления задачами. Хочу найти первых пользователей.",
    3:  "Слушай, а какие сейчас тренды в AI-агентах? Может я отстал от рынка",
    4:  "Блин, устал сегодня жутко. Целый день в коде сидел, глаза болят",
    5:  "Кстати, надо бы не забыть завтра позвонить инвестору в 11. И ещё тренировку хочу вечером в 19",
    6:  "А что думаешь — стоит ли мне идти на конференцию AI в Москве через 2 недели? Билет 15к",
    7:  "Ок убедил. А как мне питч подготовить за неделю? Я никогда не выступал перед инвесторами",
    8:  "Слушай, а вот философский вопрос — не заменит ли AI вообще всех менеджеров через 5 лет?",
    9:  "Ладно, пойду спать. Завтра важный день",
    10: "Доброе утро! Как думаешь, с чего начать день?",
}


class AgentDialogTester:

    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.history = []
        self.tool_log = []
        self.errors = []
        self.timings = []
        self.max_turns = 10
        self._setup()

    # ── подготовка: почти пустой профиль, как новый юзер ─────────
    def _setup(self):
        old = self.db.query(User).filter_by(telegram_id=TEST_TG_ID).first()
        if old:
            from models import Interaction, Post, Subscription, Goal, ContactAlert, ActivityAlert
            for model in [ContactAlert, ActivityAlert, Task, UserProfile, Interaction, Post, Subscription, Goal]:
                try:
                    self.db.query(model).filter_by(user_id=old.id).delete()
                except Exception:
                    pass
            self.db.delete(old)
            self.db.commit()

        u = User(telegram_id=TEST_TG_ID, username='aleksey_ceo',
                 first_name='Алексей', timezone='Asia/Yekaterinburg',
                 subscription_tier='PREMIUM')
        self.db.add(u)
        self.db.flush()
        # Минимальный профиль — только город
        self.db.add(UserProfile(user_id=u.id, city='Пермь'))
        self.db.commit()
        logger.info(f'[SETUP] user id={u.id}  tg={TEST_TG_ID}')

    # ── основной цикл ────────────────────────────────────────────
    async def run(self):
        print(f'\n🧪 ТЕСТ: {self.max_turns} ходов — агент как живой партнёр\n')

        for turn in range(1, self.max_turns + 1):
            msg = USER_MESSAGES[turn]
            print(f'  👤 [{turn:2d}] {msg}')
            self.history.append({'role': 'user', 'content': msg, 'turn': turn})

            t0 = datetime.now()
            try:
                resp = await chat_with_ai(
                    message=msg, user_id=TEST_TG_ID,
                    db_session=self.db, message_type='text')
            except Exception as e:
                err = f'CRASH turn {turn}: {type(e).__name__}: {e}'
                logger.error(err); logger.error(traceback.format_exc())
                self.errors.append(err); self.timings.append(0)
                print(f'  ❌ {err}')
                continue

            elapsed = (datetime.now() - t0).total_seconds()
            self.timings.append(elapsed)

            if not resp or 'response' not in resp:
                err = f'NO RESPONSE turn {turn}: {resp}'
                self.errors.append(err); print(f'  ❌ {err}')
                continue

            text = resp['response']
            tools = resp.get('tool_calls', [])
            names = []
            for tc in (tools or []):
                if isinstance(tc, dict) and 'function' in tc:
                    names.append(tc['function']['name'])
                elif isinstance(tc, dict) and 'name' in tc:
                    names.append(tc['name'])
                else:
                    names.append(str(tc))
            self.tool_log.extend(names)

            preview = text.replace('\n', ' ')
            tool_s = f'  🔧 {", ".join(names)}' if names else ''
            print(f'  🤖 [{turn:2d}] {preview}')
            print(f'        ({elapsed:.1f}s){tool_s}')

            self.history.append({'role': 'assistant', 'content': text,
                                 'tool_calls': tools, 'turn': turn})
            await asyncio.sleep(0.3)

        self._analyze()

    # ── вспомогательные ──────────────────────────────────────────
    def _tool_names(self, idx):
        """Имена инструментов из ответа агента по индексу"""
        agents = [m for m in self.history if m['role'] == 'assistant']
        if idx >= len(agents):
            return []
        names = []
        for tc in (agents[idx].get('tool_calls') or []):
            if isinstance(tc, dict):
                names.append(tc.get('function', {}).get('name', tc.get('name', '')))
        return names

    def _agent_text(self, idx):
        agents = [m for m in self.history if m['role'] == 'assistant']
        return agents[idx]['content'].lower() if idx < len(agents) else ''

    # ── анализ живости ────────────────────────────────────────────
    def _analyze(self):
        agent_msgs = [m for m in self.history if m['role'] == 'assistant']
        n = len(agent_msgs)

        print(f'\n{"="*70}')
        print(f'  📊 АНАЛИЗ ЖИВОСТИ ({n} ответов)')
        print(f'{"="*70}')

        score = 0
        max_score = 0

        # ─── 1. «Привет» + пустой профиль → спросил кто ты? ─────
        t = self._agent_text(0)
        asked = any(w in t for w in ['чем занимаешь', 'кто ты', 'расскаж',
            'о себе', 'что важно', 'чем живёшь', 'знаю о тебе', 'не знаю',
            'познакомимся', 'рад', 'привет'])
        no_dump = '1.' not in t and 'погод' not in t and len(t) < 400
        print(f'\n  1. ПРИВЕТ + пустой профиль')
        print(f'     Спросил/поприветствовал: {"✅" if asked else "❌"}')
        print(f'     Без инфо-дампа:         {"✅" if no_dump else "❌"}')
        s1 = (10 if asked else 0) + (10 if no_dump else 0)
        score += s1; max_score += 20

        # ─── 2. Рассказал о себе → обновил профиль? ──────────────
        t2n = self._tool_names(1)
        updated = 'update_profile' in t2n
        no_task = 'add_task' not in t2n
        print(f'\n  2. РАССКАЗАЛ О СЕБЕ')
        print(f'     Обновил профиль:        {"✅" if updated else "❌"}')
        print(f'     Не создал задачу:       {"✅" if no_task else "❌"}')
        s2 = (8 if updated else 0) + (4 if no_task else 0)
        score += s2; max_score += 12

        # ─── 3. Тренды AI → research? ────────────────────────────
        t3n = self._tool_names(2)
        researched = 'research_topic' in t3n
        print(f'\n  3. ТРЕНДЫ AI')
        print(f'     Сделал research:        {"✅" if researched else "❌"}')
        s3 = 8 if researched else 0
        score += s3; max_score += 8

        # ─── 4. «Устал» → эмпатия, а не план задач ──────────────
        t4 = self._agent_text(3)
        t4n = self._tool_names(3)
        empathy_words = ['тяжёл','тяжел','понима','отдохн','забо','глаза',
            'здоров','бывает','выгора','перерыв','устал','сочувств','береги']
        has_empathy = any(w in t4 for w in empathy_words)
        no_task_tired = 'add_task' not in t4n
        print(f'\n  4. «УСТАЛ» — ЭМПАТИЯ')
        print(f'     Эмпатия в ответе:       {"✅" if has_empathy else "❌"}')
        print(f'     Не создал задачу:       {"✅" if no_task_tired else "❌"}')
        s4 = (8 if has_empathy else 0) + (4 if no_task_tired else 0)
        score += s4; max_score += 12

        # ─── 5. «Позвонить/тренировка» → ПРЕДЛОЖИЛ, НЕ создал ───
        t5 = self._agent_text(4)
        t5n = self._tool_names(4)
        suggested = any(w in t5 for w in ['постав','напомн','поставим',
            'запланиру','предлагаю','добавить','добавим','хочешь',
            'запиш','фиксир','контрольн','записать','поставить'])
        auto = 'add_task' in t5n
        has_time = bool(re.search(r'\d{1,2}[:.]\d{2}|\d{1,2}\s*(утра|дня|вечера|часов|час)', t5))
        print(f'\n  5. «ПОЗВОНИТЬ + ТРЕНИРОВКА» — ЗАДАЧИ')
        print(f'     Предложил точку:        {"✅" if suggested else "❌"}')
        print(f'     НЕ создал сам:          {"✅" if not auto else "❌ (создал без спроса!)"}')
        print(f'     Указал время:           {"✅" if has_time else "❌"}')
        s5 = (5 if suggested else 0) + (10 if not auto else 0) + (5 if has_time else 0)
        score += s5; max_score += 20

        # ─── 6. Конференция → своё мнение ────────────────────────
        t6 = self._agent_text(5)
        opinion = any(w in t6 for w in ['я бы','стоит','не стоит','однозначно',
            'рекоменду','советую','на твоём месте','считаю','думаю что',
            'определённо','имеет смысл','окупится','вложение'])
        print(f'\n  6. КОНФЕРЕНЦИЯ — МНЕНИЕ')
        print(f'     Своё мнение:            {"✅" if opinion else "❌"}')
        s6 = 5 if opinion else 0
        score += s6; max_score += 5

        # ─── 7. Питч → конкретный план ──────────────────────────
        t7 = self._agent_text(6)
        concrete = any(w in t7 for w in ['слайд','структур','проблем','решени',
            'рынок','метрик','команд','демо','вступлен','аудитори','план',
            'шаг','день'])
        print(f'\n  7. ПИТЧ — КОНКРЕТИКА')
        print(f'     Конкретный план:        {"✅" if concrete else "❌"}')
        s7 = 5 if concrete else 0
        score += s7; max_score += 5

        # ─── 8. Философия → глубина ──────────────────────────────
        t8 = self._agent_text(7)
        shallow = ['время покажет','сложный вопрос','трудно сказать','зависит от']
        is_deep = len(t8) > 150 and not any(w in t8 for w in shallow)
        print(f'\n  8. ФИЛОСОФИЯ — ГЛУБИНА')
        print(f'     Глубокий ответ:         {"✅" if is_deep else "❌"}')
        s8 = 5 if is_deep else 0
        score += s8; max_score += 5

        # ─── 9. «Пойду спать» → тёплое и короткое ───────────────
        t9 = self._agent_text(8)
        warm = any(w in t9 for w in ['спокойной','отдыхай','выспись','ночи',
            'отдохни','сон','завтра','удачи'])
        short = len(t9) < 400
        print(f'\n  9. «ПОЙДУ СПАТЬ»')
        print(f'     Тёплое прощание:        {"✅" if warm else "❌"}')
        print(f'     Короткое (<400):        {"✅" if short else "❌"}')
        s9 = (3 if warm else 0) + (2 if short else 0)
        score += s9; max_score += 5

        # ─── 10. «Доброе утро» → план дня с учётом задач ────────
        t10 = self._agent_text(9)
        t10n = self._tool_names(9)
        morning = any(w in t10 for w in ['утро','план','день','задач','назначен',
            'начн','сначала','перв','расписани','сегодня'])
        used_tasks = any(n in ('get_tasks','list_tasks','check_time_conflicts') for n in t10n)
        print(f'\n  10. «ДОБРОЕ УТРО»')
        print(f'     План дня:               {"✅" if morning else "❌"}')
        print(f'     Посмотрел задачи:       {"✅" if used_tasks else "❌"}')
        s10 = (4 if morning else 0) + (4 if used_tasks else 0)
        score += s10; max_score += 8

        # ═══ ОБЩИЕ МЕТРИКИ ═══
        print(f'\n  {"─"*60}')

        no_crashes = len(self.errors) == 0
        print(f'  Нет крашей:              {"✅" if no_crashes else "❌"}')

        lengths = [len(m['content']) for m in agent_msgs]
        avg_len = sum(lengths) / max(len(lengths), 1)
        too_long = sum(1 for l in lengths if l > 600)
        print(f'  Средняя длина:           {avg_len:.0f} символов')
        print(f'  Слишком длинных (>600):  {too_long}/{n}')

        starts = [' '.join(m['content'].split()[:2]).lower().rstrip('!.,') for m in agent_msgs]
        repetitive = {k: v for k, v in Counter(starts).items() if v >= 3}
        print(f'  Разнообразные начала:    {"✅" if not repetitive else "❌ " + str(repetitive)}')

        hallucinations = sum(1 for m in agent_msgs
            for u in re.findall(r'@\w+', m['content'])
            if u.lower() not in ('@aleksey_ceo','@channel'))
        print(f'  Галлюцинации (@user):    {hallucinations}')

        tech_patterns = ['tool_call','function_call','```json','```python',
            'user_id=','traceback','Exception:','DSML']
        tech_leak = sum(1 for m in agent_msgs
            if any(p.lower() in m['content'].lower() for p in tech_patterns))
        print(f'  Утечка тех. деталей:     {tech_leak}')

        valid_t = [t for t in self.timings if t > 0]
        avg_t = sum(valid_t) / len(valid_t) if valid_t else 0
        print(f'  Среднее время ответа:    {avg_t:.1f}s')

        if self.tool_log:
            print(f'\n  🔧 Tools ({len(self.tool_log)}): {", ".join(f"{n}:{c}x" for n,c in Counter(self.tool_log).most_common())}')

        # ═══ ИТОГО ═══
        pct = 100 * score / max_score if max_score else 0
        grade = ('A+' if pct >= 95 else 'A' if pct >= 85 else 'B' if pct >= 70
                 else 'C' if pct >= 55 else 'D')

        print(f'\n{"="*70}')
        print(f'  🏆 ЖИВОСТЬ: {score}/{max_score} ({pct:.0f}%) — {grade}')
        print(f'{"="*70}')
        print(f'\n  📄 Подробный лог: test_agent_30.log')
        self.db.close()


async def main():
    t = AgentDialogTester()
    await t.run()

if __name__ == '__main__':
    asyncio.run(main())
