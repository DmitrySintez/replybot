"""
Callback query handlers for the Telegram Forwarder Bot.

This module registers and implements all callback query handlers for inline buttons.
"""

from loguru import logger
from aiogram import Dispatcher, types
from aiogram.exceptions import TelegramAPIError

from src.utils.keyboards import KeyboardFactory


async def toggle_forwarding_callback(callback: types.CallbackQuery, owner_id: int, bot_instance):
    """Handle toggle_forward callback to start/stop forwarding."""
    if callback.from_user.id != owner_id:
        return
    
    if bot_instance.running:
        # Stop forwarding
        await bot_instance.stop_forwarding()
        status = "Stopped"
    else:
        # Start forwarding
        await bot_instance.start_forwarding()
        status = "Started"
    
    try:
        await callback.message.edit_text(
            f"Forwarding {status}!",
            reply_markup=KeyboardFactory.main_menu(bot_instance.running)
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"Error updating message: {e}")
    
    await callback.answer()


async def set_interval_callback(callback: types.CallbackQuery, owner_id: int, db, bot_instance):
    """Handle interval_* callbacks to set repost interval."""
    if callback.from_user.id != owner_id:
        return
    
    if callback.data == "interval_menu":
        try:
            await callback.message.edit_text(
                "Select repost interval:",
                reply_markup=KeyboardFactory.interval_menu()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Error updating message: {e}")
    else:
        interval = int(callback.data.split("_")[1])
        await db.set_config("repost_interval", str(interval))
        
        if bot_instance.running:
            # Restart forwarding with new interval
            await bot_instance.stop_forwarding()
            await bot_instance.start_forwarding(interval)
        
        try:
            # Format interval display
            if interval < 60:
                display = f"{interval} seconds"
            elif interval < 3600:
                display = f"{interval // 60} minutes"
            else:
                display = f"{interval // 3600} hours"
                
            await callback.message.edit_text(
                f"Interval set to {display}",
                reply_markup=KeyboardFactory.main_menu(bot_instance.running)
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Error updating message: {e}")
    
    await callback.answer()


async def remove_chat_callback(callback: types.CallbackQuery, owner_id: int, db, bot_instance):
    """Handle remove_* callbacks to remove target chats."""
    if callback.from_user.id != owner_id:
        return
    
    chat_id = int(callback.data.split("_")[1])
    
    try:
        # Get chat info before removal
        try:
            chat = await bot_instance.bot.get_chat(chat_id)
            chat_title = chat.title
        except:
            chat_title = f"Chat {chat_id}"
        
        # Remove the chat
        await db.remove_target_chat(chat_id)
        
        await callback.answer(f"Removed {chat_title}")
        
        # Refresh the chat list
        await list_chats_callback(callback, owner_id, db, bot_instance.bot)
    except Exception as e:
        logger.error(f"Error removing chat {chat_id}: {e}")
        await callback.answer(f"Error: {str(e)}", show_alert=True)


async def show_stats_callback(callback: types.CallbackQuery, owner_id: int, db):
    """Handle stats callback to show forwarding statistics."""
    if callback.from_user.id != owner_id:
        return
    
    try:
        stats = await db.get_stats()
        
        # Format last messages info
        last_messages_text = ""
        if stats.get("last_messages"):
            for channel_id, data in stats["last_messages"].items():
                last_messages_text += (
                    f"• Channel: <code>{channel_id}</code>\n"
                    f"  Message ID: {data['message_id']}\n"
                    f"  Timestamp: {data['timestamp']}\n\n"
                )
        
        total_forwards = stats.get('total_forwards', 0)
        last_forward = stats.get('last_forward', 'Never')
        
        text = (
            "📊 <b>Forwarding Statistics</b>\n\n"
            f"Total forwards: {total_forwards}\n"
            f"Last forward: {last_forward or 'Never'}\n\n"
            f"<b>Last saved messages:</b>\n{last_messages_text or 'None'}"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=KeyboardFactory.stats_keyboard()
        )
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        await callback.answer(f"Error: {str(e)}", show_alert=True)
    
    await callback.answer()


async def list_chats_callback(callback: types.CallbackQuery, owner_id: int, db, bot):
    """Handle list_chats callback to show and manage target chats."""
    if callback.from_user.id != owner_id:
        return
    
    try:
        # Get target chats
        chat_ids = await db.get_target_chats()
        
        if not chat_ids:
            text = (
                "No target chats configured.\n\n"
                "To add chats:\n"
                "1. Add the bot to target groups/supergroups\n"
                "2. Make the bot an admin in the source channel"
            )
            
            await callback.message.edit_text(
                text,
                reply_markup=KeyboardFactory.back_button()
            )
            await callback.answer()
            return
        
        # Process each chat to get more information
        processed_chats = []
        text = "<b>📡 Target Chats:</b>\n\n"
        
        for chat_id in chat_ids:
            try:
                chat = await bot.get_chat(chat_id)
                member_count = await bot.get_chat_member_count(chat_id)
                
                # Store for keyboard creation
                processed_chats.append({
                    'id': chat_id,
                    'title': chat.title,
                    'member_count': member_count
                })
                
                # Add to text
                text += (
                    f"• <b>{chat.title}</b>\n"
                    f"  ID: <code>{chat_id}</code>\n"
                    f"  Type: {chat.type}\n"
                    f"  Members: {member_count}\n\n"
                )
            except Exception as e:
                # Chat might be inaccessible
                processed_chats.append({
                    'id': chat_id,
                    'title': f"Unknown Chat ({chat_id})",
                    'member_count': 0
                })
                
                text += (
                    f"• <b>Unknown Chat</b>\n"
                    f"  ID: <code>{chat_id}</code>\n"
                    f"  Error: {str(e)}\n\n"
                )
                logger.error(f"Error getting info for chat {chat_id}: {e}")
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=KeyboardFactory.chat_list(processed_chats)
        )
    except Exception as e:
        logger.error(f"Error listing chats: {e}")
        await callback.answer(f"Error: {str(e)}", show_alert=True)
    
    await callback.answer()


async def find_last_callback(callback: types.CallbackQuery, owner_id: int, bot_instance):
    """Handle find_last callback to find the last valid message."""
    if callback.from_user.id != owner_id:
        return
    
    message_handler = bot_instance.message_handler
    
    try:
        # Update message to show progress
        await callback.message.edit_text(
            "🔍 Searching for last valid message...\n\n"
            "This may take a moment."
        )
        
        # Find the last valid message
        valid_id, checked_count = await message_handler.find_last_valid_message()
        
        if valid_id:
            await callback.message.edit_text(
                f"✅ Found valid message with ID {valid_id} after checking {checked_count} messages.\n\n"
                f"This ID is now set as the last message for periodic forwarding.",
                reply_markup=KeyboardFactory.message_controls(valid_id)
            )
        else:
            await callback.message.edit_text(
                f"❌ Could not find a valid message after checking {checked_count} messages.\n\n"
                f"Set an ID manually using /setlast command.",
                reply_markup=KeyboardFactory.back_button()
            )
    except Exception as e:
        logger.error(f"Error finding last message: {e}")
        await callback.answer(f"Error: {str(e)}", show_alert=True)
        await callback.message.edit_text(
            f"❌ Error finding last message: {str(e)}",
            reply_markup=KeyboardFactory.back_button()
        )
    
    await callback.answer()


async def forward_message_callback(callback: types.CallbackQuery, owner_id: int, message_handler):
    """Handle forward_* callbacks to forward a specific message."""
    if callback.from_user.id != owner_id:
        return
    
    try:
        message_id = int(callback.data.split("_")[1])
        source_channel = message_handler.source_channel
        
        # Update message to show progress
        await callback.message.edit_text(
            f"🔄 Forwarding message ID: {message_id}..."
        )
        
        # Forward the message
        success = await message_handler.message_processor.repost_saved_message(
            message_id=message_id,
            source_channel=source_channel
        )
        
        if success:
            await callback.message.edit_text(
                f"✅ Message {message_id} successfully forwarded to all active chats.",
                reply_markup=KeyboardFactory.message_controls(message_id)
            )
        else:
            await callback.message.edit_text(
                f"❌ Failed to forward message {message_id} to any chat.",
                reply_markup=KeyboardFactory.back_button()
            )
    except Exception as e:
        logger.error(f"Error forwarding message: {e}")
        await callback.answer(f"Error: {str(e)}", show_alert=True)
    
    await callback.answer()


async def test_message_callback(callback: types.CallbackQuery, owner_id: int, message_handler):
    """Handle test_* callbacks to test a specific message."""
    if callback.from_user.id != owner_id:
        return
    
    try:
        message_id = int(callback.data.split("_")[1])
        source_channel = message_handler.source_channel
        
        # Update message to show progress
        await callback.message.edit_text(
            f"🔍 Testing message ID {message_id}..."
        )
        
        try:
            # Try to forward the message to verify it exists
            await message_handler.bot.forward_message(
                chat_id=owner_id,
                from_chat_id=source_channel,
                message_id=message_id
            )
            
            # Get additional message info
            info = await message_handler.message_processor.get_message_info(
                channel_id=source_channel,
                message_id=message_id
            )
            
            media_type = info.get('media_type', 'none')
            text_length = len(info.get('text', ''))
            
            await callback.message.edit_text(
                f"✅ Message ID {message_id} exists and can be forwarded!\n\n"
                f"Type: {media_type}\n"
                f"Text length: {text_length} characters",
                reply_markup=KeyboardFactory.message_controls(message_id)
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ Error testing message {message_id}:\n\n{str(e)}",
                reply_markup=KeyboardFactory.back_button()
            )
    except Exception as e:
        logger.error(f"Error testing message: {e}")
        await callback.answer(f"Error: {str(e)}", show_alert=True)
    
    await callback.answer()


async def main_menu_callback(callback: types.CallbackQuery, owner_id: int, bot_instance):
    """Handle back_to_main callback to return to main menu."""
    if callback.from_user.id != owner_id:
        return
    
    try:
        await callback.message.edit_text(
            "Main Menu:",
            reply_markup=KeyboardFactory.main_menu(bot_instance.running)
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"Error updating message: {e}")
    
    await callback.answer()


def register_callback_handlers(dp: Dispatcher, owner_id: int, db, bot_instance):
    """Register all callback query handlers with the dispatcher."""
    # Main menu actions
    dp.callback_query.register(
        lambda c: toggle_forwarding_callback(c, owner_id, bot_instance),
        lambda c: c.data == "toggle_forward"
    )
    
    dp.callback_query.register(
        lambda c: set_interval_callback(c, owner_id, db, bot_instance),
        lambda c: c.data == "interval_menu" or c.data.startswith("interval_")
    )
    
    dp.callback_query.register(
        lambda c: show_stats_callback(c, owner_id, db),
        lambda c: c.data == "stats" or c.data == "refresh_stats"
    )
    
    dp.callback_query.register(
        lambda c: list_chats_callback(c, owner_id, db, bot_instance.bot),
        lambda c: c.data == "list_chats" or c.data == "refresh_chats"
    )
    
    dp.callback_query.register(
        lambda c: find_last_callback(c, owner_id, bot_instance),
        lambda c: c.data == "find_last"
    )
    
    # Chat management
    dp.callback_query.register(
        lambda c: remove_chat_callback(c, owner_id, db, bot_instance),
        lambda c: c.data.startswith("remove_")
    )
    
    # Message actions
    dp.callback_query.register(
        lambda c: forward_message_callback(c, owner_id, bot_instance.message_handler),
        lambda c: c.data.startswith("forward_")
    )
    
    dp.callback_query.register(
        lambda c: test_message_callback(c, owner_id, bot_instance.message_handler),
        lambda c: c.data.startswith("test_")
    )
    
    # Navigation
    dp.callback_query.register(
        lambda c: main_menu_callback(c, owner_id, bot_instance),
        lambda c: c.data == "back_to_main"
    )
    
    logger.info("Registered callback handlers")