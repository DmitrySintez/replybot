import os
import json
from typing import List, Optional
from dotenv import load_dotenv

class Config:
    """Singleton configuration class"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        load_dotenv()
        
        # Load environment variables
        self.bot_token: str = os.getenv("BOT_TOKEN", "")
        self.owner_id: int = int(os.getenv("OWNER_ID", "0"))
        self.source_channels: List[str] = []
        
        # Support for backwards compatibility - add initial source channel if provided
        initial_source = os.getenv("SOURCE_CHANNEL", "").lstrip('@')
        if initial_source:
            self.source_channels.append(initial_source)
            
        self.db_path: str = os.getenv("DB_PATH", "forwarder.db")
        
        # Try to load additional source channels from config file
        self._load_channels_from_config()
        
        # Validate required settings
        if not all([self.bot_token, self.owner_id]):
            raise ValueError("Missing required environment variables")
        
        # Cache settings
        self.cache_ttl: int = 300  # 5 minutes cache for chat info
        self.max_cache_size: int = 100
        
        # Database connection settings
        self.max_db_connections: int = 5
        
        self._initialized = True
    
    def _load_channels_from_config(self):
        """Load channels from configuration file"""
        try:
            with open('bot_config.json', 'r') as f:
                config = json.load(f)
                if 'source_channels' in config and isinstance(config['source_channels'], list):
                    # Add channels not already in the list
                    for channel in config['source_channels']:
                        channel = str(channel).lstrip('@')
                        if channel and channel not in self.source_channels:
                            self.source_channels.append(channel)
        except (FileNotFoundError, json.JSONDecodeError):
            # Create default config if not exists
            self._save_channels_to_config()
    
    def _save_channels_to_config(self):
        """Save channels to configuration file"""
        try:
            config = {}
            # Try to load existing config first
            try:
                with open('bot_config.json', 'r') as f:
                    config = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                config = {"source_channels": [], "target_chats": [], "last_message_ids": {}}
            
            # Update source channels
            config['source_channels'] = self.source_channels
            
            # Save updated config
            with open('bot_config.json', 'w') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            from loguru import logger
            logger.error(f"Failed to save channels to config: {e}")
    
    def add_source_channel(self, channel: str) -> bool:
        """Add a new source channel and save to config"""
        channel = channel.lstrip('@')
        if channel and channel not in self.source_channels:
            self.source_channels.append(channel)
            self._save_channels_to_config()
            return True
        return False
    
    def remove_source_channel(self, channel: str) -> bool:
        """Remove a source channel and update config"""
        channel = channel.lstrip('@')
        if channel in self.source_channels:
            self.source_channels.remove(channel)
            self._save_channels_to_config()
            return True
        return False