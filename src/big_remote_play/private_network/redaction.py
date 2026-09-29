"""Central redaction of credentials from text that may be logged or shown.

Every string that leaves the process through a log, a diagnostic report, a
toast or an error dialog can carry CLI output or an HTTP error body. Those are
passed through :func:`redact` first. The patterns cover the credential shapes
this application handles; :func:`redact_values` additionally removes exact
values the caller knows (a token it just used).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
import re

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    # Tailscale keys: tskey-auth-…, tskey-api-…, tskey-client-…, tskey-scim-….
    (re.compile(r"\b(tskey-[a-z]+-)[A-Za-z0-9_-]+"), r"\1" + REDACTED),
    # Headscale keys that carry a recognisable prefix.
    (re.compile(r"\b(hskey-[a-z]+-)[A-Za-z0-9_.-]+"), r"\1" + REDACTED),
    # HTTP authorization headers, whatever the scheme.
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:(token|bearer|basic)\s+)?[^\s\"',;]+"), lambda m: f"{m.group(1)}{(m.group(2) + ' ') if m.group(2) else ''}{REDACTED}"),
    # JSON members whose value is a credential.
    (
        re.compile(r"(?i)(\"(?:key|secret|token|password|authkey|auth_key|apikey|api_key|access_token|client_secret|refresh_token)\"\s*:\s*\")[^\"]*(\")"),
        r"\1" + REDACTED + r"\2",
    ),
    # key=value / key: value pairs in CLI output, argv or query strings. A
    # ``file:`` reference names a path, not the secret, and is kept.
    (
        re.compile(r"(?i)\b((?:auth[_-]?key|authkey|api[_-]?key|api[_-]?token|access[_-]?token|client[_-]?secret|password|passwd|secret|token)\s*[=:]\s*)(?!file:)(?!\[REDACTED\])[^\s&\"',;]+"),
        r"\1" + REDACTED,
    ),
)


def redact(text: object) -> str:
    """Return ``text`` with every recognisable credential replaced."""
    value = "" if text is None else str(text)
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact_values(text: object, secrets: Iterable[str]) -> str:
    """Remove the exact ``secrets`` (longest first), then apply :func:`redact`."""
    value = "" if text is None else str(text)
    for secret in sorted({item for item in secrets if item and len(item) >= 4}, key=len, reverse=True):
        value = value.replace(secret, REDACTED)
    return redact(value)


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the fully formatted message of a record.

    The message is formatted once here, so a secret passed as a ``%s`` argument
    is caught as reliably as one embedded in the format string. Tracebacks are
    formatted and redacted as well, because an exception message can quote a
    request header or a command line.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a malformed record must not break logging
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info:
            formatted = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = redact(formatted)
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_log_redaction(logger: logging.Logger) -> None:
    """Attach one :class:`RedactingFilter` to ``logger`` and its handlers."""
    targets: list[logging.Filterer] = [logger, *logger.handlers]
    for target in targets:
        if not any(isinstance(existing, RedactingFilter) for existing in target.filters):
            target.addFilter(RedactingFilter())
