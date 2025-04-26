import os
from typing import Optional
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
        self.source_channel: str = os.getenv("SOURCE_CHANNEL", "").lstrip('@')
        self.db_path: str = os.getenv("DB_PATH", "forwarder.db")
        
        # Validate required settings
        if not all([self.bot_token, self.owner_id, self.source_channel]):
            raise ValueError("Missing required environment variables")
        
        # Cache settings
        self.cache_ttl: int = 300  # 5 minutes cache for chat info
        self.max_cache_size: int = 100
        
        # Database connection settings
        self.max_db_connections: int = 5
        
        self._initialized = True
    
    @property
    def source_channel_id(self) -> Optional[str]:
        """Get source channel ID, stripping @ if present"""
        return self.source_channel.lstrip('@') if self.source_channel else None
