import os
import asyncio
from datetime import datetime
import aiosqlite
from dotenv import load_dotenv
from loguru import logger
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "").lstrip('@')  # Remove @ if present

if not all([BOT_TOKEN, OWNER_ID, SOURCE_CHANNEL]):
    raise ValueError("Missing required environment variables")

# Configure logging
logger.add("bot.log", rotation="1 MB", compression="zip")

class Database:
    DB_PATH = os.getenv("DB_PATH", "forwarder.db")
    
    @classmethod
    async def init_db(cls):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS target_chats (
                    chat_id INTEGER PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS forward_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Добавляем новую таблицу для хранения последних сообщений канала
            await db.execute("""
                CREATE TABLE IF NOT EXISTS last_messages (
                    channel_id TEXT PRIMARY KEY,
                    message_id INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

    @classmethod
    async def get_target_chats(cls):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            async with db.execute("SELECT chat_id FROM target_chats") as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

    @classmethod
    async def add_target_chat(cls, chat_id: int):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO target_chats (chat_id) VALUES (?)", (chat_id,))
            await db.commit()

    @classmethod
    async def remove_target_chat(cls, chat_id: int):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute("DELETE FROM target_chats WHERE chat_id = ?", (chat_id,))
            await db.commit()

    @classmethod
    async def get_config(cls, key: str, default=None):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            async with db.execute("SELECT value FROM config WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    @classmethod
    async def set_config(cls, key: str, value: str):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, str(value))
            )
            await db.commit()

    @classmethod
    async def log_forward(cls, message_id: int):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute(
                "INSERT INTO forward_stats (message_id) VALUES (?)",
                (message_id,)
            )
            await db.commit()
    
    @classmethod
    async def save_last_message(cls, channel_id: str, message_id: int):
        """Сохраняет ID последнего сообщения из канала"""
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO last_messages (channel_id, message_id, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (channel_id, message_id)
            )
            await db.commit()
            logger.info(f"Saved last message ID {message_id} for channel {channel_id}")
    
    @classmethod
    async def get_last_message(cls, channel_id: str):
        """Получает ID последнего сообщения из канала"""
        async with aiosqlite.connect(cls.DB_PATH) as db:
            async with db.execute(
                "SELECT message_id FROM last_messages WHERE channel_id = ?", 
                (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    @classmethod
    async def get_stats(cls):
        async with aiosqlite.connect(cls.DB_PATH) as db:
            total = await db.execute("SELECT COUNT(*) FROM forward_stats")
            total = await total.fetchone()
            
            last = await db.execute(
                "SELECT timestamp FROM forward_stats ORDER BY timestamp DESC LIMIT 1"
            )
            last = await last.fetchone()
            
            # Также добавим информацию о последнем сохраненном сообщении
            last_msg = await db.execute(
                "SELECT channel_id, message_id, timestamp FROM last_messages"
            )
            last_msg_rows = await last_msg.fetchall()
            last_msgs = {}
            for row in last_msg_rows:
                last_msgs[row[0]] = {
                    "message_id": row[1],
                    "timestamp": row[2]
                }
            
            return {
                "total_forwards": total[0] if total else 0,
                "last_forward": last[0] if last else None,
                "last_messages": last_msgs
            }

class ForwarderBot:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.dp = Dispatcher()
        self.running = False
        self.repost_task = None
        self.setup_handlers()

    def setup_handlers(self):
        # Owner-only command handlers
        self.dp.message.register(self.start_command, Command("start"))
        self.dp.message.register(self.help_command, Command("help"))
        
        # Команды для управления сообщениями
        self.dp.message.register(self.set_last_message_command, Command("setlast"))
        self.dp.message.register(self.get_last_message_command, Command("getlast"))
        self.dp.message.register(self.forward_now_command, Command("forwardnow"))
        self.dp.message.register(self.test_message_command, Command("test"))
        self.dp.message.register(self.find_last_message_command, Command("findlast"))  # Новая команда
        
        # Channel post handler
        self.dp.channel_post.register(self.handle_channel_post)
        
        # Callback query handlers
        self.dp.callback_query.register(
            self.toggle_forwarding, lambda c: c.data == "toggle_forward"
        )
        self.dp.callback_query.register(
            self.set_interval, lambda c: c.data.startswith("interval_")
        )
        self.dp.callback_query.register(
            self.remove_chat, lambda c: c.data.startswith("remove_")
        )
        self.dp.callback_query.register(
            self.show_stats, lambda c: c.data == "stats"
        )
        self.dp.callback_query.register(
            self.list_chats, lambda c: c.data == "list_chats"
        )
        self.dp.callback_query.register(
            self.main_menu, lambda c: c.data == "back_to_main"
        )
        
        # Handler for bot being added to chats
        self.dp.my_chat_member.register(self.handle_chat_member)

    def get_main_keyboard(self):
        kb = InlineKeyboardBuilder()
        kb.button(
            text="🔄 Start Forwarding" if not self.running else "⏹ Stop Forwarding",
            callback_data="toggle_forward"
        )
        kb.button(text="⚙️ Set Interval", callback_data="interval_menu")
        kb.button(text="📊 Show Stats", callback_data="stats")
        kb.button(text="📋 List Chats", callback_data="list_chats")
        kb.adjust(2)
        return kb.as_markup()

    async def find_last_message_command(self, message: types.Message):
        """Поиск последнего действительного сообщения в канале"""
        if message.from_user.id != OWNER_ID:
            return
            
        await message.answer("🔍 Начинаю поиск последнего действительного сообщения в канале...")
        
        # Получаем текущее сохраненное ID сообщения
        current_id = await Database.get_last_message(SOURCE_CHANNEL)
        if not current_id:
            await message.answer("⚠️ Нет сохраненного ID сообщения. Используйте /setlast, чтобы установить ID вручную.")
            return
        
        # Начинаем искать от текущего ID в обратном порядке
        start_id = current_id
        valid_id = None
        checked_count = 0
        max_check = 100  # Максимальное количество проверяемых сообщений
        
        progress_msg = await message.answer("⏳ Проверка сообщений...")
        
        for msg_id in range(start_id, start_id - max_check, -1):
            if msg_id <= 0:
                break
                
            checked_count += 1
            
            # Обновляем статус каждые 10 проверок
            if checked_count % 10 == 0:
                try:
                    await self.bot.edit_message_text(
                        f"⏳ Проверено {checked_count} сообщений...",
                        chat_id=message.chat.id,
                        message_id=progress_msg.message_id
                    )
                except Exception:
                    pass
            
            try:
                # Проверяем, существует ли сообщение, пытаясь получить информацию о нем
                try:
                    msg = await self.bot.forward_message(
                        chat_id=OWNER_ID,  # Пересылаем себе для проверки
                        from_chat_id=SOURCE_CHANNEL,
                        message_id=msg_id
                    )
                    
                    # Если дошли сюда, сообщение действительно
                    valid_id = msg_id
                    logger.info(f"Найдено действительное сообщение с ID {msg_id}")
                    break
                except Exception as e:
                    error_text = str(e).lower()
                    if "message_id_invalid" in error_text or "message not found" in error_text:
                        # Продолжаем поиск
                        continue
                    else:
                        # Возможно, проблема с правами доступа
                        logger.warning(f"Необычная ошибка при проверке сообщения {msg_id}: {e}")
                        continue
            except Exception as e:
                logger.error(f"Ошибка при проверке сообщения {msg_id}: {e}")
                continue
        
        # Удаляем сообщение о прогрессе
        try:
            await self.bot.delete_message(chat_id=message.chat.id, message_id=progress_msg.message_id)
        except:
            pass
        
        if valid_id:
            # Сохраняем найденное ID
            await Database.save_last_message(SOURCE_CHANNEL, valid_id)
            await message.answer(
                f"✅ Найдено действительное сообщение с ID {valid_id} после проверки {checked_count} сообщений.\n"
                f"Этот ID теперь установлен как последнее сообщение для периодической рассылки."
            )
        else:
            await message.answer(
                f"❌ Не удалось найти действительное сообщение после проверки {checked_count} сообщений.\n"
                f"Установите ID вручную с помощью команды /setlast или увеличьте диапазон поиска в коде."
            )
    async def start_command(self, message: types.Message):
        if message.from_user.id != OWNER_ID:
            return
        
        await message.answer(
            "Welcome to Channel Forwarder Bot!\n"
            "Use the buttons below to control the bot:\n\n"
            "Type /help to see available commands.",
            reply_markup=self.get_main_keyboard()
        )
    
    async def help_command(self, message: types.Message):
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
        """Ручная установка ID последнего сообщения"""
        if message.from_user.id != OWNER_ID:
            return
            
        # Парсим ID сообщения из команды
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Используйте формат: /setlast <message_id>")
            return
            
        try:
            message_id = int(args[1])
            
            # Проверяем, существует ли сообщение
            try:
                # Пробуем переслать сообщение пользователю для проверки
                try:
                    test_message = await self.bot.forward_message(
                        chat_id=OWNER_ID,
                        from_chat_id=SOURCE_CHANNEL,
                        message_id=message_id
                    )
                    # Если дошли сюда - сообщение существует
                    await message.answer(f"✅ Сообщение с ID {message_id} найдено и проверено.")
                except Exception as e:
                    await message.answer(f"⚠️ Сообщение с ID {message_id} не может быть переслано: {e}")
                    return
            except Exception as e:
                logger.warning(f"Не удалось проверить существование сообщения {message_id}: {e}")
                await message.answer(f"⚠️ Предупреждение: Не удалось проверить существование сообщения, но ID будет сохранен.")
            
            # Сохраняем ID сообщения
            await Database.save_last_message(SOURCE_CHANNEL, message_id)
            await message.answer(f"✅ ID последнего сообщения установлен на {message_id}")
            
        except ValueError:
            await message.answer("❌ ID сообщения должен быть числом")
    
    async def get_last_message_command(self, message: types.Message):
        """Получение текущего сохраненного ID сообщения"""
        if message.from_user.id != OWNER_ID:
            return
            
        last_message_id = await Database.get_last_message(SOURCE_CHANNEL)
        if last_message_id:
            await message.answer(f"📝 Текущий ID последнего сообщения: {last_message_id}")
        else:
            await message.answer("⚠️ ID последнего сообщения не найден в базе данных.")
    
    async def forward_now_command(self, message: types.Message):
        """Немедленная пересылка последнего сохраненного сообщения"""
        if message.from_user.id != OWNER_ID:
            return
            
        last_message_id = await Database.get_last_message(SOURCE_CHANNEL)
        if not last_message_id:
            await message.answer("⚠️ ID последнего сообщения не найден в базе данных. Используйте /setlast для установки.")
            return
            
        await message.answer(f"🔄 Начинаю пересылку сообщения ID: {last_message_id}...")
        
        success = await self.repost_saved_message(last_message_id)
        if success:
            await message.answer("✅ Сообщение успешно переслано во все активные чаты.")
        else:
            await message.answer("❌ Не удалось переслать сообщение ни в один чат.")
    
    async def test_message_command(self, message: types.Message):
        """Тестирование существования сообщения в канале"""
        if message.from_user.id != OWNER_ID:
            return
            
        # Парсим ID сообщения из команды
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Используйте формат: /test <message_id>")
            return
            
        try:
            message_id = int(args[1])
            
            # Пробуем переслать сообщение владельцу для проверки
            try:
                await message.answer(f"🔍 Проверяю сообщение ID {message_id}...")
                
                # Пробуем переслать сообщение владельцу
                forwarded = await self.bot.forward_message(
                    chat_id=OWNER_ID,
                    from_chat_id=SOURCE_CHANNEL,
                    message_id=message_id
                )
                
                if forwarded:
                    await message.answer(f"✅ Сообщение ID {message_id} существует и доступно для пересылки!")
                else:
                    await message.answer(f"⚠️ Проблема с копированием сообщения {message_id}.")
            except Exception as e:
                await message.answer(f"❌ Ошибка при проверке сообщения: {e}")
                
        except ValueError:
            await message.answer("❌ ID сообщения должен быть числом")

    async def toggle_forwarding(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        
        self.running = not self.running
        if self.running:
            interval = int(await Database.get_config("repost_interval", "3600"))
            self.repost_task = asyncio.create_task(self.fallback_repost(interval))
            status = "Started"
        else:
            if self.repost_task:
                self.repost_task.cancel()
            status = "Stopped"
        
        try:
            await callback.message.edit_text(
                f"Forwarding {status}!",
                reply_markup=self.get_main_keyboard()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
        await callback.answer()

    async def set_interval(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        
        if callback.data == "interval_menu":
            kb = InlineKeyboardBuilder()
            intervals = [
                (5, "5m", 300),      # 5 minutes in seconds
                (60, "1h", 3600),    # 1 hour in seconds
                (120, "2h", 7200),   # 2 hours in seconds
                (360, "6h", 21600),  # 6 hours in seconds
                (720, "12h", 43200), # 12 hours in seconds
                (1440, "24h", 86400) # 24 hours in seconds
            ]
            for minutes, label, seconds in intervals:
                kb.button(
                    text=label,
                    callback_data=f"interval_{seconds}"
                )
            kb.button(text="Back", callback_data="back_to_main")
            kb.adjust(3)
            
            try:
                await callback.message.edit_text(
                    "Select repost interval:",
                    reply_markup=kb.as_markup()
                )
            except Exception as e:
                if "message is not modified" not in str(e):
                    raise
        else:
            interval = int(callback.data.split("_")[1])
            await Database.set_config("repost_interval", str(interval))
            
            if self.running and self.repost_task:
                self.repost_task.cancel()
                self.repost_task = asyncio.create_task(self.fallback_repost(interval))
            
            try:
                # Format interval display
                if interval < 3600:  # Less than 1 hour
                    display = f"{interval//60}m"
                else:
                    display = f"{interval//3600}h"
                    
                await callback.message.edit_text(
                    f"Interval set to {display}",
                    reply_markup=self.get_main_keyboard()
                )
            except Exception as e:
                if "message is not modified" not in str(e):
                    raise
        
        await callback.answer()

    async def remove_chat(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        
        chat_id = int(callback.data.split("_")[1])
        await Database.remove_target_chat(chat_id)
        await self.list_chats(callback)
        await callback.answer("Chat removed!")

    async def list_chats(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        
        # Re-register existing chats
        await self.register_existing_chats()
        
        chats = await Database.get_target_chats()
        if not chats:
            text = "No target chats configured.\nMake sure to:\n1. Add bot to target chats\n2. Make bot admin in source channel"
        else:
            text = "📡 Target Chats:\n\n"
            for chat_id in chats:
                try:
                    chat = await self.bot.get_chat(chat_id)
                    members = await self.bot.get_chat_member_count(chat_id)
                    text += f"• {chat.title}\n  ID: {chat_id}\n  Type: {chat.type}\n  Members: {members}\n\n"
                except Exception as e:
                    text += f"• Unknown chat ({chat_id})\n  Error: {str(e)}\n\n"
                    logger.error(f"Error getting chat info: {e}")
        
        kb = InlineKeyboardBuilder()
        for chat_id in chats:
            kb.button(
                text=f"❌ Remove {chat_id}",
                callback_data=f"remove_{chat_id}"
            )
        kb.button(text="Back", callback_data="back_to_main")
        kb.adjust(1)
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=kb.as_markup()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
        await callback.answer()

    async def show_stats(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        
        stats = await Database.get_stats()
        
        # Форматируем информацию о последних сообщениях
        last_messages_text = ""
        if stats.get("last_messages"):
            for channel_id, data in stats["last_messages"].items():
                last_messages_text += f"Channel: {channel_id}\nMessage ID: {data['message_id']}\nTimestamp: {data['timestamp']}\n\n"
        
        text = (
            "📊 Forwarding Statistics\n\n"
            f"Total forwards: {stats['total_forwards']}\n"
            f"Last forward: {stats['last_forward'] or 'Never'}\n\n"
            f"Last saved messages:\n{last_messages_text or 'None'}"
        )
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=self.get_main_keyboard()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
        await callback.answer()

    async def handle_channel_post(self, message: types.Message | None):
        if not self.running:
            logger.info("Bot is not running, ignoring post")
            return

        if message is None:
            # Это периодическая пересылка - используем сохраненный ID последнего сообщения
            last_message_id = await Database.get_last_message(SOURCE_CHANNEL)
            if not last_message_id:
                logger.warning("Нет сохраненного ID последнего сообщения для повторной отправки")
                return
                
            logger.info(f"Повторная отправка сообщения ID: {last_message_id}")
            await self.repost_saved_message(last_message_id)
            return

        # Handle normal channel post
        chat_id = str(message.chat.id)
        username = message.chat.username
        
        is_source = (
            chat_id == SOURCE_CHANNEL or 
            (username and username.lower() == SOURCE_CHANNEL.lower())
        )
            
        if not is_source:
            logger.info(f"Message not from source channel. Got {chat_id}/{username}, expected {SOURCE_CHANNEL}")
            return
        
        logger.info(f"Forwarding channel post {message.message_id} to all target chats")
        
        # Сохраняем ID этого сообщения как последнее из канала
        await Database.save_last_message(SOURCE_CHANNEL, message.message_id)
        
        # Пересылаем сообщение во все целевые чаты
        await self.forward_to_all(message)

    async def repost_saved_message(self, message_id: int):
        """
        Повторно отправляет сохраненное сообщение из исходного канала во все целевые чаты
        """
        target_chats = await Database.get_target_chats()
        
        if not target_chats:
            logger.warning("Нет целевых чатов для пересылки")
            return False
            
        success = False
        invalid_message = False
        
        for chat_id in target_chats:
            try:
                # Проверяем тип чата
                chat = await self.bot.get_chat(chat_id)
                
                # Пересылаем только в группы и супергруппы
                if chat.type not in ['group', 'supergroup']:
                    logger.info(f"Пропускаем пересылку в {chat.type} {chat_id}")
                    continue
                
                # Пересылаем сообщение
                sent_message = await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=SOURCE_CHANNEL,
                    message_id=message_id
                )
                
                if sent_message:
                    await Database.log_forward(message_id)
                    logger.info(f"Периодически переслано в {chat.type} {chat.title} ({chat_id})")
                    success = True
            except Exception as e:
                error_text = str(e).lower()
                
                # Проверяем, является ли ошибка связанной с недействительным ID сообщения
                if "message_id_invalid" in error_text or "message not found" in error_text:
                    invalid_message = True
                    logger.error(f"Сообщение {message_id} больше не существует в канале")
                    # Прерываем цикл, так как сообщение недействительно для всех чатов
                    break
                else:
                    logger.error(f"Ошибка при пересылке в {chat_id}: {e}")
                    continue
        
        # Если сообщение недействительно, попробуем найти предыдущее действительное сообщение
        if invalid_message:
            logger.warning(f"Сообщение {message_id} недействительно. Ищем предыдущее активное сообщение...")
            
            # Отправляем уведомление владельцу
            try:
                await self.bot.send_message(
                    OWNER_ID,
                    f"⚠️ Внимание! Сообщение ID {message_id} больше не существует в канале. "
                    f"Запустите команду /findlast, чтобы найти последнее активное сообщение."
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление владельцу: {e}")
                
            # Автоматически не ищем предыдущее, чтобы не создать нагрузку на API
            # Вместо этого отправляем владельцу сообщение о необходимости вручную обновить ID
        
        return success

    async def forward_to_all(self, message: types.Message):
        target_chats = await Database.get_target_chats()
        
        for chat_id in target_chats:
            try:
                # Get chat info to check type
                chat = await self.bot.get_chat(chat_id)
                
                # Only forward to groups and supergroups
                if chat.type not in ['group', 'supergroup']:
                    logger.info(f"Skipping forward to {chat.type} {chat_id}")
                    continue
                
                await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                await Database.log_forward(message.message_id)
                logger.info(f"Forwarded to {chat.type} {chat.title} ({chat_id})")
            except Exception as e:
                logger.error(f"Error forwarding to {chat_id}: {e}")
                continue




    async def fallback_repost(self, interval: int):
        while True:
            try:
                await asyncio.sleep(interval)
                
                if not self.running:
                    break
                
                try:
                    # Вызываем периодическую пересылку
                    await self.handle_channel_post(None)
                    logger.info("Triggered periodic repost")
                except Exception as e:
                    logger.error(f"Failed to trigger repost: {e}")
                
            except asyncio.CancelledError:
                logger.info("Repost task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in fallback repost: {e}")
                await asyncio.sleep(60)  # Wait before retry

    async def main_menu(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        try:
            await callback.message.edit_text(
                "Main Menu:",
                reply_markup=self.get_main_keyboard()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
        await callback.answer()

    async def handle_chat_member(self, update: types.ChatMemberUpdated):
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
                await Database.add_target_chat(update.chat.id)
                chat_info = await self.bot.get_chat(update.chat.id)
                logger.info(
                    f"Bot added to {update.chat.type}: "
                    f"{chat_info.title} ({update.chat.id})"
                )
        elif update.new_chat_member.status == 'left':
            # Bot was removed
            await Database.remove_target_chat(update.chat.id)
            logger.info(f"Bot removed from chat {update.chat.id}")

        # Notify owner about the change
        try:
            await self.bot.send_message(
                OWNER_ID,
                f"Bot {'added to' if update.new_chat_member.status in ['member', 'administrator'] else 'removed from'} "
                f"{update.chat.type} {update.chat.id}"
            )
        except Exception as e:
            logger.error(f"Failed to notify owner: {e}")

    async def verify_channel_access(self):
        try:
            # Try to get channel info
            channel = await self.bot.get_chat(SOURCE_CHANNEL)
            logger.info(f"Successfully connected to channel: {channel.title} ({channel.id})")
            
            # Try to get channel member count to verify admin rights
            member_count = await self.bot.get_chat_member_count(SOURCE_CHANNEL)
            logger.info(f"Channel member count: {member_count}")
            
            # Проверяем права бота в канале
            bot_member = await self.bot.get_chat_member(SOURCE_CHANNEL, self.bot.id)
            logger.info(f"Bot status in channel: {bot_member.status}")
            
            # Проверяем можем ли мы посылать служебные сообщения
            try:
                await self.bot.send_message(
                    OWNER_ID,
                    f"✅ Successfully connected to channel {channel.title} ({channel.id})"
                )
                return True
            except Exception as e:
                logger.warning(f"Failed to send test message to owner: {e}")
                # Продолжаем работу, потому что это не критическая ошибка
                return True
            
        except Exception as e:
            logger.error(f"Failed to access channel {SOURCE_CHANNEL}: {e}")
            return False

    async def register_existing_chats(self):
        """Register chats where the bot is already a member."""
        try:
            # Get list of bot's existing chats
            updates = await self.bot.get_updates(limit=100, timeout=1)
            registered = set()
            
            for update in updates:
                if update.my_chat_member and update.my_chat_member.chat.id not in registered:
                    chat = update.my_chat_member.chat
                    if chat.type in ['group', 'supergroup']:
                        await Database.add_target_chat(chat.id)
                        logger.info(f"Registered existing chat: {chat.title} ({chat.id})")
                        registered.add(chat.id)
        except Exception as e:
            logger.error(f"Error registering existing chats: {e}")

    async def start(self):
        await Database.init_db()
        
        # Verify channel access
        channel_access = await self.verify_channel_access()
        if not channel_access:
            logger.warning("Could not fully verify channel access, but continuing anyway")
            # Не прерываем работу бота, просто логируем предупреждение
            # return
            
        # Register existing chats
        await self.register_existing_chats()
            
        # Set default interval if not set
        if not await Database.get_config("repost_interval"):
            await Database.set_config("repost_interval", "3600")
        
        logger.info("Bot started successfully!")
        try:
            # Get the last update ID to avoid duplicate updates
            offset = 0
            try:
                updates = await self.bot.get_updates(limit=1, timeout=1)
                if updates:
                    offset = updates[-1].update_id + 1
            except Exception as e:
                logger.warning(f"Failed to get initial updates: {e}")

            logger.info(f"Starting polling with offset {offset}")
            await self.dp.start_polling(self.bot, offset=offset)
        finally:
            await self.bot.session.close()


async def main():
    # Implement singleton pattern using a lock file
    lock_file = "bot.lock"
    
    if os.path.exists(lock_file):
        try:
            # Check if process is actually running
            with open(lock_file, 'r') as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # Test if process is running
            logger.error(f"Another instance is already running (PID: {pid})")
            return
        except (ProcessLookupError, ValueError):
            # Process not running, clean up stale lock file
            os.remove(lock_file)
            logger.info("Cleaned up stale lock file")
        except PermissionError:
            logger.error("Cannot access lock file")
            return

    # Create lock file with current PID
    try:
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.error(f"Failed to create lock file: {e}")
        return

    try:
        bot = ForwarderBot()
        await bot.start()
    finally:
        # Clean up lock file on exit
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
