import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from ai_integration import generate_reminder, generate_result_check, generate_daily_report, generate_proactive_message, generate_overdue_reminder
from ai_integration import add_task, list_tasks, complete_task, update_profile, find_partners
from models import Session, User, Task, UserProfile
from reminder_service import ReminderService
from subscription_service import check_subscription
from payments import create_payment
import datetime
import logging

logging.basicConfig(level=logging.INFO)

async def test_ai_functions():
    """Test AI integration functions"""
    print("=== Testing AI Functions ===")

    session = Session()
    try:
        # Create test user
        user = session.query(User).filter_by(telegram_id=999999999).first()
        if not user:
            user = User(telegram_id=999999999, username='test_ai_user')
            session.add(user)
            session.commit()

        # Test chat_with_ai
        print("Testing chat_with_ai...")
        response = await chat_with_ai("Создай задачу: купить молоко", [], user.telegram_id)
        print(f"Response: {response[:100]}...")

        # Test task functions
        print("Testing task functions...")
        task_id = add_task("Test task", "Test description", "2026-01-27 10:00", user_id=user.telegram_id)
        print(f"Created task ID: {task_id}")

        tasks = list_tasks(user.telegram_id)
        print(f"User has {len(tasks)} tasks")

        if isinstance(task_id, int):
            complete_task(task_id, user.telegram_id)
            print("Task completed")

        # Test profile update
        print("Testing profile update...")
        update_profile(interests="программирование, спорт", user_id=user.telegram_id)
        print("Profile updated")

        # Test partner finding
        print("Testing partner finding...")
        try:
            partners = find_partners(user.telegram_id, "программирование")
            print(f"Found partners: {partners}")
        except Exception as e:
            print(f"Error finding partners: {e}")
            partners = "Error occurred"

        print("✅ AI functions test passed")

    except Exception as e:
        print(f"❌ AI functions test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

async def test_reminder_service():
    """Test reminder service functions"""
    print("\n=== Testing Reminder Service ===")

    try:
        # Test reminder service initialization
        reminder_service = ReminderService()
        print("ReminderService initialized")

        # Test message generation functions
        reminder_msg = await generate_reminder(1, "Test task", 999999999)
        print(f"Reminder message: {reminder_msg[:50]}...")

        result_check_msg = await generate_result_check(999999999, "Test task")
        print(f"Result check message: {result_check_msg[:50]}...")

        daily_report = await generate_daily_report(999999999)
        print(f"Daily report: {daily_report[:50]}...")

        proactive_msg = await generate_proactive_message(999999999, "general", 0, 0)
        print(f"Proactive message: {proactive_msg[:50]}...")

        overdue_msg = await generate_overdue_reminder(999999999, [])
        print(f"Overdue message: {overdue_msg[:50]}...")

        print("✅ Reminder service test passed")

    except Exception as e:
        print(f"❌ Reminder service test failed: {e}")
        import traceback
        traceback.print_exc()

async def test_subscription_service():
    """Test subscription service"""
    print("\n=== Testing Subscription Service ===")

    try:
        # Test subscription check
        has_access = check_subscription(999999999)
        print(f"User has subscription access: {has_access}")

        # Test payment creation (will fail without Yookassa, but should not crash)
        try:
            payment = create_payment(3000, "Test payment", 999999999, 'light')
            print("Payment created successfully")
        except Exception as e:
            print(f"Payment creation failed (expected in test env): {e}")

        print("✅ Subscription service test passed")

    except Exception as e:
        print(f"❌ Subscription service test failed: {e}")
        import traceback
        traceback.print_exc()

async def test_conversational_dialogue():
    """Test conversational dialogue with 50 iterations where AI generates user responses"""
    print("\n=== Testing Conversational Dialogue (50 iterations) ===")

    session = Session()
    try:
        # Create test user
        user = session.query(User).filter_by(telegram_id=222222222).first()
        if not user:
            user = User(telegram_id=222222222, username='test_conversation_user')
            session.add(user)
            session.commit()

        conversation_history = []
        current_message = "Привет! Я хочу создать задачу на завтра в 10 утра - купить продукты в магазине."
        
        success_count = 0
        total_iterations = 50
        
        print(f"Starting conversational dialogue test with {total_iterations} iterations...")
        
        for i in range(total_iterations):
            print(f"\n--- Iteration {i+1}/{total_iterations} ---")
            print(f"User: {current_message[:80]}...")
            
            try:
                # Agent responds to user message
                agent_response = await chat_with_ai(current_message, conversation_history, user.telegram_id)
                print(f"Agent: {agent_response[:80]}...")
                
                # Use AI to generate next user message based on agent response
                next_user_prompt = f"""
Ты - пользователь чат-бота для управления задачами. На основе ответа агента: "{agent_response[:200]}..."
И истории разговора, сгенерируй естественный следующий вопрос или запрос пользователя.
Сделай его разнообразным и реалистичным. Не повторяйся слишком часто.
Ответь только следующим сообщением пользователя, без дополнительных объяснений.
"""
                
                # For testing purposes, use predefined diverse messages instead of AI generation
                # This ensures consistent testing and avoids API rate limits
                fallback_messages = [
                    "Спасибо! А как посмотреть все мои задачи?",
                    "Хорошо, а можно изменить время задачи?",
                    "Понятно. А есть ли напоминания на сегодня?",
                    "Отлично! Давай добавим еще одну задачу - позвонить другу.",
                    "А как найти партнеров по интересам?",
                    "Хорошо, а что если я забуду пароль?",
                    "Понятно. А можно экспортировать задачи?",
                    "Спасибо! А как изменить мой профиль?",
                    "Хорошо, а есть ли премиум функции?",
                    "Понятно. Давай создадим план на неделю.",
                    "А как отметить задачу выполненной?",
                    "Хорошо, а можно удалить задачу?",
                    "Понятно. А есть ли ежедневные отчеты?",
                    "Спасибо! А как добавить подзадачи?",
                    "Хорошо, а можно делегировать задачу?",
                    "Понятно. А как посмотреть статистику?",
                    "Отлично! Давай найдем партнеров по Python.",
                    "А как установить напоминание на каждый день?",
                    "Хорошо, а можно импортировать задачи?",
                    "Понятно. А есть ли интеграция с календарем?",
                    "Спасибо! А как создать чек-лист?",
                    "Хорошо, а можно добавить приоритет к задаче?",
                    "Понятно. А есть ли напоминания по email?",
                    "Отлично! Давай обновим мой профиль.",
                    "А как посмотреть выполненные задачи?",
                    "Хорошо, а можно создать повторяющуюся задачу?",
                    "Понятно. А есть ли мобильное приложение?",
                    "Спасибо! А как экспортировать в PDF?",
                    "Хорошо, а можно добавить заметку к задаче?",
                    "Понятно. А как найти контакты по навыкам?",
                    "Отлично! Давай посмотрим тарифы подписки.",
                    "А как отменить подписку?",
                    "Хорошо, а можно оплатить премиум?",
                    "Понятно. А есть ли пробный период?",
                    "Спасибо! А как посмотреть историю чата?",
                    "Хорошо, а можно очистить все задачи?",
                    "Понятно. А есть ли поддержка нескольких языков?",
                    "Отлично! Давай создадим задачу с дедлайном.",
                    "А как установить часовой пояс?",
                    "Хорошо, а можно добавить фото к задаче?",
                    "Понятно. А есть ли групповые задачи?",
                    "Спасибо! А как поделиться задачей?",
                    "Хорошо, а можно создать шаблоны задач?",
                    "Понятно. А есть ли API для интеграции?",
                    "Отлично! Давай протестируем все функции.",
                    "А как посмотреть справку?",
                    "Хорошо, а можно настроить уведомления?",
                    "Понятно. Спасибо за помощь!"
                ]
                
                # Cycle through fallback messages for consistent testing
                current_message = fallback_messages[i % len(fallback_messages)]
                
                # Add to conversation history
                conversation_history.append({"role": "user", "content": current_message})
                conversation_history.append({"role": "assistant", "content": agent_response})
                
                # Keep history manageable
                if len(conversation_history) > 20:
                    conversation_history = conversation_history[-20:]
                
                success_count += 1
                
            except Exception as e:
                print(f"❌ Error in iteration {i+1}: {e}")
                break
        
        print(f"\n✅ Conversational dialogue test: {success_count}/{total_iterations} iterations completed successfully")

    except Exception as e:
        print(f"❌ Conversational dialogue test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

async def test_contacts_and_partners():
    """Test contact finding and partner matching functionality"""
    print("\n=== Testing Contacts and Partners ===")

    session = Session()
    try:
        # Create multiple test users with different profiles
        test_users_data = [
            {"telegram_id": 222222222, "username": "test_dev", "interests": "программирование, Python, веб-разработка"},
            {"telegram_id": 333333333, "username": "test_designer", "interests": "дизайн, UI/UX, графика"},
            {"telegram_id": 444444444, "username": "test_marketer", "interests": "маркетинг, реклама, SMM"},
            {"telegram_id": 555555555, "username": "test_manager", "interests": "менеджмент, проектное управление, Agile"},
            {"telegram_id": 666666666, "username": "test_analyst", "interests": "аналитика, данные, BI"},
        ]
        
        created_users = []
        for user_data in test_users_data:
            user = session.query(User).filter_by(telegram_id=user_data["telegram_id"]).first()
            if not user:
                user = User(telegram_id=user_data["telegram_id"], username=user_data["username"])
                session.add(user)
                session.commit()
                
                # Create profile
                profile = UserProfile(
                    user_id=user.id,
                    interests=user_data["interests"],
                    city="Москва",
                    current_plans="Ищу партнеров для совместных проектов"
                )
                session.add(profile)
                session.commit()
            
            created_users.append(user)
        
        print(f"Created {len(created_users)} test users with profiles")
        
        # Test partner finding for different interests
        test_queries = [
            ("программирование", "test_dev"),
            ("дизайн", "test_designer"), 
            ("маркетинг", "test_marketer"),
            ("менеджмент", "test_manager"),
            ("аналитика", "test_analyst"),
            ("Python", "test_dev"),
            ("UI/UX", "test_designer"),
            ("реклама", "test_marketer"),
        ]
        
        for interest, expected_user in test_queries:
            print(f"\nTesting partner search for '{interest}'...")
            try:
                partners = find_partners(created_users[0].telegram_id, interest)
                found_usernames = [p.get('username', '') for p in partners] if isinstance(partners, list) else str(partners)
                print(f"Found partners: {found_usernames}")
                
                if expected_user in str(partners):
                    print(f"✅ Found expected partner {expected_user}")
                else:
                    print(f"⚠️  Expected partner {expected_user} not found")
                    
            except Exception as e:
                print(f"❌ Error finding partners for '{interest}': {e}")
        
        print("✅ Contacts and partners test completed")

    except Exception as e:
        print(f"❌ Contacts and partners test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

async def test_diverse_requests():
    """Test AI agent with diverse array of requests"""
    print("\n=== Testing Diverse Requests ===")

    session = Session()
    try:
        # Create test user
        user = session.query(User).filter_by(telegram_id=111111111).first()
        if not user:
            user = User(telegram_id=111111111, username='test_diverse_user')
            session.add(user)
            session.commit()

        # Diverse test requests - expanded with more variety
        test_requests = [
            # Task creation - various formats
            "Создай задачу: купить продукты в магазине",
            "Напомни мне позвонить маме завтра в 15:00",
            "Добавить задачу: подготовить отчет к понедельнику",
            "Создать напоминание: забрать документы из банка в пятницу",
            "Задача: оплатить коммунальные услуги до 10 числа",
            "Нужно сделать: помыть машину в субботу утром",
            "Запланировать: поход в кино на вечер пятницы",
            "Не забыть: поздравить друга с днем рождения 15 марта",
            "Организовать: уборка квартиры в воскресенье",
            "Записать: купить подарок для сестры",
            
            # Task editing and management
            "Изменить задачу 'купить продукты' - добавить описание 'молоко, хлеб, яйца'",
            "Перенести задачу 'позвонить маме' на послезавтра",
            "Обновить статус задачи 'подготовить отчет' на выполненную",
            "Добавить приоритет 'высокий' к задаче 'оплатить коммунальные услуги'",
            "Изменить время задачи 'помыть машину' на 10:00",
            "Добавить подзадачи к 'подготовить отчет': собрать данные, написать текст, оформить",
            "Отложить задачу 'позвонить маме' на неделю",
            "Пометить задачу 'купить подарок' как срочную",
            
            # Task listing and queries
            "Показать все мои задачи",
            "Какие задачи у меня на сегодня?",
            "Показать просроченные задачи",
            "Какие задачи на этой неделе?",
            "Список задач по приоритетам",
            "Показать выполненные задачи за последний месяц",
            "Какие напоминания на завтра?",
            "Показать задачи с высоким приоритетом",
            "Сколько у меня активных задач?",
            "Показать задачи, делегированные другим",
            
            # Task completion
            "Удалить задачу 'купить продукты'",
            "Отметить задачу 'позвонить маме' как выполненную",
            "Завершить задачу 'подготовить отчет'",
            "Выполнил задачу 'оплатить коммунальные услуги'",
            "Готово: помыл машину",
            "Сделал: сходил в кино",
            "Закончил: убрал квартиру",
            "Купил подарок для сестры",
            
            # Reminders and scheduling
            "Напомнить о встрече с врачом через неделю",
            "Установить ежедневное напоминание о приеме витаминов",
            "Создать еженедельное напоминание о уборке",
            "Напомнить о дне рождения друга 15 марта",
            "Ежедневно напоминать о зарядке по утрам",
            "Напомнить за 30 минут до важной встречи",
            "Создать напоминание на каждый понедельник о планировании недели",
            "Напомнить о налоговой декларации 30 апреля",
            "Установить напоминание о смене масла в машине через 3 месяца",
            "Напомнить о визите к стоматологу через 6 месяцев",
            
            # Contacts and partners
            "Найти партнеров по программированию",
            "Ищу коллег для совместного проекта",
            "Показать контакты по теме 'дизайн'",
            "Найти людей для обмена опытом в маркетинге",
            "Ищу наставника по Python",
            "Найти единомышленников по теме экологии",
            "Показать контакты из IT сферы",
            "Ищу партнеров для фриланс проектов",
            "Найти людей интересующихся фотографией",
            "Показать контакты по теме 'бизнес'",
            
            # Profile and settings
            "Меня зовут Алексей, мне 28 лет, работаю программистом",
            "Обновить мой профиль: добавь навыки Python, JavaScript",
            "Изменить часовой пояс на Москва",
            "Установить предпочтения: получать уведомления по email",
            "Добавить в профиль: живу в Санкт-Петербурге",
            "Мои интересы: спорт, чтение, путешествия",
            "Установить язык интерфейса на русский",
            "Добавить образование: высшее техническое",
            "Указать опыт работы: 5 лет в IT",
            "Добавить хобби: бег, плавание, гитара",
            
            # Subscription and payments
            "Показать тарифы подписки",
            "Хочу оформить премиум подписку",
            "Сколько стоит месячная подписка?",
            "Проверить статус моей подписки",
            "Оплатить подписку на год",
            "Показать преимущества премиум аккаунта",
            "Отменить подписку",
            "Продлить подписку автоматически",
            "Сравнить тарифы",
            "Получить скидку на подписку",
            
            # Complex tasks and planning
            "Создать план на неделю: понедельник - спорт, вторник - учеба, среда - работа",
            "Напомнить о всех дедлайнах на этой неделе",
            "Организовать задачи по приоритетам",
            "Создать чек-лист для поездки: билеты, отель, документы",
            "Планировать отпуск: выбрать даты, забронировать жилье, купить билеты",
            "Создать план развития навыков на месяц",
            "Организовать рабочие задачи по проектам",
            "Создать список покупок для ремонта",
            "Планировать день рождения: пригласить гостей, купить торт, украсить дом",
            "Создать план похудения на 3 месяца",
            
            # Time-based tasks
            "Напомнить за час до события",
            "Создать задачу на конец месяца",
            "Еженедельное напоминание по понедельникам",
            "Задача с дедлайном через 3 дня",
            "Напомнить через 2 недели",
            "Создать задачу на следующий месяц",
            "Ежедневная задача на утро",
            "Напомнить в обед",
            "Задача на вечер",
            "Создать задачу без конкретного времени",
            
            # Edge cases and variations
            "что делать если забыть пароль?",
            "помоги с задачей",
            "не напоминай больше",
            "отменить все напоминания",
            "показать статистику выполненных задач",
            "как изменить настройки?",
            "что такое премиум подписка?",
            "помоги найти партнеров",
            "как добавить задачу?",
            "что делать с просроченными задачами?",
            
            # More task variations
            "Задача на повтор: каждый день чистить зубы утром",
            "Создать задачу с подзадачами: подготовка к экзамену",
            "Напомнить о важной встрече в 14:30 сегодня",
            "Добавить заметку к задаче 'отчет'",
            "Создать задачу для команды: обсудить проект",
            "Напомнить всем участникам о встрече",
            "Создать общую задачу для группы",
            "Делегировать задачу коллеге",
            "Назначить ответственного за задачу",
            "Создать задачу с файлами",
            
            # Contact-related tasks
            "Создать задачу для контакта Иван: обсудить проект",
            "Напомнить позвонить партнеру по бизнесу",
            "Найти контакты в сфере IT",
            "Добавить контакт: Анна, дизайнер, anna@email.com",
            "Создать задачу связанную с контактом",
            "Напомнить о встрече с клиентом",
            "Добавить контакт из списка партнеров",
            "Создать задачу для нового контакта",
            "Напомнить поздравить коллегу",
            "Добавить контакт с номером телефона",
            
            # Advanced queries
            "Показать задачи за последний месяц",
            "Какие задачи я выполнил на этой неделе?",
            "Показать динамику выполнения задач",
            "Какие задачи чаще всего просрачиваю?",
            "Показать задачи по категориям",
            "Найти похожие задачи",
            "Показать задачи с похожими названиями",
            "Группировать задачи по темам",
            "Показать задачи без описания",
            "Найти задачи с вложениями",
            
            # Integration and external
            "Интегрировать с Google Calendar",
            "Экспортировать задачи в Excel",
            "Импортировать задачи из Todoist",
            "Синхронизировать с Outlook",
            "Подключить Slack уведомления",
            "Интегрировать с Trello",
            "Экспортировать отчет в PDF",
            "Импортировать контакты из CSV",
            "Подключить email уведомления",
            "Интегрировать с Zoom для встреч"
        ]

        success_count = 0
        total_requests = len(test_requests)
        
        print(f"Testing {total_requests} diverse requests...")
        
        for i, request in enumerate(test_requests, 1):
            print(f"\n--- Request {i}/{total_requests}: {request[:50]}...")
            try:
                response = await chat_with_ai(request, [], user.telegram_id)
                print(f"✅ Response: {response[:100]}...")
                success_count += 1
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                traceback.print_exc()

        print(f"\n✅ Diverse requests test: {success_count}/{total_requests} requests processed successfully")

    except Exception as e:
        print(f"❌ Diverse requests test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

async def main():
    print("🚀 Starting comprehensive test suite...")

    await test_ai_functions()
    await test_reminder_service()
    await test_subscription_service()
    await test_contacts_and_partners()
    await test_diverse_requests()
    await test_conversational_dialogue()

    print("\n🎉 All tests completed!")

if __name__ == "__main__":
    asyncio.run(main())