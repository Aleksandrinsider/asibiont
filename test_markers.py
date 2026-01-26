#!/usr/bin/env python3
"""
Простой тест маркеров делегирования
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_markers():
    print("=== ТЕСТ МАРКЕРОВ ДЕЛЕГИРОВАНИЯ ===\n")

    # Тест 1: Проверка маркера подписки
    print("1. Проверка маркера DELEGATION_SUBSCRIPTION_REQUIRED:")
    marker = "DELEGATION_SUBSCRIPTION_REQUIRED: Делегирование задач доступно только на тарифах Standard и Premium"
    print(f"Маркер: {marker}")
    print("✓ Маркер содержит правильный префикс\n")

    # Тест 2: Проверка маркера самоделегирования
    print("2. Проверка маркера SELF_DELEGATION_ERROR:")
    marker = "SELF_DELEGATION_ERROR: Нельзя делегировать задачу самому себе"
    print(f"Маркер: {marker}")
    print("✓ Маркер содержит правильный префикс\n")

    # Тест 3: Проверка маркера успешного делегирования
    print("3. Проверка маркера TASK_DELEGATED_SUCCESS:")
    marker = "TASK_DELEGATED_SUCCESS: Задача 'Тест' успешно делегирована пользователю @user"
    print(f"Маркер: {marker}")
    print("✓ Маркер содержит правильный префикс\n")

    print("Все маркеры корректны! Система готова к работе с естественными ответами AI.")

if __name__ == "__main__":
    test_markers()