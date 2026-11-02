"""Small, local ChatGPT OAuth adapter backed by the installed Codex CLI.

The CLI owns authentication and reads its supported OAuth cache directly. This
module never reads or returns credentials, and it never persists a Codex
session. Requests run in a new, empty temporary directory with read-only
execution permissions.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any


MODEL_ID = "gpt-6-luna"
REASONING_EFFORT = "high"
EXEC_TIMEOUT_SECONDS = 180
STATUS_TIMEOUT_SECONDS = 12
MAX_PROMPT_CHARS = 128_000


class CodexProviderError(RuntimeError):
    """Safe, user-facing Codex provider failure."""


class CodexPromptError(ValueError):
    """The chat request is not a supported text conversation."""


_login_lock = threading.Lock()
_login_process: subprocess.Popen | None = None


def _find_codex() -> str | None:
    """Resolve Codex from PATH or its standard app install without auth files."""
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured and Path(configured).is_file():
        return configured

    # Prefer the requested F: installation when it exists. Resolve the native
    # executable instead of the npm .cmd shim so Python can launch it directly.
    f_drive_install = Path(
        r"F:\CodexCLI\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64"
        r"\vendor\x86_64-pc-windows-msvc\bin\codex.exe"
    )
    if f_drive_install.is_file():
        return str(f_drive_install)

    executable = shutil.which("codex.exe") or shutil.which("codex")
    if executable:
        return executable

    # The desktop app bundles the CLI in a versioned directory and may add
    # that directory to PATH only for its own child processes. This fallback
    # lets a regular PowerShell/Python invocation find the same installation.
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        bundled = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        candidates = [path for path in bundled.glob("*/codex.exe") if path.is_file()]
        if candidates:
            candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            return str(candidates[0])
    return None


def _cli_environment() -> dict[str, str]:
    """Pass only ordinary runtime paths; API-key variables are never copied."""
    names = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "HOME",
        "XDG_CONFIG_HOME",
        "CODEX_HOME",
    )
    env = {name: os.environ[name] for name in names if os.environ.get(name)}
    # Some Windows app-launched processes omit HOME even though USERPROFILE is
    # set; Codex CLI uses HOME to find its per-user OAuth store.
    if os.name == "nt":
        profile = os.environ.get("USERPROFILE")
        if profile:
            env.setdefault("HOME", profile)
            # The desktop app may launch this adapter without a shell profile.
            # Supplying CODEX_HOME explicitly keeps CLI auth discovery stable
            # even when its Windows home-directory probing is unavailable.
            env.setdefault("CODEX_HOME", str(Path(profile) / ".codex"))
        for name in ("HOMEDRIVE", "HOMEPATH"):
            if os.environ.get(name):
                env.setdefault(name, os.environ[name])
    return env


def status() -> dict[str, bool]:
    """Return only CLI discovery and the exit-status boolean for login status."""
    executable = _find_codex()
    if not executable:
        return {"cli_found": False, "logged_in": False}

    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": _cli_environment(),
        "timeout": STATUS_TIMEOUT_SECONDS,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        result = subprocess.run([executable, "login", "status"], **kwargs)
        logged_in = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        logged_in = False
    return {"cli_found": True, "logged_in": logged_in}


def launch_login() -> str:
    """Open an interactive Codex login window on Windows, without capturing I/O."""
    global _login_process
    executable = _find_codex()
    if not executable:
        raise CodexProviderError("Codex CLI was not found on PATH.")
    if os.name != "nt":
        raise CodexProviderError("Interactive Codex login is supported on Windows only.")

    with _login_lock:
        if _login_process is not None and _login_process.poll() is None:
            return "already_running"
        try:
            _login_process = subprocess.Popen(
                [executable, "login"],
                stdin=None,
                stdout=None,
                stderr=None,
                env=_cli_environment(),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                close_fds=False,
            )
        except OSError:
            _login_process = None
            raise CodexProviderError("Could not open the Codex login window.") from None
    return "started"


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, dict) and item.get("type") in {"image_url", "input_image"}:
                # Codex is exposed here as a text-only chat provider. Preserve
                # the surrounding prompt and any extracted image descriptions,
                # but do not turn an optional PDF image attachment into a hard
                # failure for an otherwise valid outline request.
                continue
        return "\n".join(parts)
    if content is None:
        return ""
    raise CodexPromptError("Each chat message must contain text.")


def _build_prompt(messages: Any) -> str:
    if not isinstance(messages, list):
        raise CodexPromptError("A messages array is required.")

    transcript: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"system", "developer", "user", "assistant"}:
            continue
        text = _text_content(item.get("content"))
        if text:
            transcript.append(f"{role.upper()}:\n{text}")

    if not transcript:
        raise CodexPromptError("At least one text message is required.")

    prompt = (
        "You are a conversational assistant. Answer the messages directly. "
        "Do not inspect files, run commands, or make changes. Use only the conversation below.\n\n"
        + "\n\n".join(transcript)
    )
    if len(prompt) > MAX_PROMPT_CHARS:
        raise CodexPromptError("The conversation is too long for the Codex chat endpoint.")
    return prompt


def _run_codex(prompt: str) -> str:
    executable = _find_codex()
    if not executable:
        raise CodexProviderError("Codex CLI was not found on PATH.")

    with tempfile.TemporaryDirectory(prefix="campus-codex-") as temp_dir:
        working_dir = Path(temp_dir)
        output_path = working_dir / "last-message.txt"
        command = [
            executable,
            "exec",
            "--model",
            MODEL_ID,
            "--config",
            'model_reasoning_effort="high"',
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--cd",
            str(working_dir),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                stdin=None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=working_dir,
                env=_cli_environment(),
                timeout=EXEC_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CodexProviderError("Codex did not finish before the request timeout.") from None
        except OSError:
            raise CodexProviderError("Could not start the Codex CLI.") from None

        if result.returncode != 0:
            raise CodexProviderError("Codex could not complete the request. Check Codex login status and try again.")
        try:
            response = output_path.read_text(encoding="utf-8").strip()
        except OSError:
            response = ""
        if not response:
            raise CodexProviderError("Codex returned an empty response.")
        return response


def chat_completion(messages: Any) -> dict[str, Any]:
    """Run one ephemeral Codex turn and return an OpenAI chat completion."""
    text = _run_codex(_build_prompt(messages))
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
