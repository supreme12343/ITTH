import pytest

import pptcontroller as controller


@pytest.mark.parametrize(
    "environment_names",
    [
        ("GEMMA_API_KEY",),
        ("PARAKEET_API_KEY", "RIVA_API_KEY"),
        ("RERANK_API_KEY", "NEMOTRON_API_KEY"),
    ],
)
def test_provider_credentials_are_optional_and_environment_only(monkeypatch, environment_names):
    for name in environment_names:
        monkeypatch.delenv(name, raising=False)

    assert controller._optional_provider_credential(*environment_names) is None

    monkeypatch.setenv(environment_names[-1], " competition-test-token ")
    assert controller._optional_provider_credential(*environment_names) == "competition-test-token"


def test_gemma_inference_skips_network_when_unconfigured(monkeypatch):
    monkeypatch.delenv("GEMMA_API_KEY", raising=False)
    logs = []

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("Gemma request should be skipped when its key is not configured")

    monkeypatch.setattr(controller.requests, "post", unexpected_request)
    monkeypatch.setattr(controller.gui_log_queue, "put", logs.append)

    controller._perform_ai_inference("advance when the next topic begins")

    assert any("GEMMA_API_KEY" in message and "not configured" in message.lower() for message in logs)


def test_riva_worker_skips_authentication_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PARAKEET_API_KEY", raising=False)
    monkeypatch.delenv("RIVA_API_KEY", raising=False)
    logs = []

    def unexpected_auth(*_args, **_kwargs):
        pytest.fail("Riva authentication should be skipped when its key is not configured")

    monkeypatch.setattr(controller.riva.client.auth, "Auth", unexpected_auth)
    monkeypatch.setattr(controller.gui_log_queue, "put", logs.append)

    controller.riva_streaming_pipeline_worker()

    assert any("speech recognition" in message.lower() and "not configured" in message.lower() for message in logs)


def test_similarity_monitor_skips_requests_when_unconfigured(monkeypatch):
    monkeypatch.delenv("RERANK_API_KEY", raising=False)
    monkeypatch.delenv("NEMOTRON_API_KEY", raising=False)
    logs = []

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("Similarity request should be skipped when its key is not configured")

    monkeypatch.setattr(controller.requests, "post", unexpected_request)
    monkeypatch.setattr(controller.gui_log_queue, "put", logs.append)

    controller.ai_similarity_monitor_worker()

    assert any("Similarity Monitor" in message and "not configured" in message.lower() for message in logs)
