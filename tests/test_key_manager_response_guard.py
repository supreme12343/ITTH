"""Regression tests for the hardened response parsing in key_manager.AiApi.

A provider returning zero candidates — a safety block, a quota refusal, or a
truncated response — used to raise a bare IndexError/AttributeError out of
`response.candidates[0].content.parts`, surfacing as an unexplained crash in
every Campus Suite tool. These tests pin the clear, catchable error instead.
"""
import pytest

from key_manager import AiApi, AiResponseError, _extract_response_text


class _Part:
    def __init__(self, text):
        self.text = text


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Candidate:
    def __init__(self, parts=None, finish_reason=None):
        self.content = _Content(parts) if parts is not None else None
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, candidates, block_reason=None):
        self.candidates = candidates
        if block_reason is not None:
            self.prompt_feedback = type("_Feedback", (), {"block_reason": block_reason})()


def test_text_is_joined_across_parts_and_tolerates_none():
    response = _Response([_Candidate([_Part("Hello "), _Part(None), _Part("world")])])
    assert _extract_response_text(response) == "Hello world"


def test_zero_candidates_raises_a_named_error_with_provider_diagnostics():
    response = _Response([], block_reason="SAFETY")
    with pytest.raises(AiResponseError) as excinfo:
        _extract_response_text(response, context="AI request (gemini-key-1)")
    message = str(excinfo.value)
    assert "empty or blocked" in message
    assert "gemini-key-1" in message
    assert "block_reason=SAFETY" in message


def test_none_candidates_list_raises_rather_than_attribute_error():
    with pytest.raises(AiResponseError):
        _extract_response_text(_Response(None))


def test_candidate_without_content_reports_the_finish_reason():
    response = _Response([_Candidate(parts=None, finish_reason="MAX_TOKENS")])
    with pytest.raises(AiResponseError) as excinfo:
        _extract_response_text(response)
    assert "finish_reason=MAX_TOKENS" in str(excinfo.value)


def test_candidate_with_empty_parts_list_raises():
    with pytest.raises(AiResponseError):
        _extract_response_text(_Response([_Candidate([])]))


def test_whitespace_only_text_is_treated_as_empty():
    with pytest.raises(AiResponseError):
        _extract_response_text(_Response([_Candidate([_Part("   ")])]))


def test_none_response_object_raises():
    with pytest.raises(AiResponseError):
        _extract_response_text(None)


def test_falls_back_to_a_plain_text_response_shape():
    """The circuit-breaker response exposes only `.text` — it must still read."""
    plain = type("_Plain", (), {"candidates": [], "text": "All AI keys are rate-limited."})()
    assert _extract_response_text(plain) == "All AI keys are rate-limited."


def test_the_error_is_catchable_as_runtimeerror():
    """Existing `except RuntimeError` handlers across Campus Suite must still work."""
    assert issubclass(AiResponseError, RuntimeError)


class _FakeKeyManager:
    """Minimal stand-in; `_omniroute_available` keeps the genai config path out."""

    _omniroute_available = True

    def __init__(self, response):
        self._response = response
        self.calls = 0
        self.models = self

    def client_for_role(self, role_index):
        return self, f"fake-role-{role_index}"

    def generate_content(self, **kwargs):
        self.calls += 1
        return self._response

    def status(self):
        return []


@pytest.mark.parametrize("method", ["chat", "chat_with_role", "chat_vision"])
def test_public_chat_methods_raise_instead_of_crashing_on_a_blocked_reply(method):
    km = _FakeKeyManager(_Response([], block_reason="SAFETY"))
    api = AiApi(key_manager=km)
    with pytest.raises(AiResponseError):
        if method == "chat":
            api.chat("system", "user")
        elif method == "chat_with_role":
            api.chat_with_role("coder", "system", "user")
        else:
            api.chat_vision("system", "user", b"\x89PNG")
    # A safety block is terminal: it must not be retried as a rate limit, which
    # is what made these calls appear to hang.
    assert km.calls == 1


def test_a_good_reply_still_returns_the_documented_shape():
    km = _FakeKeyManager(_Response([_Candidate([_Part("ok")])]))
    api = AiApi(key_manager=km)
    assert api.chat("system", "user").choices[0].message.content == "ok"
    assert api.chat_with_role("coder", "system", "user").choices[0].message.content == "ok"


# ---------------------------------------------------------------------------
# Import cost: `import openai` measured 30-130s on this machine (antivirus),
# and every Campus Suite tool paid it just by touching key_manager.
# ---------------------------------------------------------------------------


def test_importing_key_manager_does_not_import_openai():
    """Pin the lazy import: a module-scope `import openai` must not come back."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [sys.executable, "-c", "import sys, key_manager; print('OPENAI_LOADED=%s' % ('openai' in sys.modules))"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    assert "OPENAI_LOADED=False" in completed.stdout, completed.stdout


def test_unconfigured_omniroute_never_triggers_the_openai_import(monkeypatch):
    """With no OMNIROUTE_API_KEY the SDK must not be touched at all."""
    import key_manager

    def fail_if_called():
        raise AssertionError("openai was imported even though OmniRoute is not configured")

    monkeypatch.setattr(key_manager, "OMNIROUTE_API_KEY", "")
    monkeypatch.setattr(key_manager, "ENABLE_OMNIROUTE", True)
    monkeypatch.setattr(key_manager, "_load_openai", fail_if_called)

    manager = key_manager.RoleKeyManager()
    assert manager._omniroute_available is False
    # The Gemini fallback must still be wired up and reporting status.
    assert isinstance(manager.status(), list)


def test_omniroute_configured_helper():
    import key_manager

    assert key_manager._omniroute_configured() == bool(
        key_manager.ENABLE_OMNIROUTE and key_manager.OMNIROUTE_API_KEY
    )


def test_outbound_requests_have_a_bounded_timeout():
    """A call with no timeout is how a tool appears to hang forever."""
    import key_manager

    assert key_manager.OMNIROUTE_TIMEOUT_SECONDS > 0
    assert key_manager.OMNIROUTE_TIMEOUT_SECONDS <= 300
