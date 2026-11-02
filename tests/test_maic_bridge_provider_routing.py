import json

import maic_bridge


def test_maic_text_stages_use_the_shared_router_with_fallbacks_enabled(monkeypatch):
    monkeypatch.setattr(maic_bridge, "CAMPUS_AI_PORT", "3310")
    env = {
        "CODEX_ONLY_PROVIDER": "true",
        "ENABLE_CODEX_PROVIDER": "false",
        "ENABLE_OMNIROUTE": "false",
        "MODEL_FALLBACK": "google:gemini-3.1-flash-lite",
    }

    maic_bridge.configure_maic_provider_router(env)

    assert env["ENABLE_CODEX_PROVIDER"] == "true"
    assert env["CODEX_ONLY_PROVIDER"] == "false"
    assert env["ENABLE_OMNIROUTE"] == "true"
    assert "MODEL_FALLBACK" not in env
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:3310/v1"
    assert env["CAMPUS_AI_GATEWAY_URL"] == "http://127.0.0.1:3310/v1/chat/completions"

    routes = json.loads(env["MODEL_ROUTES"])
    expected_stages = {
        "scene-outlines-stream",
        "scene-content",
        "scene-actions",
        "agent-profiles",
        "quiz-grade",
        "pbl-chat",
        "pbl-v2-runtime",
        "generate-classroom",
        "web-search-query-rewrite",
        "maic-agent",
    }
    assert expected_stages.issubset(routes)
    assert set(routes.values()) == {"openai:gpt-6-luna"}


def test_maic_router_setup_does_not_require_optional_provider_credentials():
    env = {}

    maic_bridge.configure_maic_provider_router(env)

    assert env["ENABLE_OMNIROUTE"] == "true"
    assert "OMNIROUTE_API_KEY" not in env
    assert "GEMINI_API_KEY" not in env
