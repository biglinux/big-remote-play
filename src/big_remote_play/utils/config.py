"""
Application configuration management
"""

import json
import os
import tempfile
from pathlib import Path
import logging

_log = logging.getLogger("big-remoteplay")


class Config:
    """Configuration manager"""

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "big-remoteplay"
        self.config_file = self.config_dir / "config.json"

        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.config = self.load()

    def load(self):
        """Loads configuration"""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                _log.error(f"Error loading configuration: {e}")
                return self.default_config()
        else:
            return self.default_config()

    def save(self):
        """Saves configuration atomically (write temp + replace) to avoid corruption on crash"""
        try:
            fd, tmp_path = tempfile.mkstemp(dir=self.config_dir, prefix=".config-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(self.config, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.config_file)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            _log.error(f"Error saving configuration: {e}")

    def get(self, key, default=None):
        """Gets configuration value"""
        return self.config.get(key, default)

    def set(self, key, value):
        """Sets configuration value"""
        self.config[key] = value
        self.save()

    def default_config(self):
        """Returns default configuration"""
        return {
            "theme": "auto",
            "network": {
                "upnp": True,
                "ipv6": True,
                "discovery": True,
                "sunshine_port": 47989,
                "streaming_port": 48010,
            },
            "host": {
                "max_players": 2,
                "quality": "high",
                "audio": True,
                "input_sharing": True,
            },
            "guest": {
                "quality": "auto",
                "audio": True,
                "hw_decode": True,
                "fullscreen": False,
            },
            "advanced": {
                "verbose_logging": False,
                "auto_start_sunshine": False,
            },
        }
