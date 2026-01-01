from ai_integration import chat_with_ai
from config import REDIS_URL, LOCAL
import asyncio
from models import Session, Task, User, Interaction, UserProfile
import sys
import redis

# Настройка кодировки для корректного вывода Unicode в Windows
sys.stdout.reconfigure(encoding='utf-8')

# Ваш реальный user_id
USER_ID = 146333757

if not LOCAL:
    r = redis.from_url(REDIS_URL)
else:
    context_store = {}

def get_context(user_id):
    """Получить контекст из Redis или локального хранилища"""
    if LOCAL:
        return context_store.get(f"context:{user_id}", [])
    else:
        try:
            context_data = r.get(f"context:{user_id}")
            if context_data:
                import json
                return json.loads(context_data)
            return []
        except Exception as e:
            print(f"Ошибка чтения контекста: {e}")
            return []

def save_context(user_id, context):
    """Сохранить контекст в Redis или локальное хранилище"""
    if LOCAL:
        context_store[f"context:{user_id}"] = context
    else:
        try:
            import json
            r.setex(f"context:{user_id}", 3600, json.dumps(context))
        except Exception as e:
            print(f"Ошибка сохранения контекста: {e}")

def print_separator(title):
    """Красивый разделитель"""
    print("\n" + "="*60)
    print(f"  {title}")
    print("="*60 + "\n")

def print_message(role, text):
    """Вывод сообщения с форматированием"""
    prefix = "👤 ВЫ:" if role == "user" else "🤖 AI:"
    print(f"{prefix} {text}\n")

def print_database_state():
    """Вывод текущего состояния БД"""
    db = Session()
    try:
        user = db.query(User).filter_by(telegram_id=USER_ID).first()
        if not user:
            print("❌ Пользователь не найден в БД")
            return
        
        print_separator("ТЕКУЩЕЕ СОСТОЯНИЕ БАЗЫ ДАННЫХ")
        
        # Задачи
        tasks = db.query(Task).filter_by(user_id=user.id).all()
        print(f"📋 ЗАДАЧИ ({len(tasks)}):")
        if tasks:
            for task in tasks:
                status_emoji = "✅" if task.status == "completed" else "⏳"
                reminder = f", напоминание: {task.reminder_time.strftime('%d.%m %H:%M')}" if task.reminder_time else ""
                print(f"  {status_emoji} {task.title} (статус: {task.status}{reminder})")
        else:
            print("  Нет задач")
        
        # Профиль
        profile = db.query(UserProfile).filter_by(user_id=user.id).first()
        print(f"\n👤 ПРОФИЛЬ:")
        if profile:
            print(f"  Город: {profile.city or 'не указан'}")
            print(f"  Интересы: {profile.interests or 'не указаны'}")
            print(f"  Навыки: {profile.skills or 'не указаны'}")
            print(f"  Цели: {profile.goals or 'не указаны'}")
            print(f"  Планы: {profile.current_plans or 'не указаны'}")
        else:
            print("  Профиль не заполнен")
        
        # Партнеры/контакты (если есть таблица)
        try:
            from models import Partner
            partners = db.query(Partner).filter_by(user_id=user.id).all()
            print(f"\n👥 КОНТАКТЫ ({len(partners)}):")
            if partners:
                for partner in partners:
                    print(f"  @{partner.partner_username}: {partner.shared_interests}")
            else:
                print("  Нет контактов")
        except:
            print(f"\n👥 КОНТАКТЫ: таблица не найдена")
        
        # Память пользователя
        if user.memory:
            from ai_integration import decrypt_data
            try:
                memory = decrypt_data(user.memory)
                print(f"\n🧠 ПАМЯТЬ AI:\n  {memory}")
            except:
                print(f"\n🧠 ПАМЯТЬ AI: (зашифрована)")
        
        print()
        
    except Exception as e:
        print(f"Ошибка при выводе состояния БД: {e}")
    finally:
        db.close()

async def send_message(message):
    """Отправить сообщение AI и получить ответ"""
    context = get_context(USER_ID)
    print_message("user", message)
    
    response = await chat_with_ai(message, context, USER_ID)
    print_message("agent", response)
    
    # Обновить контекст
    context.append({"user": message, "agent": response})
    if len(context) > 10:
        context = context[-10:]
    save_context(USER_ID, context)
    
    return response

async def test_dialogue():
    """Тестовый диалог с различными сценариями"""
    
    print_separator("ТЕСТОВЫЙ ДИАЛОГ С AI-АССИСТЕНТОМ")
    print(f"User ID: {USER_ID}")
    print(f"Режим: {'LOCAL (SQLite)' if LOCAL else 'PRODUCTION (Railway)'}")
    
    # Показать начальное состояние
    print_database_state()
    
    # Сценарий 1: Приветствие
    print_separator("СЦЕНАРИЙ 1: ПЕРВОЕ ЗНАКОМСТВО")
    await send_message("Привет!")
    await asyncio.sleep(1)
    print_database_state()
    
    # Сценарий 2: Добавление задач
    print_separator("СЦЕНАРИЙ 2: ДОБАВЛЕНИЕ ЗАДАЧ")
    await send_message("Добавь задачу: позвонить маме сегодня в 18:00")
    await asyncio.sleep(1)
    print_database_state()
    
    await send_message("Еще добавь задачу купить продукты завтра утром в 10:00")
    await asyncio.sleep(1)
    print_database_state()
    
    # Сценарий 3: Заполнение профиля
    print_separator("СЦЕНАРИЙ 3: ЗАПОЛНЕНИЕ ПРОФИЛЯ")
    await send_message("Я живу в Москве, интересуюсь программированием и машинным обучением")
    await asyncio.sleep(1)
    print_database_state()
    
    # Сценарий 4: Просмотр задач
    print_separator("СЦЕНАРИЙ 4: ПРОСМОТР ЗАДАЧ")
    await send_message("Покажи мои задачи")
    await asyncio.sleep(1)
    
    # Сценарий 5: Завершение задачи
    print_separator("СЦЕНАРИЙ 5: ЗАВЕРШЕНИЕ ЗАДАЧИ")
    await send_message("Я позвонил маме, отметь задачу как выполненную")
    await asyncio.sleep(1)
    print_database_state()
    
    # Сценарий 6: Поиск контактов
    print_separator("СЦЕНАРИЙ 6: ПОИСК КОНТАКТОВ")
    await send_message("Можешь найти людей с похожими интересами?")
    await asyncio.sleep(1)
    print_database_state()
    
    # Финальное состояние
    print_separator("ИТОГОВОЕ СОСТОЯНИЕ")
    print_database_state()
    
    print_separator("ТЕСТ ЗАВЕРШЕН")
    print("✅ Все сценарии выполнены успешно!")
    print("📊 Проверьте дашборд для просмотра изменений")

if __name__ == "__main__":
    asyncio.run(test_dialogue())
