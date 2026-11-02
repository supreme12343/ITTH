"""AI-backed tools use the shared boundary first and stay usable offline."""

import sys


def test_interview_cli_uses_shared_ai_by_default(monkeypatch, capsys):
    import campus_ai
    from python_tools.interview_practice import main

    calls = []

    def fake_ask(system, prompt, **kwargs):
        calls.append((system, prompt, kwargs))
        return '["A tailored backend question?", "A second backend question?"]'

    monkeypatch.setattr(campus_ai, "ask_campus_ai", fake_ask)
    monkeypatch.setattr(sys, "argv", [
        "interview_practice", "--role", "software", "--target-role", "backend engineer",
        "--count", "2", "--list",
    ])

    main()

    output = capsys.readouterr().out
    assert "AI-GENERATED INTERVIEW PRACTICE · backend engineer" in output
    assert "A tailored backend question?" in output
    assert len(calls) == 1
    assert "backend engineer" in calls[0][1]
    assert calls[0][2]["max_tokens"] > 0


def test_interview_cli_labels_local_fallback_when_ai_fails(monkeypatch, capsys):
    import campus_ai
    from python_tools.interview_practice import main

    monkeypatch.setattr(campus_ai, "ask_campus_ai", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(sys, "argv", [
        "interview_practice", "--role", "data", "--count", "6", "--list",
    ])

    main()

    output = capsys.readouterr().out
    assert "OFFLINE INTERVIEW PRACTICE · Data" in output
    assert "Campus Suite AI is unavailable" in output
    assert output.count("\n1.") == 1
    assert output.count("\n6.") == 1


def test_interview_cli_offline_flag_skips_provider(monkeypatch, capsys):
    import campus_ai
    from python_tools.interview_practice import main

    monkeypatch.setattr(campus_ai, "ask_campus_ai", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider should not be called")))
    monkeypatch.setattr(sys, "argv", ["interview_practice", "--offline", "--count", "2", "--list"])

    main()

    output = capsys.readouterr().out
    assert "OFFLINE INTERVIEW PRACTICE" in output
    assert "Offline mode is enabled" in output


def test_teacher_drafts_use_ai_first_then_provide_offline_templates(monkeypatch):
    import campus_ai
    from teacher_tools.ai_drafts import draft_with_fallback

    values_by_tool = {
        "lesson_planner": {
            "subject": "Science", "grade": "7", "duration": "45",
            "objectives": "Explain evaporation", "materials": "Water and a clear cup",
        },
        "worksheet_builder": {
            "subject": "Science", "grade": "7", "focus": "evaporation",
            "length": "Short · 10 minutes", "include_key": True,
        },
        "rubric_builder": {
            "assignment": "Lab report", "grade": "7",
            "criteria": "Evidence\nReasoning", "scale": "4 levels",
        },
        "differentiation_planner": {
            "subject": "Science / grade 7", "activity": "Explain evaporation",
            "needs": "Vocabulary preview",
        },
    }

    calls = []
    monkeypatch.setattr(campus_ai, "ask_campus_ai", lambda *_args, **_kwargs: calls.append("ai") or "Fresh AI draft")
    for tool, values in values_by_tool.items():
        result, used_fallback = draft_with_fallback(tool, values)
        assert result == "Fresh AI draft"
        assert used_fallback is False
    assert calls == ["ai"] * len(values_by_tool)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(campus_ai, "ask_campus_ai", unavailable)
    for tool, values in values_by_tool.items():
        draft, used_fallback = draft_with_fallback(tool, values)
        assert used_fallback is True
        assert draft.startswith("OFFLINE TEMPLATE · Campus Suite AI is unavailable")
        assert "Review and adapt before classroom use." in draft
        assert len(draft) > 250
    rubric, _ = draft_with_fallback("rubric_builder", values_by_tool["rubric_builder"])
    assert "Evidence · 50 points maximum" in rubric
    assert "Reasoning · 50 points maximum" in rubric
