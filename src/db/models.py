"""
Database schema models for the Telegram Forwarder Bot.

This module defines the SQL schemas for all database tables used by the application.
"""

# Config table stores bot configuration parameters
CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""

# Target chats table stores the list of chats to forward messages to
TARGET_CHATS_TABLE = """
CREATE TABLE IF NOT EXISTS target_chats (
    chat_id INTEGER PRIMARY KEY,
    chat_title TEXT,
    chat_type TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Forward stats table logs statistics about forwarded messages
FORWARD_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS forward_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    source_chat_id TEXT,
    target_chat_id INTEGER,
    success BOOLEAN DEFAULT 1,
    error_message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Last messages table stores the ID of the most recent message in each channel
LAST_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS last_messages (
    channel_id TEXT PRIMARY KEY,
    message_id INTEGER,
    message_type TEXT,
    has_media BOOLEAN DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Cache table for storing temporary data to reduce API calls
CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT,
    expires_at TIMESTAMP
)
"""

# Combine all table definitions into a single list for easy access
DB_SCHEMA = [
    CONFIG_TABLE,
    TARGET_CHATS_TABLE,
    FORWARD_STATS_TABLE,
    LAST_MESSAGES_TABLE,
    CACHE_TABLE
]