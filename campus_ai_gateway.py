"""Local OpenAI-compatible adapter backed by Campus Suite's AiApi.

MAIC speaks the OpenAI chat-completions protocol. This process translates
those requests to the existing Python key manager, keeping provider keys out
of the browser and out of the MAIC settings store.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from campus_codex_provider import CodexPromptError, CodexProviderError

HOST = "127.0.0.1"
PORT = int(os.environ.get("CAMPUS_AI_PORT", "3110"))

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_PATH = LOG_DIR / "campus_ai_gateway.log"

# `api` is created on first use rather than at import time: a missing key or a
# bad config used to raise inside `AiApi()` while this module was being
# imported, which took the whole gateway down — and MAIC with it — with no
# usable message. It is still reachable as `campus_ai_gateway.api` through the
# module __getattr__ below so existing callers keep working.
_api = None
_api_lock = threading.Lock()
study_agent = None
video_agent = None
_agent_lock = threading.Lock()

_logger = None
_logger_lock = threading.Lock()

# Secret values are never written to the log. Known key env vars are redacted
# by value, and the common provider key shapes are redacted by pattern.
_SECRET_ENV_NAMES = (
    "OMNIROUTE_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_1",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEY_4",
    "GEMINI_API_KEY_5",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)
_SECRET_PATTERNS = (
    re.compile(r"AQ\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
)

_AI_UNAVAILABLE_MESSAGE = (
    "Campus Suite AI is currently unavailable. Check local provider configuration and gateway logs."
)
_CODEX_UNAVAILABLE_MESSAGE = "The Codex provider is currently unavailable."


def _scrub(text: str) -> str:
    """Strip anything that looks like a credential out of log output."""
    if not text:
        return ""
    for name in _SECRET_ENV_NAMES:
        value = os.environ.get(name)
        if value and len(value) >= 8:
            text = text.replace(value, f"<{name}:redacted>")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("<redacted-key>", text)
    return text


def _get_logger() -> logging.Logger:
    """Real server-side logging, created lazily so import stays side-effect free."""
    global _logger
    with _logger_lock:
        if _logger is not None:
            return _logger
        logger = logging.getLogger("campus_ai_gateway")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler: logging.Handler
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                handler = logging.handlers.RotatingFileHandler(
                    LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
                )
            except OSError:
                handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        _logger = logger
        return logger


def _log_error(context: str, exc: BaseException) -> None:
    """Record the full traceback server-side; the HTTP body stays clean."""
    try:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _get_logger().error("%s: %s", _scrub(context), _scrub(detail))
    except Exception:
        pass


def _log_info(message: str) -> None:
    try:
        _get_logger().info("%s", _scrub(message))
    except Exception:
        pass


class GatewayUnavailableError(RuntimeError):
    """The AI core could not be initialised — a configuration problem, not a bug."""


def _create_api():
    """Import and construct AiApi only when an AI route first needs it."""
    from key_manager import AiApi

    return AiApi()


def _get_api():
    """Build the shared AiApi on first use, with a clear diagnostic on failure."""
    global _api
    with _api_lock:
        if _api is None:
            try:
                # Keep construction lazy so the gateway can start without provider SDKs.
                _api = _create_api()
            except Exception as exc:
                _log_error("AiApi initialisation failed", exc)
                raise GatewayUnavailableError(_AI_UNAVAILABLE_MESSAGE) from exc
            globals()["api"] = _api
            _log_info("AiApi initialised")
        return _api


def __getattr__(name: str):
    """Keep `campus_ai_gateway.api` working now that construction is lazy."""
    if name == "api":
        return _get_api()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _get_study_agent():
    """Lock-guarded lazy init: ThreadingHTTPServer can race two first requests."""
    global study_agent
    with _agent_lock:
        if study_agent is None:
            from study_agent import StudyAgent

            study_agent = StudyAgent(_get_api())
        return study_agent


def _get_video_agent():
    """Lock-guarded lazy init: ThreadingHTTPServer can race two first requests."""
    global video_agent
    with _agent_lock:
        if video_agent is None:
            from tempfile import gettempdir
            from video_editing_agent import VideoEditingAgent

            video_agent = VideoEditingAgent(
                _get_api(),
                bridge_dir=str(Path(gettempdir()) / "campus_suite_video_agent_bridge"),
            )
        return video_agent


def _message_text(response) -> str:
    """Read an OpenAI-shaped reply without trusting choices[0] to exist."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise RuntimeError(
            "The AI provider returned no choices — the response was empty or blocked. "
            f"See {LOG_PATH} for the provider diagnostics."
        )
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if content is None:
        raise RuntimeError(
            "The AI provider returned a reply with no message content (usually a safety "
            f"block or a truncated response). See {LOG_PATH} for details."
        )
    return content


class ChatRequestValidationError(ValueError):
    """A malformed chat-completions message list."""


def _general_chat_messages(messages):
    """Validate a text chat while keeping user and assistant turns distinct."""
    if not isinstance(messages, list) or not messages:
        raise ChatRequestValidationError("At least one chat message is required.")

    system_parts = []
    conversation = []
    for message in messages:
        if not isinstance(message, dict):
            raise ChatRequestValidationError("Each chat message must be an object.")
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "developer", "user", "assistant"}:
            raise ChatRequestValidationError("Chat messages require system, developer, user, or assistant roles.")
        if not isinstance(content, str):
            raise ChatRequestValidationError("Chat message content must be text.")
        if role in {"system", "developer"}:
            system_parts.append(content)
        else:
            conversation.append({"role": role, "content": content})

    if not any(message["role"] == "user" for message in conversation):
        raise ChatRequestValidationError("At least one user message is required.")

    system = "\n\n".join(system_parts)
    user = "\n".join(message["content"] for message in conversation if message["role"] == "user")
    return system, user, conversation


def _redact_request_text(value):
    """Scrub credential-like strings before any gateway input reaches an agent."""
    from campus_ai import redact_sensitive_text

    def visit(item, *, top_level=False):
        if isinstance(item, str):
            return redact_sensitive_text(item)
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            clean = {}
            for key, child in item.items():
                # Only the top-level workspace and conversation ID are
                # operational values. Nested properties with those names can
                # be prompt content and must still be scanned.
                preserve_identifier = (
                    top_level
                    and key in {"workspace", "conversationId"}
                    and isinstance(child, str)
                )
                clean_key = redact_sensitive_text(key) if isinstance(key, str) else key
                clean[clean_key] = child if preserve_identifier else visit(child)
            return clean
        return item

    return visit(value, top_level=True)


def _warm_imports() -> None:
    """Pre-import the common request-path modules so the first call is not slow."""
    for module in ("google.genai", "study_agent", "quiz_generator", "coding_agent"):
        try:
            __import__(module)
        except Exception as exc:  # a missing optional dep must not kill the server
            _log_info(f"warmup skipped {module}: {type(exc).__name__}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        # stdout stays clean for MAIC, but the request is still recorded.
        try:
            _log_info(f"{self.command} {self.path}")
        except Exception:
            pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_dependency_error(self, exc: ImportError) -> None:
        candidate = getattr(exc, "name", None)
        missing = (
            candidate
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", candidate)
            else "a required Python package"
        )
        self._send(503, {"error": {
            "message": (
                f"This feature needs the Python package '{missing}', which is not installed "
                "on the gateway. Install it on the server, then retry."
            ),
            "type": "campus_dependency_missing",
        }})

    def _send_sse_completion(self, completion: dict) -> None:
        """Send a complete Codex reply using OpenAI-compatible SSE framing."""
        choice = completion["choices"][0]
        chunk = {
            "id": completion["id"],
            "object": "chat.completion.chunk",
            "created": completion["created"],
            "model": completion["model"],
            "choices": [{
                "index": 0,
                "delta": {
                    "role": choice["message"]["role"],
                    "content": choice["message"]["content"],
                },
                "finish_reason": None,
            }],
        }
        final_chunk = {
            "id": completion["id"],
            "object": "chat.completion.chunk",
            "created": completion["created"],
            "model": completion["model"],
            "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        events = (
            f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
            "data: [DONE]\n\n"
        )
        try:
            self.wfile.write(events.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/v1/health"}:
            self._send(200, {"status": "ok", "provider": "campus-suite-key-manager", "service_version": 2})
            return
        if self.path == "/v1/status":
            try:
                status_fn = getattr(getattr(_get_api(), "_km", None), "status", None)
                if not callable(status_fn):
                    raise RuntimeError("The active key manager does not expose a status() report.")
                self._send(200, {"status": "ok", "keys": status_fn()})
            except Exception as exc:
                _log_error("GET /v1/status failed", exc)
                self._send(503, {
                    "status": "error",
                    "keys": [],
                    "error": {"message": _AI_UNAVAILABLE_MESSAGE, "type": "campus_ai_unavailable"},
                })
            return
        if self.path == "/v1/codex/status":
            from campus_codex_provider import status as codex_status

            self._send(200, {"status": "ok", **codex_status()})
            return
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": "campus-suite", "object": "model"}]})
            return
        self._send(404, {"error": {"message": "Not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/v1/chat/completions", "/v1/coding/run", "/v1/quiz/generate", "/v1/blender/run", "/v1/video/run", "/v1/codex/connect"}:
            self._send(404, {"error": {"message": "Not found"}})
            return
        try:
            if self.path == "/v1/codex/connect":
                from campus_codex_provider import launch_login

                state = launch_login()
                self._send(202 if state == "started" else 200, {
                    "status": state,
                    "launched": state == "started",
                })
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = _redact_request_text(json.loads(self.rfile.read(length)))
            if self.path in {"/v1/blender/run", "/v1/video/run"}:
                prompt = str(body.get("prompt", "")).strip()
                if not prompt:
                    self._send(400, {"error": {"message": "Describe the task first."}})
                    return
                events = []
                if self.path == "/v1/blender/run":
                    from blender_agent import BlenderMasterOrchestrator, BlenderSocket

                    result = BlenderMasterOrchestrator(BlenderSocket(), _get_api()).execute_task(
                        prompt, progress_cb=lambda stage, detail: events.append({"stage": stage, "detail": detail})
                    )
                    self._send(200, {"result": result, "events": events})
                    return

                agent = _get_video_agent()
                response_text = []
                command_id = agent.send_prompt(
                    prompt,
                    on_step=lambda stage, detail, **_meta: events.append({"stage": stage, "detail": detail}),
                    on_response=response_text.append,
                )
                self._send(200, {
                    "content": response_text[-1] if response_text else "Request processed.",
                    "commandId": command_id or None,
                    "bridge": agent.get_bridge_status(),
                    "events": events,
                })
                return
            if self.path == "/v1/coding/run":
                from coding_agent import CodingAgent

                request = str(body.get("request", "")).strip()
                workspace = str(body.get("workspace", "")).strip()
                if not request or not workspace or not Path(workspace).expanduser().is_dir():
                    self._send(400, {"error": {"message": "A request and an existing workspace folder are required."}})
                    return
                events = []
                result = CodingAgent(_get_api(), str(Path(workspace).expanduser().resolve())).run(
                    request, on_step=lambda role, detail="": events.append({"role": role, "detail": detail})
                )
                self._send(200, {"result": result, "events": events})
                return
            if self.path == "/v1/quiz/generate":
                from quiz_generator import QuizGenerator

                topic = str(body.get("topic", "")).strip()
                material = str(body.get("material", "")).strip()
                if not topic and not material:
                    self._send(400, {"error": {"message": "Enter a topic or attach study material."}})
                    return
                if len(material) > 1_000_000:
                    self._send(413, {"error": {"message": "Study material must be smaller than 1 MB."}})
                    return
                result = QuizGenerator(_get_api()).generate(
                    None, topic, str(body.get("difficulty", "Intermediate")),
                    max(1, min(int(body.get("count", 10)), 30)),
                    body.get("types") if isinstance(body.get("types"), list) else ["Multiple Choice"],
                    source_text=material,
                )
                self._send(200, {"result": result})
                return
            messages = body.get("messages")
            agent_id = self.headers.get("x-campus-agent-id", "general")
            conversation = None
            if body.get("model") == "gpt-6-luna" or agent_id not in {"study", "personal-growth"}:
                try:
                    system, user, conversation = _general_chat_messages(messages)
                except ChatRequestValidationError as exc:
                    self._send(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
                    return
            else:
                messages = messages or []
                system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
                user = "\n".join(m.get("content", "") for m in messages if m.get("role") != "system")
            if body.get("model") == "gpt-6-luna":
                response = _get_api().chat_with_role(
                    "coder",
                    system,
                    user,
                    temperature=body.get("temperature", 0.3),
                    max_retries=1,
                    model="gpt-6-luna",
                    conversation=conversation,
                )
                text = _message_text(response)
                completion = {
                    "id": "campus-suite-gpt-6-luna",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "gpt-6-luna",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }],
                }
                if body.get("stream") is True:
                    self._send_sse_completion(completion)
                else:
                    self._send(200, completion)
                return
            if agent_id == "study":
                from google.genai import types

                agent = _get_study_agent()
                history = []
                for message in messages:
                    if message.get("role") not in {"user", "assistant"}:
                        continue
                    history.append(types.Content(
                        role="model" if message["role"] == "assistant" else "user",
                        parts=[types.Part(text=str(message.get("content", "")))],
                    ))
                if history and history[-1].role == "user":
                    question = history.pop().parts[0].text or ""
                else:
                    question = user
                result = agent.chat(
                    str(body.get("materialContext") or system or "No additional study material supplied."),
                    history,
                    question,
                )
                text = result["reply"]
                metadata = {"mode": result.get("mode"), "tool_calls": result.get("tool_calls", [])}
            elif agent_id == "personal-growth":
                from google.genai import types
                from agents.personal_growth_mentor import PersonalGrowthMentor

                conversation_id = str(body.get("conversationId") or "").strip()
                if not conversation_id or len(conversation_id) > 128:
                    self._send(400, {"error": {"message": "A valid conversation ID is required for private goal storage."}})
                    return
                history = [
                    types.Content(
                        role="model" if message.get("role") == "assistant" else "user",
                        parts=[types.Part(text=str(message.get("content", "")))],
                    )
                    for message in messages
                    if message.get("role") in {"user", "assistant"}
                ]
                if history and history[-1].role == "user":
                    question = history.pop().parts[0].text or ""
                else:
                    question = user
                result = PersonalGrowthMentor(_get_api()._km, scope_id=conversation_id).chat(history, question)
                text = result["reply"]
                metadata = {"mode": result.get("mode"), "tool_calls": result.get("tool_calls", [])}
            else:
                role = self.headers.get("x-campus-role", "coder")
                response = _get_api().chat_with_role(
                    role,
                    system,
                    user,
                    temperature=body.get("temperature", 0.3),
                    conversation=conversation,
                )
                text = _message_text(response)
                metadata = {}
            self._send(200, {
                "id": "campus-suite-chat",
                "object": "chat.completion",
                "model": "campus-suite",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                **metadata,
            })
        except GatewayUnavailableError as exc:
            _log_error(f"POST {self.path} — AI core unavailable", exc)
            self._send(503, {"error": {"message": _AI_UNAVAILABLE_MESSAGE, "type": "campus_ai_unavailable"}})
        except CodexPromptError:
            self._send(400, {"error": {"message": "This Codex request is not supported.", "type": "codex_invalid_request"}})
        except ValueError as exc:
            # Keep prompt/provider details out of logs while making it possible
            # to distinguish the gateway's validation class from an upstream
            # provider error when diagnosing a 400 response.
            _log_info(f"POST {self.path} returned 400 ({type(exc).__name__})")
            self._send(400, {"error": {"message": "The chat request is invalid.", "type": "invalid_request_error"}})
        except CodexProviderError as exc:
            _log_error(f"POST {self.path} | Codex unavailable", exc)
            self._send(503, {"error": {"message": _CODEX_UNAVAILABLE_MESSAGE, "type": "codex_unavailable"}})
        except ModuleNotFoundError as exc:
            _log_error(f"POST {self.path} — missing dependency", exc)
            self._send_dependency_error(exc)
        except ImportError as exc:
            _log_error(f"POST {self.path} — dependency failed to load", exc)
            self._send_dependency_error(exc)
        except Exception as exc:
            _log_error(f"POST {self.path} failed", exc)
            self._send(500, {"error": {"message": "Campus Suite AI could not complete the request.", "type": "campus_key_manager_error"}})


if __name__ == "__main__":
    _log_info(f"gateway starting on {HOST}:{PORT}")
    threading.Thread(target=_warm_imports, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
