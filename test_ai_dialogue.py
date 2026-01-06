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
    
    # Упрощённые сценарии - по одному действию за раз
    scenarios = {
        2: "Попроси поручить задачу @testuser на подготовку отчета до завтра 15:00. Будь кратким.",
        3: "Попроси добавить задачу позвонить клиенту через 2 часа. Одно короткое предложение.",
        4: "Попроси показать все задачи. Одно предложение.",
        5: "Скажи что переехал в Москву и работаешь в Google как Senior Engineer. Одно предложение.",
        6: "Попрощайся коротко.",
        7: "Скажи спасибо и закончи разговор."
    }
    
    scenario = scenarios.get(scenario_step, "Ответь коротко на вопрос AI")
    
    prompt = f"""Ты пользователь, который общается с AI-ассистентом по управлению задачами.

Последнее сообщение от AI:
{ai_message}

Твоя задача: {scenario}

Ответь ОДНИМ коротким предложением (максимум 10-15 слов). Пиши только текст сообщения."""

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
                "temperature": 0.5,
                "max_tokens": 50
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()["choices"][0]["message"]["content"].strip()
            # Убираем кавычки если есть
            result = result.strip('"\'')
            return result
        else:
            # Fallback на простые команды
            fallbacks = {
                1: "Поручи @testuser подготовить отчет до завтра 15:00",
                2: "Добавь задачу позвонить клиенту через 2 часа",
                3: "Покажи мои задачи",
                4: "Я переехал в Москву и работаю в Google как Senior Engineer",
                5: "Спасибо, пока!",
                6: "До свидания!"
            }
            return fallbacks.get(scenario_step, "Хорошо")
            
    except Exception as e:
        print(f"[Предупреждение] Ошибка генерации: {e}")
        return scenarios.get(scenario_step, "Привет!")


async def test_ai_dialogue():
    """Тестирует AI диалог с генерацией ответов пользователя"""
    print("\n" + "="*80)
    print("INTERACTIVE AI DIALOGUE TEST WITH RESPONSE GENERATION")
    print("="*80 + "\n")
    
    session = SessionLocal()
    
    try:
        # Находим или создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=146333757).first()
        
        if not user:
            print("[INFO] Creating test user...")
            user = User(
                telegram_id=146333757,
                username="test_user",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            print("[OK] User created\n")
        else:
            print(f"[OK] User: {user.username} (ID: {user.telegram_id})")
            print(f"    Timezone: {user.timezone or 'UTC'}")
            
            # Проверяем реальные данные
            tasks_count = session.query(Task).filter_by(user_id=user.id).count()
            print(f"    Tasks in DB: {tasks_count}\n")
        
        print("="*80)
        print("DIALOGUE START")
        print("="*80 + "\n")
        
        conversation_context = []
        max_steps = 7
        
        # Жесткий список тестовых команд
        test_commands = [
            "Привет!",
            "Поручи @testuser подготовить отчет до завтра 15:00",
            "Добавь задачу позвонить клиенту через 2 часа",
            "Покажи все мои задачи",
            "Я переехал в Москву и работаю Senior Engineer в Google",
            "Удали первую задачу",
            "Спасибо, пока!"
        ]
        
        for step in range(1, min(max_steps + 1, len(test_commands) + 1)):
            user_message = test_commands[step - 1]
            print(f"[Shag {step}]")
            print(f"User: {user_message}")
            print()
            
            try:
                # Вызываем AI агента с таймаутом
                ai_response = await asyncio.wait_for(
                    chat_with_ai(
                        message=user_message,
                        user_id=user.telegram_id
                    ),
                    timeout=45.0
                )
                
                print(f"AI: {ai_response}")
                print()
                
                # Проверяем на наличие tool calls в логах
                if "Args for" in ai_response or "tool_calls" in ai_response.lower():
                    print(">>> Tool call detected!")
                
                # Сохраняем в контекст
                conversation_context.append(f"User: {user_message}")
                conversation_context.append(f"AI: {ai_response[:200]}")  # Первые 200 символов
                
                # Проверяем реальные изменения в БД
                session.expire_all()  # Обновляем данные из БД
                tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.id).all()
                
                print(f">> Database state after step:")
                print(f"   Total tasks: {len(tasks)}")
                if tasks:
                    now = datetime.now(pytz.UTC)
                    for task in tasks[-3:]:  # Последние 3 задачи
                        if task.reminder_time:
                            # Убедимся что reminder_time timezone-aware
                            reminder_tz = task.reminder_time if task.reminder_time.tzinfo else pytz.UTC.localize(task.reminder_time)
                            status = "overdue" if reminder_tz < now else "active"
                            reminder = task.reminder_time.strftime("%H:%M")
                        else:
                            status = "no time"
                            reminder = "not set"
                        print(f"   - [{status}] {task.title} (reminder: {reminder})")
                print()
                print("-" * 80)
                print()
                    
            except asyncio.TimeoutError:
                print(f"ERROR: Timeout after 45 seconds on step {step}")
                print()
                print("-" * 80)
                print()
                continue
            except Exception as e:
                print(f"ERROR: {e}")
                import traceback
                traceback.print_exc()
                print()
                print("-" * 80)
                print()
                break
        
        print("="*80)
        print("DIALOGUE COMPLETED")
        print("="*80)
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_dialogue())
