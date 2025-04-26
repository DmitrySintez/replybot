"""
Configuration management for the Telegram Forwarder Bot.

This module provides a centralized configuration management system
that loads settings from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from dataclasses import dataclass

# Load environment variables from .env file if it exists
load_dotenv()


@dataclass
class Config:
    """Configuration settings for the application"""
    
    # Bot configuration
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
    SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "").lstrip('@')  # Remove @ if present
    
    # Database configuration
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
    DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "forwarder.db"))
    
    # Performance settings
    DB_POOLSIZE = int(os.getenv("DB_POOLSIZE", "5"))
    CHANNEL_CACHE_TTL = int(os.getenv("CHANNEL_CACHE_TTL", "300"))  # 5 minutes
    MAX_FIND_LAST_LOOKBACK = int(os.getenv("MAX_FIND_LAST_LOOKBACK", "100"))
    
    # Operational settings
    DEFAULT_REPOST_INTERVAL = int(os.getenv("DEFAULT_REPOST_INTERVAL", "3600"))  # 1 hour
    
    # Logging configuration
    DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() in ("true", "1", "yes")
    LOG_FILE = os.getenv("LOG_FILE", os.path.join(BASE_DIR, "bot.log"))
    LOG_ROTATION = os.getenv("LOG_ROTATION", "1 MB")
    LOG_COMPRESSION = os.getenv("LOG_COMPRESSION", "zip")
    
    # Process management
    LOCK_FILE = os.getenv("LOCK_FILE", os.path.join(BASE_DIR, "bot.lock"))

    @classmethod
    def validate(cls) -> bool:
        """Validate that all required configuration values are present"""
        if not cls.BOT_TOKEN:
            print("Missing BOT_TOKEN environment variable")
            return False
            
        if cls.OWNER_ID <= 0:
            print("Invalid OWNER_ID environment variable")
            return False
            
        if not cls.SOURCE_CHANNEL:
            print("Missing SOURCE_CHANNEL environment variable")
            return False
            
        # Ensure data directory exists
        os.makedirs(cls.DATA_DIR, exist_ok=True)
            
        return True