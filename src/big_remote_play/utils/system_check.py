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

    def has_pacman(self) -> bool:
        """True on Arch-family distros where we install VPN packages via pacman."""
        return shutil.which("pacman") is not None

    def flatpak_app_id(self, keyword: str) -> str | None:
        """First installed Flatpak app id whose id contains `keyword`, or None.

        Lets us recognise (and later run via `flatpak run`) a VPN tool the user
        already installed as a Flatpak, without hardcoding a guessed app id."""
        if shutil.which("flatpak") is None:
            return None
        try:
            r = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                key = keyword.lower()
                for line in r.stdout.splitlines():
                    app = line.strip()
                    if key in app.lower():
                        return app
        except Exception:
            pass
        return None

    def tailscale_cmd(self) -> list[str]:
        """Argv prefix to invoke the tailscale CLI (native or Flatpak)."""
        if shutil.which("tailscale") is not None:
            return ["tailscale"]
        fid = self.flatpak_app_id("tailscale")
        if fid is not None:
            return ["flatpak", "run", "--command=tailscale", fid]
        return ["tailscale"]

    def has_sunshine(self) -> bool:
        """Sunshine as Big Remote Play starts it: a native executable."""
        from big_remote_play.utils.dependencies import COMPONENTS, find_executable

        return find_executable(COMPONENTS["sunshine"].executables) is not None

    def has_moonlight(self) -> bool:
        """Moonlight under either of its executable names."""
        from big_remote_play.utils.dependencies import COMPONENTS, find_executable

        return find_executable(COMPONENTS["moonlight"].executables) is not None

    def has_avahi(self) -> bool:
        """Checks if Avahi is installed"""
        return shutil.which("avahi-browse") is not None

    def zerotier_cmd(self) -> list[str]:
        """Argv prefix to invoke the ZeroTier CLI (native or Flatpak)."""
        if shutil.which("zerotier-cli") is not None:
            return ["zerotier-cli"]
        fid = self.flatpak_app_id("zerotier")
        if fid is not None:
            return ["flatpak", "run", "--command=zerotier-cli", fid]
        return ["zerotier-cli"]

    def has_zerotier(self) -> bool:
        """Checks if ZeroTier is installed (native or Flatpak)"""
        return shutil.which("zerotier-cli") is not None or self.flatpak_app_id("zerotier") is not None

    def has_tailscale(self) -> bool:
        """Checks if Tailscale is installed (native or Flatpak)"""
        return shutil.which("tailscale") is not None or self.flatpak_app_id("tailscale") is not None

    def is_sunshine_running(self) -> bool:
        """Checks if Sunshine process is running"""
        try:
            result = subprocess.run(["pgrep", "-x", "sunshine"], capture_output=True, timeout=2)
            return result.returncode == 0
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
