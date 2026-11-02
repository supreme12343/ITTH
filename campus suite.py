"""Start the React + Electron Campus Suite desktop app with visible boot progress."""

from __future__ import annotations

import subprocess
import shutil
import threading
import os
import signal
from pathlib import Path
import tkinter as tk
from tkinter import ttk


PROJECT_ROOT = Path(__file__).resolve().parent
DESKTOP_APP = PROJECT_ROOT / "ai-agent-overlay"
STEPS = ("Check desktop setup", "Prepare the Campus Suite interface", "Open the desktop window")


def _resolve_npm_cli(npm_command: str) -> Path:
    npm_path = Path(npm_command)
    candidates = []
    if npm_path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        candidates.append(npm_path.parent / "node_modules" / "npm" / "bin" / "npm-cli.js")
    resolved = npm_path.resolve()
    if resolved.suffix.lower() == ".js":
        candidates.append(resolved)
    candidates.extend((
        npm_path.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
        resolved.parent / "npm-cli.js",
        resolved.parent / "npm" / "bin" / "npm-cli.js",
    ))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("The npm JavaScript launcher could not be located. Repair the Node.js installation and retry.")


def _npm_start_command(npm_command: str, node_command: str) -> list[str]:
    """Run npm's CLI with Node directly, without a cmd.exe/shell wrapper."""
    return [node_command, str(_resolve_npm_cli(npm_command)), "start"]


def _signal_process_group(pid: int, sig: int) -> bool:
    """Signal a POSIX process group when the current platform supports it."""
    kill_process_group = getattr(os, "killpg", None)
    if kill_process_group is None:
        return False
    try:
        kill_process_group(pid, sig)
        return True
    except OSError:
        return False


def _terminate_process_tree(process: subprocess.Popen, *, platform: str | None = None) -> None:
    """Stop and reap the startup tree when the user closes the splash early."""
    platform = platform or os.name
    if process.poll() is not None:
        return

    if platform == "nt":
        taskkill = shutil.which("taskkill.exe") or "taskkill.exe"
        try:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        if not _signal_process_group(process.pid, signal.SIGTERM):
            try: process.terminate()
            except OSError: pass

    if process.poll() is None:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if platform == "nt":
                try: process.kill()
                except OSError: pass
            elif not _signal_process_group(process.pid, signal.SIGKILL):
                try: process.kill()
                except OSError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: pass


def main() -> None:
    root = tk.Tk()
    root.title("Campus Suite · Starting")
    root.geometry("540x350")
    root.resizable(False, False)
    root.configure(bg="#111915")

    state = {"step": 0, "detail": "Checking Node.js and the desktop app files…", "failed": "", "ready": False, "process": None}
    closed = threading.Event()
    labels: list[tuple[tk.Label, tk.Label]] = []

    header = tk.Frame(root, bg="#111915")
    header.pack(fill="x", padx=28, pady=(26, 18))
    mark = tk.Label(header, text="c", bg="#d9efc4", fg="#233326", width=3, height=1, font=("Segoe UI", 16, "bold"))
    mark.pack(side="left", padx=(0, 12), ipady=5)
    title = tk.Label(header, text="Campus Suite", bg="#111915", fg="#edf3eb", font=("Segoe UI", 19, "bold"))
    title.pack(anchor="w")
    subtitle = tk.Label(header, text="Getting your workspace ready", bg="#111915", fg="#a2b0a6", font=("Segoe UI", 10))
    subtitle.pack(anchor="w", pady=(3, 0))

    panel = tk.Frame(root, bg="#1c2821", highlightthickness=1, highlightbackground="#304136")
    panel.pack(fill="both", expand=True, padx=22, pady=(0, 14))
    detail = tk.Label(panel, text=state["detail"], anchor="w", bg="#1c2821", fg="#d8e4d4", font=("Segoe UI", 10))
    detail.pack(fill="x", padx=18, pady=(17, 12))

    steps = tk.Frame(panel, bg="#1c2821")
    steps.pack(fill="x", padx=18)
    for index, name in enumerate(STEPS):
        row = tk.Frame(steps, bg="#1c2821")
        row.pack(fill="x", pady=5)
        icon = tk.Label(row, text="○", width=3, bg="#1c2821", fg="#74847a", font=("Segoe UI", 11, "bold"))
        icon.pack(side="left")
        label = tk.Label(row, text=name, anchor="w", bg="#1c2821", fg="#9ead9f", font=("Segoe UI", 10))
        label.pack(side="left")
        labels.append((icon, label))

    progress_style = ttk.Style(root)
    progress_style.theme_use("clam")
    progress_style.configure("Campus.Horizontal.TProgressbar", troughcolor="#27372f", background="#b8d99e", bordercolor="#27372f", lightcolor="#b8d99e", darkcolor="#b8d99e")
    progress = ttk.Progressbar(panel, mode="indeterminate", length=480, style="Campus.Horizontal.TProgressbar")
    progress.pack(fill="x", padx=18, pady=(15, 11))
    progress.start(12)
    foot = tk.Label(panel, text="This can take a little longer on first launch.", anchor="w", bg="#1c2821", fg="#7f9084", font=("Segoe UI", 9))
    foot.pack(fill="x", padx=18, pady=(0, 15))

    def render() -> None:
        detail.configure(text=state["failed"] or state["detail"], fg="#f0a398" if state["failed"] else "#d8e4d4")
        for index, (icon, label) in enumerate(labels):
            if state["failed"] and index == state["step"]:
                icon.configure(text="!", fg="#df7566")
                label.configure(fg="#f0a398")
            elif index < state["step"] or state["ready"]:
                icon.configure(text="✓", fg="#b8d99e")
                label.configure(fg="#d8e4d4")
            elif index == state["step"]:
                icon.configure(text="◌", fg="#b8d99e")
                label.configure(fg="#edf3eb")
            else:
                icon.configure(text="○", fg="#74847a")
                label.configure(fg="#9ead9f")

    def update(step: int, message: str) -> None:
        state["step"] = step
        state["detail"] = message
        try:
            root.after(0, render)
        except tk.TclError:
            pass

    def finish_ready() -> None:
        state["ready"] = True
        state["step"] = len(STEPS)
        state["detail"] = "Campus Suite is ready."
        render()
        root.after(250, root.destroy)

    def close_splash() -> None:
        closed.set()
        process = state["process"]
        if process is not None and process.poll() is None and not state["ready"]:
            _terminate_process_tree(process)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_splash)

    def boot() -> None:
        if closed.is_set():
            return
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        node = shutil.which("node.exe") or shutil.which("node")
        electron_marker = DESKTOP_APP / "node_modules" / "electron" / "path.txt"
        if npm is None:
            state["failed"] = "Node.js and npm were not found. Install Node.js, then reopen Campus Suite."
            root.after(0, render)
            return
        if node is None:
            state["failed"] = "Node.js was not found. Install Node.js, then reopen Campus Suite."
            root.after(0, render)
            return
        if not electron_marker.exists():
            state["failed"] = "Desktop setup is incomplete. Install dependencies in ai-agent-overlay first."
            root.after(0, render)
            return
        if closed.is_set():
            return

        update(1, "Checking the app and building its interface…")
        log_path = PROJECT_ROOT / "logs" / "campus-suite-desktop.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            command = _npm_start_command(npm, node)
            if os.name == "nt":
                process = subprocess.Popen(
                    command,
                    cwd=DESKTOP_APP,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            else:
                process = subprocess.Popen(
                    command,
                    cwd=DESKTOP_APP,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
            state["process"] = process
            if closed.is_set():
                _terminate_process_tree(process)
                return
            tail: list[str] = []
            with log_path.open("a", encoding="utf-8") as log:
                if process.stdout:
                    for raw_line in process.stdout:
                        if closed.is_set():
                            break
                        line = raw_line.rstrip()
                        log.write(line + "\n")
                        log.flush()
                        lowered = line.lower()
                        if "tsc -b" in lowered:
                            update(1, "Type-checking the Campus Suite screens…")
                        elif "vite build" in lowered:
                            update(1, "Bundling the interface and desktop assets…")
                        elif "campus_ui_build_required" in lowered:
                            update(1, "Preparing the interface for first use or after an update…")
                        elif "campus_ui_cache_hit" in lowered:
                            update(1, "Reusing the saved Campus Suite interface…")
                        elif "campus_ui_build_ready" in lowered:
                            update(1, "Interface prepared and saved for next time…")
                        elif "campus_ui_launching" in lowered:
                            update(2, "Starting the native window and loading your tools…")
                        elif "campus_suite_ready" in lowered or "campus_suite_already_running" in lowered:
                            state["ready"] = True
                            root.after(0, finish_ready)
            code = process.wait()
            if not state["ready"] and not closed.is_set():
                state["failed"] = "Campus Suite could not start. See logs/campus-suite-desktop.log for details."
                state["detail"] = "The desktop process exited before its window became ready."
                root.after(0, render)
        except OSError as error:
            state["failed"] = f"Could not start the desktop app: {error}"
            root.after(0, render)

    threading.Thread(target=boot, name="campus-suite-boot", daemon=True).start()
    root.mainloop()

    process = state["process"]
    if process is not None and state["ready"]:
        process.wait()


if __name__ == "__main__":
    main()
