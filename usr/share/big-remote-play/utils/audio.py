import subprocess
from typing import List, Dict, Optional
from utils.i18n import _

class AudioManager:
    """
    Simplified and robust Audio Manager for Big Remote Play.
    Focuses on important configurations:
    1. Host Only (Default)
    2. Host + Guest (Streaming Active)
    """

    def is_virtual(self, name: str, description: str = "") -> bool:
        """Checks if a sink is virtual"""
        n = name.lower()
        d = description.lower()
        # Filtra nomes de sinks conhecidamente virtuais ou nossos
        virtual_patterns = ['sunshine', 'null-sink', 'module-combine-sink', 'combined', 'easyeffects']
        return any(x in n or x in d for x in virtual_patterns) or n.endswith('.monitor')

    def get_passive_sinks(self) -> List[Dict[str, str]]:
        """
        Lists physical output devices (Hardware).
        Aggressively filters virtual sinks to avoid loops.
        """
        sinks = []
        try:
            # Get sinks with pactl
            res = subprocess.run(['pactl', 'list', 'sinks'], capture_output=True, text=True)
            if res.returncode != 0: return []
            
            current = {}
            for line in res.stdout.splitlines():
                line = line.strip()
                if line.startswith('Sink #'):
                    if current: sinks.append(current)
                    current = {'id': line.split('#')[1]}
                elif line.startswith('Name:'):
                    current['name'] = line.split(':', 1)[1].strip()
                elif line.startswith('Description:'):
                    current['description'] = line.split(':', 1)[1].strip()
            if current: sinks.append(current)
            
            # Filtrar
            valid_sinks = []
            for s in sinks:
                if not self.is_virtual(s.get('name', ''), s.get('description', '')):
                    valid_sinks.append(s)
            
            return valid_sinks
        except Exception as e:
            print(f"Error listing sinks: {e}")
            return []

    def get_default_sink(self) -> Optional[str]:
        try:
            res = subprocess.run(['pactl', 'get-default-sink'], capture_output=True, text=True)
            return res.stdout.strip() if res.returncode == 0 else None
        except: return None

    def set_default_sink(self, sink_name: str):
        try:
            subprocess.run(['pactl', 'set-default-sink', sink_name], check=False)
        except: pass

    def enable_streaming_audio(self, host_sink: str) -> bool:
        """
        Activates Streaming mode (Host + Guest) using RADICAL strategy: Combine Sink.
        Instead of Null Sink + Loopback (which fails and mutes), we use a Combine Sink.
        SunshineGameSink -> [Hardware Sink]
        Sunshine captures from SunshineGameSink.monitor.
        Host hears because Hardware Sink is a slave.
        """
        # If host_sink is virtual or null, try to find first real hardware
        if not host_sink or self.is_virtual(host_sink):
            hardware_devices = self.get_passive_sinks()
            if hardware_devices:
                host_sink = hardware_devices[0]['name']
                print(f"Host sink was virtual or null, fallback to hardware: {host_sink}")
            else:
                print("ERROR: No hardware audio device found.")
                return False

        # Clear before creating to avoid duplicates
        self.disable_streaming_audio(None) 
        
        try:
            print(f"Enabling Isolated Audio (Radical) -> Combine Sink 'SunshineGameSink' -> Slave: {host_sink}")
            
            # 1. Combine Sink 'SunshineGameSink'
            # This creates a virtual output that relays audio to host_sink (Hardware)
            # And provides a .monitor for Sunshine to record.
            subprocess.run([
                'pactl', 'load-module', 'module-combine-sink',
                'sink_name=SunshineGameSink',
                f'slaves={host_sink}',
                'sink_properties=device.description=SunshineGameSink'
            ], check=True)
            
            # 2. Small delay and ensure volumes
            import time; time.sleep(0.5)
            subprocess.run(['pactl', 'set-sink-mute', 'SunshineGameSink', '0'], check=False)
            subprocess.run(['pactl', 'set-sink-volume', 'SunshineGameSink', '100%'], check=False)

            # 3. Set SunshineGameSink as default
            self.set_default_sink("SunshineGameSink")

            # 4. Verify creation
            time.sleep(0.2)
            sinks = subprocess.run(['pactl', 'list', 'short', 'sinks'], capture_output=True, text=True).stdout
            if 'SunshineGameSink' not in sinks:
                print("CRITICAL ERROR: SunshineGameSink (Combine) was not created!")
                self.disable_streaming_audio(host_sink)
                return False
                
            print(f"Radical Audio Activated: SunshineGameSink combining to {host_sink}")
            return True
            
        except Exception as e:
            print(f"Falha ao ativar streaming de áudio (Combine): {e}")
            self.disable_streaming_audio(host_sink) 
            return False

    def get_sink_monitor_source(self, sink_name: str) -> Optional[str]:
        """
        Returns monitor name for a sink.
        Avoids issues where monitor name is not exactly .monitor
        """
        try:
            # pactl list sources short returns: ID Name ...
            res = subprocess.run(['pactl', 'list', 'short', 'sources'], capture_output=True, text=True)
            candidate = f"{sink_name}.monitor"
            
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) > 1:
                    source_name = parts[1]
                    # Exact match or default monitor
                    if source_name == candidate:
                        return source_name
            
            # If not found exact, try finding one containing sink name and 'monitor'
            # risky but better than failing
            for line in res.stdout.splitlines():
                 parts = line.split()
                 if len(parts) > 1:
                     nm = parts[1]
                     if sink_name in nm and 'monitor' in nm:
                         return nm
                         
            return candidate # Fallback
        except:
             return f"{sink_name}.monitor"

    def disable_streaming_audio(self, host_sink: str):
        """
        Disables Streaming mode.
        Restores default sink and removes virtual modules.
        """
        # 1. Restore default (if not virtual)
        if host_sink and not self.is_virtual(host_sink):
            self.set_default_sink(host_sink)
            
            # Restore apps that might be stuck on the virtual sink
            try:
                apps = self.get_apps()
                for app in apps:
                    # Move all apps from virtual sinks back to host_sink
                    if self.is_virtual(app.get('sink_name', '')):
                         print(f"Restoring {app.get('name')} to {host_sink}")
                         self.move_app(app['id'], host_sink)
            except Exception as e:
                print(f"Error restoring apps to hardware: {e}")
            
        # 2. Unload specific modules
        try:
            res = subprocess.run(['pactl', 'list', 'short', 'modules'], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    # Search criteria to unload:
                    # - null-sink module with our name (GameSink)
                    # - Our loopbacks
                    if 'sink_name=SunshineGameSink' in line or \
                       'sink_name=SunshineStereo' in line or \
                       'sink_name=SunshineHybrid' in line or \
                       'source=SunshineGameSink.monitor' in line or \
                       'SunshineLoopback' in line:
                        
                        mod_id = line.split()[0]
                        print(f"Cleaning audio module: {mod_id}")
                        subprocess.run(['pactl', 'unload-module', mod_id], check=False)
        except Exception as e:
            print(f"Error cleaning modules: {e}")

    def get_apps(self) -> List[Dict]:
        """
        Lists applications playing audio (Sink Inputs).
        """
        apps = []
        try:
            # ID mapping -> Sink Name for reference
            sinks_map = {}
            res_s = subprocess.run(['pactl', 'list', 'short', 'sinks'], capture_output=True, text=True)
            for l in res_s.stdout.splitlines():
                p = l.split()
                if len(p) > 1: sinks_map[p[0]] = p[1]

            res = subprocess.run(['pactl', 'list', 'sink-inputs'], capture_output=True, text=True)
            current = {}
            
            for line in res.stdout.splitlines():
                line = line.strip()
                if line.startswith('Sink Input #'):
                    if current: apps.append(current)
                    current = {'id': line.split('#')[1], 'name': _('Unknown'), 'icon': 'audio-x-generic-symbolic'}
                elif line.startswith('Sink:'):
                    sid = line.split(':')[1].strip()
                    current['sink_id'] = sid
                    current['sink_name'] = sinks_map.get(sid, sid)
                elif 'application.name = ' in line:
                    val = line.split('=', 1)[1].strip().strip('"')
                    if val: current['name'] = val
                elif 'application.icon_name = ' in line:
                    val = line.split('=', 1)[1].strip().strip('"')
                    if val: current['icon'] = val
                elif 'media.name = ' in line and current.get('name') == _('Unknown'):
                    val = line.split('=', 1)[1].strip().strip('"')
                    if val: current['name'] = val
                    
            if current: apps.append(current)
            
            # Filter internal streams if necessary
            # Ignore internal PulseAudio/Pipewire streams that cause loops if moved
            def is_internal(name):
                n = name.lower()
                return any(x in n for x in ['sunshine', 'monitor', 'loopback', 'simultaneous', 'combine', 'output to'])
            
            return [a for a in apps if not is_internal(a.get('name', ''))]
            
        except Exception: 
            return []

    def move_app(self, app_id: str, sink_name: str):
        try:
            subprocess.run(['pactl', 'move-sink-input', str(app_id), sink_name], check=False)
        except: pass

    def cleanup(self):
        """Cleans everything and tries to restore original sound"""
        # Tries to find real hardware to restore
        hardware = self.get_passive_sinks()
        target = hardware[0]['name'] if hardware else None
        self.disable_streaming_audio(target)
