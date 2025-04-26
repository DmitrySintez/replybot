"""
Main bot class implementing the Telegram channel forwarder functionality.

This module contains the core ForwarderBot class that orchestrates all
bot operations, including setup, message handling, and periodic reposting.
"""

import asyncio
from loguru import logger
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from src.config import Config
from src.db.manager import DatabaseManager
from src.handlers.commands import register_command_handlers
from src.handlers.callbacks import register_callback_handlers
from src.handlers.messages import MessageHandler
from src.utils.messaging import MessageProcessor


class ForwarderBot:
    """Main bot class implementing the channel message forwarding functionality."""
    
    def __init__(self):
        """Initialize the bot components and dependencies."""
        # Core components
        self.bot = Bot(token=Config.BOT_TOKEN, parse_mode=ParseMode.HTML)
        self.dp = Dispatcher()
        self.db = DatabaseManager()
        
        # State variables
        self.running = False
        self.repost_task = None
        
        # Specialized handlers
        self.message_processor = MessageProcessor(self.bot, self.db)
        self.message_handler = MessageHandler(
            bot=self.bot,
            db=self.db,
            message_processor=self.message_processor,
            source_channel=Config.SOURCE_CHANNEL,
            owner_id=Config.OWNER_ID,
        )

    async def start(self):
        """Start the bot and initialize all components."""
        logger.info("Initializing bot components")
        
        # Initialize database
        await self.db.init()
        
        # Verify channel access
        if not await self._verify_channel_access():
            logger.warning("Channel access verification incomplete or failed")
            # Continue anyway to allow setup/configuration
        
        # Register command handlers
        register_command_handlers(
            dp=self.dp,
            owner_id=Config.OWNER_ID,
            db=self.db,
            message_handler=self.message_handler,
            bot_instance=self,
        )
        
        # Register callback handlers
        register_callback_handlers(
            dp=self.dp, 
            owner_id=Config.OWNER_ID,
            db=self.db,
            bot_instance=self,
        )
        
        # Register message handlers
        self.dp.channel_post.register(self._handle_channel_post)
        self.dp.my_chat_member.register(self.message_handler.handle_chat_member)
        
        # Set default repost interval if not set
        current_interval = await self.db.get_config("repost_interval")
        if not current_interval:
            await self.db.set_config("repost_interval", str(Config.DEFAULT_REPOST_INTERVAL))
        
        # Register existing chats where bot is a member
        await self.message_handler.register_existing_chats()
        
        # Start polling for updates
        logger.info("Bot started, beginning polling")
        
        # Get the last update ID to avoid duplicate updates
        offset = await self._get_last_update_id()
        
        await self.dp.start_polling(self.bot, offset=offset)

    async def _verify_channel_access(self):
        """Verify the bot has proper access to the source channel."""
        try:
            # Try to get channel info
            channel = await self.bot.get_chat(Config.SOURCE_CHANNEL)
            logger.info(f"Successfully connected to channel: {channel.title} ({channel.id})")
            
            # Try to get channel member count to verify admin rights
            member_count = await self.bot.get_chat_member_count(Config.SOURCE_CHANNEL)
            logger.info(f"Channel member count: {member_count}")
            
            # Check bot's status in the channel
            bot_member = await self.bot.get_chat_member(Config.SOURCE_CHANNEL, self.bot.id)
            logger.info(f"Bot status in channel: {bot_member.status}")
            
            return True
            
        except TelegramAPIError as e:
            logger.error(f"Failed to access channel {Config.SOURCE_CHANNEL}: {e}")
            return False
    
    async def _get_last_update_id(self):
        """Get the last update ID to avoid processing old updates."""
        try:
            updates = await self.bot.get_updates(limit=1, timeout=1)
            if updates:
                return updates[-1].update_id + 1
            return 0
        except Exception as e:
            logger.warning(f"Failed to get initial updates: {e}")
            return 0

    async def _handle_channel_post(self, message):
        """Handle channel post messages for forwarding."""
        await self.message_handler.handle_channel_post(
            message=message, 
            running=self.running
        )

    async def start_forwarding(self, interval=None):
        """Start the periodic message forwarding task."""
        if self.running:
            return False
            
        self.running = True
        
        if interval is None:
            interval_str = await self.db.get_config("repost_interval", str(Config.DEFAULT_REPOST_INTERVAL))
            interval = int(interval_str)
        
        self.repost_task = asyncio.create_task(self._periodic_repost(interval))
        logger.info(f"Started forwarding with interval {interval} seconds")
        
        return True

    async def stop_forwarding(self):
        """Stop the periodic message forwarding task."""
        if not self.running:
            return False
        
        self.running = False
        
        if self.repost_task:
            self.repost_task.cancel()
            try:
                # Wait for the task to be cancelled
                await self.repost_task
            except asyncio.CancelledError:
                pass
            
        logger.info("Stopped forwarding")
        return True

    async def _periodic_repost(self, interval):
        """Periodically repost the last message from the source channel."""
        try:
            while True:
                await asyncio.sleep(interval)
                
                if not self.running:
                    break
                
                try:
                    # Trigger message repost
                    await self.message_handler.handle_channel_post(None, self.running)
                    logger.info("Triggered periodic repost")
                except Exception as e:
                    logger.error(f"Failed to repost message: {e}")
        
        except asyncio.CancelledError:
            logger.info("Periodic repost task cancelled")
            raise  # Re-raise to properly handle the cancellation
        except Exception as e:
            logger.exception(f"Error in periodic repost task: {e}")
            # Restart the task after a delay if bot is still running
            if self.running:
                logger.info("Restarting periodic repost task after error")
                await asyncio.sleep(10)  # Small delay to prevent rapid restarts
                self.repost_task = asyncio.create_task(self._periodic_repost(interval))