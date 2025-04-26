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
    async def handle_message(self, channel_id: str, message_id: int) -> None:
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
    
    async def handle_message(self, channel_id: str, message_id: int) -> None:
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
    
    async def handle_message(self, channel_id: str, message_id: int) -> None:
        await self.context._forward_message(channel_id, message_id)
    
    async def _fallback_repost(self):
        """Periodic repost task for most recent message across all channels"""
        while True:
            try:
                await asyncio.sleep(self.interval)
                
                # Get the most recent message across all channels
                channel_id, message_id = await Repository.get_latest_message()
                
                if channel_id and message_id:
                    await self.handle_message(channel_id, message_id)
                    logger.info(f"Triggered periodic repost from channel {channel_id}")
                else:
                    logger.warning("No recent messages found for periodic repost")
                    
            except asyncio.CancelledError:
                logger.info("Repost task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in fallback repost: {e}")
                await asyncio.sleep(60)

class BotContext:
    """Context class that maintains current bot state"""
    
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.state: BotState = IdleState(self)
    
    async def start(self) -> None:
        await self.state.start()
    
    async def stop(self) -> None:
        await self.state.stop()
    
    async def handle_message(self, channel_id: str, message_id: int) -> None:
        await self.state.handle_message(channel_id, message_id)
    
    async def _forward_message(self, channel_id: str, message_id: int) -> bool:
        """Forward a message to all target chats (groups/supergroups, not channels)"""
        success = False
        target_chats = await Repository.get_target_chats()
        
        if not target_chats:
            logger.warning("No target chats for forwarding")
            return False

        for chat_id in target_chats:
            # Skip forwarding to the source channel itself to avoid loops
            if str(chat_id) == channel_id:
                logger.info(f"Skipping forward to source channel {chat_id}")
                continue
                
            try:
                # Check if this is a channel (we want to skip forwarding to channels)
                chat_info = await self.bot.get_chat(chat_id)
                if chat_info.type == 'channel':
                    logger.info(f"Skipping forward to channel {chat_id} (channels are not target destinations)")
                    continue
                    
                # Only forward to groups and supergroups
                await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=channel_id,
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
            await self.bot.send_message(self.config.owner_id, message)
        except Exception as e:
            logger.error(f"Failed to notify owner: {e}")