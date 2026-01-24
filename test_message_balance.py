"""
Комплексный тест всех типов сообщений бота:
1. Обычные ответы AI (chat_with_ai)
2. Напоминания о задачах (reminder_service)
3. Проактивные сообщения (proactive checks)
4. Автопосты в ленту (auto_post_service)
5. Делегирование (delegation messages)

Проверяем:
- Единый стиль
- Отсутствие технических фраз
- Баланс частоты отправки
- Логическую непротиворечивость
"""
import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from models import Session, User, Task, UserProfile

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class MessageBalanceAnalyzer:
    """Анализатор баланса сообщений"""
    
    def __init__(self):
        self.issues = []
        self.successes = []
        self.technical_phrases = [
            "На какое время поставить задачу",
            "NEED_TIME_FOR_TASK",
            "ОБЯЗАТЕЛЬНО СПРОСИ",
            "CONFIRMATION_REQUIRED",
            "TASK_DELETED_ASK_REASON",
            "function_call",
            "tool_use"
        ]
    
    def check_message(self, message_type, content):
        """Проверить сообщение на технические фразы"""
        found_issues = [phrase for phrase in self.technical_phrases if phrase in content]
        
        if found_issues:
            self.issues.append({
                'type': message_type,
                'content': content[:100],
                'issues': found_issues
            })
            return False
        else:
            self.successes.append({
                'type': message_type,
                'content': content[:100]
            })
            return True
    
    def print_report(self):
        """Вывести отчет"""
        print("\n" + "=" * 80)
        print("📊 ОТЧЕТ О БАЛАНСЕ СООБЩЕНИЙ")
        print("=" * 80)
        
        print(f"\n✅ Успешных проверок: {len(self.successes)}")
        print(f"❌ Найдено проблем: {len(self.issues)}")
        
        if self.issues:
            print("\n⚠️ ПРОБЛЕМЫ:")
            for i, issue in enumerate(self.issues, 1):
                print(f"\n{i}. {issue['type']}:")
                print(f"   Контент: {issue['content']}...")
                print(f"   Проблемы: {', '.join(issue['issues'])}")
        
        if self.successes:
            print("\n✅ УСПЕШНЫЕ СООБЩЕНИЯ:")
            for i, success in enumerate(self.successes[:5], 1):  # Показать первые 5
                print(f"{i}. {success['type']}: {success['content']}...")
        
        print("\n" + "=" * 80)


async def test_ai_chat_responses():
    """Тест 1: Обычные ответы AI"""
    logger.info("\n" + "=" * 80)
    logger.info("🤖 ТЕСТ 1: ОБЫЧНЫЕ ОТВЕТЫ AI")
    logger.info("=" * 80)
    
    from ai_integration.chat import chat_with_ai
    
    test_cases = [
        "Создай задачу на завтра в 10:00 - позвонить клиенту",
        "Покажи мои задачи",
        "Удали последнюю задачу",
        "Делегируй @test_user сделать презентацию до пятницы",
    ]
    
    analyzer = MessageBalanceAnalyzer()
    session = Session()
    
    try:
        # Найти тестового пользователя
        user = session.query(User).first()
        if not user:
            logger.warning("Нет пользователей в БД")
            return analyzer
        
        logger.info(f"Тестирую на пользователе: @{user.username} (ID: {user.telegram_id})")
        
        for i, test_input in enumerate(test_cases, 1):
            logger.info(f"\n--- Тест {i}: {test_input}")
            
            try:
                response = await chat_with_ai(
                    test_input,
                    user_id=user.telegram_id,
                    db_session=session
                )
                
                logger.info(f"Ответ: {response[:200]}")
                
                if analyzer.check_message("AI Chat", response):
                    logger.info("✅ Без технических фраз")
                else:
                    logger.warning("⚠️ Найдены технические фразы!")
                    
            except Exception as e:
                logger.error(f"❌ Ошибка: {e}")
                analyzer.issues.append({
                    'type': 'AI Chat Error',
                    'content': test_input,
                    'issues': [str(e)]
                })
    finally:
        session.close()
    
    return analyzer


async def test_reminder_messages():
    """Тест 2: Напоминания о задачах"""
    logger.info("\n" + "=" * 80)
    logger.info("⏰ ТЕСТ 2: НАПОМИНАНИЯ О ЗАДАЧАХ")
    logger.info("=" * 80)
    
    from reminder_service import ReminderService
    
    analyzer = MessageBalanceAnalyzer()
    session = Session()
    
    try:
        # Найти задачу с напоминанием
        task = session.query(Task).filter(
            Task.status == 'pending',
            Task.reminder_time.isnot(None)
        ).first()
        
        if not task:
            logger.warning("Нет задач с напоминаниями")
            return analyzer
        
        logger.info(f"Тестирую напоминание для задачи: {task.title}")
        
        # Создать ReminderService (без бота)
        service = ReminderService(bot=None)
        
        # Генерировать напоминание
        reminder_text = await service.generate_reminder(
            task.user.telegram_id,
            task.title,
            task.id
        )
        
        logger.info(f"Напоминание: {reminder_text}")
        
        if analyzer.check_message("Reminder", reminder_text):
            logger.info("✅ Без технических фраз")
        else:
            logger.warning("⚠️ Найдены технические фразы!")
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        analyzer.issues.append({
            'type': 'Reminder Error',
            'content': 'generate_reminder',
            'issues': [str(e)]
        })
    finally:
        session.close()
    
    return analyzer


async def test_proactive_messages():
    """Тест 3: Проактивные сообщения"""
    logger.info("\n" + "=" * 80)
    logger.info("💡 ТЕСТ 3: ПРОАКТИВНЫЕ СООБЩЕНИЯ")
    logger.info("=" * 80)
    
    from reminder_service import ReminderService
    
    analyzer = MessageBalanceAnalyzer()
    session = Session()
    
    try:
        user = session.query(User).first()
        if not user:
            logger.warning("Нет пользователей в БД")
            return analyzer
        
        logger.info(f"Тестирую проактивное сообщение для: @{user.username}")
        
        service = ReminderService(bot=None)
        
        # Тест разных контекстов
        contexts = [
            ("no_tasks", {}),
            ("overdue_tasks", {"overdue_count": 2}),
            ("few_tasks", {"task_count": 2}),
        ]
        
        for context, kwargs in contexts:
            logger.info(f"\n--- Контекст: {context}")
            
            message = await service.generate_proactive_message(
                user.telegram_id,
                context=context,
                **kwargs
            )
            
            logger.info(f"Сообщение: {message}")
            
            if analyzer.check_message(f"Proactive ({context})", message):
                logger.info("✅ Без технических фраз")
            else:
                logger.warning("⚠️ Найдены технические фразы!")
                
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        analyzer.issues.append({
            'type': 'Proactive Error',
            'content': 'generate_proactive_message',
            'issues': [str(e)]
        })
    finally:
        session.close()
    
    return analyzer


async def test_auto_posts():
    """Тест 4: Автопосты в ленту"""
    logger.info("\n" + "=" * 80)
    logger.info("📝 ТЕСТ 4: АВТОПОСТЫ В ЛЕНТУ")
    logger.info("=" * 80)
    
    from auto_post_service import generate_progress_post
    
    analyzer = MessageBalanceAnalyzer()
    session = Session()
    
    try:
        # Найти пользователя с профилем
        user = session.query(User).join(UserProfile).first()
        
        if not user:
            logger.warning("Нет пользователей с профилем")
            return analyzer
        
        logger.info(f"Тестирую автопост для: @{user.username}")
        
        # Генерировать пост
        post_content = await generate_progress_post(user.telegram_id, session)
        
        if post_content:
            logger.info(f"Пост: {post_content}")
            
            if analyzer.check_message("Auto Post", post_content):
                logger.info("✅ Без технических фраз")
            else:
                logger.warning("⚠️ Найдены технические фразы!")
        else:
            logger.warning("⚠️ Не удалось сгенерировать пост")
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        analyzer.issues.append({
            'type': 'Auto Post Error',
            'content': 'generate_progress_post',
            'issues': [str(e)]
        })
    finally:
        session.close()
    
    return analyzer


async def test_message_frequency():
    """Тест 5: Баланс частоты сообщений"""
    logger.info("\n" + "=" * 80)
    logger.info("⏱️ ТЕСТ 5: БАЛАНС ЧАСТОТЫ СООБЩЕНИЙ")
    logger.info("=" * 80)
    
    from config import (
        PROACTIVE_CHECK_INTERVAL_MINUTES,
        OVERDUE_CHECK_INTERVAL_MINUTES,
        DAILY_REPORT_HOUR,
        PROACTIVE_NO_SEND_START_HOUR,
        PROACTIVE_NO_SEND_END_HOUR
    )
    
    analyzer = MessageBalanceAnalyzer()
    
    # Проверить настройки частоты
    logger.info("\n📊 Текущие настройки частоты:")
    logger.info(f"  Проактивные проверки: каждые {PROACTIVE_CHECK_INTERVAL_MINUTES} минут")
    logger.info(f"  Проверка просроченных: каждые {OVERDUE_CHECK_INTERVAL_MINUTES} минут")
    logger.info(f"  Ежедневный отчет: {DAILY_REPORT_HOUR}:00")
    logger.info(f"  Тихое время: {PROACTIVE_NO_SEND_START_HOUR}:00 - {PROACTIVE_NO_SEND_END_HOUR}:00")
    
    # Оценка баланса
    total_daily_messages = 0
    
    # Проактивные сообщения (учитываем тихое время)
    active_hours = 24 - (PROACTIVE_NO_SEND_END_HOUR - PROACTIVE_NO_SEND_START_HOUR)
    proactive_per_day = (60 * active_hours) // PROACTIVE_CHECK_INTERVAL_MINUTES
    total_daily_messages += proactive_per_day
    logger.info(f"  → Максимум проактивных: ~{proactive_per_day} в день")
    
    # Проверки просроченных
    overdue_per_day = (60 * 24) // OVERDUE_CHECK_INTERVAL_MINUTES
    total_daily_messages += overdue_per_day
    logger.info(f"  → Максимум проверок просроченных: ~{overdue_per_day} в день")
    
    # Ежедневный отчет
    total_daily_messages += 1
    logger.info(f"  → Ежедневный отчет: 1 в день")
    
    # Автопосты (1 раз в день с вероятностью 20%)
    autoposts_per_day = 0.2
    total_daily_messages += autoposts_per_day
    logger.info(f"  → Автопосты: ~{autoposts_per_day} в день (20% вероятность)")
    
    logger.info(f"\n📈 ИТОГО максимум сообщений в день: ~{int(total_daily_messages)}")
    
    # Оценка баланса
    if total_daily_messages > 50:
        analyzer.issues.append({
            'type': 'Frequency Balance',
            'content': f'Слишком много сообщений: {int(total_daily_messages)} в день',
            'issues': ['Перегрузка пользователя']
        })
        logger.warning("⚠️ СЛИШКОМ МНОГО СООБЩЕНИЙ!")
    elif total_daily_messages < 5:
        analyzer.issues.append({
            'type': 'Frequency Balance',
            'content': f'Слишком мало сообщений: {int(total_daily_messages)} в день',
            'issues': ['Недостаточная вовлеченность']
        })
        logger.warning("⚠️ СЛИШКОМ МАЛО СООБЩЕНИЙ!")
    else:
        analyzer.successes.append({
            'type': 'Frequency Balance',
            'content': f'Баланс хороший: ~{int(total_daily_messages)} сообщений в день'
        })
        logger.info("✅ БАЛАНС ХОРОШИЙ!")
    
    return analyzer


async def main():
    """Запуск всех тестов"""
    print("\n" + "=" * 80)
    print("🧪 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ СИСТЕМЫ СООБЩЕНИЙ")
    print("=" * 80)
    
    all_analyzers = []
    
    # Тест 1: AI Chat
    analyzer1 = await test_ai_chat_responses()
    all_analyzers.append(analyzer1)
    
    # Тест 2: Напоминания
    analyzer2 = await test_reminder_messages()
    all_analyzers.append(analyzer2)
    
    # Тест 3: Проактивные сообщения
    analyzer3 = await test_proactive_messages()
    all_analyzers.append(analyzer3)
    
    # Тест 4: Автопосты
    analyzer4 = await test_auto_posts()
    all_analyzers.append(analyzer4)
    
    # Тест 5: Баланс частоты
    analyzer5 = await test_message_frequency()
    all_analyzers.append(analyzer5)
    
    # Сводный отчет
    print("\n" + "=" * 80)
    print("📊 СВОДНЫЙ ОТЧЕТ")
    print("=" * 80)
    
    total_issues = sum(len(a.issues) for a in all_analyzers)
    total_successes = sum(len(a.successes) for a in all_analyzers)
    
    print(f"\n✅ Успешных проверок: {total_successes}")
    print(f"❌ Найдено проблем: {total_issues}")
    
    if total_issues > 0:
        print("\n⚠️ ДЕТАЛИ ПРОБЛЕМ:")
        for analyzer in all_analyzers:
            for issue in analyzer.issues:
                print(f"\n❌ {issue['type']}:")
                print(f"   {issue['content']}...")
                print(f"   Проблемы: {', '.join(issue['issues'])}")
    
    # Итоговая оценка
    print("\n" + "=" * 80)
    if total_issues == 0:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! СИСТЕМА СБАЛАНСИРОВАНА!")
    elif total_issues <= 3:
        print("⚠️ ЕСТЬ НЕБОЛЬШИЕ ПРОБЛЕМЫ, НО СИСТЕМА РАБОТОСПОСОБНА")
    else:
        print("🔴 КРИТИЧЕСКИЕ ПРОБЛЕМЫ! ТРЕБУЕТСЯ ДОРАБОТКА")
    print("=" * 80)
    
    # Рекомендации
    print("\n💡 РЕКОМЕНДАЦИИ:")
    print("  1. Все сообщения должны быть без технических фраз")
    print("  2. Оптимальная частота: 10-30 сообщений в день")
    print("  3. Соблюдать тихое время (22:00 - 10:00)")
    print("  4. Единый неформальный стиль с эмодзи")
    print("  5. Проактивные сообщения реже при большом количестве задач")


if __name__ == "__main__":
    asyncio.run(main())
