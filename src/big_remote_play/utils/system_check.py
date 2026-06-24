"""
System component verification
"""

import subprocess
import shutil
from big_remote_play.utils.i18n import _


class SystemCheck:
    """System component checker"""

    def __init__(self):
        pass

    def has_sunshine(self) -> bool:
        """Checks if Sunshine is installed"""
        return shutil.which("sunshine") is not None

    def has_moonlight(self) -> bool:
        """Checks if Moonlight is installed"""
        # Moonlight may have different names
        return shutil.which("moonlight") is not None or shutil.which("moonlight-qt") is not None

    def has_avahi(self) -> bool:
        """Checks if Avahi is installed"""
        return shutil.which("avahi-browse") is not None

    def has_docker(self) -> bool:
        """Checks if Docker is installed"""
        return shutil.which("docker") is not None

    def has_zerotier(self) -> bool:
        """Checks if ZeroTier is installed"""
        return shutil.which("zerotier-cli") is not None

    def has_tailscale(self) -> bool:
        """Checks if Tailscale is installed"""
        return shutil.which("tailscale") is not None

    def check_all(self) -> dict:
        """Checks all components"""
        return {
            "sunshine": self.has_sunshine(),
            "moonlight": self.has_moonlight(),
            "avahi": self.has_avahi(),
            "docker": self.has_docker(),
            "tailscale": self.has_tailscale(),
            "zerotier": self.has_zerotier(),
        }

    def is_sunshine_running(self) -> bool:
        """Checks if Sunshine process is running"""
        try:
            result = subprocess.run(["pgrep", "-x", "sunshine"], capture_output=True, timeout=2)
            return result.returncode == 0
        except Exception:
            return False

    def is_docker_running(self) -> bool:
        """Checks if Docker daemon is running"""
        try:
            return subprocess.run(["systemctl", "is-active", "--quiet", "docker"], timeout=5).returncode == 0
        except Exception:
            return False

    def are_containers_running(self) -> bool:
        """Checks if app containers (caddy, headscale) are running"""
        try:
            result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                output = result.stdout.strip()
                return "caddy" in output and "headscale" in output
            return False
        except Exception:
            return False

    def is_tailscale_running(self) -> bool:
        """Checks if Tailscale daemon is running"""
        try:
            return subprocess.run(["systemctl", "is-active", "--quiet", "tailscaled"], timeout=5).returncode == 0
        except Exception:
            return False

    def is_zerotier_running(self) -> bool:
        """Checks if ZeroTier daemon is running"""
        try:
            return subprocess.run(["systemctl", "is-active", "--quiet", "zerotier-one"], timeout=5).returncode == 0
        except Exception:
            return False

    def is_moonlight_running(self) -> bool:
        """Checks if Moonlight process is running (ignores zombies)"""
        try:
            for process_name in ["moonlight", "moonlight-qt"]:
                # Get PIDs
                result = subprocess.run(
                    ["pgrep", "-x", process_name],
                    capture_output=True,
                    text=True,  # Important to read output as text
                    timeout=2,
                )

                if result.returncode == 0 and result.stdout:
                    pids = result.stdout.strip().split()
                    for pid in pids:
                        # Check process state
                        try:
                            state_check = subprocess.run(["ps", "-o", "state=", "-p", pid], capture_output=True, text=True, timeout=1)
                            if state_check.returncode == 0:
                                state = state_check.stdout.strip()
                                # If state is not Z (Zombie) or T (Stopped), consider running
                                if state and state not in ["Z", "T", "Z+"]:
                                    return True
                        except Exception:
                            continue

            return False
        except Exception:
            return False

    def get_sunshine_version(self) -> str:
        """Gets Sunshine version"""
        try:
            result = subprocess.run(["sunshine", "--version"], capture_output=True, text=True, timeout=2)

            if result.returncode == 0:
                return result.stdout.strip()
            else:
                return _("Unknown")

        except Exception:
            return _("Unknown")

    def check_sunshine_runtime(self) -> tuple[bool, str]:
        """Verify that the installed Sunshine binary can start far enough to print its version."""
        if not self.has_sunshine():
            return False, _("Sunshine executable not found")
        try:
            result = subprocess.run(["sunshine", "--version"], capture_output=True, text=True, timeout=5)
            detail = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0:
                return True, detail or _("Sunshine runtime is available")
            return False, detail or _("Sunshine failed to report its version")
        except Exception as exc:
            return False, str(exc)

    def get_moonlight_version(self) -> str:
        """Gets Moonlight version"""
        try:
            # Try different variants
            for cmd in ["moonlight-qt", "moonlight"]:
                result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=2)

                if result.returncode == 0:
                    return result.stdout.strip()

            return _("Unknown")

        except Exception:
            return _("Unknown")
