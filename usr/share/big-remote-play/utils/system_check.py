"""
System component verification
"""

import subprocess
import shutil
from typing import Tuple
from utils.i18n import _

class SystemCheck:
    """System component checker"""
    
    def __init__(self):
        pass
        
    def has_sunshine(self) -> bool:
        """Checks if Sunshine is installed"""
        return shutil.which('sunshine') is not None
        
    def has_moonlight(self) -> bool:
        """Checks if Moonlight is installed"""
        # Moonlight may have different names
        return (
            shutil.which('moonlight') is not None or
            shutil.which('moonlight-qt') is not None
        )
        
    def has_avahi(self) -> bool:
        """Checks if Avahi is installed"""
        return shutil.which('avahi-browse') is not None
        
    def has_docker(self) -> bool:
        """Checks if Docker is installed"""
        return shutil.which('docker') is not None

    def has_tailscale(self) -> bool:
        """Checks if Tailscale is installed"""
        return shutil.which('tailscale') is not None
        
    def check_all(self) -> dict:
        """Checks all components"""
        return {
            'sunshine': self.has_sunshine(),
            'moonlight': self.has_moonlight(),
            'avahi': self.has_avahi(),
            'docker': self.has_docker(),
            'tailscale': self.has_tailscale(),
        }
    
    def is_sunshine_running(self) -> bool:
        """Checks if Sunshine process is running"""
        try:
            result = subprocess.run(
                ['pgrep', '-x', 'sunshine'],
                capture_output=True,
                timeout=2
            )
            return result.returncode == 0
        except:
            return False

    def is_docker_running(self) -> bool:
        """Checks if Docker daemon is running"""
        try:
            return subprocess.run(['systemctl', 'is-active', '--quiet', 'docker']).returncode == 0
        except:
            return False

    def is_tailscale_running(self) -> bool:
        """Checks if Tailscale daemon is running"""
        try:
            return subprocess.run(['systemctl', 'is-active', '--quiet', 'tailscaled']).returncode == 0
        except:
            return False
    
    def is_moonlight_running(self) -> bool:
        """Checks if Moonlight process is running (ignores zombies)"""
        try:
            for process_name in ['moonlight', 'moonlight-qt']:
                # Get PIDs
                result = subprocess.run(
                    ['pgrep', '-x', process_name],
                    capture_output=True,
                    text=True, # Important to read output as text
                    timeout=2
                )
                
                if result.returncode == 0 and result.stdout:
                    pids = result.stdout.strip().split()
                    for pid in pids:
                        # Check process state
                        try:
                            state_check = subprocess.run(
                                ['ps', '-o', 'state=', '-p', pid],
                                capture_output=True,
                                text=True,
                                timeout=1
                            )
                            if state_check.returncode == 0:
                                state = state_check.stdout.strip()
                                # If state is not Z (Zombie) or T (Stopped), consider running
                                if state and state not in ['Z', 'T', 'Z+']:
                                    return True
                        except:
                            continue
                            
            return False
        except:
            return False
            
    def get_sunshine_version(self) -> str:
        """Gets Sunshine version"""
        try:
            result = subprocess.run(
                ['sunshine', '--version'],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                # Check for standard library error
                if "libicuuc.so.76" in result.stderr:
                    return _("Error: Missing ICU 76")
                return _("Unknown")
                
        except:
            return _("Unknown")
            
    def get_moonlight_version(self) -> str:
        """Gets Moonlight version"""
        try:
            # Try different variants
            for cmd in ['moonlight-qt', 'moonlight']:
                result = subprocess.run(
                    [cmd, '--version'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                
                if result.returncode == 0:
                    return result.stdout.strip()
                    
            return _("Unknown")
            
        except:
            return _("Unknown")
            
    def check_firewall(self) -> Tuple[bool, str]:
        """
        Checks firewall status
        Returns (has_firewall, type)
        """
        if shutil.which('ufw'):
            try:
                result = subprocess.run(
                    ['ufw', 'status'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                
                if result.returncode == 0:
                    active = 'Status: active' in result.stdout
                    return (active, 'ufw')
                    
            except:
                pass
                
        if shutil.which('iptables'):
            return (True, 'iptables')
            
        return (False, 'none')
        
    def check_network_connectivity(self) -> bool:
        """Checks network connectivity"""
        try:
            # Ping Google DNS
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', '8.8.8.8'],
                capture_output=True,
                timeout=3
            )
            
            return result.returncode == 0
            
        except:
            return False

    def check_icu(self) -> bool:
        """Checks if required libicuuc is present"""
        import os
        # Sunshine often expects .76 or .7*
        paths = [
            '/usr/lib/libicuuc.so.76',
            '/usr/lib/libicuuc.so.77',
            '/usr/lib/libicuuc.so.78'
        ]
        for p in paths:
            if os.path.exists(p):
                return True
        return False
