import subprocess, shutil
class MoonlightClient:
    def __init__(self, logger=None):
        self.process = None; self.connected_host = None; self.logger = logger
        self.moonlight_cmd = next((c for c in ['moonlight-qt', 'moonlight'] if shutil.which(c)), None)
    
    def _prepare_ip(self, ip):
        """Prepares IP for Moonlight CLI."""
        if not ip: return ""
        clean_ip = ip.strip()
        
        # Remove brackets if present to facilitate processing
        was_bracketed = clean_ip.startswith('[') and clean_ip.endswith(']')
        if was_bracketed:
            clean_ip = clean_ip[1:-1]
        
        if ':' in clean_ip and '%' not in clean_ip and clean_ip.startswith('fe80'):
            try:
                # Get interface with default route
                route = subprocess.check_output(['ip', '-6', 'route', 'show', 'default'], text=True).split()
                if 'dev' in route:
                    dev_idx = route.index('dev') + 1
                    if dev_idx < len(route):
                        iface = route[dev_idx]
                        clean_ip = f"{clean_ip}%{iface}"
                else:
                    # Dumb fallback: get first UP interface that is not lo
                    try:
                        import json
                        out = subprocess.check_output(['ip', '-j', 'addr'], text=True)
                        for i in json.loads(out):
                            if i['ifname'] != 'lo' and 'UP' in i['flags']:
                                clean_ip = f"{clean_ip}%{i['ifname']}"
                                break
                    except: pass
            except: pass
            
        return clean_ip

    def connect(self, ip, **kw):
        if not self.moonlight_cmd or self.is_connected(): return False
        
        try:
            target_ip = self._prepare_ip(ip)
            
            cmd = [self.moonlight_cmd, 'stream', target_ip, 'Desktop']
            if kw.get('width') and kw.get('height') and kw.get('width') != 'custom': cmd.extend(['--resolution', f"{kw['width']}x{kw['height']}"])
            if kw.get('fps') and kw.get('fps') != 'custom': cmd.extend(['--fps', str(kw['fps'])])
            if kw.get('bitrate'): cmd.extend(['--bitrate', str(kw['bitrate'])])
            cmd.extend(['--display-mode', kw.get('display_mode', 'borderless')])
            if not kw.get('audio', True): cmd.append('--audio-on-host')
            cmd.append('--quit-after') # Close when app ends
            if kw.get('hw_decode', True): cmd.extend(['--video-decoder', 'hardware'])
            else: cmd.extend(['--video-decoder', 'software'])
            
            if self.logger:
                self.logger.info(f"Connecting to {ip} (target: {target_ip}) with options: {kw}")
                self.logger.info(f"Command: {' '.join(cmd)}")
            
            stdout_target = subprocess.PIPE if self.logger else None
            stderr_target = subprocess.PIPE if self.logger else None
            
            self.process = subprocess.Popen(cmd, stdout=stdout_target, stderr=stderr_target, text=True)
            self.connected_host = ip
            
            if self.logger:
                import threading
                def log_output(pipe, level):
                    for line in iter(pipe.readline, ''):
                        if line: getattr(self.logger, level, self.logger.info)(f"[Moonlight] {line.strip()}")
                    pipe.close()
                if self.process.stdout: threading.Thread(target=log_output, args=(self.process.stdout, 'info'), daemon=True).start()
                if self.process.stderr: threading.Thread(target=log_output, args=(self.process.stderr, 'error'), daemon=True).start()
            
            try:
                exit_code = self.process.wait(timeout=1.0)
                msg = f"Moonlight ended prematurely (Code {exit_code})"
                if self.logger: self.logger.error(msg)
                return False
            except subprocess.TimeoutExpired: pass
            
            return True
        except Exception as e: 
            if self.logger: self.logger.error(f"Error connecting: {e}")
            return False

    def is_connected(self): return self.process and self.process.poll() is None

    def disconnect(self):
        if not self.is_connected(): return False
        try:
            if self.process: self.process.terminate(); self.process.wait(timeout=5)
            self.process = None; self.connected_host = None; return True
        except:
            if self.process: self.process.kill(); self.process = None; self.connected_host = None
            return False

    def probe_host(self, host_ip):
        try: 
            target_ip = self._prepare_ip(host_ip)
            # Aggressive timeout for probe
            res = subprocess.run([self.moonlight_cmd, 'list', target_ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.5)
            return res.returncode == 0
        except Exception as e: 
            if self.logger: self.logger.error(f"Probe error: {e}")
            return False

    def pair(self, host_ip, on_pin_callback=None):
        try:
            target_ip = self._prepare_ip(host_ip)
            cmd = [self.moonlight_cmd, 'pair', target_ip]
            if self.logger: self.logger.info(f"Starting pair with {host_ip} (target: {target_ip}): {' '.join(cmd)}")
            
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            success = False
            
            while True:
                line = self.process.stdout.readline()
                # If no line and process ended, break
                if not line and self.process.poll() is not None: break
                if not line: continue
                
                if self.logger: self.logger.debug(f"[Pair] {line.strip()}")
                
                if "PIN" in line and "target PC" in line:
                    pin = ''.join(filter(str.isdigit, line.strip().split()[-1]))
                    if self.logger: self.logger.info(f"PIN detected: {pin}")
                    if pin and on_pin_callback: on_pin_callback(pin)
                
                if "successfully paired" in line.lower() or "already paired" in line.lower(): 
                    if self.logger: self.logger.info("Pairing successful")
                    success = True
                    # Do not terminate immediately, let it finish and sync
                    continue
            
            # Record return code and cleanup
            ret = self.process.wait()
            self.process = None
            
            # Return success flag or 0 exit code (if it finished naturally with success)
            return success or ret == 0
        except Exception as e:
            if self.logger: self.logger.error(f"Pairing exception: {e}")
            return False

    def list_apps(self, host_ip):
        if not self.moonlight_cmd: return []
        
        try:
            target_ip = self._prepare_ip(host_ip)
            # Try clearing neighbor cache if IPv6 to avoid dead routes
            if ':' in target_ip:
                # 2. Flush neighbor cache for the specific target interface to avoid stale routes
                try: subprocess.run(['ip', '-6', 'neigh', 'flush', 'all'], capture_output=True, timeout=1)
                except: pass
                
                # 3. Ping all-nodes multicast briefly to populate neighbor cache
                try: subprocess.run(['ping', '-6', '-c', '1', '-W', '1', 'ff02::1%lo'], capture_output=True, timeout=1) 
                except: pass
        except: pass

        try:
            target_ip = self._prepare_ip(host_ip)
            # Uses start_new_session=True instead of external setsid for better compatibility
            r = subprocess.run([self.moonlight_cmd, 'list', target_ip], capture_output=True, text=True, timeout=5, start_new_session=True)
            
            if self.logger: 
                self.logger.debug(f"List apps {host_ip} (target: {target_ip}) stdout: {r.stdout}")
                if r.stderr: self.logger.error(f"List apps {host_ip} stderr: {r.stderr}")
            
            if r.returncode == 0:
                return [l.strip() for l in r.stdout.splitlines() if l.strip()]
            
            # Check for explicit pairing error in stderr
            err = (r.stderr or "").lower()
            if "not paired" in err or "não foi pareado" in err or "unpaired" in err:
                return None
                
            return [] # Other error (timeout, connection refused, etc)
        except Exception as e: 
            if self.logger: self.logger.error(f"List apps error: {e}")
            return []

    def get_status(self): return {'connected': self.is_connected(), 'host': self.connected_host, 'moonlight_cmd': self.moonlight_cmd}
