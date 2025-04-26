# commands/commands.py (updated version)
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from .base_command import Command
from database.repository import Repository
from utils.keyboard_factory import KeyboardFactory
from utils.bot_state import IdleState, RunningState
from utils.config import Config

class StartCommand(Command):
    def __init__(self, owner_id: int, running: bool = False):
        super().__init__(owner_id)
        self.running = running

    async def _handle(self, message: types.Message) -> None:
        await message.answer(
            "Welcome to Multi-Channel Forwarder Bot!\n"
            "Use the buttons below to control the bot:\n\n"
            "Type /help to see available commands.",
            reply_markup=KeyboardFactory.create_main_keyboard(self.running)
        )

class HelpCommand(Command):
    async def _handle(self, message: types.Message) -> None:
        help_text = (
            "📋 <b>Available commands:</b>\n\n"
            "/start - Show main menu\n"
            "/help - Show this help message\n"
            "/setlast <channel_id> <message_id> - Set the last message ID manually\n"
            "/getlast - Get current last message IDs for all channels\n"
            "/forwardnow - Forward latest message immediately\n"
            "/test <channel_id> <message_id> - Test if a message ID exists in channel\n"
            "/findlast <channel_id> - Find the last valid message in channel\n\n"
            "Use buttons in the menu to control forwarding and settings."
        )
        await message.answer(help_text, parse_mode="HTML")

class SetLastMessageCommand(Command):
    def __init__(self, owner_id: int, bot):
        super().__init__(owner_id)
        self.bot = bot

    async def _handle(self, message: types.Message) -> None:
        args = message.text.split()
        
        if len(args) != 3:
            await message.answer("Usage: /setlast <channel_id> <message_id>")
            return

        try:
            channel_id = args[1]
            message_id = int(args[2])
            
            try:
                # Verify the channel exists and message is valid
                test_msg = await self.bot.forward_message(
                    chat_id=self.owner_id,
                    from_chat_id=channel_id,
                    message_id=message_id
                )
                
                # Save the message
                await Repository.save_last_message(channel_id, message_id)
                await message.answer(f"✅ Message ID {message_id} from channel {channel_id} verified and saved.")
            
            except Exception as e:
                await message.answer(f"⚠️ Could not verify message in channel {channel_id}: {e}")
        
        except ValueError:
            await message.answer("❌ Message ID must be a number")

class GetLastMessageCommand(Command):
    def __init__(self, owner_id: int):
        super().__init__(owner_id)

    async def _handle(self, message: types.Message) -> None:
        last_messages = await Repository.get_all_last_messages()
        
        if not last_messages:
            await message.answer("❌ No saved message IDs found.")
            return
            
        response = "📝 Current last messages by channel:\n\n"
        
        for channel_id, data in last_messages.items():
            response += f"Channel: {channel_id}\n"
            response += f"Message ID: {data['message_id']}\n"
            response += f"Timestamp: {data['timestamp']}\n\n"
        
        await message.answer(response)

class ForwardNowCommand(Command):
    def __init__(self, owner_id: int, bot_context):
        super().__init__(owner_id)
        self.context = bot_context

    async def _handle(self, message: types.Message) -> None:
        # Get the most recent message across all channels
        channel_id, message_id = await Repository.get_latest_message()
        
        if not channel_id or not message_id:
            await message.answer(
                "⚠️ No recent messages found. Add channels and messages first."
            )
            return

        progress_msg = await message.answer(f"🔄 Forwarding message {message_id} from channel {channel_id}...")
        
        await self.context.handle_message(channel_id, message_id)
        await progress_msg.edit_text("✅ Message forwarded successfully.")

class TestMessageCommand(Command):
    def __init__(self, owner_id: int, bot):
        super().__init__(owner_id)
        self.bot = bot

    async def _handle(self, message: types.Message) -> None:
        args = message.text.split()
        
        if len(args) != 3:
            await message.answer("Usage: /test <channel_id> <message_id>")
            return

        try:
            channel_id = args[1]
            message_id = int(args[2])
            
            progress_msg = await message.answer(f"🔍 Testing message {message_id} in channel {channel_id}...")
            
            try:
                test_msg = await self.bot.forward_message(
                    chat_id=self.owner_id,
                    from_chat_id=channel_id,
                    message_id=message_id
                )
                await progress_msg.edit_text(f"✅ Message {message_id} in channel {channel_id} exists and can be forwarded.")
            except Exception as e:
                await progress_msg.edit_text(f"❌ Error: {e}")
        except ValueError:
            await message.answer("❌ Message ID must be a number")

class FindLastMessageCommand(Command):
    def __init__(self, owner_id: int, bot):
        super().__init__(owner_id)
        self.bot = bot

    async def _handle(self, message: types.Message) -> None:
        args = message.text.split()
        
        if len(args) != 2:
            await message.answer("Usage: /findlast <channel_id>")
            return
            
        channel_id = args[1]
        progress_msg = await message.answer(f"🔍 Searching for last valid message in channel {channel_id}...")
        
        # Get last ID for this channel if available
        last_messages = await Repository.get_all_last_messages()
        current_id = None
        
        for chan, data in last_messages.items():
            if chan == channel_id:
                current_id = data["message_id"]
                break
        
        if not current_id:
            # If no last ID is known, try with a reasonable starting point
            current_id = 1000
        
        valid_id = None
        checked_count = 0
        max_check = 100

        # Search backwards from current_id + some buffer
        for msg_id in range(current_id + 10, current_id - max_check, -1):
            if msg_id <= 0:
                break

            checked_count += 1
            if checked_count % 10 == 0:
                try:
                    await progress_msg.edit_text(f"⏳ Checked {checked_count} messages...")
                except Exception:
                    pass

            try:
                msg = await self.bot.forward_message(
                    chat_id=self.owner_id,
                    from_chat_id=channel_id,
                    message_id=msg_id
                )
                valid_id = msg_id
                break
            except Exception as e:
                if "message not found" in str(e).lower():
                    continue
                logger.warning(f"Unexpected error checking message {msg_id} in channel {channel_id}: {e}")

        try:
            await progress_msg.delete()
        except Exception:
            pass

        if valid_id:
            await Repository.save_last_message(channel_id, valid_id)
            await message.answer(
                f"✅ Found valid message (ID: {valid_id}) in channel {channel_id} after checking {checked_count} messages."
            )
        else:
            await message.answer(
                f"❌ No valid message found in channel {channel_id} after checking {checked_count} messages."
            )