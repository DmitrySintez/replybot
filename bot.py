from datetime import datetime
import os
import asyncio
import json
from loguru import logger
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Optional, List, Dict, Any


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
        self.context = BotContext(self.bot, self.config)
        self.cache_service = ChatCacheService()
        self.awaiting_channel_input = None  # Track if waiting for channel input
        
        # Register as cache observer
        self.cache_service.add_observer(self)
        
        self._setup_handlers()
    # Update in bot.py - ForwarderBot.add_channel_command method
    async def add_channel_command(self, message: types.Message):
        """Command to add a channel directly"""
        if message.from_user.id != self.config.owner_id:
            return
        
        args = message.text.split(maxsplit=1)
        if len(args) != 2:
            await message.reply(
                "Использование: /addchannel <channel_id_или_username>\n\n"
                "Примеры:\n"
                "• /addchannel -100123456789\n"
                "• /addchannel mychannel"
            )
            return
        
        channel = args[1].strip()
        
        if not channel:
            await message.reply("⚠️ ID/username канала не может быть пустым")
            return
        
        try:
            # Try to get basic info about the channel
            chat = await self.bot.get_chat(channel)
            
            # Check if bot is an admin in the channel
            bot_id = (await self.bot.get_me()).id
            member = await self.bot.get_chat_member(chat.id, bot_id)
            
            if member.status != "administrator":
                await message.reply(
                    "⚠️ Бот должен быть администратором канала для пересылки сообщений.\n"
                    "Пожалуйста, добавьте бота как администратора и попробуйте снова."
                )
                return
            
            # Add channel to configuration
            if self.config.add_source_channel(str(chat.id)):
                await message.reply(
                    f"✅ Канал успешно добавлен: {chat.title} ({chat.id})"
                )
                logger.info(f"Added channel: {chat.title} ({chat.id})")
                
                # Now find and save the latest message ID
                progress_msg = await message.reply(f"🔍 Ищу последнее сообщение в канале {chat.id}...")
                
                # Try to find the last message ID
                try:
                    # Start with a reasonably high message ID and try to access messages backwards
                    latest_id = None
                    start_id = 10000  # Start with a reasonable high number
                    
                    # Try to access more recent messages first
                    for test_id in range(start_id, 0, -1):
                        try:
                            # Just try to get message info, no need to forward
                            msg = await self.bot.get_messages(chat.id, test_id)
                            if msg and not msg.empty:
                                latest_id = test_id
                                break
                        except Exception:
                            # Skip errors for non-existent messages
                            pass
                        
                        # Add some progress updates
                        if test_id % 1000 == 0:
                            try:
                                await progress_msg.edit_text(f"🔍 Ищу сообщения... (проверено до ID {test_id})")
                            except:
                                pass
                    
                    if latest_id:
                        # Found a valid message, save it
                        await Repository.save_last_message(str(chat.id), latest_id)
                        await progress_msg.edit_text(f"✅ Найдено и сохранено последнее сообщение (ID: {latest_id}) в канале {chat.title}")
                    else:
                        await progress_msg.edit_text(f"⚠️ Не удалось найти сообщения в канале {chat.title}. Используйте /findlast {chat.id} вручную.")
                except Exception as e:
                    logger.error(f"Error finding latest message in channel {chat.id}: {e}")
                    await progress_msg.edit_text(f"⚠️ Ошибка при поиске последнего сообщения: {e}. Используйте /findlast {chat.id} вручную.")
                    
            else:
                await message.reply("⚠️ Этот канал уже настроен")
        
        except Exception as e:
            await message.reply(
                f"❌ Ошибка доступа к каналу: {e}\n\n"
                "Убедитесь что:\n"
                "• ID/username канала указан правильно\n"
                "• Бот является участником канала\n"
                "• Бот является администратором канала"
            )
            logger.error(f"Failed to add channel {channel}: {e}")

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
                self.bot
            ),
            "getlast": GetLastMessageCommand(
                self.config.owner_id
            ),
            "forwardnow": ForwardNowCommand(
                self.config.owner_id,
                self.context
            ),
            "test": TestMessageCommand(
                self.config.owner_id,
                self.bot
            ),
            "findlast": FindLastMessageCommand(
                self.config.owner_id,
                self.bot
            )
        }
        
        for cmd_name, cmd_handler in commands.items():
            self.dp.message.register(cmd_handler.execute, Command(cmd_name))
    
        self.dp.message.register(
            self.add_channel_submit,
            lambda message: message.from_user.id == self.awaiting_channel_input
        )
        # Register the direct add channel command
        self.dp.message.register(self.add_channel_command, Command("addchannel"))
        
        # Channel post handler
        self.dp.channel_post.register(self.handle_channel_post)
        
        # Callback query handlers
        # Update in _setup_handlers in bot.py
        callbacks = {
            "toggle_forward": self.toggle_forwarding,
            "toggle_auto_forward": self.toggle_auto_forward,
            "add_channel_input": self.add_channel_input,
            "interval_": self.set_interval,
            "interval_between_": self.set_interval,
            "set_interval_": self.set_interval,
            "remove_channel_": self.remove_channel,  # Specific handler for channel removal
            "remove_": self.remove_chat,  # Handler for chat removal
            "stats": self.show_stats,
            "list_chats": self.list_chats,
            "back_to_main": self.main_menu,
            "channels": self.manage_channels,
            "add_channel": self.add_channel_prompt,
            "channel_intervals": self.manage_channel_intervals,
        }
        
        # Register handlers with specific order to avoid conflicts
        for prefix, handler in callbacks.items():
            self.dp.callback_query.register(
                handler,
                lambda c, p=prefix: c.data.startswith(p)
            )
        
        # Handler for bot being added to chats
        self.dp.my_chat_member.register(self.handle_chat_member)


        # Add these methods to ForwarderBot class in bot.py
    # Make sure this method is in the ForwarderBot class
    async def find_latest_message(self, channel_id: str) -> Optional[int]:
        """Helper method to find the latest valid message ID in a channel"""
        try:
            # Try to access more recent messages first
            max_id = 10000  # Start with a reasonably high number
            
            for test_id in range(max_id, 0, -1):
                try:
                    # Try to get message info
                    msg = await self.bot.get_messages(chat_id=channel_id, message_ids=test_id)
                    if msg and not msg.empty:
                        return test_id
                except Exception:
                    # Skip errors for non-existent messages
                    pass
                    
                # Don't make too many attempts to avoid rate limits
                if test_id % 1000 == 0:
                    await asyncio.sleep(0.5)
                    
                # Don't check too many IDs
                if max_id - test_id > 5000:
                    break
                    
            return None
        except Exception as e:
            logger.error(f"Error finding latest message in channel {channel_id}: {e}")
            return None
    # Add this method to ForwarderBot class in bot.py
    async def find_last_message_handler(self, callback: types.CallbackQuery):
        """Handler for finding last message button"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        channel_id = callback.data.replace("findlast_", "")
        
        await callback.message.edit_text(
            f"🔍 Ищу последнее сообщение в канале {channel_id}...",
            reply_markup=None
        )
        
        try:
            latest_id = await self.find_latest_message(channel_id)
            
            if latest_id:
                await Repository.save_last_message(str(channel_id), latest_id)
                
                kb = InlineKeyboardBuilder()
                kb.button(text="Назад к каналам", callback_data="channels")
                
                await callback.message.edit_text(
                    f"✅ Найдено и сохранено последнее сообщение (ID: {latest_id}) в канале {channel_id}",
                    reply_markup=kb.as_markup()
                )
            else:
                kb = InlineKeyboardBuilder()
                kb.button(text="Назад к каналам", callback_data="channels")
                
                await callback.message.edit_text(
                    f"⚠️ Не удалось найти валидные сообщения в канале {channel_id}.",
                    reply_markup=kb.as_markup()
                )
        except Exception as e:
            kb = InlineKeyboardBuilder()
            kb.button(text="Назад к каналам", callback_data="channels")
            
            await callback.message.edit_text(
                f"❌ Ошибка при поиске последнего сообщения: {e}",
                reply_markup=kb.as_markup()
            )
        
        await callback.answer()
        
    async def add_channel_prompt(self, callback: types.CallbackQuery):
        """Improved prompt to add a channel"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        # Create a keyboard with buttons for common channel types
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Enter Channel ID/Username", callback_data="add_channel_input")
        kb.button(text="Back", callback_data="channels")
        kb.adjust(1)
        
        await callback.message.edit_text(
            "Please select an option to add a channel:\n\n"
            "• You can enter the channel ID (starts with -100...)\n"
            "• Or the channel username (without @)\n\n"
            "The bot must be an administrator in the channel.",
            reply_markup=kb.as_markup()
        )
        await callback.answer()

    async def add_channel_input(self, callback: types.CallbackQuery):
        """Handler for channel ID/username input"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        self.awaiting_channel_input = callback.from_user.id
        
        kb = InlineKeyboardBuilder()
        kb.button(text="Отмена", callback_data="channels")
        
        await callback.message.edit_text(
            "Пожалуйста, введите ID канала или username для добавления:\n\n"
            "• Для публичных каналов: введите username без @\n"
            "• Для приватных каналов: введите ID канала (начинается с -100...)\n\n"
            "Отправьте ID/username сообщением 💬",
            reply_markup=kb.as_markup()
        )
        await callback.answer()

    # Fix add_channel_submit method in ForwarderBot class in bot.py
    async def add_channel_submit(self, message: types.Message):
        """Handler for direct channel input message"""
        if message.from_user.id != self.config.owner_id:
            return
        
        channel = message.text.strip()
        
        if not channel:
            await message.reply("⚠️ ID/username канала не может быть пустым")
            return
        
        self.awaiting_channel_input = None
        
        progress_msg = await message.reply("🔄 Проверяю доступ к каналу...")
        
        try:
            chat = await self.bot.get_chat(channel)
            
            bot_id = (await self.bot.get_me()).id
            member = await self.bot.get_chat_member(chat.id, bot_id)
            
            if member.status != "administrator":
                kb = InlineKeyboardBuilder()
                kb.button(text="Назад к каналам", callback_data="channels")
                
                await progress_msg.edit_text(
                    "⚠️ Бот должен быть администратором канала.\n"
                    "Пожалуйста, добавьте бота как администратора и попробуйте снова.",
                    reply_markup=kb.as_markup()
                )
                return
            
            if self.config.add_source_channel(str(chat.id)):
                await progress_msg.edit_text(f"✅ Добавлен канал: {chat.title} ({chat.id})\n\n🔍 Теперь ищу последнее сообщение...")
                
                try:
                    latest_id = await self.find_latest_message(str(chat.id))
                    
                    if latest_id:
                        await Repository.save_last_message(str(chat.id), latest_id)
                        
                        kb = InlineKeyboardBuilder()
                        kb.button(text="Назад к каналам", callback_data="channels")
                        
                        await progress_msg.edit_text(
                            f"✅ Добавлен канал: {chat.title} ({chat.id})\n"
                            f"✅ Найдено и сохранено последнее сообщение (ID: {latest_id})",
                            reply_markup=kb.as_markup()
                        )
                    else:
                        kb = InlineKeyboardBuilder()
                        kb.button(text="Назад к каналам", callback_data="channels")
                        
                        await progress_msg.edit_text(
                            f"✅ Добавлен канал: {chat.title} ({chat.id})\n"
                            f"⚠️ Не удалось найти валидные сообщения. Будет использоваться следующее сообщение в канале.",
                            reply_markup=kb.as_markup()
                        )
                except Exception as e:
                    logger.error(f"Error finding latest message: {e}")
                    
                    kb = InlineKeyboardBuilder()
                    kb.button(text="Назад к каналам", callback_data="channels")
                    
                    await progress_msg.edit_text(
                        f"✅ Добавлен канал: {chat.title} ({chat.id})\n"
                        f"⚠️ Ошибка при поиске последнего сообщения.",
                        reply_markup=kb.as_markup()
                    )
            else:
                kb = InlineKeyboardBuilder()
                kb.button(text="Назад к каналам", callback_data="channels")
                
                await progress_msg.edit_text(
                    f"⚠️ Канал {chat.title} уже настроен.",
                    reply_markup=kb.as_markup()
                )
        except Exception as e:
            kb = InlineKeyboardBuilder()
            kb.button(text="Назад к каналам", callback_data="channels")
            
            await progress_msg.edit_text(
                f"❌ Ошибка доступа к каналу: {e}\n\n"
                "Убедитесь что:\n"
                "• ID/username канала указан правильно\n"
                "• Бот является участником канала\n"
                "• Бот является администратором канала",
                reply_markup=kb.as_markup()
            )
            logger.error(f"Failed to add channel {channel}: {e}")


    # In bot.py - Check if this method exists and is at the correct indentation level
    async def toggle_auto_forward(self, callback: types.CallbackQuery):
        """Handler for auto-forward toggle button"""
        if callback.from_user.id != self.config.owner_id:
            return

        if isinstance(self.context.state, RunningState):
            await self.context.state.toggle_auto_forward()
            await callback.message.edit_text(
                "Main Menu:",
                reply_markup=KeyboardFactory.create_main_keyboard(
                    True, 
                    self.context.state.auto_forward
                )
            )
        else:
            await callback.answer("Start forwarding first to enable auto-forward")
        
        await callback.answer()
    # Add to the ForwarderBot class in bot.py
    async def toggle_forwarding(self, callback: types.CallbackQuery):
        """Handler for forwarding toggle button"""
        if callback.from_user.id != self.config.owner_id:
            return

        if isinstance(self.context.state, IdleState):
            await self.context.start()
        else:
            await self.context.stop()

        await callback.message.edit_text(
            f"Пересылка {'начата' if isinstance(self.context.state, RunningState) else 'остановлена'}!",
            reply_markup=KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState),
                isinstance(self.context.state, RunningState) and self.context.state.auto_forward
            )
        )
        await callback.answer()

    # Add these methods to ForwarderBot
    async def manage_channel_intervals(self, callback: types.CallbackQuery):
        """Manager for channel intervals"""
        if callback.from_user.id != self.config.owner_id:
            return
            
        source_channels = self.config.source_channels
        
        if len(source_channels) < 2:
            await callback.message.edit_text(
                "Вам нужно минимум 2 канала для установки интервалов между ними.",
                reply_markup=InlineKeyboardBuilder().button(
                    text="Назад", callback_data="channels"
                ).as_markup()
            )
            await callback.answer()
            return
        
        kb = InlineKeyboardBuilder()
        for i, channel in enumerate(source_channels):
            if i < len(source_channels) - 1:
                next_channel = source_channels[i + 1]
                try:
                    chat1 = await self.bot.get_chat(channel)
                    chat2 = await self.bot.get_chat(next_channel)
                    name1 = (chat1.title or channel)[:8]
                    name2 = (chat2.title or next_channel)[:8]
                except Exception:
                    name1 = channel[:8]
                    name2 = next_channel[:8]
                
                kb.button(
                    text=f"⏱️ {name1} → {name2}",
                    callback_data=f"interval_between_{channel}_{next_channel}"
                )
        
        kb.button(text="Назад", callback_data="channels")
        kb.adjust(1)
        
        await callback.message.edit_text(
            "Выберите пару каналов для установки интервала пересылки:",
            reply_markup=kb.as_markup()
        )
        await callback.answer()

    async def set_channel_interval_prompt(self, callback: types.CallbackQuery):
        """Prompt for setting interval between channels"""
        if callback.from_user.id != self.config.owner_id:
            return
            
        # Parse the channel IDs from callback data
        parts = callback.data.split('_')
        if len(parts) >= 4:
            channel1 = parts[2]
            channel2 = parts[3]
            
            # Get channel names for display
            try:
                chat1 = await self.bot.get_chat(channel1)
                chat2 = await self.bot.get_chat(channel2)
                name1 = chat1.title or channel1
                name2 = chat2.title or channel2
            except Exception:
                name1 = channel1
                name2 = channel2
            
            await callback.message.edit_text(
                f"Set interval between forwarding from:\n"
                f"{name1} → {name2}",
                reply_markup=KeyboardFactory.create_channel_interval_options(channel1, channel2)
            )
            await callback.answer()
        else:
            await callback.answer("Invalid channel selection")

    async def set_channel_interval(self, callback: types.CallbackQuery):
        """Set interval between two channels"""
        if callback.from_user.id != self.config.owner_id:
            return
            
        # Parse data: set_interval_channel1_channel2_seconds
        parts = callback.data.split('_')
        if len(parts) >= 5:
            channel1 = parts[2]
            channel2 = parts[3]
            interval = int(parts[4])
            
            # Save the interval
            await Repository.set_channel_interval(channel1, channel2, interval)
            
            # Format interval for display
            display = f"{interval//3600}h" if interval >= 3600 else f"{interval//60}m"
            
            # Get channel names for display
            try:
                chat1 = await self.bot.get_chat(channel1)
                chat2 = await self.bot.get_chat(channel2)
                name1 = chat1.title or channel1
                name2 = chat2.title or channel2
            except Exception:
                name1 = channel1
                name2 = channel2
            
            await callback.message.edit_text(
                f"✅ Interval set to {display} between:\n"
                f"{name1} → {name2}",
                reply_markup=InlineKeyboardBuilder().button(
                    text="Back to Intervals", callback_data="channel_intervals"
                ).as_markup()
            )
            await callback.answer()
        else:
            await callback.answer("Invalid interval selection")


    async def set_interval(self, callback: types.CallbackQuery):
        """Handler for interval setting"""
        if callback.from_user.id != self.config.owner_id:
            return

        data = callback.data
        
        if "interval_between_" in data:
            channel_parts = data.split('_')
            if len(channel_parts) >= 4:
                channel1 = channel_parts[2]
                channel2 = channel_parts[3]
                
                try:
                    chat1 = await self.bot.get_chat(channel1)
                    chat2 = await self.bot.get_chat(channel2)
                    name1 = chat1.title or channel1
                    name2 = chat2.title or channel2
                except Exception:
                    name1 = channel1
                    name2 = channel2
                
                await callback.message.edit_text(
                    f"Установите интервал между пересылкой из:\n"
                    f"{name1} → {name2}",
                    reply_markup=KeyboardFactory.create_channel_interval_options(channel1, channel2)
                )
                await callback.answer()
            else:
                await callback.answer("Неверный выбор канала")
        elif "set_interval_" in data:
            parts = data.split('_')
            if len(parts) >= 5:
                channel1 = parts[2]
                channel2 = parts[3]
                interval = int(parts[4])
                
                await Repository.set_channel_interval(channel1, channel2, interval)
                
                display = f"{interval//3600}ч" if interval >= 3600 else f"{interval//60}м"
                
                try:
                    chat1 = await self.bot.get_chat(channel1)
                    chat2 = await self.bot.get_chat(channel2)
                    name1 = chat1.title or channel1
                    name2 = chat2.title or channel2
                except Exception:
                    name1 = channel1
                    name2 = channel2
                
                await callback.message.edit_text(
                    f"✅ Интервал установлен на {display} между:\n"
                    f"{name1} → {name2}",
                    reply_markup=InlineKeyboardBuilder().button(
                        text="Назад к интервалам", callback_data="channel_intervals"
                    ).as_markup()
                )
                await callback.answer()
            else:
                await callback.answer("Неверный выбор интервала")
        
        # Regular global interval setting
        if data == "interval_menu":
            await callback.message.edit_text(
                "Выберите интервал повторной отправки:",
                reply_markup=KeyboardFactory.create_interval_keyboard()
            )
        elif data.startswith("interval_") and not "between" in data and not "menu" in data:
            try:
                interval = int(data.split("_")[1])
                
                await Repository.set_config("repost_interval", str(interval))
                
                if isinstance(self.context.state, RunningState):
                    self.context.state.interval = interval
                    
                    now = datetime.now().timestamp()
                    for channel in self.context.config.source_channels:
                        self.context.state._channel_last_post[channel] = now
                    
                    self.context.state._last_global_post_time = now
                    
                    display = f"{interval//3600}ч" if interval >= 3600 else f"{interval//60}м"
                    await callback.message.edit_text(
                        f"Интервал установлен на {display}. Первая отправка произойдет через этот интервал.",
                        reply_markup=KeyboardFactory.create_main_keyboard(
                            True, 
                            self.context.state.auto_forward
                        )
                    )
                    
                    logger.info(f"Установлен интервал пересылки {interval} секунд ({interval//60} минут)")
                else:
                    display = f"{interval//3600}ч" if interval >= 3600 else f"{interval//60}м"
                    await callback.message.edit_text(
                        f"Интервал установлен на {display}",
                        reply_markup=KeyboardFactory.create_main_keyboard(
                            False, 
                            False
                        )
                    )
            except Exception as e:
                logger.error(f"Ошибка установки интервала: {e}")
                await callback.answer("Ошибка установки интервала")
    

    async def remove_chat(self, callback: types.CallbackQuery):
        """Handler for chat removal"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        # Check if this is for removing a chat, not a channel
        if not callback.data.startswith("remove_") or callback.data.startswith("remove_channel_"):
            await callback.answer("Эта команда только для удаления чатов")
            return
        
        try:
            chat_id = int(callback.data.split("_")[1])
            await Repository.remove_target_chat(chat_id)
            self.cache_service.remove_from_cache(chat_id)
            await self.list_chats(callback)
            await callback.answer("Чат удален!")
        except ValueError:
            await callback.answer("Ошибка при удалении чата")
            logger.error(f"Invalid chat_id in callback data: {callback.data}")

    async def show_stats(self, callback: types.CallbackQuery):
        """Handler for statistics display"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        stats = await Repository.get_stats()
        text = (
            "📊 Статистика пересылки\n\n"
            f"Всего пересылок: {stats['total_forwards']}\n"
            f"Последняя пересылка: {stats['last_forward'] or 'Никогда'}\n\n"
            "Последние сохраненные сообщения:\n"
        )
        
        if stats["last_messages"]:
            text += "\n".join(
                f"Канал: {channel_id}\n"
                f"ID сообщения: {data['message_id']}\n"
                f"Время: {data['timestamp']}"
                for channel_id, data in stats["last_messages"].items()
            )
        else:
            text += "Нет"
        
        await callback.message.edit_text(
            text,
            reply_markup=KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState),
                isinstance(self.context.state, RunningState) and self.context.state.auto_forward
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
                "Нет настроенных целевых чатов.\n"
                "Убедитесь, что:\n"
                "1. Бот добавлен в целевые чаты\n"
                "2. Бот является администратором в исходных каналах"
            )
            markup = KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState),
                isinstance(self.context.state, RunningState) and self.context.state.auto_forward
            )
        else:
            text = "📡 Целевые чаты:\n\n"
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
                isinstance(self.context.state, RunningState),
                isinstance(self.context.state, RunningState) and self.context.state.auto_forward
            )
        )
        await callback.answer()
    # # Add to ForwarderBot class in bot.py
    # async def manage_single_channel(self, callback: types.CallbackQuery):
    #     """Combined management for a single channel"""
    #     if callback.from_user.id != self.config.owner_id:
    #         return
        
    #     channel_id = callback.data.replace("manage_channel_", "")
        
    #     try:
    #         chat = await self.bot.get_chat(channel_id)
    #         channel_name = chat.title or channel_id
    #     except:
    #         channel_name = channel_id
        
    #     kb = InlineKeyboardBuilder()
    #     kb.button(text="🔍 Найти последнее сообщение", callback_data=f"findlast_{channel_id}")
    #     kb.button(text="❌ Удалить канал", callback_data=f"remove_channel_{channel_id}")
    #     kb.button(text="Назад к каналам", callback_data="channels")
    #     kb.adjust(1)
        
    #     await callback.message.edit_text(
    #         f"Управление каналом: {channel_name}\n\n"
    #         f"Выберите действие:",
    #         reply_markup=kb.as_markup()
    #     )
    #     await callback.answer()
    # Update manage_channels method in ForwarderBot class
    async def manage_channels(self, callback: types.CallbackQuery):
        """Channel management menu"""
        if callback.from_user.id != self.config.owner_id:
            return
                
        # Reset any channel input state
        self.awaiting_channel_input = None
        
        source_channels = self.config.source_channels
        
        if not source_channels:
            text = (
                "Нет настроенных исходных каналов.\n"
                "Добавьте канал, нажав кнопку ниже."
            )
        else:
            text = "📡 Исходные каналы:\n\n"
            for channel in source_channels:
                # Try to get chat info for better display
                try:
                    chat = await self.bot.get_chat(channel)
                    if chat.title:
                        text += f"• {chat.title} ({channel})\n"
                    else:
                        text += f"• {channel}\n"
                except Exception:
                    text += f"• {channel}\n"
        
        # Use KeyboardFactory to create management keyboard
        markup = KeyboardFactory.create_channel_management_keyboard(source_channels)
        
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()

    async def add_channel_prompt(self, callback: types.CallbackQuery):
        """Improved prompt to add a channel without command"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        # Set state to wait for channel input
        self.awaiting_channel_input = callback.from_user.id
        
        # Create a keyboard with cancel button
        kb = InlineKeyboardBuilder()
        kb.button(text="Отмена", callback_data="channels")
        
        await callback.message.edit_text(
            "Введите ID канала или его username для добавления:\n\n"
            "• Для публичных каналов: введите username без @\n"
            "• Для приватных каналов: введите ID канала (начинается с -100...)\n\n"
            "Просто отправьте ID канала или username сообщением 💬",
            reply_markup=kb.as_markup()
        )
        await callback.answer()

    async def add_channel_handler(self, message: types.Message):
        """Handle channel addition from user input"""
        logger.info(f"Received channel addition message: {message.text}")
        channel = message.text.strip()
        
        if not channel:
            await message.reply("⚠️ Channel ID/username cannot be empty")
            return
            
        # Verify that bot can access the channel
        try:
            # Try to get basic info about the channel
            chat = await self.bot.get_chat(channel)
            
            # Check if bot is an admin in the channel
            bot_id = (await self.bot.get_me()).id
            member = await self.bot.get_chat_member(chat.id, bot_id)
            
            if member.status != "administrator":
                await message.reply(
                    "⚠️ Bot must be an administrator in the channel to forward messages.\n"
                    "Please add the bot as admin and try again."
                )
                return
                
            # Add channel to configuration
            if self.config.add_source_channel(str(chat.id)):
                await message.reply(
                    f"✅ Successfully added channel: {chat.title} ({chat.id})"
                )
                logger.info(f"Added channel: {chat.title} ({chat.id})")
            else:
                await message.reply("⚠️ This channel is already configured")
                
        except Exception as e:
            await message.reply(
                f"❌ Error accessing channel: {e}\n\n"
                "Make sure:\n"
                "• The channel ID/username is correct\n"
                "• The bot is a member of the channel\n"
                "• The bot is an administrator in the channel"
            )
            logger.error(f"Failed to add channel {channel}: {e}")

    async def remove_channel(self, callback: types.CallbackQuery):
        """Remove a source channel"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        # Extract channel ID from callback data
        if not callback.data.startswith("remove_channel_"):
            await callback.answer("Неверный формат данных")
            return
        
        channel = callback.data.replace("remove_channel_", "")
        
        if self.config.remove_source_channel(channel):
            await callback.answer("Канал успешно удален")
        else:
            await callback.answer("Не удалось удалить канал")
        
        await self.manage_channels(callback)

    async def handle_channel_post(self, message: types.Message | None):
        """Handler for channel posts"""
        if message is None:
            return
            
        chat_id = str(message.chat.id)
        username = message.chat.username
        source_channels = self.config.source_channels
            
        is_source = False
        for channel in source_channels:
            if channel == chat_id or (username and channel.lower() == username.lower()):
                is_source = True
                break
                
        if not is_source:
            logger.info(f"Сообщение не из канала-источника: {chat_id}/{username}")
            return
        
        await Repository.save_last_message(chat_id, message.message_id)
        
        if isinstance(self.context.state, RunningState):
            await self.context.handle_message(chat_id, message.message_id)
            logger.info(f"Пересылка сообщения {message.message_id} из {chat_id} во все целевые чаты")
        else:
            logger.info("Бот не запущен, игнорирую сообщение")

    async def handle_chat_member(self, update: types.ChatMemberUpdated):
        """Handler for bot being added/removed from chats"""
        if update.new_chat_member.user.id != self.bot.id:
            return

        chat_id = update.chat.id
        is_member = update.new_chat_member.status in ['member', 'administrator']
        
        if is_member and update.chat.type in ['group', 'supergroup']:
            await Repository.add_target_chat(chat_id)
            self.cache_service.remove_from_cache(chat_id)
            await self._notify_owner(f"Бот добавлен в {update.chat.type}: {update.chat.title} ({chat_id})")
            logger.info(f"Бот добавлен в {update.chat.type}: {update.chat.title} ({chat_id})")
        elif not is_member:
            await Repository.remove_target_chat(chat_id)
            self.cache_service.remove_from_cache(chat_id)
            await self._notify_owner(f"Бот удален из чата {chat_id}")
            logger.info(f"Бот удален из чата {chat_id}")

    async def _notify_owner(self, message: str):
        """Send notification to bot owner"""
        try:
            await self.bot.send_message(self.config.owner_id, message)
        except Exception as e:
            logger.error(f"Не удалось уведомить владельца: {e}")

    async def start(self):
        """Start the bot"""
        await Repository.init_db()
        
        # Set default interval if not set
        if not await Repository.get_config("repost_interval"):
            await Repository.set_config("repost_interval", "3600")
        
        logger.info("Бот успешно запущен!")
        try:
            # Get the last update ID to avoid duplicates
            offset = 0
            try:
                updates = await self.bot.get_updates(limit=1, timeout=1)
                if updates:
                    offset = updates[-1].update_id + 1
            except Exception as e:
                logger.warning(f"Не удалось получить начальные обновления: {e}")

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
                logger.info("Cleaned up stale lock file")
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
                await Repository.close_db()  # Ensure DB is closed even if shutdown fails
                os.remove(lock_file)
            except Exception as e:
                logger.error(f"Failed to remove lock file: {e}")

    # Update the main section at the bottom of bot.py
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Бот остановлен из-за ошибки: {e}")