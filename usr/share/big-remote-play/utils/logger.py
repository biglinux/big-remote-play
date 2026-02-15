"""
Logging system
"""

import logging
import os
from pathlib import Path
from datetime import datetime

class Logger:
    """Log manager"""
    
    def __init__(self, name='big-remoteplay', force_new=False):
        self.name = name
        self.logger = logging.getLogger(name)
        
        if force_new:
            for h in self.logger.handlers[:]:
                self.logger.removeHandler(h)
                h.close()
        

        
        # Log directory
        self.log_dir = Path.home() / '.config' / 'big-remoteplay' / 'logs'
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Log file
        log_file = self.log_dir / f'{name}_{datetime.now().strftime("%Y%m%d")}.log'
        
        # Configure logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG if force_new else logging.INFO)
        
        # File handler
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        
        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG if force_new else logging.INFO)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        if not self.logger.handlers:
            self.logger.addHandler(fh)
            self.logger.addHandler(ch)
        
    def info(self, message):
        """Log info"""
        self.logger.info(message)
        
    def warning(self, message):
        """Log warning"""
        self.logger.warning(message)
        
    def error(self, message):
        """Log error"""
        self.logger.error(message)
        
    def debug(self, message):
        """Log debug"""
        self.logger.debug(message)
        
    def set_verbose(self, enabled):
        """Enables/disables verbose mode"""
        if enabled:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)
    def clear_old_logs(self):
        """Removes old log files"""
        # Remove ALL log files
        try:
             for f in self.log_dir.glob('*.log'):
                 f.unlink()
        except: pass
