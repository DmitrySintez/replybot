from abc import ABC, abstractmethod
from typing import Optional
import asyncio
from loguru import logger
from database.repository import Repository
from datetime import datetime
from aiogram import types

class BotState(ABC):
    """Abstract base class for bot states"""
    
    @abstractmethod
    async def start(self) -> None:
        """Handle start action"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Handle stop action"""
        pass
    
    @abstractmethod
    async def handle_message(self, channel_id: str, message_id: int) -> None:
        """Handle message forwarding"""
        pass

class IdleState(BotState):
    """State when bot is not forwarding messages"""
    
    def __init__(self, bot_context, auto_forward: bool = False):
        self.context = bot_context
        self.auto_forward = auto_forward
    
    async def start(self) -> None:
        interval = int(await Repository.get_config("repost_interval", "3600"))
        self.context.state = RunningState(self.context, interval, self.auto_forward)
        await self.context._notify_admins("Бот начал пересылку")
    
    async def stop(self) -> None:
        # Already stopped
        pass
    
    async def handle_message(self, channel_id: str, message_id: int) -> None:
        # Don't forward messages in idle state
        logger.info("Bot is idle, not forwarding messages")

class RunningState(BotState):
    """State when bot is actively forwarding messages"""
    
    def __init__(self, bot_context, interval: int, auto_forward: bool = False):
        self.context = bot_context
        self.interval = interval  # Global repost interval
        self._repost_task: Optional[asyncio.Task] = None
        self.auto_forward = auto_forward
        
        # Initialize tracking for each channel's last post time
        now = datetime.now().timestamp()
        self._channel_last_post = {}
        
        # Important: Initialize all channels with current time
        # This forces each channel to wait for the full interval before posting
        for channel in self.context.config.source_channels:
            self._channel_last_post[channel] = now
        
        # Initialize these attributes to track channel rotation
        self._last_processed_channel = None
        self._last_global_post_time = now
        
        # Start the repost task
        self._start_repost_task()
        
    def _start_repost_task(self):
        # Always start the repost task if it's not running, regardless of auto_forward setting
        if not self._repost_task or self._repost_task.done():
            self._repost_task = asyncio.create_task(self._fallback_repost())

    async def toggle_auto_forward(self):
        """Toggle automatic message forwarding"""
        self.auto_forward = not self.auto_forward
        logger.info(f"Автопересылка: {self.auto_forward}")
        
    async def start(self) -> None:
        # Already running
        pass
    
    async def stop(self) -> None:
        if self._repost_task and not self._repost_task.done():
            self._repost_task.cancel()
        self.auto_forward = False
        self.context.state = IdleState(self.context, self.auto_forward)
        await self.context._notify_admins("Бот остановил пересылку")
    
    # Также модифицируем метод handle_message в классе RunningState в файле utils/bot_state.py

    # Также модифицируем метод handle_message в классе RunningState в файле utils/bot_state.py
# для пересылки в прямом порядке

    async def handle_message(self, channel_id: str, message_id: int) -> None:
        """Обрабатывает пересылку сообщений - теперь пересылает в прямом порядке"""
        if self.auto_forward:
            # Определяем диапазон ID сообщений для пересылки
            max_id = message_id
            start_id = max(1, max_id - 100)  # Берем только последние ~100 сообщений
            
            logger.info(f"Пересылка сообщений из канала {channel_id} ({start_id}-{max_id}) в прямом порядке")
            
            # Счетчики для статистики
            forwarded_count = 0
            error_count = 0
            
            # Пересылаем доступные сообщения в прямом порядке (от старых к новым)
            for msg_id in range(start_id, max_id + 1):  # Прямой порядок
                try:
                    success = await self.context._forward_message(channel_id, msg_id)
                    if success:
                        forwarded_count += 1
                    else:
                        error_count += 1
                    # Небольшая задержка между запросами
                    await asyncio.sleep(0.1)
                except Exception as e:
                    error_count += 1
                    if "message to forward not found" not in str(e) and "message can't be forwarded" not in str(e):
                        logger.error(f"Ошибка при пересылке сообщения {msg_id} из канала {channel_id}: {e}")
            
            # Обновляем время последней пересылки для этого канала
            self._channel_last_post[channel_id] = datetime.now().timestamp()
            
            logger.info(f"Пересылка сообщений из канала {channel_id} завершена: переслано {forwarded_count}, ошибок {error_count}")
        else:
            logger.info("Автопересылка отключена, пропускаем сообщение")
    
    async def _get_next_channel_to_repost(self):
        """Get the next channel that should be reposted based on intervals"""
        now = datetime.now().timestamp()
        source_channels = self.context.config.source_channels
        
        if not source_channels:
            return None
        
        # Use the interval set by the user
        channel_interval = self.interval
        
        # Find the channel that hasn't been posted for the longest time
        oldest_channel = None
        oldest_time = now
        
        for channel in source_channels:
            last_post_time = self._channel_last_post.get(channel, 0)
            
            # If this channel hasn't been posted for more than the interval
            # and is older than our current oldest, select it
            if now - last_post_time >= channel_interval and last_post_time < oldest_time:
                oldest_channel = channel
                oldest_time = last_post_time
                
        return oldest_channel
        
    async def _get_channel_pair_interval(self, channel1: str, channel2: str) -> Optional[int]:
        """Get the interval between two channels (if set)"""
        try:
            # Get from database
            intervals = await Repository.get_channel_intervals()
            
            # Check if this pair has a configured interval
            for pair_key, pair_data in intervals.items():
                if pair_key == channel1 and pair_data["next_channel"] == channel2:
                    return pair_data["interval"]
            
            return None  # No specific interval set
        except Exception as e:
            logger.error(f"Error getting channel pair interval: {e}")
            return None
        
    # Также улучшим периодическую пересылку в методе _fallback_repost в RunningState:

    async def _fallback_repost(self):
        """Periodic repost task with improved handling"""
        while True:
            try:
                await asyncio.sleep(10)
                
                now = datetime.now().timestamp()
                source_channels = self.context.config.source_channels
                if not source_channels:
                    logger.warning("Нет настроенных исходных каналов")
                    continue
                
                # Находим каналы, готовые для пересылки
                eligible_channels = []
                for channel in source_channels:
                    last_post_time = self._channel_last_post.get(channel, 0)
                    
                    if now - last_post_time >= self.interval:
                        eligible_channels.append(channel)
                
                if not eligible_channels:
                    continue
                    
                next_channel = None
                
                # Логика выбора следующего канала для пересылки
                if self._last_processed_channel is None:
                    next_channel = eligible_channels[0]
                    logger.debug(f"Первый запуск, выбран канал {next_channel}")
                else:
                    current_idx = -1
                    try:
                        current_idx = source_channels.index(self._last_processed_channel)
                    except ValueError:
                        pass
                    
                    for i in range(1, len(source_channels) + 1):
                        next_idx = (current_idx + i) % len(source_channels)
                        candidate = source_channels[next_idx]
                        
                        if candidate in eligible_channels:
                            next_channel = candidate
                            logger.debug(f"Следующий канал {next_channel} готов для пересылки")
                            break
                
                if next_channel is None:
                    continue
                    
                # Получаем ID последнего сообщения в канале
                message_id = await Repository.get_last_message(next_channel)

                if not message_id:
                    logger.warning(f"Не найдено сообщение для канала {next_channel}")
                    
                    latest_id = await self.context.find_latest_message(next_channel)
                    if latest_id:
                        message_id = latest_id
                        await Repository.save_last_message(next_channel, latest_id)
                    else:
                        self._channel_last_post[next_channel] = now
                        continue

                # Определяем диапазон ID сообщений для пересылки
                max_id = message_id
                start_id = max(1, max_id - 100)  # Берем только последние ~100 сообщений

                logger.info(f"Пересылка сообщений из канала {next_channel} ({start_id}-{max_id}) в прямом порядке")

                # Счетчики для статистики
                forwarded_count = 0
                error_count = 0

                # Пересылаем доступные сообщения в прямом порядке (от старых к новым)
                for msg_id in range(start_id, max_id + 1):  # Прямой порядок
                    try:
                        success = await self.context._forward_message(next_channel, msg_id)
                        if success:
                            forwarded_count += 1
                        else:
                            error_count += 1
                        # Небольшая задержка между запросами
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        error_count += 1
                        if "message to forward not found" not in str(e) and "message can't be forwarded" not in str(e):
                            logger.error(f"Ошибка при пересылке сообщения {msg_id} из канала {next_channel}: {e}")

                # Обновляем время последней пересылки
                now = datetime.now().timestamp()
                self._channel_last_post[next_channel] = now
                self._last_global_post_time = now
                self._last_processed_channel = next_channel

                next_global_time = now + self.interval
                next_time_str = datetime.fromtimestamp(next_global_time).strftime('%H:%M:%S')

                minutes = self.interval // 60
                logger.info(f"Переслано {forwarded_count} сообщений из канала {next_channel} (ошибок: {error_count}). Следующая пересылка через {minutes} минут (в {next_time_str}).")
                
            except asyncio.CancelledError:
                logger.info("Задача рассылки отменена")
                break
            except Exception as e:
                logger.error(f"Ошибка в периодической рассылке: {e}")
                await asyncio.sleep(60)
                
class BotContext:
    """Context class that maintains current bot state"""
    
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.state: BotState = IdleState(self)
    
    async def start(self) -> None:
        await self.state.start()
    
    async def stop(self) -> None:
        await self.state.stop()
    
    async def handle_message(self, channel_id: str, message_id: int) -> None:
        await self.state.handle_message(channel_id, message_id)
    
    
    # Улучшим метод _forward_message в классе BotContext в файле utils/bot_state.py
# для большей надежности пересылки:

    async def _forward_message(self, channel_id: str, message_id: int) -> bool:
        """Forward a message to all target chats with improved reliability"""
        success = False
        target_chats = await Repository.get_target_chats()
        
        if not target_chats:
            logger.warning("Нет целевых чатов для пересылки")
            return False

        # Проверяем существование сообщения перед пересылкой
        try:
            # Попытка получить информацию о сообщении
            await self.bot.get_messages(channel_id, message_id)
        except Exception as e:
            if "message to forward not found" in str(e) or "message not found" in str(e):
                logger.debug(f"Сообщение {message_id} не найдено в канале {channel_id}")
                return False
            # Игнорируем ошибку, если не можем проверить сообщение,
            # но попробуем переслать его все равно

        for chat_id in target_chats:
            if str(chat_id) == channel_id:
                logger.info(f"Пропускаю пересылку в исходный канал {chat_id}")
                continue
                
            try:
                chat_info = await self.bot.get_chat(chat_id)
                if chat_info.type == 'channel':
                    logger.info(f"Пропускаю пересылку в канал {chat_id} (каналы не являются целевыми)")
                    continue
                
                # Проверяем права бота в целевом чате
                bot_member = await self.bot.get_chat_member(chat_id, self.bot.id)
                if not any([bot_member.status == "administrator", 
                        bot_member.status == "creator"]):
                    logger.warning(f"У бота нет прав администратора в чате {chat_id}, пересылка может быть невозможна")
                
                # Пробуем переслать сообщение
                await self.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=channel_id,
                    message_id=message_id
                )
                await Repository.log_forward(message_id)
                success = True
                logger.debug(f"Сообщение {message_id} успешно переслано в {chat_id}")
            except Exception as e:
                error_text = str(e).lower()
                if "message to forward not found" in error_text or "message can't be forwarded" in error_text:
                    logger.debug(f"Сообщение {message_id} недоступно для пересылки в {chat_id}")
                elif "bot was blocked by the user" in error_text:
                    logger.warning(f"Бот заблокирован в чате {chat_id}")
                    # Можно удалить этот чат из целевых, чтобы не пытаться пересылать в него в будущем
                    # await Repository.remove_target_chat(chat_id)
                elif "chat not found" in error_text:
                    logger.warning(f"Чат {chat_id} не найден, возможно бот был удален из группы")
                    # Можно удалить этот чат из целевых
                    # await Repository.remove_target_chat(chat_id)
                else:
                    logger.error(f"Ошибка при пересылке в {chat_id}: {e}")

        return success
    
    async def _notify_owner(self, message: str):
        """Send notification to bot owner (for compatibility)"""
        try:
            await self.bot.send_message(self.config.owner_id, message)
        except Exception as e:
            logger.error(f"Не удалось уведомить владельца: {e}")
            
    async def _notify_admins(self, message: str):
        """Send notification to all bot admins"""
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, message)
            except Exception as e:
                logger.error(f"Не удалось уведомить администратора {admin_id}: {e}")