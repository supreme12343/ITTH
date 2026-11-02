"""Try regular expressions against sample text without opening or changing files."""
from __future__ import annotations

import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from campus_theme import RoundedTkButton, get_theme_palette, sync_native_theme, watch_theme
from campus_ai import ask_about_result


def inspect_pattern(pattern: str, text: str, *, ignore_case: bool = False, max_matches: int = 250) -> dict:
    """Return match locations and capture groups for a pattern and sample text."""
    if not pattern:
        raise ValueError("Enter a pattern to test.")
    if max_matches < 1:
        raise ValueError("The match display limit must be positive.")
    compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    found = []
    count = 0
    for match in compiled.finditer(text):
        count += 1
        if count <= max_matches:
            found.append({
                "text": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "groups": match.groupdict(),
                "captures": match.groups(),
            })
    return {"count": count, "matches": found, "truncated": count > max_matches}


class RegexWorkbench:
    BG = "#f4f5f1"
    INK = "#20302a"
    MUTED = "#718075"

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Campus Suite · Regex Workbench")
        root.geometry("960x700")
        root.minsize(720, 520)
        root.configure(bg=get_theme_palette()["BG"])
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TEntry", padding=7)

        outer = tk.Frame(root, bg=self.BG, padx=24, pady=20)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="CREATE  /  BUILD & TEST", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(outer, text="Regex Workbench", bg=self.BG, fg=self.INK, font=("Segoe UI", 24, "bold")).pack(anchor="w", pady=(5, 2))
        tk.Label(outer, text="Test patterns locally. Ask AI for help only when you choose.", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 14))

        pattern_row = tk.Frame(outer, bg=self.BG)
        pattern_row.pack(fill="x")
        tk.Label(pattern_row, text="Pattern", bg=self.BG, fg=self.INK, font=("Segoe UI", 10, "bold")).pack(side="left")
        self.pattern = tk.StringVar(value=r"(?P<word>\w+)@(?P<domain>[\w.]+)")
        self.pattern_entry = ttk.Entry(pattern_row, textvariable=self.pattern, font=("Consolas", 11))
        self.pattern_entry.pack(side="left", fill="x", expand=True, padx=10)
        self.ignore_case = tk.BooleanVar(value=True)
        tk.Checkbutton(pattern_row, text="Ignore case", variable=self.ignore_case, bg=self.BG, fg=self.INK, activebackground=self.BG, selectcolor="#ffffff").pack(side="right")

        panes = tk.Frame(outer, bg=self.BG)
        panes.pack(fill="both", expand=True, pady=12)
        left = tk.Frame(panes, bg=self.BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 7))
        right = tk.Frame(panes, bg=self.BG)
        right.pack(side="left", fill="both", expand=True, padx=(7, 0))
        tk.Label(left, text="Sample text", bg=self.BG, fg=self.INK, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        self.sample = tk.Text(left, height=16, wrap="word", font=("Consolas", 10), relief="flat", padx=10, pady=10)
        self.sample.pack(fill="both", expand=True)
        self.sample.insert("1.0", "Write to Ada@school.edu or contact team@campus.example for help.")
        tk.Label(right, text="Matches & groups", bg=self.BG, fg=self.INK, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        self.output = tk.Text(right, height=16, wrap="word", state="disabled", font=("Consolas", 9), relief="flat", padx=10, pady=10, bg="#ffffff")
        self.output.pack(fill="both", expand=True)
        controls = tk.Frame(outer, bg=self.BG)
        controls.pack(fill="x")
        RoundedTkButton(controls, "Test pattern", self._run, variant="primary", width=160, height=40).pack(side="left")
        RoundedTkButton(controls, "Ask AI", self._ask_ai, variant="secondary", width=110, height=40).pack(side="left", padx=8)
        self.status = tk.Label(controls, text="Ready to test", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9))
        self.status.pack(side="left", padx=12)
        self._run()
        self._sync_theme(get_theme_palette())
        watch_theme(root, self._sync_theme)

    def _sync_theme(self, palette=None):
        sync_native_theme(self.root)
        palette = palette if isinstance(palette, dict) else get_theme_palette()
        style = ttk.Style(self.root)
        style.configure("TEntry", fieldbackground=palette["CARD"], foreground=palette["TEXT"])

    def _run(self):
        try:
            result = inspect_pattern(self.pattern.get(), self.sample.get("1.0", "end-1c"), ignore_case=self.ignore_case.get())
        except (ValueError, re.error) as error:
            self.output.configure(state="normal")
            self.output.delete("1.0", "end")
            self.output.insert("end", f"Pattern error: {error}")
            self.output.configure(state="disabled")
            self.status.configure(text=f"Pattern error: {error}", fg="#a24538")
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        if result["matches"]:
            for number, match in enumerate(result["matches"], start=1):
                self.output.insert("end", f"{number}. {match['text']!r}  [{match['start']}:{match['end']}]\n")
                if match["groups"]:
                    self.output.insert("end", f"   named: {match['groups']}\n")
                if match["captures"]:
                    self.output.insert("end", f"   groups: {match['captures']}\n")
                self.output.insert("end", "\n")
        else:
            self.output.insert("end", "No matches in this sample.")
        if result["truncated"]:
            self.output.insert("end", "\nOnly the first 250 matches are shown.")
        self.output.configure(state="disabled")
        self.status.configure(text=f"{result['count']} match{'es' if result['count'] != 1 else ''}", fg=self.MUTED)

    def _ask_ai(self):
        context = (
            f"Pattern: {self.pattern.get()}\n"
            f"Ignore case: {self.ignore_case.get()}\n"
            f"Sample text:\n{self.sample.get('1.0', 'end-1c')}\n\n"
            f"Current result:\n{self.output.get('1.0', 'end-1c')}"
        )
        ask_about_result(
            self.root,
            "Regex Workbench",
            context,
            privacy_note="This includes the sample text shown in the editor. Remove emails, tokens, or other sensitive values before sending.",
        )


def main():
    root = tk.Tk()
    RegexWorkbench(root)
    root.mainloop()


if __name__ == "__main__":
    main()
