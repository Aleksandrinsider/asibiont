"""
Система мониторинга ошибок с автоматическими уведомлениями
"""
import asyncio
import re
import json
import requests
from datetime import datetime
from pathlib import Path
import subprocess
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ErrorMonitor:
    def __init__(self):
        self.error_patterns = {
            'missing_parameter': {
                'pattern': r'(\w+)\(\) missing \d+ required positional argument: \'(\w+)\'',
                'severity': 'HIGH',
                'auto_fix': True
            },
            'key_error': {
                'pattern': r'KeyError: \'(\w+)\'',
                'severity': 'MEDIUM', 
                'auto_fix': False
            },
            'attribute_error': {
                'pattern': r'AttributeError: \'(\w+)\' object has no attribute \'(\w+)\'',
                'severity': 'MEDIUM',
                'auto_fix': False
            },
            'import_error': {
                'pattern': r'ImportError: (.+)',
                'severity': 'HIGH',
                'auto_fix': True
            },
            'syntax_error': {
                'pattern': r'SyntaxError: (.+)',
                'severity': 'CRITICAL',
                'auto_fix': False
            }
        }
        
        self.last_errors = set()
        self.fix_requests = []
        
    async def check_file_logs(self, log_file='test_logs.txt'):
        """Проверка логов из файла"""
        try:
            errors = []
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Парсинг формата [timestamp] LEVEL: message
                match = re.match(r'\[([^\]]+)\]\s+(\w+):\s+(.+)', line)
                if not match:
                    continue
                    
                timestamp, level, message = match.groups()
                
                for error_type, config in self.error_patterns.items():
                    error_match = re.search(config['pattern'], message)
                    if error_match:
                        error_info = {
                            'type': error_type,
                            'message': f"{level}: {message}",
                            'timestamp': timestamp,
                            'severity': config['severity'],
                            'auto_fix': config['auto_fix'],
                            'match_groups': error_match.groups(),
                            'raw_log': {'timestamp': timestamp, 'level': level, 'message': message}
                        }
                        
                        error_key = f"{error_type}:{message}"
                        if error_key not in self.last_errors:
                            errors.append(error_info)
                            self.last_errors.add(error_key)
            
            return errors
            
        except FileNotFoundError:
            logger.warning(f"Log file {log_file} not found")
            return []
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
            return []

    async def check_local_logs(self):
        """Проверка локальных логов для тестирования"""
        # Создадим тестовые логи с ошибками
        test_logs = [
            {'message': 'INFO: Application started', 'timestamp': '2026-01-24T12:00:00'},
            {'message': 'ERROR: post_process_response() missing 1 required positional argument: "user_id"', 'timestamp': '2026-01-24T12:01:00'},
            {'message': 'WARNING: Database connection slow', 'timestamp': '2026-01-24T12:02:00'},
            {'message': 'ERROR: KeyError: "session_id"', 'timestamp': '2026-01-24T12:03:00'},
            {'message': 'CRITICAL: SyntaxError: invalid syntax', 'timestamp': '2026-01-24T12:04:00'},
            {'message': 'ERROR: ImportError: No module named "missing_package"', 'timestamp': '2026-01-24T12:05:00'},
            {'message': 'INFO: Request processed successfully', 'timestamp': '2026-01-24T12:06:00'}
        ]
        
        errors = []
        for log_entry in test_logs:
            message = log_entry.get('message', '')
            
            for error_type, config in self.error_patterns.items():
                match = re.search(config['pattern'], message)
                if match:
                    error_info = {
                        'type': error_type,
                        'message': message,
                        'timestamp': log_entry['timestamp'],
                        'severity': config['severity'],
                        'auto_fix': config['auto_fix'],
                        'match_groups': match.groups(),
                        'raw_log': log_entry
                    }
                    
                    error_key = f"{error_type}:{message}"
                    if error_key not in self.last_errors:
                        errors.append(error_info)
                        self.last_errors.add(error_key)
        
        return errors

    async def check_railway_logs(self):
        """Проверка логов Railway через CLI"""
        try:
            # Railway CLI команда для получения логов
            result = subprocess.run(
                ['railway', 'logs', '--json', '--lines', '50'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                logs = result.stdout
                return self.parse_logs(logs)
            else:
                logger.warning(f"Railway logs error: {result.stderr}")
                return []
                
        except subprocess.TimeoutExpired:
            logger.warning("Railway logs timeout")
            return []
        except Exception as e:
            logger.error(f"Error checking Railway logs: {e}")
            return []
    
    def parse_logs(self, logs):
        """Парсинг логов для поиска ошибок"""
        errors = []
        lines = logs.strip().split('\n')
        
        for line in lines:
            try:
                log_entry = json.loads(line) if line.strip() else {}
                message = log_entry.get('message', '')
                
                for error_type, config in self.error_patterns.items():
                    match = re.search(config['pattern'], message)
                    if match:
                        error_info = {
                            'type': error_type,
                            'message': message,
                            'timestamp': datetime.now().isoformat(),
                            'severity': config['severity'],
                            'auto_fix': config['auto_fix'],
                            'match_groups': match.groups(),
                            'raw_log': log_entry
                        }
                        
                        error_key = f"{error_type}:{message}"
                        if error_key not in self.last_errors:
                            errors.append(error_info)
                            self.last_errors.add(error_key)
                            
            except json.JSONDecodeError:
                continue
                
        return errors
    
    def generate_fix_prompt(self, error):
        """Генерация промпта для исправления ошибки"""
        if error['type'] == 'missing_parameter':
            func_name, param_name = error['match_groups']
            return f"""
КРИТИЧЕСКАЯ ОШИБКА: {error['message']}

Функция {func_name}() вызывается без обязательного параметра '{param_name}'.

Найди все вызовы функции {func_name} и добавь недостающий параметр {param_name}.

Контекст ошибки:
- Время: {error['timestamp']}
- Серьезность: {error['severity']}
- Сообщение: {error['message']}

Исправь это НЕМЕДЛЕННО.
"""
        
        elif error['type'] == 'import_error':
            missing_module = error['match_groups'][0]
            return f"""
ОШИБКА ИМПОРТА: {error['message']}

Отсутствует модуль или неправильный импорт: {missing_module}

Проверь:
1. Установлен ли модуль в requirements.txt
2. Правильность путей импорта
3. Отсутствие циклических импортов

Исправь импорты и зависимости.
"""
        
        else:
            return f"""
ОШИБКА: {error['type'].upper()}

Сообщение: {error['message']}
Время: {error['timestamp']}
Серьезность: {error['severity']}

Проанализируй и исправь эту ошибку.
"""
    
    async def send_notification(self, errors):
        """Отправка уведомления о найденных ошибках"""
        if not errors:
            return
            
        critical_errors = [e for e in errors if e['severity'] == 'CRITICAL']
        high_errors = [e for e in errors if e['severity'] == 'HIGH']
        
        notification = {
            'timestamp': datetime.now().isoformat(),
            'total_errors': len(errors),
            'critical_count': len(critical_errors),
            'high_count': len(high_errors),
            'errors': errors[:5]  # первые 5 ошибок
        }
        
        # Сохранение в файл для просмотра
        with open('error_reports.json', 'a', encoding='utf-8') as f:
            f.write(json.dumps(notification, ensure_ascii=False, indent=2) + '\n---\n')
        
        # Вывод в консоль
        print(f"\n🚨 НАЙДЕНО ОШИБОК: {len(errors)}")
        for error in errors[:3]:
            print(f"  {error['severity']} - {error['type']}: {error['message'][:100]}...")
        
        # Генерация промптов для исправления
        auto_fix_errors = [e for e in errors if e['auto_fix']]
        if auto_fix_errors:
            print(f"\n🔧 АВТОИСПРАВЛЕНИЯ ДОСТУПНЫ: {len(auto_fix_errors)}")
            for error in auto_fix_errors:
                fix_prompt = self.generate_fix_prompt(error)
                self.fix_requests.append({
                    'error': error,
                    'prompt': fix_prompt,
                    'created_at': datetime.now().isoformat()
                })
                
            # Сохранение в файл для постоянства
            with open('pending_fixes.json', 'w', encoding='utf-8') as f:
                json.dump(self.fix_requests, f, ensure_ascii=False, indent=2)
    
    def get_pending_fixes(self):
        """Получение списка ожидающих исправления ошибок"""
        # Попробуем загрузить из файла
        try:
            with open('pending_fixes.json', 'r', encoding='utf-8') as f:
                self.fix_requests = json.load(f)
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            pass
            
        return self.fix_requests
    
    def mark_fix_completed(self, error_index):
        """Отметка исправления как завершенного"""
        if 0 <= error_index < len(self.fix_requests):
            completed = self.fix_requests.pop(error_index)
            logger.info(f"Fix completed for: {completed['error']['type']}")
            return completed
        return None
    
    async def monitor_loop(self, interval=60):
        """Основной цикл мониторинга"""
        logger.info(f"🚀 Запуск мониторинга ошибок (интервал: {interval}s)")
        
        while True:
            try:
                logger.info("🔍 Проверка логов...")
                errors = await self.check_railway_logs()
                
                if errors:
                    logger.warning(f"⚠️ Найдено ошибок: {len(errors)}")
                    await self.send_notification(errors)
                else:
                    logger.info("✅ Ошибок не найдено")
                
                await asyncio.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("🛑 Мониторинг остановлен пользователем")
                break
            except Exception as e:
                logger.error(f"❌ Ошибка мониторинга: {e}")
                await asyncio.sleep(interval)

# CLI интерфейс
if __name__ == "__main__":
    import sys
    
    monitor = ErrorMonitor()
    
    if len(sys.argv) > 1 and sys.argv[1] == "start":
        # Запуск мониторинга
        asyncio.run(monitor.monitor_loop())
    
    elif len(sys.argv) > 1 and sys.argv[1] == "file":
        # Тестирование с файлом логов
        async def file_test():
            print("📁 ФАЙЛОВЫЙ РЕЖИМ: Проверка test_logs.txt")
            errors = await monitor.check_file_logs()
            await monitor.send_notification(errors)
        
        asyncio.run(file_test())
        
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        # Локальное тестирование с фиктивными данными
        async def local_test():
            print("🧪 ТЕСТОВЫЙ РЕЖИМ: Проверка с фиктивными ошибками")
            errors = await monitor.check_local_logs()
            await monitor.send_notification(errors)
        
        asyncio.run(local_test())
    
    elif len(sys.argv) > 1 and sys.argv[1] == "check":
        # Одноразовая проверка
        async def single_check():
            errors = await monitor.check_railway_logs()
            await monitor.send_notification(errors)
        
        asyncio.run(single_check())
    
    elif len(sys.argv) > 1 and sys.argv[1] == "fixes":
        # Показать ожидающие исправления
        fixes = monitor.get_pending_fixes()
        if fixes:
            print(f"\n📋 ОЖИДАЕТ ИСПРАВЛЕНИЯ: {len(fixes)} ошибок")
            for i, fix in enumerate(fixes):
                error = fix['error']
                print(f"\n{i+1}. {error['severity']} - {error['type']}")
                print(f"   Время: {error['timestamp']}")
                print(f"   Сообщение: {error['message'][:100]}...")
                print(f"\n   ПРОМПТ ДЛЯ ИСПРАВЛЕНИЯ:")
                print(fix['prompt'])
                print("-" * 80)
        else:
            print("✅ Нет ошибок, ожидающих исправления")
    
    else:
        print("""
Использование:
  python error_monitor.py start    - Запуск постоянного мониторинга
  python error_monitor.py check    - Одноразовая проверка Railway логов
  python error_monitor.py file     - Тестирование с файлом test_logs.txt
  python error_monitor.py test     - Локальное тестирование с фиктивными ошибками
  python error_monitor.py fixes    - Показать ошибки для исправления
""")