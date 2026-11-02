"""Offline integration checks for Blender's shared AI routing entrypoints."""
from types import SimpleNamespace

import blender_agent


class _CapturingManager:
    ALL_POOL = "all"

    def __init__(self, response_text="routed response", error=None):
        self.response_text = response_text
        self.error = error
        self.calls = []

    def generate(self, pool_name, **kwargs):
        self.calls.append((pool_name, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.response_text), "mock-priority-provider"


def test_blender_call_ai_preserves_caller_system_and_routes_image(monkeypatch, tmp_path):
    image_path = tmp_path / "reference.png"
    image_path.write_bytes(b"mock-image-bytes")
    monkeypatch.setattr(
        blender_agent,
        "_blender_image_part",
        lambda _path: {"inline_data": {"mime_type": "image/png", "data": b"mock-image-bytes"}},
    )
    manager = _CapturingManager("```python\nimport bpy\n```")

    result = blender_agent.call_ai(
        "Create a blue cube.",
        image_path=image_path,
        scene_info="Current scene has one camera.",
        manager=manager,
    )

    assert result.engine == "mock-priority-provider"
    assert result.text.startswith("```python")
    assert result.trail == ["mock-priority-provider ✓"]
    assert len(manager.calls) == 1
    pool, request = manager.calls[0]
    assert pool == manager.ALL_POOL
    assert request["contents"][0]["role"] == "user"
    assert request["contents"][0]["parts"][0]["text"] == "Create a blue cube."
    assert request["contents"][0]["parts"][1]["inline_data"]["data"] == b"mock-image-bytes"
    assert request["config"].system_instruction.startswith(blender_agent.SYSTEM_PROMPT)
    assert "Current scene has one camera." in request["config"].system_instruction


def test_visual_audit_uses_shared_multimodal_router_and_does_not_claim_unverified(monkeypatch, tmp_path):
    screenshot = tmp_path / "viewport.png"
    screenshot.write_bytes(b"mock-screenshot-bytes")
    monkeypatch.setattr(
        blender_agent,
        "_blender_image_part",
        lambda _path: {"inline_data": {"mime_type": "image/png", "data": b"mock-screenshot-bytes"}},
    )
    manager = _CapturingManager("QUALITY_PASS")

    passed, feedback = blender_agent.audit_visual_quality(screenshot, "A blue cube.", manager=manager)

    assert passed is True
    assert feedback == "Visual quality verified."
    pool, request = manager.calls[0]
    assert pool == manager.ALL_POOL
    assert request["contents"][0]["parts"][0]["text"].startswith("You are a 3D Visual Quality Auditor")
    assert request["contents"][0]["parts"][1]["inline_data"]["data"] == b"mock-screenshot-bytes"

    unavailable = _CapturingManager(error=RuntimeError("mock provider failure detail"))
    passed, feedback = blender_agent.audit_visual_quality(screenshot, "A blue cube.", manager=unavailable)
    assert passed is None
    assert "unavailable" in feedback.lower()
    assert "mock provider failure detail" not in feedback
