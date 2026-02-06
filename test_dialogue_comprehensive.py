"""
Комплексный тест реального диалога с агентом
Проверяет работу с БД, логику, конкретность задач
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from models import Session, User, Task, UserProfile
from ai_integration.chat import chat_with_ai
from sqlalchemy import text

# Set test environment
os.environ['LOCAL'] = '1'

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

class DialogueTester:
    def __init__(self):
        self.test_user_id = 999888777  # Тестовый пользователь
        self.messages_history = []
        self.issues_found = []
        self.session = Session()
        
    def setup_test_user(self):
        """Создание тестового пользователя"""
        # Очистка старых данных
        self.session.execute(text("DELETE FROM tasks WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": self.test_user_id})
        self.session.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": self.test_user_id})
        self.session.execute(text("DELETE FROM users WHERE telegram_id = :tid"), {"tid": self.test_user_id})
        self.session.commit()
        
        # Создание пользователя
        user = User(
            telegram_id=self.test_user_id,
            username="test_user",
            timezone="Europe/Moscow"
        )
        self.session.add(user)
        self.session.commit()
        
        # Создание профиля
        profile = UserProfile(
            user_id=user.id,
            city="Пермь",
            company="ASI Biont",
            position="Founder",
            goals="Продвигать агента на рынке",
            interests="AI, бизнес, LitRPG",
            skills="Python, ML, Product Management"
        )
        self.session.add(profile)
        self.session.commit()
        
        print(f"✅ Создан тестовый пользователь ID={user.id}, telegram_id={self.test_user_id}")
        return user
    
    async def send_message(self, message, context=""):
        """Отправка сообщения и получение ответа"""
        print(f"\n{'='*60}")
        print(f"👤 USER: {message}")
        if context:
            print(f"   [Контекст: {context}]")
        
        try:
            # Получаем ответ от агента
            response = await chat_with_ai(
                message=message,
                context=context,
                user_id=self.test_user_id,
                db_session=self.session
            )
            
            print(f"🤖 AGENT: {response}")
            
            # Сохраняем в историю
            self.messages_history.append({
                "user": message,
                "agent": response,
                "context": context,
                "timestamp": datetime.now()
            })
            
            return response
            
        except Exception as e:
            print(f"❌ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
            self.issues_found.append(f"Ошибка при обработке '{message}': {e}")
            return None
    
    def check_tasks_in_db(self, expected_count=None, check_title=None):
        """Проверка задач в БД"""
        user = self.session.query(User).filter_by(telegram_id=self.test_user_id).first()
        tasks = self.session.query(Task).filter_by(user_id=user.id).all()
        
        print(f"\n📊 ПРОВЕРКА БД:")
        print(f"   Всего задач: {len(tasks)}")
        
        for task in tasks:
            status_emoji = "✅" if task.status == "completed" else "⏳"
            time_str = task.reminder_time.strftime('%d.%m %H:%M') if task.reminder_time else "без времени"
            print(f"   {status_emoji} [{task.status}] {task.title} ({time_str})")
            if task.description:
                print(f"      Описание: {task.description[:80]}...")
        
        # Проверки
        if expected_count is not None and len(tasks) != expected_count:
            issue = f"Ожидалось {expected_count} задач, найдено {len(tasks)}"
            print(f"   ⚠️ {issue}")
            self.issues_found.append(issue)
        
        if check_title:
            found = any(check_title.lower() in task.title.lower() for task in tasks)
            if not found:
                issue = f"Не найдена задача с '{check_title}' в названии"
                print(f"   ⚠️ {issue}")
                self.issues_found.append(issue)
        
        return tasks
    
    def analyze_task_quality(self, tasks):
        """Анализ качества созданных задач"""
        print(f"\n🔍 АНАЛИЗ КАЧЕСТВА ЗАДАЧ:")
        
        generic_titles = ["заняться вопросом", "сделать это", "та задача", "вопрос", "задача"]
        
        for task in tasks:
            issues = []
            
            # Проверка конкретности названия
            if any(generic.lower() in task.title.lower() for generic in generic_titles):
                issues.append("Неконкретное название")
            
            if len(task.title) < 10:
                issues.append("Слишком короткое название")
            
            # Проверка времени
            if not task.reminder_time:
                issues.append("Нет времени напоминания")
            
            # Проверка описания (для сложных задач)
            if not task.description or len(task.description) < 10:
                issues.append("Отсутствует или короткое описание")
            
            if issues:
                print(f"   ⚠️ {task.title}")
                for issue in issues:
                    print(f"      - {issue}")
                self.issues_found.append(f"Задача '{task.title}': {', '.join(issues)}")
            else:
                print(f"   ✅ {task.title}")
    
    async def run_dialogue_test(self):
        """Запуск комплексного диалога"""
        print("\n" + "="*60)
        print("КОМПЛЕКСНЫЙ ТЕСТ ДИАЛОГА (30 ИТЕРАЦИЙ)")
        print("="*60)
        
        user = self.setup_test_user()
        
        # Диалог из 30 сообщений
        dialogue = [
            # 1-3: Приветствие и общение
            ("привет", ""),
            ("как дела?", ""),
            ("что мне сейчас делать?", ""),
            
            # 4-6: Создание задач с разной конкретностью
            ("создай задачу позвонить клиенту завтра в 15:00", ""),
            ("напомни про встречу через 2 часа", ""),
            ("добавь задачу купить молоко", ""),
            
            # 7-9: Работа с контекстом
            ("давай обсудим реферальную программу для партнеров", ""),
            ("нужно найти первых 2-3 кандидата", ""),
            ("напомни через 5 минут заняться этим вопросом", "Обсуждали реферальную программу"),
            
            # 10-12: Список и завершение
            ("покажи мои задачи", ""),
            ("готово купить молоко", ""),
            ("что у меня на сегодня?", ""),
            
            # 13-15: Перенос и удаление
            ("перенеси позвонить клиенту на послезавтра в 10:00", ""),
            ("удали задачу встреча", ""),
            ("мои задачи", ""),
            
            # 16-18: Сложные сценарии
            ("найди мне единомышленников в IT", ""),
            ("создай повторяющуюся задачу зарядка каждый день в 7:00", ""),
            ("анализируй мои задачи и предложи что делать", ""),
            
            # 19-21: Дополнительные контекстные задачи
            ("начнем работать над новой фичей в продукте", ""),
            ("добавь подзадачи для этой фичи", "Работаем над новой фичей"),
            ("напомни завтра утром про это", "Обсуждали новую фичу"),
            
            # 22-24: Тестирование памяти и профиля
            ("запомни что я предпочитаю работать с 9 до 18", ""),
            ("какие у меня цели?", ""),
            ("обновI мой профиль - добавь навык Docker", ""),
            
            # 25-27: Комплексные команды
            ("создай 3 задачи на сегодня: созвон в 14:00, код-ревью в 16:00, планирование в 18:00", ""),
            ("какие задачи у меня просрочены?", ""),
            ("перенеси все просроченные на завтра", ""),
            
            # 28-30: Финал
            ("запомни что я люблю работать утром", ""),
            ("покажи финальный список задач", ""),
            ("спасибо за помощь", ""),
        ]
        
        for i, (message, context) in enumerate(dialogue, 1):
            print(f"\n--- Итерация {i}/30 ---")
            await self.send_message(message, context)
            
            # Проверка БД после ключевых операций
            if i in [6, 9, 12, 15, 18, 21, 24, 27, 30]:
                self.check_tasks_in_db()
            
            # Небольшая пауза для имитации реального общения
            await asyncio.sleep(0.5)
        
        # Финальная проверка
        print(f"\n{'='*60}")
        print("ФИНАЛЬНАЯ ПРОВЕРКА")
        print("="*60)
        
        tasks = self.check_tasks_in_db()
        self.analyze_task_quality(tasks)
        
        # Отчет
        print(f"\n{'='*60}")
        print("ИТОГОВЫЙ ОТЧЕТ")
        print("="*60)
        print(f"Обработано сообщений: {len(self.messages_history)}")
        print(f"Создано задач: {len(tasks)}")
        print(f"Найдено проблем: {len(self.issues_found)}")
        
        if self.issues_found:
            print(f"\n⚠️ ОБНАРУЖЕННЫЕ ПРОБЛЕМЫ:")
            for i, issue in enumerate(self.issues_found, 1):
                print(f"{i}. {issue}")
        else:
            print(f"\n✅ ВСЕ ОТЛИЧНО! Проблем не обнаружено")
        
        # Очистка
        self.cleanup()
    
    def cleanup(self):
        """Очистка тестовых данных"""
        try:
            self.session.execute(text("DELETE FROM tasks WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": self.test_user_id})
            self.session.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": self.test_user_id})
            self.session.execute(text("DELETE FROM users WHERE telegram_id = :tid"), {"tid": self.test_user_id})
            self.session.commit()
            print(f"\n✅ Тестовые данные очищены")
        except Exception as e:
            print(f"\n⚠️ Ошибка при очистке: {e}")
        finally:
            self.session.close()

async def main():
    tester = DialogueTester()
    await tester.run_dialogue_test()

if __name__ == "__main__":
    asyncio.run(main())
