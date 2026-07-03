"""Plugin telemetry — usage tracking via PostHog.

Fire-and-forget batched queue: flush every 5s or 10 events,
drain on atexit. Never blocks plugin work, never raises.

Opt-in: set MEMORYLAKE_POSTHOG_API_KEY to enable.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"
_FLUSH_INTERVAL = 5.0
_FLUSH_THRESHOLD = 10
_HTTP_TIMEOUT = 3


def _read_plugin_version() -> str:
    try:
        p = Path(__file__).parent / "plugin.yaml"
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "dev"


_PLUGIN_VERSION = _read_plugin_version()

_POSTHOG_API_KEY: str = os.environ.get("MEMORYLAKE_POSTHOG_API_KEY", "").strip()
_POSTHOG_HOST: str = (
    os.environ.get("MEMORYLAKE_POSTHOG_HOST", "").strip() or _DEFAULT_POSTHOG_HOST
).rstrip("/")

_lock = threading.Lock()
_queue: list[Dict[str, Any]] = []
_flush_timer: Optional[threading.Timer] = None
_atexit_registered = False


def _is_enabled() -> bool:
    return bool(_POSTHOG_API_KEY)


def _distinct_id(api_key: str = "") -> str:
    return hashlib.sha256((api_key or "").encode()).hexdigest()


def _send_batch(batch: list[Dict[str, Any]]) -> None:
    try:
        body = json.dumps({"api_key": _POSTHOG_API_KEY, "batch": batch}).encode()
        req = Request(
            f"{_POSTHOG_HOST}/i/v0/e/",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=_HTTP_TIMEOUT)
    except Exception:
        pass


def _flush() -> None:
    global _flush_timer
    with _lock:
        if not _queue:
            _flush_timer = None
            return
        batch = list(_queue)
        _queue.clear()
        _flush_timer = None
    _send_batch(batch)


def _schedule_flush() -> None:
    global _flush_timer
    if _flush_timer is not None:
        return
    t = threading.Timer(_FLUSH_INTERVAL, _flush)
    t.daemon = True
    t.start()
    _flush_timer = t


def _atexit_flush() -> None:
    with _lock:
        batch = list(_queue)
        _queue.clear()
    if batch:
        _send_batch(batch)


def capture_event(
    event: str,
    properties: Dict[str, Any] | None = None,
    *,
    api_key: str = "",
) -> None:
    """Queue a telemetry event. No-op when MEMORYLAKE_POSTHOG_API_KEY is unset."""
    if not _is_enabled():
        return

    global _atexit_registered
    try:
        entry: Dict[str, Any] = {
            "event": event,
            "distinct_id": _distinct_id(api_key),
            "properties": {
                "source": "HERMES",
                "language": "python",
                "plugin_version": _PLUGIN_VERSION,
                "python_version": platform.python_version(),
                "os": sys.platform,
                "$process_person_profile": False,
                "$lib": "posthog-node",
                **(properties or {}),
            },
        }

        with _lock:
            _queue.append(entry)
            should_flush = len(_queue) >= _FLUSH_THRESHOLD
            if not _atexit_registered:
                atexit.register(_atexit_flush)
                _atexit_registered = True

        if should_flush:
            _flush()
        else:
            _schedule_flush()
    except Exception:
        pass
