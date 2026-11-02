"""Small, local-only goal tools for the Personal Growth Mentor."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.genai import types

ROOT_DIR = Path(__file__).resolve().parents[2]
STORE_DIR = ROOT_DIR / "storage" / "agents" / "personal_growth_mentor"
_LOCKS_GUARD = threading.Lock()
_SCOPE_LOCKS: dict[str, threading.RLock] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store_path(scope_id: str) -> Path:
    # A conversation-scoped hash avoids putting user-entered text in a filename.
    digest = hashlib.sha256(str(scope_id).encode("utf-8")).hexdigest()[:32]
    return STORE_DIR / f"{digest}.json"


def _scope_lock(scope_id: str) -> threading.RLock:
    digest = hashlib.sha256(str(scope_id).encode("utf-8")).hexdigest()[:32]
    with _LOCKS_GUARD:
        return _SCOPE_LOCKS.setdefault(digest, threading.RLock())


def _read_state(scope_id: str) -> dict[str, Any]:
    path = _store_path(scope_id)
    if not path.exists():
        return {"goals": [], "check_ins": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("goals", [])
            data.setdefault("check_ins", [])
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"goals": [], "check_ins": []}


def _write_state(scope_id: str, state: dict[str, Any]) -> None:
    path = _store_path(scope_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _result(status: str, summary: str, next_actions: list[str] | None = None,
            artifacts: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "next_actions": next_actions or [],
        "artifacts": artifacts or [],
    }


def create_goal(scope_id: str, title: str, outcome: str, next_action: str,
                target_date: str = "") -> dict[str, Any]:
    title = str(title or "").strip()
    outcome = str(outcome or "").strip()
    next_action = str(next_action or "").strip()
    target_date = str(target_date or "").strip()
    if not title or not outcome or not next_action:
        return _result("error", "A goal needs a title, measurable outcome, and first action.",
                       ["Ask the student for the missing detail, then retry."])
    if any(len(value) > limit for value, limit in ((title, 120), (outcome, 500), (next_action, 300), (target_date, 40))):
        return _result("error", "One or more goal fields are too long.",
                       ["Shorten the goal details and retry."])

    state = _read_state(scope_id)
    goal = {
        "id": f"goal-{uuid.uuid4().hex[:10]}",
        "title": title,
        "outcome": outcome,
        "next_action": next_action,
        "target_date": target_date,
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
    }
    state["goals"].append(goal)
    _write_state(scope_id, state)
    return _result("success", f"Saved goal: {title}",
                   ["Start with the saved first action.", "Check in after the student makes progress."],
                   [{"goal_id": goal["id"]}])


def list_goals(scope_id: str, status: str = "all") -> dict[str, Any]:
    state = _read_state(scope_id)
    allowed = {"all", "active", "completed", "paused"}
    status = status if status in allowed else "all"
    goals = [goal for goal in state["goals"] if status == "all" or goal.get("status") == status]
    if not goals:
        return _result("success", "No saved goals match that filter.",
                       ["Ask what skill or outcome the student wants to work toward."])
    check_ins_by_goal: dict[str, list[dict[str, Any]]] = {}
    for check_in in state["check_ins"]:
        check_ins_by_goal.setdefault(check_in.get("goal_id", ""), []).append(check_in)
    details = [
        {**goal, "recent_check_ins": check_ins_by_goal.get(goal.get("id", ""), [])[-5:]}
        for goal in goals
    ]
    return _result("success", f"Found {len(goals)} goal(s).", artifacts=details)


def record_goal_checkin(scope_id: str, goal_id: str, progress_note: str,
                        next_action: str = "", status: str = "") -> dict[str, Any]:
    goal_id = str(goal_id or "").strip()
    progress_note = str(progress_note or "").strip()
    next_action = str(next_action or "").strip()
    status = str(status or "").strip()
    if not goal_id or not progress_note:
        return _result("error", "A goal ID and progress note are required.",
                       ["List the student's goals and ask what changed before retrying."])
    if len(progress_note) > 1000 or len(next_action) > 300:
        return _result("error", "The check-in details are too long.", ["Shorten the note and retry."])
    if status and status not in {"active", "paused", "completed"}:
        status = ""

    state = _read_state(scope_id)
    goal = next((item for item in state["goals"] if item.get("id") == goal_id), None)
    if goal is None:
        return _result("warning", "That goal was not found in this conversation.",
                       ["List saved goals and choose a valid goal ID."])

    check_in = {
        "id": f"checkin-{uuid.uuid4().hex[:10]}",
        "goal_id": goal_id,
        "progress_note": progress_note,
        "next_action": next_action,
        "created_at": _now(),
    }
    state["check_ins"].append(check_in)
    if status:
        goal["status"] = status
    if next_action:
        goal["next_action"] = next_action
    goal["updated_at"] = _now()
    _write_state(scope_id, state)
    return _result("success", f"Saved progress check-in for {goal['title']}.",
                   ["Use the updated next action as the next small step."],
                   [{"goal_id": goal_id, "check_in_id": check_in["id"]}])


TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="create_goal",
        description="Save a learning, skill-building, or personal development goal only when the student asks to save or set one.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "title": types.Schema(type="STRING", description="Short goal name."),
                "outcome": types.Schema(type="STRING", description="Specific, measurable outcome."),
                "next_action": types.Schema(type="STRING", description="Small first action the student can take."),
                "target_date": types.Schema(type="STRING", description="Optional target date, written plainly."),
            },
            required=["title", "outcome", "next_action"],
        ),
    ),
    types.FunctionDeclaration(
        name="list_goals",
        description="List goals saved for this conversation, optionally filtered by status.",
        parameters=types.Schema(
            type="OBJECT",
            properties={"status": types.Schema(type="STRING", description="all | active | completed | paused")},
        ),
    ),
    types.FunctionDeclaration(
        name="record_goal_checkin",
        description="Save a student-requested progress check-in for one saved goal and optionally set its next action or status.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "goal_id": types.Schema(type="STRING", description="Goal ID returned by list_goals or create_goal."),
                "progress_note": types.Schema(type="STRING", description="Brief student progress update."),
                "next_action": types.Schema(type="STRING", description="Optional revised next action."),
                "status": types.Schema(type="STRING", description="active | paused | completed; use completed only when the student says the goal is done."),
            },
            required=["goal_id", "progress_note"],
        ),
    ),
]

TOOL = types.Tool(function_declarations=TOOL_DECLARATIONS)
IMPLEMENTATIONS = {
    "create_goal": create_goal,
    "list_goals": list_goals,
    "record_goal_checkin": record_goal_checkin,
}


def execute_tool(name: str, args: dict[str, Any], scope_id: str) -> dict[str, Any]:
    """Run one allow-listed tool and return the stable result envelope."""
    function = IMPLEMENTATIONS.get(name)
    if function is None:
        return _result("error", f"Unknown tool: {name}", ["Stop and ask for a different request."])
    try:
        with _scope_lock(scope_id):
            return function(scope_id=scope_id, **args)
    except (TypeError, ValueError) as exc:
        return _result("error", f"Invalid arguments for {name}: {exc}",
                       ["Correct the arguments and retry once."])
    except OSError as exc:
        return _result("error", f"Local goal storage failed: {exc}",
                       ["Tell the student the goal was not saved; try again later."])
