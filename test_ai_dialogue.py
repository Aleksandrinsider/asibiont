"""
Интерактивный тест AI диалога с генерацией ответов пользователя через AI
"""
import asyncio
import os
from models import SessionLocal, User, Task
from ai_integration import chat_with_ai
from datetime import datetime
import pytz
import requests
import json

def generate_user_response(ai_message, conversation_context, scenario_step):
    """Генерирует ответ пользователя на основе сообщения AI"""
    
    # Сценарии для тестирования
    scenarios = {
        1: "Поприветствуй и попроси поручить @testuser подготовить отчет до завтра 15:00",
        2: "Если AI задаёт вопросы о деталях - ответь коротко 'просто сделай', если AI делегировал - попроси добавить задачу позвонить клиенту через 2 часа",
        3: "Попроси показать все задачи",
        4: "Скажи что переехал в Москву и работаешь в Google как Senior Engineer",
        5: "Попрощайся"
    }
    
    scenario = scenarios.get(scenario_step, "Ответь естественно на сообщение AI")
    
    prompt = f"""Ты пользователь, который общается с AI-ассистентом по управлению задачами.

Последнее сообщение от AI:
{ai_message}

Контекст разговора:
{conversation_context}

Твоя задача на этом шаге: {scenario}

Сгенерируй короткое (1-2 предложения) естественное сообщение пользователя. Отвечай только текстом сообщения, без пояснений."""

    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 100
            },
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        else:
            return scenarios.get(scenario_step, "Привет!")
            
    except Exception as e:
        print(f"[Предупреждение] Ошибка генерации: {e}")
        return scenarios.get(scenario_step, "Привет!")


async def test_ai_dialogue():
    """Тестирует AI диалог с генерацией ответов пользователя"""
    print("\n" + "="*80)
    print("ИНТЕРАКТИВНЫЙ ТЕСТ AI ДИАЛОГА С ГЕНЕРАЦИЕЙ ОТВЕТОВ")
    print("="*80 + "\n")
    
    session = SessionLocal()
    
    try:
        # Находим или создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=146333757).first()
        
        if not user:
            print("[INFO] Создаю тестового пользователя...")
            user = User(
                telegram_id=146333757,
                username="test_user",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            print("[OK] Пользователь создан\n")
        else:
            print(f"[OK] Пользователь: {user.username} (ID: {user.telegram_id})")
            print(f"    Timezone: {user.timezone or 'UTC'}")
            
            # Проверяем реальные данные
            tasks_count = session.query(Task).filter_by(user_id=user.id).count()
            print(f"    Задач в БД: {tasks_count}\n")
        
        print("="*80)
        print("НАЧАЛО ДИАЛОГА")
        print("="*80 + "\n")
        
        conversation_context = []
        max_steps = 6
        
        # Первое сообщение
        user_message = "Привет!"
        
        for step in range(1, max_steps + 1):
            print(f"[Шаг {step}]")
            print(f"👤 Пользователь: {user_message}")
            print()
            
            try:
                # Вызываем AI агента
                ai_response = await chat_with_ai(
                    message=user_message,
                    user_id=user.telegram_id
                )
                
                print(f"🤖 AI: {ai_response}")
                print()
                
                # Сохраняем в контекст
                conversation_context.append(f"User: {user_message}")
                conversation_context.append(f"AI: {ai_response}")
                
                # Проверяем реальные изменения в БД
                session.expire_all()  # Обновляем данные из БД
                tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.id).all()
                
                print(f"📊 Данные после шага:")
                print(f"   Всего задач в БД: {len(tasks)}")
                if tasks:
                    now = datetime.now(pytz.UTC)
                    for task in tasks[-3:]:  # Последние 3 задачи
                        if task.reminder_time:
                            # Убедимся что reminder_time timezone-aware
                            reminder_tz = task.reminder_time if task.reminder_time.tzinfo else pytz.UTC.localize(task.reminder_time)
                            status = "просрочена" if reminder_tz < now else "активна"
                            reminder = task.reminder_time.strftime("%H:%M")
                        else:
                            status = "без времени"
                            reminder = "не установлено"
                        print(f"   - [{status}] {task.title} (напоминание: {reminder})")
                print()
                print("-" * 80)
                print()
                
                # Генерируем следующий ответ пользователя
                if step < max_steps:
                    context_str = "\n".join(conversation_context[-6:])  # Последние 3 обмена
                    user_message = generate_user_response(ai_response, context_str, step + 1)
                    await asyncio.sleep(1)
                    
            except Exception as e:
                print(f"❌ ОШИБКА: {e}")
                import traceback
                traceback.print_exc()
                print()
                print("-" * 80)
                print()
                break
        
        print("="*80)
        print("ДИАЛОГ ЗАВЕРШЁН")
        print("="*80)
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_dialogue())
