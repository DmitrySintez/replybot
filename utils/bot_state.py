from abc import ABC, abstractmethod
from typing import Optional
import asyncio
from loguru import logger
from database.repository import Repository
from datetime import datetime

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
    
    def __init__(self, bot_context, auto_forward: bool = False):
        self.context = bot_context
        self.auto_forward = auto_forward
    
    async def start(self) -> None:
        interval = int(await Repository.get_config("repost_interval", "3600"))
        self.context.state = RunningState(self.context, interval, self.auto_forward)
        await self.context._notify_owner("Bot started forwarding")
    
    async def stop(self) -> None:
        # Already stopped
        pass
    
    async def handle_message(self, channel_id: str, message_id: int) -> None:
        # Don't forward messages in idle state
        logger.info("Bot is idle, not forwarding messages")

# utils/bot_state.py - Updated RunningState class

class RunningState(BotState):
    """State when bot is actively forwarding messages"""
    
    def __init__(self, bot_context, interval: int, auto_forward: bool = False):
        self.context = bot_context
        self.interval = interval
        self._repost_task: Optional[asyncio.Task] = None
        self.auto_forward = auto_forward
        self._channel_last_post = {}  # Track last post time for each channel
        self._start_repost_task()
    
    def _start_repost_task(self):
        # Always start the repost task if it's not running, regardless of auto_forward setting
        if not self._repost_task or self._repost_task.done():
            self._repost_task = asyncio.create_task(self._fallback_repost())

    async def toggle_auto_forward(self):
        """Toggle automatic message forwarding"""
        self.auto_forward = not self.auto_forward
        await self.context._notify_owner(
            "Automatic forwarding enabled" if self.auto_forward else "Automatic forwarding disabled"
        )
    
    async def start(self) -> None:
        # Already running
        pass
    
    async def stop(self) -> None:
        if self._repost_task and not self._repost_task.done():
            self._repost_task.cancel()
        self.auto_forward = False
        self.context.state = IdleState(self.context, self.auto_forward)
        await self.context._notify_owner("Bot stopped forwarding")
    
    async def handle_message(self, channel_id: str, message_id: int) -> None:
        if self.auto_forward:
            await self.context._forward_message(channel_id, message_id)
            # Update last post time for this channel
            self._channel_last_post[channel_id] = datetime.now().timestamp()
        else:
            logger.info("Auto-forwarding is disabled, skipping message")
    
    async def _get_next_channel_to_repost(self):
        """Get the next channel that should be reposted based on intervals"""
        now = datetime.now().timestamp()
        source_channels = self.context.config.source_channels
        
        if not source_channels:
            return None
            
        # Default interval between channels is 5 minutes (300 seconds)
        channel_interval = 300
        
        # Find the channel that hasn't been posted for the longest time
        oldest_channel = None
        oldest_time = now
        
        for channel in source_channels:
            last_post_time = self._channel_last_post.get(channel, 0)
            
            # If this channel hasn't been posted for more than the interval
            # and is older than our current oldest, select it
            if now - last_post_time >= channel_interval and last_post_time < oldest_time:
                oldest_channel = channel
                oldest_time = last_post_time
                
        return oldest_channel
    
    # Update RunningState._fallback_repost method in utils/bot_state.py
    async def _fallback_repost(self):
        """Periodic repost task rotating through source channels with proper intervals"""
        while True:
            try:
                # Check every minute for a channel that needs reposting
                await asyncio.sleep(60)
                
                # Find the next channel to repost
                channel_id = await self._get_next_channel_to_repost()
                
                if not channel_id:
                    # No channel needs reposting yet
                    continue
                    
                # Get last message for this channel
                message_id = await Repository.get_last_message(channel_id)
                
                if not message_id:
                    logger.warning(f"No message found for channel {channel_id}")
                    
                    # Mark this channel as recently processed to avoid constantly checking it
                    now = datetime.now().timestamp()
                    self._channel_last_post[channel_id] = now
                    
                    # Try to find a message in another channel instead
                    continue
                
                # Forward the message
                await self.context._forward_message(channel_id, message_id)
                
                # Update last post time for this channel
                now = datetime.now().timestamp()
                self._channel_last_post[channel_id] = now
                
                logger.info(f"Triggered periodic repost from channel {channel_id}")
                    
            except asyncio.CancelledError:
                logger.info("Repost task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in fallback repost: {e}")
                await asyncio.sleep(60)  # Wait a bit before trying again

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
