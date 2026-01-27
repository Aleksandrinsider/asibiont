import re
from typing import Union
from .commands import *

class CommandRouter:
    PATTERNS = {
        'create': r'(напомни|создай|добавь|нужно|надо|через \d+ минут)',
        'delete': r'(удали|убери)\s+задачу',
        'complete': r'(готово|сделал|выполнил)',
        'list': r'(покажи|список|какие|мои)\s+(задач|дел|задачи)',
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