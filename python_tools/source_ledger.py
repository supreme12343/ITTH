"""Research Source Ledger: record a claim, its evidence, and what it leaves open."""
from __future__ import annotations

import json
import sys
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from campus_theme import RoundedTkButton, get_theme_palette, sync_native_theme, watch_theme
from campus_ai import ask_about_result


DEFAULT_LEDGER = PROJECT_ROOT / "storage" / "research" / "source_ledger.json"
FIELDS = ("claim", "title", "publisher", "url", "published_date", "source_type", "evidence", "limitations")


def normalize_entry(values: dict) -> dict:
    """Clean and validate a ledger entry without discarding a user's wording."""
    entry = {field: str(values.get(field, "")).strip() for field in FIELDS}
    if not entry["claim"]:
        raise ValueError("A claim is required so the evidence has a clear purpose.")
    if entry["url"]:
        parsed = urlparse(entry["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Source links must use a valid http or https URL.")
    return entry


def assess_source(entry: dict) -> dict:
    """Give a transparent research-readiness checklist, not a truth verdict."""
    points = 0
    strengths, gaps = [], []
    date_recorded = False
    try:
        date.fromisoformat(entry.get("published_date", ""))
        date_recorded = True
    except (TypeError, ValueError):
        pass
    checks = [
        (bool(entry.get("publisher")), 1, "Publisher or author is named", "Name the author or publishing organization"),
        (bool(entry.get("url")), 1, "A source link is recorded", "Add a direct link to the source"),
        (date_recorded, 1, "A valid publication date is recorded", "Look for and record a valid publication date"),
        (len(entry.get("evidence", "")) >= 12, 1, "The evidence is described", "Write what evidence supports the claim"),
        (len(entry.get("limitations", "")) >= 8, 1, "A limitation is recorded", "Note a limitation or what the source does not establish"),
    ]
    for passed, score, good, missing in checks:
        if passed:
            points += score
            strengths.append(good)
        else:
            gaps.append(missing)
    return {"score": points, "out_of": len(checks), "strengths": strengths, "gaps": gaps,
            "note": "A checklist for organizing research; it cannot verify whether a claim is true."}


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def export_markdown(entries: list[dict]) -> str:
    """Render saved sources to a portable Markdown research log."""
    lines = ["# Research Source Ledger", "", "Organized evidence notes. A completed checklist is not a fact-check.", ""]
    if not entries:
        return "\n".join(lines + ["No sources recorded.", ""])
    lines += ["| Claim | Source | Publisher | Published | Evidence | Limitations | Link |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for entry in entries:
        values = [_cell(entry.get(key, "")) for key in FIELDS]
        lines.append(f"| {values[0]} | {values[1]} | {values[2]} | {values[4]} | {values[6]} | {values[7]} | {values[3]} |")
    return "\n".join(lines) + "\n"


def load_entries(path: Path = DEFAULT_LEDGER) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read the source ledger: {error}") from error
    if not isinstance(data, list):
        raise ValueError("The source ledger must contain a list of entries.")
    return [normalize_entry(entry) for entry in data if isinstance(entry, dict)]


def save_entries(entries: list[dict], path: Path = DEFAULT_LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class SourceLedgerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Campus Suite · Research Source Ledger")
        root.geometry("1020x780")
        root.minsize(760, 600)
        root.configure(bg=get_theme_palette()["BG"])
        self.entries = load_entries()
        self.vars = {field: tk.StringVar() for field in FIELDS}
        self._build()
        self._refresh()
        self._sync_theme(get_theme_palette())
        watch_theme(root, self._sync_theme)

    def _build(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.style.configure("Treeview", rowheight=26, background="#ffffff", fieldbackground="#ffffff")
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        outer = tk.Frame(self.root, bg="#f4f5f1", padx=22, pady=18)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="RESEARCH  /  SOURCE NOTES", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(outer, text="Research Source Ledger", bg="#f4f5f1", fg="#20302a", font=("Segoe UI", 24, "bold")).pack(anchor="w", pady=(5, 2))
        tk.Label(outer, text="Keep the claim, evidence, source, and limitations together.", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 12))

        form = tk.LabelFrame(outer, text="New source note", bg="#f4f5f1", fg="#405047", padx=12, pady=9)
        form.pack(fill="x")
        labels = [("claim", "Claim"), ("title", "Source title"), ("publisher", "Author / publisher"),
                  ("url", "URL (optional)"), ("published_date", "Published (YYYY-MM-DD)"), ("source_type", "Source type"),
                  ("evidence", "Evidence for the claim"), ("limitations", "Limitations / open questions")]
        for index, (key, label) in enumerate(labels):
            row, col = divmod(index, 2)
            cell = tk.Frame(form, bg="#f4f5f1")
            cell.grid(row=row, column=col, sticky="ew", padx=5, pady=3)
            tk.Label(cell, text=label, bg="#f4f5f1", fg="#59695f", font=("Segoe UI", 9)).pack(anchor="w")
            tk.Entry(cell, textvariable=self.vars[key], relief="flat", bg="#ffffff", font=("Segoe UI", 10)).pack(fill="x", ipady=5)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        buttons = tk.Frame(form, bg="#f4f5f1")
        buttons.grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=(7, 0))
        RoundedTkButton(buttons, "Save source note", self._save, variant="primary", width=174, height=40).pack(side="left", padx=(0, 8))
        RoundedTkButton(buttons, "Export Markdown…", self._export, variant="secondary", width=174, height=40).pack(side="left")
        RoundedTkButton(buttons, "Ask AI about selected source", self._ask_ai, variant="secondary", width=215, height=40).pack(side="left", padx=(8, 0))

        summary = tk.Frame(outer, bg="#f4f5f1")
        summary.pack(fill="x", pady=(13, 7))
        tk.Label(summary, text="Saved sources", bg="#f4f5f1", fg="#20302a", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.count_label = tk.Label(summary, text="", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 9))
        self.count_label.pack(side="right")
        columns = ("claim", "source", "publisher", "date", "score")
        self.table = ttk.Treeview(outer, columns=columns, show="headings", height=10)
        for key, heading, width in [("claim", "Claim", 290), ("source", "Source", 210), ("publisher", "Author / publisher", 180), ("date", "Date", 110), ("score", "Checklist", 90)]:
            self.table.heading(key, text=heading)
            self.table.column(key, width=width, anchor="w")
        self.table.pack(fill="both", expand=True)
        self.assessment = tk.Label(outer, text="Select a source to review its checklist.", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 9), anchor="w", justify="left", wraplength=930)
        self.assessment.pack(fill="x", pady=(8, 0))
        self.table.bind("<<TreeviewSelect>>", self._show_assessment)

    def _sync_theme(self, palette=None):
        sync_native_theme(self.root)
        palette = palette if isinstance(palette, dict) else get_theme_palette()
        self.style.configure("Treeview", background=palette["CARD"], fieldbackground=palette["CARD"], foreground=palette["TEXT"])
        self.style.configure("Treeview.Heading", background=palette["BG"], foreground=palette["TEXT"])

    def _save(self):
        try:
            entry = normalize_entry({key: variable.get() for key, variable in self.vars.items()})
            if entry["published_date"]:
                try:
                    date.fromisoformat(entry["published_date"])
                except ValueError as error:
                    raise ValueError("Use YYYY-MM-DD for the publication date, or leave it blank.") from error
            self.entries.insert(0, entry)
            save_entries(self.entries)
            for variable in self.vars.values():
                variable.set("")
            self._refresh()
        except (ValueError, OSError) as error:
            messagebox.showerror("Source note not saved", str(error), parent=self.root)

    def _refresh(self):
        self.table.delete(*self.table.get_children())
        for index, entry in enumerate(self.entries):
            assessment = assess_source(entry)
            self.table.insert("", "end", iid=str(index), values=(entry["claim"], entry["title"], entry["publisher"], entry["published_date"], f"{assessment['score']}/{assessment['out_of']}"))
        self.count_label.configure(text=f"{len(self.entries)} note{'s' if len(self.entries) != 1 else ''}")

    def _show_assessment(self, _event=None):
        selection = self.table.selection()
        if not selection:
            return
        entry = self.entries[int(selection[0])]
        result = assess_source(entry)
        strengths = "; ".join(result["strengths"]) or "No checklist items complete yet"
        gaps = "; ".join(result["gaps"]) or "No checklist gaps"
        self.assessment.configure(text=f"Research readiness {result['score']}/{result['out_of']} · Recorded: {strengths}. Next: {gaps}. {result['note']}")

    def _export(self):
        target = filedialog.asksaveasfilename(parent=self.root, title="Export source ledger", defaultextension=".md", filetypes=[("Markdown", "*.md"), ("Text", "*.txt")], initialfile="research-source-ledger.md")
        if target:
            try:
                Path(target).write_text(export_markdown(self.entries), encoding="utf-8")
            except OSError as error:
                messagebox.showerror("Export failed", str(error), parent=self.root)

    def _ask_ai(self):
        selection = self.table.selection()
        if not selection:
            messagebox.showinfo("Select a source", "Choose a source note first.", parent=self.root)
            return
        entry = self.entries[int(selection[0])]
        context = "\n".join(f"{field.replace('_', ' ').title()}: {value}" for field, value in entry.items())
        ask_about_result(
            self.root,
            "Research Source Ledger",
            context,
            privacy_note="Only the selected source note is included. Check it for private or unpublished information before sending.",
        )


def main():
    root = tk.Tk()
    SourceLedgerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
