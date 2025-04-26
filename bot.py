import os
import asyncio
from loguru import logger
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from utils.config import Config
from utils.bot_state import BotContext, IdleState, RunningState
from utils.keyboard_factory import KeyboardFactory
from database.repository import Repository
from services.chat_cache import ChatCacheService, CacheObserver, ChatInfo
from commands.commands import (
    StartCommand,
    HelpCommand,
    SetLastMessageCommand,
    GetLastMessageCommand,
    ForwardNowCommand,
    TestMessageCommand,
    FindLastMessageCommand
)

class ForwarderBot(CacheObserver):
    """Main bot class with Observer pattern implementation"""
    
    def __init__(self):
        self.config = Config()
        self.bot = Bot(token=self.config.bot_token)
        self.dp = Dispatcher()
        self.context = BotContext(self.bot, self.config.source_channel)
        self.cache_service = ChatCacheService()
        
        # Register as cache observer
        self.cache_service.add_observer(self)
        
        self._setup_handlers()

    def _setup_handlers(self):
        """Initialize message handlers with Command pattern"""
        # Owner-only command handlers
        commands = {
            "start": StartCommand(
                self.config.owner_id,
                isinstance(self.context.state, RunningState)
            ),
            "help": HelpCommand(self.config.owner_id),
            "setlast": SetLastMessageCommand(
                self.config.owner_id,
                self.bot,
                self.config.source_channel
            ),
            "getlast": GetLastMessageCommand(
                self.config.owner_id,
                self.config.source_channel
            ),
            "forwardnow": ForwardNowCommand(
                self.config.owner_id,
                self.context
            ),
            "test": TestMessageCommand(
                self.config.owner_id,
                self.bot,
                self.config.source_channel
            ),
            "findlast": FindLastMessageCommand(
                self.config.owner_id,
                self.bot,
                self.config.source_channel
            )
        }
        
        for cmd_name, cmd_handler in commands.items():
            self.dp.message.register(cmd_handler.execute, Command(cmd_name))
        
        # Channel post handler
        self.dp.channel_post.register(self.handle_channel_post)
        
        # Callback query handlers
        callbacks = {
            "toggle_forward": self.toggle_forwarding,
            "interval_": self.set_interval,
            "remove_": self.remove_chat,
            "stats": self.show_stats,
            "list_chats": self.list_chats,
            "back_to_main": self.main_menu
        }
        
        for prefix, handler in callbacks.items():
            self.dp.callback_query.register(
                handler,
                lambda c, p=prefix: c.data.startswith(p)
            )
        
        # Handler for bot being added to chats
        self.dp.my_chat_member.register(self.handle_chat_member)

    async def on_cache_update(self, chat_id: int, info: ChatInfo) -> None:
        """Handle chat info cache updates"""
        logger.info(f"Chat info updated: {info.title} ({chat_id})")

    async def toggle_forwarding(self, callback: types.CallbackQuery):
        """Handler for forwarding toggle button"""
        if callback.from_user.id != self.config.owner_id:
            return

        if isinstance(self.context.state, IdleState):
            await self.context.start()
        else:
            await self.context.stop()

        await callback.message.edit_text(
            f"Forwarding {'Started' if isinstance(self.context.state, RunningState) else 'Stopped'}!",
            reply_markup=KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState)
            )
        )
        await callback.answer()

    async def set_interval(self, callback: types.CallbackQuery):
        """Handler for interval setting"""
        if callback.from_user.id != self.config.owner_id:
            return

        data = callback.data
        if data == "interval_menu":
            await callback.message.edit_text(
                "Select repost interval:",
                reply_markup=KeyboardFactory.create_interval_keyboard()
            )
        else:
            interval = int(data.split("_")[1])
            await Repository.set_config("repost_interval", str(interval))
            
            if isinstance(self.context.state, RunningState):
                await self.context.stop()
                await self.context.start()
            
            display = f"{interval//3600}h" if interval >= 3600 else f"{interval//60}m"
            await callback.message.edit_text(
                f"Interval set to {display}",
                reply_markup=KeyboardFactory.create_main_keyboard(
                    isinstance(self.context.state, RunningState)
                )
            )
        await callback.answer()

    async def remove_chat(self, callback: types.CallbackQuery):
        """Handler for chat removal"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        chat_id = int(callback.data.split("_")[1])
        await Repository.remove_target_chat(chat_id)
        self.cache_service.remove_from_cache(chat_id)
        await self.list_chats(callback)
        await callback.answer("Chat removed!")

    async def show_stats(self, callback: types.CallbackQuery):
        """Handler for statistics display"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        stats = await Repository.get_stats()
        text = (
            "📊 Forwarding Statistics\n\n"
            f"Total forwards: {stats['total_forwards']}\n"
            f"Last forward: {stats['last_forward'] or 'Never'}\n\n"
            "Last saved messages:\n"
        )
        
        if stats["last_messages"]:
            text += "\n".join(
                f"Channel: {channel_id}\n"
                f"Message ID: {data['message_id']}\n"
                f"Timestamp: {data['timestamp']}"
                for channel_id, data in stats["last_messages"].items()
            )
        else:
            text += "None"
        
        await callback.message.edit_text(
            text,
            reply_markup=KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState)
            )
        )
        await callback.answer()

    async def list_chats(self, callback: types.CallbackQuery):
        """Handler for chat listing"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        chats = await Repository.get_target_chats()
        chat_info = {}
        
        for chat_id in chats:
            info = await self.cache_service.get_chat_info(self.bot, chat_id)
            if info:
                chat_info[chat_id] = info.title
        
        if not chats:
            text = (
                "No target chats configured.\n"
                "Make sure to:\n"
                "1. Add bot to target chats\n"
                "2. Make bot admin in source channel"
            )
            markup = KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState)
            )
        else:
            text = "📡 Target Chats:\n\n"
            for chat_id, title in chat_info.items():
                text += f"• {title} ({chat_id})\n"
            markup = KeyboardFactory.create_chat_list_keyboard(chat_info)
        
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()

    async def main_menu(self, callback: types.CallbackQuery):
        """Handler for main menu button"""
        if callback.from_user.id != self.config.owner_id:
            return
            
        await callback.message.edit_text(
            "Main Menu:",
            reply_markup=KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState)
            )
        )
        await callback.answer()

    async def handle_channel_post(self, message: types.Message | None):
        """Handler for channel posts"""
        if message is None:
            await self.context.handle_message(
                await Repository.get_last_message(self.config.source_channel)
            )
            return

        chat_id = str(message.chat.id)
        username = message.chat.username
        
        is_source = (
            chat_id == self.config.source_channel or
            (username and username.lower() == self.config.source_channel.lower())
        )
            
        if not is_source:
            logger.info(f"Message not from source channel: {chat_id}/{username}")
            return
        
        await Repository.save_last_message(self.config.source_channel, message.message_id)
        await self.context.handle_message(message.message_id)

    async def handle_chat_member(self, update: types.ChatMemberUpdated):
        """Handler for bot being added/removed from chats"""
        if update.new_chat_member.user.id != self.bot.id:
            return

        chat_id = update.chat.id
        is_member = update.new_chat_member.status in ['member', 'administrator']
        
        if is_member and update.chat.type in ['group', 'supergroup']:
            await Repository.add_target_chat(chat_id)
            self.cache_service.remove_from_cache(chat_id)  # Force cache refresh
            await self._notify_owner(f"Bot added to {update.chat.type} {chat_id}")
        elif not is_member:
            await Repository.remove_target_chat(chat_id)
            self.cache_service.remove_from_cache(chat_id)
            await self._notify_owner(f"Bot removed from chat {chat_id}")

    async def _notify_owner(self, message: str):
        """Send notification to bot owner"""
        try:
            await self.bot.send_message(self.config.owner_id, message)
        except Exception as e:
            logger.error(f"Failed to notify owner: {e}")

    async def start(self):
        """Start the bot"""
        await Repository.init_db()
        
        # Set default interval if not set
        if not await Repository.get_config("repost_interval"):
            await Repository.set_config("repost_interval", "3600")
        
        logger.info("Bot started successfully!")
        try:
            # Get the last update ID to avoid duplicates
            offset = 0
            try:
                updates = await self.bot.get_updates(limit=1, timeout=1)
                if updates:
                    offset = updates[-1].update_id + 1
            except Exception as e:
                logger.warning(f"Failed to get initial updates: {e}")

            await self.dp.start_polling(self.bot, offset=offset)
        finally:
            self.cache_service.remove_observer(self)
            await self.bot.session.close()

async def main():
    """Main entry point with improved error handling"""
    lock_file = "bot.lock"
    
    if os.path.exists(lock_file):
        try:
            with open(lock_file, 'r') as f:
                pid = int(f.read().strip())
            
            import psutil
            if psutil.pid_exists(pid):
                logger.error(f"Another instance is running (PID: {pid})")
                return
            os.remove(lock_file)
        except Exception as e:
            logger.warning(f"Error handling lock file: {e}")
            os.remove(lock_file)

    try:
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))

        bot = ForwarderBot()
        await bot.start()
    finally:
        try:
            os.remove(lock_file)
        except Exception as e:
            logger.error(f"Failed to remove lock file: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot stopped due to error: {e}")
