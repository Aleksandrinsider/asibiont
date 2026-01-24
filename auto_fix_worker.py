"""
Worker для автоматического запроса исправлений ошибок
"""
import asyncio
import json
import requests
import time
from error_monitor import ErrorMonitor
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AutoFixWorker:
    def __init__(self, copilot_api_endpoint=None):
        self.monitor = ErrorMonitor()
        self.copilot_api = copilot_api_endpoint
        self.processed_errors = set()
    
    def create_github_issue(self, error):
        """Создание GitHub Issue для ошибки (если нужно)"""
        issue_title = f"🚨 Auto-detected error: {error['type']}"
        issue_body = f"""
## Автоматически обнаруженная ошибка

**Тип:** {error['type']}
**Серьезность:** {error['severity']}
**Время:** {error['timestamp']}

### Сообщение об ошибке:
```
{error['message']}
```

### Детали:
- Auto-fix доступен: {'✅' if error['auto_fix'] else '❌'}
- Match groups: {error['match_groups']}

### Предлагаемое действие:
{'Автоматическое исправление запрошено' if error['auto_fix'] else 'Требуется ручное исправление'}

---
*Создано автоматически системой мониторинга*
"""
        
        # Здесь можно добавить GitHub API вызов
        logger.info(f"Issue created: {issue_title}")
        return issue_title, issue_body
    
    def send_telegram_notification(self, error):
        """Отправка уведомления в Telegram"""
        message = f"""
🚨 <b>ОШИБКА В ПРОЕКТЕ</b>

<b>Тип:</b> {error['type']}
<b>Серьезность:</b> {error['severity']}
<b>Время:</b> {error['timestamp']}

<b>Сообщение:</b>
<code>{error['message']}</code>

{'🔧 Автоисправление доступно' if error['auto_fix'] else '⚠️ Требуется ручное вмешательство'}
"""
        
        # Здесь добавить отправку через ваш Telegram бот
        logger.info("Telegram notification sent")
        return message
    
    async def request_copilot_fix(self, error):
        """Запрос исправления через Copilot API (концептуально)"""
        fix_prompt = self.monitor.generate_fix_prompt(error)
        
        # Имитация API запроса к Copilot
        # В реальности это был бы HTTP запрос
        fix_request = {
            'prompt': fix_prompt,
            'error_context': error,
            'timestamp': time.time(),
            'priority': 'HIGH' if error['severity'] in ['CRITICAL', 'HIGH'] else 'NORMAL'
        }
        
        logger.info(f"🔧 Fix request created for {error['type']}")
        
        # Сохранение запроса для обработки
        with open('pending_fixes.json', 'a', encoding='utf-8') as f:
            f.write(json.dumps(fix_request, ensure_ascii=False, indent=2) + '\n---\n')
        
        return fix_request
    
    async def process_errors(self):
        """Обработка найденных ошибок"""
        errors = await self.monitor.check_railway_logs()
        
        for error in errors:
            error_key = f"{error['type']}:{hash(error['message'])}"
            
            if error_key in self.processed_errors:
                continue
            
            self.processed_errors.add(error_key)
            
            logger.info(f"Processing error: {error['type']}")
            
            # Уведомления
            self.send_telegram_notification(error)
            
            # GitHub Issue для критических ошибок
            if error['severity'] == 'CRITICAL':
                self.create_github_issue(error)
            
            # Запрос автоисправления
            if error['auto_fix']:
                await self.request_copilot_fix(error)
            
            # Пауза между обработкой
            await asyncio.sleep(2)
    
    async def worker_loop(self, interval=120):
        """Основной цикл worker'а"""
        logger.info(f"🤖 AutoFix Worker запущен (интервал: {interval}s)")
        
        while True:
            try:
                await self.process_errors()
                logger.info("✅ Цикл обработки завершен")
                await asyncio.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("🛑 Worker остановлен")
                break
            except Exception as e:
                logger.error(f"❌ Worker error: {e}")
                await asyncio.sleep(60)  # пауза при ошибке

# Запуск
if __name__ == "__main__":
    worker = AutoFixWorker()
    asyncio.run(worker.worker_loop())