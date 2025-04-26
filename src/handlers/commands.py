"""
Keyboard creation utilities for the Telegram Forwarder Bot.

This module provides factories for creating inline keyboards for the bot UI.
"""

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup


class KeyboardFactory:
    """Factory class for creating inline keyboards."""
    
    @staticmethod
    def main_menu(is_running: bool = False) -> InlineKeyboardMarkup:
        """
        Create the main menu keyboard.
        
        Args:
            is_running: Whether the bot is currently running
            
        Returns:
            InlineKeyboardMarkup: The main menu keyboard
        """
        kb = InlineKeyboardBuilder()
        kb.button(
            text="🔄 Start Forwarding" if not is_running else "⏹ Stop Forwarding",
            callback_data="toggle_forward"
        )
        kb.button(text="⚙️ Set Interval", callback_data="interval_menu")
        kb.button(text="📊 Show Stats", callback_data="stats")
        kb.button(text="📋 List Chats", callback_data="list_chats")
        kb.button(text="🔍 Find Last Message", callback_data="find_last")
        kb.adjust(2)
        return kb.as_markup()
    
    @staticmethod
    def interval_menu() -> InlineKeyboardMarkup:
        """
        Create the interval selection keyboard.
        
        Returns:
            InlineKeyboardMarkup: The interval selection keyboard
        """
        kb = InlineKeyboardBuilder()
        intervals = [
            (5, "5m", 300),      # 5 minutes
            (15, "15m", 900),    # 15 minutes
            (30, "30m", 1800),   # 30 minutes
            (60, "1h", 3600),    # 1 hour
            (120, "2h", 7200),   # 2 hours
            (360, "6h", 21600),  # 6 hours
            (720, "12h", 43200), # 12 hours
            (1440, "24h", 86400) # 24 hours
        ]
        
        for minutes, label, seconds in intervals:
            kb.button(
                text=label,
                callback_data=f"interval_{seconds}"
            )
        
        kb.button(text="🔙 Back", callback_data="back_to_main")
        kb.adjust(4, 4, 1)  # 4 buttons in first row, 4 in second, 1 in third
        return kb.as_markup()
    
    @staticmethod
    def chat_list(chats: list, show_counts: bool = True) -> InlineKeyboardMarkup:
        """
        Create a keyboard with chat removal buttons.
        
        Args:
            chats: List of chat data dictionaries with id, title, and member_count
            show_counts: Whether to show member counts
            
        Returns:
            InlineKeyboardMarkup: The chat list keyboard
        """
        kb = InlineKeyboardBuilder()
        
        for chat in chats:
            chat_id = chat.get('id')
            title = chat.get('title', f"Chat {chat_id}")
            count = chat.get('member_count', 0)
            
            button_text = f"❌ {title}"
            if show_counts and count > 0:
                button_text += f" ({count})"
                
            kb.button(
                text=button_text,
                callback_data=f"remove_{chat_id}"
            )
        
        kb.button(text="🔄 Refresh", callback_data="refresh_chats")
        kb.button(text="🔙 Back", callback_data="back_to_main")
        kb.adjust(1)  # One button per row
        return kb.as_markup()
    
    @staticmethod
    def confirmation(action: str, data: str = None) -> InlineKeyboardMarkup:
        """
        Create a confirmation keyboard.
        
        Args:
            action: The action to confirm (used in callback data)
            data: Additional data for the callback
            
        Returns:
            InlineKeyboardMarkup: The confirmation keyboard
        """
        kb = InlineKeyboardBuilder()
        callback_prefix = f"{action}_{data}_" if data else f"{action}_"
        
        kb.button(text="✅ Yes", callback_data=f"{callback_prefix}yes")
        kb.button(text="❌ No", callback_data=f"{callback_prefix}no")
        kb.adjust(2)  # Two buttons in one row
        return kb.as_markup()
    
    @staticmethod
    def back_button() -> InlineKeyboardMarkup:
        """
        Create a simple back button.
        
        Returns:
            InlineKeyboardMarkup: A keyboard with just a back button
        """
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Back", callback_data="back_to_main")
        return kb.as_markup()
    
    @staticmethod
    def stats_keyboard() -> InlineKeyboardMarkup:
        """
        Create a keyboard for the stats screen.
        
        Returns:
            InlineKeyboardMarkup: The stats keyboard
        """
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Refresh Stats", callback_data="refresh_stats")
        kb.button(text="📊 Detailed Stats", callback_data="detailed_stats")
        kb.button(text="🔙 Back", callback_data="back_to_main")
        kb.adjust(2, 1)  # 2 buttons in first row, 1 in second
        return kb.as_markup()
    
    @staticmethod
    def message_controls(message_id: int) -> InlineKeyboardMarkup:
        """
        Create a keyboard for controlling messages.
        
        Args:
            message_id: The ID of the message
            
        Returns:
            InlineKeyboardMarkup: The message controls keyboard
        """
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Forward Now", callback_data=f"forward_{message_id}")
        kb.button(text="🔍 Test", callback_data=f"test_{message_id}")
        kb.button(text="❌ Delete", callback_data=f"delete_{message_id}")
        kb.button(text="🔙 Back", callback_data="back_to_main")
        kb.adjust(2, 2)  # 2 buttons per row
        return kb.as_markup()