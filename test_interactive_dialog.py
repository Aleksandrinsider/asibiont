#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Интерактивный тест диалога с агентом
Показывает ответы агента и вызванные функции
"""

import asyncio
import sys
from datetime import datetime
from models import SessionLocal
from ai_integration.chat import chat_with_ai

# Fix console encoding for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# ANSI color codes
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_separator():
    print(f"\n{Colors.CYAN}{'='*80}{Colors.RESET}")

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.HEADER}{text}{Colors.RESET}")

def print_user_message(text):
    print(f"\n{Colors.BLUE}👤 ВЫ:{Colors.RESET} {text}")

def print_agent_message(text):
    print(f"\n{Colors.GREEN}🤖 АГЕНТ:{Colors.RESET} {text}")

def print_function_calls(tool_calls):
    if not tool_calls:
        return
    
    print(f"\n{Colors.YELLOW}⚡ ВЫЗВАННЫЕ ФУНКЦИИ:{Colors.RESET}")
    for i, call in enumerate(tool_calls, 1):
        func_name = call.get('function', {}).get('name', 'unknown')
        try:
            import json
            args = json.loads(call.get('function', {}).get('arguments', '{}'))
            args_str = json.dumps(args, ensure_ascii=False, indent=2)
        except:
            args_str = call.get('function', {}).get('arguments', '{}')
        
        print(f"{Colors.YELLOW}  {i}. {func_name}{Colors.RESET}")
        print(f"{Colors.CYAN}     Параметры: {args_str}{Colors.RESET}")

async def send_message(user_message, user_id=99999):
    """Отправляем сообщение агенту"""
    try:
        session_db = SessionLocal()
        try:
            result = await chat_with_ai(
                user_message,
                context=[],
                user_id=user_id,
                db_session=session_db
            )
            
            response = result.get('response', 'Нет ответа') if isinstance(result, dict) else str(result)
            tool_calls = result.get('tool_calls', []) if isinstance(result, dict) else []
            
            return response, tool_calls
            
        finally:
            session_db.close()
            
    except Exception as e:
        print(f"{Colors.RED}❌ Ошибка: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        return f"Ошибка: {e}", []

async def run_test_scenario():
    """Запускаем тестовый сценарий"""
    print_header("🎯 ТЕСТОВЫЙ СЦЕНАРИЙ ДИАЛОГА С АГЕНТОМ")
    print(f"{Colors.CYAN}Это автоматический тест для проверки работы агента{Colors.RESET}")
    
    test_messages = [
        ("Привет", "Проверка естественного приветствия"),
        ("Найти тестовых пользователей для ИИ агента", "Должен вызвать find_partners"),
        ("На завтра в 10:00", "Создание задачи с указанным временем"),
        ("Найди партнеров по AI", "Поиск партнеров по интересам"),
        ("Покажи мои задачи", "Отображение списка задач")
    ]
    
    results = {
        'total': len(test_messages),
        'issues': []
    }
    
    for i, (message, expected) in enumerate(test_messages, 1):
        print_separator()
        print_header(f"СООБЩЕНИЕ {i}/{len(test_messages)}")
        print(f"{Colors.CYAN}Ожидаемое поведение: {expected}{Colors.RESET}")
        print_user_message(message)
        
        response, tool_calls = await send_message(message)
        
        print_agent_message(response)
        print_function_calls(tool_calls)
        
        # Анализ результатов
        if i == 1 and "Вижу ты" in response:
            results['issues'].append("❌ Приветствие: описывает профиль (должен просто 'Привет!')")
        
        if i == 2:
            func_names = [call.get('function', {}).get('name', '') for call in tool_calls]
            if 'find_partners' not in func_names and 'find_relevant_contacts_for_task' not in func_names:
                results['issues'].append(f"❌ Поиск партнеров: вызвал {func_names} вместо find_partners")
        
        if i == 3:
            func_names = [call.get('function', {}).get('name', '') for call in tool_calls]
            if 'add_task' in func_names:
                results['issues'].append("✅ Создание задачи: правильно создал задачу с временем")
            else:
                results['issues'].append("❌ Создание задачи: не создал задачу или создал без времени")
        
        await asyncio.sleep(1)
    
    # Итоги
    print_separator()
    print_header("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print(f"\n{Colors.BOLD}Всего тестов: {results['total']}{Colors.RESET}")
    
    if results['issues']:
        print(f"\n{Colors.YELLOW}Обнаруженные проблемы:{Colors.RESET}")
        for issue in results['issues']:
            print(f"  {issue}")
    else:
        print(f"\n{Colors.GREEN}✅ Все тесты прошли успешно!{Colors.RESET}")
    
    print_separator()
    print_header("✅ ТЕСТОВЫЙ СЦЕНАРИЙ ЗАВЕРШЕН")

async def run_interactive_mode():
    """Интерактивный режим"""
    print_header("💬 ИНТЕРАКТИВНЫЙ РЕЖИМ")
    print(f"{Colors.CYAN}Введите сообщение для агента (или 'exit' для выхода){Colors.RESET}\n")
    
    while True:
        try:
            user_input = input(f"{Colors.BLUE}👤 ВЫ: {Colors.RESET}").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ['exit', 'quit', 'выход']:
                print(f"\n{Colors.YELLOW}👋 До встречи!{Colors.RESET}")
                break
            
            response, tool_calls = await send_message(user_input)
            
            print_agent_message(response)
            print_function_calls(tool_calls)
            print()
            
        except KeyboardInterrupt:
            print(f"\n\n{Colors.YELLOW}👋 До встречи!{Colors.RESET}")
            break
        except Exception as e:
            print(f"\n{Colors.RED}❌ Ошибка: {e}{Colors.RESET}\n")

async def main():
    print_header("🚀 ТЕСТ ДИАЛОГА С AI АГЕНТОМ")
    print(f"{Colors.CYAN}Выберите режим:{Colors.RESET}")
    print(f"  1. Автоматический тестовый сценарий")
    print(f"  2. Интерактивный режим (ввод вручную)")
    
    choice = input(f"\n{Colors.YELLOW}Ваш выбор (1/2): {Colors.RESET}").strip()
    
    if choice == '1':
        await run_test_scenario()
    elif choice == '2':
        await run_interactive_mode()
    else:
        print(f"{Colors.RED}Неверный выбор. Запускаем автоматический тест...{Colors.RESET}")
        await run_test_scenario()

if __name__ == "__main__":
    asyncio.run(main())
