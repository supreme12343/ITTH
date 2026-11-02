"""
key_manager.py

Manages shared chat access through Codex, Gemini, OmniRoute, and finally the
local Campus Suite model.

OmniRoute (Antigravity) is an OpenAI-compatible gateway running locally that
routes to all connected providers (Gemini, OpenAI, Claude, etc.).
Endpoint: http://localhost:20128/v1

Architecture:
  - Codex CLI         — primary text provider, using the user's ChatGPT login.
  - Gemini            — first configured API fallback.
  - OmniRoute client  — configured provider fallback.
  - Campus Suite AI   — final optional local GGUF fallback.

  - client_for_role(i)  -> Returns an OmniRouteClient wrapper that looks
                           identical to the old Gemini client interface.
                           Falls back to Gemini key i if OmniRoute is down.

  - generate(pool, ...) -> Codex, Gemini, OmniRoute, then local Campus Suite AI;
                           CODEX_ONLY_PROVIDER explicitly disables fallbacks.

Demo Mode
---------
Use MockRoleKeyManager() for offline demos — no real keys or internet needed.
"""

import base64
import itertools
import io
import mimetypes
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List


def _load_root_env() -> None:
    """Load the project .env for desktop tools without overriding process env."""
    dotenv = Path(__file__).resolve().parent / ".env"
    try:
        lines = dotenv.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if not name or os.environ.get(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[name] = value


_load_root_env()

OMNIROUTE_BASE_URL = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
OMNIROUTE_API_KEY  = os.environ.get("OMNIROUTE_API_KEY", "")
OMNIROUTE_MODEL    = os.environ.get("OMNIROUTE_MODEL",    "auto/coding")
GEMINI_MODEL = os.environ.get("CAMPUS_GEMINI_MODEL", "gemini-3.1-flash-lite")
ENABLE_CODEX_PROVIDER = os.environ.get("ENABLE_CODEX_PROVIDER", "true").strip().lower() not in {
    "0", "false", "no", "off"
}
CODEX_ONLY_PROVIDER = os.environ.get("CODEX_ONLY_PROVIDER", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

# Every outbound AI request needs an upper bound. The OpenAI SDK defaults to a
# 600s read timeout, which is exactly how a Campus Suite tool appears to "hang
# forever" when OmniRoute accepts a connection but never answers.
try:
    OMNIROUTE_TIMEOUT_SECONDS = float(os.environ.get("OMNIROUTE_TIMEOUT_SECONDS", "60"))
except (TypeError, ValueError):
    OMNIROUTE_TIMEOUT_SECONDS = 60.0

# OmniRoute is the last connected-provider fallback before the local model.
# Set the flag to false to skip it.
ENABLE_OMNIROUTE = os.environ.get("ENABLE_OMNIROUTE", "true").strip().lower() not in {
    "0", "false", "no", "off"
}


class _SimplePart:
    """Simple part container compatible with both OmniRoute shim and Gemini."""
    def __init__(self, text: str = None):
        self.text = text
    function_call = None


def _make_vision_part(data: bytes, mime_type: str, *, use_genai: bool = True):
    """Create a vision part compatible with the current client (OmniRoute shim or Gemini)."""
    if use_genai and _load_genai():
        try:
            return _genai_types.Part.from_bytes(data=data, mime_type=mime_type)
        except Exception:
            pass
    # Fallback simple object
    class _VisionPart:
        def __init__(self, data, mime_type):
            self.data = data
            self.mime_type = mime_type
            self.text = None
    return _VisionPart(data, mime_type)

# The openai package is imported only when a configured OmniRoute client is
# initialized. It is not needed for Gemini, local tools, or offline demos.
_OpenAI = None
_OPENAI_AVAILABLE = False  # updated once the lazy load below actually runs
_openai_loaded = False
_openai_import_lock = threading.Lock()


def _omniroute_configured() -> bool:
    """True only when OmniRoute could actually be used."""
    return bool(ENABLE_OMNIROUTE and OMNIROUTE_API_KEY)


def _load_openai():
    """Import the openai SDK on first real use and cache the result."""
    global _OpenAI, _OPENAI_AVAILABLE, _openai_loaded
    with _openai_import_lock:
        if _openai_loaded:
            return _OpenAI
        _openai_loaded = True
        try:
            from openai import OpenAI as _ImportedOpenAI
        except ImportError:
            _OpenAI = None
            _OPENAI_AVAILABLE = False
        else:
            _OpenAI = _ImportedOpenAI
            _OPENAI_AVAILABLE = True
        return _OpenAI


def _openai_available() -> bool:
    """Whether the openai SDK can be loaded. Triggers the lazy import."""
    return _load_openai() is not None


class _GenAIUnavailableError(Exception):
    """Placeholder exception used when the optional GenAI SDK is unavailable."""


_GENAI_AVAILABLE = False
_genai_loaded = False
_genai_load_lock = threading.Lock()
_genai = None
_genai_types = None
_GenAIClientError = _GenAIUnavailableError
_GenAIServerError = _GenAIUnavailableError

_TRANSPORT_ERROR_BASES = {
    "CloseError",
    "ConnectionException",
    "ConnectError",
    "ConnectTimeout",
    "LocalProtocolError",
    "NetworkError",
    "PoolTimeout",
    "ProtocolError",
    "ProxyError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "TimeoutException",
    "TransportError",
    "UnsupportedProtocol",
    "WriteError",
    "WriteTimeout",
    "APIConnectionError",
    "APITimeoutError",
}


def _is_transport_error(error: BaseException) -> bool:
    """Recognize socket, httpx, and httpcore transport failures without imports."""
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (OSError, TimeoutError, ConnectionError)):
            return True
        if any(cls.__name__ in _TRANSPORT_ERROR_BASES for cls in type(current).__mro__):
            return True
        for nested in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _transport_failure(error: BaseException, provider: str = "Gemini") -> RuntimeError:
    """Return a credential-safe message for host/provider transport failures."""
    return RuntimeError(
        f"Could not reach the {provider} provider. Check this computer's network or firewall "
        f"settings. ({type(error).__name__})"
    )


def _is_input_validation_error(error: BaseException) -> bool:
    """Recognize terminal provider-side request validation failures safely."""
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ValueError):
            return True
        for name in ("code", "status_code"):
            value = getattr(current, name, None)
            try:
                if int(value) in (400, 422):
                    return True
            except (TypeError, ValueError):
                continue
        for nested in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


class AiInputError(ValueError):
    """A request validation failure that must not fall through providers."""


def _load_genai() -> bool:
    """Import Google's optional SDK only when a Gemini request needs it."""
    global _GENAI_AVAILABLE, _genai_loaded, _genai, _genai_types
    global _GenAIClientError, _GenAIServerError
    with _genai_load_lock:
        if _genai_loaded:
            return _GENAI_AVAILABLE
        _genai_loaded = True
        try:
            from google import genai as genai_module
            from google.genai.errors import ClientError, ServerError
            from google.genai import types as genai_types
        except ImportError:
            _GENAI_AVAILABLE = False
            return False
        _genai = genai_module
        _genai_types = genai_types
        _GenAIClientError = ClientError
        _GenAIServerError = ServerError
        _GENAI_AVAILABLE = True
        return True


def _probe_omniroute(timeout: float = 2.0) -> bool:
    """Check if OmniRoute is reachable at the configured base URL."""
    # Checked before touching the SDK so an unconfigured OmniRoute never pays
    # the openai import cost.
    if not _omniroute_configured():
        return False
    if not _openai_available():
        return False
    try:
        import urllib.request
        health_url = OMNIROUTE_BASE_URL.rstrip("/").replace("/v1", "") + "/health"
        req = urllib.request.Request(health_url, method="GET")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        try:
            import urllib.request
            models_url = OMNIROUTE_BASE_URL.rstrip("/") + "/models"
            req = urllib.request.Request(models_url, headers={"Authorization": f"Bearer {OMNIROUTE_API_KEY}"})
            urllib.request.urlopen(req, timeout=timeout)
            return True
        except Exception:
            return False


class _OmniRouteGenaiShim:
    """
    Wraps the OpenAI SDK client so it presents the same interface that
    video_editing_agent.py and coding_agent.py expect from a google.genai client:
        client.models.generate_content(model=..., contents=..., config=...)
    """

    def __init__(self, openai_client, model: str = OMNIROUTE_MODEL):
        self._client = openai_client
        self._model = model
        self.models = self

    def generate_content(self, model=None, contents=None, config=None, **kwargs):
        model = model or self._model
        messages = self._convert_contents(contents or [])

        system = None
        if config and hasattr(config, "system_instruction") and config.system_instruction:
            system = config.system_instruction

        if system:
            messages = [{"role": "system", "content": system}] + messages

        temperature = 0.3
        top_p = 0.9
        max_tokens = 4096
        if config:
            if hasattr(config, "temperature") and config.temperature is not None:
                temperature = config.temperature
            if hasattr(config, "top_p") and config.top_p is not None:
                top_p = config.top_p
            output_tokens = getattr(config, "max_output_tokens", None)
            if output_tokens is None:
                output_tokens = getattr(config, "max_tokens", None)
            if output_tokens is not None:
                max_tokens = output_tokens

            # Function calling and schema-constrained responses are kept on the
            # native Gemini client. OmniRoute's current compatibility surface
            # supports text and OpenAI-compatible image parts only.
            for option in ("tools", "tool_config", "response_schema", "response_mime_type"):
                value = getattr(config, option, None)
                if value is not None and value is not False and value != [] and value != "":
                    raise ValueError("OmniRoute compatibility does not support structured output requests.")

        safe_model = model if "/" in model or "-" in model else self._model

        resp = self._client.chat.completions.create(
            model=safe_model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )

        return _OmniRouteResponse(resp.choices[0].message.content)

    def _convert_contents(self, contents) -> list:
        messages = []
        for c in contents:
            # Handle both object attributes and dict keys
            if isinstance(c, dict):
                role = c.get("role", "user")
                parts = c.get("parts", [])
            else:
                role = getattr(c, "role", "user")
                parts = getattr(c, "parts", [])
            
            if role == "model":
                role = "assistant"
            if role not in {"system", "developer", "user", "assistant"}:
                raise ValueError("OmniRoute compatibility received an unsupported message role.")

            converted_parts = []
            for p in parts:
                text = p.get("text") if isinstance(p, dict) else getattr(p, "text", None)
                if text:
                    converted_parts.append({"type": "text", "text": str(text)})
                    continue
                image_url = self._image_url(p)
                if image_url:
                    converted_parts.append({"type": "image_url", "image_url": {"url": image_url}})
                    continue
                raise ValueError("OmniRoute compatibility received an unsupported content part.")

            if converted_parts:
                content = "\n".join(part["text"] for part in converted_parts) if all(
                    part["type"] == "text" for part in converted_parts
                ) else converted_parts
                messages.append({"role": role, "content": content})
        if not any(message["role"] == "user" for message in messages):
            raise ValueError("OmniRoute compatibility requires a user message.")
        return messages

    @staticmethod
    def _image_url(part) -> str | None:
        """Convert a supported native image part to an OpenAI data URL."""
        inline = None
        if isinstance(part, dict):
            inline = part.get("inline_data") or part.get("inlineData")
            if isinstance(part.get("image_url"), dict):
                return part["image_url"].get("url")
        else:
            inline = getattr(part, "inline_data", None)

        if inline is not None:
            if isinstance(inline, dict):
                data = inline.get("data")
                mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
            else:
                data = getattr(inline, "data", None)
                mime = getattr(inline, "mime_type", None) or getattr(inline, "mimeType", None) or "image/png"
            if isinstance(data, bytes):
                encoded = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{encoded}"
            if isinstance(data, str) and data.startswith("data:"):
                return data
            return None

        # Google GenAI accepts PIL images directly in contents. Encode them
        # locally so the OpenAI-compatible endpoint receives equivalent input.
        save = getattr(part, "save", None)
        if callable(save):
            buffer = io.BytesIO()
            try:
                image_format = getattr(part, "format", None) or "PNG"
                save(buffer, format=image_format)
            except Exception:
                return None
            mime = mimetypes.types_map.get(f".{image_format.lower()}", "image/png")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        return None


class _OmniRoutePart:
    def __init__(self, text: str):
        self.text = text
    function_call = None


class _OmniRouteContent:
    def __init__(self, text: str):
        self.role = "model"
        self._text = text

    @property
    def parts(self):
        return [_OmniRoutePart(self._text)]


class _OmniRouteCandidate:
    def __init__(self, text: str):
        self.content = _OmniRouteContent(text)


class _OmniRouteResponse:
    """Mimics google.genai response so existing agent code works unchanged."""
    def __init__(self, text: str):
        self._text = text or ""

    @property
    def candidates(self):
        return [_OmniRouteCandidate(self._text)]


@dataclass
class KeyState:
    key: str
    label: str
    cooldown_until: float = 0.0
    failure_count: int = 0

    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until


class _RotatingGeminiClient:
    """Small google-genai-compatible client that fails over between keys."""

    def __init__(self, manager, pool_name: str):
        self._manager = manager
        self._pool_name = pool_name
        self.models = self

    def generate_content(self, **kwargs):
        candidates = self._manager._pool_states(self._pool_name)
        last_error = None
        attempted = set()
        for _ in range(len(candidates)):
            state = self._manager._next_available(self._pool_name)
            if state.key in attempted:
                continue
            attempted.add(state.key)
            try:
                response = self._manager._genai_client_for(state).models.generate_content(**kwargs)
                with self._manager._lock:
                    state.failure_count = 0
                return response
            except Exception as exc:
                last_error = exc
                # A transport failure affects every key equally, so do not
                # wait through the rotation pool for a host/network issue.
                if _is_transport_error(exc):
                    raise _transport_failure(exc) from exc
                with self._manager._lock:
                    state.failure_count += 1
                    code = getattr(exc, 'code', None)
                    state.cooldown_until = time.time() + (
                        self._manager.cooldown_seconds if code in (403, 429) else 10
                    )
                print(f"[KeyManager] Rotating away from {state.label} ({type(exc).__name__})")
        raise RuntimeError("All Gemini keys failed.") from last_error


class _RoleRoutingClient:
    """Google-genai-shaped bridge into the shared, capability-aware router."""

    def __init__(self, manager, pool_name: str):
        self._manager = manager
        self._pool_name = pool_name
        self.models = self
        self.last_label = "campus-ai-router"

    def generate_content(self, **kwargs):
        response, label = self._manager.generate(self._pool_name, **kwargs)
        self.last_label = label
        return response


class RoleKeyManager:
    SEARCH_POOL = "search"
    TOOL_POOL   = "tools"
    ALL_POOL    = "all"

    def __init__(self, keys: Optional[list] = None, cooldown_seconds: int = 60):
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._omniroute_client: Optional[_OmniRouteGenaiShim] = None
        self._omniroute_available = False

        self._try_init_omniroute()

        gemini_keys = [] if CODEX_ONLY_PROVIDER else self._load_gemini_keys(keys)
        self._states = [KeyState(key=k, label=f"gemini-key-{i + 1}") for i, k in enumerate(gemini_keys)]
        self._genai_clients = {}
        search_states = self._states[:2] or self._states
        tool_states = self._states[2:5] or self._states
        self._pools = {
            self.SEARCH_POOL: itertools.cycle(search_states),
            self.TOOL_POOL:   itertools.cycle(tool_states),
            self.ALL_POOL:    itertools.cycle(self._states),
        }

    def _try_init_omniroute(self):
        if CODEX_ONLY_PROVIDER:
            return
        if not ENABLE_OMNIROUTE:
            print("[KeyManager] OmniRoute disabled via ENABLE_OMNIROUTE flag")
            return
        # The key check comes before the SDK check so an unconfigured OmniRoute
        # never triggers the expensive lazy `import openai`.
        if not OMNIROUTE_API_KEY:
            print("[KeyManager] OMNIROUTE_API_KEY is not configured")
            return
        openai_cls = _load_openai()
        if openai_cls is None:
            print("[KeyManager] openai package not installed — OmniRoute disabled")
            return
        try:
            try:
                raw = openai_cls(
                    base_url=OMNIROUTE_BASE_URL,
                    api_key=OMNIROUTE_API_KEY,
                    timeout=OMNIROUTE_TIMEOUT_SECONDS,
                )
            except TypeError:
                # Very old openai SDKs do not accept a client-level timeout.
                raw = openai_cls(base_url=OMNIROUTE_BASE_URL, api_key=OMNIROUTE_API_KEY)
            self._omniroute_client = _OmniRouteGenaiShim(raw)
            self._omniroute_available = True
            print(f"[KeyManager] OmniRoute connected at {OMNIROUTE_BASE_URL} (model: {OMNIROUTE_MODEL})")
        except Exception as e:
            print(f"[KeyManager] OmniRoute initialization failed ({type(e).__name__})")

    def _load_gemini_keys(self, keys: Optional[list]) -> list:
        if keys is None:
            numbered_keys = [
                os.environ.get(f"GEMINI_API_KEY_{i + 1}") for i in range(5)
            ]
            primary_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            keys = []
            if numbered_keys[0]:
                keys.append(numbered_keys[0])
            elif primary_key:
                keys.append(primary_key)
            keys.extend(key for key in numbered_keys[1:] if key)
            return [str(key).strip() for key in keys[:5] if str(key).strip()]

        for i in range(min(5, len(keys))):
            env_key = os.environ.get(f"GEMINI_API_KEY_{i + 1}")
            if env_key:
                keys[i] = env_key
        return [str(key).strip() for key in keys[:5] if str(key).strip()]

    def _codex_is_configured(self) -> bool:
        """Skip Codex unless its CLI is present and the user's login is active."""
        if not ENABLE_CODEX_PROVIDER:
            return False
        checked_at = getattr(self, "_codex_status_checked_at", 0.0)
        if time.monotonic() - checked_at < 15:
            return bool(getattr(self, "_codex_ready", False))
        try:
            from campus_codex_provider import status as codex_status

            state = codex_status()
            ready = bool(state.get("cli_found") and state.get("logged_in"))
        except Exception:
            ready = False
        self._codex_ready = ready
        self._codex_status_checked_at = time.monotonic()
        return ready

    def _genai_client_for(self, state: KeyState):
        if not _load_genai():
            raise RuntimeError("google-genai package not installed and OmniRoute is unavailable")
        if state.key not in self._genai_clients:
            self._genai_clients[state.key] = _genai.Client(
                api_key=state.key,
                http_options=_genai_types.HttpOptions(timeout=15000),
            )
        return self._genai_clients[state.key]

    def client_for_role(self, role_index: int):
        """
        Return a Google-genai-compatible client that enters the shared router.

        The proxy preserves native Gemini request/response objects for tool and
        image requests while the router skips providers that cannot handle them.
        """
        if CODEX_ONLY_PROVIDER:
            raise RuntimeError("Direct provider clients are disabled; use the GPT-6 Luna chat route.")
        pool_name = self.TOOL_POOL if role_index >= 2 else self.SEARCH_POOL
        return _RoleRoutingClient(self, pool_name), "campus-ai-router"

    def _pool_states(self, pool_name: str) -> list:
        if pool_name == self.SEARCH_POOL:
            return self._states[0:2] or self._states
        if pool_name == self.TOOL_POOL:
            return self._states[2:5] or self._states
        return self._states

    def _next_available(self, pool_name: str) -> KeyState:
        pool = self._pools[pool_name]
        candidates = self._pool_states(pool_name)
        with self._lock:
            for _ in range(len(candidates) * 2):
                state = next(pool)
                if state.is_available():
                    return state
            return min(candidates, key=lambda s: s.cooldown_until)

    def generate(self, pool_name: str, **kwargs):
        """
        Try configured providers in the shared Campus Suite order:
        Codex, Gemini, OmniRoute, then the local Campus Suite AI model.

        Unconfigured providers are skipped. Provider diagnostics intentionally
        omit exception text because SDK errors may contain request or auth data.
        Returns (response, label) — response matches google.genai shape.
        """
        request_messages = self._codex_messages(kwargs)
        native_capability_required = self._native_capability_required(kwargs)
        text_only_request = bool(request_messages) and not native_capability_required
        if CODEX_ONLY_PROVIDER and (not ENABLE_CODEX_PROVIDER or request_messages is None):
            raise RuntimeError("This service is configured for text-only GPT-6 Luna requests.")

        if text_only_request and self._codex_is_configured():
            from campus_codex_provider import CodexPromptError, chat_completion

            try:
                # The local Codex CLI uses the user's existing ChatGPT login.
                # campus_codex_provider fixes the model to gpt-6-luna and
                # reasoning effort to high, without exposing its auth cache.
                completion = chat_completion(request_messages)
                text = completion["choices"][0]["message"]["content"]
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError("Codex returned an empty response.")
                return _OmniRouteResponse(text), "codex-gpt-6-luna-high"
            except CodexPromptError:
                # Invalid requests are terminal and must not be hidden by a
                # provider fallback.
                raise
            except Exception as exc:
                if CODEX_ONLY_PROVIDER:
                    raise RuntimeError(
                        "GPT-6 Luna through Codex could not complete the request; no model fallback is enabled."
                    ) from exc
                # Avoid logging prompts or provider error bodies.
                print(f"[KeyManager] Codex request failed; trying fallbacks ({type(exc).__name__})")

        if CODEX_ONLY_PROVIDER:
            raise RuntimeError(
                "GPT-6 Luna through Codex is not configured; no model fallback is enabled."
            )

        if self._states and _load_genai():
            try:
                response, label = self._generate_gemini(pool_name, kwargs, request_messages)
                if text_only_request:
                    # Text-only agents cannot use a function-call-only or empty
                    # Gemini response. Treat it as a provider failure so the
                    # next configured backend can answer instead of returning
                    # an empty response as a successful generation.
                    _extract_response_text(response, context=f"Gemini ({label})")
                return response, label
            except Exception as e:
                if _is_input_validation_error(e):
                    print(
                        f"[KeyManager] Gemini rejected the request; trying fallback "
                        f"({type(e).__name__})"
                    )
                else:
                    print(f"[KeyManager] Gemini request failed; trying fallback ({type(e).__name__})")

        if self._omniroute_supports(kwargs) and self._omniroute_available and self._omniroute_client:
            try:
                omni_kwargs = dict(kwargs)
                omni_kwargs["model"] = OMNIROUTE_MODEL
                response = self._omniroute_client.models.generate_content(**omni_kwargs)
                if text_only_request:
                    _extract_response_text(response, context="OmniRoute")
                return response, "omniroute"
            except Exception as exc:
                if _is_input_validation_error(exc):
                    print(f"[KeyManager] OmniRoute rejected the request; trying fallback ({type(exc).__name__})")
                else:
                    print(f"[KeyManager] OmniRoute request failed ({type(exc).__name__})")

        if text_only_request:
            try:
                import campus_local_provider

                if campus_local_provider.is_configured():
                    config = kwargs.get("config")
                    max_tokens = getattr(config, "max_output_tokens", None)
                    if max_tokens is None:
                        max_tokens = getattr(config, "max_tokens", None)
                    response = campus_local_provider.chat_completion(
                        request_messages,
                        max_tokens=max_tokens or 512,
                        temperature=getattr(config, "temperature", 0.3),
                        top_p=getattr(config, "top_p", 0.9),
                    )
                    text = response["choices"][0]["message"]["content"]
                    if not isinstance(text, str) or not text.strip():
                        raise RuntimeError("The local Campus Suite model returned an empty response.")
                    return _OmniRouteResponse(text), "campus-suite-ai-local"
            except ValueError as exc:
                raise AiInputError("The AI request is invalid.") from exc
            except Exception as exc:
                if _is_input_validation_error(exc):
                    raise AiInputError("The local Campus Suite model rejected the AI request as invalid.") from exc
                print(f"[KeyManager] Local Campus Suite AI failed ({type(exc).__name__})")

        raise RuntimeError(
            "No configured AI provider could complete this request. Check provider setup or retry."
        )

    @staticmethod
    def _native_capability_required(kwargs: dict) -> bool:
        """True for function tools, structured outputs, or any non-text part."""
        config = kwargs.get("config")
        if kwargs.get("tools"):
            return True
        if config is not None:
            for name in ("tools", "tool_config", "response_schema", "response_mime_type"):
                value = getattr(config, name, None)
                if value is not None and value is not False and value != [] and value != "":
                    return True

        for content in kwargs.get("contents") or []:
            parts = content.get("parts", []) if isinstance(content, dict) else getattr(content, "parts", [])
            for part in parts:
                text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if text is None:
                    # PIL images, inline/file data, function calls/responses,
                    # and unknown native part types require a capable SDK.
                    return True
        return False

    @staticmethod
    def _omniroute_supports(kwargs: dict) -> bool:
        """Report whether the OpenAI-compatible shim can preserve this request."""
        config = kwargs.get("config")
        if kwargs.get("tools"):
            return False
        if config is not None:
            for name in ("tools", "tool_config", "response_schema", "response_mime_type"):
                value = getattr(config, name, None)
                if value is not None and value is not False and value != [] and value != "":
                    return False

        has_user = False
        for content in kwargs.get("contents") or []:
            if isinstance(content, dict):
                role = content.get("role", "user")
                parts = content.get("parts", [])
            else:
                role = getattr(content, "role", "user")
                parts = getattr(content, "parts", [])
            if role == "user":
                has_user = True
            if role == "model":
                role = "assistant"
            if role not in {"system", "developer", "user", "assistant"}:
                return False
            for part in parts:
                text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if not text and _OmniRouteGenaiShim._image_url(part) is None:
                    return False
        return has_user

    def _generate_gemini(self, pool_name: str, kwargs: dict, request_messages: list | None):
        """Generate through configured Gemini keys before OmniRoute/local fallback."""
        request = dict(kwargs)
        if request_messages or not request.get("model") or request.get("model") in {OMNIROUTE_MODEL, "gpt-6-luna"}:
            request["model"] = GEMINI_MODEL
        config = request.get("config")
        if config is not None:
            config_type = getattr(_genai_types, "GenerateContentConfig", None)
            try:
                already_genai_config = config_type is not None and isinstance(config, config_type)
            except TypeError:
                already_genai_config = False
            if not already_genai_config:
                values = {
                    "system_instruction": getattr(config, "system_instruction", None),
                    "temperature": getattr(config, "temperature", None),
                    "top_p": getattr(config, "top_p", None),
                }
                for native_option in ("tools", "tool_config", "response_schema", "response_mime_type"):
                    option_value = getattr(config, native_option, None)
                    if option_value is not None:
                        values[native_option] = option_value
                output_tokens = getattr(config, "max_output_tokens", None)
                if output_tokens is None:
                    output_tokens = getattr(config, "max_tokens", None)
                if output_tokens is not None:
                    values["max_output_tokens"] = output_tokens
                request["config"] = _genai_types.GenerateContentConfig(**values)

        candidates = self._pool_states(pool_name)
        last_error = None
        for _ in range(len(candidates)):
            state = self._next_available(pool_name)
            try:
                client = self._genai_client_for(state)
                response = client.models.generate_content(**request)
                with self._lock:
                    state.failure_count = 0
                return response, state.label
            except Exception as exc:
                if _is_input_validation_error(exc):
                    raise
                if _is_transport_error(exc):
                    last_error = _transport_failure(exc)
                    break
                last_error = exc
                code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                with self._lock:
                    state.failure_count += 1
                    if code in (403, 429):
                        state.cooldown_until = time.time() + self.cooldown_seconds
                    elif code is not None:
                        state.cooldown_until = time.time() + 10
        raise RuntimeError("Gemini request failed.") from last_error

    @staticmethod
    def _codex_messages(kwargs: dict) -> list | None:
        """Convert a text-only GenerateContent request to Codex chat messages."""
        messages = []
        config = kwargs.get("config")
        system = getattr(config, "system_instruction", None) if config is not None else None
        if system:
            messages.append({"role": "system", "content": str(system)})

        for content in kwargs.get("contents") or []:
            if isinstance(content, dict):
                role = content.get("role", "user")
                parts = content.get("parts", [])
            else:
                role = getattr(content, "role", "user")
                parts = getattr(content, "parts", [])
            role = "assistant" if role == "model" else role
            if role not in {"user", "assistant", "system", "developer"}:
                continue

            text_parts = []
            for part in parts:
                text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if text:
                    text_parts.append(str(text))
                elif part is not None:
                    # Codex chat is text-only; do not silently discard images,
                    # function parts, or unknown structured content.
                    return None
            if text_parts:
                messages.append({"role": role, "content": "\n".join(text_parts)})
        return messages if any(message["role"] == "user" for message in messages) else None

    def _circuit_breaker_error(self, pool_name: str, reason: str):
        class _CircuitResponse:
            text = f"All AI keys are currently rate-limited or failing ({reason}). Try again in a moment."
        return _CircuitResponse(), "circuit-breaker"

    def get_key(self, label: str) -> str:
        """Compatibility for quiz_generator.py — returns first Gemini key."""
        return self._states[0].key if self._states else ""

    def status(self) -> list:
        result = []
        if ENABLE_CODEX_PROVIDER:
            try:
                from campus_codex_provider import MODEL_ID, REASONING_EFFORT, status as codex_status

                codex = codex_status()
                result.append({
                    "label": "codex",
                    "available": codex.get("cli_found", False) and codex.get("logged_in", False),
                    "cli_found": codex.get("cli_found", False),
                    "logged_in": codex.get("logged_in", False),
                    "model": MODEL_ID,
                    "reasoning_effort": REASONING_EFFORT,
                    "mode": "primary",
                })
            except Exception:
                result.append({
                    "label": "codex",
                    "available": False,
                    "mode": "primary",
                })
        if CODEX_ONLY_PROVIDER:
            return result
        for s in self._states:
            result.append({
                "label": s.label,
                "available": s.is_available(),
                "cooldown_seconds_left": max(0, round(s.cooldown_until - time.time())),
                "failure_count": s.failure_count,
                "mode": "fallback",
            })
        if self._omniroute_available:
            result.append({
                "label": "omniroute",
                "available": True,
                "cooldown_seconds_left": 0,
                "failure_count": 0,
                "mode": "fallback",
            })
        try:
            import campus_local_provider

            result.append({
                "label": "campus-suite-ai-local",
                "available": campus_local_provider.is_configured(),
                "mode": "fallback",
            })
        except Exception:
            result.append({"label": "campus-suite-ai-local", "available": False, "mode": "fallback"})
        return result


# ---------------------------------------------------------------------------
# AE PATH AUTO-DETECTION UTILITY
# ---------------------------------------------------------------------------

def find_ae_scriptui_panels_dir() -> Optional[str]:
    """
    Auto-detect the After Effects ScriptUI Panels folder by scanning
    known install locations and drive letters. Returns the first found path.
    """
    import string

    ae_versions = [
        "2024", "2023", "2022", "2021", "2020", "2019", "2018",
        "CC 2024", "CC 2023", "CC 2022", "CC 2021", "CC 2020",
    ]
    sub = r"Adobe After Effects {ver}\Support Files\Scripts\ScriptUI Panels"

    drives = []
    for letter in string.ascii_uppercase:
        d = f"{letter}:\\"
        if os.path.exists(d):
            drives.append(d)

    base_dirs = [
        r"Program Files\Adobe",
        r"Adobe",
        r"",
    ]

    for drive in drives:
        for base in base_dirs:
            for ver in ae_versions:
                candidate = os.path.join(drive, base, sub.format(ver=ver))
                if os.path.isdir(candidate):
                    return candidate

    env_ae = os.environ.get("AE_SCRIPTUI_PANELS_DIR")
    if env_ae and os.path.isdir(env_ae):
        return env_ae

    known = [
        r"H:\Adobe After Effects 2020\Support Files\Scripts\ScriptUI Panels",
        r"I:\Adobe After Effects 2020\Support Files\Scripts\ScriptUI Panels",
        r"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\Scripts\ScriptUI Panels",
        r"C:\Program Files\Adobe\Adobe After Effects 2023\Support Files\Scripts\ScriptUI Panels",
        r"C:\Program Files\Adobe\Adobe After Effects 2022\Support Files\Scripts\ScriptUI Panels",
        r"C:\Program Files\Adobe\Adobe After Effects 2021\Support Files\Scripts\ScriptUI Panels",
        r"C:\Program Files\Adobe\Adobe After Effects 2020\Support Files\Scripts\ScriptUI Panels",
    ]
    for p in known:
        if os.path.isdir(p):
            return p

    return None


# ---------------------------------------------------------------------------
# DEMO / MOCK KEY MANAGER
# ---------------------------------------------------------------------------

class MockRoleKeyManager:
    """
    Drop-in replacement for RoleKeyManager that works with no API keys and
    no internet. Every call returns a realistic canned response so the full
    UI pipeline (Study Agent, Coding Agent, Quiz Generator, Video Editor)
    runs end-to-end for live demos.
    """

    SEARCH_POOL = "search"
    TOOL_POOL   = "tools"
    ALL_POOL    = "all"

    _CANNED = {
        "quiz": (
            "**📝 DEMO QUIZ — Python Basics**\n\n"
            "1. What does `len([1, 2, 3])` return?\n"
            "   A) 1   B) 2   **C) 3**   D) 4\n\n"
            "2. Which keyword is used to define a function in Python?\n"
            "   A) func   **B) def**   C) define   D) lambda\n\n"
            "*(🔵 DEMO MODE — connect OmniRoute or Gemini keys for live AI generation)*"
        ),
        "flashcards": (
            "**🃃 DEMO FLASHCARDS**\n\n"
            "Q: What is a variable?\nA: A named storage location that holds a value.\n\n"
            "Q: What is a loop?\nA: A control structure that repeats a block of code.\n\n"
            "*(🔵 DEMO MODE — connect OmniRoute or Gemini keys for live AI generation)*"
        ),
        "search": (
            "Great question! Based on verified sources:\n\n"
            "The topic you asked about is a foundational concept in computer science.\n\n"
            "*(🔵 DEMO MODE — connect OmniRoute or Gemini keys for live search-grounded answers)*"
        ),
        "coding": (
            "## Coding Agent Report (DEMO)\n\n"
            "**Plan:** Analysed the request and identified 3 implementation steps.\n\n"
            "*(🔵 DEMO MODE — connect OmniRoute or Gemini keys for live AI coding)*"
        ),
        "default": (
            "This is a demo response. The AI Agents system is running in **Demo Mode**.\n\n"
            "In live mode, this would call OmniRoute (Antigravity) with smart provider routing.\n\n"
            "*(🔵 DEMO MODE — connect OmniRoute or Gemini keys for live AI responses)*"
        ),
    }

    class _FakePart:
        def __init__(self, text: str):
            self.text = text
        function_call = None

    class _FakeContent:
        def __init__(self, text: str):
            self.role = "model"
            self._text = text

        @property
        def parts(self):
            return [MockRoleKeyManager._FakePart(self._text)]

    class _FakeCandidate:
        def __init__(self, text: str):
            self.content = MockRoleKeyManager._FakeContent(text)

    class _FakeResponse:
        def __init__(self, text: str):
            self._text = text

        @property
        def candidates(self):
            return [MockRoleKeyManager._FakeCandidate(self._text)]

    def __init__(self):
        self._labels = [f"demo-key-{i}" for i in range(1, 6)]
        self._label_cycle = itertools.cycle(self._labels)
        self.models = self  # For compatibility with AiApi.chat()

    def _pick_canned(self, **kwargs) -> str:
        contents = kwargs.get("contents", [])
        text = ""
        for c in contents:
            if hasattr(c, "parts"):
                for p in c.parts:
                    if hasattr(p, "text") and p.text:
                        text += p.text.lower()
        if "quiz" in text or "question" in text:
            return self._CANNED["quiz"]
        if "flashcard" in text:
            return self._CANNED["flashcards"]
        if "code" in text or "build" in text or "write" in text:
            return self._CANNED["coding"]
        return self._CANNED["default"]

    def client_for_role(self, role_index: int):
        return self, self._labels[role_index % len(self._labels)]

    def generate(self, pool_name: str, **kwargs):
        label = next(self._label_cycle)
        time.sleep(0.6)
        canned = self._pick_canned(**kwargs)
        return self._FakeResponse(canned), label

    def generate_content(self, model=None, contents=None, config=None, **kwargs):
        """Compatibility with AiApi.chat() which calls client.models.generate_content()"""
        system = getattr(config, "system_instruction", "") if config else ""
        user_text = ""
        if contents:
            for c in contents:
                if hasattr(c, "parts"):
                    for p in c.parts:
                        if hasattr(p, "text") and p.text:
                            user_text += p.text + "\n"
                elif isinstance(c, dict) and c.get("parts"):
                    for p in c["parts"]:
                        if isinstance(p, dict):
                            if p.get("text"):
                                user_text += p["text"] + "\n"
                        elif hasattr(p, "text") and p.text:
                            user_text += p.text + "\n"
        combined = (system + "\n" + user_text).strip()
        # Create a simple object structure that _pick_canned can parse
        class _Content:
            def __init__(self, text):
                self.parts = [type('Part', (), {'text': text})()]
        canned = self._pick_canned(contents=[_Content(combined)])
        return self._FakeResponse(canned)

    def get_key(self, label: str) -> str:
        return "demo-key"

    def status(self) -> list:
        return [
            {"label": lbl, "available": True, "cooldown_seconds_left": 0, "failure_count": 0, "mode": "demo"}
            for lbl in self._labels
        ]


# ---------------------------------------------------------------------------
# UNIFIED AI API INTERFACE
# ---------------------------------------------------------------------------
# All agents should use this instead of importing RoleKeyManager directly.
# Usage:
#     from key_manager import AiApi
#     api = AiApi()
#     response = api.chat("You are a helpful assistant", "Hello!")
#     response = api.chat_with_role("coder", "Write a Python function", "Make it async")
# ---------------------------------------------------------------------------


class _SimpleMessage:
    """Simple message container for chat interface."""
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


class _SimpleChoice:
    def __init__(self, message: _SimpleMessage):
        self.message = message


class _SimpleResponse:
    """Standardized response object matching OpenAI chat.completions format."""
    def __init__(self, text: str, model: str = "omniroute", role: str = "assistant"):
        self.choices = [_SimpleChoice(_SimpleMessage(role, text))]
        self.model = model
        self.usage = None


class AiResponseError(RuntimeError):
    """Raised when a provider returns no usable text.

    Subclasses RuntimeError so existing ``except RuntimeError`` handlers across
    Campus Suite keep working unchanged.
    """


def _safe_direct_text(response) -> Optional[str]:
    """Read ``response.text`` without letting the SDK's own accessor explode."""
    try:
        value = getattr(response, "text", None)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _response_diagnostics(response) -> str:
    """Collect the provider's own non-sensitive signals about an empty reply."""
    details = []
    try:
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None) if feedback is not None else None
        if block_reason:
            details.append(f"block_reason={block_reason}")
        candidates = getattr(response, "candidates", None) or []
        for candidate in list(candidates)[:1]:
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason:
                details.append(f"finish_reason={finish_reason}")
            blocked = [
                str(getattr(rating, "category", "unknown"))
                for rating in (getattr(candidate, "safety_ratings", None) or [])
                if getattr(rating, "blocked", False)
            ]
            if blocked:
                details.append("blocked_categories=" + ",".join(blocked))
    except Exception:
        pass
    return "; ".join(details) if details else "no provider diagnostics available"


def _extract_response_text(response, context: str = "AI request") -> str:
    """Safely pull the text out of a google.genai-shaped response.

    Providers legitimately return zero candidates (safety block, quota refusal)
    or a candidate whose content/parts are None (truncation). Indexing
    ``response.candidates[0].content.parts`` blindly raises IndexError or
    AttributeError, which reaches the user as an unexplained crash in every
    Campus Suite tool, so each failure mode is converted into a catchable
    AiResponseError that names the likely cause.
    """
    if response is None:
        raise AiResponseError(f"{context} failed: the provider returned no response object.")

    candidates = getattr(response, "candidates", None)
    if not candidates:
        direct = _safe_direct_text(response)
        if direct and direct.strip():
            return direct
        raise AiResponseError(
            f"{context} returned an empty or blocked response: the provider sent zero "
            f"candidates (usually a safety block, a quota refusal, or a truncated "
            f"response). {_response_diagnostics(response)}"
        )

    candidate = list(candidates)[0]
    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        direct = _safe_direct_text(response)
        if direct and direct.strip():
            return direct
        raise AiResponseError(
            f"{context} returned a candidate with no content parts (usually a safety "
            f"block or a truncated response). {_response_diagnostics(response)}"
        )

    text = "".join(getattr(part, "text", None) or "" for part in parts)
    if not text.strip():
        direct = _safe_direct_text(response)
        if direct and direct.strip():
            return direct
        raise AiResponseError(
            f"{context} returned empty text. {_response_diagnostics(response)}"
        )
    return text


class AiApi:
    """
    Unified AI API interface for all agents.
    
    Abstracts away:
    - Codex (primary), OmniRoute (fallback), then Gemini
    - Role-based key management (planner, designer, coder, reviewer, reporter)
    - Rate limiting, retries, fallback logic
    
    Usage:
        from key_manager import AiApi
        
        api = AiApi()
        
        # Simple chat
        resp = api.chat("You are a helpful assistant", "Hello!")
        print(resp.choices[0].message.content)
        
        # Role-based chat (uses dedicated role keys)
        resp = api.chat_with_role("coder", "System prompt", "User prompt")
        resp = api.chat_with_role("planner", "System prompt", "User prompt")
        
        # Vision/multimodal
        resp = api.chat_vision("System prompt", "Describe this image", image_bytes)
        
        # Structured output
        resp = api.chat_json("System prompt", "Extract JSON", schema_dict)
    """

    _ROLE_MAP = {
        "planner": 0,
        "designer": 1,
        "coder": 2,
        "reviewer": 3,
        "reporter": 4,
        "analyst": 5,
    }

    def __init__(self, use_mock: bool = False, key_manager=None):
        """
        Initialize the API client.
        
        Args:
            use_mock: If True, use MockRoleKeyManager for offline demos.
        """
        if key_manager is not None:
            self._km = key_manager
        elif use_mock:
            from key_manager import MockRoleKeyManager
            self._km = MockRoleKeyManager()
        else:
            from key_manager import RoleKeyManager
            self._km = RoleKeyManager()
        
        self._vision_supported = True  # Will be set to False if vision fails

    def _get_client_for_role(self, role: str):
        """Get client for a specific role."""
        role_idx = self._ROLE_MAP.get(role.lower(), 0)
        return self._km.client_for_role(role_idx)

    def _model_for_provider(self) -> str:
        """Choose a valid model for the active KeyManager backend."""
        if getattr(self._km, "_omniroute_available", False):
            return OMNIROUTE_MODEL
        return GEMINI_MODEL

    def _uses_gemini_sdk(self) -> bool:
        """Whether this request needs Google's SDK instead of a shim or demo."""
        return (
            not getattr(self._km, "_omniroute_available", False)
            and not isinstance(self._km, MockRoleKeyManager)
        )

    def _build_contents(
        self,
        system: str,
        user: str,
        vision_parts: Optional[List] = None,
        conversation: Optional[List[dict]] = None,
    ):
        """Build user contents; provider adapters receive system text via config."""
        if vision_parts is None:
            vision_parts = []

        if conversation is not None:
            contents = []
            for message in conversation:
                role = "model" if message["role"] == "assistant" else "user"
                contents.append({
                    "role": role,
                    "parts": [_SimplePart(text=message["content"])],
                })
            return contents
        
        parts = []
        for vp in vision_parts:
            parts.append(vp)
        parts.append(_SimplePart(text=user))

        # Gemini only accepts user/model roles in contents. System instructions
        # are supplied separately through GenerateContentConfig; the OmniRoute
        # adapter reads the same config and converts it to a system message.
        return [{"role": "user", "parts": parts}]

    def chat(self, system: str, user: str, model: str = None, temperature: float = 0.3, 
             top_p: float = 0.9, max_retries: int = 3) -> _SimpleResponse:
        """
        Simple chat completion - uses the default role (coder) or pool.
        
        Args:
            system: System prompt
            user: User prompt
            model: Optional model override; Gemini mode defaults to Campus Suite's configured model.
            temperature: Sampling temperature
            top_p: Top-p sampling
            max_retries: Max retry attempts
            max_tokens: Optional output-token cap for bounded requests.
            
        Returns:
            _SimpleResponse with .choices[0].message.content
        """
        if isinstance(self._km, RoleKeyManager):
            client = None
            label = "coder-pool"
        else:
            client, label = self._get_client_for_role("coder")
        
        # Build simple config-like object
        class _Cfg:
            def __init__(self, system_instruction, temperature, top_p):
                self.system_instruction = system_instruction
                self.temperature = temperature
                self.top_p = top_p
        
        use_genai_config = self._uses_gemini_sdk() and _load_genai()
        config = (
            _genai_types.GenerateContentConfig(
                system_instruction=system, temperature=temperature, top_p=top_p
            )
            if use_genai_config
            else _Cfg(system, temperature, top_p)
        )
        contents = self._build_contents(system, user)
        
        last_error = None
        backoff = 1.0
        
        for attempt in range(max_retries):
            try:
                request = {
                    "model": model or self._model_for_provider(),
                    "contents": contents,
                    "config": config,
                }
                if isinstance(self._km, RoleKeyManager):
                    response, label = self._km.generate(RoleKeyManager.TOOL_POOL, **request)
                else:
                    response = client.models.generate_content(**request)
                text = _extract_response_text(response, context=f"AI request ({label})")
                return _SimpleResponse(text, model=label)
            except AiResponseError:
                # Terminal: retrying a safety block, quota refusal or truncated
                # response only delays a clear error for the caller.
                raise
            except Exception as e:
                last_error = e
                error_code = getattr(e, "code", None) or getattr(e, "status_code", None)
                is_rate_limit = error_code == 429 or "429" in str(e) or "rate" in str(e).lower()
                
                if is_rate_limit and attempt < max_retries - 1:
                    time.sleep(min(backoff, 32))
                    backoff *= 2
                    continue
                raise
        
        raise RuntimeError(f"All retries exhausted: {last_error}")

    def chat_with_role(self, role: str, system: str, user: str, 
                       temperature: float = 0.3, top_p: float = 0.9,
                       max_retries: int = 3, model: str = None,
                       max_tokens: int = None,
                       conversation: Optional[List[dict]] = None) -> _SimpleResponse:
        """
        Chat completion with a specific role (planner, designer, coder, reviewer, reporter, analyst).
        
        Each role uses its dedicated API key for consistent behavior and quota isolation.
        
        Args:
            role: One of "planner", "designer", "coder", "reviewer", "reporter", "analyst"
            system: System prompt
            user: User prompt
            temperature: Sampling temperature
            top_p: Top-p sampling
            max_retries: Max retry attempts
        """
        role_idx = self._ROLE_MAP.get(role.lower(), 0)
        if not isinstance(system, str) or not isinstance(user, str):
            raise AiInputError("System and user prompts must be text.")
        if conversation is not None:
            if not isinstance(conversation, list) or not conversation:
                raise AiInputError("Conversation must contain user and assistant messages.")
            validated_conversation = []
            for message in conversation:
                if not isinstance(message, dict):
                    raise AiInputError("Each conversation message must be an object.")
                msg_role = message.get("role")
                content = message.get("content")
                if msg_role not in {"user", "assistant"} or not isinstance(content, str):
                    raise AiInputError("Conversation messages require user or assistant roles and text content.")
                validated_conversation.append({"role": msg_role, "content": content})
            if not any(message["role"] == "user" for message in validated_conversation):
                raise AiInputError("Conversation must include a user message.")
            conversation = validated_conversation
        if isinstance(self._km, RoleKeyManager):
            pool_name = RoleKeyManager.TOOL_POOL if role_idx >= 2 else RoleKeyManager.SEARCH_POOL
            client = None
            label = f"{role}-pool"
        else:
            # Keep the documented client interface for custom/demo managers.
            client, label = self._get_client_for_role(role)
        
        class _Cfg:
            def __init__(self, system_instruction, temperature, top_p, max_tokens):
                self.system_instruction = system_instruction
                self.temperature = temperature
                self.top_p = top_p
                self.max_tokens = max_tokens
        
        use_genai_config = self._uses_gemini_sdk() and _load_genai()
        config_values = {
            "system_instruction": system,
            "temperature": temperature,
            "top_p": top_p,
        }
        if max_tokens is not None:
            max_tokens = int(max_tokens)
            if max_tokens < 1:
                raise ValueError("max_tokens must be a positive integer")
            config_values["max_output_tokens"] = max_tokens
        config = (
            _genai_types.GenerateContentConfig(**config_values)
            if use_genai_config
            else _Cfg(system, temperature, top_p, max_tokens)
        )
        contents = self._build_contents(system, user, conversation=conversation)
        
        last_error = None
        backoff = 1.0
        
        for attempt in range(max_retries):
            try:
                request = {
                    "model": model or self._model_for_provider(),
                    "contents": contents,
                    "config": config,
                }
                if isinstance(self._km, RoleKeyManager):
                    response, label = self._km.generate(pool_name, **request)
                else:
                    response = client.models.generate_content(**request)
                text = _extract_response_text(response, context=f"AI request ({label})")
                return _SimpleResponse(text, model=label)
            except AiResponseError:
                # Terminal: retrying a safety block, quota refusal or truncated
                # response only delays a clear error for the caller.
                raise
            except Exception as e:
                last_error = e
                error_code = getattr(e, "code", None) or getattr(e, "status_code", None)
                is_rate_limit = error_code == 429 or "429" in str(e) or "rate" in str(e).lower()
                
                if is_rate_limit and attempt < max_retries - 1:
                    time.sleep(min(backoff, 32))
                    backoff *= 2
                    continue
                raise
        
        raise RuntimeError(f"All retries exhausted for role {role}: {last_error}")

    def chat_vision(self, system: str, user: str, image_bytes: bytes, 
                    mime_type: str = "image/png", temperature: float = 0.3,
                    max_retries: int = 3) -> _SimpleResponse:
        """
        Vision/multimodal chat completion.
        
        Args:
            system: System prompt
            user: User prompt
            image_bytes: Raw image bytes
            mime_type: MIME type (image/png, image/jpeg, etc.)
            temperature: Sampling temperature
            max_retries: Max retry attempts
        """
        client, label = self._get_client_for_role("coder")  # Vision uses coder role
        
        vision_part = _make_vision_part(
            image_bytes, mime_type, use_genai=self._uses_gemini_sdk()
        )
        
        class _Cfg:
            def __init__(self, system_instruction, temperature, top_p):
                self.system_instruction = system_instruction
                self.temperature = temperature
                self.top_p = top_p
        
        use_genai_config = self._uses_gemini_sdk() and _load_genai()
        config = (
            _genai_types.GenerateContentConfig(
                system_instruction=system, temperature=temperature, top_p=0.9
            )
            if use_genai_config
            else _Cfg(system, temperature, 0.9)
        )
        contents = self._build_contents(system, user, [vision_part])
        
        last_error = None
        backoff = 1.0
        
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=self._model_for_provider(),
                    contents=contents,
                    config=config
                )
                text = _extract_response_text(response, context=f"AI request ({label})")
                return _SimpleResponse(text, model=label)
            except AiResponseError:
                # Terminal: retrying a safety block, quota refusal or truncated
                # response only delays a clear error for the caller.
                raise
            except Exception as e:
                last_error = e
                error_code = getattr(e, "code", None) or getattr(e, "status_code", None)
                is_rate_limit = error_code == 429 or "429" in str(e) or "rate" in str(e).lower()
                
                if is_rate_limit and attempt < max_retries - 1:
                    time.sleep(min(backoff, 32))
                    backoff *= 2
                    continue
                # If vision fails, mark as unsupported and raise
                self._vision_supported = False
                raise
        
        raise RuntimeError(f"Vision request failed after retries: {last_error}")

    def chat_json(self, system: str, user: str, schema: dict = None,
                  role: str = "coder", temperature: float = 0.1) -> _SimpleResponse:
        """
        Chat completion that expects JSON output.
        
        Args:
            system: System prompt (should instruct JSON output)
            user: User prompt
            schema: Optional JSON schema for validation
            role: Role to use
            temperature: Low temperature for deterministic JSON
        """
        # Add JSON instruction to system prompt
        json_instruction = "\n\nRespond ONLY with valid JSON. No markdown, no explanation."
        if schema:
            import json
            json_instruction += f"\nSchema: {json.dumps(schema)}"
        
        return self.chat_with_role(role, system + json_instruction, user, temperature=temperature)

    def get_status(self) -> dict:
        """Get status of all enabled providers and fallback keys."""
        keys = self._km.status()
        codex_available = any(k.get("label") == "codex" and k.get("available") for k in keys)
        omniroute_available = any(k.get("label") == "omniroute" and k.get("available") for k in keys)
        return {
            "keys": keys,
            "codex_available": codex_available,
            "omniroute_available": omniroute_available,
            "mode": "codex" if codex_available else "omniroute" if omniroute_available else "gemini_fallback",
        }

    @staticmethod
    def create_mock() -> "AiApi":
        """Create an AiApi instance in demo/mock mode (no API keys needed)."""
        return AiApi(use_mock=True)


# Backward compatibility - simple function interface
def quick_chat(prompt: str, system: str = "You are a helpful assistant.") -> str:
    """Quick one-liner for simple prompts. Returns text directly."""
    api = AiApi()
    resp = api.chat(system, prompt)
    return resp.choices[0].message.content


def quick_code(prompt: str, system: str = "You are an expert programmer.") -> str:
    """Quick code generation using coder role."""
    api = AiApi()
    resp = api.chat_with_role("coder", system, prompt)
    return resp.choices[0].message.content


def quick_plan(prompt: str, system: str = "You are a strategic planner.") -> str:
    """Quick planning using planner role."""
    api = AiApi()
    resp = api.chat_with_role("planner", system, prompt)
    return resp.choices[0].message.content
