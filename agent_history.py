"""Small, file-backed conversation history store for Campus Suite agents.

Each agent gets an independent directory under ``history/``. Conversation
files contain the visible transcript and optional JSON-compatible context.
Writes use a temporary file followed by replace so an interrupted save does
not leave a truncated history file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HISTORY_ROOT = Path(__file__).resolve().parent / "history"
_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_ROLES = {"user", "assistant", "system", "tool"}


def _safe_id(value: str, field: str) -> str:
    value = str(value)
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conversation_path(agent_id: str, conversation_id: str) -> Path:
    agent_id = _safe_id(agent_id, "agent id")
    conversation_id = _safe_id(conversation_id, "conversation id")
    return HISTORY_ROOT / agent_id / f"{conversation_id}.json"


def new_conversation(agent_id: str, title: str = "New conversation") -> dict[str, Any]:
    """Create an empty transcript and return it."""
    agent_id = _safe_id(agent_id, "agent id")
    conversation = {
        "id": uuid.uuid4().hex,
        "agent_id": agent_id,
        "title": str(title).strip()[:120] or "New conversation",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
        "context": None,
    }
    _write(conversation)
    return conversation


def _write(conversation: dict[str, Any]) -> None:
    path = _conversation_path(conversation["agent_id"], conversation["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(conversation, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_conversation(agent_id: str, conversation_id: str) -> dict[str, Any] | None:
    """Load a conversation only from the requested agent's history folder."""
    path = _conversation_path(agent_id, conversation_id)
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("agent_id") != agent_id or data.get("id") != conversation_id:
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def list_conversations(agent_id: str) -> list[dict[str, Any]]:
    """List summaries for one agent, newest first."""
    agent_id = _safe_id(agent_id, "agent id")
    folder = HISTORY_ROOT / agent_id
    if not folder.is_dir():
        return []
    conversations = []
    for path in folder.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as stream:
                item = json.load(stream)
            if item.get("agent_id") == agent_id and isinstance(item.get("messages"), list):
                conversations.append({
                    "id": item.get("id"),
                    "agent_id": agent_id,
                    "title": item.get("title", "Conversation"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "message_count": len(item["messages"]),
                })
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(conversations, key=lambda item: item.get("updated_at") or "", reverse=True)


def append_message(
    agent_id: str,
    conversation_id: str,
    role: str,
    content: str,
    title: str | None = None,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a visible chat message and return the updated conversation."""
    if role not in _ROLES:
        raise ValueError("Invalid message role")
    conversation = load_conversation(agent_id, conversation_id)
    if conversation is None:
        raise FileNotFoundError("Conversation not found for this agent")
    message: dict[str, Any] = {"role": role, "content": str(content), "created_at": _now()}
    if metadata:
        # Metadata must be JSON-compatible; dumping validates it before saving.
        message["metadata"] = metadata
    conversation["messages"].append(message)
    if title is not None:
        conversation["title"] = str(title).strip()[:120] or "New conversation"
    elif role == "user" and conversation.get("title") == "New conversation":
        conversation["title"] = str(content).strip().splitlines()[0][:120] or "New conversation"
    conversation["updated_at"] = _now()
    _write(conversation)
    return conversation


def set_context(agent_id: str, conversation_id: str, context: Any) -> dict[str, Any]:
    """Save JSON-compatible agent context alongside the visible transcript."""
    conversation = load_conversation(agent_id, conversation_id)
    if conversation is None:
        raise FileNotFoundError("Conversation not found for this agent")
    # Round-trip now so unsupported model objects fail close to the caller.
    conversation["context"] = json.loads(json.dumps(context, ensure_ascii=False))
    conversation["updated_at"] = _now()
    _write(conversation)
    return conversation

