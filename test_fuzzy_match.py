"""
Простой тест fuzzy matching для обнаружения дубликатов
"""
from difflib import SequenceMatcher

def test_similarity():
    test_cases = [
        ("Заказать продукты", "закзать продукты", True),  # Опечатка
        ("Заказать продукты", "Заказать продукты", True),  # Идентичные
        ("Позвонить маме", "Позвонить папе", False),  # Разные
        ("Подготовить отчет", "Подготовить отчёт", True),  # е/ё
        ("Купить молоко", "Купить хлеб", False),  # Разные объекты
    ]
    
    print("=" * 80)
    print("ТЕСТ FUZZY MATCHING ДЛЯ ОБНАРУЖЕНИЯ ДУБЛИКАТОВ")
    print("=" * 80)
    
    for title1, title2, expected_duplicate in test_cases:
        similarity = SequenceMatcher(None, title1.lower(), title2.lower()).ratio()
        is_duplicate = similarity > 0.87  # Повышен порог
        
        status = "✅" if is_duplicate == expected_duplicate else "❌"
        print(f"\n{status} '{title1}' vs '{title2}'")
        print(f"   Схожесть: {similarity:.2f} (порог: 0.87)")
        print(f"   Ожидался дубликат: {expected_duplicate}, Определён как дубликат: {is_duplicate}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    test_similarity()
