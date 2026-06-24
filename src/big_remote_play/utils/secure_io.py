"""Filesystem helpers for writing secrets and config with owner-only permissions.

Auth tokens, VPN history (contains auth keys) and sunshine.conf (credentials)
must not be world-readable. These helpers create the file mode 0o600 and tighten
the containing directory to 0o700.
"""

import os
import tempfile


def _secure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def secure_write_text(path: str, text: str) -> None:
    """Atomically write text to a 0o600 file inside a 0o700 directory."""
    _secure_dir(path)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
