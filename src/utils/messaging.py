"""
Messaging utilities for the Telegram Forwarder Bot.

This module provides message processing, forwarding, and validation utilities.
"""

from typing import Optional, Dict, Any, Tuple, List
from loguru import logger
from aiogram import Bot
from aiogram import types
from aiogram.exceptions import TelegramAPIError


class MessageProcessor:
    """Handles message processing, formatting, and delivery."""
    
    def __init__(self, bot: Bot, db_manager):
        """Initialize with required dependencies."""
        self.bot = bot
        self.db = db_manager
        self._message_cache = {}
    
    async def forward_message_to_all_targets(self, message: types.Message) -> bool:
        """
        Forward a message to all target chats.
        
        Args:
            message: The message to forward
            
        Returns:
            bool: True if forwarded to at least one chat, False otherwise
        """
        target_chats = await self.db.get_target_chats()
        
        if not target_chats:
            logger.warning("No target chats configured for forwarding")
            return False
        
        success_count = 0
        source_chat_id = str(message.chat.id)
        
        for chat_id in target_chats:
            try:
                # Get chat info
                chat = await self.bot.get_chat(chat_id)
                
                # Skip forwarding to the source channel itself
                if str(chat_id) == source_chat_id:
                    logger.info(f"Skipping forward to source {chat.type} {chat_id}")
                    continue
                
                # Forward the message
                await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                
                # Log the successful forward
                await self.db.log_forward(message.message_id)
                success_count += 1
                
                logger.info(f"Successfully forwarded to {chat.type} {chat.title} ({chat_id})")
                
            except Exception as e:
                logger.error(f"Error forwarding to {chat_id}: {e}")
                continue
        
        return success_count > 0
    
    async def repost_saved_message(self, message_id: int, source_channel: str) -> bool:
        """
        Repost a saved message to all target chats.
        
        Args:
            message_id: The ID of the message to repost
            source_channel: The source channel ID
            
        Returns:
            bool: True if reposted to at least one chat, False otherwise
        """
        target_chats = await self.db.get_target_chats()
        
        if not target_chats:
            logger.warning("No target chats for reposting")
            return False
            
        success_count = 0
        invalid_message = False
        
        for chat_id in target_chats:
            try:
                # Check chat type
                chat = await self.bot.get_chat(chat_id)
                
                # Skip forwarding to the source channel itself
                if str(chat_id) == source_channel:
                    logger.info(f"Skipping repost to channel {chat_id}")
                    continue
                
                # Forward the message
                sent_message = await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=source_channel,
                    message_id=message_id
                )
                
                if sent_message:
                    await self.db.log_forward(message_id)
                    success_count += 1
                    logger.info(f"Successfully reposted to {chat.type} {chat.title} ({chat_id})")
            except Exception as e:
                error_text = str(e).lower()
                
                # Check if message is invalid
                if any(msg in error_text for msg in ["message_id_invalid", "message not found", "message to forward not found"]):
                    invalid_message = True
                    logger.error(f"Message {message_id} no longer exists in the channel")
                    break
                else:
                    logger.error(f"Error reposting to {chat_id}: {e}")
                    continue
        
        return success_count > 0
    
    async def find_last_valid_message(
        self, 
        source_channel: str, 
        start_from: int, 
        owner_id: int,
        max_lookback: int = 100
    ) -> Tuple[Optional[int], int]:
        """
        Find the last valid message in the channel by checking backwards.
        
        Args:
            source_channel: The source channel ID
            start_from: The message ID to start searching from
            owner_id: The owner's user ID for verification
            max_lookback: Maximum number of messages to check backwards
            
        Returns:
            Tuple[Optional[int], int]: (valid_message_id, checked_count)
        """
        valid_id = None
        checked_count = 0
        
        # Use binary search to find a valid message more efficiently
        low = max(1, start_from - max_lookback)
        high = start_from
        
        while low <= high and checked_count < max_lookback:
            mid = (low + high) // 2
            checked_count += 1
            
            try:
                # Check if the message exists
                await self.bot.forward_message(
                    chat_id=owner_id,
                    from_chat_id=source_channel,
                    message_id=mid
                )
                
                # Message exists, set as valid and search higher
                valid_id = mid
                low = mid + 1
            except Exception as e:
                error_text = str(e).lower()
                
                if any(msg in error_text for msg in ["message_id_invalid", "message not found", "message to forward not found"]):
                    # Message doesn't exist, search lower
                    high = mid - 1
                else:
                    # Other error, log and search lower
                    logger.warning(f"Unusual error checking message {mid}: {e}")
                    high = mid - 1
        
        # If we found a valid message during binary search, check sequentially from there
        if valid_id:
            # Search forward from the found valid ID to find the highest valid ID
            for msg_id in range(valid_id + 1, start_from + 1):
                checked_count += 1
                try:
                    await self.bot.forward_message(
                        chat_id=owner_id,
                        from_chat_id=source_channel,
                        message_id=msg_id
                    )
                    valid_id = msg_id
                except Exception:
                    break
        
        return valid_id, checked_count
    
    async def get_message_info(self, channel_id: str, message_id: int) -> Dict[str, Any]:
        """
        Get information about a specific message.
        
        Args:
            channel_id: The channel ID
            message_id: The message ID
            
        Returns:
            Dict: Message information
        """
        cache_key = f"{channel_id}:{message_id}"
        
        # Check cache first
        if cache_key in self._message_cache:
            return self._message_cache[cache_key]
        
        try:
            # Get the message
            message = await self.bot.copy_message(
                chat_id=self.bot.id,  # Copy to the bot itself
                from_chat_id=channel_id,
                message_id=message_id
            )
            
            # Extract information
            info = {
                "exists": True,
                "message_id": message_id,
                "channel_id": channel_id,
                "has_media": bool(message.photo or message.video or message.document or message.animation),
                "media_type": self._get_media_type(message),
                "text": message.text or message.caption or "",
                "entities": message.entities or message.caption_entities or []
            }
            
            # Cache the result
            self._message_cache[cache_key] = info
            
            return info
        except TelegramAPIError as e:
            # Message doesn't exist or can't be accessed
            logger.error(f"Failed to get message info for {channel_id}:{message_id}: {e}")
            
            info = {
                "exists": False,
                "message_id": message_id,
                "channel_id": channel_id,
                "error": str(e)
            }
            
            # Cache the negative result too
            self._message_cache[cache_key] = info
            
            return info
    
    def _get_media_type(self, message: types.Message) -> Optional[str]:
        """Determine the media type of a message."""
        if message.photo:
            return "photo"
        elif message.video:
            return "video"
        elif message.animation:
            return "animation"
        elif message.document:
            return "document"
        elif message.audio:
            return "audio"
        elif message.voice:
            return "voice"
        elif message.video_note:
            return "video_note"
        elif message.sticker:
            return "sticker"
        elif message.poll:
            return "poll"
        elif message.location:
            return "location"
        elif message.venue:
            return "venue"
        elif message.contact:
            return "contact"
        return None