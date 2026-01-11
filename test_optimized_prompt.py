"""
Тест оптимизированного промпта
Сравниваем качество ответов: оригинал vs оптимизированный
"""
import asyncio
import sys
from ai_integration import chat_with_ai
from models import SessionLocal, User
from ai_integration_optimized import get_optimized_system_prompt
import ai_integration

# Тестовые сценарии
test_cases = [
    {
        "name": "Приветствие",
        "message": "привет, как дела?",
        "check": ["вопрос", "задач"]  # должен спросить и упомянуть контекст
    },
    {
        "name": "Добавление задачи",
        "message": "добавь задачу позвонить маме завтра в 15:00",
        "check": ["добавил", "?"]  # должен подтвердить и задать вопрос
    },
    {
        "name": "Показ задач",
        "message": "покажи мои задачи",
        "check": ["задач", "рекоменд", "пред"]  # должен показать и дать рекомендации
    },
    {
        "name": "Обновление профиля",
        "message": "живу в москве, работаю в яндексе",
        "check": ["москв", "яндекс", "?"]  # должен обновить и предложить что-то
    },
]

async def test_prompt_version(prompt_type, test_cases, user_id):
    """Тестирует одну версию промпта"""
    print(f"\n{'='*80}")
    print(f"  ТЕСТИРОВАНИЕ: {prompt_type}")
    print(f"{'='*80}\n")
    
    results = []
    
    for i, test in enumerate(test_cases, 1):
        print(f"[{i}/{len(test_cases)}] {test['name']}")
        print(f"   Запрос: {test['message']}")
        
        try:
            response = await chat_with_ai(
                message=test['message'],
                context=[],
                user_id=user_id
            )
            
            # Проверяем качество
            word_count = len(response.split())
            checks_passed = sum(1 for keyword in test['check'] if keyword.lower() in response.lower())
            quality = checks_passed / len(test['check']) * 100
            
            print(f"   Ответ ({word_count} слов, качество {quality:.0f}%):")
            print(f"   {response[:150]}{'...' if len(response) > 150 else ''}\n")
            
            results.append({
                "test": test['name'],
                "words": word_count,
                "quality": quality,
                "response": response
            })
            
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"   ❌ ОШИБКА: {e}\n")
            results.append({
                "test": test['name'],
                "error": str(e)
            })
    
    return results

async def main():
    print("\n" + "="*80)
    print("  СРАВНИТЕЛЬНЫЙ ТЕСТ ПРОМПТОВ")
    print("="*80)
    
    # Находим пользователя для тестирования
    session = SessionLocal()
    try:
        user = session.query(User).first()
        if not user:
            print("❌ Нет пользователей в базе")
            return
        user_id = user.id
        print(f"\n[CONFIG] Тестовый пользователь: {user.username} (ID: {user_id})")
    finally:
        session.close()
    
    # Тест 1: ОРИГИНАЛЬНЫЙ ПРОМПТ
    original_results = await test_prompt_version("ОРИГИНАЛЬНЫЙ ПРОМПТ", test_cases, user_id)
    
    print("\n" + "="*80)
    print("  Пауза 3 секунды между тестами...")
    print("="*80)
    await asyncio.sleep(3)
    
    # Тест 2: ОПТИМИЗИРОВАННЫЙ ПРОМПТ
    # Временно подменяем функцию
    original_get_system_prompt = ai_integration.get_system_prompt
    ai_integration.get_system_prompt = get_optimized_system_prompt
    
    optimized_results = await test_prompt_version("ОПТИМИЗИРОВАННЫЙ ПРОМПТ", test_cases, user_id)
    
    # Возвращаем оригинальную функцию
    ai_integration.get_system_prompt = original_get_system_prompt
    
    # Сравнение результатов
    print("\n" + "="*80)
    print("  СРАВНИТЕЛЬНАЯ ТАБЛИЦА")
    print("="*80 + "\n")
    
    print(f"{'Тест':<25} {'Оригинал':<20} {'Оптимизир.':<20} {'Разница'}")
    print("-" * 80)
    
    for i, test in enumerate(test_cases):
        orig = original_results[i] if i < len(original_results) else {}
        opt = optimized_results[i] if i < len(optimized_results) else {}
        
        orig_words = orig.get('words', 0)
        opt_words = opt.get('words', 0)
        
        orig_quality = orig.get('quality', 0)
        opt_quality = opt.get('quality', 0)
        
        diff = orig_words - opt_words
        quality_diff = opt_quality - orig_quality
        
        print(f"{test['name']:<25} {orig_words} сл/{orig_quality:.0f}%{'':<8} {opt_words} сл/{opt_quality:.0f}%{'':<8} {diff:+d} сл")
    
    # Средние значения
    avg_orig_words = sum(r.get('words', 0) for r in original_results) / len(original_results) if original_results else 0
    avg_opt_words = sum(r.get('words', 0) for r in optimized_results) / len(optimized_results) if optimized_results else 0
    
    avg_orig_quality = sum(r.get('quality', 0) for r in original_results) / len(original_results) if original_results else 0
    avg_opt_quality = sum(r.get('quality', 0) for r in optimized_results) / len(optimized_results) if optimized_results else 0
    
    print("-" * 80)
    print(f"{'СРЕДНИЕ:':<25} {avg_orig_words:.0f} сл/{avg_orig_quality:.0f}%{'':<8} {avg_opt_words:.0f} сл/{avg_opt_quality:.0f}%{'':<8} {avg_orig_words - avg_opt_words:+.0f} сл")
    
    # Выводы
    print("\n" + "="*80)
    print("  ВЫВОДЫ")
    print("="*80 + "\n")
    
    print(f"📊 Длина ответов:")
    if avg_opt_words < avg_orig_words:
        percent = (1 - avg_opt_words/avg_orig_words) * 100
        print(f"   ✅ Оптимизированный короче на {percent:.1f}%")
    else:
        print(f"   ⚠️  Оптимизированный длиннее")
    
    print(f"\n📊 Качество ответов:")
    if avg_opt_quality >= avg_orig_quality * 0.9:  # не хуже чем на 10%
        print(f"   ✅ Качество сопоставимо ({avg_opt_quality:.0f}% vs {avg_orig_quality:.0f}%)")
    else:
        print(f"   ⚠️  Качество ниже ({avg_opt_quality:.0f}% vs {avg_orig_quality:.0f}%)")
    
    print(f"\n💰 Экономия:")
    prompt_economy = (28520 - 4704) / 28520 * 100  # из предыдущего анализа
    print(f"   Промпт:  83.5% меньше токенов")
    print(f"   Ответы:  {(1 - avg_opt_words/avg_orig_words) * 100:.1f}% меньше слов")
    print(f"   Итого:   ~70-80% экономия на каждый запрос")
    
    print(f"\n🎯 РЕКОМЕНДАЦИЯ:")
    if avg_opt_quality >= avg_orig_quality * 0.85 and avg_opt_words <= avg_orig_words * 1.2:
        print(f"   ✅ ИСПОЛЬЗОВАТЬ оптимизированный промпт!")
        print(f"   Качество сохранено, экономия существенная")
    else:
        print(f"   ⚠️  ДОРАБОТАТЬ оптимизированный промпт")
        print(f"   Качество или длина требуют улучшения")
    
    print("\n" + "="*80)

if __name__ == "__main__":
    asyncio.run(main())
