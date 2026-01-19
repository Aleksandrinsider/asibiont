#!/usr/bin/env python3
"""
Расширенное тестирование агента с ИИ-симуляцией пользователя
Использует DeepSeek для генерации реалистичных пользовательских запросов
"""

import asyncio
import aiohttp
import json
import logging
import sys
import os
from datetime import datetime, timezone
import pytz

# Устанавливаем FREE_ACCESS_MODE для тестирования
os.environ["FREE_ACCESS_MODE"] = "1"

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEEPSEEK_API_KEY
from ai_integration import chat_with_ai, set_redis_client
from models import Session, User, Task, UserProfile, init_db
from ai_integration.memory import decrypt_data
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AISimulator:
    """ИИ-симулятор для генерации реалистичных пользовательских запросов"""

    def __init__(self):
        self.url = "https://api.deepseek.com/v1/chat/completions"
        self.headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

    async def generate_user_message(self, context_history, scenario_type="general", user_profile=None):
        """Генерирует реалистичное сообщение пользователя на основе контекста"""

        system_prompt = f"""Ты - обычный пользователь Telegram бота для управления задачами.
Генерируй реалистичные сообщения как настоящий человек.

ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:
{user_profile or "Обычный пользователь, менеджер в IT компании"}

ТИП СЦЕНАРИЯ: {scenario_type}

ПРАВИЛА ГЕНЕРАЦИИ:
- Пиши естественно, как в реальном чате
- Используй разговорный стиль с опечатками и сокращениями
- Задавай уточняющие вопросы если нужно
- Проявляй эмоции и личность
- Не используй формальный язык
- Добавляй детали из реальной жизни
- Варьируй длину сообщений (короткие и длинные)
- Используй эмодзи иногда
- Не повторяй предыдущие сообщения

КОНТЕКСТ ПРЕДЫДУЩИХ СООБЩЕНИЙ:
{context_history}

Сгенерируй ОДНО новое сообщение пользователя в ответ на последний ответ бота.
Верни только текст сообщения, без кавычек и объяснений."""

        messages = [{"role": "system", "content": system_prompt}]

        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.9,
            "max_tokens": 150
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.url, headers=self.headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    message = result["choices"][0]["message"]["content"].strip()
                    # Убираем кавычки если они есть
                    message = message.strip('"').strip("'")
                    return message
                else:
                    logger.error(f"Failed to generate user message: {response.status}")
                    return "Привет, что умеешь делать?"

async def run_ai_simulation_test():
    """Запуск расширенного тестирования с ИИ-симуляцией"""

    logger.info("🚀 Начинаем расширенное тестирование агента с ИИ-симуляцией")

    # Инициализация Redis (или None для локального режима)
    try:
        redis_client = redis.Redis.from_url("redis://localhost:6379", decode_responses=False)
        await redis_client.ping()  # Проверяем подключение
        logger.info("✅ Redis подключен")
    except Exception as e:
        logger.warning(f"Redis недоступен: {e}, используем локальный режим")
        redis_client = None

    set_redis_client(redis_client)

    # Создаем тестового пользователя
    session = Session()
    test_user_id = 999999
    test_user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not test_user:
        test_user = User(telegram_id=test_user_id, username="test_ai_user")
        session.add(test_user)
        session.commit()

        # Создаем профиль
        profile = UserProfile(
            user_id=test_user.id,
            city="Москва",
            company="TechCorp",
            position="Менеджер проектов",
            interests="IT, управление проектами, спорт",
            skills="Agile, Scrum, Python",
            goals="Карьерный рост, саморазвитие"
        )
        session.add(profile)
        session.commit()

    session.close()

    # Инициализация симулятора
    simulator = AISimulator()

    # Сценарии тестирования
    scenarios = [
        {
            "name": "Создание задач",
            "initial_message": "Привет! Помоги мне создать задачу на завтра в 10 утра - нужно подготовить презентацию для команды",
            "expected_tools": ["add_task"]
        },
        {
            "name": "Управление задачами",
            "initial_message": "Покажи мои активные задачи",
            "expected_tools": ["list_tasks"]
        },
        {
            "name": "Обновление профиля",
            "initial_message": "Обнови мой профиль - я теперь работаю в Google как senior developer",
            "expected_tools": ["update_profile"]
        },
        {
            "name": "Поиск партнеров",
            "initial_message": "Найди мне коллег по интересам в области Python разработки",
            "expected_tools": ["find_partners"]
        },
        {
            "name": "Советы и общение",
            "initial_message": "Дай совет как лучше организовать время на работе",
            "expected_tools": []  # Только разговор
        },
        {
            "name": "Делегирование",
            "initial_message": "Хочу делегировать задачу по код-ревью коллеге @testuser",
            "expected_tools": ["delegate_task"]
        }
    ]

    results = []

    for scenario in scenarios:
        logger.info(f"📋 Тестируем сценарий: {scenario['name']}")

        # Очищаем контекст
        if redis_client:
            await redis_client.delete(f"context:{test_user_id}")

        # Начальное сообщение
        context = []
        user_message = scenario['initial_message']

        conversation = []
        max_turns = 5  # Максимум 5 ходов в диалоге

        for turn in range(max_turns):
            logger.info(f"🔄 Ход {turn + 1}: Пользователь -> '{user_message[:50]}...'")

            # Получаем ответ агента
            try:
                agent_response = await chat_with_ai(
                    message=user_message,
                    context=context,
                    user_id=test_user_id,
                    file_content=None,
                    db_session=None
                )

                logger.info(f"🤖 Агент ответил: '{agent_response[:50]}...'")

                # Сохраняем в контексте
                context.append({"user": user_message, "agent": agent_response})
                conversation.append({"user": user_message, "agent": agent_response})

                # Проверяем на завершение диалога
                if any(phrase in agent_response.lower() for phrase in [
                    "до свидания", "пока", "увидимся", "готово", "выполнено"
                ]) and turn >= 2:
                    logger.info("💬 Диалог завершен естественным образом")
                    break

                # Генерируем следующий пользовательский запрос через ИИ
                if turn < max_turns - 1:
                    context_history = "\n".join([
                        f"Пользователь: {msg['user']}\nАгент: {msg['agent']}"
                        for msg in conversation[-3:]  # Последние 3 сообщения
                    ])

                    user_profile = "Менеджер проектов в IT компании, интересуется Python и управлением временем"
                    user_message = await simulator.generate_user_message(
                        context_history,
                        scenario['name'],
                        user_profile
                    )

                    if not user_message or user_message in [msg['user'] for msg in conversation[-2:]]:  # Проверяем на повтор с предыдущими
                        logger.info("🔄 ИИ сгенерировал повторяющееся сообщение, завершаем диалог")
                        break

            except Exception as e:
                logger.error(f"❌ Ошибка в ходе {turn + 1}: {e}")
                break

        # Анализируем результаты сценария
        scenario_result = {
            "scenario": scenario['name'],
            "conversation": conversation,
            "turns": len(conversation),
            "success": len(conversation) > 1,  # Минимум 2 сообщения
            "errors": []
        }

        # Проверяем использование ожидаемых инструментов
        # (Здесь можно добавить более детальный анализ)

        results.append(scenario_result)
        logger.info(f"✅ Сценарий '{scenario['name']}' завершен: {len(conversation)} ходов")

    # Финальный отчет
    logger.info("\n" + "="*50)
    logger.info("📊 РЕЗУЛЬТАТЫ РАСШИРЕННОГО ТЕСТИРОВАНИЯ")
    logger.info("="*50)

    successful_scenarios = 0
    total_turns = 0

    for result in results:
        status = "✅" if result['success'] else "❌"
        logger.info(f"{status} {result['scenario']}: {result['turns']} ходов")

        if result['success']:
            successful_scenarios += 1
        total_turns += result['turns']

        # Показываем краткую беседу
        if result['conversation']:
            logger.info("   Пример диалога:")
            for i, msg in enumerate(result['conversation'][:2]):  # Первые 2 сообщения
                user_text = msg['user'][:40] + "..." if len(msg['user']) > 40 else msg['user']
                agent_text = msg['agent'][:40] + "..." if len(msg['agent']) > 40 else msg['agent']
                logger.info(f"   П{i+1}: {user_text}")
                logger.info(f"   А{i+1}: {agent_text}")

    logger.info(f"\n🎯 ИТОГО: {successful_scenarios}/{len(results)} сценариев успешны")
    logger.info(f"💬 Средняя длина диалога: {total_turns/len(results):.1f} ходов")

    # Оценка качества
    quality_score = (successful_scenarios / len(results)) * 100
    if quality_score >= 90:
        logger.info(f"🏆 ОЦЕНКА: Отлично ({quality_score:.1f}%)")
    elif quality_score >= 75:
        logger.info(f"👍 ОЦЕНКА: Хорошо ({quality_score:.1f}%)")
    elif quality_score >= 60:
        logger.info(f"🤔 ОЦЕНКА: Удовлетворительно ({quality_score:.1f}%)")
    else:
        logger.info(f"⚠️ ОЦЕНКА: Требует улучшения ({quality_score:.1f}%)")

    # Сохраняем детальный отчет
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "summary": {
            "successful_scenarios": successful_scenarios,
            "total_scenarios": len(results),
            "average_turns": total_turns/len(results),
            "quality_score": quality_score
        }
    }

    with open("ai_simulation_test_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("📄 Детальный отчет сохранен в ai_simulation_test_report.json")

if __name__ == "__main__":
    asyncio.run(run_ai_simulation_test())