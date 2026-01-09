import asyncio
import logging
import os
from ai_integration import chat_with_ai, set_redis_client
from redis.asyncio import Redis
from config import REDIS_URL, FREE_ACCESS_MODE
import json

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def simulate_dialogue():
    """Симуляция полного диалога с агентом для тестирования всех функций"""

    # Инициализация Redis (если доступен)
    try:
        if REDIS_URL:
            redis_client = Redis.from_url(REDIS_URL)
            set_redis_client(redis_client)
        else:
            set_redis_client(None)
    except Exception as e:
        logger.warning(f"Redis not available: {e}")
        set_redis_client(None)

    # Тестовый user_id (можно использовать существующий или создать новый)
    test_user_id = 123456789  # Тестовый ID

    # Включаем бесплатный доступ для тестирования
    os.environ['FREE_ACCESS_MODE'] = 'true'

    # Сценарии тестирования
    test_scenarios = [
        {
            "name": "Приветствие и знакомство",
            "messages": [
                "Привет! Я новый пользователь, расскажи что ты умеешь",
                "Расскажи подробнее про управление задачами",
                "А что насчет поиска партнеров?"
            ]
        },
        {
            "name": "Создание и управление задачами",
            "messages": [
                "Создай задачу: купить продукты в магазине завтра в 10 утра",
                "Добавь еще задачу: позвонить маме в субботу",
                "Покажи все мои задачи",
                "Изменить задачу 'купить продукты' - добавить 'и молоко с хлебом'",
                "Отметь задачу 'купить продукты' как выполненную"
            ]
        },
        {
            "name": "Поиск партнеров",
            "messages": [
                "Обнови мой профиль: я из Москвы, работаю программистом в IT компании, интересуюсь Python и AI",
                "Найди мне партнеров по интересам",
                "Расскажи подробнее про @testuser если он есть"
            ]
        },
        {
            "name": "Делегирование задач",
            "messages": [
                "Создай задачу для делегирования: разработать логотип для проекта",
                "Поручи эту задачу пользователю @partner1",
                "Проверь статус делегированных задач"
            ]
        },
        {
            "name": "Анализ и советы",
            "messages": [
                "У меня проблемы с мотивацией, дай советы",
                "Проанализируй мои задачи и скажи что можно улучшить"
            ]
        },
        {
            "name": "Обработка ошибок",
            "messages": [
                "создай задачу без текста",
                "покажи задачу с ID 999999",
                "обнови профиль с некорректными данными"
            ]
        },
        {
            "name": "Управление подпиской",
            "messages": [
                "Проверь статус моей подписки",
                "Создай платеж для подписки",
                "Отмени мою подписку"
            ]
        }
    ]

    print("🚀 НАЧИНАЕМ ПОЛНОЕ ТЕСТИРОВАНИЕ АГЕНТА")
    print("=" * 60)

    for scenario in test_scenarios:
        print(f"\n📋 СЦЕНАРИЙ: {scenario['name']}")
        print("-" * 40)

        context = []  # Контекст диалога

        for i, message in enumerate(scenario['messages'], 1):
            print(f"\n👤 ПОЛЬЗОВАТЕЛЬ {i}: {message}")

            try:
                # Вызываем агента
                response = await chat_with_ai(
                    message=message,
                    context=context.copy(),  # Копируем контекст
                    user_id=test_user_id
                )

                print(f"🤖 АГЕНТ {i}: {response}")

                # Добавляем в контекст для продолжения диалога
                context.append({"role": "user", "content": message})
                context.append({"role": "assistant", "content": response})

                # Ограничиваем контекст последними 10 сообщениями
                if len(context) > 20:
                    context = context[-20:]

            except Exception as e:
                print(f"❌ ОШИБКА в сценарии '{scenario['name']}', сообщение {i}: {e}")
                logger.error(f"Test error: {e}", exc_info=True)

        print(f"\n✅ СЦЕНАРИЙ '{scenario['name']}' ЗАВЕРШЕН")

    print("\n" + "=" * 60)
    print("🎉 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО!")
    print("Проверьте логи выше на наличие ошибок и корректность ответов агента.")

if __name__ == "__main__":
    asyncio.run(simulate_dialogue())