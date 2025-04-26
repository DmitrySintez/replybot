from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from .base_command import Command
from database.repository import Repository
from utils.keyboard_factory import KeyboardFactory
from utils.bot_state import IdleState, RunningState

class StartCommand(Command):
    def __init__(self, owner_id: int, running: bool = False):
        super().__init__(owner_id)
        self.running = running

    async def _handle(self, message: types.Message) -> None:
        await message.answer(
            "Welcome to Channel Forwarder Bot!\n"
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
            "/setlast <message_id> - Set the last message ID manually\n"
            "/getlast - Get current last message ID\n"
            "/forwardnow - Forward last saved message immediately\n"
            "/test <message_id> - Test if a message ID exists in channel\n"
            "/findlast - Automatically find the last valid message in channel\n\n"
            "Use buttons in the menu to control forwarding and settings."
        )
        await message.answer(help_text, parse_mode="HTML")

class SetLastMessageCommand(Command):
    def __init__(self, owner_id: int, bot, source_channel: str):
        super().__init__(owner_id)
        self.bot = bot
        self.source_channel = source_channel

    async def _handle(self, message: types.Message) -> None:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /setlast <message_id>")
            return

        try:
            message_id = int(args[1])
            try:
                test_msg = await self.bot.forward_message(
                    chat_id=self.owner_id,
                    from_chat_id=self.source_channel,
                    message_id=message_id
                )
                await Repository.save_last_message(self.source_channel, message_id)
                await message.answer(f"✅ Message ID {message_id} verified and saved.")
            except Exception as e:
                await message.answer(f"⚠️ Could not verify message {message_id}: {e}")
        except ValueError:
            await message.answer("❌ Message ID must be a number")

class GetLastMessageCommand(Command):
    def __init__(self, owner_id: int, source_channel: str):
        super().__init__(owner_id)
        self.source_channel = source_channel

    async def _handle(self, message: types.Message) -> None:
        last_message_id = await Repository.get_last_message(self.source_channel)
        await message.answer(
            f"📝 Current last message ID: {last_message_id or 'Not set'}"
        )

class ForwardNowCommand(Command):
    def __init__(self, owner_id: int, bot_context):
        super().__init__(owner_id)
        self.context = bot_context

    async def _handle(self, message: types.Message) -> None:
        last_message_id = await Repository.get_last_message(self.context.source_channel)
        if not last_message_id:
            await message.answer("⚠️ No last message ID found. Use /setlast to set one.")
            return

        progress_msg = await message.answer(f"🔄 Forwarding message {last_message_id}...")
        await self.context.handle_message(last_message_id)
        await progress_msg.edit_text("✅ Message forwarded successfully.")

class TestMessageCommand(Command):
    def __init__(self, owner_id: int, bot, source_channel: str):
        super().__init__(owner_id)
        self.bot = bot
        self.source_channel = source_channel

    async def _handle(self, message: types.Message) -> None:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /test <message_id>")
            return

        try:
            message_id = int(args[1])
            progress_msg = await message.answer(f"🔍 Testing message {message_id}...")
            
            try:
                test_msg = await self.bot.forward_message(
                    chat_id=self.owner_id,
                    from_chat_id=self.source_channel,
                    message_id=message_id
                )
                await progress_msg.edit_text(f"✅ Message {message_id} exists and can be forwarded.")
            except Exception as e:
                await progress_msg.edit_text(f"❌ Error: {e}")
        except ValueError:
            await message.answer("❌ Message ID must be a number")

class FindLastMessageCommand(Command):
    def __init__(self, owner_id: int, bot, source_channel: str):
        super().__init__(owner_id)
        self.bot = bot
        self.source_channel = source_channel

    async def _handle(self, message: types.Message) -> None:
        progress_msg = await message.answer("🔍 Searching for last valid message...")
        current_id = await Repository.get_last_message(self.source_channel)
        
        if not current_id:
            await progress_msg.edit_text("⚠️ No saved message ID. Use /setlast to set manually.")
            return

        valid_id = None
        checked_count = 0
        max_check = 100

        for msg_id in range(current_id, current_id - max_check, -1):
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
                    from_chat_id=self.source_channel,
                    message_id=msg_id
                )
                valid_id = msg_id
                break
            except Exception as e:
                if "message not found" in str(e).lower():
                    continue
                logger.warning(f"Unexpected error checking message {msg_id}: {e}")

        try:
            await progress_msg.delete()
        except Exception:
            pass

        if valid_id:
            await Repository.save_last_message(self.source_channel, valid_id)
            await message.answer(
                f"✅ Found valid message (ID: {valid_id}) after checking {checked_count} messages."
            )
        else:
            await message.answer(
                f"❌ No valid message found after checking {checked_count} messages."
            )
