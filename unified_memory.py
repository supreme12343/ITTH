"""Local, append-only activity memory shared by Campus Suite and MAIC.

This intentionally stores activity metadata, not API keys, prompts containing secrets,
or raw media.  Both the Python desktop tools and MAIC's Next.js API use the same file.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PATH = Path(__file__).resolve().parent / "storage" / "unified_memory.json"
_LOCK = threading.Lock()
_MAX_EVENTS = 1000


def record_activity(source: str, action: str, details: dict[str, Any] | None = None) -> None:
    event = {
        "at": datetime.now(timezone.utc).isoformat(),
        "source": source[:80],
        "action": action[:120],
        "details": details or {},
    }
    with _LOCK:
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            existing = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else []
            if not isinstance(existing, list):
                existing = []
            existing.append(event)
            _PATH.write_text(json.dumps(existing[-_MAX_EVENTS:], ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, TypeError, json.JSONDecodeError):
            # Activity tracking must never stop a learning or agent workflow.
            pass
