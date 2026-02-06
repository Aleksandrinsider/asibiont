#!/usr/bin/env python3
"""
Live Dialogue Test - 20 Iterations
Tests real agent responses without pre-generated dialogue
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import User, Task, Subscription, SubscriptionTier, Base
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

class LiveDialogueTester:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.test_user = None
        self.dialogue_log = []
        self.db_checks = []
        
    def setup_test_user(self):
        """Setup test user with realistic profile"""
        Base.metadata.create_all(bind=self.engine)
        session = self.SessionLocal()
        
        try:
            # Clean up existing test user
            existing = session.query(User).filter_by(telegram_id=999888777).first()
            if existing:
                session.delete(existing)
                session.commit()
            
            # Create test user
            self.test_user = User(
                telegram_id=999888777,
                username="live_test_user",
                first_name="Алексей",
                memory="IT-предприниматель из Перми, разрабатывает стартап"
            )
            session.add(self.test_user)
            
            # Create subscription
            subscription = Subscription(
                user_id=self.test_user.telegram_id,
                tier=SubscriptionTier.LIGHT,
                status='active'
            )
            session.add(subscription)
            
            session.commit()
            print(f"✅ Создан тестовый пользователь: {self.test_user.username}")
            
        except Exception as e:
            session.rollback()
            print(f"❌ Ошибка создания пользователя: {e}")
        finally:
            session.close()

    async def send_message(self, message, iteration):
        """Send message to agent and get response"""
        print(f"\n{'='*70}")
        print(f"ИТЕРАЦИЯ {iteration}/20")
        print(f"{'='*70}")
        print(f"👤 ПОЛЬЗОВАТЕЛЬ: {message}")
        
        session = self.SessionLocal()
        try:
            # Send to agent
            result = await chat_with_ai(
                message=message,
                user_id=self.test_user.telegram_id,
                db_session=session
            )
            
            response = result.get('response', 'No response')
            tool_calls = result.get('tool_calls', [])
            
            # Log dialogue
            entry = {
                'iteration': iteration,
                'time': datetime.now().strftime("%H:%M:%S"),
                'user_message': message,
                'agent_response': response,
                'tool_calls': tool_calls,
                'response_length': len(response)
            }
            self.dialogue_log.append(entry)
            
            print(f"🤖 АГЕНТ: {response}")
            
            if tool_calls:
                print(f"🔧 ИНСТРУМЕНТЫ: {', '.join([tc.get('name', 'unknown') for tc in tool_calls])}")
            
            return response
            
        except Exception as e:
            print(f"❌ ОШИБКА: {e}")
            return f"ERROR: {e}"
        finally:
            session.close()
    
    def check_database_state(self, iteration):
        """Check database state after message"""
        session = self.SessionLocal()
        try:
            tasks = session.query(Task).filter_by(user_id=self.test_user.telegram_id).all()
            user = session.query(User).filter_by(telegram_id=self.test_user.telegram_id).first()
            
            db_state = {
                'iteration': iteration,
                'tasks_count': len(tasks),
                'tasks': [{'title': t.title, 'status': t.status, 'due_date': str(t.due_date) if t.due_date else None} for t in tasks],
                'user_memory': user.memory if user else None,
                'conversation_state': user.conversation_state if user else None
            }
            
            self.db_checks.append(db_state)
            
            print(f"\n📊 СОСТОЯНИЕ БД:")
            print(f"   Задач: {len(tasks)}")
            if tasks:
                for t in tasks:
                    status_icon = "✅" if t.status == "completed" else "⏳"
                    print(f"   {status_icon} {t.title} - {t.status}")
            
        except Exception as e:
            print(f"❌ Ошибка проверки БД: {e}")
        finally:
            session.close()

    async def run_live_dialogue(self):
        """Run 20 iterations of live dialogue"""
        print("🚀 НАЧИНАЕМ ЖИВОЙ ТЕСТ ДИАЛОГА - 20 ИТЕРАЦИЙ")
        print("="*70)
        
        # Setup
        self.setup_test_user()
        
        # Realistic conversation flow
        messages = [
            # 1. Приветствие
            "привет",
            
            # 2. Вопрос о возможностях
            "что ты умеешь?",
            
            # 3. Создание задачи
            "создай задачу: подготовить презентацию для инвесторов к завтрашнему вечеру",
            
            # 4. Просмотр задач
            "какие у меня задачи?",
            
            # 5. Сложный запрос с целью
            "я хочу найти технического со-фаундера для моего стартапа в сфере AI",
            
            # 6. Вопрос о погоде
            "какая сегодня погода?",
            
            # 7. Создание еще одной задачи
            "напомни мне позвонить в банк сегодня в 15:00",
            
            # 8. Обновление профиля
            "обнови мой профиль: навыки - Python, React, Machine Learning",
            
            # 9. Вопрос о прогрессе
            "как мои дела с задачами?",
            
            # 10. Завершение задачи
            "я закончил презентацию",
            
            # 11. Просьба о совете
            "посоветуй что мне делать сейчас",
            
            # 12. Создание повторяющейся задачи
            "создай задачу: утренняя зарядка каждый день в 7:00",
            
            # 13. Вопрос о связях
            "кто в сообществе занимается AI?",
            
            # 14. Перенос задачи
            "перенеси задачу 'позвонить в банк' на завтра",
            
            # 15. Запоминание информации
            "запомни что я лучше всего работаю вечером с 18:00 до 22:00",
            
            # 16. Анализ целей
            "помоги мне составить план по поиску со-фаундера",
            
            # 17. Удаление задачи
            "удали задачу про зарядку",
            
            # 18. Вопрос о рекомендациях
            "какие задачи я могу сделать сегодня чтобы продвинуться к цели?",
            
            # 19. Делегирование
            "можешь найти кого-то кто поможет с дизайном презентации?",
            
            # 20. Благодарность и завершение
            "спасибо, отличная работа!"
        ]
        
        # Run dialogue
        for i, message in enumerate(messages, 1):
            await self.send_message(message, i)
            self.check_database_state(i)
            await asyncio.sleep(0.5)  # Small delay between messages
        
        # Analysis
        self.analyze_results()
    
    def analyze_results(self):
        """Analyze dialogue and provide recommendations"""
        print("\n" + "="*70)
        print("📊 АНАЛИЗ РЕЗУЛЬТАТОВ")
        print("="*70)
        
        # 1. Response times and lengths
        print("\n1️⃣ ДЛИНА ОТВЕТОВ:")
        lengths = [entry['response_length'] for entry in self.dialogue_log]
        avg_length = sum(lengths) / len(lengths)
        print(f"   Средняя длина: {avg_length:.0f} символов")
        print(f"   Минимум: {min(lengths)}, Максимум: {max(lengths)}")
        
        short_responses = sum(1 for l in lengths if l < 50)
        optimal_responses = sum(1 for l in lengths if 50 <= l <= 300)
        long_responses = sum(1 for l in lengths if l > 300)
        
        print(f"   Короткие (<50): {short_responses}")
        print(f"   Оптимальные (50-300): {optimal_responses}")
        print(f"   Длинные (>300): {long_responses}")
        
        # 2. Tool usage
        print("\n2️⃣ ИСПОЛЬЗОВАНИЕ ИНСТРУМЕНТОВ:")
        all_tools = []
        for entry in self.dialogue_log:
            all_tools.extend([tc.get('name', 'unknown') for tc in entry['tool_calls']])
        
        if all_tools:
            tool_counts = {}
            for tool in all_tools:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            
            for tool, count in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True):
                print(f"   {tool}: {count} раз")
        else:
            print("   ⚠️ Инструменты не использовались!")
        
        # 3. Database state
        print("\n3️⃣ СОСТОЯНИЕ БАЗЫ ДАННЫХ:")
        final_state = self.db_checks[-1] if self.db_checks else {}
        print(f"   Всего задач: {final_state.get('tasks_count', 0)}")
        
        tasks = final_state.get('tasks', [])
        if tasks:
            completed = sum(1 for t in tasks if t['status'] == 'completed')
            active = sum(1 for t in tasks if t['status'] == 'active')
            print(f"   Завершенных: {completed}")
            print(f"   Активных: {active}")
        
        # 4. Time handling
        print("\n4️⃣ РАБОТА СО ВРЕМЕНЕМ:")
        time_mentions = []
        for entry in self.dialogue_log:
            response = entry['agent_response'].lower()
            if any(word in response for word in ['утром', 'вечером', 'днем', 'ночью']):
                if not any(t in response for t in ['7:00', '8:00', '9:00', '10:00', '11:00', '12:00', 
                                                       '13:00', '14:00', '15:00', '16:00', '17:00', '18:00',
                                                       '19:00', '20:00', '21:00', '22:00', '23:00']):
                    time_mentions.append(entry['iteration'])
        
        if time_mentions:
            print(f"   ⚠️ Неточное время в итерациях: {time_mentions}")
        else:
            print("   ✅ Время указывается корректно")
        
        # 5. Dialogue quality
        print("\n5️⃣ КАЧЕСТВО ДИАЛОГА:")
        
        # Check for questions
        questions = sum(1 for entry in self.dialogue_log if '?' in entry['agent_response'])
        print(f"   Вопросов задано: {questions}/20")
        
        # Check for varied responses
        responses_text = [entry['agent_response'] for entry in self.dialogue_log]
        unique_starts = len(set(r[:20] for r in responses_text))
        print(f"   Уникальных начал ответов: {unique_starts}/20")
        
        # Check for emoji usage
        emoji_usage = sum(1 for entry in self.dialogue_log 
                         if any(char in entry['agent_response'] for char in ['👋', '🎯', '✅', '📊', '🤖', '💡']))
        print(f"   Использование эмодзи: {emoji_usage}/20")
        
        # 6. Recommendations
        print("\n6️⃣ РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ:")
        recommendations = []
        
        if avg_length > 300:
            recommendations.append("⚠️ Ответы слишком длинные - нужно быть лаконичнее")
        
        if len(all_tools) < 5:
            recommendations.append("⚠️ Мало использует инструменты - нужно активнее вызывать функции")
        
        if questions < 5:
            recommendations.append("⚠️ Мало задает вопросов - нужно больше исследовать потребности")
        
        if time_mentions:
            recommendations.append("⚠️ Есть проблемы с точностью времени - всегда указывать конкретное время")
        
        if unique_starts < 15:
            recommendations.append("⚠️ Ответы недостаточно разнообразные - избегать шаблонов")
        
        if final_state.get('tasks_count', 0) == 0:
            recommendations.append("⚠️ Не создал задачи - проблема с инструментами add_task")
        
        if not recommendations:
            print("   ✅ ВСЁ ОТЛИЧНО! Агент работает корректно")
        else:
            for rec in recommendations:
                print(f"   {rec}")
        
        # 7. Save detailed log
        print("\n7️⃣ СОХРАНЕНИЕ ДЕТАЛЬНОГО ЛОГ�...")
        try:
            with open('test_dialogue_live_results.json', 'w', encoding='utf-8') as f:
                json.dump({
                    'dialogue': self.dialogue_log,
                    'db_checks': self.db_checks,
                    'analysis': {
                        'avg_length': avg_length,
                        'tool_usage': dict(sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)) if all_tools else {},
                        'questions_asked': questions,
                        'time_issues': time_mentions,
                        'recommendations': recommendations
                    }
                }, f, ensure_ascii=False, indent=2)
            print("   ✅ Лог сохранен в test_dialogue_live_results.json")
        except Exception as e:
            print(f"   ❌ Ошибка сохранения: {e}")
        
        print("\n" + "="*70)
        print("🎉 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО!")
        print("="*70)

async def main():
    tester = LiveDialogueTester()
    await tester.run_live_dialogue()

if __name__ == "__main__":
    asyncio.run(main())
