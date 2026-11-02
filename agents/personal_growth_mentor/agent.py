"""Tool-calling Personal Growth Mentor for student learning and IT talent discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.genai import types

from key_manager import RoleKeyManager
from .tools import TOOL, execute_tool

MODEL = "gemini-3.1-flash-lite"
MAX_TOOL_ROUNDS = 5
MAX_TOOL_CALLS_PER_ROUND = 3
MAX_HISTORY_MESSAGES = 12

_INSTRUCTIONS = (Path(__file__).with_name("instructions.md")).read_text(encoding="utf-8")


class PersonalGrowthMentor:
    """Coach students with a constrained, local goal and check-in tool set."""

    def __init__(self, key_manager: Any, scope_id: str = "local-conversation"):
        self.km = key_manager._km if hasattr(key_manager, "_km") else key_manager
        self.scope_id = str(scope_id)

    @staticmethod
    def _parts(candidate: Any) -> list[Any]:
        content = getattr(candidate, "content", None)
        return list(getattr(content, "parts", None) or [])

    def chat(self, history: list[Any], user_message: str) -> dict[str, Any]:
        history = list(history[-MAX_HISTORY_MESSAGES:])
        visible_history = history + [types.Content(role="user", parts=[types.Part(text=user_message)])]
        contents = list(visible_history)
        config = types.GenerateContentConfig(
            system_instruction=_INSTRUCTIONS,
            tools=[TOOL],
            temperature=0.45,
        )
        keys_used: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for _round in range(MAX_TOOL_ROUNDS):
            response, key_label = self.km.generate(
                RoleKeyManager.TOOL_POOL,
                model=MODEL,
                contents=contents,
                config=config,
            )
            keys_used.append(key_label)
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                return self._finish("I couldn't get a response just now. Please try again.",
                                    visible_history, keys_used, tool_calls)

            candidate = candidates[0]
            parts = self._parts(candidate)
            calls = [getattr(part, "function_call", None) for part in parts]
            calls = [call for call in calls if call is not None]
            if not calls:
                answer = "".join(getattr(part, "text", "") or "" for part in parts).strip()
                if not answer:
                    answer = "I didn't get a usable response. Try asking in a different way."
                contents.append(candidate.content)
                return {
                    "reply": answer,
                    "history": (visible_history + [types.Content(role="model", parts=[types.Part(text=answer)])])[-MAX_HISTORY_MESSAGES:],
                    "tool_calls": tool_calls,
                    "keys_used": keys_used,
                    "mode": "mentor",
                }

            contents.append(candidate.content)
            response_parts = []
            for call in calls[:MAX_TOOL_CALLS_PER_ROUND]:
                name = getattr(call, "name", "")
                args = dict(getattr(call, "args", None) or {})
                result = execute_tool(name, args, self.scope_id)
                tool_calls.append({"tool": name, "args": args, "status": result.get("status")})
                response_parts.append(types.Part.from_function_response(name=name, response=result))
            if len(calls) > MAX_TOOL_CALLS_PER_ROUND:
                for call in calls[MAX_TOOL_CALLS_PER_ROUND:]:
                    name = getattr(call, "name", "unknown")
                    response_parts.append(types.Part.from_function_response(
                        name=name,
                        response={
                            "status": "warning",
                            "summary": "This turn reached the tool-call limit.",
                            "next_actions": ["Continue only if the student still needs this action."],
                            "artifacts": [],
                        },
                    ))
            contents.append(types.Content(role="user", parts=response_parts))

        return self._finish(
            "I reached the action limit for this turn. The saved goal tools have completed; "
            "ask me to continue if you still need help.",
            visible_history,
            keys_used,
            tool_calls,
        )

    @staticmethod
    def _finish(reply: str, visible_history: list[Any],
                keys_used: list[str], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        visible_history.append(types.Content(role="model", parts=[types.Part(text=reply)]))
        return {
            "reply": reply,
            "history": visible_history[-MAX_HISTORY_MESSAGES:],
            "tool_calls": tool_calls,
            "keys_used": keys_used,
            "mode": "mentor",
        }
