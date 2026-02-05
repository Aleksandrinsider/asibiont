"""
Тест диалога с AI агентом - симуляция реального взаимодействия
Пользователь генерируется AI, агент отвечает, проверяется соответствие требованиям из my.txt
"""

import asyncio
import os
os.environ['LOCAL'] = '1'

import aiohttp
import json
from datetime import datetime, timedelta
from typing import List, Dict

from models import Session, User, UserProfile, Task, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

# Тестовая БД в памяти
engine = create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)


class DialogueTester:
    """Тестер диалогов с AI агентом"""
    
    def __init__(self):
        self.session = TestSession()
        self.user_id = None
        self.conversation_history = []
        self.issues_found = []
        self.agent_stats = {
            'total_responses': 0,
            'tool_calls': 0,
            'avg_response_length': 0,
            'violated_requirements': []
        }
        
    def setup_test_user(self):
        """Создание тестового пользователя с реалистичным профилем"""
        user = User(
            telegram_id=777888999,
            username='test_user_alex'
        )
        self.session.add(user)
        self.session.commit()
        
        profile = UserProfile(
            user_id=user.id,
            city='Москва',
            interests='программирование, стартапы, бег, чтение',
            skills='Python, AI, машинное обучение, менеджмент',
            goals='запустить AI-стартап, улучшить физическую форму, найти со-основателя',
            bio='Александр, разработчик с 5 летним опытом, интересуюсь AI и стартапами'
        )
        self.session.add(profile)
        
        # Добавляем несколько задач
        now = datetime.now()
        tasks = [
            Task(
                user_id=user.id,
                title='Пробежка в парке',
                description='5 км легким темпом',
                reminder_time=now + timedelta(hours=2),
                status='pending'
            ),
            Task(
                user_id=user.id,
                title='Встреча с инвестором',
                description='Презентация проекта',
                reminder_time=now + timedelta(days=1, hours=10),
                status='pending'
            ),
            Task(
                user_id=user.id,
                title='Код-ревью pull request',
                description='Проверить PR от команды',
                reminder_time=now - timedelta(hours=3),  # Просроченная задача
                status='pending'
            )
        ]
        for task in tasks:
            self.session.add(task)
        
        self.session.commit()
        self.user_id = user.telegram_id
        print(f"✅ Создан тестовый пользователь: {user.username} (ID: {user.telegram_id})")
        print(f"   Профиль: {profile.city}, интересы: {len(profile.interests.split(','))} категорий")
        print(f"   Задач: {len(tasks)} (1 просроченная)\n")
        
    async def generate_user_message(self, context: str) -> str:
        """Генерация сообщения пользователя через AI"""
        prompt = f"""Ты - пользователь Александр, который общается с AI-помощником для управления задачами.

ТВОЙ ПРОФИЛЬ:
- Город: Москва
- Интересы: программирование, стартапы, бег, чтение
- Навыки: Python, AI, машинное обучение, менеджмент
- Цели: запустить AI-стартап, улучшить физическую форму, найти со-основателя

ТЕКУЩИЕ ЗАДАЧИ:
- Пробежка в парке (через 2 часа)
- Встреча с инвестором (завтра в 10:00)
- Код-ревью pull request (просрочено на 3 часа)

КОНТЕКСТ ДИАЛОГА:
{context}

Сгенерируй ОДНО короткое сообщение (1-2 предложения) пользователя агенту. Сообщение должно быть:
- Естественным, как реальный человек пишет в мессенджере
- Связанным с контекстом или его задачами/целями
- Разнообразным (не повторяй предыдущие запросы)

Примеры возможных сообщений:
- "Привет, что у меня сегодня?"
- "Помоги найти кого-то для стартапа"
- "Забыл про код-ревью, что делать?"
- "Хочу сегодня пробежаться, есть кто?"
- "Как подготовиться к встрече с инвестором?"
- "Создай задачу на завтра"
- "Кто может помочь с AI проектом?"

Ответь ТОЛЬКО текстом сообщения без пояснений."""

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": DEEPSEEK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.9,  # Высокая для разнообразия
                    "max_tokens": 100
                }
                
                async with session.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        message = data['choices'][0]['message']['content'].strip()
                        # Убираем возможные кавычки
                        message = message.strip('"').strip("'")
                        return message
                    else:
                        return "Привет! Что у меня сегодня запланировано?"
        except Exception as e:
            print(f"⚠️ Ошибка генерации сообщения: {e}")
            return "Привет! Помоги мне с задачами"
    
    async def get_agent_response(self, user_message: str) -> Dict:
        """Получение ответа от агента"""
        from ai_integration.chat import call_ai_with_tools
        from ai_integration.prompts import get_extended_system_prompt
        
        # Получаем системный промпт
        now = datetime.now()
        
        # Получаем данные пользователя
        user = self.session.query(User).filter_by(telegram_id=self.user_id).first()
        profile = self.session.query(UserProfile).filter_by(user_id=user.id).first()
        tasks = self.session.query(Task).filter_by(user_id=user.id, status='pending').all()
        
        # Формируем контекст
        active_tasks = "\n".join([f"- {t.title} ({t.reminder_time.strftime('%d.%m %H:%M')})" for t in tasks])
        
        system_prompt = get_extended_system_prompt(
            user_now=now,
            current_time_str=now.strftime("%H:%M"),
            current_date_str=now.strftime("%d.%m.%Y"),
            user_username=user.username,
            mentions_str='',
            user_memory=None,
            context=active_tasks,
            intent=None,
            subscription_tier='premium',
            message_type='request'
        )
        
        # Формируем контекст диалога
        context = []
        for msg in self.conversation_history[-5:]:  # Последние 5 сообщений
            context.append({
                'user': msg.get('user'),
                'agent': msg.get('agent')
            })
        
        result = await call_ai_with_tools(
            user_message=user_message,
            system_prompt=system_prompt,
            user_id=self.user_id,
            context=context
        )
        
        return result
    
    def check_requirements(self, user_message: str, agent_response: str, tool_calls: List) -> List[str]:
        """Проверка соответствия требованиям из my.txt"""
        violations = []
        
        # Загружаем требования из my.txt
        with open('my.txt', 'r', encoding='utf-8') as f:
            requirements = f.read()
        
        # Проверка 1: Нумерация, списки, жирный шрифт
        if any(marker in agent_response for marker in ['1.', '2.', '**', '###', '- ', '* ']):
            if agent_response.count('- ') > 2 or '1.' in agent_response:
                violations.append("Используется нумерация/списки (требование нарушено)")
        
        # Проверка 2: Общие фразы и клише
        cliches = [
            'Здравствуйте',
            'Спасибо за вопрос',
            'Я помогу',
            'Конечно, я могу помочь',
            'Давайте разберемся',
            'Вот что я могу предложить'
        ]
        for cliche in cliches:
            if cliche.lower() in agent_response.lower():
                violations.append(f"Использование клише: '{cliche}'")
        
        # Проверка 3: Длина ответа (2-4 абзаца, не больше 100 слов по валидатору)
        paragraphs = [p for p in agent_response.split('\n\n') if p.strip()]
        word_count = len(agent_response.split())
        
        if len(paragraphs) > 4 and word_count > 150:
            violations.append(f"Ответ слишком длинный: {len(paragraphs)} абзацев, {word_count} слов")
        elif word_count < 10:
            violations.append(f"Ответ слишком короткий: {word_count} слов")
        
        # Проверка 4: Конкретность (не должно быть слишком общих фраз)
        vague_phrases = [
            'в целом',
            'как правило',
            'обычно',
            'возможно',
            'вероятно',
            'по возможности'
        ]
        vague_count = sum(1 for phrase in vague_phrases if phrase in agent_response.lower())
        if vague_count > 2:
            violations.append(f"Слишком много общих фраз ({vague_count})")
        
        # Проверка 5: Использование данных пользователя
        if 'пользователь' in user_message.lower() or 'я' in user_message.lower():
            # Агент должен учитывать профиль
            has_context = any(word in agent_response.lower() for word in [
                'москва', 'программ', 'стартап', 'бег', 'ai', 'python'
            ])
            if not has_context and not tool_calls:
                violations.append("Не учитывает данные пользователя из профиля")
        
        return violations
    
    async def run_dialogue_turn(self, turn_number: int, context_summary: str):
        """Один раунд диалога"""
        print(f"\n{'='*70}")
        print(f"РАУНД {turn_number}")
        print('='*70)
        
        # Генерируем сообщение пользователя
        user_message = await self.generate_user_message(context_summary)
        print(f"\n👤 ПОЛЬЗОВАТЕЛЬ: {user_message}")
        
        # Получаем ответ агента
        result = await self.get_agent_response(user_message)
        agent_response = result.get('response', '')
        tool_calls = result.get('tool_calls', [])
        
        print(f"\n🤖 АГЕНТ: {agent_response}")
        
        if tool_calls:
            print(f"\n🛠️ Вызваны инструменты: {[tc['function']['name'] for tc in tool_calls]}")
        
        # Проверяем на соответствие требованиям
        violations = self.check_requirements(user_message, agent_response, tool_calls)
        
        if violations:
            print(f"\n⚠️ НАРУШЕНИЯ ТРЕБОВАНИЙ:")
            for v in violations:
                print(f"   • {v}")
            self.issues_found.extend(violations)
            self.agent_stats['violated_requirements'].extend(violations)
        else:
            print(f"\n✅ Ответ соответствует требованиям")
        
        # Обновляем статистику
        self.agent_stats['total_responses'] += 1
        self.agent_stats['tool_calls'] += len(tool_calls)
        current_avg = self.agent_stats['avg_response_length']
        word_count = len(agent_response.split())
        self.agent_stats['avg_response_length'] = (
            (current_avg * (self.agent_stats['total_responses'] - 1) + word_count) 
            / self.agent_stats['total_responses']
        )
        
        # Сохраняем в историю
        self.conversation_history.append({
            'turn': turn_number,
            'user': user_message,
            'agent': agent_response,
            'tool_calls': tool_calls,
            'violations': violations
        })
        
        # Обновляем контекст для следующего раунда
        context_summary = f"Предыдущий диалог:\nПользователь: {user_message}\nАгент: {agent_response[:100]}..."
        
        return context_summary
    
    def print_final_report(self):
        """Финальный отчет по тестированию"""
        print("\n" + "="*70)
        print("📊 ФИНАЛЬНЫЙ ОТЧЕТ")
        print("="*70)
        
        print(f"\n📈 СТАТИСТИКА:")
        print(f"   • Всего раундов диалога: {self.agent_stats['total_responses']}")
        print(f"   • Вызовов инструментов: {self.agent_stats['tool_calls']}")
        print(f"   • Средняя длина ответа: {self.agent_stats['avg_response_length']:.1f} слов")
        print(f"   • Нарушений требований: {len(self.agent_stats['violated_requirements'])}")
        
        if self.agent_stats['violated_requirements']:
            print(f"\n⚠️ НАЙДЕННЫЕ ПРОБЛЕМЫ:")
            # Группируем по типу нарушения
            from collections import Counter
            violation_counts = Counter(self.agent_stats['violated_requirements'])
            for violation, count in violation_counts.most_common():
                print(f"   • {violation} ({count}x)")
        
        print(f"\n💡 РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ:")
        
        recommendations = []
        
        # Анализ нарушений
        if any('клише' in v.lower() for v in self.agent_stats['violated_requirements']):
            recommendations.append(
                "Усилить фильтрацию клише в промпте - добавить больше примеров запрещенных фраз"
            )
        
        if any('нумерация' in v.lower() for v in self.agent_stats['violated_requirements']):
            recommendations.append(
                "Уточнить в промпте: использовать естественный текст без списков и маркеров"
            )
        
        if any('длинный' in v.lower() for v in self.agent_stats['violated_requirements']):
            recommendations.append(
                "Добавить жесткое ограничение: max_tokens=300 для базовых запросов"
            )
        
        if any('данные пользователя' in v.lower() for v in self.agent_stats['violated_requirements']):
            recommendations.append(
                "Улучшить инъекцию контекста профиля в системный промпт - делать явные ссылки"
            )
        
        if self.agent_stats['tool_calls'] == 0:
            recommendations.append(
                "Агент не использовал инструменты - проверить TOOLS definition и промпт"
            )
        
        if self.agent_stats['tool_calls'] / self.agent_stats['total_responses'] > 0.8:
            recommendations.append(
                "Слишком частое использование инструментов - баланс между текстом и actions"
            )
        
        if not recommendations:
            recommendations.append("Агент работает отлично! Продолжайте в том же духе 🎉")
        
        for i, rec in enumerate(recommendations, 1):
            print(f"   {i}. {rec}")
        
        print(f"\n{'='*70}\n")
    
    def cleanup(self):
        """Очистка ресурсов"""
        self.session.close()


async def main():
    """Главная функция тестирования"""
    print("\n🧪 ТЕСТ ДИАЛОГА С AI АГЕНТОМ")
    print("="*70)
    print("Симуляция реального взаимодействия пользователя с агентом")
    print("Пользователь генерируется AI, агент отвечает")
    print("Проверяется соответствие требованиям из my.txt\n")
    
    tester = DialogueTester()
    
    try:
        # Настройка
        tester.setup_test_user()
        
        # Запускаем N раундов диалога
        num_rounds = 8
        context = "Начало диалога. Пользователь впервые обращается к агенту."
        
        for turn in range(1, num_rounds + 1):
            context = await tester.run_dialogue_turn(turn, context)
            await asyncio.sleep(1)  # Пауза между раундами
        
        # Финальный отчет
        tester.print_final_report()
        
        # Сохраняем полный лог диалога
        with open('dialogue_test_log.json', 'w', encoding='utf-8') as f:
            json.dump({
                'conversation': tester.conversation_history,
                'stats': tester.agent_stats
            }, f, ensure_ascii=False, indent=2, default=str)
        
        print(f"💾 Полный лог диалога сохранен в dialogue_test_log.json\n")
        
    except Exception as e:
        print(f"\n❌ Ошибка во время тестирования: {e}")
        import traceback
        traceback.print_exc()
    finally:
        tester.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
