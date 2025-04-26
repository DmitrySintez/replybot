from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Dict, List, Any

class KeyboardFactory:
    """Factory Pattern implementation for creating keyboards"""
    
    @staticmethod
    def create_main_keyboard(running: bool = False) -> Any:
        """Create main menu keyboard"""
        kb = InlineKeyboardBuilder()
        kb.button(
            text="🔄 Start Forwarding" if not running else "⏹ Stop Forwarding",
            callback_data="toggle_forward"
        )
        kb.button(text="⚙️ Set Interval", callback_data="interval_menu")
        kb.button(text="📊 Show Stats", callback_data="stats")
        kb.button(text="📡 Manage Channels", callback_data="channels")
        kb.button(text="📋 List Target Chats", callback_data="list_chats")
        kb.adjust(2)
        return kb.as_markup()

    @staticmethod
    def create_interval_keyboard() -> Any:
        """Create interval selection keyboard"""
        kb = InlineKeyboardBuilder()
        intervals = [
            ("5m", 300), ("15m", 900), ("30m", 1800),
            ("1h", 3600), ("2h", 7200), ("6h", 21600), 
            ("12h", 43200), ("24h", 86400)
        ]
        for label, seconds in intervals:
            kb.button(text=label, callback_data=f"interval_{seconds}")
        kb.button(text="Back", callback_data="back_to_main")
        kb.adjust(4)
        return kb.as_markup()

    @staticmethod
    def create_chat_list_keyboard(chats: Dict[int, str]) -> Any:
        """Create chat list keyboard with remove buttons"""
        kb = InlineKeyboardBuilder()
        for chat_id, title in chats.items():
            kb.button(
                text=f"❌ Remove {title}",
                callback_data=f"remove_{chat_id}"
            )
        kb.button(text="Back", callback_data="back_to_main")
        kb.adjust(1)
        return kb.as_markup()
        
    @staticmethod
    def create_channel_management_keyboard(channels: List[str]) -> Any:
        """Create channel management keyboard"""
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Add Channel", callback_data="add_channel")
        
        # Add remove buttons for each channel
        for channel in channels:
            # Truncate channel name if too long
            display_name = channel[:20] + "..." if len(channel) > 23 else channel
            kb.button(
                text=f"❌ {display_name}",
                callback_data=f"remove_channel_{channel}"
            )
        
        kb.button(text="Back", callback_data="back_to_main")
        kb.adjust(1)
        return kb.as_markup()