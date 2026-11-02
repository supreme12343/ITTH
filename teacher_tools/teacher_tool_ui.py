"""CustomTkinter teacher utilities for Campus Suite."""
from __future__ import annotations

import csv
import random
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campus_theme import get_theme_mode, get_theme_palette, watch_theme, apply_ctk_theme
from teacher_tools.ai_drafts import draft_with_fallback
# The shared Campus AI entry point stays inside background workers so optional
# provider imports do not block local teacher tools while their windows open.

DATA_DIR = ROOT / "storage"
CONFIG = {
    "lesson_planner": ("Lesson plan builder", "Draft an editable lesson plan from your learning goals.", True, [
        ("subject", "Subject and topic", "entry", ""), ("grade", "Grade / learner age", "entry", ""),
        ("duration", "Lesson length (minutes)", "entry", "45"), ("objectives", "Learning objectives", "text", ""),
        ("materials", "Available materials or constraints", "text", ""),
    ]),
    "worksheet_builder": ("Worksheet & exit ticket", "Create a classroom-ready practice sheet from a topic, not a multiple-choice quiz.", True, [
        ("subject", "Subject and topic", "entry", ""), ("grade", "Grade / learner age", "entry", ""),
        ("focus", "Skill or learning objective", "entry", ""), ("length", "Worksheet length", "select", ["Short · 10 minutes", "Standard · 20 minutes", "Extended · 35 minutes"]),
        ("include_key", "Include a teacher answer guide", "check", True),
    ]),
    "rubric_builder": ("Rubric builder", "Draft a transparent scoring rubric for an assignment.", True, [
        ("assignment", "Assignment or project", "entry", ""), ("grade", "Grade / learner age", "entry", ""),
        ("criteria", "What should be assessed?", "text", ""), ("scale", "Performance levels", "select", ["4 levels", "3 levels", "5 levels"]),
    ]),
    "differentiation_planner": ("Differentiation planner", "Adapt a lesson activity with practical supports and extensions.", True, [
        ("activity", "Lesson activity", "text", ""), ("subject", "Subject and grade", "entry", ""),
        ("needs", "Learning needs to plan for (no student names)", "text", ""),
    ]),
    "seating_chart": ("Seating chart maker", "Create a quick randomized or alphabetical seating layout.", False, [
        ("students", "Student names · one per line", "text", ""), ("rows", "Number of rows", "select", ["3", "4", "5", "6", "7", "8"]),
        ("columns", "Seats per row", "select", ["2", "3", "4", "5", "6"]), ("order", "Arrangement", "select", ["Random", "Alphabetical"]),
    ]),
    "group_maker": ("Balanced group maker", "Distribute learners into evenly sized mixed groups.", False, [
        ("students", "Students · one per line; optional Name | level (1–5)", "text", ""),
        ("groups", "Number of groups", "select", [str(n) for n in range(2, 13)]),
    ]),
    "rotation_planner": ("Station rotation planner", "Build a timed rotation for centers, labs, or small-group instruction.", False, [
        ("stations", "Station names · one per line", "text", ""), ("minutes", "Available class time (minutes)", "entry", "50"),
        ("rotation", "Minutes per rotation", "entry", "12"), ("transition", "Transition time (minutes)", "entry", "2"),
        ("start", "Start time (24-hour HH:MM)", "entry", "09:00"),
    ]),
    "conference_planner": ("Family conference notes", "Prepare a strengths-based meeting agenda and action record. Nothing is saved or sent automatically.", False, [
        ("student", "Student name or initials", "entry", ""), ("date", "Meeting date", "entry", date.today().isoformat()),
        ("strengths", "Strengths and recent wins", "text", ""), ("topics", "Topics to discuss", "text", ""),
        ("next_steps", "Possible next steps", "text", ""),
    ]),
    "participation_tracker": ("Participation log", "Record and review classroom participation locally in a CSV file.", False, [
        ("action", "Choose action", "select", ["Log participation", "View class summary"]),
        ("student", "Student name or initials", "entry", ""), ("type", "Participation type", "select", ["Contribution", "Question", "Peer support", "Presentation", "Other"]),
        ("notes", "Brief note (optional)", "entry", ""),
    ]),
    "assignment_tracker": ("Assignment tracker", "Keep a local list of tasks, classes, and due dates.", False, [
        ("action", "Choose action", "select", ["Add assignment", "View assignments"]),
        ("title", "Assignment", "entry", ""), ("class_name", "Class / subject", "entry", ""),
        ("due", "Due date (YYYY-MM-DD)", "entry", ""), ("notes", "Instructions or notes", "text", ""),
    ]),
}

def pair(key):
    return get_theme_palette("light")[key], get_theme_palette("dark")[key]

def text_value(controls, key):
    widget = controls[key]
    if isinstance(widget, ctk.CTkTextbox): return widget.get("1.0", "end-1c").strip()
    if isinstance(widget, ctk.StringVar): return widget.get().strip()
    if isinstance(widget, ctk.BooleanVar): return widget.get()
    return str(widget.get()).strip()

def run_local(tool, v):
    if tool == "seating_chart":
        names = [n.strip() for n in v["students"].splitlines() if n.strip()]
        seats = int(v["rows"]) * int(v["columns"])
        if not names: raise ValueError("Enter at least one student name.")
        if len(names) > seats: raise ValueError(f"There are {len(names)} names but only {seats} seats. Increase the chart size.")
        if v["order"] == "Random": random.SystemRandom().shuffle(names)
        else: names.sort(key=str.casefold)
        width = int(v["columns"])
        lines = [f"SEATING CHART · {v['rows']} rows × {width} seats\n"]
        names.extend(["—"] * (seats - len(names)))
        for row in range(int(v["rows"])):
            cells = names[row * width:(row + 1) * width]
            lines.append("  |  ".join(cells))
        return "\n".join(lines)
    if tool == "group_maker":
        entries = []
        for raw in v["students"].splitlines():
            if not raw.strip(): continue
            bits = raw.rsplit("|", 1)
            name = bits[0].strip(); level = 0
            if len(bits) == 2:
                try: level = int(bits[1].strip())
                except ValueError: raise ValueError(f"Skill level must be 1–5: {raw}")
                if level < 1 or level > 5: raise ValueError(f"Skill level must be 1–5: {raw}")
            entries.append((name, level))
        count = int(v["groups"])
        if not entries: raise ValueError("Enter at least one student.")
        if count > len(entries): raise ValueError("There must be at least one student per group.")
        # Deal strongest-to-weakest in alternating directions for balanced teams.
        entries.sort(key=lambda item: item[1], reverse=True)
        groups = [[] for _ in range(count)]
        for i, item in enumerate(entries):
            cycle, offset = divmod(i, count)
            index = offset if cycle % 2 == 0 else count - 1 - offset
            groups[index].append(item)
        return "BALANCED GROUPS\n\n" + "\n\n".join(f"Group {i + 1}\n" + "\n".join(f"  • {name}" for name, _ in group) for i, group in enumerate(groups))
    if tool == "rotation_planner":
        stations = [s.strip() for s in v["stations"].splitlines() if s.strip()]
        if not stations: raise ValueError("Enter at least one station.")
        minutes, rotation, transition = int(v["minutes"]), int(v["rotation"]), int(v["transition"])
        if min(minutes, rotation) < 1 or transition < 0: raise ValueError("Minutes and rotation must be positive; transition cannot be negative.")
        start = datetime.strptime(v["start"], "%H:%M")
        lines = [f"ROTATION PLAN · {minutes} minutes\n"]
        elapsed = 0
        for number, station in enumerate(stations, 1):
            end_elapsed = elapsed + rotation
            if end_elapsed > minutes: break
            begin = (start + timedelta(minutes=elapsed)).strftime("%H:%M")
            end = (start + timedelta(minutes=end_elapsed)).strftime("%H:%M")
            assignments = "  ·  ".join(f"Group {group + 1} → {stations[(group + number - 1) % len(stations)]}" for group in range(len(stations)))
            lines.append(f"Round {number}  ·  {begin}–{end}\n  {assignments}")
            elapsed = end_elapsed + (transition if number < len(stations) else 0)
        if len(lines) == 1: raise ValueError("The rotation length does not fit in the available class time.")
        if elapsed < minutes: lines.append(f"\nRemaining time: {minutes - elapsed} minutes")
        return "\n".join(lines)
    if tool == "conference_planner":
        student = v["student"] or "Student: __________________"
        return (f"FAMILY CONFERENCE · {v['date']}\n{student}\n\n"
            f"1. Start with strengths\n{v['strengths'] or '• Share a recent success or positive observation.'}\n\n"
            f"2. Topics and observations\n{v['topics'] or '• Describe specific examples and invite the family perspective.'}\n\n"
            "3. Shared next steps\n" + (v["next_steps"] or "• Agree on one achievable action at school and one at home.") +
            "\n\n4. Follow-up date: __________________\n\nWrite factual, respectful notes. Keep this record in an approved secure location.")
    if tool == "participation_tracker":
        DATA_DIR.mkdir(exist_ok=True)
        path = DATA_DIR / "teacher_participation.csv"
        if v["action"] == "Log participation":
            if not v["student"]: raise ValueError("Enter a student name or initials.")
            exists = path.exists()
            with path.open("a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not exists: writer.writerow(["date", "student", "type", "notes"])
                writer.writerow([date.today().isoformat(), v["student"], v["type"], v["notes"]])
            return f"Participation recorded locally for {v['student']} ({v['type']}).\nFile: {path}"
        if not path.exists(): return "No participation entries yet."
        totals = {}
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                key = row["student"]
                totals[key] = totals.get(key, 0) + 1
        return "PARTICIPATION COUNTS · locally stored\n\n" + "\n".join(f"{name}: {amount}" for name, amount in sorted(totals.items(), key=lambda item: item[0].casefold()))
    if tool == "assignment_tracker":
        DATA_DIR.mkdir(exist_ok=True)
        path = DATA_DIR / "teacher_assignments.csv"
        if v["action"] == "Add assignment":
            if not v["title"] or not v["class_name"]: raise ValueError("Enter both an assignment and class.")
            try: datetime.strptime(v["due"], "%Y-%m-%d")
            except ValueError: raise ValueError("Enter the due date as YYYY-MM-DD.")
            exists = path.exists()
            with path.open("a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not exists: writer.writerow(["title", "class", "due", "notes"])
                writer.writerow([v["title"], v["class_name"], v["due"], v["notes"]])
            return f"Assignment saved locally.\nFile: {path}"
        if not path.exists(): return "No assignments recorded yet."
        with path.open(encoding="utf-8", newline="") as f: rows = list(csv.DictReader(f))
        rows.sort(key=lambda row: row["due"])
        return "ASSIGNMENTS · ordered by due date\n\n" + "\n".join(f"{r['due']}  ·  {r['class']}  ·  {r['title']}" + (f"\n    {r['notes']}" if r["notes"] else "") for r in rows)
    raise ValueError("Unknown teacher tool.")

def open_tool(tool):
    title, description, uses_ai, fields = CONFIG[tool]
    ctk.set_appearance_mode(get_theme_mode())
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.title(f"{title} | Campus Suite for Teachers")
    root.geometry("820x850")
    root.minsize(620, 650)
    root.configure(fg_color=pair("BG"))
    shell = ctk.CTkFrame(root, fg_color="transparent")
    shell.pack(fill="both", expand=True, padx=24, pady=20)
    ctk.CTkLabel(shell, text="CAMPUS SUITE  /  TEACHER TOOLS", text_color=pair("TITLE"), font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 7))
    title_row = ctk.CTkFrame(shell, fg_color="transparent")
    title_row.pack(fill="x")
    mark = "◉" if tool == "group_maker" else ("▦" if tool == "seating_chart" else "✳")
    ctk.CTkLabel(title_row, text=mark, width=46, height=46, corner_radius=14, fg_color=pair("CARD"), text_color=pair("TITLE"), font=("Segoe UI", 23, "bold")).pack(side="left", padx=(0, 12))
    title_copy = ctk.CTkFrame(title_row, fg_color="transparent")
    title_copy.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(title_copy, text=title, text_color=pair("TEXT"), font=("Segoe UI", 25, "bold")).pack(anchor="w")
    ctk.CTkLabel(title_copy, text=description, text_color=pair("TEXT_MUTED"), font=("Segoe UI", 11), wraplength=680, justify="left").pack(anchor="w", pady=(2, 0))
    ctk.CTkFrame(shell, height=1, fg_color=pair("BORDER")).pack(fill="x", pady=(14, 12))
    if uses_ai:
        ctk.CTkLabel(shell, text="AI drafts use Campus Suite key_manager. Review and adapt before classroom use; avoid entering identifiable student information.", text_color=pair("TITLE"), font=("Segoe UI", 10), wraplength=680, justify="left").pack(anchor="w", pady=(0, 8))
    form = ctk.CTkScrollableFrame(shell, fg_color="transparent", scrollbar_button_color=pair("BORDER"))
    form.pack(fill="both", expand=True)
    if tool == "group_maker":
        form.grid_columnconfigure(0, weight=3)
        form.grid_columnconfigure(1, weight=1)
    controls = {}
    for key, label, kind, default in fields:
        card = ctk.CTkFrame(form, fg_color=pair("CARD"), corner_radius=12)
        if tool == "group_maker":
            card.grid(row=0, column=0 if key == "students" else 1, sticky="nsew", padx=(0, 6) if key == "students" else (6, 0), pady=4)
        else:
            card.pack(fill="x", pady=4)
        inside = ctk.CTkFrame(card, fg_color="transparent")
        inside.pack(fill="x", padx=13, pady=10)
        if kind == "check":
            var = ctk.BooleanVar(value=default)
            controls[key] = var
            ctk.CTkCheckBox(inside, text=label, variable=var, text_color=pair("TEXT"), fg_color=pair("ACCENT"), hover_color=pair("ACCENT_HOVER"), border_color=pair("BORDER")).pack(anchor="w")
        else:
            ctk.CTkLabel(inside, text=label, text_color=pair("TEXT_MUTED"), font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 5))
            if kind == "select":
                var = ctk.StringVar(value=default[0])
                controls[key] = var
                ctk.CTkOptionMenu(inside, values=default, variable=var, fg_color=pair("CARD_LIGHT"), button_color=pair("ACCENT"), button_hover_color=pair("ACCENT_HOVER"), text_color=pair("TEXT"), dropdown_fg_color=pair("CARD"), dropdown_text_color=pair("TEXT")).pack(fill="x")
            elif kind == "text":
                widget = ctk.CTkTextbox(inside, height=116 if tool in ("group_maker", "seating_chart") else 92, fg_color=pair("CARD_LIGHT"), text_color=pair("TEXT"), border_color=pair("BORDER"), border_width=1, corner_radius=10, font=("Segoe UI", 12), wrap="word")
                widget.pack(fill="x")
                if default: widget.insert("1.0", default)
                controls[key] = widget
            else:
                var = ctk.StringVar(value=default)
                controls[key] = var
                ctk.CTkEntry(inside, textvariable=var, fg_color=pair("CARD_LIGHT"), text_color=pair("TEXT"), border_color=pair("BORDER")).pack(fill="x")
    out_card = ctk.CTkFrame(shell, fg_color=pair("CARD"), corner_radius=16)
    out_card.pack(fill="both", expand=False, pady=(12, 8))
    out_heading = {"group_maker": "YOUR GROUPS", "seating_chart": "SEATING PLAN", "rotation_planner": "ROTATION SCHEDULE", "lesson_planner": "LESSON DRAFT", "worksheet_builder": "WORKSHEET DRAFT", "rubric_builder": "RUBRIC DRAFT", "differentiation_planner": "SUPPORT PLAN", "conference_planner": "MEETING NOTES", "participation_tracker": "CLASS SUMMARY", "assignment_tracker": "ASSIGNMENT LIST"}.get(tool, "RESULT")
    out_head = ctk.CTkFrame(out_card, fg_color="transparent")
    out_head.pack(fill="x", padx=15, pady=(12, 6))
    ctk.CTkLabel(out_head, text=out_heading, text_color=pair("TITLE"), font=("Segoe UI", 9, "bold")).pack(side="left")
    ctk.CTkLabel(out_head, text="Review and adjust as needed", text_color=pair("TEXT_MUTED"), font=("Segoe UI", 9)).pack(side="right")
    output = ctk.CTkTextbox(out_card, height=174 if tool in ("group_maker", "seating_chart") else 150, fg_color=pair("CARD_LIGHT"), text_color=pair("TEXT"), border_width=0, wrap="word", font=("Segoe UI", 12), corner_radius=11, spacing1=3, spacing3=3)
    output.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    output.insert("1.0", "Your plan will appear here after you run the tool.")
    output.configure(state="disabled")
    status = ctk.CTkLabel(shell, text="Ready", text_color=pair("TEXT_MUTED"), font=("Segoe UI", 10))
    status.pack(anchor="w", pady=(0, 6))
    buttons = ctk.CTkFrame(shell, fg_color="transparent")
    buttons.pack(fill="x")
    latest_result = {"text": ""}
    def write_output(text, error=False):
        visible_text = text[-18000:] or "Done."
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", visible_text)
        output.configure(state="disabled")
        status.configure(text="Check the details above" if error else "Ready to review", text_color=pair("RED") if error else pair("GREEN"))
        latest_result["text"] = "" if error else visible_text
        ask_ai_button.configure(state="normal" if not error and visible_text else "disabled")

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
            "Review the result first and remove student names or other private information before continuing.",
            parent=root,
        ):
            return
        ask_ai_button.configure(state="disabled", text="Thinking…")
        status.configure(text="Asking Campus Suite AI…", text_color=pair("TITLE"))
        def worker():
            try:
                from campus_ai import ask_campus_ai
                reply = ask_campus_ai(
                    f"You are the optional AI assistant inside the Campus Suite teacher tool {title}. "
                    "Help interpret or improve the displayed draft or result. Keep advice practical, "
                    "avoid unsupported claims, and do not claim to have changed records.",
                    f"Tool result:\n{context}\n\nTeacher question:\n{question.strip()}",
                )
                error = False
            except Exception as exc:
                reply, error = str(exc), True
            def finish():
                if error:
                    status.configure(text="Campus AI could not answer", text_color=pair("RED"))
                else:
                    write_output(f"{context}\n\nCAMPUS AI FOLLOW-UP\n{reply}")
                ask_ai_button.configure(state="normal", text="Ask AI")
            try: root.after(0, finish)
            except Exception: pass
        threading.Thread(target=worker, daemon=True).start()
    def copy_output():
        root.clipboard_clear()
        root.clipboard_append(output.get("1.0", "end-1c"))
        status.configure(text="Output copied", text_color=pair("GREEN"))
    def save_output():
        content = output.get("1.0", "end-1c").strip()
        if not content or content == "Your plan will appear here after you run the tool.":
            status.configure(text="Create a result before saving", text_color=pair("RED")); return
        destination = filedialog.asksaveasfilename(title="Save teacher tool output", defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if destination:
            try:
                Path(destination).write_text(content, encoding="utf-8")
                status.configure(text=f"Saved: {destination}", text_color=pair("GREEN"))
            except OSError as exc: write_output(str(exc), True)
    def run():
        values = {key: text_value(controls, key) for key in controls}
        required_fields = {
            "lesson_planner": ("subject", "grade", "objectives"),
            "worksheet_builder": ("subject", "grade", "focus"),
            "rubric_builder": ("assignment", "grade", "criteria"),
            "differentiation_planner": ("activity", "subject"),
        }.get(tool, ())
        missing = [field.replace("_", " ") for field in required_fields if not values[field]]
        if missing:
            write_output("Please fill in: " + ", ".join(missing) + ".", True)
            return
        if uses_ai:
            generate_button.configure(state="disabled", text="Generating…")
            status.configure(text="Drafting with Campus Suite AI…", text_color=pair("TITLE"))
            def worker():
                try:
                    result, used_fallback = draft_with_fallback(tool, values)
                    error = False
                except Exception as exc:
                    result, used_fallback, error = str(exc), False, True
                def finish(result=result, error=error, used_fallback=used_fallback):
                    write_output(result, error)
                    generate_button.configure(state="normal", text="Generate")
                    if used_fallback:
                        status.configure(text="Campus AI unavailable · offline template ready", text_color=pair("TEXT_MUTED"))
                try: root.after(0, finish)
                except Exception: pass
            threading.Thread(target=worker, daemon=True).start()
            return
        try: write_output(run_local(tool, values))
        except Exception as exc: write_output(str(exc), True)
    ask_ai_button = ctk.CTkButton(buttons, text="Ask AI", command=ask_ai_followup, width=100, fg_color=pair("CARD_LIGHT"), hover_color=pair("BORDER"), text_color=pair("TEXT"), state="disabled")
    ask_ai_button.pack(side="right", padx=(8, 0))
    ctk.CTkButton(buttons, text="Copy output", command=copy_output, width=120, fg_color=pair("CARD_LIGHT"), hover_color=pair("BORDER"), text_color=pair("TEXT")).pack(side="right", padx=(8, 0))
    ctk.CTkButton(buttons, text="Save .txt", command=save_output, width=105, fg_color=pair("CARD_LIGHT"), hover_color=pair("BORDER"), text_color=pair("TEXT")).pack(side="right", padx=(8, 0))
    generate_button = ctk.CTkButton(buttons, text="Generate" if uses_ai else "Run tool", command=run, width=150, height=42, corner_radius=10, fg_color=pair("ACCENT"), hover_color=pair("ACCENT_HOVER"), text_color=pair("BG"), font=("Segoe UI", 12, "bold"))
    generate_button.pack(side="right")
    watch_theme(root, lambda mode: (ctk.set_appearance_mode(mode), apply_ctk_theme(root, mode)))
    root.mainloop()
