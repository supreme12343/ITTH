"""Offline contract tests for the local Codex CLI adapter."""
from pathlib import Path
from types import SimpleNamespace

import campus_codex_provider as provider


def test_chat_completion_uses_gpt_6_luna_high_and_returns_mocked_output(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output_index = command.index("--output-last-message") + 1
        Path(command[output_index]).write_text("Mocked Codex answer", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(provider, "_find_codex", lambda: "codex.exe")
    monkeypatch.setattr(provider.subprocess, "run", fake_run)

    completion = provider.chat_completion([
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Say hello."},
    ])

    command = captured["command"]
    assert command[command.index("--model") + 1] == "gpt-6-luna"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="high"'
    assert "--ephemeral" in command
    assert "SYSTEM:\nBe concise." in captured["kwargs"]["input"]
    assert "USER:\nSay hello." in captured["kwargs"]["input"]
    assert completion["model"] == "gpt-6-luna"
    assert completion["choices"][0]["message"]["content"] == "Mocked Codex answer"
