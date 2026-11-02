"""Open the official Codex CLI sign-in flow and wait for login to finish.

This helper delegates OAuth to the installed Codex CLI. It never reads,
prints, or copies the CLI's local authentication data.
"""

import time

from campus_codex_provider import CodexProviderError, launch_login, status


POLL_SECONDS = 2
WAIT_SECONDS = 300


def main() -> int:
    initial = status()
    if not initial["cli_found"]:
        print("Codex CLI was not found on PATH. Install it, then run this script again.")
        return 1
    if initial["logged_in"]:
        print("Codex CLI is already signed in.")
        return 0

    try:
        launch_login()
    except CodexProviderError as exc:
        print(str(exc))
        return 1

    print("Finish signing in through the Codex window or browser that opened.")
    print("Waiting for the Codex CLI to confirm sign-in...")
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        current = status()
        if current["logged_in"]:
            print("Codex sign-in confirmed. The key manager can now use your Codex account.")
            return 0

    print("Sign-in was not confirmed yet. Finish the Codex authorization and run this script again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
