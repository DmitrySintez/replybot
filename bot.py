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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS source_channels (
                    channel_id TEXT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

            # If SOURCE_CHANNEL from env exists, add it as default source
            if SOURCE_CHANNEL:
                await db.execute(
                    "INSERT OR IGNORE INTO source_channels (channel_id) VALUES (?)",
                    (SOURCE_CHANNEL,)
                )
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
    async def add_source_channel(cls, channel_id: str):
        """Add a new source channel"""
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO source_channels (channel_id) VALUES (?)",
                (channel_id,)
            )
            await db.commit()

    @classmethod
    async def remove_source_channel(cls, channel_id: str):
        """Remove a source channel"""
        async with aiosqlite.connect(cls.DB_PATH) as db:
            await db.execute(
                "DELETE FROM source_channels WHERE channel_id = ?",
                (channel_id,)
            )
            await db.commit()

    @classmethod
    async def get_source_channels(cls):
        """Get list of source channels"""
        async with aiosqlite.connect(cls.DB_PATH) as db:
            async with db.execute("SELECT channel_id FROM source_channels") as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

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
        
        # Message handlers
        self.dp.message.register(self.set_last_message_command, Command("setlast"))
        self.dp.message.register(self.get_last_message_command, Command("getlast"))
        self.dp.message.register(self.forward_now_command, Command("forwardnow"))
        self.dp.message.register(self.test_message_command, Command("test"))
        self.dp.message.register(self.find_last_message_command, Command("findlast"))
        self.dp.message.register(self.add_channel_command, Command("addchannel"))
        self.dp.message.register(self.remove_channel_command, Command("removechannel"))
        self.dp.message.register(self.list_channels_command, Command("listchannels"))
        
        # Forward handler for adding channels
        self.dp.message.register(
            self.handle_forwarded_channel_message,
            lambda message: message.forward_from_chat is not None
        )

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
        self.dp.callback_query.register(
            self.add_channel_menu, lambda c: c.data == "add_channel"
        )
        self.dp.callback_query.register(
            self.list_channels_menu, lambda c: c.data == "list_channels"
        )
        
        # Handler for bot being added to chats
        self.dp.my_chat_member.register(self.handle_chat_member)

    def get_main_keyboard(self):
        kb = InlineKeyboardBuilder()
        kb.button(
            text="🔄 Запустить рассылку" if not self.running else "⏹ Остановить рассылку",
            callback_data="toggle_forward"
        )
        kb.button(text="⚙️ Интервал", callback_data="interval_menu")
        kb.button(text="📊 Статистика", callback_data="stats")
        kb.button(text="📋 Список чатов", callback_data="list_chats")
        kb.button(text="➕ Добавить канал", callback_data="add_channel")
        kb.button(text="📋 Список каналов", callback_data="list_channels")
        kb.adjust(2)
        return kb.as_markup()

    async def find_last_message_command(self, message: types.Message):
        """Поиск последнего действительного сообщения в канале"""
        if message.from_user.id != OWNER_ID:
            return

        args = message.text.split()
        if len(args) != 2:
            await message.answer("Используйте формат: /findlast <channel_id>")
            return

        channel_id = args[1].lstrip('@')
        
        # Check if channel is in source channels
        source_channels = await Database.get_source_channels()
        if channel_id not in source_channels:
            await message.answer("❌ Указанный канал не является источником")
            return
            
        await message.answer(f"🔍 Начинаю поиск последнего действительного сообщения в канале {channel_id}...")
        
        # Get current saved message ID
        current_id = await Database.get_last_message(channel_id)
        if not current_id:
            current_id = 1  # Start from beginning if no saved ID
        
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
                        from_chat_id=channel_id,
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
            await Database.save_last_message(channel_id, valid_id)
            await message.answer(
                f"✅ Найдено действительное сообщение с ID {valid_id} в канале {channel_id}\n"
                f"после проверки {checked_count} сообщений.\n"
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
            "Бот для пересылки сообщений из каналов!\n"
            "Используйте кнопки ниже для управления ботом:\n\n"
            "Напишите /help для просмотра списка команд.",
            reply_markup=self.get_main_keyboard()
        )
    
    async def help_command(self, message: types.Message):
        if message.from_user.id != OWNER_ID:
            return
        
        help_text = (
            "📋 <b>Доступные команды:</b>\n\n"
            "/start - Показать главное меню\n"
            "/help - Показать это сообщение\n"
            "/setlast <channel_id> <message_id> - Установить ID последнего сообщения\n"
            "/getlast <channel_id> - Получить текущий ID последнего сообщения\n"
            "/forwardnow - Переслать последние сообщения сейчас\n"
            "/test <channel_id> <message_id> - Проверить существование сообщения\n"
            "/findlast <channel_id> - Найти последнее сообщение автоматически\n"
            "/addchannel <channel_id> - Добавить канал-источник\n"
            "/removechannel <channel_id> - Удалить канал-источник\n"
            "/listchannels - Список каналов-источников\n\n"
            "Используйте кнопки меню для управления рассылкой."
        )
        
        await message.answer(help_text, parse_mode="HTML")
    
    async def set_last_message_command(self, message: types.Message):
        """Ручная установка ID последнего сообщения"""
        if message.from_user.id != OWNER_ID:
            return
            
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Используйте формат: /setlast <channel_id> <message_id>")
            return
            
        try:
            channel_id = args[1].lstrip('@')
            message_id = int(args[2])
            
            # Check if channel is in source channels
            source_channels = await Database.get_source_channels()
            if channel_id not in source_channels:
                await message.answer("❌ Указанный канал не является источником")
                return
            
            # Проверяем, существует ли сообщение
            try:
                # Пробуем переслать сообщение пользователю для проверки
                try:
                    test_message = await self.bot.forward_message(
                        chat_id=OWNER_ID,
                        from_chat_id=channel_id,
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
            await Database.save_last_message(channel_id, message_id)
            await message.answer(f"✅ ID последнего сообщения установлен на {message_id} для канала {channel_id}")
            
        except ValueError:
            await message.answer("❌ ID сообщения должен быть числом")
    
    async def get_last_message_command(self, message: types.Message):
        """Получение текущего сохраненного ID сообщения"""
        if message.from_user.id != OWNER_ID:
            return

        args = message.text.split()
        if len(args) != 2:
            await message.answer("Используйте формат: /getlast <channel_id>")
            return

        channel_id = args[1].lstrip('@')
        
        # Check if channel is in source channels
        source_channels = await Database.get_source_channels()
        if channel_id not in source_channels:
            await message.answer("❌ Указанный канал не является источником")
            return
            
        last_message_id = await Database.get_last_message(channel_id)
        if last_message_id:
            await message.answer(f"📝 Текущий ID последнего сообщения для канала {channel_id}: {last_message_id}")
        else:
            await message.answer(f"⚠️ ID последнего сообщения не найден для канала {channel_id}")
    
    async def forward_now_command(self, message: types.Message):
        """Немедленная пересылка последних сохраненных сообщений из всех каналов"""
        if message.from_user.id != OWNER_ID:
            return
            
        source_channels = await Database.get_source_channels()
        if not source_channels:
            await message.answer("⚠️ Нет настроенных каналов-источников")
            return
            
        success = False
        for channel_id in source_channels:
            last_message_id = await Database.get_last_message(channel_id)
            if last_message_id:
                await message.answer(f"🔄 Начинаю пересылку сообщения ID: {last_message_id} из канала {channel_id}...")
                if await self.repost_saved_message(last_message_id, channel_id):
                    success = True

        if success:
            await message.answer("✅ Сообщения успешно пересланы во все активные чаты.")
        else:
            await message.answer("❌ Не удалось переслать сообщения ни из одного канала.")
    
    async def test_message_command(self, message: types.Message):
        """Тестирование существования сообщения в канале"""
        if message.from_user.id != OWNER_ID:
            return
            
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Используйте формат: /test <channel_id> <message_id>")
            return
            
        try:
            channel_id = args[1].lstrip('@')
            message_id = int(args[2])
            
            source_channels = await Database.get_source_channels()
            if channel_id not in source_channels:
                await message.answer("❌ Указанный канал не является источником")
                return
            
            # Пробуем переслать сообщение владельцу для проверки
            try:
                await message.answer(f"🔍 Проверяю сообщение ID {message_id} в канале {channel_id}...")
                
                forwarded = await self.bot.forward_message(
                    chat_id=OWNER_ID,
                    from_chat_id=channel_id,
                    message_id=message_id
                )
                
                if forwarded:
                    await message.answer(f"✅ Сообщение ID {message_id} существует и доступно для пересылки!")
                else:
                    await message.answer("⚠️ Проблема с пересылкой сообщения.")
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
                f"Рассылка {'запущена' if status == 'Started' else 'остановлена'}!",
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
            kb.button(text="↩️ Назад", callback_data="back_to_main")
            kb.adjust(3)
            
            try:
                await callback.message.edit_text(
                    "Выберите интервал рассылки:",
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
                    f"Интервал установлен: {display}",
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
        await callback.answer("Чат удален!")

    async def list_chats(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        
        # Re-register existing chats
        await self.register_existing_chats()
        
        chats = await Database.get_target_chats()
        if not chats:
            text = "Нет настроенных целевых чатов.\nУбедитесь что:\n1. Бот добавлен в целевые чаты\n2. Бот является администратором в каналах-источниках"
        else:
            text = "📡 Целевые чаты:\n\n"
            for chat_id in chats:
                try:
                    chat = await self.bot.get_chat(chat_id)
                    members = await self.bot.get_chat_member_count(chat_id)
                    text += f"• {chat.title}\n  ID: {chat_id}\n  Type: {chat.type}\n  Members: {members}\n\n"
                except Exception as e:
                    text += f"• Неизвестный чат ({chat_id})\n  Ошибка: {str(e)}\n\n"
                    logger.error(f"Ошибка получения информации о чате: {e}")
        
        kb = InlineKeyboardBuilder()
        for chat_id in chats:
            kb.button(
                text=f"❌ Удалить {chat_id}",
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
                last_messages_text += f"Канал: {channel_id}\nID сообщения: {data['message_id']}\nВремя: {data['timestamp']}\n\n"
        
        text = (
            "📊 Статистика рассылки\n\n"
            f"Всего пересланных сообщений: {stats['total_forwards']}\n"
            f"Последняя рассылка: {stats['last_forward'] or 'Никогда'}\n\n"
            f"Последние сохраненные сообщения:\n{last_messages_text or 'Нет'}"
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
            logger.info("Бот не запущен, игнорирую сообщение")
            return

        if message is None:
            # Получаем все каналы-источники
            source_channels = await Database.get_source_channels()
            if not source_channels:
                logger.warning("Нет настроенных каналов-источников")
                return

            success = False
            for channel_id in source_channels:
                last_message_id = await Database.get_last_message(channel_id)
                if last_message_id:
                    logger.info(f"Повторная отправка сообщения ID: {last_message_id} из канала {channel_id}")
                    if await self.repost_saved_message(last_message_id, channel_id):
                        success = True

            if not success:
                logger.warning("Не удалось переслать сообщения ни из одного канала")
            return

        # Handle normal channel post
        chat_id = str(message.chat.id)
        username = message.chat.username
        
        # Check if message is from any of our source channels
        source_channels = await Database.get_source_channels()
        source_channel = None

        for sc in source_channels:
            if chat_id == sc or (username and username.lower() == sc.lstrip('@').lower()):
                source_channel = sc
                break

        if not source_channel:
            logger.info(f"Сообщение не из канала-источника. Получено от {chat_id}/{username}")
            return
        
        logger.info(f"Пересылка сообщения {message.message_id} во все целевые чаты")
        
        # Сохраняем ID этого сообщения как последнее из канала
        await Database.save_last_message(source_channel, message.message_id)
        
        # Пересылаем сообщение во все целевые чаты
        await self.forward_to_all(message)

    async def repost_saved_message(self, message_id: int, source_channel: str):
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
                    from_chat_id=source_channel,
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
                    logger.info(f"Пропускаю пересылку в {chat.type} {chat_id}")
                    continue
                
                await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                await Database.log_forward(message.message_id)
                logger.info(f"Переслано в {chat.type} {chat.title} ({chat_id})")
            except Exception as e:
                logger.error(f"Ошибка пересылки в {chat_id}: {e}")
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
                    logger.info("Запущена периодическая рассылка")
                except Exception as e:
                    logger.error(f"Не удалось запустить рассылку: {e}")
                
            except asyncio.CancelledError:
                logger.info("Задача рассылки отменена")
                break
            except Exception as e:
                logger.error(f"Ошибка при выполнении резервной рассылки: {e}")
                await asyncio.sleep(60)  # Wait before retry

    async def add_channel_command(self, message: types.Message):
        """Add a new source channel"""
        if message.from_user.id != OWNER_ID:
            return

        args = message.text.split()
        if len(args) != 2:
            await message.answer("Используйте формат: /addchannel <channel_id или @username>")
            return

        channel_id = args[1].lstrip('@')
        
        try:
            # Try to get channel info
            chat = await self.bot.get_chat(channel_id)
            if chat.type != 'channel':
                await message.answer("❌ Указанный ID не является каналом")
                return

            # Check bot's rights in the channel
            bot_member = await self.bot.get_chat_member(chat.id, self.bot.id)
            if bot_member.status not in ['administrator']:
                await message.answer("❌ Бот должен быть администратором канала")
                return

            # Add to database
            await Database.add_source_channel(str(chat.id))
            await message.answer(f"✅ Канал {chat.title} успешно добавлен как источник")
            
        except Exception as e:
            await message.answer(f"❌ Ошибка при добавлении канала: {e}")

    async def remove_channel_command(self, message: types.Message):
        """Remove a source channel"""
        if message.from_user.id != OWNER_ID:
            return

        args = message.text.split()
        if len(args) != 2:
            await message.answer("Используйте формат: /removechannel <channel_id или @username>")
            return

        channel_id = args[1].lstrip('@')
        
        try:
            await Database.remove_source_channel(channel_id)
            await message.answer(f"✅ Канал {channel_id} удален из источников")
        except Exception as e:
            await message.answer(f"❌ Ошибка при удалении канала: {e}")

    async def list_channels_command(self, message: types.Message):
        """List all source channels"""
        if message.from_user.id != OWNER_ID:
            return

        channels = await Database.get_source_channels()
        if not channels:
            await message.answer("ℹ️ Нет настроенных каналов-источников")
            return

        text = "📋 Каналы-источники:\n\n"
        for channel_id in channels:
            try:
                chat = await self.bot.get_chat(channel_id)
                bot_member = await self.bot.get_chat_member(channel_id, self.bot.id)
                text += (f"• {chat.title}\n"
                        f"  ID: {channel_id}\n"
                        f"  Статус бота: {bot_member.status}\n\n")
            except Exception as e:
                text += f"• {channel_id}\n  ⚠️ Ошибка доступа: {str(e)}\n\n"

        await message.answer(text)

    async def main_menu(self, callback: types.CallbackQuery):
        if callback.from_user.id != OWNER_ID:
            return
        try:
            await callback.message.edit_text(
                "Главное меню:",
                reply_markup=self.get_main_keyboard()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
        await callback.answer()

    async def add_channel_menu(self, callback: types.CallbackQuery):
        """Обработчик кнопки добавления канала"""
        if callback.from_user.id != OWNER_ID:
            return
        
        kb = InlineKeyboardBuilder()
        kb.button(text="↩️ Назад", callback_data="back_to_main")
        kb.adjust(1)
        
        await callback.message.edit_text(
            "📝 Для добавления канала:\n\n"
            "1. Добавьте бота администратором в канал\n"
            "2. Перешлите любое сообщение из канала сюда\n"
            "или отправьте команду:\n"
            "/addchannel <ID канала или @username>",
            reply_markup=kb.as_markup()
        )
        await callback.answer()

    async def handle_forwarded_channel_message(self, message: types.Message):
        """Обработка пересланных сообщений для добавления канала"""
        if message.from_user.id != OWNER_ID:
            return

        if not message.forward_from_chat or message.forward_from_chat.type != 'channel':
            await message.answer("❌ Перешлите сообщение именно из канала")
            return

        channel = message.forward_from_chat
        try:
            # Проверяем права бота в канале
            bot_member = await self.bot.get_chat_member(channel.id, self.bot.id)
            if bot_member.status not in ['administrator']:
                await message.answer("❌ Бот должен быть администратором канала")
                return

            # Добавляем канал
            await Database.add_source_channel(str(channel.id))
            await message.answer(
                f"✅ Канал {channel.title} успешно добавлен как источник\n"
                f"ID: {channel.id}"
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка при добавлении канала: {e}")

    async def list_channels_menu(self, callback: types.CallbackQuery):
        """Обработчик кнопки списка каналов"""
        if callback.from_user.id != OWNER_ID:
            return

        channels = await Database.get_source_channels()
        if not channels:
            text = "ℹ️ Нет настроенных каналов-источников"
        else:
            text = "📋 Каналы-источники:\n\n"
            for channel_id in channels:
                try:
                    chat = await self.bot.get_chat(channel_id)
                    bot_member = await self.bot.get_chat_member(channel_id, self.bot.id)
                    text += (f"• {chat.title}\n"
                            f"  ID: {channel_id}\n"
                            f"  Статус бота: {bot_member.status}\n\n")
                except Exception as e:
                    text += f"• {channel_id}\n  ⚠️ Ошибка доступа: {str(e)}\n\n"

        kb = InlineKeyboardBuilder()
        kb.button(text="↩️ Назад", callback_data="back_to_main")
        kb.adjust(1)

        try:
            await callback.message.edit_text(text, reply_markup=kb.as_markup())
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
                f"Бот {'добавлен в' if update.new_chat_member.status in ['member', 'administrator'] else 'удален из'} "
                f"{update.chat.type} {update.chat.id}"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить владельца: {e}")

    async def verify_channel_access(self):
        """Verify access to all configured source channels"""
        try:
            source_channels = await Database.get_source_channels()
            if not source_channels:
                logger.warning("Нет настроенных каналов-источников, бот может быть запущен и настроен позже")
                return True

            all_access_ok = True
            access_errors = []

            for channel_id in source_channels:
                try:
                    # Try to get channel info
                    channel = await self.bot.get_chat(channel_id)
                    logger.info(f"Checking access to channel: {channel.title} ({channel.id})")
                    
                    # Try to get channel member count to verify admin rights
                    member_count = await self.bot.get_chat_member_count(channel_id)
                    logger.info(f"Channel {channel_id} member count: {member_count}")
                    
                    # Check bot's rights in the channel
                    bot_member = await self.bot.get_chat_member(channel_id, self.bot.id)
                    logger.info(f"Bot status in channel {channel_id}: {bot_member.status}")
                    
                    if bot_member.status not in ['administrator']:
                        access_errors.append(f"Bot needs admin rights in channel {channel.title}")
                        all_access_ok = False
                        
                except Exception as e:
                    access_errors.append(f"Failed to access channel {channel_id}: {e}")
                    all_access_ok = False
                    continue
            
            if access_errors:
                error_msg = "⚠️ Channel access check results:\n" + "\n".join(access_errors)
                try:
                    await self.bot.send_message(OWNER_ID, error_msg)
                except Exception as e:
                    logger.error(f"Не удалось отправить результаты проверки доступа владельцу: {e}")
            elif all_access_ok:
                await self.bot.send_message(OWNER_ID, "✅ Successfully connected to all source channels")

            return all_access_ok
            
        except Exception as e:
            logger.error(f"Failed to verify channel access: {e}")
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
            logger.error(f"Ошибка при регистрации существующих чатов: {e}")

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
        
        logger.info("Бот успешно запущен!")
        try:
            # Get the last update ID to avoid duplicate updates
            offset = 0
            try:
                updates = await self.bot.get_updates(limit=1, timeout=1)
                if updates:
                    offset = updates[-1].update_id + 1
            except Exception as e:
                logger.warning(f"Не удалось получить начальные обновления: {e}")

            logger.info(f"Запуск получения обновлений с отступом {offset}")
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
            logger.info("Удален устаревший файл блокировки")
        except PermissionError:
            logger.error("Невозможно получить доступ к файлу блокировки")
            return

    # Create lock file with current PID
    try:
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.error(f"Не удалось создать файл блокировки: {e}")
        return

    try:
        bot = ForwarderBot()
        await bot.start()
    finally:
        # Clean up lock file on exit
        try:
            os.remove(lock_file)
        except Exception as e:
            logger.error(f"Не удалось удалить файл блокировки: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot stopped due to error: {e}")
