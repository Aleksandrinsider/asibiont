from abc import ABC, abstractmethod
from datetime import datetime

class BaseCommand(ABC):
    def __init__(self, message: str, message_time: datetime = None, **kwargs):
        self.message = message
        self.message_time = message_time
        # Store any additional parameters extracted by AI
        self.params = kwargs

    @abstractmethod
    async def execute(self, user_id, db_session):
        pass