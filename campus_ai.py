"""Small shared entry point for optional Campus Suite AI assistance."""
from __future__ import annotations

import threading
import re


_SENSITIVE_PATTERNS = (
    # Private key blocks are unmistakable and should be removed as a unit.
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL),
    # Recognizable provider credentials (including the project's Gemini key form).
    re.compile(r"\b(?:AQ\.[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{20,})\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}"),
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|secret)"
    r"(\s*[:=]\s*)(?:\"[^\"\r\n]{16,}\"|'[^'\r\n]{16,}'|[^\s,;}\]]{16,})"
)


def redact_sensitive_text(value: str) -> str:
    """Mask recognizable credentials before user content leaves this process."""
    text = str(value or "")
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return _CREDENTIAL_ASSIGNMENT.sub(lambda match: match.group(1) + match.group(2) + "[REDACTED]", text)


def ask_campus_ai(
    system_prompt: str,
    user_prompt: str,
    *,
    role: str = "analyst",
    max_tokens: int | None = None,
    temperature: float = 0.3,
) -> str:
    """Send an explicit user-requested prompt through the shared key manager."""
    # Keep the shared provider stack off import paths used by local-only tools.
    from key_manager import AiApi

    response = AiApi().chat_with_role(
        role,
        redact_sensitive_text(system_prompt),
        redact_sensitive_text(user_prompt),
        temperature=temperature,
        max_retries=1,
        max_tokens=max_tokens,
    )
    answer = response.choices[0].message.content
    if not answer or not answer.strip():
        raise RuntimeError("Campus Suite AI returned an empty response.")
    return answer.strip()


def ask_about_result(parent, tool_name: str, context: str, *, privacy_note: str = "") -> None:
    """Show an opt-in AI follow-up for a result in a standalone desktop tool."""
    from tkinter import messagebox, simpledialog, Toplevel, ttk

    from tkinter.scrolledtext import ScrolledText

    if not context.strip():
        messagebox.showinfo("Nothing to ask about", "Create or select a result first.", parent=parent)
        return
    question = simpledialog.askstring("Ask Campus AI", "What would you like help with?", parent=parent)
    if not question or not question.strip():
        return
    notice = "This sends the result and your question to the configured Campus Suite AI provider. Review it first and remove private information."
    if privacy_note:
        notice += "\n\n" + privacy_note
    if not messagebox.askyesno("Send result to Campus AI?", notice, parent=parent):
        return

    window = Toplevel(parent)
    window.title(f"Campus AI · {tool_name}")
    window.geometry("720x520")
    window.minsize(520, 360)
    ttk.Label(window, text=f"Campus AI · {tool_name}", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
    status = ttk.Label(window, text="Asking the configured Campus Suite AI provider…")
    status.pack(anchor="w", padx=16, pady=(0, 8))
    answer = ScrolledText(window, wrap="word", font=("Segoe UI", 10), padx=10, pady=10)
    answer.pack(fill="both", expand=True, padx=16, pady=(0, 16))
    answer.insert("1.0", "Working…")
    answer.configure(state="disabled")

    def worker():
        try:
            reply = ask_campus_ai(
                f"You are an optional assistant inside the Campus Suite {tool_name}. "
                "Help interpret the displayed tool result or answer the user's question. "
                "Be clear about uncertainty and do not claim to have performed actions.",
                f"Tool result:\n{context[:16000]}\n\nUser question:\n{question.strip()}",
            )
            error = False
        except Exception as exc:
            reply, error = str(exc), True

        def finish():
            if not window.winfo_exists():
                return
            status.configure(text="Campus AI could not answer" if error else "Review the suggestion before using it")
            answer.configure(state="normal")
            answer.delete("1.0", "end")
            answer.insert("1.0", reply)
            answer.configure(state="disabled")

        try:
            parent.after(0, finish)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
