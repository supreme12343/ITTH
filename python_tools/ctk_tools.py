"""Shared CustomTkinter windows for the independent Campus Suite utilities."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Import the shared Campus AI entry point inside the worker so unused follow-up
# AI never adds provider imports to a local tool's startup path.
CONFIG = {
    "resume_match": ("Resume matcher", "Compare resume text with a job description and spot useful keywords.", [
        ("resume", "Resume text file", "file", "", "resume"), ("job_description", "Job description text file", "file", "", "job"),
    ]),
    "job_tracker": ("Job application tracker", "Keep application notes in a local CSV file on this computer.", [
        ("action", "Choose an action", "select", "Add application", ["Add application", "View applications"]),
        ("company", "Company", "entry", "", None), ("role", "Role", "entry", "", None),
        ("status", "Status", "select", "Applied", ["Interested", "Applied", "Interview", "Offer", "Rejected", "Withdrawn"]),
        ("link", "Job link (optional)", "entry", "", None), ("notes", "Notes (optional)", "text", "", None),
    ]),
    "interview_practice": ("Interview practice", "Generate fresh role-specific questions, then ask AI for answer feedback or follow-ups.", [
        ("role", "Role focus", "select", "general", ["general", "software", "data", "product"]),
        ("target_role", "Job title or specialization (optional)", "entry", "", None),
        ("count", "Number of questions", "select", "5", [str(n) for n in range(1, 11)]), ("shuffle", "Shuffle the questions", "check", False, None),
    ]),
    "duplicate_finder": ("Duplicate finder", "Find identical files by content. This tool never deletes files.", [
        ("folder", "Folder to scan", "directory", "", "folder"), ("recursive", "Include subfolders", "check", False, None),
    ]),
    "csv_cleaner": ("CSV cleaner", "Trim cell whitespace and remove blank and duplicate rows.", [
        ("input", "Source CSV file", "file", "", "csv"), ("output", "Save cleaned CSV as", "save", "", "csv"), ("delimiter", "Delimiter", "entry", ",", None),
    ]),
    "json_helper": ("JSON helper", "Validate, format, or minify a JSON file.", [
        ("input", "JSON file", "file", "", "json"), ("output", "Output file (optional)", "save", "", "json"), ("minify", "Minify JSON", "check", False, None),
    ]),
    "backup_snapshot": ("Folder backup", "Preview a dated backup location before making a copy.", [
        ("source", "Folder to back up", "directory", "", "folder"), ("to", "Backup parent folder", "directory", str(ROOT / "backups"), "folder"),
        ("apply", "Create the backup after preview", "check", False, None),
    ]),
    "api_tester": ("REST API tester", "Send a request to an API and inspect its status, response time, headers, and body.", [
        ("method", "HTTP method", "select", "GET", ["GET", "POST", "PUT", "PATCH", "DELETE"]),
        ("url", "Request URL", "entry", "", None), ("headers", "Headers (one Name: value per line)", "text", "", None),
        ("body", "Request body (optional JSON or text)", "text", "", None), ("timeout", "Timeout in seconds", "select", "15", ["5", "10", "15", "30"]),
    ]),
    "repo_readiness": ("Project readiness check", "Check a project folder for essentials before sharing it or applying for roles.", [
        ("folder", "Project folder", "directory", "", "folder"),
    ]),
    "secret_scanner": ("GitHub secret scanner", "Look for accidentally exposed credentials before you publish a project.", [
        ("folder", "Project folder to scan", "directory", "", "folder"),
    ]),
}

def _pair(key):
    # The command-line entry points share this module, so keep the Tk theme
    # module out of headless imports too.
    from campus_theme import get_theme_palette

    return (get_theme_palette("light")[key], get_theme_palette("dark")[key])

def _cli_args(tool, values):
    if tool == "resume_match": return [values["resume"], values["job_description"]], None
    if tool == "job_tracker":
        action = ["list" if values["action"] == "View applications" else "add"]
        args = ["--db", str(ROOT / "storage" / "job_applications.csv"), *action]
        if action[-1] == "add": args += [values["company"], values["role"], "--status", values["status"], "--link", values["link"], "--notes", values["notes"]]
        return args, None
    if tool == "interview_practice": return ["--role", values["role"], "--count", values["count"], "--list", *( ["--shuffle"] if values["shuffle"] else [])], None
    if tool == "duplicate_finder": return [values["folder"], *( ["--recursive"] if values["recursive"] else [])], None
    if tool == "csv_cleaner": return [values["input"], values["output"], "--delimiter", values["delimiter"] or ","], None
    if tool == "json_helper": return [values["input"], *( ["--output", values["output"]] if values["output"].strip() else []), *( ["--minify"] if values["minify"] else [])], None
    if tool == "backup_snapshot": return [values["source"], "--to", values["to"], *( ["--apply"] if values["apply"] else [])], None
    if tool == "api_tester": return [values["method"], values["url"], "--headers", values["headers"], "--body", values["body"], "--timeout", values["timeout"]], None
    if tool in ("repo_readiness", "secret_scanner"): return [values["folder"]], None
    raise ValueError("Unknown tool")

def launch_tool(tool):
    # GUI dependencies are needed only for normal desktop launches. In
    # particular, `--cli --help` must work on machines without CustomTkinter.
    import customtkinter as ctk
    from tkinter import filedialog, messagebox, simpledialog
    from campus_theme import apply_ctk_theme, get_theme_mode, watch_theme

    title, subtitle, fields = CONFIG[tool]
    ctk.set_appearance_mode(get_theme_mode())
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.title(f"{title} | Campus Suite")
    root.geometry("880x780" if tool == "resume_match" else "820x780")
    root.minsize(620, 620)
    root.configure(fg_color=_pair("BG"))

    shell = ctk.CTkFrame(root, fg_color="transparent")
    shell.pack(fill="both", expand=True, padx=26, pady=22)
    ctk.CTkLabel(shell, text="CAMPUS SUITE  /  TOOLBOX", text_color=_pair("TITLE"), font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 7))
    title_row = ctk.CTkFrame(shell, fg_color="transparent")
    title_row.pack(fill="x")
    tool_mark = {"resume_match": "↔", "duplicate_finder": "◇", "api_tester": "↗"}.get(tool, "✳")
    ctk.CTkLabel(title_row, text=tool_mark, width=46, height=46, corner_radius=14, fg_color=_pair("CARD"), text_color=_pair("TITLE"), font=("Segoe UI", 24, "bold")).pack(side="left", padx=(0, 12))
    title_copy = ctk.CTkFrame(title_row, fg_color="transparent")
    title_copy.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(title_copy, text=title, text_color=_pair("TEXT"), font=("Segoe UI", 25, "bold")).pack(anchor="w")
    ctk.CTkLabel(title_copy, text=subtitle, text_color=_pair("TEXT_MUTED"), font=("Segoe UI", 11), wraplength=710, justify="left").pack(anchor="w", pady=(2, 0))
    ctk.CTkFrame(shell, height=1, fg_color=_pair("BORDER")).pack(fill="x", pady=(15, 13))

    form = ctk.CTkScrollableFrame(shell, fg_color="transparent", scrollbar_button_color=_pair("BORDER"), corner_radius=0)
    form.pack(fill="both", expand=True)
    values, rows = {}, {}

    # Resume matching is a comparison task, so show its two source documents
    # as a pair instead of making the user scan a long generic form.
    comparison = ctk.CTkFrame(form, fg_color="transparent") if tool == "resume_match" else None
    if comparison:
        comparison.pack(fill="x")
        for column in range(2):
            comparison.grid_columnconfigure(column, weight=1, uniform="documents")

    def refresh_visibility(*_):
        for key, row in rows.items():
            visible = True
            if tool == "job_tracker" and key not in ("action",) and values["action"].get() == "View applications": visible = False
            if visible: row.pack(fill="x", pady=5)
            else: row.pack_forget()

    for key, label, kind, default, extra in fields:
        parent = comparison if comparison else form
        row = ctk.CTkFrame(parent, fg_color=_pair("CARD"), corner_radius=15)
        rows[key] = row
        inner = ctk.CTkFrame(row, fg_color="transparent")
        if comparison:
            row.grid(row=0, column=0 if key == "resume" else 1, sticky="nsew", padx=(0, 6) if key == "resume" else (6, 0), pady=4)
            ctk.CTkLabel(inner, text="01  ·  YOUR RESUME" if key == "resume" else "02  ·  ROLE TO MATCH", text_color=_pair("TITLE"), font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 8))
        else:
            row.pack(fill="x", pady=5)
        inner.pack(fill="x", padx=16, pady=14)
        if kind == "check":
            var = ctk.BooleanVar(value=default)
            values[key] = var
            ctk.CTkCheckBox(inner, text=label, variable=var, text_color=_pair("TEXT"), fg_color=_pair("ACCENT"), hover_color=_pair("ACCENT_HOVER"), border_color=_pair("BORDER"), checkmark_color=_pair("BG")).pack(anchor="w")
        else:
            ctk.CTkLabel(inner, text=label, text_color=_pair("TEXT_MUTED"), font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))
            if kind == "select":
                var = ctk.StringVar(value=default)
                values[key] = var
                widget = ctk.CTkOptionMenu(inner, values=extra, variable=var, fg_color=_pair("CARD_LIGHT"), button_color=_pair("ACCENT"), button_hover_color=_pair("ACCENT_HOVER"), text_color=_pair("TEXT"), dropdown_fg_color=_pair("CARD"), dropdown_text_color=_pair("TEXT"), command=refresh_visibility)
                widget.pack(fill="x")
            elif kind == "text":
                widget = ctk.CTkTextbox(inner, height=80, fg_color=_pair("CARD_LIGHT"), text_color=_pair("TEXT"), border_color=_pair("BORDER"), border_width=1, corner_radius=9)
                values[key] = widget
                widget.pack(fill="x")
            else:
                entry_row = ctk.CTkFrame(inner, fg_color="transparent")
                entry_row.pack(fill="x")
                var = ctk.StringVar(value=default)
                values[key] = var
                widget = ctk.CTkEntry(entry_row, textvariable=var, show="●" if kind == "password" else "", fg_color=_pair("CARD_LIGHT"), text_color=_pair("TEXT"), border_color=_pair("BORDER"), placeholder_text="Choose a file or enter a path" if kind in ("file", "save", "directory") else "")
                widget.pack(side="left", fill="x", expand=True)
                if kind in ("file", "save", "directory"):
                    def browse(field=key, path_kind=extra, variable=var, field_kind=kind):
                        if path_kind == "folder": selected = filedialog.askdirectory(title="Choose a folder")
                        elif field_kind == "save": selected = filedialog.asksaveasfilename(title="Choose output file", defaultextension={"csv": ".csv", "json": ".json"}.get(path_kind, ""))
                        else:
                            patterns = {"csv": [("CSV files", "*.csv"), ("All files", "*.*")], "json": [("JSON files", "*.json"), ("All files", "*.*")], "resume": [("Text files", "*.txt"), ("All files", "*.*")], "job": [("Text files", "*.txt"), ("All files", "*.*")]}
                            selected = filedialog.askopenfilename(title="Choose a file", filetypes=patterns.get(path_kind, [("All files", "*.*")]))
                        if selected: variable.set(selected)
                    ctk.CTkButton(entry_row, text="Browse", width=76, command=browse, fg_color=_pair("CARD_LIGHT"), hover_color=_pair("BORDER"), text_color=_pair("TEXT")).pack(side="left", padx=(8, 0))
        if tool == "duplicate_finder" and key == "recursive":
            ctk.CTkLabel(inner, text="Also inspect folders inside this location", text_color=_pair("TEXT_MUTED"), font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 0))

    result_card = ctk.CTkFrame(shell, fg_color=_pair("CARD"), corner_radius=16)
    result_card.pack(fill="both", expand=False, pady=(13, 9))
    result_heading = {"resume_match": "MATCH REPORT", "duplicate_finder": "SCAN FINDINGS", "api_tester": "RESPONSE", "job_tracker": "APPLICATIONS", "csv_cleaner": "CLEANUP SUMMARY", "json_helper": "VALIDATION RESULT", "backup_snapshot": "BACKUP PREVIEW"}.get(tool, "RESULT")
    result_head = ctk.CTkFrame(result_card, fg_color="transparent")
    result_head.pack(fill="x", padx=16, pady=(13, 6))
    ctk.CTkLabel(result_head, text=result_heading, text_color=_pair("TITLE"), font=("Segoe UI", 9, "bold")).pack(side="left")
    ctk.CTkLabel(result_head, text="Updates when you run the tool", text_color=_pair("TEXT_MUTED"), font=("Segoe UI", 9)).pack(side="right")
    output = ctk.CTkTextbox(result_card, height=154 if tool == "resume_match" else 132, fg_color=_pair("CARD_LIGHT"), text_color=_pair("TEXT"), border_width=0, wrap="word", font=("Segoe UI", 12), corner_radius=11, spacing1=3, spacing3=3)
    output.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    output.insert("1.0", "Your findings will appear here after you run the tool.")
    output.configure(state="disabled")
    status = ctk.CTkLabel(shell, text="Ready", text_color=_pair("TEXT_MUTED"), font=("Segoe UI", 10))
    status.pack(anchor="w", pady=(0, 8))

    def set_output(text, error=False):
        visible_text = text[-12000:] or "Done."
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", visible_text)
        output.configure(state="disabled")
        status.configure(text="Check the details above" if error else "Ready to review", text_color=_pair("RED") if error else _pair("GREEN"))
        latest_result["text"] = "" if error else visible_text

    latest_result = {"text": ""}

    def ask_ai_followup():
        context = latest_result["text"]
        if not context:
            return
        question = simpledialog.askstring("Ask Campus AI", "What would you like help with?", parent=root)
        if not question or not question.strip():
            return
        if not messagebox.askyesno(
            "Send result to Campus AI?",
            "This sends the result shown in this window and your question to the configured Campus Suite AI provider. "
            "Review the result first and remove private information before continuing.",
            parent=root,
        ):
            return
        ask_ai_button.configure(state="disabled", text="Thinking…")
        status.configure(text="Asking Campus Suite AI…", text_color=_pair("TITLE"))

        def worker():
            try:
                from campus_ai import ask_campus_ai
                reply = ask_campus_ai(
                    f"You are the optional AI assistant inside the Campus Suite {title}. "
                    "Explain or help with the user's tool result. Be clear about uncertainty. "
                    "Do not claim to have changed files or performed actions.",
                    f"Tool result:\n{context}\n\nUser question:\n{question.strip()}",
                )
                error = False
            except Exception as exc:
                reply, error = str(exc), True
            def finish():
                if error:
                    status.configure(text="Campus AI could not answer", text_color=_pair("RED"))
                else:
                    combined = f"{context}\n\nCAMPUS AI FOLLOW-UP\n{reply}"
                    set_output(combined)
                ask_ai_button.configure(state="normal", text="Ask AI")
            try:
                root.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def run():
        raw = {key: (value.get("1.0", "end-1c") if isinstance(value, ctk.CTkTextbox) else value.get()) for key, value in values.items()}
        if tool == "job_tracker" and raw["action"] == "Add application" and (not raw["company"].strip() or not raw["role"].strip()):
            set_output("Enter both a company and role to save an application.", True); return
        for required in {"resume_match": ["resume", "job_description"], "duplicate_finder": ["folder"], "csv_cleaner": ["input", "output"], "json_helper": ["input"], "backup_snapshot": ["source"], "api_tester": ["url"], "repo_readiness": ["folder"], "secret_scanner": ["folder"]}.get(tool, []):
            if not raw[required].strip(): set_output(f"Choose or enter {required.replace('_', ' ')} first.", True); return
        try: args, stdin = _cli_args(tool, raw)
        except (KeyError, ValueError) as error: set_output(str(error), True); return
        if tool == "interview_practice":
            run_button.configure(state="disabled", text="Generating…")
            ask_ai_button.configure(state="disabled")
            set_output("Generating fresh interview questions with Campus Suite AI…")
            status.configure(text="Using the configured Campus Suite AI provider…", text_color=_pair("TITLE"))
            def generate_interview():
                offline_fallback = False
                try:
                    from python_tools.interview_practice import fallback_questions, generate_ai_questions
                    questions = generate_ai_questions(raw["role"], raw["target_role"], int(raw["count"]))
                    if raw["shuffle"]:
                        import random
                        random.SystemRandom().shuffle(questions)
                    focus = raw["target_role"].strip() or raw["role"].title()
                    result = f"AI-GENERATED INTERVIEW PRACTICE · {focus}\n\n" + "\n\n".join(
                        f"{index}. {question}" for index, question in enumerate(questions, 1)
                    )
                except Exception:
                    from python_tools.interview_practice import fallback_questions
                    questions = fallback_questions(raw["role"], int(raw["count"]))
                    if raw["shuffle"]:
                        import random
                        random.SystemRandom().shuffle(questions)
                    offline_fallback = True
                if offline_fallback:
                    focus = raw["target_role"].strip() or raw["role"].title()
                    result = (
                        f"OFFLINE INTERVIEW PRACTICE · {focus}\n\n"
                        + "\n\n".join(f"{index}. {question}" for index, question in enumerate(questions, 1))
                        + "\n\nCampus Suite AI is unavailable. These built-in questions are ready for practice."
                    )
                try:
                    root.after(0, lambda: (
                        set_output(result),
                        run_button.configure(state="normal", text="Generate questions"),
                        ask_ai_button.configure(state="normal"),
                        status.configure(
                            text=("Campus AI unavailable · built-in questions ready" if offline_fallback else "Fresh AI questions ready"),
                            text_color=_pair("TEXT_MUTED" if offline_fallback else "TITLE"),
                        ),
                    ))
                except Exception:
                    pass
            threading.Thread(target=generate_interview, daemon=True).start()
            return
        script = Path(sys.argv[0]).resolve()
        command = [sys.executable, str(script), "--cli", *args]
        run_button.configure(state="disabled", text="Working…")
        set_output("Working…")
        status.configure(text="Running your tool…", text_color=_pair("TITLE"))
        def worker():
            try:
                completed = subprocess.run(command, input=stdin, text=True, capture_output=True, cwd=ROOT, timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                result = completed.stdout.strip() or completed.stderr.strip() or ("Done." if completed.returncode == 0 else f"Exited with code {completed.returncode}.")
                error = completed.returncode != 0
            except (OSError, subprocess.TimeoutExpired) as exc:
                result, error = str(exc), True
            try:
                root.after(0, lambda: (
                    set_output(result, error),
                    run_button.configure(state="normal", text="Run tool"),
                    ask_ai_button.configure(state="normal" if not error else "disabled"),
                ))
            except Exception: pass
        threading.Thread(target=worker, daemon=True).start()

    run_button = ctk.CTkButton(shell, text="Generate questions" if tool == "interview_practice" else "Run tool", command=run, width=150, height=42, corner_radius=11,
        fg_color=_pair("ACCENT"), hover_color=_pair("ACCENT_HOVER"), text_color=_pair("BG"), font=("Segoe UI", 12, "bold"))
    run_button.pack(side="right")
    ask_ai_button = ctk.CTkButton(shell, text="Ask AI", command=ask_ai_followup, width=110, height=42,
        corner_radius=11, fg_color=_pair("CARD_LIGHT"), hover_color=_pair("BORDER"),
        text_color=_pair("TEXT"), state="disabled")
    ask_ai_button.pack(side="right", padx=(0, 8))
    watch_theme(root, lambda mode: (ctk.set_appearance_mode(mode), apply_ctk_theme(root, mode)))
    root.mainloop()

def run_tool(tool, cli_main):
    """Keep CLI use available while opening the themed UI for normal launches."""
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        cli_main()
    else:
        launch_tool(tool)
