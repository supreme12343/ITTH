import io
import json
from types import SimpleNamespace

import pytest

import campus_ai_gateway as gateway


def test_personal_growth_requests_use_conversation_scoped_tools(monkeypatch):
    captured = {}

    class FakeMentor:
        def __init__(self, key_manager, scope_id):
            captured["key_manager"] = key_manager
            captured["scope_id"] = scope_id

        def chat(self, history, question):
            captured["history"] = history
            captured["question"] = question
            return {"reply": "Your goal is saved.", "mode": "mentor", "tool_calls": [{"tool": "create_goal"}]}

    monkeypatch.setattr("agents.personal_growth_mentor.PersonalGrowthMentor", FakeMentor)
    body = {
        "conversationId": "mentor-conversation-123",
        "messages": [
            {"role": "system", "content": "You are the Personal Growth Mentor."},
            {"role": "user", "content": "I want to learn Python."},
            {"role": "assistant", "content": "What would you like to build?"},
            {"role": "user", "content": "Save a goal to build a quiz."},
        ],
    }
    payload = json.dumps(body).encode("utf-8")
    handler = gateway.Handler.__new__(gateway.Handler)
    handler.path = "/v1/chat/completions"
    handler.headers = {"Content-Length": str(len(payload)), "x-campus-agent-id": "personal-growth"}
    handler.rfile = io.BytesIO(payload)
    response = {}
    handler._send = lambda status, data: response.update(status=status, data=data)

    handler.do_POST()

    assert response["status"] == 200
    assert response["data"]["choices"][0]["message"]["content"] == "Your goal is saved."
    assert response["data"]["tool_calls"] == [{"tool": "create_goal"}]
    assert captured["key_manager"] is gateway.api._km
    assert captured["scope_id"] == body["conversationId"]
    assert [message.role for message in captured["history"]] == ["user", "model"]
    assert captured["question"] == "Save a goal to build a quiz."


def test_personal_growth_requires_a_conversation_id():
    body = {"messages": [{"role": "user", "content": "Save a goal."}]}
    payload = json.dumps(body).encode("utf-8")
    handler = gateway.Handler.__new__(gateway.Handler)
    handler.path = "/v1/chat/completions"
    handler.headers = {"Content-Length": str(len(payload)), "x-campus-agent-id": "personal-growth"}
    handler.rfile = io.BytesIO(payload)
    response = {}
    handler._send = lambda status, data: response.update(status=status, data=data)

    handler.do_POST()

    assert response["status"] == 400
    assert "conversation ID" in response["data"]["error"]["message"]


# ---------------------------------------------------------------------------
# Hardening regressions
# ---------------------------------------------------------------------------


def _post(path="/v1/chat/completions", body=None, headers=None):
    """Drive a single POST through the handler the way the live server does."""
    payload = json.dumps(body if body is not None else {"messages": []}).encode("utf-8")
    handler = gateway.Handler.__new__(gateway.Handler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(payload)), **(headers or {})}
    handler.rfile = io.BytesIO(payload)
    response = {}
    handler._send = lambda status, data: response.update(status=status, data=data)
    handler.do_POST()
    return response


def _get(path="/v1/status"):
    handler = gateway.Handler.__new__(gateway.Handler)
    handler.path = path
    response = {}
    handler._send = lambda status, data: response.update(status=status, data=data)
    handler.do_GET()
    return response


def test_api_is_built_lazily_but_still_exposed_as_a_module_attribute():
    """`AiApi()` no longer runs at import; `gateway.api` must still resolve."""
    assert gateway.api is gateway._get_api()
    assert gateway.api._km is not None


def test_failed_ai_core_init_does_not_expose_exception_details(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("no credentials configured")

    monkeypatch.setattr(gateway, "_api", None)
    monkeypatch.setattr(gateway, "_create_api", boom)
    monkeypatch.setattr(gateway, "_log_error", lambda *_args, **_kwargs: None)
    with pytest.raises(gateway.GatewayUnavailableError) as excinfo:
        gateway._get_api()
    message = str(excinfo.value)
    assert "no credentials configured" not in message
    assert "GEMINI_API_KEY_1" not in message
    assert message == gateway._AI_UNAVAILABLE_MESSAGE


def test_unavailable_ai_core_returns_503_not_500(monkeypatch):
    def boom():
        raise gateway.GatewayUnavailableError("provider credential detail: SECRET_SENTINEL")

    monkeypatch.setattr(gateway, "_get_api", boom)
    response = _post(body={"messages": [{"role": "user", "content": "hi"}]})
    assert response["status"] == 503
    assert response["data"]["error"]["type"] == "campus_ai_unavailable"
    assert "SECRET_SENTINEL" not in json.dumps(response["data"])


def test_provider_exception_details_are_not_returned_by_status_or_chat(monkeypatch):
    monkeypatch.setattr(gateway, "_log_error", lambda *_args, **_kwargs: None)

    def unavailable():
        raise RuntimeError("provider credential detail: SECRET_SENTINEL")

    monkeypatch.setattr(gateway, "_get_api", unavailable)
    status_response = _get("/v1/status")
    assert status_response["status"] == 503
    assert "SECRET_SENTINEL" not in json.dumps(status_response["data"])

    class FailingApi:
        def chat_with_role(self, *_args, **_kwargs):
            raise RuntimeError("upstream token: SECRET_SENTINEL")

    monkeypatch.setattr(gateway, "_get_api", lambda: FailingApi())
    chat_response = _post(body={"messages": [{"role": "user", "content": "hi"}]})
    assert chat_response["status"] == 500
    assert "SECRET_SENTINEL" not in json.dumps(chat_response["data"])


def test_missing_dependency_returns_an_actionable_503(monkeypatch):
    def boom():
        raise ModuleNotFoundError("No module named 'study_agent'", name="study_agent")

    monkeypatch.setattr(gateway, "_get_study_agent", boom)
    response = _post(
        body={"messages": [{"role": "user", "content": "hi"}]},
        headers={"x-campus-agent-id": "study"},
    )
    assert response["status"] == 503
    assert response["data"]["error"]["type"] == "campus_dependency_missing"
    assert "study_agent" in response["data"]["error"]["message"]


def test_empty_choices_from_the_provider_is_reported_clearly():
    with pytest.raises(RuntimeError, match="no choices"):
        gateway._message_text(type("_R", (), {"choices": []})())


def test_none_message_content_is_reported_clearly():
    empty = type("_R", (), {"choices": [type("_C", (), {"message": None})()]})()
    with pytest.raises(RuntimeError, match="no message content"):
        gateway._message_text(empty)


def test_good_message_content_is_passed_through():
    message = type("_M", (), {"content": "hello"})()
    response = type("_R", (), {"choices": [type("_C", (), {"message": message})()]})()
    assert gateway._message_text(response) == "hello"


def test_chat_gateway_redacts_credentials_before_calling_ai(monkeypatch):
    captured = {}

    class FakeApi:
        def chat_with_role(self, role, system, user, **kwargs):
            captured.update(role=role, system=system, user=user)
            message = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(gateway, "_get_api", lambda: FakeApi())
    response = _post(body={
        "messages": [
            {"role": "system", "content": "Help with the API key: AQ.abcdefghijklmnopqrstuvwxyz0123456789"},
            {"role": "user", "content": "Explain photosynthesis; access_token=long_fake_access_token_value"},
        ]
    })

    assert response["status"] == 200
    assert "photosynthesis" in captured["user"]
    assert "AQ.abcdefghijklmnopqrstuvwxyz0123456789" not in captured["system"]
    assert "long_fake_access_token_value" not in captured["user"]
    assert "[REDACTED]" in captured["system"]
    assert "[REDACTED]" in captured["user"]


def test_general_chat_gateway_preserves_user_and_assistant_turns(monkeypatch):
    captured = {}

    class FakeApi:
        def chat_with_role(self, role, system, user, **kwargs):
            captured.update(role=role, system=system, user=user, **kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="reply"))])

    monkeypatch.setattr(gateway, "_get_api", lambda: FakeApi())
    response = _post(body={
        "messages": [
            {"role": "system", "content": "Caller instructions."},
            {"role": "user", "content": "Question one."},
            {"role": "assistant", "content": "Earlier answer."},
            {"role": "user", "content": "Follow-up."},
        ]
    })

    assert response["status"] == 200
    assert captured["system"] == "Caller instructions."
    assert captured["user"] == "Question one.\nFollow-up."
    assert captured["conversation"] == [
        {"role": "user", "content": "Question one."},
        {"role": "assistant", "content": "Earlier answer."},
        {"role": "user", "content": "Follow-up."},
    ]


def test_general_chat_validation_returns_400_without_provider_dispatch(monkeypatch):
    calls = []

    class FakeApi:
        def chat_with_role(self, *_args, **_kwargs):
            calls.append("provider")
            raise AssertionError("invalid chat must not reach a provider")

    monkeypatch.setattr(gateway, "_get_api", lambda: FakeApi())
    response = _post(body={
        "messages": [
            {"role": "user", "content": "Question."},
            {"role": "tool", "content": "unexpected role"},
        ]
    })

    assert response["status"] == 400
    assert response["data"]["error"]["type"] == "invalid_request_error"
    assert calls == []


def test_only_top_level_operational_fields_bypass_recursive_redaction():
    credential = "AQ." + ("x" * 30)
    request = {
        "workspace": "C:/work/project",
        "conversationId": "conversation-123",
        "metadata": {
            "workspace": credential,
            "conversationId": credential,
            credential: "nested credential-like key",
        },
    }

    clean = gateway._redact_request_text(request)

    assert clean["workspace"] == request["workspace"]
    assert clean["conversationId"] == request["conversationId"]
    assert credential not in repr(clean["metadata"])
    assert "[REDACTED]" in repr(clean["metadata"])


def test_nested_structured_video_prompt_is_redacted_before_dispatch(monkeypatch):
    captured = {}
    credential = "AQ." + ("y" * 30)

    class FakeVideoAgent:
        def send_prompt(self, prompt, *, on_step, on_response):
            captured["prompt"] = prompt
            on_response("mocked response")
            return "mock-command"

        def get_bridge_status(self):
            return {"connected": True}

    monkeypatch.setattr(gateway, "_get_video_agent", lambda: FakeVideoAgent())
    response = _post("/v1/video/run", body={
        "prompt": {
            "workspace": credential,
            "conversationId": credential,
            credential: "structured prompt key",
        },
    })

    assert response["status"] == 200
    assert credential not in captured["prompt"]
    assert "[REDACTED]" in captured["prompt"]


def test_request_path_logging_redacts_query_credentials(monkeypatch):
    logged = []

    class FakeLogger:
        def info(self, message, *args):
            logged.append(message % args if args else message)

    monkeypatch.setattr(gateway, "_get_logger", lambda: FakeLogger())
    handler = gateway.Handler.__new__(gateway.Handler)
    handler.command = "POST"
    credential = "AQ." + ("z" * 30)
    handler.path = f"/v1/chat/completions?token={credential}"

    handler.log_message("unused")

    assert len(logged) == 1
    assert "POST /v1/chat/completions?token=" in logged[0]
    assert credential not in logged[0]
    assert "<redacted-key>" in logged[0]


def test_lazy_agents_are_constructed_once_under_concurrency(monkeypatch):
    """Two concurrent first requests must not double-construct the agent."""
    import threading

    built = []

    class FakeStudyAgent:
        def __init__(self, api):
            built.append(api)

    monkeypatch.setattr(gateway, "study_agent", None)
    monkeypatch.setattr("study_agent.StudyAgent", FakeStudyAgent)

    start = threading.Barrier(4)
    results = []

    def worker():
        start.wait()
        results.append(gateway._get_study_agent())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(built) == 1
    assert len({id(agent) for agent in results}) == 1


def test_secret_values_are_never_written_to_the_log(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AQ.Ab8RN6SuperSecretValue123")
    scrubbed = gateway._scrub(
        "failed with key AQ.Ab8RN6SuperSecretValue123 and AIzaSyFAKEKEY0123456789 and sk-abcdef1234567890"
    )
    assert "AQ.Ab8RN6SuperSecretValue123" not in scrubbed
    assert "AIzaSyFAKEKEY0123456789" not in scrubbed
    assert "sk-abcdef1234567890" not in scrubbed


def test_errors_are_logged_with_a_traceback_to_the_logs_directory():
    try:
        raise ValueError("diagnosable failure")
    except ValueError as exc:
        gateway._log_error("unit test context", exc)
    assert gateway.LOG_PATH.exists()
    written = gateway.LOG_PATH.read_text(encoding="utf-8", errors="replace")
    assert "unit test context" in written
    assert "diagnosable failure" in written
    assert "Traceback" in written
