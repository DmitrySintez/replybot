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
                "Usage: /addchannel <channel_id_or_username>\n\n"
                "Examples:\n"
                "• /addchannel -100123456789\n"
                "• /addchannel mychannel"
            )
            return
        
        channel = args[1].strip()
        
        if not channel:
            await message.reply("⚠️ Channel ID/username cannot be empty")
            return
        
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
                
                # Now find and save the latest message ID
                progress_msg = await message.reply(f"🔍 Searching for latest message in channel {chat.id}...")
                
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
                                await progress_msg.edit_text(f"🔍 Searching for messages... (checked up to ID {test_id})")
                            except:
                                pass
                    
                    if latest_id:
                        # Found a valid message, save it
                        await Repository.save_last_message(str(chat.id), latest_id)
                        await progress_msg.edit_text(f"✅ Found and saved latest message (ID: {latest_id}) in channel {chat.title}")
                    else:
                        await progress_msg.edit_text(f"⚠️ Could not find any messages in channel {chat.title}. Please use /findlast {chat.id} manually.")
                except Exception as e:
                    logger.error(f"Error finding latest message in channel {chat.id}: {e}")
                    await progress_msg.edit_text(f"⚠️ Error finding latest message: {e}. Please use /findlast {chat.id} manually.")
                    
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
        
        except Exception as e:
            await message.reply(
                f"❌ Error accessing channel: {e}\n\n"
                "Make sure:\n"
                "• The channel ID/username is correct\n"
                "• The bot is a member of the channel\n"
                "• The bot is an administrator in the channel"
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
            "findlast_": self.find_last_message_handler,
            "add_channel_input": self.add_channel_input,  # Add this
            "manage_channel_": self.manage_single_channel,  # Add this
            "interval_": self.set_interval,
            "interval_between_": self.set_interval,
            "set_interval_": self.set_interval,
            "remove_": self.remove_chat,
            "stats": self.show_stats,
            "list_chats": self.list_chats,
            "back_to_main": self.main_menu,
            "channels": self.manage_channels,
            "add_channel": self.add_channel_prompt,
            "remove_channel_": self.remove_channel,
            "channel_intervals": self.manage_channel_intervals,
        }
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
        
        # Extract channel ID from callback data
        channel_id = callback.data.replace("findlast_", "")
        
        # Update message to show progress
        await callback.message.edit_text(
            f"🔍 Searching for latest message in channel {channel_id}...",
            reply_markup=None
        )
        
        try:
            # Find last valid message
            latest_id = await self.find_latest_message(channel_id)
            
            if latest_id:
                # Found a valid message, save it
                await Repository.save_last_message(str(channel_id), latest_id)
                
                # Show success message with back button
                kb = InlineKeyboardBuilder()
                kb.button(text="Back to Channels", callback_data="channels")
                
                await callback.message.edit_text(
                    f"✅ Found and saved latest message (ID: {latest_id}) in channel {channel_id}",
                    reply_markup=kb.as_markup()
                )
            else:
                # No valid message found
                kb = InlineKeyboardBuilder()
                kb.button(text="Back to Channels", callback_data="channels")
                
                await callback.message.edit_text(
                    f"⚠️ Could not find any valid messages in channel {channel_id}.",
                    reply_markup=kb.as_markup()
                )
        except Exception as e:
            # Show error message with back button
            kb = InlineKeyboardBuilder()
            kb.button(text="Back to Channels", callback_data="channels")
            
            await callback.message.edit_text(
                f"❌ Error finding latest message: {e}",
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
        
        # Register a message handler for the next message
        self.awaiting_channel_input = callback.from_user.id
        
        # Create a keyboard with cancel button
        kb = InlineKeyboardBuilder()
        kb.button(text="Cancel", callback_data="channels")
        
        await callback.message.edit_text(
            "Please enter the channel ID or username to add:\n\n"
            "• For public channels: Enter the username without @\n"
            "• For private channels: Enter the channel ID (starts with -100...)\n\n"
            "Send the ID/username as a message 💬",
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
            await message.reply("⚠️ Channel ID/username cannot be empty")
            return
        
        # Reset awaiting state
        self.awaiting_channel_input = None
        
        # Create progress message
        progress_msg = await message.reply("🔄 Checking channel access...")
        
        try:
            # Try to get basic info about the channel
            chat = await self.bot.get_chat(channel)
            
            # Check if bot is an admin in the channel
            bot_id = (await self.bot.get_me()).id
            member = await self.bot.get_chat_member(chat.id, bot_id)
            
            if member.status != "administrator":
                # Not an admin, show error
                kb = InlineKeyboardBuilder()
                kb.button(text="Back to Channels", callback_data="channels")
                
                await progress_msg.edit_text(
                    "⚠️ Bot must be an administrator in the channel.\n"
                    "Please add the bot as admin and try again.",
                    reply_markup=kb.as_markup()
                )
                return
            
            # Add channel to configuration
            if self.config.add_source_channel(str(chat.id)):
                # Update progress for finding latest message
                await progress_msg.edit_text(f"✅ Added channel: {chat.title} ({chat.id})\n\n🔍 Now searching for latest message...")
                
                # Try to find the last message ID using the bot's method, not config's
                try:
                    latest_id = await self.find_latest_message(str(chat.id))
                    
                    if latest_id:
                        # Found a valid message, save it
                        await Repository.save_last_message(str(chat.id), latest_id)
                        
                        # Show success with back button
                        kb = InlineKeyboardBuilder()
                        kb.button(text="Back to Channels", callback_data="channels")
                        
                        await progress_msg.edit_text(
                            f"✅ Added channel: {chat.title} ({chat.id})\n"
                            f"✅ Found and saved latest message (ID: {latest_id})",
                            reply_markup=kb.as_markup()
                        )
                    else:
                        # No valid message found
                        kb = InlineKeyboardBuilder()
                        kb.button(text="Find Latest Message", callback_data=f"findlast_{chat.id}")
                        kb.button(text="Back to Channels", callback_data="channels")
                        kb.adjust(1)
                        
                        await progress_msg.edit_text(
                            f"✅ Added channel: {chat.title} ({chat.id})\n"
                            f"⚠️ Could not find any valid messages. Click button below to try finding the latest message.",
                            reply_markup=kb.as_markup()
                        )
                except Exception as e:
                    logger.error(f"Error finding latest message: {e}")
                    
                    # Show button to manually find message
                    kb = InlineKeyboardBuilder()
                    kb.button(text="Find Latest Message", callback_data=f"findlast_{chat.id}")
                    kb.button(text="Back to Channels", callback_data="channels")
                    kb.adjust(1)
                    
                    await progress_msg.edit_text(
                        f"✅ Added channel: {chat.title} ({chat.id})\n"
                        f"⚠️ Error finding latest message. Click button below to try manually.",
                        reply_markup=kb.as_markup()
                    )
            else:
                # Channel already configured
                kb = InlineKeyboardBuilder()
                kb.button(text="Back to Channels", callback_data="channels")
                
                await progress_msg.edit_text(
                    f"⚠️ Channel {chat.title} is already configured.",
                    reply_markup=kb.as_markup()
                )
        except Exception as e:
            # Access error
            kb = InlineKeyboardBuilder()
            kb.button(text="Back to Channels", callback_data="channels")
            
            await progress_msg.edit_text(
                f"❌ Error accessing channel: {e}\n\n"
                "Make sure:\n"
                "• The channel ID/username is correct\n"
                "• The bot is a member of the channel\n"
                "• The bot is an administrator in the channel",
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
            f"Forwarding {'Started' if isinstance(self.context.state, RunningState) else 'Stopped'}!",
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
                "You need at least 2 channels to set intervals between them.",
                reply_markup=InlineKeyboardBuilder().button(
                    text="Back", callback_data="channels"
                ).as_markup()
            )
            await callback.answer()
            return
        
        # Build a keyboard for channel pairs
        kb = InlineKeyboardBuilder()
        for i, channel in enumerate(source_channels):
            if i < len(source_channels) - 1:
                next_channel = source_channels[i + 1]
                # Get channel names if possible
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
        
        kb.button(text="Back", callback_data="channels")
        kb.adjust(1)
        
        await callback.message.edit_text(
            "Select channel pair to set forwarding interval:",
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
        
        # Check if this is a channel interval callback
        if "interval_between_" in data:
            # Handle the channel interval setting UI
            channel_parts = data.split('_')
            if len(channel_parts) >= 4:
                channel1 = channel_parts[2]
                channel2 = channel_parts[3]
                
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
        # Check if this is setting a specific channel interval
        elif "set_interval_" in data:
            # Parse data: set_interval_channel1_channel2_seconds
            parts = data.split('_')
            if len(parts) >= 5:
                channel1 = parts[2]
                channel2 = parts[3]
                interval = int(parts[4])
                
                # Save the interval to your database
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
        # Regular global interval setting
        # Menu for selecting interval
        if data == "interval_menu":
            await callback.message.edit_text(
                "Select repost interval:",
                reply_markup=KeyboardFactory.create_interval_keyboard()
            )
        elif data.startswith("interval_") and not "between" in data and not "menu" in data:
            try:
                # Get interval value
                interval = int(data.split("_")[1])
                
                # Save to config
                await Repository.set_config("repost_interval", str(interval))
                
                # Apply to current state if running
                if isinstance(self.context.state, RunningState):
                    # Important: Reset all timing when changing interval
                    self.context.state.interval = interval
                    
                    # Reset all channel last post times to now
                    now = datetime.now().timestamp()
                    for channel in self.context.config.source_channels:
                        self.context.state._channel_last_post[channel] = now
                    
                    self.context.state._last_global_post_time = now
                    
                    # Notify about change
                    display = f"{interval//3600}h" if interval >= 3600 else f"{interval//60}m"
                    await callback.message.edit_text(
                        f"Interval set to {display}. First post will occur after this interval.",
                        reply_markup=KeyboardFactory.create_main_keyboard(
                            True, 
                            self.context.state.auto_forward
                        )
                    )
                    
                    logger.info(f"Set forwarding interval to {interval} seconds ({interval//60} minutes)")
                else:
                    # Just save for when bot starts
                    display = f"{interval//3600}h" if interval >= 3600 else f"{interval//60}m"
                    await callback.message.edit_text(
                        f"Interval set to {display}",
                        reply_markup=KeyboardFactory.create_main_keyboard(
                            False, 
                            False
                        )
                    )
            except Exception as e:
                logger.error(f"Error setting interval: {e}")
                await callback.answer("Error setting interval")
    

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
                "No target chats configured.\n"
                "Make sure to:\n"
                "1. Add bot to target chats\n"
                "2. Make bot admin in source channels"
            )
            markup = KeyboardFactory.create_main_keyboard(
                isinstance(self.context.state, RunningState),
                isinstance(self.context.state, RunningState) and self.context.state.auto_forward
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
                isinstance(self.context.state, RunningState),
                isinstance(self.context.state, RunningState) and self.context.state.auto_forward
            )
        )
        await callback.answer()
    # Add to ForwarderBot class in bot.py
    async def manage_single_channel(self, callback: types.CallbackQuery):
        """Combined management for a single channel"""
        if callback.from_user.id != self.config.owner_id:
            return
        
        # Extract channel ID from callback data
        channel_id = callback.data.replace("manage_channel_", "")
        
        try:
            # Get channel info
            chat = await self.bot.get_chat(channel_id)
            channel_name = chat.title or channel_id
        except:
            channel_name = channel_id
        
        # Create keyboard with options
        kb = InlineKeyboardBuilder()
        kb.button(text="🔍 Find Latest Message", callback_data=f"findlast_{channel_id}")
        kb.button(text="❌ Remove Channel", callback_data=f"remove_channel_{channel_id}")
        kb.button(text="Back to Channels", callback_data="channels")
        kb.adjust(1)
        
        await callback.message.edit_text(
            f"Managing channel: {channel_name}\n\n"
            f"Select an action:",
            reply_markup=kb.as_markup()
        )
        await callback.answer()
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
                "No source channels configured.\n"
                "Add a channel by clicking the button below."
            )
        else:
            text = "📡 Source Channels:\n\n"
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
        kb.button(text="Cancel", callback_data="channels")
        
        await callback.message.edit_text(
            "Please enter the channel ID or username to add:\n\n"
            "• For public channels: Enter the username without @\n"
            "• For private channels: Enter the channel ID (starts with -100...)\n\n"
            "Simply send the channel ID or username as a message 💬",
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
            
        channel = callback.data.replace("remove_channel_", "")
        
        if self.config.remove_source_channel(channel):
            await callback.answer("Channel removed successfully")
        else:
            await callback.answer("Failed to remove channel")
            
        await self.manage_channels(callback)

    async def handle_channel_post(self, message: types.Message | None):
        """Handler for channel posts"""
        if message is None:
            return
            
        chat_id = str(message.chat.id)
        username = message.chat.username
        source_channels = self.config.source_channels
            
        # Check if this message is from a configured source channel
        is_source = False
        for channel in source_channels:
            # Compare with either channel ID or username
            if channel == chat_id or (username and channel.lower() == username.lower()):
                is_source = True
                break
                
        if not is_source:
            logger.info(f"Message not from source channel: {chat_id}/{username}")
            return
        
        # Save the last message ID for this channel
        await Repository.save_last_message(chat_id, message.message_id)
        
        # Handle message if the bot is running
        if isinstance(self.context.state, RunningState):
            await self.context.handle_message(chat_id, message.message_id)
            logger.info(f"Forwarding channel post {message.message_id} from {chat_id} to all target chats")
        else:
            logger.info("Bot is not running, ignoring post")

    async def handle_chat_member(self, update: types.ChatMemberUpdated):
        """Handler for bot being added/removed from chats"""
        if update.new_chat_member.user.id != self.bot.id:
            return

        chat_id = update.chat.id
        is_member = update.new_chat_member.status in ['member', 'administrator']
        
        # Only add groups and supergroups as targets, not channels
        if is_member and update.chat.type in ['group', 'supergroup']:
            await Repository.add_target_chat(chat_id)
            self.cache_service.remove_from_cache(chat_id)  # Force cache refresh
            await self._notify_owner(f"Bot added to {update.chat.type}: {update.chat.title} ({chat_id})")
            logger.info(f"Bot added to {update.chat.type}: {update.chat.title} ({chat_id})")
        elif not is_member:
            await Repository.remove_target_chat(chat_id)
            self.cache_service.remove_from_cache(chat_id)
            await self._notify_owner(f"Bot removed from chat {chat_id}")
            logger.info(f"Bot removed from chat {chat_id}")

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
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot stopped due to error: {e}")