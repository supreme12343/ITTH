"""Open the Campus Suite agent workspace as soon as the native window is ready."""
from __future__ import annotations

import html
import subprocess
import threading


LOADING_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;
background:#111915;color:#edf3eb;font-family:Segoe UI,Arial,sans-serif}
main{width:min(480px,calc(100vw - 48px));padding:38px;border:1px solid #34443a;
border-radius:22px;background:#1c2821;box-shadow:0 24px 70px #0003}
.mark{display:grid;place-items:center;width:48px;height:48px;border-radius:16px;
background:#d9efc4;color:#233326;font-size:26px;font-weight:800}
.eyebrow{margin:27px 0 10px;color:#b8d5a8;font-size:11px;font-weight:700;
letter-spacing:1.8px}h1{margin:0;font-size:29px;line-height:1.15;letter-spacing:-.7px}
p{color:#aab9ac;line-height:1.6;font-size:14px;margin:13px 0 0}
.progress{height:5px;overflow:hidden;margin-top:30px;border-radius:5px;background:#34443a}
.progress::before{content:"";display:block;width:40%;height:100%;border-radius:5px;
background:#d9efc4;animation:move 1.6s ease-in-out infinite alternate}
@keyframes move{from{transform:translateX(-100%)}to{transform:translateX(250%)}}
@media(prefers-reduced-motion:reduce){.progress::before{animation:none;width:100%}}
</style></head><body><main><div class="mark">✳</div>
<div class="eyebrow">CAMPUS SUITE / AI AGENTS</div>
<h1>Preparing your workspace</h1>
<p>The first launch prepares and saves the workspace build. Later launches reuse it and should start faster.</p>
<div class="progress" role="progressbar" aria-label="Loading Agent Workspace"></div>
</main></body></html>"""


def _error_html(message: str) -> str:
    safe_message = html.escape(message)
    return LOADING_HTML.replace(
        '<h1>Preparing your workspace</h1>',
        '<h1>Agent Workspace could not open</h1>',
    ).replace(
        '<p>The first launch prepares and saves the workspace build. Later launches reuse it and should start faster.</p>',
        f'<p>{safe_message}</p><p>Close this window and try again after checking the local server.</p>',
    ).replace('<div class="progress" role="progressbar" aria-label="Loading Agent Workspace"></div>', '')


def _load_workspace(window, state: dict, closed: threading.Event) -> None:
    process: subprocess.Popen | None = None
    try:
        from maic_bridge import start_maic_server

        process, url = start_maic_server(ready_path="/agents", fast_startup=True)
        if closed.is_set():
            if process is not None and process.poll() is None:
                process.terminate()
            return
        state["process"] = process
        window.load_url(f"{url}/agents")
    except Exception as error:
        if closed.is_set():
            return
        window.load_html(_error_html(str(error)))


def main() -> None:
    closed = threading.Event()
    state: dict[str, subprocess.Popen | None] = {"process": None}
    try:
        import webview

        window = webview.create_window(
            "Campus Suite · Agent Workspace",
            html=LOADING_HTML,
            width=1440,
            height=960,
            min_size=(1024, 700),
            background_color="#111915",
        )
        if window is None:
            raise RuntimeError("The Agent Workspace window could not be created.")
        webview.start(_load_workspace, (window, state, closed))
    except Exception as error:
        from tkinter import messagebox

        messagebox.showerror("Campus Suite — AI Agents", str(error))
    finally:
        closed.set()
        process = state["process"]
        if process is not None and process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    main()
