"""Mocked provider-routing regressions; these tests never call the network."""
import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import key_manager


class _GeminiConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _response(text="Gemini answer"):
    part = SimpleNamespace(text=text)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content, finish_reason="STOP")
    return SimpleNamespace(candidates=[candidate])


def _manager(monkeypatch, *, omniroute=False, gemini_generate=None):
    import campus_local_provider

    monkeypatch.setattr(key_manager, "ENABLE_CODEX_PROVIDER", False)
    monkeypatch.setattr(key_manager, "CODEX_ONLY_PROVIDER", False)
    monkeypatch.setattr(campus_local_provider, "is_configured", lambda: False)
    states = [key_manager.KeyState(f"mock-key-{i}", f"mock-label-{i}") for i in range(5)]
    manager = key_manager.RoleKeyManager.__new__(key_manager.RoleKeyManager)
    manager.cooldown_seconds = 30
    manager._lock = threading.Lock()
    manager._states = states
    manager._pools = {
        manager.SEARCH_POOL: itertools.cycle(states[:2]),
        manager.TOOL_POOL: itertools.cycle(states[2:5]),
        manager.ALL_POOL: itertools.cycle(states),
    }
    manager._genai_clients = {}
    manager._omniroute_available = omniroute
    manager._omniroute_client = None
    monkeypatch.setattr(key_manager, "_load_genai", lambda: True)
    monkeypatch.setattr(key_manager, "_genai_types", SimpleNamespace(GenerateContentConfig=_GeminiConfig))
    if gemini_generate is not None:
        class _GeminiModels:
            def generate_content(self, **kwargs):
                return gemini_generate(**kwargs)

        client = SimpleNamespace(models=_GeminiModels())
        manager._genai_client_for = lambda _state: client
    return manager


def test_unset_gemini_key_slots_do_not_create_default_credentials(monkeypatch):
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        "GEMINI_API_KEY_5",
    ):
        monkeypatch.delenv(name, raising=False)

    manager = key_manager.RoleKeyManager.__new__(key_manager.RoleKeyManager)
    assert manager._load_gemini_keys(None) == []


def test_gemini_key_loader_uses_only_configured_slots(monkeypatch):
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        "GEMINI_API_KEY_5",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "configured-primary")
    monkeypatch.setenv("GEMINI_API_KEY_2", "configured-secondary")

    manager = key_manager.RoleKeyManager.__new__(key_manager.RoleKeyManager)
    assert manager._load_gemini_keys(None) == ["configured-primary", "configured-secondary"]


def test_unconfigured_manager_reports_missing_credentials_without_loading_a_provider(monkeypatch):
    monkeypatch.setattr(key_manager, "ENABLE_CODEX_PROVIDER", False)
    monkeypatch.setattr(key_manager, "CODEX_ONLY_PROVIDER", False)
    manager = key_manager.RoleKeyManager.__new__(key_manager.RoleKeyManager)
    manager._states = []
    manager._omniroute_available = False
    manager._omniroute_client = None

    with pytest.raises(RuntimeError, match="No configured AI provider could complete"):
        manager.generate(manager.TOOL_POOL, model=key_manager.GEMINI_MODEL, contents=[])


def test_chat_with_role_uses_gemini_before_configured_omniroute(monkeypatch):
    calls = {"omni": [], "gemini": []}

    def gemini_generate(**kwargs):
        calls["gemini"].append(kwargs)
        return _response()

    manager = _manager(monkeypatch, omniroute=True, gemini_generate=gemini_generate)

    class _OmniModels:
        def generate_content(self, **kwargs):
            calls["omni"].append(kwargs)
            raise RuntimeError("mock OmniRoute outage")

    manager._omniroute_client = SimpleNamespace(models=_OmniModels())
    answer = key_manager.AiApi(key_manager=manager).chat_with_role(
        "coder", "You are concise.", "Ask one question.", max_tokens=20
    )

    assert answer.choices[0].message.content == "Gemini answer"
    assert manager._omniroute_available is True
    assert calls["omni"] == []
    assert len(calls["gemini"]) == 1
    assert calls["gemini"][0]["model"] == key_manager.GEMINI_MODEL
    assert isinstance(calls["gemini"][0]["config"], _GeminiConfig)
    assert calls["gemini"][0]["config"].max_output_tokens == 20


def test_role_client_proxy_preserves_google_genai_response_and_provider_label(monkeypatch):
    raw_response = _response("routed response")

    def gemini(**_kwargs):
        return raw_response

    manager = _manager(monkeypatch, gemini_generate=gemini)
    state = key_manager.KeyState("mock-key", "mock-label")
    manager._states = [state]
    manager._pools = {
        manager.SEARCH_POOL: itertools.cycle([state]),
        manager.TOOL_POOL: itertools.cycle([state]),
        manager.ALL_POOL: itertools.cycle([state]),
    }
    client, label = manager.client_for_role(2)

    response = client.models.generate_content(
        model=key_manager.GEMINI_MODEL,
        contents=[{"role": "user", "parts": [{"text": "test"}]}],
        config=SimpleNamespace(system_instruction="system", temperature=0.2, top_p=0.8),
    )

    assert label == "campus-ai-router"
    assert response is raw_response
    assert client.last_label == "mock-label"


@pytest.mark.parametrize("error_name", ["RemoteProtocolError", "ProxyError"])
def test_role_generate_does_not_rotate_keys_after_transport_error(monkeypatch, error_name):
    transport_error = type(error_name, (Exception,), {})
    attempts = []

    def fail(**_kwargs):
        attempts.append("one")
        raise transport_error("mock transport failure")

    manager = _manager(monkeypatch, gemini_generate=fail)
    with pytest.raises(RuntimeError, match="No configured AI provider could complete"):
        manager.generate(manager.TOOL_POOL, model=key_manager.GEMINI_MODEL, contents=[])
    assert attempts == ["one"]


@pytest.mark.parametrize("error_name", ["RemoteProtocolError", "ProxyError"])
def test_rotating_client_does_not_try_other_keys_after_transport_error(monkeypatch, error_name):
    transport_error = type(error_name, (Exception,), {})
    attempts = []

    def fail(**_kwargs):
        attempts.append("one")
        raise transport_error("mock transport failure")

    manager = _manager(monkeypatch, gemini_generate=fail)
    client = key_manager._RotatingGeminiClient(manager, manager.TOOL_POOL)
    with pytest.raises(RuntimeError, match="Could not reach the Gemini provider"):
        client.generate_content(model=key_manager.GEMINI_MODEL, contents=[])
    assert attempts == ["one"]


def _route_manager(monkeypatch, *, gemini, local, omni):
    """One-key mocked stack; no provider executable, SDK network, or model loads."""
    manager = _manager(monkeypatch, gemini_generate=gemini)
    state = key_manager.KeyState("mock-route-key", "mock-route-label")
    manager._states = [state]
    manager._pools = {
        manager.SEARCH_POOL: itertools.cycle([state]),
        manager.TOOL_POOL: itertools.cycle([state]),
        manager.ALL_POOL: itertools.cycle([state]),
    }
    manager._omniroute_available = omni is not None

    class _OmniModels:
        def generate_content(self, **kwargs):
            return omni(**kwargs)

    manager._omniroute_client = SimpleNamespace(models=_OmniModels()) if omni else None
    monkeypatch.setattr(key_manager, "ENABLE_CODEX_PROVIDER", True)
    monkeypatch.setattr(key_manager, "CODEX_ONLY_PROVIDER", False)
    monkeypatch.setattr("campus_codex_provider.status", lambda: {"cli_found": True, "logged_in": True})

    import campus_local_provider

    monkeypatch.setattr(campus_local_provider, "is_configured", lambda: local is not None)
    monkeypatch.setattr(campus_local_provider, "chat_completion", local or (lambda *_a, **_k: pytest.fail("local provider should be skipped")))
    return manager


def _route_request(manager):
    return manager.generate(
        manager.TOOL_POOL,
        model="gpt-6-luna",
        contents=[{"role": "user", "parts": [{"text": "route test"}]}],
        config=SimpleNamespace(system_instruction="caller system", temperature=0.2, top_p=0.8),
    )


def _codex_success(text="Codex answer"):
    return {"choices": [{"message": {"content": text}}]}


def test_provider_route_scenario_a_codex_success_stops_fallbacks(monkeypatch, tmp_path):
    calls = []
    import campus_local_provider

    system_path = tmp_path / "local-system.txt"
    system_path.write_text("Local-only prompt sentinel.", encoding="utf-8")
    monkeypatch.setattr(campus_local_provider, "SYSTEM_PROMPT_PATH", system_path)
    manager = _route_manager(
        monkeypatch,
        gemini=lambda **_kwargs: calls.append("gemini") or _response(),
        local=lambda *_a, **_kwargs: calls.append("local") or _codex_success("local"),
        omni=lambda **_kwargs: calls.append("omni") or _response("omni"),
    )

    def codex(_messages):
        calls.append("codex")
        assert "Local-only prompt sentinel." not in repr(_messages)
        return _codex_success()

    monkeypatch.setattr("campus_codex_provider.chat_completion", codex)
    response, label = _route_request(manager)

    assert calls == ["codex"]
    assert label == "codex-gpt-6-luna-high"
    assert response.candidates[0].content.parts[0].text == "Codex answer"


def test_provider_route_scenario_b_gemini_follows_codex_failure(monkeypatch):
    calls = []
    manager = _route_manager(
        monkeypatch,
        gemini=lambda **_kwargs: calls.append("gemini") or _response("Gemini answer"),
        local=lambda *_a, **_kwargs: calls.append("local") or _codex_success("local"),
        omni=lambda **_kwargs: calls.append("omni") or _response("omni"),
    )

    def codex(_messages):
        calls.append("codex")
        raise RuntimeError("mock Codex outage")

    monkeypatch.setattr("campus_codex_provider.chat_completion", codex)
    response, label = _route_request(manager)

    assert calls == ["codex", "gemini"]
    assert label == "mock-route-label"
    assert response.candidates[0].content.parts[0].text == "Gemini answer"


def test_provider_route_scenario_c_local_follows_gemini_failure(monkeypatch):
    calls = []

    def gemini(**_kwargs):
        calls.append("gemini")
        raise RuntimeError("mock Gemini outage")

    def local(_messages, **_kwargs):
        calls.append("local")
        return _codex_success("Local answer")

    manager = _route_manager(
        monkeypatch, gemini=gemini, local=local,
        omni=lambda **_kwargs: calls.append("omni") or _response("omni"),
    )

    def codex(_messages):
        calls.append("codex")
        raise RuntimeError("mock Codex outage")

    monkeypatch.setattr("campus_codex_provider.chat_completion", codex)
    response, label = _route_request(manager)

    assert calls == ["codex", "gemini", "local"]
    assert label == "campus-suite-ai-local"
    assert response.candidates[0].content.parts[0].text == "Local answer"


def test_provider_route_scenario_d_omniroute_is_last_fallback(monkeypatch):
    calls = []

    def gemini(**_kwargs):
        calls.append("gemini")
        raise RuntimeError("mock Gemini outage")

    def local(_messages, **_kwargs):
        calls.append("local")
        raise RuntimeError("mock local outage")

    def omni(**_kwargs):
        calls.append("omni")
        return _response("OmniRoute answer")

    manager = _route_manager(monkeypatch, gemini=gemini, local=local, omni=omni)

    def codex(_messages):
        calls.append("codex")
        raise RuntimeError("mock Codex outage")

    monkeypatch.setattr("campus_codex_provider.chat_completion", codex)
    response, label = _route_request(manager)

    assert calls == ["codex", "gemini", "local", "omni"]
    assert label == "omniroute"
    assert response.candidates[0].content.parts[0].text == "OmniRoute answer"


def test_unconfigured_codex_is_skipped_before_gemini(monkeypatch):
    calls = []
    manager = _route_manager(
        monkeypatch,
        gemini=lambda **_kwargs: calls.append("gemini") or _response("Gemini answer"),
        local=None,
        omni=None,
    )
    monkeypatch.setattr("campus_codex_provider.status", lambda: {"cli_found": True, "logged_in": False})
    monkeypatch.setattr(
        "campus_codex_provider.chat_completion",
        lambda _messages: pytest.fail("unconfigured Codex must be skipped"),
    )

    response, label = _route_request(manager)

    assert calls == ["gemini"]
    assert label == "mock-route-label"
    assert response.candidates[0].content.parts[0].text == "Gemini answer"


def test_codex_prompt_validation_is_terminal_and_does_not_fall_through(monkeypatch):
    from campus_codex_provider import CodexPromptError

    calls = []

    def codex(_messages):
        calls.append("codex")
        raise CodexPromptError("unsupported request")

    manager = _route_manager(
        monkeypatch,
        gemini=lambda **_kwargs: calls.append("gemini") or _response(),
        local=lambda *_a, **_kwargs: calls.append("local") or _codex_success(),
        omni=lambda **_kwargs: calls.append("omni") or _response(),
    )
    monkeypatch.setattr("campus_codex_provider.chat_completion", codex)

    with pytest.raises(CodexPromptError):
        _route_request(manager)
    assert calls == ["codex"]


def test_gemini_validation_error_is_terminal_and_does_not_fall_through(monkeypatch):
    calls = []
    error = RuntimeError("mock invalid request")
    error.code = 400

    def codex(_messages):
        calls.append("codex")
        raise RuntimeError("mock Codex outage")

    def gemini(**_kwargs):
        calls.append("gemini")
        raise error

    manager = _route_manager(
        monkeypatch,
        gemini=gemini,
        local=lambda *_a, **_kwargs: calls.append("local") or _codex_success(),
        omni=lambda **_kwargs: calls.append("omni") or _response(),
    )
    monkeypatch.setattr("campus_codex_provider.chat_completion", codex)

    with pytest.raises(key_manager.AiInputError, match="Gemini rejected"):
        _route_request(manager)
    assert calls == ["codex", "gemini"]


def test_provider_exhaustion_is_controlled_and_omits_provider_error_text(monkeypatch):
    calls = []

    def codex(_messages):
        calls.append("codex")
        raise RuntimeError("auth=request-secret")

    def gemini(**_kwargs):
        calls.append("gemini")
        raise RuntimeError("request=request-secret")

    def local(_messages, **_kwargs):
        calls.append("local")
        raise RuntimeError("local=request-secret")

    def omni(**_kwargs):
        calls.append("omni")
        raise RuntimeError("omni=request-secret")

    manager = _route_manager(monkeypatch, gemini=gemini, local=local, omni=omni)
    monkeypatch.setattr("campus_codex_provider.chat_completion", codex)

    with pytest.raises(RuntimeError, match="No configured AI provider could complete") as excinfo:
        _route_request(manager)
    assert calls == ["codex", "gemini", "local", "omni"]
    assert "request-secret" not in str(excinfo.value)


def test_aiapi_rejects_invalid_token_limit_before_provider_dispatch(monkeypatch):
    manager = _manager(monkeypatch)
    manager.generate = lambda *_args, **_kwargs: pytest.fail("invalid request must not route")
    api = key_manager.AiApi(key_manager=manager)

    with pytest.raises(ValueError, match="max_tokens must be a positive integer"):
        api.chat_with_role("coder", "system", "user", max_tokens=0)


def test_aiapi_translates_assistant_turns_to_gemini_model_role(monkeypatch):
    captured = {}

    def gemini(**kwargs):
        captured.update(kwargs)
        return _response("history preserved")

    manager = _manager(monkeypatch, gemini_generate=gemini)
    manager._states = [key_manager.KeyState("mock-key", "mock-label")]
    state = manager._states[0]
    manager._pools = {
        manager.SEARCH_POOL: itertools.cycle([state]),
        manager.TOOL_POOL: itertools.cycle([state]),
        manager.ALL_POOL: itertools.cycle([state]),
    }
    import campus_local_provider

    monkeypatch.setattr(campus_local_provider, "is_configured", lambda: False)

    result = key_manager.AiApi(key_manager=manager).chat_with_role(
        "coder",
        "Caller system instructions.",
        "Final question.",
        conversation=[
            {"role": "user", "content": "First question."},
            {"role": "assistant", "content": "Prior answer."},
            {"role": "user", "content": "Final question."},
        ],
    )

    assert result.choices[0].message.content == "history preserved"
    assert [item["role"] for item in captured["contents"]] == ["user", "model", "user"]
    assert [item["parts"][0].text for item in captured["contents"]] == [
        "First question.", "Prior answer.", "Final question."
    ]
    assert captured["config"].system_instruction == "Caller system instructions."


def test_local_system_prompt_is_read_only_for_the_local_adapter(monkeypatch, tmp_path):
    import campus_local_provider

    system_path = tmp_path / "local-system.txt"
    system_path.write_text("Local identity sentinel.", encoding="utf-8")
    captured = {}

    class FakeModel:
        def create_chat_completion(self, **kwargs):
            captured.update(kwargs)
            return _codex_success("local response")

    monkeypatch.setattr(campus_local_provider, "SYSTEM_PROMPT_PATH", system_path)
    monkeypatch.setattr(campus_local_provider, "_load_model", lambda: FakeModel())
    result = campus_local_provider.chat_completion([
        {"role": "system", "content": "Caller-specific system."},
        {"role": "user", "content": "Question."},
        {"role": "assistant", "content": "Prior answer."},
        {"role": "user", "content": "Follow-up."},
    ])

    assert result["choices"][0]["message"]["content"] == "local response"
    assert captured["messages"] == [
        {"role": "system", "content": "Local identity sentinel.\n\nCaller-specific system."},
        {"role": "user", "content": "Question."},
        {"role": "assistant", "content": "Prior answer."},
        {"role": "user", "content": "Follow-up."},
    ]


def test_role_proxy_forwards_native_tool_request_and_response_to_gemini(monkeypatch):
    calls = []
    function_call = SimpleNamespace(name="read_file", args={"path": "notes.txt"})
    native_response = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(
        parts=[SimpleNamespace(text=None, function_call=function_call)]
    ))])

    def gemini(**kwargs):
        calls.append(kwargs)
        return native_response

    manager = _manager(monkeypatch, gemini_generate=gemini, omniroute=True)
    state = key_manager.KeyState("mock-tool-key", "mock-tool-label")
    manager._states = [state]
    manager._pools = {
        manager.SEARCH_POOL: itertools.cycle([state]),
        manager.TOOL_POOL: itertools.cycle([state]),
        manager.ALL_POOL: itertools.cycle([state]),
    }
    monkeypatch.setattr(key_manager, "ENABLE_CODEX_PROVIDER", True)
    monkeypatch.setattr("campus_codex_provider.status", lambda: {"cli_found": True, "logged_in": True})
    import campus_local_provider

    monkeypatch.setattr(campus_local_provider, "is_configured", lambda: True)
    monkeypatch.setattr("campus_codex_provider.chat_completion", lambda *_a: pytest.fail("Codex cannot preserve tools"))
    tools = [{"function_declarations": [{"name": "read_file", "parameters": {"type": "object"}}]}]
    client, _ = manager.client_for_role(2)

    response = client.models.generate_content(
        model="gemini-tool-model",
        contents=[{"role": "user", "parts": [{"text": "Read notes."}]}],
        config=SimpleNamespace(system_instruction="tool system", temperature=0.2, top_p=0.8, tools=tools),
    )

    assert response is native_response
    assert response.candidates[0].content.parts[0].function_call is function_call
    assert client.last_label == "mock-tool-label"
    assert len(calls) == 1
    assert calls[0]["config"].tools == tools


def test_role_proxy_forwards_opaque_image_part_intact_to_gemini(monkeypatch):
    calls = []
    native_response = _response("image understood")
    image_part = object()

    def gemini(**kwargs):
        calls.append(kwargs)
        return native_response

    manager = _manager(monkeypatch, gemini_generate=gemini, omniroute=True)
    monkeypatch.setattr(key_manager, "ENABLE_CODEX_PROVIDER", True)
    monkeypatch.setattr("campus_codex_provider.status", lambda: {"cli_found": True, "logged_in": True})
    import campus_local_provider

    monkeypatch.setattr(campus_local_provider, "is_configured", lambda: True)
    monkeypatch.setattr("campus_codex_provider.chat_completion", lambda *_a: pytest.fail("Codex cannot preserve images"))
    contents = [{"role": "user", "parts": [{"text": "Describe this."}, image_part]}]
    client, _ = manager.client_for_role(0)

    response = client.models.generate_content(
        model="gemini-vision-model",
        contents=contents,
        config=SimpleNamespace(system_instruction="vision system", temperature=0.2, top_p=0.8),
    )

    assert response is native_response
    assert calls[0]["contents"][0]["parts"][1] is image_part
    assert client.last_label == "mock-label-0"


def test_image_request_falls_back_from_gemini_to_omniroute_with_openai_image_content(monkeypatch):
    requests = []

    def gemini(**_kwargs):
        raise RuntimeError("mock Gemini image outage")

    manager = _manager(monkeypatch, gemini_generate=gemini, omniroute=True)
    state = key_manager.KeyState("mock-image-key", "mock-image-label")
    manager._states = [state]
    manager._pools = {
        manager.SEARCH_POOL: itertools.cycle([state]),
        manager.TOOL_POOL: itertools.cycle([state]),
        manager.ALL_POOL: itertools.cycle([state]),
    }

    class Completions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="image fallback"))])

    manager._omniroute_client = key_manager._OmniRouteGenaiShim(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    )
    contents = [{
        "role": "user",
        "parts": [
            {"text": "Describe this image."},
            {"inline_data": {"mime_type": "image/png", "data": b"mock-png-bytes"}},
        ],
    }]

    response, label = manager.generate(
        manager.SEARCH_POOL,
        model="gemini-vision-model",
        contents=contents,
        config=SimpleNamespace(system_instruction="Caller vision instructions.", temperature=0.2),
    )

    assert response.candidates[0].content.parts[0].text == "image fallback"
    assert label == "omniroute"
    assert len(requests) == 1
    assert requests[0]["model"] == key_manager.OMNIROUTE_MODEL
    assert requests[0]["messages"][0] == {
        "role": "system", "content": "Caller vision instructions."
    }
    user_content = requests[0]["messages"][1]["content"]
    assert user_content[0] == {"type": "text", "text": "Describe this image."}
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_tool_request_gemini_failure_does_not_fall_back_to_text_only_providers(monkeypatch):
    calls = []

    def gemini(**_kwargs):
        calls.append("gemini")
        raise RuntimeError("mock Gemini tool outage")

    manager = _manager(monkeypatch, gemini_generate=gemini, omniroute=True)
    state = key_manager.KeyState("mock-tool-key", "mock-tool-label")
    manager._states = [state]
    manager._pools = {
        manager.SEARCH_POOL: itertools.cycle([state]),
        manager.TOOL_POOL: itertools.cycle([state]),
        manager.ALL_POOL: itertools.cycle([state]),
    }
    monkeypatch.setattr(key_manager, "ENABLE_CODEX_PROVIDER", True)
    monkeypatch.setattr("campus_codex_provider.status", lambda: {"cli_found": True, "logged_in": True})
    import campus_local_provider

    monkeypatch.setattr(campus_local_provider, "is_configured", lambda: True)
    monkeypatch.setattr("campus_codex_provider.chat_completion", lambda *_a: calls.append("codex"))
    monkeypatch.setattr(campus_local_provider, "chat_completion", lambda *_a, **_k: calls.append("local"))
    client, _ = manager.client_for_role(2)

    with pytest.raises(RuntimeError, match="No configured AI provider could complete"):
        client.models.generate_content(
            model="gemini-tool-model",
            contents=[{"role": "user", "parts": [{"text": "Use tool."}]}],
            config=SimpleNamespace(system_instruction="system", temperature=0.2, top_p=0.8, tools=[{"function_declarations": []}]),
        )

    assert calls == ["gemini"]


def test_local_model_load_is_singleton_and_generation_is_serialized(monkeypatch, tmp_path):
    import sys
    import time
    import campus_local_provider

    prompt_path = tmp_path / "local-system.txt"
    prompt_path.write_text("Local-only identity.", encoding="utf-8")
    counts = {"loads": 0, "active": 0, "max_active": 0}
    counts_lock = threading.Lock()

    class FakeLlama:
        def __init__(self, **_kwargs):
            counts["loads"] += 1

        def create_chat_completion(self, **_kwargs):
            with counts_lock:
                counts["active"] += 1
                counts["max_active"] = max(counts["max_active"], counts["active"])
            time.sleep(0.01)
            with counts_lock:
                counts["active"] -= 1
            return _codex_success("local")

    monkeypatch.setattr(campus_local_provider, "MODEL_PATH", tmp_path / "unused.gguf")
    monkeypatch.setattr(campus_local_provider, "SYSTEM_PROMPT_PATH", prompt_path)
    monkeypatch.setattr(campus_local_provider, "_model", None)
    monkeypatch.setattr(campus_local_provider, "_model_lock", threading.Lock())
    monkeypatch.setattr(campus_local_provider, "_generation_lock", threading.Lock())
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=FakeLlama))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda i: campus_local_provider.chat_completion([
                {"role": "user", "content": f"Question {i}."}
            ]),
            range(4),
        ))

    assert counts == {"loads": 1, "active": 0, "max_active": 1}
    assert len(results) == 4
