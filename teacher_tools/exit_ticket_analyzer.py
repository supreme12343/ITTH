"""Summarize anonymous exit-ticket answers to spot questions to revisit."""
from __future__ import annotations

import csv
import sys
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from campus_theme import RoundedTkButton, get_theme_palette, sync_native_theme, watch_theme
from campus_ai import ask_about_result


def _key(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def analyze_responses(source: str | Path) -> dict:
    """Aggregate CSV rows by question; student and other columns are ignored."""
    path = Path(source)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("The CSV needs a header row with question, answer, and expected columns.")
        headers = {}
        for raw_name in reader.fieldnames:
            if raw_name:
                name = raw_name.strip().casefold()
                if name in headers:
                    raise ValueError(f"Duplicate CSV column: {raw_name}")
                headers[name] = raw_name
        required = {"question", "answer", "expected"}
        missing = required - headers.keys()
        if missing:
            raise ValueError(f"CSV needs question, answer, and expected columns; missing: {', '.join(sorted(missing))}.")

        grouped = {}
        skipped = 0
        rows_read = 0
        for row in reader:
            rows_read += 1
            prompt = str(row.get(headers["question"]) or "").strip()
            expected = str(row.get(headers["expected"]) or "").strip()
            if not prompt or not expected:
                skipped += 1
                continue
            prompt_key = _key(prompt)
            expected_key = _key(expected)
            group = grouped.setdefault(prompt_key, {
                "prompt": prompt,
                "expected_answer": expected,
                "expected_key": expected_key,
                "correct": 0,
                "incorrect": Counter(),
                "unanswered": 0,
                "total": 0,
            })
            if group["expected_key"] != expected_key:
                raise ValueError(f"Question {prompt!r} has more than one expected answer.")
            answer = str(row.get(headers["answer"]) or "").strip()
            group["total"] += 1
            if not answer:
                group["unanswered"] += 1
            elif _key(answer) == expected_key:
                group["correct"] += 1
            else:
                group["incorrect"][answer] += 1

    questions = []
    for group in grouped.values():
        accuracy = round(group["correct"] * 100 / group["total"]) if group["total"] else 0
        misses = group["incorrect"].most_common(3)
        prompt = group["prompt"]
        if accuracy < 80:
            misconception = f" Common response: {misses[0][0]!r}." if misses else " Some responses were blank."
            next_step = f"Revisit {prompt!r} and model how to reach {group['expected_answer']!r}.{misconception}"
        else:
            next_step = "Most responses were correct; move on or use a harder example."
        questions.append({
            "prompt": prompt,
            "expected_answer": group["expected_answer"],
            "total": group["total"],
            "correct": group["correct"],
            "incorrect": sum(group["incorrect"].values()),
            "unanswered": group["unanswered"],
            "accuracy_percent": accuracy,
            "common_incorrect_answers": misses,
            "needs_review": accuracy < 80,
            "next_step": next_step,
        })
    questions.sort(key=lambda question: (question["accuracy_percent"], question["prompt"].casefold()))
    return {"filename": path.name, "rows_read": rows_read, "skipped_rows": skipped, "questions": questions}


def report_to_markdown(report: dict) -> str:
    lines = [f"# Exit-Ticket Response Summary: {report['filename']}", "",
             f"Responses reviewed: {report['rows_read'] - report['skipped_rows']}  ·  Rows skipped: {report['skipped_rows']}", "",
             "Only question, answer, and expected-answer fields are summarized; extra columns are ignored.", ""]
    if not report["questions"]:
        lines.extend(["No usable question responses were found.", ""])
        return "\n".join(lines)
    lines.extend(["| Question | Correct | Accuracy | Expected | Common incorrect answers | Next step |",
                  "| --- | ---: | ---: | --- | --- | --- |"])
    for question in report["questions"]:
        incorrect = ", ".join(f"{answer} ({count} responses)" for answer, count in question["common_incorrect_answers"])
        values = [question["prompt"], f"{question['correct']}/{question['total']}", f"{question['accuracy_percent']}%",
                  question["expected_answer"], incorrect, question["next_step"]]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + " |")
    lines.append("")
    return "\n".join(lines)


class ExitTicketAnalyzer:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Campus Suite · Exit-Ticket Analyzer")
        root.geometry("1060x700")
        root.minsize(780, 540)
        root.configure(bg=get_theme_palette()["BG"])
        self.report = None
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.style.configure("Treeview", rowheight=28, background="#ffffff", fieldbackground="#ffffff")
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        outer = tk.Frame(root, bg="#f4f5f1", padx=24, pady=20)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="TEACH  /  CHECK FOR UNDERSTANDING", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(outer, text="Exit-Ticket Analyzer", bg="#f4f5f1", fg="#20302a", font=("Segoe UI", 24, "bold")).pack(anchor="w", pady=(5, 2))
        tk.Label(outer, text="Find class-wide misconceptions from response patterns. Extra identity columns are ignored.", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 13))
        actions = tk.Frame(outer, bg="#f4f5f1")
        actions.pack(fill="x", pady=(0, 12))
        RoundedTkButton(actions, "Analyze response CSV…", self._open, variant="primary", width=210, height=40).pack(side="left")
        RoundedTkButton(actions, "Export summary…", self._export, variant="secondary", width=176, height=40).pack(side="left", padx=8)
        RoundedTkButton(actions, "Ask AI", self._ask_ai, variant="secondary", width=110, height=40).pack(side="left")
        self.summary = tk.Label(outer, text="Columns needed: question, answer, expected. Other columns are ignored.", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 10), anchor="w")
        self.summary.pack(fill="x", pady=(2, 9))
        columns = ("question", "correct", "accuracy", "expected", "common", "next")
        self.table = ttk.Treeview(outer, columns=columns, show="headings")
        for key, title, width in [("question", "Question", 230), ("correct", "Correct", 70), ("accuracy", "Accuracy", 75), ("expected", "Expected answer", 150), ("common", "Common wrong answers", 180), ("next", "Suggested next step", 270)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="w")
        self.table.pack(fill="both", expand=True)
        self.note = tk.Label(outer, text="", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 9), anchor="w", justify="left", wraplength=960)
        self.note.pack(fill="x", pady=(9, 0))
        self._sync_theme(get_theme_palette())
        watch_theme(root, self._sync_theme)

    def _sync_theme(self, palette=None):
        sync_native_theme(self.root)
        palette = palette if isinstance(palette, dict) else get_theme_palette()
        self.style.configure("Treeview", background=palette["CARD"], fieldbackground=palette["CARD"], foreground=palette["TEXT"])
        self.style.configure("Treeview.Heading", background=palette["BG"], foreground=palette["TEXT"])

    def _open(self):
        source = filedialog.askopenfilename(parent=self.root, title="Choose a response CSV", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not source:
            return
        try:
            self.report = analyze_responses(source)
        except (OSError, csv.Error, ValueError) as error:
            messagebox.showerror("Could not analyze responses", str(error), parent=self.root)
            return
        self.table.delete(*self.table.get_children())
        for question in self.report["questions"]:
            common = ", ".join(f"{answer} ({count})" for answer, count in question["common_incorrect_answers"])
            self.table.insert("", "end", values=(question["prompt"], f"{question['correct']}/{question['total']}", f"{question['accuracy_percent']}%", question["expected_answer"], common or "—", question["next_step"]))
        responses = self.report["rows_read"] - self.report["skipped_rows"]
        review_count = sum(question["needs_review"] for question in self.report["questions"])
        self.summary.configure(text=f"{self.report['filename']}  ·  {responses} responses summarized  ·  {review_count} question(s) to revisit")
        self.note.configure(text="Only grouped counts and common answer patterns appear here; extra columns are ignored. Check context before changing instruction based on a small response set.")

    def _export(self):
        if not self.report:
            messagebox.showinfo("Nothing to export", "Analyze a response CSV first.", parent=self.root)
            return
        target = filedialog.asksaveasfilename(parent=self.root, title="Export anonymous summary", defaultextension=".md", filetypes=[("Markdown", "*.md"), ("Text", "*.txt")], initialfile="exit-ticket-summary.md")
        if target:
            try:
                Path(target).write_text(report_to_markdown(self.report), encoding="utf-8")
            except OSError as error:
                messagebox.showerror("Export failed", str(error), parent=self.root)

    def _ask_ai(self):
        if not self.report:
            messagebox.showinfo("Nothing to ask about", "Analyze a response CSV first.", parent=self.root)
            return
        ask_about_result(
            self.root,
            "Exit-Ticket Analyzer",
            report_to_markdown(self.report),
            privacy_note="This sends aggregated response patterns. Confirm your school permits sharing these class results with the configured AI provider.",
        )


def main():
    root = tk.Tk()
    ExitTicketAnalyzer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
