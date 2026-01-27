import re
from typing import Union
from .commands import *

class CommandRouter:
    PATTERNS = {
        'create': r'(напомни|создай|добавь|нужно|надо|давай|сыграем|встретимся|сходим|позвони|напиши|через|сегодня|завтра|вечером|утром|в \d+:\d+|в \d+ |в \d+час)',
        'delete': r'(удали|убери|отмени)\s+задачу',
        'complete': r'(готово|сделал|выполнил|завершил)',
        'list': r'(покажи|список|какие|мои|все)\s+(задач|дел|задачи|дела)',
    }

    def route(self, message: str):
        message_lower = message.lower()

        for cmd_type, pattern in self.PATTERNS.items():
            if re.search(pattern, message_lower):
                return self._create_command(cmd_type, message)

        return ConversationCommand(message)

    def _create_command(self, cmd_type, message):
        commands = {
            'create': CreateTaskCommand,
            'delete': DeleteTaskCommand,
            'complete': CompleteTaskCommand,
            'list': ListTasksCommand,
        }
        return commands[cmd_type](message)