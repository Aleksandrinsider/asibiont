#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тест проактивности агента - меньше вопросов, больше действий
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
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_proactive_agent():
    """Тестирует проактивность агента - должен предлагать действия вместо вопросов"""
    print("🚀 ТЕСТ: Проактивность агента")
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
        
        # Проблемные сценарии - где агент раньше задавал много вопросов
        test_scenarios = [
            {
                "message": "нужно привлечь первых пользователей для теста агента ии",
                "description": "Должен предложить создать задачу + найти партнеров",
                "expected_actions": ["создать задачу", "find_relevant_contacts", "предложить решение"],
                "avoid": ["какой вариант", "что больше подходит", "хочешь попробую"]
            },
            {
                "message": "надо найти дизайнера для проекта", 
                "description": "Должен сразу найти дизайнеров",
                "expected_actions": ["find_partners", "создать задачу"],
                "avoid": ["какой вариант", "варианты решения"]
            },
            {
                "message": "хочу запустить стартап в сфере ии",
                "description": "Должен предложить задачу + найти экспертов",
                "expected_actions": ["создать задачу", "find_relevant_contacts"],
                "avoid": ["спроси", "уточни", "какие варианты"]
            }
        ]
        
        results = []
        
        for i, scenario in enumerate(test_scenarios, 1):
            print(f"📝 Тест {i}/{len(test_scenarios)}: {scenario['description']}")
            print(f"   Сообщение: '{scenario['message']}'")
            
            try:
                # Отправляем сообщение агенту
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
                
                # Анализируем ответ
                response_lower = response_text.lower()
                
                # Проверяем наличие ожидаемых действий
                action_score = 0
                for action in scenario['expected_actions']:
                    if any(keyword in response_lower for keyword in [
                        "создать задач", "найти", "подобрать", "поищу", 
                        "нашел", "могу найти", "предлагаю"
                    ]):
                        action_score += 1
                
                # Проверяем отсутствие избыточных вопросов
                question_score = 0 
                for avoid_phrase in scenario['avoid']:
                    if avoid_phrase.lower() in response_lower:
                        question_score += 1
                
                # Подсчитываем общее количество вопросительных знаков
                question_marks = response_text.count('?')
                
                # Оцениваем проактивность
                proactive_score = action_score * 2 - question_score - min(question_marks, 3)
                
                result = {
                    "scenario": i,
                    "action_score": action_score,
                    "question_score": question_score,
                    "question_marks": question_marks,
                    "proactive_score": proactive_score,
                    "response_length": len(response_text)
                }
                
                results.append(result)
                
                # Показываем анализ
                if proactive_score >= 2:
                    print("   🚀 ОТЛИЧНО: Проактивный ответ")
                elif proactive_score >= 0:
                    print("   ⚠️  СРЕДНЕ: Есть действия, но много вопросов")
                else:
                    print("   ❌ ПЛОХО: Слишком много вопросов, мало действий")
                
                print(f"   📊 Действия: {action_score}, Лишние вопросы: {question_score}, Знаков '?': {question_marks}")
                
                # Показываем превью ответа
                preview = response_text[:200] + "..." if len(response_text) > 200 else response_text
                print(f"   Превью: {preview}")
                
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
                results.append({"scenario": i, "error": str(e)})
            
            print("-" * 50)
            print()
        
        # Итоговый анализ
        valid_results = [r for r in results if 'error' not in r]
        
        if valid_results:
            avg_proactive_score = sum(r['proactive_score'] for r in valid_results) / len(valid_results)
            avg_questions = sum(r['question_marks'] for r in valid_results) / len(valid_results)
            
            print("🎯 ИТОГОВЫЙ АНАЛИЗ ПРОАКТИВНОСТИ:")
            print(f"   📈 Средний балл проактивности: {avg_proactive_score:.1f}")
            print(f"   ❓ Среднее количество вопросов: {avg_questions:.1f}")
            print(f"   ✅ Тестов пройдено: {len(valid_results)}/{len(test_scenarios)}")
            
            if avg_proactive_score >= 2:
                print("   🚀 РЕЗУЛЬТАТ: Агент стал ПРОАКТИВНЫМ!")
            elif avg_proactive_score >= 0:
                print("   ⚠️  РЕЗУЛЬТАТ: Агент частично проактивный")  
            else:
                print("   ❌ РЕЗУЛЬТАТ: Агент все еще слишком пассивный")
            
            print()
            print("🔧 ИЗМЕНЕНИЯ:")
            print("   - Убрано 'предлагай 2-3 варианта'")
            print("   - Убрано 'задавай уточняющие вопросы'") 
            print("   - Добавлено 'ДЕЙСТВУЙ вместо вопросов'")
            print("   - Добавлено автопредложение создания задач")
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_proactive_agent())