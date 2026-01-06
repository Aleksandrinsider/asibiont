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
    """Генерирует ответ пользователя на основе сообщения AI-агента"""
    
    # Собираем контекст последних сообщений
    context_summary = "\n".join(conversation_context[-6:]) if conversation_context else "Начало диалога"
    
    # Специальные случаи для начала и конца
    if scenario_step == 1:
        return "Привет!"
    elif scenario_step >= 19:
        return "Спасибо, пока!"
    
    # Анализируем сообщение AI и генерируем естественный ответ
    prompt = f"""Ты пользователь, который общается с AI-ассистентом по задачам.

История диалога:
{context_summary}

AI только что сказал:
"{ai_message}"

Проанализируй ответ AI и реагируй ЕСТЕСТВЕННО:

- Если AI спросил что-то или предложил помощь → дай конкретную команду (добавь задачу, покажи список, делегируй @test_user, удали задачу, расскажи о работе/городе)
- Если AI показал задачи → попроси удалить/добавить/делегировать или скажи что-то о задачах
- Если AI добавил задачу → попроси показать список ИЛИ добавь ещё задачу ИЛИ делегируй что-то
- Если AI удалил задачу → попроси показать что осталось ИЛИ добавь новую
- Варьируй действия: добавление, удаление, делегирование @test_user, просмотр списка, информация о себе

Правила:
- КОРОТКИЙ ответ (5-12 слов)
- Конкретная команда/действие
- Варьируй формулировки
- Используй разные времена: "через 2 часа", "завтра в 10:00", "послезавтра в 15:00"
- Только текст команды, БЕЗ кавычек и пояснений

Ответ пользователя:"""

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
                "temperature": 0.9,
                "max_tokens": 50
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()["choices"][0]["message"]["content"].strip()
            result = result.strip('"\'')
            # Убираем лишние пояснения если AI добавил
            if '\n' in result:
                result = result.split('\n')[0]
            return result
        else:
            return "Покажи мои задачи"
            
    except Exception as e:
        print(f"[Предупреждение] Ошибка генерации: {e}")
        return "Что у меня в списке?"


async def test_ai_dialogue():
    """Тестирует AI диалог с генерацией ответов пользователя"""
    print("\n" + "="*80)
    print("INTERACTIVE AI DIALOGUE TEST WITH RESPONSE GENERATION")
    print("="*80 + "\n")
    
    session = SessionLocal()
    
    try:
        # Используем реального пользователя (ID 146333757) для тестирования
        # Данные будут видны в панели управления в реальном времени
        user = session.query(User).filter_by(telegram_id=146333757).first()
        
        if not user:
            print("[INFO] Creating user...")
            user = User(
                telegram_id=146333757,
                username="Aleksandrinsider",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            print("[OK] User created\n")
        else:
            print(f"[OK] User: {user.username} (ID: {user.telegram_id})")
            print(f"    Timezone: {user.timezone or 'UTC'}")
            
            # НЕ очищаем задачи - работаем с реальными данными
            tasks_count = session.query(Task).filter_by(user_id=user.id).count()
            print(f"    Existing tasks in DB: {tasks_count}")
            print(f"    [NOTE] Working with REAL user data - check dashboard!")
            
            # Проверяем что БД чистая
            tasks_count = session.query(Task).filter_by(user_id=user.id).count()
            print(f"    Tasks in DB: {tasks_count}\n")
        
        print("="*80)
        print("DIALOGUE START - 20 ITERATIONS (AI-GENERATED RESPONSES)")
        print("="*80 + "\n")
        
        conversation_context = []
        max_steps = 20
        
        for step in range(1, max_steps + 1):
            # Генерируем ответ пользователя на основе предыдущего сообщения AI
            if step == 1:
                user_message = "Привет!"
            else:
                # Используем последний AI ответ для генерации следующей команды
                last_ai_message = conversation_context[-1].replace("AI: ", "") if conversation_context else ""
                print(f"[Генерация команды пользователя на основе: {last_ai_message[:60]}...]")
                user_message = generate_user_response(last_ai_message, conversation_context, step)
            print(f"[Shag {step}]")
            print(f"User: {user_message}")
            print()
            
            try:
                # Вызываем AI агента с таймаутом
                # AI агент теперь сам сохраняет Interactions в базу
                ai_response = await asyncio.wait_for(
                    chat_with_ai(
                        message=user_message,
                        user_id=user.telegram_id
                    ),
                    timeout=90.0
                )
                
                # Удаляем emoji для безопасного вывода в Windows консоль
                safe_response = ai_response.encode('ascii', 'ignore').decode('ascii')
                print(f"AI: {safe_response}")
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
