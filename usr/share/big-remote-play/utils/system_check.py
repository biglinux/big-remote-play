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

    def has_zerotier(self) -> bool:
        """Checks if ZeroTier is installed"""
        return shutil.which('zerotier-cli') is not None

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
            'zerotier': self.has_zerotier(),
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

    def are_containers_running(self) -> bool:
        """Checks if app containers (caddy, headscale) are running"""
        try:
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Names}}'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                return 'caddy' in output and 'headscale' in output
            return False
        except:
            return False

    def is_tailscale_running(self) -> bool:
        """Checks if Tailscale daemon is running"""
        try:
            return subprocess.run(['systemctl', 'is-active', '--quiet', 'tailscaled']).returncode == 0
        except:
            return False

    def is_zerotier_running(self) -> bool:
        """Checks if ZeroTier daemon is running"""
        try:
            return subprocess.run(['systemctl', 'is-active', '--quiet', 'zerotier-one']).returncode == 0
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

    def check_icu_libs(self) -> list:
        """
        Checks for missing ICU libraries required by Sunshine (specifically .76)
        Returns a list of missing libraries.
        """
        import os
        
        missing = []
        required_libs = ['libicuuc.so.76', 'libicudata.so.76', 'libicui18n.so.76']
        lib_paths = ['/usr/share/big-remote-play/libs', '/usr/lib', '/usr/lib64']
        
        for lib in required_libs:
            found = False
            for path in lib_paths:
                full_path = os.path.join(path, lib)
                if os.path.exists(full_path):
                    # Check if it's a symlink to .78 (which is broken)
                    try:
                        real_path = os.path.realpath(full_path)
                        if "78" in os.path.basename(real_path) and "76" not in os.path.basename(real_path):
                             # It's a fake symlink
                             continue
                    except: pass
                    
                    found = True
                    break
            
            if not found:
                missing.append(lib)
                
        return missing
