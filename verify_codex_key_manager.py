"""Run a one-prompt smoke check through the local Codex-first key manager."""

from key_manager import AiApi


def main() -> int:
    api = AiApi()
    status = api.get_status()
    codex = next((item for item in status["keys"] if item.get("label") == "codex"), None)
    if not codex or not codex.get("available"):
        print("This process cannot see a signed-in Codex CLI. Run connect_codex.py in this same shell first.")
        return 1

    response = api.chat(
        "Return the requested token exactly.",
        "Reply exactly: CODEX_KEY_MANAGER_OK",
        max_retries=1,
    )
    print("Provider:", response.model)
    print("Reply:", response.choices[0].message.content)
    if response.model != "codex-gpt-6-luna-high":
        print("The request succeeded, but it used a fallback provider instead of Codex.")
        return 1
    print("Codex key-manager smoke check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
