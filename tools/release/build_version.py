#!/usr/bin/env python3
"""Single source of truth for Big Remote Play build-date versions."""

from __future__ import annotations
import datetime as _dt
import os as _os
import re as _re

_VERSION_RE = _re.compile(r"^(?:\d{2}|\d{4})\.\d{2}\.\d{2}$")


def build_datetime(env: dict[str, str] | None = None) -> _dt.datetime:
    values = _os.environ if env is None else env
    raw = values.get("SOURCE_DATE_EPOCH")
    if raw:
        try:
            value = int(raw, 10)
        except ValueError as exc:
            raise ValueError("SOURCE_DATE_EPOCH must be an integer Unix timestamp") from exc
        if value < 0:
            raise ValueError("SOURCE_DATE_EPOCH must not be negative")
        return _dt.datetime.fromtimestamp(value, tz=_dt.timezone.utc)
    return _dt.datetime.now(tz=_dt.timezone.utc)


def build_version(env: dict[str, str] | None = None) -> str:
    values = _os.environ if env is None else env
    override = values.get("BRP_BUILD_VERSION")
    if override:
        if not _VERSION_RE.fullmatch(override):
            raise ValueError("BRP_BUILD_VERSION must use YY.MM.DD or YYYY.MM.DD")
        return override
    return build_datetime(values).strftime("%y.%m.%d")


def main() -> int:
    print(build_version())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
