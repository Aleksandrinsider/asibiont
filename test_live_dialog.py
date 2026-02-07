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
    """Генерирует сообщение от имени пользователя через DeepSeek БЕЗ заготовок"""
    
    # Персона пользователя для естественного поведения
    user_persona = """Ты - Алексей, 32 года, программист из Москвы.

Твоя жизнь:
- Работаешь над AI-проектом, дедлайн через неделю
- Хочешь найти партнеров для стартапа в области ML
- Интересуешься Python, AI, machine learning
- Занимаешься бегом по утрам
- Типичный день: работа 9-18, вечером саморазвитие

Твое взаимодействие с ассистентом:
- Используй его для управления задачами и получения советов
- Веди себя естественно - создавай задачи когда нужно, спрашивай совета, отмечай выполненное
- Иногда отвлекайся на отвлеченные темы (погода, мотивация, карьера)
- Говори разговорным языком, используй сокращения
- Реагируй на ответы ассистента - благодари, уточняй, соглашайся или не соглашайся
- НЕ следуй никакому сценарию - веди себя как живой человек в реальном диалоге

ВАЖНО:
- Смотри на КОНТЕКСТ диалога и реагируй естественно
- Если ассистент дал совет - прокомментируй его
- Если ассистент создал задачу - можешь попросить что-то еще или поблагодарить
- Не форсируй все функции подряд - веди себя органично
- Пиши 1-2 предложения, как в реальной переписке

Сейчас ход {turn_number}."""
    
    # Формируем промпт на основе истории диалога БЕЗ заготовок
    context_instruction = ""
    if turn_number == 1:
        context_instruction = "Это начало диалога. Поздоровайся и кратко представься."
    elif turn_number >= 38:
        context_instruction = "Диалог подходит к концу. Можешь постепенно завершать общение."
    else:
        context_instruction = "Продолжай естественный диалог на основе контекста. Реагируй на ответ ассистента."
    
    messages = [
        {"role": "system", "content": user_persona},
        {"role": "user", "content": f"История диалога:\n{conversation_history}\n\n{context_instruction}\n\nОтветь ОДНИМ коротким сообщением (1-2 предложения) от имени Алексея:"}
    ]
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                DEEPSEEK_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "temperature": 0.8,
                    "max_tokens": 100
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
    
    # Создаем пользователя
    user = User(telegram_id=user_id, username='alexey_test', first_name='Алексей', timezone='Europe/Moscow')
    session.add(user)
    session.commit()
    session.refresh(user)
    
    profile = UserProfile(user_id=user.id, interests='', goals='', city='')
    session.add(profile)
    session.commit()
    
    print("="*80)
    print("[TEST] ZHIVOY DIALOG")
    print("DeepSeek igraet rol polzovatelya Alekseya")
    print("="*80)
    print()
    
    conversation_history = ""
    turn = 0
    max_turns = 15  # Quick test
    
    while turn < max_turns:
        turn += 1
        
        # Генерируем сообщение пользователя
        print(f"\n{'-'*80}")
        print(f"[HOD {turn}/{max_turns}]")
        print(f"{'-'*80}\n")
        
        print("[*] Generiruyu soobschenie polzovatelya...")
        user_message = await generate_user_message(conversation_history, turn)
        
        if not user_message:
            print("[X] Ne udalos sgenerirovat soobschenie")
            break
        
        print(f"\n[USER] POLZOVATEL (Aleksey):")
        print(f"   {user_message}")
        
        # Отправляем агенту
        print(f"\n[...] Agent obrabatyvaet...")
        try:
            response = await chat_with_ai(user_message, user_id=user_id, db_session=session)
            agent_response = response.get('response', 'Нет ответа')
            
            # Убираем технические символы для чистого вывода
            clean_response = agent_response.replace('✅', '').replace('🎯', '').replace('📋', '')
            clean_response = ' '.join(clean_response.split())  # Убираем лишние пробелы
            
            print(f"\n[BOT] AGENT (ASI Biont):")
            # Выводим ответ с переносом строк для читаемости
            lines = clean_response.split('\n')
            for line in lines[:5]:  # Первые 5 строк
                if line.strip():
                    print(f"   {line.strip()[:120]}")
            
            if len(lines) > 5:
                print(f"   ... (еще {len(lines) - 5} строк)")
            
            # Обновляем историю
            conversation_history += f"\nПользователь: {user_message}\nАссистент: {clean_response[:200]}\n"
            
        except Exception as e:
            print(f"\n[X] OSHIBKA: {e}")
            break
        
        # Небольшая пауза между ходами
        await asyncio.sleep(1)
    
    # Финальный анализ
    print("\n" + "="*80)
    print("[REPORT] ANALIZ DIALOGA")
    print("="*80)
    
    # Создаем НОВУЮ сессию для чтения финальных данных
    final_session = Session()
    try:
        # Проверяем созданные задачи
        tasks = final_session.query(Task).filter_by(user_id=user.id).all()
        print(f"\n[+] Zadachi sozdany: {len(tasks)}")
        for i, task in enumerate(tasks, 1):
            status_icon = "+" if task.status == 'completed' else "o"
            print(f"   [{status_icon}] {i}. {task.title} ({task.status})")
        
        # Проверяем профиль
        profile = final_session.query(UserProfile).filter_by(user_id=user.id).first()
        print(f"\n[PROFILE] Profil obnovlen:")
        print(f"   Gorod: {profile.city or '(ne ukazan)'}")
        print(f"   Interesy: {profile.interests or '(ne ukazany)'}")
        print(f"   Navyki: {profile.skills or '(ne ukazany)'}")
    finally:
        final_session.close()
    
    # Очистка
    try:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.delete(profile)
        session.commit()
        session.delete(user)
        session.commit()
    except:
        pass
    finally:
        session.close()
    
    print("\n" + "="*80)
    print("[+] TEST ZAVERSHEN")
    print("="*80)

if __name__ == '__main__':
    asyncio.run(run_live_dialog_test())
