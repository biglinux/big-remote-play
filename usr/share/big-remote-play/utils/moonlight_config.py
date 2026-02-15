import configparser
import os
from pathlib import Path

class MoonlightConfigManager:
    _shared_state = {}
    
    def __init__(self):
        self.__dict__ = self._shared_state
        
        if hasattr(self, 'cp'):
            return

        # Possible paths for Moonlight.conf
        paths = [
            Path.home() / '.config' / 'Moonlight Game Streaming Project' / 'Moonlight.conf',
            Path.home() / '.var' / 'app' / 'com.moonlight_stream.Moonlight' / 'config' / 'Moonlight Game Streaming Project' / 'Moonlight.conf'
        ]
        
        self.config_file = None
        for p in paths:
            if p.exists():
                self.config_file = p
                break
        
        # If none exist, default to the standard path
        if not self.config_file:
            self.config_file = paths[0]
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

        self.cp = configparser.ConfigParser()
        self.load()

    def load(self):
        if self.config_file and self.config_file.exists():
            try:
                self.cp.read(self.config_file)
            except Exception as e:
                print(f"Error loading Moonlight config: {e}")
        
        if 'General' not in self.cp:
            self.cp.add_section('General')
        
    def reload(self):
        """Force reload from file"""
        self.cp = configparser.ConfigParser()
        self.load()

    def save(self):
        try:
            with open(self.config_file, 'w') as f:
                self.cp.write(f)
        except Exception as e:
            print(f"Error saving Moonlight config: {e}")

    def get(self, key, default=None):
        return self.cp.get('General', key, fallback=str(default))

    def set(self, key, value):
        self.cp.set('General', key, str(value))
        self.save()
