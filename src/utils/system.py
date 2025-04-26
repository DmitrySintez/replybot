"""
System utilities for the Telegram Forwarder Bot.

This module provides system-level functions for process management,
file handling, and lock management.
"""

import os
import fcntl
import psutil
from loguru import logger
from contextlib import contextmanager
from typing import Optional

from src.config import Config


class LockManager:
    """
    Manages process locks to ensure only one instance of the bot runs at a time.
    Implements proper cross-platform locking mechanisms.
    """
    
    def __init__(self, lock_file: Optional[str] = None):
        """Initialize with optional custom lock file path."""
        self.lock_file = lock_file or Config.LOCK_FILE
        self.lock_fd = None
        self.pid = os.getpid()
    
    async def acquire_lock(self) -> bool:
        """
        Acquire an exclusive lock on the lock file.
        
        Returns:
            bool: True if lock acquired, False if another process has the lock.
        """
        try:
            # First check if lock file exists and has valid PID
            if os.path.exists(self.lock_file):
                # Check if the process is still running
                with open(self.lock_file, 'r') as f:
                    try:
                        old_pid = int(f.read().strip())
                        if self._is_process_running(old_pid):
                            logger.warning(f"Another instance is already running (PID: {old_pid})")
                            return False
                        else:
                            logger.info(f"Found stale lock file for PID {old_pid}, cleaning up")
                            # Process not running, remove stale lock file
                            os.remove(self.lock_file)
                    except (ValueError, FileNotFoundError):
                        # Invalid PID in file, remove it
                        os.remove(self.lock_file)
            
            # Create lock file with current PID
            self.lock_fd = open(self.lock_file, 'w')
            
            # Try to acquire an exclusive lock
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # Write PID to the file
            self.lock_fd.write(str(self.pid))
            self.lock_fd.flush()
            
            logger.debug(f"Acquired lock for PID {self.pid}")
            return True
            
        except (IOError, OSError) as e:
            logger.error(f"Failed to acquire lock: {e}")
            if self.lock_fd:
                self.lock_fd.close()
            return False
    
    async def release_lock(self) -> bool:
        """
        Release the lock and clean up.
        
        Returns:
            bool: True if successfully released, False on error.
        """
        try:
            if self.lock_fd:
                # Release the lock
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
                self.lock_fd = None
            
            # Remove the lock file
            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)
                
            logger.debug(f"Released lock for PID {self.pid}")
            return True
            
        except (IOError, OSError) as e:
            logger.error(f"Failed to release lock: {e}")
            return False
    
    def _is_process_running(self, pid: int) -> bool:
        """
        Check if a process with the given PID is running.
        
        Args:
            pid: Process ID to check
            
        Returns:
            bool: True if the process is running, False otherwise
        """
        try:
            # Use psutil for cross-platform compatibility
            process = psutil.Process(pid)
            return process.is_running()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False


@contextmanager
def file_lock(file_path):
    """
    Context manager for file locking.
    
    Example:
        with file_lock('/path/to/file.lock'):
            # Do something that requires exclusive access
    """
    lock_file = open(file_path, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        if os.path.exists(file_path):
            os.remove(file_path)


def get_memory_usage():
    """
    Get the current memory usage of the application.
    
    Returns:
        dict: Memory usage information
    """
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    
    return {
        'rss': memory_info.rss / (1024 * 1024),  # RSS in MB
        'vms': memory_info.vms / (1024 * 1024),  # VMS in MB
        'percent': process.memory_percent(),
    }