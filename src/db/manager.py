"""
Database management for the Telegram Forwarder Bot.

This module provides a high-performance database interface with connection pooling
and caching to minimize database access and improve responsiveness.
"""

import os
import asyncio
import aiosqlite
from datetime import datetime, timedelta
from functools import lru_cache
from loguru import logger

from src.config import Config
from src.db.models import DB_SCHEMA


class DatabaseManager:
    """Manages database operations with connection pooling and caching."""
    
    def __init__(self, db_path=None):
        """Initialize the database manager."""
        self.db_path = db_path or Config.DB_PATH
        self.connection_pool = []
        self.pool_lock = asyncio.Lock()
        self.cache = {}
        self.cache_ttl = {}
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
    
    async def init(self):
        """Initialize the database schema and connection pool."""
        # Create database if it doesn't exist and set up schema
        async with await self._get_connection() as db:
            for table_sql in DB_SCHEMA:
                await db.execute(table_sql)
            await db.commit()
        
        # Pre-populate the connection pool
        for _ in range(Config.DB_POOLSIZE):
            conn = await self._create_connection()
            self.connection_pool.append(conn)
            
        logger.info(f"Initialized database at {self.db_path} with connection pool size {len(self.connection_pool)}")
    
    async def _create_connection(self):
        """Create a new database connection."""
        return await aiosqlite.connect(self.db_path, isolation_level=None)
    
    async def _get_connection(self):
        """Get a connection from the pool or create a new one if needed."""
        async with self.pool_lock:
            if self.connection_pool:
                return self.connection_pool.pop()
            return await self._create_connection()
    
    async def _release_connection(self, conn):
        """Return a connection to the pool."""
        async with self.pool_lock:
            if len(self.connection_pool) < Config.DB_POOLSIZE:
                self.connection_pool.append(conn)
            else:
                await conn.close()
    
    async def _execute(self, query, params=None, fetch_one=False, fetch_all=False):
        """Execute a database query with connection pooling."""
        conn = await self._get_connection()
        try:
            cursor = await conn.execute(query, params or ())
            
            if fetch_one:
                result = await cursor.fetchone()
                await cursor.close()
                return result
            elif fetch_all:
                result = await cursor.fetchall()
                await cursor.close()
                return result
            
            await conn.commit()
            await cursor.close()
            return True
        except Exception as e:
            logger.error(f"Database error: {e} in query: {query} with params {params}")
            raise
        finally:
            await self._release_connection(conn)
    
    def _cache_key(self, method_name, *args, **kwargs):
        """Generate a cache key from method name and arguments."""
        key_parts = [method_name]
        key_parts.extend(str(arg) for arg in args)
        key_parts.extend(f"{k}:{v}" for k, v in sorted(kwargs.items()))
        return ":".join(key_parts)
    
    def _get_cached(self, key):
        """Get a value from cache if it exists and is not expired."""
        if key in self.cache and datetime.now() < self.cache_ttl.get(key, datetime.min):
            return self.cache[key]
        return None
    
    def _set_cache(self, key, value, ttl_seconds=None):
        """Store a value in cache with optional expiration time."""
        if ttl_seconds is None:
            ttl_seconds = Config.CHANNEL_CACHE_TTL
            
        self.cache[key] = value
        self.cache_ttl[key] = datetime.now() + timedelta(seconds=ttl_seconds)
    
    def _invalidate_cache(self, prefix=None):
        """Invalidate all cache entries or those matching a prefix."""
        if prefix:
            keys_to_remove = [k for k in self.cache if k.startswith(prefix)]
            for k in keys_to_remove:
                self.cache.pop(k, None)
                self.cache_ttl.pop(k, None)
        else:
            self.cache.clear()
            self.cache_ttl.clear()
    
    async def get_target_chats(self):
        """Get list of all target chat IDs with caching."""
        cache_key = self._cache_key("get_target_chats")
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
            
        query = "SELECT chat_id FROM target_chats"
        rows = await self._execute(query, fetch_all=True)
        result = [row[0] for row in (rows or [])]
        
        self._set_cache(cache_key, result)
        return result
    
    async def add_target_chat(self, chat_id: int):
        """Add a target chat ID to the database."""
        self._invalidate_cache("get_target_chats")
        
        query = "INSERT OR IGNORE INTO target_chats (chat_id) VALUES (?)"
        return await self._execute(query, (chat_id,))
    
    async def remove_target_chat(self, chat_id: int):
        """Remove a target chat ID from the database."""
        self._invalidate_cache("get_target_chats")
        
        query = "DELETE FROM target_chats WHERE chat_id = ?"
        return await self._execute(query, (chat_id,))
    
    async def get_config(self, key: str, default=None):
        """Get a configuration value by key with caching."""
        cache_key = self._cache_key("get_config", key)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
            
        query = "SELECT value FROM config WHERE key = ?"
        row = await self._execute(query, (key,), fetch_one=True)
        result = row[0] if row else default
        
        self._set_cache(cache_key, result)
        return result
    
    async def set_config(self, key: str, value: str):
        """Set a configuration value."""
        self._invalidate_cache("get_config")
        
        query = "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)"
        return await self._execute(query, (key, str(value)))
    
    async def log_forward(self, message_id: int):
        """Log a forwarded message."""
        query = "INSERT INTO forward_stats (message_id) VALUES (?)"
        return await self._execute(query, (message_id,))
    
    async def save_last_message(self, channel_id: str, message_id: int):
        """Save the ID of the last message in a channel."""
        self._invalidate_cache("get_last_message")
        
        query = """
            INSERT OR REPLACE INTO last_messages 
            (channel_id, message_id, timestamp) 
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """
        result = await self._execute(query, (channel_id, message_id))
        
        if result:
            logger.info(f"Saved last message ID {message_id} for channel {channel_id}")
        
        return result
    
    async def get_last_message(self, channel_id: str):
        """Get the ID of the last message in a channel with caching."""
        cache_key = self._cache_key("get_last_message", channel_id)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
            
        query = "SELECT message_id FROM last_messages WHERE channel_id = ?"
        row = await self._execute(query, (channel_id,), fetch_one=True)
        result = row[0] if row else None
        
        self._set_cache(cache_key, result)
        return result
    
    @lru_cache(maxsize=1)
    async def get_stats(self):
        """Get forwarding statistics."""
        # Get total forwards
        total_query = "SELECT COUNT(*) FROM forward_stats"
        total_row = await self._execute(total_query, fetch_one=True)
        
        # Get last forward timestamp
        last_query = "SELECT timestamp FROM forward_stats ORDER BY timestamp DESC LIMIT 1"
        last_row = await self._execute(last_query, fetch_one=True)
        
        # Get last messages for all channels
        last_msg_query = "SELECT channel_id, message_id, timestamp FROM last_messages"
        last_msg_rows = await self._execute(last_msg_query, fetch_all=True)
        
        # Process the last messages data
        last_msgs = {}
        for row in (last_msg_rows or []):
            last_msgs[row[0]] = {
                "message_id": row[1],
                "timestamp": row[2]
            }
        
        return {
            "total_forwards": total_row[0] if total_row else 0,
            "last_forward": last_row[0] if last_row else None,
            "last_messages": last_msgs
        }
    
    async def close(self):
        """Close all database connections."""
        async with self.pool_lock:
            for conn in self.connection_pool:
                await conn.close()
            self.connection_pool.clear()