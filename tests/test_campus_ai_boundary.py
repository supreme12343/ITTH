"""Outbound prompt sanitization and offline interview content regressions."""
from types import SimpleNamespace


def test_ask_campus_ai_redacts_credentials_but_keeps_ordinary_prose(monkeypatch):
    import key_manager
    from campus_ai import ask_campus_ai

    sent = {}

    class _FakeAiApi:
        def chat_with_role(self, role, system, user, **kwargs):
            sent.update(role=role, system=system, user=user, kwargs=kwargs)
            message = SimpleNamespace(content="A helpful answer")
            choice = SimpleNamespace(message=message)
            return SimpleNamespace(choices=[choice])

    monkeypatch.setattr(key_manager, "AiApi", _FakeAiApi)
    credential = "AQ.abcdefghijklmnopqrstuvwxyz0123456789"
    reply = ask_campus_ai(
        "Help with this lesson plan. API key: " + credential,
        "The learner's project is about science. access_token=long_fake_access_token_value",
    )

    assert reply == "A helpful answer"
    assert "lesson plan" in sent["system"]
    assert "project is about science" in sent["user"]
    assert credential not in sent["system"]
    assert "long_fake_access_token_value" not in sent["user"]
    assert "[REDACTED]" in sent["system"]
    assert "[REDACTED]" in sent["user"]
    assert sent["kwargs"]["max_retries"] == 1


def test_offline_interview_bank_covers_requested_desktop_count():
    from python_tools.interview_practice import fallback_questions

    for role in ("general", "software", "data", "product"):
        questions = fallback_questions(role, 10)
        assert len(questions) == 10
        assert len(set(questions)) == 10
        assert all(question.strip() for question in questions)
