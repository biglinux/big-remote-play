import subprocess, signal, os, shutil
from pathlib import Path
from utils.i18n import _
class SunshineHost:
    def __init__(self, cdir: Path = None):
        self.config_dir = cdir or (Path.home() / '.config' / 'big-remoteplay' / 'sunshine')
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.pid = None
        
    def start(self, **kwargs):
        if self.is_running():
            print("DEBUG: SunshineHost.start - already running")
            return False
            
        sc = shutil.which('sunshine')
        if not sc:
            print("DEBUG: SunshineHost.start - sunshine executable not found")
            return False
        try:
            config_file = self.config_dir / 'sunshine.conf'
            # Prepare environment
            env = os.environ.copy()
            if 'DISPLAY' not in env:
                env['DISPLAY'] = ':0'
            
            if 'XAUTHORITY' not in env:
                home = os.path.expanduser('~')
                xauth = os.path.join(home, '.Xauthority')
                if os.path.exists(xauth):
                    env['XAUTHORITY'] = xauth
            
            if 'XDG_RUNTIME_DIR' not in env:
                uid = os.getuid()
                runtime_dir = f'/run/user/{uid}'
                if os.path.exists(runtime_dir):
                    env['XDG_RUNTIME_DIR'] = runtime_dir
            
            # Pass WAYLAND_DISPLAY if exists
            if 'WAYLAND_DISPLAY' in os.environ:
                env['WAYLAND_DISPLAY'] = os.environ['WAYLAND_DISPLAY']

            cmd = [
                sc,
                str(config_file)
            ]
            
            # Start process redirecting logs to file
            log_path = self.config_dir / 'sunshine.log'
            self.log_file = open(log_path, 'a')
            
            self.process = subprocess.Popen(
                cmd,
                text=True,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(self.config_dir), # Force CWD for local configs
                start_new_session=True # Create new group ID
            )
            
            self.pid = self.process.pid
            
            # Check if process died immediately (e.g. library error)
            try:
                # Wait a bit to see if startup fails
                print("DEBUG: SunshineHost.start - process started, waiting for exit")
                exit_code = self.process.wait(timeout=2.0)
                
                # If reached here, process ended (failed)
                self.log_file.flush()
                # Try to read the error from the log
                error_detail = ""
                try:
                    with open(log_path, 'r') as f:
                        lines = f.readlines()
                        if lines:
                            # Look for shared library errors in the last 10 lines
                            for line in lines[-10:]:
                                if "error while loading shared libraries" in line or "symbol lookup error" in line:
                                    error_detail = line.strip()
                                    break
                except:
                    pass

                self.log_file.write(_("Sunshine failed to start (Exit code {}).\n").format(exit_code))
                if error_detail:
                    print(_("Sunshine failed to start: {}").format(error_detail))
                else:
                    print(_("Sunshine failed to start (Exit code {}). Check logs.").format(exit_code))
                
                self.process = None
                self.pid = None
                return False
                
            except subprocess.TimeoutExpired:
                # Process continues running after timeout, success!
                pass            
            
            # Save PID
            pid_file = self.config_dir / 'sunshine.pid'
            with open(pid_file, 'w') as f:
                f.write(str(self.pid))
                
            print(_("Sunshine started (PID: {})").format(self.pid))
            return True
            
        except Exception as e:
            print(_("Error starting Sunshine: {}").format(e))
            return False
            
    def stop(self) -> bool:
        """Stops Sunshine server"""
        if not self.is_running():
            print(_("Sunshine is not running"))
            return False
            
        try:
            if self.process:
                print(f"Stopping child process group for PID {self.process.pid}...")
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        print("Timeout, forcing SIGKILL...")
                        os.killpg(pgid, signal.SIGKILL)
                except Exception as e:
                    print(f"Error killing process group: {e}")
                    try: self.process.terminate()
                    except: pass
            else:
                pid_file = self.config_dir / 'sunshine.pid'
                if pid_file.exists():
                    try:
                        with open(pid_file, 'r') as f:
                            pid = int(f.read().strip())
                        print(f"Stopping PID {pid} from file...")
                        os.kill(pid, signal.SIGTERM)
                    except: pass
        
            # Fallback for orphan processes
            subprocess.run(['pkill', 'sunshine'], stderr=subprocess.DEVNULL)
            
            # Close log
            if hasattr(self, 'log_file'):
                try: self.log_file.close()
                except: pass
                del self.log_file
                    
            pid_file = self.config_dir / 'sunshine.pid'
            if pid_file.exists(): pid_file.unlink()
                
            self.process = None
            self.pid = None
            return True
        except Exception as e:
            print(f"Error in stop: {e}")
            subprocess.run(['pkill', '-9', 'sunshine'], stderr=subprocess.DEVNULL)
            return False
            
    def restart(self) -> bool:
        """Restarts the server"""
        self.stop()
        return self.start()
        
    def is_running(self) -> bool:
        """Checks if Sunshine is running"""
        # Check process directly
        if self.process and self.process.poll() is None:
            return True
            
        # Check PID file
        pid_file = self.config_dir / 'sunshine.pid'
        if pid_file.exists():
            try:
                with open(pid_file, 'r') as f:
                    pid = int(f.read().strip())
                    
                # Check if process exists
                os.kill(pid, 0)
                return True
                
            except (OSError, ValueError):
                # Process does not exist, clear PID file
                pid_file.unlink()
                return False
                
        # Check via pgrep
        try:
            result = subprocess.run(
                ['pgrep', '-x', 'sunshine'],
                capture_output=True
            )
            return result.returncode == 0
        except:
            return False
            
    def get_status(self) -> dict:
        """Gets server status"""
        return {
            'running': self.is_running(),
            'pid': self.pid,
            'config_dir': str(self.config_dir),
        }
        
    def update_apps(self, apps_list: list) -> bool:
        """
        Updates application list (apps.json)
        
        Args:
            apps_list: List of dictionaries describing apps
                       Ex: [{'name': 'Steam', 'cmd': 'steam', ...}]
        """
        try:
            import json
            apps_file = self.config_dir / 'apps.json'
            
            # Sunshine apps.json format
            data = {
                "env": {
                    "PATH": "$(PATH):$(HOME)/.local/bin"
                },
                "apps": apps_list
            }
            
            with open(apps_file, 'w') as f:
                json.dump(data, f, indent=4)
                
            return True
        except Exception as e:
            print(f"Error saving apps.json: {e}")
            return False

    def configure(self, settings: dict) -> bool:
        """
        Configures Sunshine
        
        Args:
            settings: Dictionary with settings
        """
        try:
            config_file = self.config_dir / 'sunshine.conf'
            
            # Load existing config
            current_config = {}
            if config_file.exists():
                try:
                    with open(config_file, 'r') as f:
                        for line in f:
                            if '=' in line:
                                parts = line.strip().split('=', 1)
                                if len(parts) == 2:
                                    current_config[parts[0].strip()] = parts[1].strip()
                except Exception as e:
                    print(f"Error reading existing config: {e}")
            
            # Update with new settings
            current_config.update(settings)

            # Ensure pointing to apps.json
            if 'apps_file' not in current_config:
                current_config['apps_file'] = 'apps.json'
            
            # Save merged config
            with open(config_file, 'w') as f:
                for key, value in current_config.items():
                    f.write(f"{key} = {value}\n")
                    
            return True
            
        except Exception as e:
            print(f"Error configuring Sunshine: {e}")
            return False

    def send_pin(self, pin: str, auth: tuple[str, str] = None) -> tuple[bool, str]:
        """Sends PIN to Sunshine via API"""
        import urllib.request, ssl, json, base64
        
        # Use 127.0.0.1 to avoid IPv6 (::1) issues if Sunshine binds to 0.0.0.0
        url = "https://127.0.0.1:47990/api/pin"
        headers = {
            "Content-Type": "application/json",
        }
        
        if auth:
            username, password = auth
            auth_str = f"{username}:{password}"
            b64_auth = base64.b64encode(auth_str.encode()).decode()
            headers["Authorization"] = f"Basic {b64_auth}"
        
        ctx = ssl._create_unverified_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
             # Fix for legacy server support or specific SSL options
             ctx.options |= 0x4  # ssl.OP_LEGACY_SERVER_CONNECT
        except: pass
        
        try:
            data = json.dumps({"pin": pin}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers=headers, method='POST')
            
            with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                if response.status == 200:
                    return True, _("PIN sent successfully")
                return False, f"HTTP Status {response.status}"
                
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, _("Authentication Failed. Configure a user in Sunshine.")
            return False, _("API Error: {} - {}").format(e.code, e.reason)
        except Exception as e:
            return False, _("Connection Error: {}").format(e)

    def create_user(self, username, password) -> tuple[bool, str]:
        """Creates new admin user in Sunshine via API"""
        import urllib.request, ssl, json
        
        url = "https://127.0.0.1:47990/api/users"
        headers = {
            "Content-Type": "application/json",
        }
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        try:
            # Try sending password confirmation too, as error 400 suggests missing fields.
            # Based on web form that requires confirmation.
            # And on field IDs: usernameInput, passwordInput, confirmPasswordInput
            data_dict = {
                "usernameInput": username, 
                "passwordInput": password,
                "confirmPasswordInput": password 
            }
            data = json.dumps(data_dict).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers=headers, method='POST')
            
            with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                if response.status == 200:
                    return True, _("User created successfully")
                return False, f"HTTP Status {response.status}"
                
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf-8') if e.fp else e.reason
            return False, _("API Error: {} - {}").format(e.code, msg)
        except Exception as e:
            return False, _("Connection Error: {}").format(e)
    def terminate_session(self, session_id: str, auth: tuple[str, str] = None) -> bool:
        """Terminates a specific session via Sunshine API"""
        if not session_id:
             print("DEBUG: terminate_session called with empty ID")
             return False

        print(f"DEBUG: SunshineManager.terminate_session called for ID: {session_id}")
        import urllib.request, ssl, base64
        
        # Use 127.0.0.1 to avoid IPv6 issues
        url = f"https://127.0.0.1:47990/api/sessions/{session_id}"
        print(f"DEBUG: Sending DELETE to {url}")
        
        headers = {}
        if auth:
            u, p = auth
            headers["Authorization"] = f"Basic {base64.b64encode(f'{u}:{p}'.encode()).decode()}"
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        try:
            req = urllib.request.Request(url, headers=headers, method='DELETE')
            with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                print(f"DEBUG: Terminate response code: {response.status}")
                return response.status in [200, 204]
        except Exception as e:
            print(f"Error terminating session {session_id}: {e}")
            return False

    def get_performance_stats(self, auth=None) -> dict:
        """Fetches performance stats from Sunshine API"""
        import urllib.request, ssl, json, base64
        url = "https://127.0.0.1:47990/api/stats"
        headers = {"Content-Type": "application/json"}
        if auth:
            u, p = auth
            headers["Authorization"] = f"Basic {base64.b64encode(f'{u}:{p}'.encode()).decode()}"
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        try:
            req = urllib.request.Request(url, headers=headers, method='GET')
            with urllib.request.urlopen(req, context=ctx, timeout=2) as r:
                body = r.read().decode('utf-8')
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404: 
                # Endpoint doesn't exist on this version, silent fail
                return {}
            if e.code == 401: 
                print("DEBUG: Sunshine API 401 Unauthorized - Check credentials")
            else: 
                print(f"DEBUG: Sunshine API HTTP Error (stats): {e.code} - {e.reason}")
            return {}
        except Exception as e:
            return {}

    def get_active_sessions(self, auth=None) -> list:
        """Fetches active sessions from Sunshine API"""
        import urllib.request, ssl, json, base64
        url = "https://127.0.0.1:47990/api/sessions"
        headers = {"Content-Type": "application/json"}
        if auth:
            u, p = auth
            headers["Authorization"] = f"Basic {base64.b64encode(f'{u}:{p}'.encode()).decode()}"
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        try:
            req = urllib.request.Request(url, headers=headers, method='GET')
            with urllib.request.urlopen(req, context=ctx, timeout=2) as r:
                body = r.read().decode('utf-8')
                # print(f"DEBUG: Sunshine API Response (sessions): {body}")
                data = json.loads(body)
                # Handle different Sunshine versions response format
                if isinstance(data, dict): return data.get('sessions', [])
                return data if isinstance(data, list) else []
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Try fallback for older Sunshine versions
                try:
                    url_fallback = "https://127.0.0.1:47990/api/clients/list"
                    req = urllib.request.Request(url_fallback, headers=headers, method='GET')
                    with urllib.request.urlopen(req, context=ctx, timeout=2) as r:
                        data = json.loads(r.read().decode('utf-8'))
                        if isinstance(data, dict):
                            # In older versions, connected clients are in 'clients' and have a 'connected' flag
                            clients = data.get('clients', [])
                            return [c for c in clients if c.get('connected')]
                        return data if isinstance(data, list) else []
                except: return []
            if e.code == 401: print("DEBUG: Sunshine API 401 Unauthorized (sessions)")
            else: print(f"DEBUG: Sunshine API HTTP Error (sessions): {e.code} - {e.reason}")
            return []
        except Exception as e:
            return []
