"""Lazy adapter for the local Campus Suite GGUF model.

The Campus Suite identity prompt is read only inside this adapter and is never
added to a request sent to Codex, Gemini, or OmniRoute.
"""
from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from typing import Any


MODEL_PATH = Path(__file__).resolve().with_name("campus-suite-ai-Q4_K_M.gguf")
SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().with_name("campus-suite-ai-lora") / "system_prompt.txt"
)
_model = None
_model_lock = threading.Lock()
_generation_lock = threading.Lock()


def assets_available() -> bool:
    """Whether the GGUF file and its Campus Suite identity prompt are present."""
    try:
        return (
            MODEL_PATH.is_file()
            and SYSTEM_PROMPT_PATH.is_file()
            and SYSTEM_PROMPT_PATH.stat().st_size > 0
        )
    except OSError:
        return False


def is_configured() -> bool:
    """Whether assets exist and llama.cpp's native runtime can be loaded."""
    if not assets_available() or importlib.util.find_spec("llama_cpp") is None:
        return False
    try:
        # Importing checks the native DLLs without loading the large GGUF into RAM.
        from llama_cpp import Llama  # noqa: F401

        return True
    except Exception:
        return False


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            from llama_cpp import Llama

            try:
                gpu_layers = max(0, int(os.environ.get("CAMPUS_LOCAL_GPU_LAYERS", "0")))
            except (TypeError, ValueError):
                gpu_layers = 0
            _model = Llama(
                model_path=str(MODEL_PATH),
                n_ctx=4096,
                n_threads=max(1, min(8, os.cpu_count() or 1)),
                n_gpu_layers=gpu_layers,
                verbose=False,
            )
        return _model


def warm_up() -> None:
    """Load the local GGUF into memory before a live demonstration."""
    _load_model()


def _local_messages(
    messages: list[dict[str, str]],
    *,
    identity_prompt: str | None = None,
) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        raise ValueError("The local AI request must contain a message list.")

    local_identity = (
        identity_prompt.strip()
        if identity_prompt is not None
        else SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    )
    if not local_identity:
        raise ValueError("The local Campus Suite system prompt is empty.")

    request_system = []
    conversation = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Each local AI message must be an object.")
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "developer", "user", "assistant"}:
            raise ValueError("The local AI request contains an unsupported message role.")
        if not isinstance(content, str):
            raise ValueError("Local AI message content must be text.")
        if role in {"system", "developer"}:
            request_system.append(content)
        else:
            conversation.append({"role": role, "content": content})

    if not any(message["role"] == "user" for message in conversation):
        raise ValueError("The local AI request must include a user message.")

    system_parts = [local_identity, *request_system]
    return [{"role": "system", "content": "\n\n".join(system_parts)}, *conversation]


def chat_completion(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 512,
    temperature: float = 0.3,
    top_p: float = 0.9,
    identity_prompt: str | None = None,
) -> dict[str, Any]:
    """Generate one local-only chat completion using the local identity prompt."""
    local_request = _local_messages(messages, identity_prompt=identity_prompt)
    # llama.cpp model instances are reused across requests but are not assumed
    # thread-safe. Serialize both lazy first-load and generation on this path.
    with _generation_lock:
        response = _load_model().create_chat_completion(
            messages=local_request,
            max_tokens=max(1, int(max_tokens)),
            temperature=float(temperature),
            top_p=float(top_p),
            repeat_penalty=1.1,
        )
    return response
