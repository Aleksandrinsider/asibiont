from abc import ABC, abstractmethod

class BaseCommand(ABC):
    def __init__(self, message: str):
        self.message = message

    @abstractmethod
    async def execute(self, user_id, db_session):
        pass