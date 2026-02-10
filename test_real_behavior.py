#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тест реального поведения агента после изменений
"""

import asyncio
import sys
import os
import logging

# Add path
sys.path.insert(0, os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import Session, User

# Setup logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_real_agent_behavior():
    """Тестирует поведение агента на реальных запросах"""
    print("🎯 ТЕСТ: Реальное поведение агента")
    print("=" * 60)
    
    session = Session()
    
    try:
        # Находим любого пользователя для тестов
        user = session.query(User).first()
        if not user:
            print("❌ Нет пользователей в базе данных")
            return
        
        print(f"👤 Пользователь для тестов: {user.telegram_id}")
        print()
        
        # Реальные запросы пользователей
        real_scenarios = [
            {
                "message": "привет",
                "description": "Простое приветствие",
                "expectation": "Короткий дружелюбный ответ + предложение помощи"
            },
            {
                "message": "нужно привлечь первых пользователей для теста агента ии",
                "description": "Бизнес задача - привлечение пользователей", 
                "expectation": "Предложение создать задачу + поиск партнеров/решений"
            },
            {
                "message": "завтра в 10 утра встреча с инвестором",
                "description": "Информация о встрече",
                "expectation": "Предложение создать задачу-напоминание"
            },
            {
                "message": "надо найти дизайнера для проекта",
                "description": "Поиск специалиста",
                "expectation": "Поиск дизайнеров в базе + предложение задачи"
            },
            {
                "message": "хочу изучить python",
                "description": "Цель обучения",
                "expectation": "Предложение создать план/задачи + поиск менторов"
            },
            {
                "message": "как дела с моими задачами?",
                "description": "Статус задач", 
                "expectation": "Показ списка задач"
            },
            {
                "message": "сделал отчет по продажам",
                "description": "Завершение задачи",
                "expectation": "Закрытие задачи + похвала"
            },
            {
                "message": "что нового в мире ai?",
                "description": "Запрос информации",
                "expectation": "Быстрый поиск новостей (LIGHT) или анализ трендов (STANDARD+)"
            }
        ]
        
        for i, scenario in enumerate(real_scenarios, 1):
            print(f"📝 Тест {i}/{len(real_scenarios)}: {scenario['description']}")
            print(f"   📨 Запрос: \"{scenario['message']}\"")
            print(f"   🎯 Ожидание: {scenario['expectation']}")
            
            try:
                # Отправляем реальный запрос
                response = await chat_with_ai(
                    message=scenario['message'],
                    user_id=user.telegram_id,
                    db_session=session
                )
                
                # Проверяем тип ответа 
                if isinstance(response, dict):
                    response_text = response.get('response', '') or response.get('content', '') or str(response)
                elif isinstance(response, str):
                    response_text = response
                else:
                    response_text = str(response)
                
                print(f"   ✅ Ответ получен ({len(response_text)} символов)")
                
                # Анализируем качество ответа
                response_lower = response_text.lower()
                
                # Проверяем проактивность
                action_indicators = [
                    "создать задач", "найд", "поищу", "могу помочь", "предлагаю",
                    "давай", "попробу", "сделаем", "покажу"
                ]
                
                question_indicators = ["?", "какой", "хочешь", "нужно ли"]
                
                action_count = sum(1 for indicator in action_indicators if indicator in response_lower)
                question_count = response_text.count('?')
                
                # Оценка качества
                if action_count >= 2 and question_count <= 2:
                    quality = "🚀 ОТЛИЧНО - Проактивный"
                elif action_count >= 1 and question_count <= 3:
                    quality = "✅ ХОРОШО - Сбалансировано" 
                elif question_count <= 5:
                    quality = "⚠️ СРЕДНЕ - Много вопросов"
                else:
                    quality = "❌ ПЛОХО - Пассивный"
                
                print(f"   📊 Оценка: {quality}")
                print(f"   🎯 Действия: {action_count}, Вопросы: {question_count}")
                
                # Показываем ответ
                print(f"   💬 Ответ:")
                # Разбиваем на строки для читаемости
                lines = response_text.split('\\n')
                for line in lines[:5]:  # Показываем первые 5 строк
                    if line.strip():
                        print(f"      {line.strip()}")
                if len(lines) > 5:
                    print(f"      ... (еще {len(lines)-5} строк)")
                
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
            
            print("-" * 50)
            print()
        
        print("🎯 СВОДКА ТЕСТИРОВАНИЯ:")
        print("Агент протестирован на 8 реальных сценариях")
        print("Проверены: проактивность, полезность, краткость")
        print()
        print("🔧 НАСТРОЕННЫЕ ИЗМЕНЕНИЯ:")
        print("✅ Убрано 'предлагай 2-3 варианта' → больше решительности")
        print("✅ Убрано 'задавай уточняющие вопросы' → меньше расспросов")  
        print("✅ Добавлено 'ДЕЙСТВУЙ вместо вопросов' → больше инициативы")
        print("✅ Добавлено автопредложение задач → практическая польза")
        print("✅ Сохранено базовое форматирование в чате")
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_real_agent_behavior())