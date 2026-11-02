"""Read-only CSV profiler for exploring a dataset before analysis."""
from __future__ import annotations

import csv
import math
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


def _number(value: str):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def analyze_csv(source: str | Path) -> dict:
    """Summarize CSV columns without changing the file."""
    path = Path(source)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not any(name and name.strip() for name in reader.fieldnames):
            raise ValueError("The CSV needs a header row with at least one named column.")
        source_names = list(reader.fieldnames)
        names = [name.strip() if name else "" for name in source_names]
        if any(not name for name in names):
            raise ValueError("Every CSV column needs a non-empty header.")
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"Duplicate column names are ambiguous: {', '.join(sorted(duplicates))}")

        counters = {name: Counter() for name in names}
        numeric = {name: {"count": 0, "sum": 0.0, "minimum": None, "maximum": None} for name in names}
        missing = dict.fromkeys(names, 0)
        non_numeric = dict.fromkeys(names, 0)
        row_count = 0
        warnings = []
        for row_count, record in enumerate(reader, start=1):
            if None in record:
                warnings.append(f"Row {row_count + 1} has more values than the header.")
            for source_name, name in zip(source_names, names):
                raw_value = record.get(source_name)
                value = str(raw_value).strip() if raw_value is not None else ""
                if not value:
                    missing[name] += 1
                    continue
                counters[name][value] += 1
                number = _number(value)
                if number is None:
                    non_numeric[name] += 1
                    continue
                column = numeric[name]
                column["count"] += 1
                column["sum"] += number
                column["minimum"] = number if column["minimum"] is None else min(column["minimum"], number)
                column["maximum"] = number if column["maximum"] is None else max(column["maximum"], number)
            if any(record.get(name) is None for name in source_names):
                warnings.append(f"Row {row_count + 1} has fewer values than the header.")

    columns = []
    for name in names:
        counts = counters[name]
        numbers = numeric[name]
        filled = sum(counts.values())
        is_number = numbers["count"] > 0 and non_numeric[name] == 0
        kind = "Number" if is_number else ("Category / text" if filled else "Empty")
        suggestion = "Explore the numeric range" if is_number else "Compare the most common categories" if filled else "Check why this field has no values"
        columns.append({
            "name": name,
            "kind": kind,
            "filled": filled,
            "missing": missing[name],
            "unique": len(counts),
            "mean": numbers["sum"] / numbers["count"] if is_number else None,
            "minimum": numbers["minimum"] if is_number else None,
            "maximum": numbers["maximum"] if is_number else None,
            "common_values": counts.most_common(3),
            "suggestion": suggestion,
        })
    return {"filename": path.name, "row_count": row_count, "columns": columns, "warnings": list(dict.fromkeys(warnings))}


def report_to_markdown(report: dict) -> str:
    lines = [f"# Dataset Field Guide: {report['filename']}", "", f"Rows inspected: {report['row_count']}", "",
             "| Field | Kind | Filled | Missing | Unique | Range / mean | Common values | Next step |",
             "| --- | --- | ---: | ---: | ---: | --- | --- | --- |"]
    for column in report["columns"]:
        values = ", ".join(f"{value} ({count})" for value, count in column["common_values"])
        metrics = ""
        if column["kind"] == "Number":
            metrics = f"{column['minimum']:g} to {column['maximum']:g}; mean {column['mean']:g}"
        fields = [column["name"], column["kind"], str(column["filled"]), str(column["missing"]), str(column["unique"]), metrics, values, column["suggestion"]]
        lines.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", " ") for value in fields) + " |")
    if report["warnings"]:
        lines.extend(["", "## Row shape notes", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    lines.append("")
    return "\n".join(lines)


class DatasetFieldGuide:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Campus Suite · Dataset Field Guide")
        root.geometry("1020x700")
        root.minsize(760, 520)
        root.configure(bg=get_theme_palette()["BG"])
        self.report = None
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.style.configure("Treeview", rowheight=28, background="#ffffff", fieldbackground="#ffffff")
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        outer = tk.Frame(root, bg="#f4f5f1", padx=24, pady=20)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="DATA  /  EXPLORE", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(outer, text="Dataset Field Guide", bg="#f4f5f1", fg="#20302a", font=("Segoe UI", 24, "bold")).pack(anchor="w", pady=(5, 2))
        tk.Label(outer, text="Preview the shape of a CSV before you decide what to investigate.", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 15))
        buttons = tk.Frame(outer, bg="#f4f5f1")
        buttons.pack(fill="x", pady=(0, 12))
        RoundedTkButton(buttons, "Open CSV…", self._open, variant="primary", width=158, height=40).pack(side="left")
        RoundedTkButton(buttons, "Export field guide…", self._export, variant="secondary", width=192, height=40).pack(side="left", padx=8)
        RoundedTkButton(buttons, "Ask AI", self._ask_ai, variant="secondary", width=110, height=40).pack(side="left")
        self.summary = tk.Label(outer, text="Choose a CSV file to inspect. The original file is never edited.", bg="#f4f5f1", fg="#718075", font=("Segoe UI", 10), anchor="w")
        self.summary.pack(fill="x", pady=(2, 9))
        keys = ("name", "kind", "filled", "missing", "unique", "range", "common", "suggestion")
        self.table = ttk.Treeview(outer, columns=keys, show="headings")
        for key, label, width in [("name", "Field", 150), ("kind", "Kind", 120), ("filled", "Filled", 66), ("missing", "Missing", 70), ("unique", "Unique", 66), ("range", "Range / mean", 175), ("common", "Common values", 180), ("suggestion", "Explore", 180)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, anchor="w")
        self.table.pack(fill="both", expand=True)
        self.note = tk.Label(outer, text="", bg="#f4f5f1", fg="#875b37", font=("Segoe UI", 9), anchor="w", justify="left", wraplength=950)
        self.note.pack(fill="x", pady=(9, 0))
        self._sync_theme(get_theme_palette())
        watch_theme(root, self._sync_theme)

    def _sync_theme(self, palette=None):
        sync_native_theme(self.root)
        palette = palette if isinstance(palette, dict) else get_theme_palette()
        self.style.configure("Treeview", background=palette["CARD"], fieldbackground=palette["CARD"], foreground=palette["TEXT"])
        self.style.configure("Treeview.Heading", background=palette["BG"], foreground=palette["TEXT"])

    def _open(self):
        source = filedialog.askopenfilename(parent=self.root, title="Inspect a CSV dataset", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not source:
            return
        try:
            self.report = analyze_csv(source)
        except (OSError, csv.Error, ValueError) as error:
            messagebox.showerror("Could not read CSV", str(error), parent=self.root)
            return
        self.table.delete(*self.table.get_children())
        for column in self.report["columns"]:
            value_range = ""
            if column["kind"] == "Number":
                value_range = f"{column['minimum']:g}–{column['maximum']:g}; μ {column['mean']:g}"
            common = ", ".join(f"{value} ({count})" for value, count in column["common_values"])
            self.table.insert("", "end", values=(column["name"], column["kind"], column["filled"], column["missing"], column["unique"], value_range, common, column["suggestion"]))
        self.summary.configure(text=f"{self.report['filename']}  ·  {self.report['row_count']} rows  ·  {len(self.report['columns'])} fields")
        self.note.configure(text="  ".join(self.report["warnings"]) or "Summaries describe the file contents; they do not prove a relationship or explain why values differ.")

    def _export(self):
        if not self.report:
            messagebox.showinfo("Nothing to export", "Open a CSV first.", parent=self.root)
            return
        target = filedialog.asksaveasfilename(parent=self.root, title="Export field guide", defaultextension=".md", filetypes=[("Markdown", "*.md"), ("Text", "*.txt")], initialfile=f"{Path(self.report['filename']).stem}-field-guide.md")
        if target:
            try:
                Path(target).write_text(report_to_markdown(self.report), encoding="utf-8")
            except OSError as error:
                messagebox.showerror("Export failed", str(error), parent=self.root)

    def _ask_ai(self):
        if not self.report:
            messagebox.showinfo("Nothing to ask about", "Open a CSV first.", parent=self.root)
            return
        ask_about_result(
            self.root,
            "Dataset Field Guide",
            report_to_markdown(self.report),
            privacy_note="The field guide can include column names and common values. Do not send confidential or identifiable data.",
        )


def main():
    root = tk.Tk()
    DatasetFieldGuide(root)
    root.mainloop()


if __name__ == "__main__":
    main()
