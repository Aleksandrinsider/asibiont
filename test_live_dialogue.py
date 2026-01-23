"""
Комплексный тест: Живой диалог с пользователем
Симулирует реалистичную серию сообщений для проверки поведения агента
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
import pytz
import json

# Настройка пути
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["LOCAL"] = "1"

from models import Session, User, Task, UserProfile, SubscriptionTier
from ai_integration.chat import chat_with_ai
import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


class LiveDialogueSimulator:
    """Симулятор живого диалога с AI агентом"""
    
    def __init__(self):
        self.test_user_id = 999999
        self.conversation_history = []
        self.tasks_created = []
        
    def setup_test_user(self):
        """Создает тестового пользователя"""
        session = Session()
        try:
            # Удаляем старого пользователя
            user = session.query(User).filter_by(telegram_id=self.test_user_id).first()
            if user:
                # Удаляем все связанные записи
                from models import Interaction, Post, Comment
                session.query(Interaction).filter_by(user_id=user.id).delete()
                session.query(Comment).filter_by(user_id=user.id).delete()
                session.query(Post).filter_by(user_id=user.id).delete()
                session.query(Task).filter_by(user_id=user.id).delete()
                session.query(UserProfile).filter_by(user_id=user.id).delete()
                session.delete(user)
                session.commit()
            
            # Создаем нового
            user = User(
                telegram_id=self.test_user_id,
                username="test_dialogue_user",
                subscription_tier=SubscriptionTier.SILVER,
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            
            profile = UserProfile(
                user_id=user.id,
                city="Москва",
                interests="программирование, спорт",
                skills="Python, JavaScript"
            )
            session.add(profile)
            session.commit()
            
            # Возвращаем данные вместо объекта, чтобы избежать проблем с сессией
            return {
                'id': user.id,
                'telegram_id': user.telegram_id,
                'username': user.username,
                'timezone': user.timezone,
                'subscription_tier': user.subscription_tier.value
            }
        finally:
            session.close()
    
    async def send_message(self, message_text, delay_seconds=0):
        """Отправляет сообщение агенту и получает ответ"""
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        
        # Получаем текущее время в timezone пользователя
        tz = pytz.timezone("Europe/Moscow")
        current_time = datetime.now(tz)
        
        print(f"\n{'='*80}")
        print(f"👤 ПОЛЬЗОВАТЕЛЬ [{current_time.strftime('%H:%M')}]: {message_text}")
        print(f"{'='*80}")
        
        try:
            response = await chat_with_ai(
                message_text,
                user_id=self.test_user_id
            )
            
            print(f"🤖 АГЕНТ: {response}")
            
            self.conversation_history.append({
                'time': current_time.strftime('%Y-%m-%d %H:%M'),
                'user': message_text,
                'agent': response
            })
            
            return response
        except Exception as e:
            print(f"❌ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def check_tasks_count(self):
        """Проверяет количество задач"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.test_user_id).first()
            if not user:
                return 0
            tasks = session.query(Task).filter_by(user_id=user.id, status='pending').all()
            return len(tasks)
        finally:
            session.close()
    
    def get_latest_task(self):
        """Получает последнюю созданную задачу"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.test_user_id).first()
            if not user:
                return None
            task = session.query(Task).filter_by(user_id=user.id).order_by(Task.id.desc()).first()
            if task:
                return {
                    'id': task.id,
                    'title': task.title,
                    'reminder_time': task.reminder_time.strftime('%Y-%m-%d %H:%M') if task.reminder_time else None,
                    'status': task.status
                }
            return None
        finally:
            session.close()


async def run_live_dialogue_test():
    """Запускает комплексный тест живого диалога"""
    
    print("\n" + "="*80)
    print("КОМПЛЕКСНЫЙ ТЕСТ: ЖИВОЙ ДИАЛОГ С АГЕНТОМ")
    print("="*80)
    
    simulator = LiveDialogueSimulator()
    user = simulator.setup_test_user()
    
    print(f"\n✅ Тестовый пользователь создан: {user['username']} (ID: {simulator.test_user_id})")
    print(f"   Timezone: {user['timezone']}")
    print(f"   Subscription: {user['subscription_tier']}")
    
    # Получаем текущее время в timezone пользователя
    tz = pytz.timezone(user['timezone'])
    now = datetime.now(tz)
    
    print(f"\n🕐 Текущее время: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n{'='*80}")
    print("НАЧАЛО ДИАЛОГА")
    print(f"{'='*80}\n")
    
    # === СЦЕНАРИЙ 1: Приветствие и создание первой задачи ===
    await simulator.send_message("Привет!")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 2: Задача с относительным временем (КРИТИЧЕСКИЙ ТЕСТ) ===
    await simulator.send_message(f"напомни через 5 минут заказать ужин домой")
    
    # Проверяем правильность времени
    task = simulator.get_latest_task()
    if task:
        expected_time = (now + timedelta(minutes=5)).replace(second=0, microsecond=0)
        actual_time = datetime.strptime(task['reminder_time'], '%Y-%m-%d %H:%M')
        actual_time = tz.localize(actual_time)
        
        time_diff = abs((actual_time - expected_time).total_seconds())
        if time_diff <= 120:  # Допуск 2 минуты
            print(f"\n✅ ПРОВЕРКА ВРЕМЕНИ: ПРАВИЛЬНО")
            print(f"   Ожидалось: {expected_time.strftime('%H:%M')}")
            print(f"   Получено: {actual_time.strftime('%H:%M')}")
        else:
            print(f"\n❌ ПРОВЕРКА ВРЕМЕНИ: ОШИБКА!")
            print(f"   Ожидалось: {expected_time.strftime('%H:%M')}")
            print(f"   Получено: {actual_time.strftime('%H:%M')}")
            print(f"   Разница: {time_diff/60:.1f} минут")
    
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 3: Создание второй задачи ===
    await simulator.send_message("завтра в 10 утра встреча с клиентом")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 4: Просмотр задач ===
    await simulator.send_message("какие у меня задачи?")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 5: Попытка создать дубликат ===
    await simulator.send_message("напомни заказать ужин")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 6: Задача без времени (должен спросить) ===
    await simulator.send_message("нужно позвонить маме")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 7: Указываем время для предыдущей задачи ===
    await simulator.send_message("в 19:00")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 8: Изменение существующей задачи ===
    await simulator.send_message("перенеси встречу с клиентом на 14:00")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 9: Две задачи в одном сообщении ===
    await simulator.send_message("в 16:00 позвонить врачу и в 20:00 сходить в спортзал")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 10: Обычный разговор (не задача) ===
    await simulator.send_message("как дела?")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 11: Завершение задачи ===
    await simulator.send_message("я заказал ужин")
    await asyncio.sleep(1)
    
    # === СЦЕНАРИЙ 12: Вопрос о погоде/совет ===
    await simulator.send_message("посоветуй что приготовить на ужин")
    await asyncio.sleep(1)
    
    # === ИТОГИ ===
    print(f"\n{'='*80}")
    print("ИТОГИ ТЕСТА")
    print(f"{'='*80}\n")
    
    tasks_count = simulator.check_tasks_count()
    print(f"Активных задач: {tasks_count}")
    print(f"Сообщений в диалоге: {len(simulator.conversation_history)}")
    
    # Проверяем контекст
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=simulator.test_user_id).first()
        tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.reminder_time).all()
        
        print(f"\nСозданные задачи:")
        for i, task in enumerate(tasks, 1):
            status_emoji = "✅" if task.status == "completed" else "⏳"
            print(f"  {i}. {status_emoji} {task.title}")
            if task.reminder_time:
                print(f"     Время: {task.reminder_time.strftime('%Y-%m-%d %H:%M')}")
            print(f"     Статус: {task.status}")
    finally:
        session.close()
    
    print(f"\n{'='*80}")
    print("История диалога сохранена")
    print(f"{'='*80}\n")
    
    # Сохраняем историю в файл
    with open('test_dialogue_history.json', 'w', encoding='utf-8') as f:
        json.dump(simulator.conversation_history, f, ensure_ascii=False, indent=2)
    
    print("✅ Тест завершен. История сохранена в test_dialogue_history.json")


if __name__ == "__main__":
    asyncio.run(run_live_dialogue_test())
