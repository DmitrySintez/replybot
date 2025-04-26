"""
Message handling functionality for the Telegram Forwarder Bot.

This module handles channel posts, chat member updates, and message reposting logic.
"""

from typing import Optional, Union, List
from loguru import logger
from aiogram import Bot, types
from aiogram.exceptions import TelegramAPIError

from src.config import Config
from src.db.manager import DatabaseManager
from src.utils.messaging import MessageProcessor


class MessageHandler:
    """Handles all message-related operations including forwarding and reposting."""
    
    def __init__(
        self, 
        bot: Bot, 
        db: DatabaseManager, 
        message_processor: MessageProcessor,
        source_channel: str, 
        owner_id: int
    ):
        """Initialize the message handler with required dependencies."""
        self.bot = bot
        self.db = db
        self.message_processor = message_processor
        self.source_channel = source_channel
        self.owner_id = owner_id
    
    async def handle_channel_post(
        self, 
        message: Optional[types.Message], 
        running: bool
    ) -> bool:
        """
        Handle channel posts for forwarding.
        
        Args:
            message: The message to forward, or None for reposting the last message
            running: Whether the bot is currently running
            
        Returns:
            bool: True if the operation was successful, False otherwise
        """
        if not running:
            logger.info("Bot is not running, ignoring post")
            return False

        # Case 1: Periodic reposting (message is None)
        if message is None:
            return await self._handle_periodic_repost()
            
        # Case 2: New message from source channel
        return await self._handle_new_channel_message(message)
    
    async def _handle_periodic_repost(self) -> bool:
        """Handle periodic reposting of the last saved message."""
        # Get the last message ID for the source channel
        last_message_id = await self.db.get_last_message(self.source_channel)
        
        if not last_message_id:
            logger.warning(f"No saved last message ID for channel {self.source_channel}")
            return False
            
        logger.info(f"Reposting message ID: {last_message_id} from channel {self.source_channel}")
        
        # Repost the message to all target chats
        return await self.message_processor.repost_saved_message(
            message_id=last_message_id, 
            source_channel=self.source_channel
        )

    async def _handle_new_channel_message(self, message: types.Message) -> bool:
        """Handle a new message from a channel."""
        # Check if the message is from the source channel
        chat_id = str(message.chat.id)
        username = message.chat.username
        
        is_source = (
            chat_id == self.source_channel or 
            (username and username.lower() == self.source_channel.lower())
        )
            
        if not is_source:
            logger.info(f"Message not from source channel. Got {chat_id}/{username}, expected {self.source_channel}")
            return False
        
        logger.info(f"Forwarding channel post {message.message_id} to all target chats")
        
        # Save this message ID as the latest from the channel
        await self.db.save_last_message(self.source_channel, message.message_id)
        
        # Forward the message to all target chats
        return await self.message_processor.forward_message_to_all_targets(message)
    
    async def handle_chat_member(self, update: types.ChatMemberUpdated) -> None:
        """Handle updates to chat membership for the bot."""
        logger.info(
            f"Chat member update in {update.chat.id} ({update.chat.type}): "
            f"from {update.old_chat_member.status} to {update.new_chat_member.status}"
        )

        # Only handle updates about the bot itself
        if update.new_chat_member.user.id != self.bot.id:
            return

        # Handle addition and removal
        if update.new_chat_member.status in ['member', 'administrator']:
            # Only register groups and supergroups, not channels
            if update.chat.type in ['group', 'supergroup']:
                await self._handle_bot_added(update.chat)
        elif update.new_chat_member.status in ['left', 'kicked']:
            # Bot was removed
            await self._handle_bot_removed(update.chat)
    
    async def _handle_bot_added(self, chat: types.Chat) -> None:
        """Handle the bot being added to a chat."""
        await self.db.add_target_chat(chat.id)
        
        chat_info = await self.bot.get_chat(chat.id)
        logger.info(f"Bot added to {chat.type}: {chat_info.title} ({chat.id})")
        
        # Notify owner about the addition
        try:
            await self.bot.send_message(
                self.owner_id,
                f"✅ Bot added to {chat.type} {chat_info.title} ({chat.id})"
            )
        except Exception as e:
            logger.error(f"Failed to notify owner about bot addition: {e}")
    
    async def _handle_bot_removed(self, chat: types.Chat) -> None:
        """Handle the bot being removed from a chat."""
        await self.db.remove_target_chat(chat.id)
        logger.info(f"Bot removed from chat {chat.id}")
        
        # Notify owner about the removal
        try:
            await self.bot.send_message(
                self.owner_id,
                f"❌ Bot removed from {chat.type} {chat.id}"
            )
        except Exception as e:
            logger.error(f"Failed to notify owner about bot removal: {e}")
    
    async def find_last_valid_message(self, start_id: Optional[int] = None) -> tuple:
        """
        Find the last valid message in the source channel.
        
        Args:
            start_id: Optional starting message ID to search from
            
        Returns:
            tuple: (valid_message_id, checked_count)
        """
        if start_id is None:
            # Get current last message ID
            start_id = await self.db.get_last_message(self.source_channel)
            
        if not start_id:
            logger.warning("No starting ID for finding last valid message")
            return None, 0
        
        return await self.message_processor.find_last_valid_message(
            source_channel=self.source_channel,
            start_from=start_id,
            owner_id=self.owner_id,
            max_lookback=Config.MAX_FIND_LAST_LOOKBACK
        )
    
    async def register_existing_chats(self) -> List[int]:
        """
        Discover and register chats where the bot is already a member.
        
        Returns:
            List[int]: List of registered chat IDs
        """
        registered_chats = []
        
        try:
            # Get list of bot's existing chats
            updates = await self.bot.get_updates(limit=100, timeout=1)
            
            for update in updates:
                if update.my_chat_member and update.my_chat_member.chat.id not in registered_chats:
                    chat = update.my_chat_member.chat
                    
                    # Only register groups and supergroups
                    if chat.type in ['group', 'supergroup']:
                        # Check if the bot is still a member
                        try:
                            me = await self.bot.get_chat_member(chat.id, self.bot.id)
                            if me.status in ['member', 'administrator']:
                                await self.db.add_target_chat(chat.id)
                                logger.info(f"Registered existing chat: {chat.title} ({chat.id})")
                                registered_chats.append(chat.id)
                        except TelegramAPIError:
                            # Bot is not a member of this chat anymore
                            continue
            
            logger.info(f"Registered {len(registered_chats)} existing chats")
            return registered_chats
            
        except Exception as e:
            logger.error(f"Error registering existing chats: {e}")
            return []