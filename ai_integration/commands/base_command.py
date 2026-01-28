from abc import ABC, abstractmethod

class BaseCommand(ABC):
    def __init__(self, message: str, **kwargs):
        self.message = message
        # Store any additional parameters extracted by AI
        self.params = kwargs

    @abstractmethod
    async def execute(self, user_id, db_session):
        pass