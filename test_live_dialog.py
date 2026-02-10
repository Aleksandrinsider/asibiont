"""
Тест полезности агента: DeepSeek играет роль предпринимателя
Проверяем как агент помогает решать реальные бизнес-задачи
Сценарий: Нужно привлечь клиентов для ИИ агента
"""
import asyncio
import sys
import os
import httpx
import json
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task, Subscription, SubscriptionTier
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from reminder_service import ReminderService
import reminder_service as reminder_service_module

# DeepSeek API для генерации сообщений пользователя
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

async def generate_user_message(conversation_history, turn_number):
    """Генерирует сообщение от имени пользователя через DeepSeek (реальные условия)"""
    
    # Персона пользователя - предприниматель с проблемой привлечения клиентов
    user_persona = f"""Ты - предприниматель, который создал ИИ-агента для бизнеса.

ПРОБЛЕМА: Нужно привлечь первых клиентов для тестирования агента.

Твой характер:
- Деловой, но не слишком формальный  
- Конкретные вопросы о маркетинге, продвижении, поиске клиентов
- Можешь поделиться деталями проекта
- Ищешь практические решения
- Иногда сомневаешься в своих идеях

Ход {turn_number}/15 диалога"""
    
    # Направляем диалог по сценарию
    if turn_number == 1:
        hint = "Поприветствуйся и кратко опиши проблему с привлечением клиентов"
    elif turn_number == 2:
        hint = "Расскажи чуть больше про своего ИИ-агента"
    elif turn_number == 3:
        hint = "Спроси конкретный совет по привлечению клиентов"
    elif turn_number in [4, 5]:
        hint = "Реагируй на совет агента, можешь задать уточняющие вопросы"
    elif turn_number in [6, 7]:
        hint = "Попроси помочь с конкретными действиями (например, написать пост, найти партнеров)"
    elif turn_number in [8, 9, 10]:
        hint = "Обсуждай предложения агента, можешь согласиться на создание задач"
    elif turn_number >= 11:
        hint = "Подводи итоги, спрашивай что дальше делать"
    else:
        hint = "Продолжай обсуждение по теме привлечения клиентов"
    
    messages = [
        {"role": "system", "content": user_persona},
        {"role": "user", "content": f"Последние сообщения:\n{conversation_history[-800:]}\n\n{hint}\n\nТвое сообщение:"}
    ]
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
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
                    "max_tokens": 50
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                user_msg = result['choices'][0]['message']['content'].strip()
                user_msg = user_msg.replace("Алексей:", "").replace("Пользователь:", "").strip()
                return user_msg
            else:
                return None
    except Exception as e:
        print(f"Ошибка генерации сообщения: {e}")
        return None

async def run_live_dialog_test():
    """Запускает тест полезности агента на реальной бизнес-задаче"""
    
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
        session.query(Subscription).filter_by(user_id=user.id).delete()
        session.commit()
        session.delete(user)
        session.commit()
    
    # Создаем пользователя - предпринимателя с ИИ-агентом (STANDARD тариф для research_topic)
    user = User(
        telegram_id=user_id, 
        username='ai_founder', 
        first_name='Максим', 
        timezone='Europe/Moscow',
        subscription_tier=SubscriptionTier.STANDARD
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    
    # Добавляем активную подписку STANDARD
    from datetime import datetime, timezone, timedelta
    subscription = Subscription(
        user_id=user.id,
        telegram_id=user_id,  # Обязательное поле
        telegram_username=user.username,
        username=user.username,
        plan='STANDARD',
        tier=SubscriptionTier.STANDARD,  # Правильное поле для тарифа
        status='active',
        start_date=datetime.now(timezone.utc),
        end_date=datetime.now(timezone.utc) + timedelta(days=30)
    )
    session.add(subscription)
    session.commit()
    
    # Профиль предпринимателя с ИИ-проектом
    profile = UserProfile(
        user_id=user.id, 
        interests='ИИ, стартапы, маркетинг, привлечение клиентов', 
        goals='Привлечь первых 50 клиентов для тестирования ИИ-агента, запустить beta-версию',
        city='Москва',
        company='AI Solutions',
        position='CEO & Founder',
        skills='продуктовая разработка, Python, управление проектами',
        bio='Создаю ИИ-агента для автоматизации бизнес-процессов. Ищу первых клиентов для тестирования.'
    )
    session.add(profile)
    session.commit()
    
    print("="*80)
    print("[ТЕСТ ПОЛЕЗНОСТИ АГЕНТА]")
    print("Сценарий: Предприниматель ищет способы привлечь клиентов для ИИ-агента")
    print("Цель: Проверить насколько агент полезен в решении реальных бизнес-задач")
    print("="*80)
    print()
    
    conversation_history = ""
    turn = 0
    max_turns = 15  # Увеличил для полного раскрытия возможностей
    tools_used = 0
    useful_actions = []  # Отслеживаем полезные действия
    
    while turn < max_turns:
        turn += 1
        
        print(f"\n{'-'*80}")
        print(f"[ХОД {turn}/{max_turns}]")
        print(f"{'-'*80}\n")
        
        # Генерируем сообщение пользователя
        user_message = await generate_user_message(conversation_history, turn)
        
        if not user_message:
            print("[X] Не удалось сгенерировать сообщение")
            break
        
        print(f"[USER] {user_message}")
        
        # Отправляем агенту
        try:
            response = await chat_with_ai(user_message, user_id=user_id, db_session=session)
            agent_response = response.get('response', 'Нет ответа')
            
            # Анализируем полезность ответа
            used_tools = response.get('tools_used', [])
            if used_tools:
                tools_used += len(used_tools)
                useful_actions.extend(used_tools)
            
            # Показываем полный ответ (агент может быть подробным если полезен)
            print(f"[BOT] {agent_response}")
            
            if used_tools:
                print(f"[TOOLS] Использованы: {', '.join(used_tools)}")
            
            # Обновляем историю
            conversation_history += f"\nПользователь: {user_message}\nАссистент: {agent_response[:300]}\n"
            
        except KeyboardInterrupt:
            print("\n[!] Прервано пользователем")
            break
        except Exception as e:
            print(f"[X] ОШИБКА: {e}")
            break
        
        # Пауза между ходами
        await asyncio.sleep(1.0)
    
    # Расширенный анализ полезности
    print("\n" + "="*80)
    print("[АНАЛИЗ ПОЛЕЗНОСТИ АГЕНТА]")
    print("="*80)
    
    print(f"\n🎯 ДИАЛОГ:")
    print(f"   Ходов диалога: {turn}/{max_turns}")
    print(f"   Инструментов использовано: {tools_used}")
    
    # Анализ полезности действий
    if useful_actions:
        print(f"\n🛠️  ПОЛЕЗНЫЕ ДЕЙСТВИЯ:")
        action_counts = {}
        for action in useful_actions:
            action_counts[action] = action_counts.get(action, 0) + 1
        
        for action, count in action_counts.items():
            print(f"   • {action}: {count}x")
        
        # Оценка по категориям
        marketing_tools = ['generate_marketing_content', 'publish_to_telegram', 'research_topic']
        task_tools = ['add_task', 'list_tasks', 'complete_task'] 
        contact_tools = ['find_partners', 'find_relevant_contacts_for_task']
        
        marketing_used = sum(1 for tool in useful_actions if tool in marketing_tools)
        task_used = sum(1 for tool in useful_actions if tool in task_tools)
        contact_used = sum(1 for tool in useful_actions if tool in contact_tools)
        
        print(f"\n📊 ПОЛЕЗНОСТЬ ПО КАТЕГОРИЯМ:")
        print(f"   📈 Маркетинг: {marketing_used} действий")
        print(f"   📋 Управление задачами: {task_used} действий") 
        print(f"   👥 Нетворкинг: {contact_used} действий")
    
    # НОВАЯ сессия для чтения финальных данных
    final_session = Session()
    try:
        # Проверяем созданные задачи
        tasks = final_session.query(Task).filter_by(user_id=user.id).all()
        print(f"\n📋 РЕЗУЛЬТАТ - СОЗДАННЫЕ ЗАДАЧИ: {len(tasks)}")
        
        business_tasks = 0
        marketing_tasks = 0
        
        for task in tasks:
            task_title_lower = task.title.lower()
            time_str = task.reminder_time.strftime("%d.%m %H:%M") if task.reminder_time else "без времени"
            
            # Категоризируем задачи
            if any(word in task_title_lower for word in ['клиент', 'маркетинг', 'пост', 'реклам', 'продвиж']):
                marketing_tasks += 1
                category = "📈"
            else:
                business_tasks += 1
                category = "💼"
                
            print(f"   {category} {task.title} ({time_str})")
        
        print(f"\n📈 Маркетинг/клиенты: {marketing_tasks} задач")
        print(f"💼 Общий бизнес: {business_tasks} задач")
        
        # Проверяем обновления профиля
        profile = final_session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"\n👤 ПРОФИЛЬ ОБНОВЛЁН:")
            changes = []
            if profile.skills and 'маркетинг' in profile.skills.lower():
                changes.append("навыки маркетинга")
            if profile.goals and len(profile.goals) > 100:
                changes.append("расширены цели")
            if profile.bio and 'клиент' in profile.bio.lower():
                changes.append("обновлено описание")
            
            if changes:
                print(f"   ✅ {', '.join(changes)}")
            else:
                print(f"   📝 Базовая информация сохранена")
                
        # ИТОГОВАЯ ОЦЕНКА ПОЛЕЗНОСТИ 
        total_score = 0
        
        # За использование инструментов (до 40 баллов)
        tool_score = min(tools_used * 4, 40)
        total_score += tool_score
        
        # За создание бизнес-задач (до 30 баллов) 
        task_score = min(len(tasks) * 6, 30)
        total_score += task_score
        
        # За маркетинговые действия (до 30 баллов)
        marketing_score = min((marketing_used + marketing_tasks) * 5, 30)
        total_score += marketing_score
        
        print(f"\n🏆 ИТОГОВАЯ ОЦЕНКА ПОЛЕЗНОСТИ:")
        print(f"   🛠️  Инструменты: {tool_score}/40")
        print(f"   📋 Задачи: {task_score}/30") 
        print(f"   📈 Маркетинг: {marketing_score}/30")
        print(f"   🎯 ОБЩИЙ БАЛЛ: {total_score}/100")
        
        if total_score >= 80:
            rating = "🚀 ОТЛИЧНО - Агент очень полезен"
        elif total_score >= 60:
            rating = "✅ ХОРОШО - Агент полезен"
        elif total_score >= 40:
            rating = "⚠️ СРЕДНЕ - Есть потенциал"
        else:
            rating = "❌ ПЛОХО - Агент не помогает"
            
        print(f"   {rating}")
            
    finally:
        final_session.close()
    
    # Очистка
    try:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.query(Subscription).filter_by(user_id=user.id).delete()
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
    print("[ТЕСТ ЗАВЕРШЁН] - Оценка готова")
    print("="*80)

if __name__ == '__main__':
    try:
        asyncio.run(run_live_dialog_test())
    except KeyboardInterrupt:
        print("\n\n[!] Тест полезности прерван пользователем")
    except Exception as e:
        print(f"\n\n[X] Ошибка теста полезности: {e}")
