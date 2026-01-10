"""
Тест различных формулировок запросов к AI-агенту
Проверяет, что агент может обработать любые типы запросов
"""
import asyncio
from ai_integration import chat_with_ai
from models import Session, User, Task, UserProfile
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_various_requests():
    """Тестируем различные формулировки запросов"""

    # Создаем тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(telegram_id=123456789, username="testuser")
        session.add(user)
        session.commit()

    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id, city="Москва", skills="Python, AI", interests="технологии, стартапы")
        session.add(profile)
        session.commit()

    session.close()

    # Различные типы запросов
    test_requests = [
        # 1. Простые приветствия
        "привет",
        "здравствуйте",
        "добрый день",
        "хай",

        # 2. Создание задач - разные формулировки
        "создай задачу: позвонить маме завтра в 10 утра",
        "добавь задачу позвонить другу",
        "напомни мне купить продукты",
        "нужно сделать отчет к пятнице",
        "запланируй встречу с клиентом на понедельник",

        # 3. Завершение задач - разные формулировки
        "я сделал задачу позвонить маме",
        "завершил задачу купить продукты",
        "отметил как выполненное: сделать отчет",
        "готово: позвонить другу",

        # 4. Делегирование задач
        "@testuser сделай отчет по продажам",
        "поручи @testuser проверить код",
        "передай задачу @testuser: обновить сайт",

        # 5. Обновление профиля
        "я живу в Санкт-Петербурге",
        "мои навыки: Python, JavaScript, React",
        "интересуюсь машинным обучением",
        "работаю в IT компании",

        # 6. Поиск партнеров
        "найди людей для проекта",
        "ищу партнера по разработке",
        "нужны люди с навыками дизайна",

        # 7. Просмотр задач
        "покажи мои задачи",
        "какие у меня дела",
        "список задач",

        # 8. Удаление задач
        "удали все задачи",
        "очисти список дел",

        # 9. Сложные запросы
        "создай задачу на завтра в 15:00: подготовить презентацию, и найди партнера для помощи",
        "я живу в Москве, работаю программистом, интересуюсь AI, создай задачу изучить новый фреймворк",

        # 10. Негативные/ошибочные запросы
        "абракадабра",
        "что ты умеешь?",
        "расскажи о себе",

        # 11. Вопросы о времени
        "который час?",
        "сколько времени?",

        # 12. Запросы с опечатками
        "создай задчу позвонти другу",
        "добавь залачу купит хлеба",

        # 13. Длинные запросы
        "Мне нужно создать несколько задач: во-первых, позвонить клиенту завтра утром, во-вторых, подготовить отчет к вечеру, и в-третьих, найти партнера для совместного проекта. Также хочу обновить свой профиль - я живу в Москве, работаю разработчиком, интересуюсь машинным обучением и стартапами.",

        # 14. Запросы с эмодзи
        "создай задачу 📅 сходить в кино 🎬",
        "завершил задачу ✅ купить подарок 🎁",

        # 15. Запросы на разных языках
        "create task: call mom tomorrow",
        "add task купить хлеб",
        "завершить задачу buy groceries",

        # 16. Запросы с числами и датами
        "напомни 15 числа сдать отчет",
        "создай задачу на 2026-01-15 в 14:00",
    ]

    print("🧪 ТЕСТИРОВАНИЕ РАЗЛИЧНЫХ ФОРМУЛИРОВОК ЗАПРОСОВ")
    print("=" * 60)

    results = []

    for i, request in enumerate(test_requests, 1):
        print(f"\n📝 Тест {i}/{len(test_requests)}: '{request[:50]}{'...' if len(request) > 50 else ''}'")

        try:
            # Получаем контекст из Redis (если есть)
            context = []  # Для простоты теста используем пустой контекст

            response = await chat_with_ai(request, context, user.telegram_id)

            if response and response.strip():
                print(f"✅ УСПЕХ: Получен ответ ({len(response)} символов)")
                print(f"   Ответ: {response[:100]}{'...' if len(response) > 100 else ''}")
                results.append((request, "SUCCESS", len(response)))
            else:
                print("❌ ПУСТОЙ ОТВЕТ")
                results.append((request, "EMPTY", 0))

        except Exception as e:
            print(f"❌ ОШИБКА: {str(e)}")
            results.append((request, "ERROR", str(e)))

    # Статистика результатов
    print("\n" + "=" * 60)
    print("📊 СТАТИСТИКА РЕЗУЛЬТАТОВ:")

    success_count = sum(1 for _, status, _ in results if status == "SUCCESS")
    empty_count = sum(1 for _, status, _ in results if status == "EMPTY")
    error_count = sum(1 for _, status, _ in results if status == "ERROR")

    total = len(results)
    print(f"Всего тестов: {total}")
    print(f"Успешных: {success_count} ({success_count/total*100:.1f}%)")
    print(f"Пустых ответов: {empty_count} ({empty_count/total*100:.1f}%)")
    print(f"Ошибок: {error_count} ({error_count/total*100:.1f}%)")

    if empty_count > 0 or error_count > 0:
        print("\n❌ ПРОБЛЕМНЫЕ ЗАПРОСЫ:")
        for request, status, detail in results:
            if status != "SUCCESS":
                print(f"   {status}: {request[:50]}{'...' if len(request) > 50 else ''}")

    print("\n🏁 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")

    return results

if __name__ == "__main__":
    asyncio.run(test_various_requests())