from __future__ import annotations

from google.genai import types

from agents.personal_growth_mentor import PersonalGrowthMentor
from agents.personal_growth_mentor import tools as mentor_tools
from study_agent import StudyAgent


def _scripted_mentor(monkeypatch, tmp_path, scope_id, tool_name, args):
    monkeypatch.setattr(mentor_tools, "STORE_DIR", tmp_path)
    call = types.FunctionCall(name=tool_name, args=args)
    tool_response = types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=call)]))
    ])
    final_response = types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(role="model", parts=[types.Part(text="Tool action completed.")]))
    ])

    class ScriptedKeyManager:
        def __init__(self):
            self.responses = iter([tool_response, final_response])
            self.requests = []

        def generate(self, pool, **kwargs):
            assert pool == "tools"
            self.requests.append({**kwargs, "contents": list(kwargs["contents"])})
            return next(self.responses), "fake-key"

    key_manager = ScriptedKeyManager()
    mentor = PersonalGrowthMentor(key_manager, scope_id=scope_id)
    result = mentor.chat([], f"Please use {tool_name}")
    tool_part = key_manager.requests[1]["contents"][-1].parts[0]
    return result, tool_part.function_response.response


def test_goal_tools_create_check_in_and_isolate_conversations(tmp_path, monkeypatch):
    monkeypatch.setattr(mentor_tools, "STORE_DIR", tmp_path)

    saved = mentor_tools.create_goal(
        "student-a", "Learn Python", "Build a small quiz app", "Practice loops for 15 minutes"
    )
    assert saved["status"] == "success"
    goal_id = saved["artifacts"][0]["goal_id"]

    checked_in = mentor_tools.record_goal_checkin(
        "student-a", goal_id, "Finished two loop exercises", next_action="Write a tiny guessing game"
    )
    assert checked_in["status"] == "success"

    goals = mentor_tools.list_goals("student-a")
    goal = goals["artifacts"][0]
    assert goal["status"] == "active"
    assert goal["next_action"] == "Write a tiny guessing game"
    assert goal["recent_check_ins"][0]["progress_note"] == "Finished two loop exercises"
    assert mentor_tools.list_goals("student-b")["artifacts"] == []


def test_completed_goal_status_is_only_changed_when_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(mentor_tools, "STORE_DIR", tmp_path)
    goal = mentor_tools.create_goal("student", "Build a portfolio", "Publish one project", "Choose a project")
    goal_id = goal["artifacts"][0]["goal_id"]

    mentor_tools.record_goal_checkin("student", goal_id, "Published the project", status="completed")
    mentor_tools.record_goal_checkin("student", goal_id, "Reviewed what worked")

    assert mentor_tools.list_goals("student")["artifacts"][0]["status"] == "completed"


def test_mentor_executes_declared_tool_and_returns_visible_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(mentor_tools, "STORE_DIR", tmp_path)
    tool_call = types.FunctionCall(
        name="create_goal",
        args={"title": "Learn JavaScript", "outcome": "Make a webpage", "next_action": "Try one DOM tutorial"},
    )
    tool_response = types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=tool_call)]))
    ])
    final_response = types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(role="model", parts=[types.Part(text="Goal saved. Start with the tutorial.")]))
    ])

    class FakeKeyManager:
        def __init__(self):
            self.responses = iter([tool_response, final_response])

        def generate(self, pool, **kwargs):
            assert pool == "tools"
            return next(self.responses), "fake-key"

    mentor = PersonalGrowthMentor(FakeKeyManager(), scope_id="mentor-chat")
    result = mentor.chat([], "Save a goal for learning JavaScript")

    assert result["reply"] == "Goal saved. Start with the tutorial."
    assert result["tool_calls"][0]["status"] == "success"
    assert [message.role for message in result["history"]] == ["user", "model"]
    assert mentor_tools.list_goals("mentor-chat")["artifacts"][0]["title"] == "Learn JavaScript"


def test_list_goals_tool_receives_and_returns_saved_goal(tmp_path, monkeypatch):
    monkeypatch.setattr(mentor_tools, "STORE_DIR", tmp_path)
    mentor_tools.create_goal("list-chat", "Learn SQL", "Query a sample database", "Practice SELECT")

    result, tool_result = _scripted_mentor(monkeypatch, tmp_path, "list-chat", "list_goals", {"status": "active"})

    assert result["tool_calls"] == [{"tool": "list_goals", "args": {"status": "active"}, "status": "success"}]
    assert tool_result["status"] == "success"
    assert tool_result["artifacts"][0]["title"] == "Learn SQL"


def test_checkin_tool_records_progress_and_preserves_status(tmp_path, monkeypatch):
    monkeypatch.setattr(mentor_tools, "STORE_DIR", tmp_path)
    created = mentor_tools.create_goal("checkin-chat", "Try robotics", "Build a sensor demo", "Review a starter kit")
    goal_id = created["artifacts"][0]["goal_id"]

    result, tool_result = _scripted_mentor(
        monkeypatch,
        tmp_path,
        "checkin-chat",
        "record_goal_checkin",
        {"goal_id": goal_id, "progress_note": "Wired the sensor and read one value", "next_action": "Log five readings"},
    )

    saved_goal = mentor_tools.list_goals("checkin-chat")["artifacts"][0]
    assert result["tool_calls"][0]["status"] == "success"
    assert tool_result["summary"].startswith("Saved progress check-in")
    assert saved_goal["recent_check_ins"][0]["progress_note"] == "Wired the sensor and read one value"
    assert saved_goal["status"] == "active"


def test_every_declared_mentor_tool_has_an_implementation():
    declared = {declaration.name for declaration in mentor_tools.TOOL_DECLARATIONS}
    assert declared == set(mentor_tools.IMPLEMENTATIONS)


def test_task_queue_does_not_spin_on_dependencies(tmp_path, monkeypatch):
    import agent_orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "TASK_LOG", tmp_path / "tasks.jsonl")
    task_queue = orchestrator.TaskQueue()
    parent = orchestrator.Task(type=orchestrator.AgentType.TOOL, prompt="parent")
    child = orchestrator.Task(
        type=orchestrator.AgentType.PERSONAL_GROWTH,
        prompt="child",
        depends_on=[parent.id],
    )
    task_queue.add(child)
    task_queue.add(parent)

    assert task_queue.get_next_ready().id == parent.id
    assert task_queue.get_next_ready() is None
    task_queue.complete(parent.id, result={"ok": True})
    assert task_queue.get_next_ready().id == child.id


def test_study_agent_bounds_tool_calls_and_reports_unknown_tools():
    calls = [types.FunctionCall(name=f"unknown_{index}", args={}) for index in range(5)]
    tool_response = types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(
            role="model", parts=[types.Part(function_call=call) for call in calls]
        ))
    ])
    final_response = types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(role="model", parts=[types.Part(text="Recovered safely.")]))
    ])

    class FakeKeyManager:
        def __init__(self):
            self.responses = iter([tool_response, final_response])

        def generate(self, pool, **kwargs):
            assert pool == "tools"
            return next(self.responses), "fake-key"

    agent = StudyAgent(FakeKeyManager())
    answer, _history, tool_calls, _keys = agent._answer_with_tools(
        "", [types.Content(role="user", parts=[types.Part(text="make a study plan")])]
    )

    assert answer == "Recovered safely."
    assert len(tool_calls) == 4
    assert all(item["status"] == "error" for item in tool_calls)
