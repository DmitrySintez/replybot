from abc import ABC, abstractmethod
from aiogram import types

class Command(ABC):
    """Base command class implementing Command Pattern"""
    
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
    
    async def execute(self, message: types.Message) -> None:
        """Execute the command if user has permission"""
        if message.from_user.id != self.owner_id:
            return
        await self._handle(message)
    
    @abstractmethod
    async def _handle(self, message: types.Message) -> None:
        """Implementation of command handling"""
        pass
