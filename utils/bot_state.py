from abc import ABC, abstractmethod
from typing import Optional
import asyncio
from loguru import logger
from database.repository import Repository
from datetime import datetime
from aiogram import types

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
    
    # In IdleState.start method (utils/bot_state.py)
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
    
    # In RunningState class (utils/bot_state.py)
    # In RunningState class - update __init__ method in utils/bot_state.py
    def __init__(self, bot_context, interval: int, auto_forward: bool = False):
        self.context = bot_context
        self.interval = interval  # Global repost interval
        self._repost_task: Optional[asyncio.Task] = None
        self.auto_forward = auto_forward
        
        # Initialize tracking for each channel's last post time
        now = datetime.now().timestamp()
        self._channel_last_post = {}
        
        # Important: Initialize all channels with current time
        # This forces each channel to wait for the full interval before posting
        for channel in self.context.config.source_channels:
            self._channel_last_post[channel] = now
        
        # Initialize these attributes to track channel rotation
        self._last_processed_channel = None
        self._last_global_post_time = now
        
        # Start the repost task
        self._start_repost_task()
        
    def _start_repost_task(self):
        # Always start the repost task if it's not running, regardless of auto_forward setting
        if not self._repost_task or self._repost_task.done():
            self._repost_task = asyncio.create_task(self._fallback_repost())

    # Add this method to the ForwarderBot class in bot.py
    # Remove this from RunningState class in bot_state.py
    # In utils/bot_state.py - Add this to RunningState class
    async def toggle_auto_forward(self):
        """Toggle automatic message forwarding"""
        self.auto_forward = not self.auto_forward
        logger.info(f"Auto-forwarding: {self.auto_forward}")
        
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
        
        # Use the interval set by the user
        channel_interval = self.interval
        
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
    # Add this helper method to RunningState
    async def _get_channel_pair_interval(self, channel1: str, channel2: str) -> Optional[int]:
        """Get the interval between two channels (if set)"""
        try:
            # Get from database
            intervals = await Repository.get_channel_intervals()
            
            # Check if this pair has a configured interval
            for pair_key, pair_data in intervals.items():
                if pair_key == channel1 and pair_data["next_channel"] == channel2:
                    return pair_data["interval"]
            
            return None  # No specific interval set
        except Exception as e:
            logger.error(f"Error getting channel pair interval: {e}")
            return None
        
    async def _fallback_repost(self):
        """Periodic repost task with proper interval handling for all channels"""
        while True:
            try:
                # Check more frequently to catch timing properly
                await asyncio.sleep(10)
                
                # Get current time
                now = datetime.now().timestamp()
                
                # Get all source channels
                source_channels = self.context.config.source_channels
                if not source_channels:
                    logger.warning("No source channels configured")
                    continue
                
                # Find all channels eligible for posting based on their global interval
                eligible_channels = []
                for channel in source_channels:
                    last_post_time = self._channel_last_post.get(channel, 0)
                    
                    # Check if this channel's global interval has elapsed
                    if now - last_post_time >= self.interval:
                        eligible_channels.append(channel)
                
                if not eligible_channels:
                    # No channels are ready yet
                    continue
                    
                # Determine which channel to post next
                next_channel = None
                
                # If no channel has been processed yet, use the first eligible channel
                if self._last_processed_channel is None:
                    next_channel = eligible_channels[0]
                    logger.debug(f"First run, selecting channel {next_channel}")
                else:
                    # Find the next channel in sequence that's eligible
                    current_idx = -1
                    try:
                        current_idx = source_channels.index(self._last_processed_channel)
                    except ValueError:
                        # Last processed channel no longer exists, start from beginning
                        pass
                    
                    # Try to find the next channel in sequence
                    for i in range(1, len(source_channels) + 1):
                        next_idx = (current_idx + i) % len(source_channels)
                        candidate = source_channels[next_idx]
                        
                        if candidate in eligible_channels:
                            # This is the next channel in sequence that's eligible
                            
                            # Check if enough time has passed since the last global post
                            # Default to 5 minutes between channels
                            pair_interval = await self._get_channel_pair_interval(
                                self._last_processed_channel, candidate
                            ) or 300
                            
                            if now - self._last_global_post_time >= pair_interval:
                                next_channel = candidate
                                logger.debug(f"Next channel {next_channel} ready after pair interval")
                                break
                
                # If no suitable channel found, continue waiting
                if next_channel is None:
                    continue
                    
                # Get the last message for this channel
                message_id = await Repository.get_last_message(next_channel)
                
                if not message_id:
                    logger.warning(f"No message found for channel {next_channel}")
                    
                    # Try to find the latest message
                    latest_id = await self.context.find_latest_message(next_channel)
                    if latest_id:
                        message_id = latest_id
                        await Repository.save_last_message(next_channel, latest_id)
                    else:
                        # Mark as processed so we don't keep trying
                        self._channel_last_post[next_channel] = now
                        continue
                
                # Forward the message
                logger.info(f"Attempting to forward message {message_id} from channel {next_channel}")
                success = await self.context._forward_message(next_channel, message_id)
                
                if success:
                    # Update tracking
                    now = datetime.now().timestamp()
                    self._channel_last_post[next_channel] = now
                    self._last_global_post_time = now
                    self._last_processed_channel = next_channel
                    
                    # Calculate next post time for user-friendly logging
                    next_global_time = now + self.interval
                    next_time_str = datetime.fromtimestamp(next_global_time).strftime('%H:%M:%S')
                    
                    # Log with formatted interval info
                    minutes = self.interval // 60
                    logger.info(f"Forwarded from channel {next_channel}. Next repost from this channel in {minutes} minutes (at {next_time_str}).")
                
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
