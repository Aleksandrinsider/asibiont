import os
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import logging
import sys
import json
import aiohttp
from datetime import datetime
from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, init_db, SubscriptionTier
import traceback

# Настройка кодировки для Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Функции для проверки
FUNCTIONS_TO_TEST = [
    {"name": "add_task", "tested": False, "description": "Создать задачу"},
    {"name": "list_tasks", "tested": False, "description": "Показать все задачи"},
    {"name": "complete_task", "tested": False, "description": "Завершить задачу"},
    {"name": "delete_task", "tested": False, "description": "Удалить задачу"},
    {"name": "reschedule_task", "tested": False, "description": "Перенести задачу"},
    {"name": "update_profile", "tested": False, "description": "Обновить профиль"},
    {"name": "find_partners", "tested": False, "description": "Найти партнеров"},
    {"name": "set_recurring_task", "tested": False, "description": "Создать повторяющуюся задачу"},
    {"name": "get_task_details", "tested": False, "description": "Получить детали задачи"},
    {"name": "update_user_memory", "tested": False, "description": "Сохранить в память"},
    {"name": "delegate_task", "tested": False, "description": "Делегировать задачу"},
    {"name": "delete_all_tasks", "tested": False, "description": "Удалить все задачи"}
]

async def generate_user_message(conversation_history, tested_functions, current_iteration, max_iterations):
    """Генерирует следующее сообщение пользователя через DeepSeek AI"""
    
    # Находим непротестированные функции
    untested = [f for f in tested_functions if not f["tested"]]
    tested = [f for f in tested_functions if f["tested"]]
    
    # Формируем контекст для AI
    context_messages = [
        {
            "role": "system",
            "content": f"""Ты симулируешь пользователя Telegram-бота для управления задачами.

ТВОЯ ЗАДАЧА: Сгенерировать естественное сообщение на русском языке, которое протестирует одну из НЕПРОТЕСТИРОВАННЫХ функций.

НЕПРОТЕСТИРОВАННЫЕ ФУНКЦИИ ({len(untested)} осталось):
{chr(10).join(f"- {f['name']}: {f['description']}" for f in untested)}

ПРОТЕСТИРОВАННЫЕ ФУНКЦИИ ({len(tested)} из {len(tested_functions)}):
{chr(10).join(f"✓ {f['name']}" for f in tested) if tested else "Пока нет"}

ПРАВИЛА ГЕНЕРАЦИИ:
1. Сообщение должно быть ЕСТЕСТВЕННЫМ и РАЗГОВОРНЫМ (как пишет реальный пользователь)
2. Выбери ОДНУ непротестированную функцию из списка выше
3. НЕ используй технические термины (например, пиши "покажи мои задачи" вместо "вызови list_tasks")
4. Используй контекст предыдущих сообщений (например, если создавалась задача "купить молоко", можешь её завершить)
5. Варьируй стиль: иногда краткое сообщение ("Готово"), иногда развернутое
6. Делай сообщения живыми и естественными
7. Для delegate_task используй реальных пользователей из базы: @test1, @test2 и т.д.
8. Для reschedule_task ссылайся на существующие задачи из контекста

ПРИМЕРЫ ЕСТЕСТВЕННЫХ СООБЩЕНИЙ:
- "Покажи что у меня запланировано" (list_tasks)
- "Сделал!" (complete_task)
- "Удали задачу про молоко" (delete_task)
- "Перенеси задачу 'купить молоко' на завтра в 10 утра" (reschedule_task) - ОБЯЗАТЕЛЬНО указывай название задачи!
- "Я из Москвы, работаю программистом" (update_profile)
- "Найди мне единомышленников" (find_partners)
- "Напоминай мне делать зарядку каждый день в 7 утра" (set_recurring_task)
- "Расскажи подробнее о первой задаче" (get_task_details)
- "Запомни что я люблю читать книги перед сном" (update_user_memory)
- "Поручи задачу 'купить продукты' пользователю @test1" (delegate_task)
- "Удали все старые задачи" (delete_all_tasks)

КРИТИЧЕСКИ ВАЖНО ДЛЯ reschedule_task:
- ВСЕГДА явно указывай название задачи в сообщении: "Перенеси задачу 'НАЗВАНИЕ' на..."
- Не говори просто "перенеси на завтра" - скажи "Перенеси задачу 'КОНКРЕТНОЕ НАЗВАНИЕ' на завтра"

ВАЖНО: Верни ТОЛЬКО текст сообщения, без пояснений и кавычек."""
        }
    ]
    
    # Добавляем последние 5 сообщений из истории для контекста
    recent_history = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
    for msg in recent_history:
        if msg["role"] in ["user", "assistant"]:
            context_messages.append({
                "role": msg["role"],
                "content": msg["content"][:200]  # Ограничиваем длину
            })
    
    try:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY не установлен")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": context_messages,
                    "temperature": 0.8,
                    "max_tokens": 150
                }
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    message = data["choices"][0]["message"]["content"].strip()
                    # Убираем кавычки если есть
                    message = message.strip('"\'')
                    return message
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка DeepSeek API: {response.status} - {error_text}")
                    return "Покажи мои задачи"  # Fallback
    
    except Exception as e:
        logger.error(f"Ошибка генерации сообщения: {e}")
        # Fallback на заготовленные сообщения
        fallback_messages = [
            "Создай задачу: позвонить другу завтра в 15:00",
            "Покажи мои задачи",
            "Готово",
            "Удали последнюю задачу",
            "Я из Москвы, люблю спорт"
        ]
        return fallback_messages[current_iteration % len(fallback_messages)]

def extract_function_calls(response_data):
    """Извлекает названия вызванных функций из ответа"""
    functions_called = []
    
    # Если response_data это строка - нет вызовов функций
    if isinstance(response_data, str):
        return functions_called
    
    # Если это dict с tool_calls
    if isinstance(response_data, dict):
        tool_calls = response_data.get('tool_calls', [])
        if tool_calls:
            for call in tool_calls:
                if isinstance(call, dict):
                    # Проверяем разные форматы
                    if 'function' in call:
                        func_data = call.get('function', {})
                        if isinstance(func_data, dict):
                            func_name = func_data.get('name')
                            if func_name:
                                functions_called.append(func_name)
                        elif isinstance(func_data, str):
                            functions_called.append(func_data)
                    elif 'name' in call:
                        func_name = call.get('name')
                        if func_name:
                            functions_called.append(func_name)
                elif isinstance(call, str):
                    # Если call - это просто строка с именем функции
                    functions_called.append(call)
    
    return functions_called

async def test_dialogue_with_ai():
    """Тестирует диалог с AI-генерацией сообщений пользователя"""
    print("\n" + "="*80)
    print("🤖 ТЕСТИРОВАНИЕ ДИАЛОГА С AI-ГЕНЕРАЦИЕЙ СООБЩЕНИЙ")
    print("="*80 + "\n")
    
    # Инициализируем базу данных
    try:
        init_db()
        print("✅ База данных инициализирована\n")
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")
        return
    
    # Создаем тестового пользователя
    db_session = Session()
    stats = {
        'total': 0,
        'success': 0,
        'errors': 0,
        'error_details': []
    }
    
    try:
        user = db_session.query(User).filter_by(telegram_id=27).first()
        if not user:
            user = User(
                telegram_id=27,
                username="test_user",
                first_name="Тест",
                subscription_tier=SubscriptionTier.LIGHT,
                timezone="Europe/Moscow"
            )
            db_session.add(user)
            db_session.commit()
            print(f"✅ Создан тестовый пользователь: ID {user.id}, telegram_id {user.telegram_id}\n")
        else:
            print(f"✅ Найден тестовый пользователь: ID {user.id}, telegram_id {user.telegram_id}\n")
        
        # Создаем профиль если нет
        profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id, interests="спорт, программирование")
            db_session.add(profile)
            db_session.commit()
            print("✅ Создан профиль пользователя\n")
        
        db_session.close()
        
    except Exception as e:
        print(f"❌ Ошибка создания пользователя: {e}")
        db_session.close()
        return
    
    # История диалога
    conversation_history = []
    
    # Максимальное количество итераций
    max_iterations = 25
    
    print("─"*80)
    print(f"🎯 ЦЕЛЬ: Протестировать все {len(FUNCTIONS_TO_TEST)} функций")
    print(f"📝 МЕТОД: AI генерирует сообщения пользователя в реальном времени")
    print(f"🔄 МАКСИМУМ ИТЕРАЦИЙ: {max_iterations}")
    print("─"*80 + "\n")
    
    for iteration in range(1, max_iterations + 1):
        print("─"*80)
        print(f"📝 ИТЕРАЦИЯ {iteration}/{max_iterations}")
        print("─"*80)
        
        stats['total'] += 1
        
        # Проверяем завершены ли все тесты
        all_tested = all(f["tested"] for f in FUNCTIONS_TO_TEST)
        if all_tested:
            print("\n✨ ВСЕ ФУНКЦИИ ПРОТЕСТИРОВАНЫ!")
            break
        
        try:
            # Генерируем сообщение пользователя
            print("\n🤔 Генерирую сообщение пользователя через DeepSeek AI...")
            user_message = await generate_user_message(
                conversation_history,
                FUNCTIONS_TO_TEST,
                iteration,
                max_iterations
            )
            
            print(f"\n👤 Пользователь: {user_message}")
            conversation_history.append({"role": "user", "content": user_message})
            
            # Создаём отдельную сессию для этого запроса
            request_session = Session()
            
            try:
                # Отправляем в AI агента с сессией
                response_data = await chat_with_ai(
                    message=user_message,
                    user_id=27,
                    context=conversation_history[-20:],  # Последние 20 сообщений
                    db_session=request_session
                )
            finally:
                request_session.close()
            
            # Обрабатываем ответ
            if isinstance(response_data, str):
                response = response_data
                tool_calls = []
            elif isinstance(response_data, dict):
                response = response_data.get('response', 'Нет ответа')
                tool_calls = response_data.get('tool_calls', [])
            else:
                response = str(response_data)
                tool_calls = []
            
            print(f"\n🤖 Агент: {response[:300]}{'...' if len(response) > 300 else ''}")
            
            # Извлекаем вызванные функции
            functions_called = extract_function_calls(response_data)
            
            if functions_called:
                print(f"\n⚙️  Вызванные функции ({len(functions_called)}):")
                for func_name in functions_called:
                    print(f"   ✓ {func_name}")
                    
                    # Отмечаем функцию как протестированную
                    for func in FUNCTIONS_TO_TEST:
                        if func["name"] == func_name and not func["tested"]:
                            func["tested"] = True
                            print(f"      ✅ НОВАЯ ФУНКЦИЯ ПРОТЕСТИРОВАНА!")
            else:
                print("\n⚙️  Функции не вызывались")
            
            # Добавляем ответ в историю
            conversation_history.append({"role": "assistant", "content": response})
            
            stats['success'] += 1
            
            # Показываем прогресс
            tested_count = sum(1 for f in FUNCTIONS_TO_TEST if f["tested"])
            untested = [f["name"] for f in FUNCTIONS_TO_TEST if not f["tested"]]
            
            print(f"\n📊 ПРОГРЕСС: {tested_count}/{len(FUNCTIONS_TO_TEST)} функций протестировано")
            if untested:
                print(f"⏳ Осталось протестировать: {', '.join(untested)}")
            
            # Пауза между итерациями
            await asyncio.sleep(0.5)
            
        except Exception as e:
            stats['errors'] += 1
            error_msg = str(e)
            stats['error_details'].append({
                'iteration': iteration,
                'message': user_message if 'user_message' in locals() else 'N/A',
                'error': error_msg,
                'traceback': traceback.format_exc()
            })
            
            print(f"\n❌ ОШИБКА: {error_msg}")
            print(f"📋 Трассировка:\n{traceback.format_exc()}")
    
    # Финальный отчет
    print("\n" + "="*80)
    print("📊 ИТОГОВЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ")
    print("="*80)
    print(f"\nВсего итераций:           {stats['total']}")
    print(f"✅ Успешно:               {stats['success']} ({stats['success']/stats['total']*100:.1f}%)")
    print(f"❌ С ошибками:            {stats['errors']} ({stats['errors']/stats['total']*100:.1f}%)")
    
    print("\n" + "─"*80)
    print("🔧 СТАТУС ФУНКЦИЙ:")
    print("─"*80)
    
    tested = [f for f in FUNCTIONS_TO_TEST if f["tested"]]
    untested = [f for f in FUNCTIONS_TO_TEST if not f["tested"]]
    
    if tested:
        print(f"\n✅ ПРОТЕСТИРОВАНО ({len(tested)}):")
        for func in tested:
            print(f"   ✓ {func['name']} - {func['description']}")
    
    if untested:
        print(f"\n❌ НЕ ПРОТЕСТИРОВАНО ({len(untested)}):")
        for func in untested:
            print(f"   ✗ {func['name']} - {func['description']}")
    
    if stats['error_details']:
        print("\n" + "─"*80)
        print("🔍 ДЕТАЛИ ОШИБОК:")
        print("─"*80)
        
        for error in stats['error_details']:
            print(f"\n❌ Итерация {error['iteration']}")
            print(f"   Сообщение: {error['message'][:100]}...")
            print(f"   Ошибка: {error['error']}")
            print(f"   Трассировка:\n{error['traceback'][:500]}...\n")
    
    print("\n" + "="*80)
    print("✨ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("="*80 + "\n")

if __name__ == "__main__":
    asyncio.run(test_dialogue_with_ai())
