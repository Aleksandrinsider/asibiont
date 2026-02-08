"""
Тест живого диалога: DeepSeek играет роль пользователя
Проверяем естественность общения и учет контекста
"""
import asyncio
import sys
import os
import httpx
import json
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from reminder_service import ReminderService
import reminder_service as reminder_service_module

# DeepSeek API для генерации сообщений пользователя
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

async def generate_user_message(conversation_history, turn_number):
    """Генерирует сообщение от имени пользователя через DeepSeek"""
    
    # Персона пользователя
    user_persona = f"""Ты - пользователь Telegram, тестируешь AI-ассистента для задач.

Правила:
- Пиши КРАТКО: 5-15 слов (как в реальном мессенджере)
- Разговорный стиль: "ок", "понял", "давай", "а если"
- Естественно реагируй: благодари, уточняй, соглашайся
- НЕ повторяй одинаковые вопросы
- НЕ пиши длинные детали

Сценарий диалога (ход {turn_number}/12):
1. Приветствие
2. Упомяни проблему с проектом
3-4. Реагируй на предложения агента
5-6. Попроси создать задачу (с конкретным временем)
7-9. Обсуди задачи или профиль
10-12. Завершай диалог"""
    
    # Инструкция по ходам
    context_instruction = ""
    if turn_number == 1:
        context_instruction = "Напиши: 'привет'"
    elif turn_number == 2:
        context_instruction = "Упомяни проблему: 'не получается привлечь пользователей в ASI Biont'"
    elif turn_number in [5, 6]:
        context_instruction = "Попроси создать задачу С ВРЕМЕНЕМ, например: 'создай задачу позвонить клиенту завтра в 14:00'"
    elif turn_number >= 11:
        context_instruction = "Завершай: 'спасибо, попробую' или 'ок, понял'"
    else:
        context_instruction = "Реагируй КРАТКО (5-15 слов) на ответ. НЕ повторяй уже заданные вопросы."
    
    messages = [
        {"role": "system", "content": user_persona},
        {"role": "user", "content": f"Последние 3 сообщения:\n{conversation_history[-1000:]}\n\n{context_instruction}\n\nТвое сообщение (5-15 слов):"}
    ]
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:  # Сократили с 30 до 20 сек
            response = await client.post(
                DEEPSEEK_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "temperature": 0.9,
                    "max_tokens": 50  # Сокращаем для коротких сообщений
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                user_msg = result['choices'][0]['message']['content'].strip()
                # Убираем возможные префиксы
                user_msg = user_msg.replace("Алексей:", "").replace("Пользователь:", "").strip()
                return user_msg
            else:
                return None
    except Exception as e:
        print(f"Ошибка генерации сообщения: {e}")
        return None

async def run_live_dialog_test():
    """Запускает симуляцию живого диалога"""
    
    # Настройка
    user_id = 111222333
    Base.metadata.create_all(engine)
    
    # Инициализация reminder service
    reminder_svc = ReminderService(bot=None)  # No bot for test
    reminder_service_module.REMINDER_SERVICE = reminder_svc
    
    session = Session()
    
    # Очистка предыдущего тестового пользователя
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
        session.delete(user)
        session.commit()
    
    # Создаем пользователя с данными в профиле
    user = User(telegram_id=user_id, username='alexey_test', first_name='Алексей', timezone='Europe/Moscow')
    session.add(user)
    session.commit()
    session.refresh(user)
    
    # Создаем профиль с конкретными данными для теста
    profile = UserProfile(
        user_id=user.id, 
        interests='ИИ, стартапы, бизнес', 
        goals='Привлечь 100 пользователей в ASI Biont, запустить реферальную программу',
        city='Пермь',
        company='ASI Biont',
        position='Основатель'
    )
    session.add(profile)
    session.commit()
    
    # Добавляем несколько задач для теста проактивного контекста
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    
    task1 = Task(
        user_id=user.id,
        title='Написать пост на Habr про AI-агентов',
        description='Поделиться опытом создания гибридного агента',
        reminder_time=now + timedelta(hours=3),
        status='pending'
    )
    task2 = Task(
        user_id=user.id,
        title='Созвон с потенциальным партнером',
        description='Обсудить интеграцию сервисов',
        reminder_time=now + timedelta(days=1, hours=10),
        status='pending'
    )
    session.add_all([task1, task2])
    session.commit()
    
    print("="*80)
    print("[ТЕСТ ЖИВОГО ДИАЛОГА]")
    print("DeepSeek играет роль пользователя Алексея")
    print("="*80)
    print()
    
    conversation_history = ""
    turn = 0
    max_turns = 12  # Оптимизированный тест
    tools_used = 0  # Счетчик использования инструментов
    
    while turn < max_turns:
        turn += 1
        
        # Генерируем сообщение пользователя
        print(f"\n{'-'*80}")
        print(f"[ХОД {turn}/{max_turns}]")
        print(f"{'-'*80}\n")
        
        user_message = await generate_user_message(conversation_history, turn)
        
        if not user_message:
            print("[X] Не удалось сгенерировать сообщение")
            break
        
        print(f"[USER] {user_message}")
        
        # Отправляем агенту
        try:
            response = await chat_with_ai(user_message, user_id=user_id, db_session=session)
            agent_response = response.get('response', 'Нет ответа')
            
            # Подсчет использования инструментов
            if response.get('tools_used'):
                tools_used += len(response['tools_used'])
            
            # Компактный вывод ответа (первые 150 символов)
            clean_response = agent_response.replace('✅', '').replace('🎯', '').replace('📋', '').strip()
            short_response = clean_response[:150] + ('...' if len(clean_response) > 150 else '')
            
            print(f"[BOT] {short_response}")
            
            # Обновляем историю
            conversation_history += f"\nПользователь: {user_message}\nАссистент: {clean_response[:300]}\n"
            
        except KeyboardInterrupt:
            print("\n[!] Прервано пользователем")
            break
        except Exception as e:
            print(f"[X] ОШИБКА: {e}")
            break
        
        # Пауза между ходами
        await asyncio.sleep(0.5)
    
    # Финальный анализ
    print("\n" + "="*80)
    print("[ОТЧЕТ]")
    print("="*80)
    
    print(f"\nСтатистика:")
    print(f"   Ходов диалога: {turn}")
    print(f"   Инструментов использовано: {tools_used}")
    
    # НОВАЯ сессия для чтения финальных данных
    final_session = Session()
    try:
        # Проверяем задачи
        tasks = final_session.query(Task).filter_by(user_id=user.id).all()
        print(f"\n[+] Создано задач: {len(tasks)}")
        for i, task in enumerate(tasks[:5], 1):  # Показываем первые 5
            status = "+" if task.status == 'completed' else "o"
            time_str = task.reminder_time.strftime("%d.%m %H:%M") if task.reminder_time else "без времени"
            print(f"   [{status}] {task.title[:50]} ({time_str})")
        
        if len(tasks) > 5:
            print(f"   ... и ещё {len(tasks) - 5} задач")
        
        # Проверяем профиль
        profile = final_session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"\n[PROFILE] Профиль обновлён:")
            if profile.city:
                print(f"   Город: {profile.city}")
            if profile.interests:
                print(f"   Интересы: {profile.interests[:80]}")
            if profile.skills:
                print(f"   Навыки: {profile.skills[:80]}")
            if profile.company:
                print(f"   Компания: {profile.company}")
    finally:
        final_session.close()
    
    # Очистка
    try:
        session.query(Task).filter_by(user_id=user.id).delete()
        if profile:
            session.delete(profile)
        session.commit()
        session.delete(user)
        session.commit()
    except Exception as e:
        print(f"\n[!] Ошибка очистки: {e}")
        session.rollback()
    finally:
        session.close()
    
    print("\n" + "="*80)
    print("[OK] ТЕСТ ЗАВЕРШЁН")
    print("="*80)

if __name__ == '__main__':
    try:
        asyncio.run(run_live_dialog_test())
    except KeyboardInterrupt:
        print("\n\n[!] Тест прерван пользователем")
    except Exception as e:
        print(f"\n\n[X] Ошибка теста: {e}")
