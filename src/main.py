"""
Telegram Channel Forwarder Bot - Main Entry Point

This is the main entry point for the Telegram forwarder bot application.
It handles startup, process management, and graceful shutdown.
"""

import os
import sys
import asyncio
import signal
from loguru import logger

# Add the src directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config
from src.bot import ForwarderBot
from src.utils.system import LockManager


async def startup(lock_manager):
    """Initialize and start the bot"""
    # Configure logging based on environment
    log_level = "DEBUG" if Config.DEBUG_MODE else "INFO"
    logger.remove()  # Remove default handler
    logger.add(
        Config.LOG_FILE,
        level=log_level,
        rotation=Config.LOG_ROTATION,
        compression=Config.LOG_COMPRESSION,
        backtrace=Config.DEBUG_MODE,
        diagnose=Config.DEBUG_MODE
    )
    
    logger.info("Starting Telegram Forwarder Bot")
    
    # Check if the config is valid
    if not Config.validate():
        logger.error("Invalid configuration. Please check your environment variables.")
        return False
    
    try:
        # Acquire process lock to ensure only one instance runs
        if not await lock_manager.acquire_lock():
            logger.error("Another instance is already running")
            return False
        
        # Initialize and start the bot
        bot = ForwarderBot()
        await bot.start()
        
        return True
    except Exception as e:
        logger.exception(f"Failed to start bot: {e}")
        return False


async def shutdown(lock_manager):
    """Clean shutdown of the application"""
    logger.info("Shutting down Telegram Forwarder Bot")
    try:
        # Release the process lock
        await lock_manager.release_lock()
        logger.info("Shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


def handle_signals():
    """Set up signal handlers for graceful shutdown"""
    loop = asyncio.get_event_loop()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, 
            lambda: asyncio.create_task(shutdown(LockManager()))
        )


async def main():
    """Main application entry point"""
    lock_manager = LockManager()
    
    try:
        # Set up signal handlers
        handle_signals()
        
        # Start the bot
        success = await startup(lock_manager)
        if not success:
            await shutdown(lock_manager)
            return 1
        
        # Keep the application running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        await shutdown(lock_manager)
    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
        await shutdown(lock_manager)
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)