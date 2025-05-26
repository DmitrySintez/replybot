from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Dict, List, Any

class KeyboardFactory:
    """Factory Pattern implementation for creating keyboards"""
    

    @staticmethod
    def create_main_keyboard(running: bool = False, auto_forward: bool = False) -> Any:
        """Create main menu keyboard"""
        kb = InlineKeyboardBuilder()
        kb.button(
            text="🔄 Начать пересылку" if not running else "⏹ Остановить пересылку",
            callback_data="toggle_forward"
        )
        kb.button(
            text=f"⚡ Автопересылка: {'ВКЛ' if auto_forward else 'ВЫКЛ'}",
            callback_data="toggle_auto_forward"
        )
        kb.button(text="⏱️ Установить интервал", callback_data="interval_menu")
        kb.button(text="📊 Показать статистику", callback_data="stats")
        kb.button(text="⚙️ Управление каналами", callback_data="channels")
        kb.button(text="💬 Список целевых чатов", callback_data="list_chats")
        kb.button(text="🤖 Клонировать бота", callback_data="clone_bot")
        kb.button(text="👥 Управление клонами", callback_data="manage_clones")
        kb.adjust(2)
        return kb.as_markup()

    @staticmethod
    async def create_interval_keyboard() -> Any:
        """Create interval selection keyboard"""
        kb = InlineKeyboardBuilder()
        intervals = [
            ("5м", 300), ("15м", 900), ("30м", 1800),
            ("1ч", 3600), ("2ч", 7200), ("6ч", 21600), 
            ("12ч", 43200), ("24ч", 86400)
        ]
        for label, seconds in intervals:
            kb.button(text=label, callback_data=f"interval_{seconds}")
        kb.button(text="Назад", callback_data="back_to_main")
        kb.adjust(4)
        return kb.as_markup()

    @staticmethod
    def create_chat_list_keyboard(chats: Dict[int, str]) -> Any:
        """Create chat list keyboard with remove buttons"""
        kb = InlineKeyboardBuilder()
        for chat_id, title in chats.items():
            kb.button(
                text=f"❌ Удалить {title}",
                callback_data=f"remove_{chat_id}"
            )
        kb.button(text="Назад", callback_data="back_to_main")
        kb.adjust(1)
        return kb.as_markup()

    @staticmethod
    def create_channel_interval_keyboard(channels: List[str]) -> Any:
        """Create keyboard for setting intervals between channels"""
        kb = InlineKeyboardBuilder()
        
        for i, channel in enumerate(channels):
            if i < len(channels) - 1:
                next_channel = channels[i + 1]
                display_name1 = channel[:10] + "..." if len(channel) > 13 else channel
                display_name2 = next_channel[:10] + "..." if len(next_channel) > 13 else next_channel
                kb.button(
                    text=f"⏱️ {display_name1} → {display_name2}",
                    callback_data=f"interval_between_{channel}_{next_channel}"
                )
        
        kb.button(text="Назад", callback_data="channels")
        kb.adjust(1)
        return kb.as_markup()

    @staticmethod
    def create_channel_interval_options(channel1: str, channel2: str) -> Any:
        """Create keyboard with interval options between two channels"""
        kb = InlineKeyboardBuilder()
        intervals = [
            ("1м", 60), ("5м", 300), ("10м", 600),
            ("15м", 900), ("30м", 1800), ("1ч", 3600)
        ]
        
        for label, seconds in intervals:
            kb.button(
                text=label, 
                callback_data=f"set_interval_{channel1}_{channel2}_{seconds}"
            )
        
        kb.button(text="Назад", callback_data="channel_intervals")
        kb.adjust(3)
        return kb.as_markup()

    @staticmethod
    def create_channel_management_keyboard(channels: List[str]) -> Any:
        """Create channel management keyboard, now with reorder option"""
        kb = InlineKeyboardBuilder()
        # Новая кнопка для перехода в режим сортировки
        kb.button(text="↕️ Изменить порядок", callback_data="reorder_channels")
        # Существующая кнопка добавления
        kb.button(text="➕ Добавить канал", callback_data="add_channel")
        
        if len(channels) >= 2:
            kb.button(text="⏱️ Настроить интервалы между каналами", callback_data="channel_intervals")
        
        # Add buttons for each channel
        for channel in channels:
            # Truncate channel name if too long
            display_name = channel[:15] + "..." if len(channel) > 18 else channel
            
            # Button only for removing channels
            kb.button(
                text=f"❌ Удалить ({display_name})",
                callback_data=f"remove_channel_{channel}"
            )
        
        kb.button(text="Назад", callback_data="back_to_main")
        kb.adjust(1)
        return kb.as_markup()