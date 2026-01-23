"""
Тест диалога с агентом - проверка ошибок на 2-3-м и последующих ответах
Использует AI для генерации реалистичных ответов пользователя
"""
import asyncio
import os
import sys
from datetime import datetime

# Set local mode
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile, Task, Interaction
from ai_integration.chat import chat_with_ai

# DeepSeek API для генерации ответов пользователя
import httpx

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-e8ccf0d34df049de821cb0f6e0d2f49e")

async def generate_user_response(conversation_history):
    """Генерирует естественный ответ пользователя на основе истории диалога"""
    prompt = f"""Ты - пользователь task-менеджера. Веди естественный диалог с AI-ассистентом.
    
История диалога:
{conversation_history}

Напиши короткий естественный ответ (1-2 предложения) как обычный пользователь. 
Будь конкретным, отвечай по существу. Можешь:
- Подтверждать действия AI
- Просить выполнить новые задачи
- Давать дополнительную информацию
- Задавать уточняющие вопросы

Ответь ТОЛЬКО текстом пользователя, без пометок:"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 100
            }
        )
        result = response.json()
        return result["choices"][0]["message"]["content"].strip()

async def test_conversation():
    """Тестирует многоходовой диалог с агентом"""
    print("=" * 80)
    print("ТЕСТ МНОГОХОДОВОГО ДИАЛОГА С АГЕНТОМ")
    print("=" * 80)
    
    session = Session()
    
    # Очистим историю тестового пользователя
    test_user_id = 999999
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if user:
        session.query(Interaction).filter_by(user_id=user.id).delete()
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    else:
        user = User(telegram_id=test_user_id, username="test_conversation")
        session.add(user)
        session.commit()
    
    # Создаем профиль
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(
            user_id=user.id,
            city="Москва",
            interests="программирование, AI",
            goals="Улучшить продуктивность"
        )
        session.add(profile)
        session.commit()
    
    print(f"\n✓ Тестовый пользователь создан: {test_user_id}\n")
    
    # Инициализирующее сообщение
    conversation_history = ""
    errors_found = []
    
    # Начальный запрос
    initial_messages = [
        "Привет! Мне нужно купить продукты сегодня вечером",
        "Добавь мне задачу позвонить маме завтра в 10 утра",
        "Какие у меня задачи на сегодня?"
    ]
    
    user_message = initial_messages[0]
    
    for turn in range(1, 6):  # 5 ходов диалога
        print(f"\n{'=' * 80}")
        print(f"ХОД {turn}")
        print(f"{'=' * 80}")
        print(f"\n👤 Пользователь: {user_message}")
        
        conversation_history += f"\n👤 Пользователь: {user_message}"
        
        # Получаем ответ агента
        try:
            ai_response = await chat_with_ai(user_message, user_id=test_user_id)
            print(f"\n🤖 Агент: {ai_response}")
            conversation_history += f"\n🤖 Агент: {ai_response}"
            
            # Проверяем на распространенные ошибки
            turn_errors = []
            
            # 1. Проверка на выдуманные задачи
            if "пробежка" in ai_response.lower() or "тренировка" in ai_response.lower():
                # Проверим, есть ли такие задачи в БД
                session_check = Session()
                user_check = session_check.query(User).filter_by(telegram_id=test_user_id).first()
                tasks = session_check.query(Task).filter_by(user_id=user_check.id).all()
                task_titles = [t.title.lower() for t in tasks]
                session_check.close()
                
                if "пробежка" in ai_response.lower() and not any("пробежка" in t for t in task_titles):
                    turn_errors.append(f"❌ Ход {turn}: Агент упоминает несуществующую задачу 'пробежка'")
                if "тренировка" in ai_response.lower() and not any("тренировка" in t for t in task_titles):
                    turn_errors.append(f"❌ Ход {turn}: Агент упоминает несуществующую задачу 'тренировка'")
            
            # 2. Проверка на неполное время
            import re
            time_pattern = r'\b\d{1,2}:\s*\b'  # "8: " или "23: "
            if re.search(time_pattern, ai_response):
                turn_errors.append(f"❌ Ход {turn}: Неполный формат времени (например '8:' вместо '08:00')")
            
            # 3. Проверка на дублирующиеся сообщения о завершении задачи
            if ai_response.count("Завершена задача") > 1 or ai_response.count("завершена") > 2:
                turn_errors.append(f"❌ Ход {turn}: Возможное дублирование сообщения о завершении")
            
            # 4. Проверка на упоминание завершенных задач как активных
            if "завершена" in ai_response.lower() or "выполнена" in ai_response.lower():
                # В следующем ответе эта задача не должна быть в списке активных
                pass  # Проверим на следующем ходу
            
            # 5. Проверка на обрывы имен пользователей
            username_pattern = r'@\w+_\s'  # "@test_sport_ " - обрыв
            if re.search(username_pattern, ai_response):
                turn_errors.append(f"❌ Ход {turn}: Обрыв имени пользователя (например '@test_sport_')")
            
            # 6. Проверка на пустые ответы
            if len(ai_response.strip()) < 10:
                turn_errors.append(f"❌ Ход {turn}: Слишком короткий ответ агента")
            
            # 7. Проверка на ошибки в датах
            if "undefined" in ai_response.lower() or "null" in ai_response.lower():
                turn_errors.append(f"❌ Ход {turn}: В ответе есть 'undefined' или 'null'")
            
            if turn_errors:
                errors_found.extend(turn_errors)
                print("\n⚠️  ОШИБКИ НА ЭТОМ ХОДУ:")
                for error in turn_errors:
                    print(f"   {error}")
            else:
                print(f"\n✓ Ход {turn}: Ошибок не обнаружено")
        
        except Exception as e:
            error_msg = f"❌ Ход {turn}: КРИТИЧЕСКАЯ ОШИБКА - {str(e)}"
            errors_found.append(error_msg)
            print(f"\n{error_msg}")
            import traceback
            traceback.print_exc()
        
        # Генерируем следующий ответ пользователя
        if turn < 5:
            if turn == 1:
                # На втором ходу используем заготовленное сообщение
                user_message = initial_messages[1]
            else:
                # Генерируем естественный ответ через AI
                try:
                    user_message = await generate_user_response(conversation_history[-500:])
                except Exception as e:
                    print(f"\n⚠️  Не удалось сгенерировать ответ пользователя: {e}")
                    # Используем заготовленные варианты
                    fallback_messages = [
                        "Покажи список моих задач",
                        "Я сделал покупку продуктов",
                        "Что еще мне нужно сделать сегодня?",
                        "Спасибо, все понятно"
                    ]
                    user_message = fallback_messages[min(turn - 2, len(fallback_messages) - 1)]
        
        await asyncio.sleep(0.5)  # Небольшая пауза между ходами
    
    session.close()
    
    # Итоговый отчет
    print("\n" + "=" * 80)
    print("ИТОГОВЫЙ ОТЧЕТ")
    print("=" * 80)
    
    if errors_found:
        print(f"\n❌ НАЙДЕНО ОШИБОК: {len(errors_found)}")
        print("\nСписок ошибок:")
        for i, error in enumerate(errors_found, 1):
            print(f"{i}. {error}")
    else:
        print("\n✓ ОШИБОК НЕ НАЙДЕНО - все 5 ходов прошли успешно!")
    
    print("\n" + "=" * 80)
    
    return errors_found

if __name__ == "__main__":
    errors = asyncio.run(test_conversation())
    sys.exit(1 if errors else 0)
