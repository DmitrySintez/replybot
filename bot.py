import os
import asyncio
from datetime import datetime
import aiosqlite
from dotenv import load_dotenv
from loguru import logger
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from functools import lru_cache
import weakref

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "").lstrip('@')  # Remove @ if present

if not all([BOT_TOKEN, OWNER_ID, SOURCE_CHANNEL]):
    raise ValueError("Missing required environment variables")

# Configure logging
logger.add("bot.log", rotation="1 MB", compression="zip")

# Cache settings
CACHE_TTL = 300  # 5 minutes cache for chat info
MAX_CACHE_SIZE = 100

@dataclass
class ChatInfo:
    """Data class for storing chat information"""
    id: int
    title: str
    type: str
    member_count: Optional[int] = None
    last_updated: float = 0.0

class DatabaseConnection:
    """Database connection manager with connection pooling"""
    _pool = weakref.WeakSet()
    _max_connections = 5
    DB_PATH = os.getenv("DB_PATH", "forwarder.db")

    @classmethod
    @asynccontextmanager
    async def get_connection(cls):
        """Get a database connection from the pool"""
        for conn in cls._pool:
            if not conn.in_use:
                conn.in_use = True
                try:
                    yield conn
                finally:
                    conn.in_use = False
                return

        if len(cls._pool) < cls._max_connections:
            conn = await aiosqlite.connect(cls.DB_PATH)
            conn.in_use = True
            cls._pool.add(conn)
            try:
                yield conn
            finally:
                conn.in_use = False
        else:
            # Wait for a connection to become available
            while True:
                await asyncio.sleep(0.1)
                for conn in cls._pool:
                    if not conn.in_use:
                        conn.in_use = True
                        try:
                            yield conn
                        finally:
                            conn.in_use = False
                        return

class Database:
    """Database operations with connection pooling and prepared statements"""
    
    @staticmethod
    async def init_db():
        async with DatabaseConnection.get_connection() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS target_chats (
                    chat_id INTEGER PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS forward_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS last_messages (
                    channel_id TEXT PRIMARY KEY,
                    message_id INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_forward_stats_timestamp ON forward_stats(timestamp);
                CREATE INDEX IF NOT EXISTS idx_target_chats_added_at ON target_chats(added_at);
            """)
            await db.commit()

    @staticmethod
    async def get_target_chats() -> List[int]:
        async with DatabaseConnection.get_connection() as db:
            async with db.execute("SELECT chat_id FROM target_chats") as cursor:
                return [row[0] for row in await cursor.fetchall()]

    @staticmethod
    async def add_target_chat(chat_id: int) -> None:
        async with DatabaseConnection.get_connection() as db:
            await db.execute(
                "INSERT OR IGNORE INTO target_chats (chat_id) VALUES (?)",
                (chat_id,)
            )
            await db.commit()

    @staticmethod
    async def remove_target_chat(chat_id: int) -> None:
        async with DatabaseConnection.get_connection() as db:
            await db.execute("DELETE FROM target_chats WHERE chat_id = ?", (chat_id,))
            await db.commit()

    @staticmethod
    async def get_config(key: str, default=None) -> Optional[str]:
        async with DatabaseConnection.get_connection() as db:
            async with db.execute(
                "SELECT value FROM config WHERE key = ?",
                (key,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    @staticmethod
    async def set_config(key: str, value: str) -> None:
        async with DatabaseConnection.get_connection() as db:
            await db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, str(value))
            )
            await db.commit()

    @staticmethod
    async def log_forward(message_id: int) -> None:
        async with DatabaseConnection.get_connection() as db:
            await db.execute(
                "INSERT INTO forward_stats (message_id) VALUES (?)",
                (message_id,)
            )
            await db.commit()

    @staticmethod
    async def save_last_message(channel_id: str, message_id: int) -> None:
        async with DatabaseConnection.get_connection() as db:
            await db.execute(
                "INSERT OR REPLACE INTO last_messages (channel_id, message_id, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (channel_id, message_id)
            )
            await db.commit()

    @staticmethod
    async def get_last_message(channel_id: str) -> Optional[int]:
        async with DatabaseConnection.get_connection() as db:
            async with db.execute(
                "SELECT message_id FROM last_messages WHERE channel_id = ?",
                (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    @staticmethod
    async def get_stats() -> Dict[str, Any]:
        async with DatabaseConnection.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM forward_stats") as cursor:
                total = (await cursor.fetchone())[0]

            async with db.execute(
                "SELECT timestamp FROM forward_stats ORDER BY timestamp DESC LIMIT 1"
            ) as cursor:
                last = (await cursor.fetchone() or [None])[0]

            async with db.execute(
                "SELECT channel_id, message_id, timestamp FROM last_messages"
            ) as cursor:
                last_msgs = {
                    row[0]: {"message_id": row[1], "timestamp": row[2]}
                    for row in await cursor.fetchall()
                }

            return {
                "total_forwards": total,
                "last_forward": last,
                "last_messages": last_msgs
            }

class ChatCache:
    """Cache manager for chat information"""
    _cache: Dict[int, ChatInfo] = {}
    
    @classmethod
    async def get_chat_info(cls, bot: Bot, chat_id: int) -> Optional[ChatInfo]:
        now = datetime.now().timestamp()
        
        # Check cache first
        if chat_id in cls._cache:
            chat_info = cls._cache[chat_id]
            if now - chat_info.last_updated < CACHE_TTL:
                return chat_info

        try:
            chat = await bot.get_chat(chat_id)
            member_count = await bot.get_chat_member_count(chat_id)
            
            info = ChatInfo(
                id=chat_id,
                title=chat.title,
                type=chat.type,
                member_count=member_count,
                last_updated=now
            )
            
            # Update cache
            cls._cache[chat_id] = info
            
            # Cleanup old entries if cache is too large
            if len(cls._cache) > MAX_CACHE_SIZE:
                oldest = min(cls._cache.items(), key=lambda x: x[1].last_updated)
                del cls._cache[oldest[0]]
            
            return info
        except Exception as e:
            logger.error(f"Error fetching chat info for {chat_id}: {e}")
            return None

class ForwarderBot:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.dp = Dispatcher()
        self.running = False
        self._repost_task: Optional[asyncio.Task] = None
        self._setup_handlers()

    def _setup_handlers(self):
        """Initialize message handlers"""
        # Owner-only command handlers
        self.dp.message.register(self.start_command, Command("start"))
        self.dp.message.register(self.help_command, Command("help"))
        self.dp.message.register(self.set_last_message_command, Command("setlast"))
        self.dp.message.register(self.get_last_message_command, Command("getlast"))
        self.dp.message.register(self.forward_now_command, Command("forwardnow"))
        self.dp.message.register(self.test_message_command, Command("test"))
        self.dp.message.register(self.find_last_message_command, Command("findlast"))
        
        # Channel post handler
        self.dp.channel_post.register(self.handle_channel_post)
        
        # Callback query handlers with optimized registration
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

    @staticmethod
    def get_main_keyboard(running: bool = False):
        """Create main keyboard markup"""
        kb = InlineKeyboardBuilder()
        kb.button(
            text="🔄 Start Forwarding" if not running else "⏹ Stop Forwarding",
            callback_data="toggle_forward"
        )
        kb.button(text="⚙️ Set Interval", callback_data="interval_menu")
        kb.button(text="📊 Show Stats", callback_data="stats")
        kb.button(text="📋 List Chats", callback_data="list_chats")
        kb.adjust(2)
        return kb.as_markup()

    async def find_last_message_command(self, message: types.Message):
        """Find last valid message in channel"""
        if message.from_user.id != OWNER_ID:
            return

        progress_msg = await message.answer("🔍 Searching for last valid message...")
        current_id = await Database.get_last_message(SOURCE_CHANNEL)
        
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
                    chat_id=OWNER_ID,
                    from_chat_id=SOURCE_CHANNEL,
                    message_id=msg_id
                )
                valid_id = msg_id
                break
            except Exception as e:
                if any(error in str(e).lower() for error in ["message_id_invalid", "message not found"]):
                    continue
                logger.warning(f"Unexpected error checking message {msg_id}: {e}")

        try:
            await progress_msg.delete()
        except Exception:
            pass

        if valid_id:
            await Database.save_last_message(SOURCE_CHANNEL, valid_id)
            await message.answer(
                f"✅ Found valid message (ID: {valid_id}) after checking {checked_count} messages."
            )
        else:
            await message.answer(
                f"❌ No valid message found after checking {checked_count} messages."
            )

    async def start_command(self, message: types.Message):
        """Handler for /start command"""
        if message.from_user.id != OWNER_ID:
            return
        
        await message.answer(
            "Welcome to Channel Forwarder Bot!\n"
            "Use the buttons below to control the bot:\n\n"
            "Type /help to see available commands.",
            reply_markup=self.get_main_keyboard(self.running)
        )
    
    async def help_command(self, message: types.Message):
        """Handler for /help command"""
        if message.from_user.id != OWNER_ID:
            return
            
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

    async def set_last_message_command(self, message: types.Message):
        """Handler for /setlast command"""
        if message.from_user.id != OWNER_ID:
            return

        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /setlast <message_id>")
            return

        try:
            message_id = int(args[1])
            
            # Test message existence
            try:
                # Forward message to verify it exists
                test_msg = await self.bot.forward_message(
                    chat_id=OWNER_ID,
                    from_chat_id=SOURCE_CHANNEL,
                    message_id=message_id
                )
                await Database.save_last_message(SOURCE_CHANNEL, message_id)
                await message.answer(f"✅ Message ID {message_id} verified and saved.")
            except Exception as e:
                await message.answer(f"⚠️ Could not verify message {message_id}: {e}")
        except ValueError:
            await message.answer("❌ Message ID must be a number")

    async def get_last_message_command(self, message: types.Message):
        """Handler for /getlast command"""
        if message.from_user.id != OWNER_ID:
            return
            
        last_message_id = await Database.get_last_message(SOURCE_CHANNEL)
        await message.answer(
            f"📝 Current last message ID: {last_message_id or 'Not set'}"
        )

    async def forward_now_command(self, message: types.Message):
        """Handler for /forwardnow command"""
        if message.from_user.id != OWNER_ID:
            return
            
        last_message_id = await Database.get_last_message(SOURCE_CHANNEL)
        if not last_message_id:
            await message.answer("⚠️ No last message ID found. Use /setlast to set one.")
            return
            
        progress_msg = await message.answer(f"🔄 Forwarding message {last_message_id}...")
        success = await self._forward_message(last_message_id)
        
        if success:
            await progress_msg.edit_text("✅ Message forwarded successfully.")
        else:
            await progress_msg.edit_text("❌ Failed to forward message.")

    async def test_message_command(self, message: types.Message):
        """Handler for /test command"""
        if message.from_user.id != OWNER_ID:
            return

        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /test <message_id>")
            return

        try:
            message_id = int(args[1])
            progress_msg = await message.answer(f"🔍 Testing message {message_id}...")
            
            try:
                test_msg = await self.bot.forward_message(
                    chat_id=OWNER_ID,
                    from_chat_id=SOURCE_CHANNEL,
                    message_id=message_id
                )
                await progress_msg.edit_text(f"✅ Message {message_id} exists and can be forwarded.")
            except Exception as e:
                await progress_msg.edit_text(f"❌ Error: {e}")
        except ValueError:
            await message.answer("❌ Message ID must be a number")

    async def toggle_forwarding(self, callback: types.CallbackQuery):
        """Handler for forwarding toggle button"""
        if callback.from_user.id != OWNER_ID:
            return

        self.running = not self.running
        
        if self.running:
            interval = int(await Database.get_config("repost_interval", "3600"))
            self._repost_task = asyncio.create_task(self._fallback_repost(interval))
            status = "Started"
        else:
            if self._repost_task and not self._repost_task.done():
                self._repost_task.cancel()
            status = "Stopped"

        await callback.message.edit_text(
            f"Forwarding {status}!",
            reply_markup=self.get_main_keyboard(self.running)
        )
        await callback.answer()

    async def set_interval(self, callback: types.CallbackQuery):
        """Handler for interval setting"""
        if callback.from_user.id != OWNER_ID:
            return

        data = callback.data
        if data == "interval_menu":
            kb = InlineKeyboardBuilder()
            intervals = [
                ("5m", 300), ("1h", 3600), ("2h", 7200),
                ("6h", 21600), ("12h", 43200), ("24h", 86400)
            ]
            for label, seconds in intervals:
                kb.button(text=label, callback_data=f"interval_{seconds}")
            kb.button(text="Back", callback_data="back_to_main")
            kb.adjust(3)
            
            await callback.message.edit_text(
                "Select repost interval:",
                reply_markup=kb.as_markup()
            )
        else:
            interval = int(data.split("_")[1])
            await Database.set_config("repost_interval", str(interval))
            
            if self.running and self._repost_task:
                self._repost_task.cancel()
                self._repost_task = asyncio.create_task(self._fallback_repost(interval))
            
            display = f"{interval//3600}h" if interval >= 3600 else f"{interval//60}m"
            await callback.message.edit_text(
                f"Interval set to {display}",
                reply_markup=self.get_main_keyboard(self.running)
            )
        
        await callback.answer()

    async def remove_chat(self, callback: types.CallbackQuery):
        """Handler for chat removal"""
        if callback.from_user.id != OWNER_ID:
            return
        
        chat_id = int(callback.data.split("_")[1])
        await Database.remove_target_chat(chat_id)
        await self.list_chats(callback)
        await callback.answer("Chat removed!")

    async def show_stats(self, callback: types.CallbackQuery):
        """Handler for statistics display"""
        if callback.from_user.id != OWNER_ID:
            return
        
        stats = await Database.get_stats()
        
        last_messages_text = "\n\n".join(
            f"Channel: {channel_id}\n"
            f"Message ID: {data['message_id']}\n"
            f"Timestamp: {data['timestamp']}"
            for channel_id, data in stats.get("last_messages", {}).items()
        ) or "None"
        
        text = (
            "📊 Forwarding Statistics\n\n"
            f"Total forwards: {stats['total_forwards']}\n"
            f"Last forward: {stats['last_forward'] or 'Never'}\n\n"
            f"Last saved messages:\n{last_messages_text}"
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=self.get_main_keyboard(self.running)
        )
        await callback.answer()

    async def list_chats(self, callback: types.CallbackQuery):
        """Handler for chat listing"""
        if callback.from_user.id != OWNER_ID:
            return
        
        chats = await Database.get_target_chats()
        if not chats:
            text = (
                "No target chats configured.\n"
                "Make sure to:\n"
                "1. Add bot to target chats\n"
                "2. Make bot admin in source channel"
            )
        else:
            text_parts = ["📡 Target Chats:\n\n"]
            for chat_id in chats:
                try:
                    chat_info = await ChatCache.get_chat_info(self.bot, chat_id)
                    if chat_info:
                        text_parts.append(
                            f"• {chat_info.title}\n"
                            f"  ID: {chat_info.id}\n"
                            f"  Type: {chat_info.type}\n"
                            f"  Members: {chat_info.member_count}\n"
                        )
                    else:
                        text_parts.append(f"• Unknown chat ({chat_id})\n")
                except Exception as e:
                    text_parts.append(f"• Error getting chat {chat_id}: {e}\n")
            text = "\n".join(text_parts)

        kb = InlineKeyboardBuilder()
        for chat_id in chats:
            kb.button(
                text=f"❌ Remove {chat_id}",
                callback_data=f"remove_{chat_id}"
            )
        kb.button(text="Back", callback_data="back_to_main")
        kb.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    async def main_menu(self, callback: types.CallbackQuery):
        """Handler for main menu button"""
        if callback.from_user.id != OWNER_ID:
            return
        await callback.message.edit_text(
            "Main Menu:",
            reply_markup=self.get_main_keyboard(self.running)
        )
        await callback.answer()

    async def handle_channel_post(self, message: types.Message | None):
        """Handler for channel posts"""
        if not self.running:
            logger.info("Bot is not running, ignoring post")
            return

        if message is None:
            last_message_id = await Database.get_last_message(SOURCE_CHANNEL)
            if not last_message_id:
                logger.warning("No last message ID saved for repost")
                return
            await self._forward_message(last_message_id)
            return

        chat_id = str(message.chat.id)
        username = message.chat.username
        
        is_source = (
            chat_id == SOURCE_CHANNEL or
            (username and username.lower() == SOURCE_CHANNEL.lower())
        )
            
        if not is_source:
            logger.info(f"Message not from source channel: {chat_id}/{username}")
            return
        
        await Database.save_last_message(SOURCE_CHANNEL, message.message_id)
        await self._forward_message(message.message_id)

    async def handle_chat_member(self, update: types.ChatMemberUpdated):
        """Handler for bot being added/removed from chats"""
        if update.new_chat_member.user.id != self.bot.id:
            return

        chat_id = update.chat.id
        is_member = update.new_chat_member.status in ['member', 'administrator']
        
        if is_member and update.chat.type in ['group', 'supergroup']:
            await Database.add_target_chat(chat_id)
            await self._notify_owner(f"Bot added to {update.chat.type} {chat_id}")
        elif not is_member:
            await Database.remove_target_chat(chat_id)
            await self._notify_owner(f"Bot removed from chat {chat_id}")

    async def _forward_message(self, message_id: int) -> bool:
        """Forward a message to all target chats"""
        success = False
        target_chats = await Database.get_target_chats()
        
        if not target_chats:
            logger.warning("No target chats for forwarding")
            return False

        for chat_id in target_chats:
            try:
                chat_info = await ChatCache.get_chat_info(self.bot, chat_id)
                if not chat_info or chat_info.type not in ['group', 'supergroup']:
                    continue
                
                await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=SOURCE_CHANNEL,
                    message_id=message_id
                )
                await Database.log_forward(message_id)
                success = True
                logger.info(f"Forwarded to {chat_info.title} ({chat_id})")
            except Exception as e:
                logger.error(f"Error forwarding to {chat_id}: {e}")

        return success

    async def _fallback_repost(self, interval: int):
        """Periodic repost task"""
        while True:
            try:
                await asyncio.sleep(interval)
                
                if not self.running:
                    break
                
                await self.handle_channel_post(None)
                logger.info("Triggered periodic repost")
                
            except asyncio.CancelledError:
                logger.info("Repost task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in fallback repost: {e}")
                await asyncio.sleep(60)

    async def _notify_owner(self, message: str):
        """Send notification to bot owner"""
        try:
            await self.bot.send_message(OWNER_ID, message)
        except Exception as e:
            logger.error(f"Failed to notify owner: {e}")

    async def start(self):
        """Start the bot"""
        await Database.init_db()
        
        # Set default interval if not set
        if not await Database.get_config("repost_interval"):
            await Database.set_config("repost_interval", "3600")
        
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
            await self.bot.session.close()

async def main():
    """Main entry point with improved error handling and resource management"""
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

        # Initialize database
        await Database.init_db()
        
        bot = ForwarderBot()
        
        # Start bot with proper cleanup
        try:
            await bot.start()
        finally:
            if bot._repost_task and not bot._repost_task.done():
                bot._repost_task.cancel()
            await bot.bot.session.close()
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
