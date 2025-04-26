from abc import ABC, abstractmethod
from typing import Optional
import asyncio
from loguru import logger
from database.repository import Repository

class BotState(ABC):
    """Abstract base class for bot states"""
    
    @abstractmethod
    async def start(self) -> None:
        """Handle start action"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Handle stop action"""
        pass
    
    @abstractmethod
    async def handle_message(self, message_id: int) -> None:
        """Handle message forwarding"""
        pass

class IdleState(BotState):
    """State when bot is not forwarding messages"""
    
    def __init__(self, bot_context):
        self.context = bot_context
    
    async def start(self) -> None:
        interval = int(await Repository.get_config("repost_interval", "3600"))
        self.context.state = RunningState(self.context, interval)
        await self.context._notify_owner("Bot started forwarding")
    
    async def stop(self) -> None:
        # Already stopped
        pass
    
    async def handle_message(self, message_id: int) -> None:
        # Don't forward messages in idle state
        logger.info("Bot is idle, not forwarding messages")

class RunningState(BotState):
    """State when bot is actively forwarding messages"""
    
    def __init__(self, bot_context, interval: int):
        self.context = bot_context
        self.interval = interval
        self._repost_task: Optional[asyncio.Task] = None
        self._start_repost_task()
    
    def _start_repost_task(self):
        if not self._repost_task or self._repost_task.done():
            self._repost_task = asyncio.create_task(self._fallback_repost())
    
    async def start(self) -> None:
        # Already running
        pass
    
    async def stop(self) -> None:
        if self._repost_task and not self._repost_task.done():
            self._repost_task.cancel()
        self.context.state = IdleState(self.context)
        await self.context._notify_owner("Bot stopped forwarding")
    
    async def handle_message(self, message_id: int) -> None:
        await self.context._forward_message(message_id)
    
    async def _fallback_repost(self):
        """Periodic repost task"""
        while True:
            try:
                await asyncio.sleep(self.interval)
                last_message_id = await Repository.get_last_message(self.context.source_channel)
                if last_message_id:
                    await self.handle_message(last_message_id)
                    logger.info("Triggered periodic repost")
            except asyncio.CancelledError:
                logger.info("Repost task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in fallback repost: {e}")
                await asyncio.sleep(60)

class BotContext:
    """Context class that maintains current bot state"""
    
    def __init__(self, bot, source_channel: str):
        self.bot = bot
        self.source_channel = source_channel
        self.state: BotState = IdleState(self)
    
    async def start(self) -> None:
        await self.state.start()
    
    async def stop(self) -> None:
        await self.state.stop()
    
    async def handle_message(self, message_id: int) -> None:
        await self.state.handle_message(message_id)
    
    async def _forward_message(self, message_id: int) -> bool:
        """Forward a message to all target chats"""
        success = False
        target_chats = await Repository.get_target_chats()
        
        if not target_chats:
            logger.warning("No target chats for forwarding")
            return False

        for chat_id in target_chats:
            try:
                await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=self.source_channel,
                    message_id=message_id
                )
                await Repository.log_forward(message_id)
                success = True
                logger.info(f"Forwarded to {chat_id}")
            except Exception as e:
                logger.error(f"Error forwarding to {chat_id}: {e}")

        return success
    
    async def _notify_owner(self, message: str):
        """Send notification to bot owner"""
        try:
            from utils.config import Config
            await self.bot.send_message(Config().owner_id, message)
        except Exception as e:
            logger.error(f"Failed to notify owner: {e}")
