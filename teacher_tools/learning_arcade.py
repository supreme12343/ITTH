"""Five short, offline learning games for Campus Suite."""
from __future__ import annotations

import tkinter as tk
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from campus_theme import RoundedTkButton, get_theme_palette, sync_native_theme, watch_theme
from campus_ai import ask_about_result
from tkinter import ttk
from decimal import Decimal, InvalidOperation


GAME_CATALOG = [
    {
        "id": "fraction-forge", "name": "Fraction Forge", "subject": "Math",
        "tagline": "Turn fractions into useful numbers.", "kind": "text",
        "questions": [
            {"prompt": "A 12-slice pie has 9 slices left. What fraction of the pie remains as a decimal?", "answer": "0.75", "display_answer": "0.75 (three quarters)", "explanation": "9 ÷ 12 = 0.75, which is the same as three quarters."},
            {"prompt": "A recipe uses 3/4 cup of oats. Double it. How many cups do you need?", "answer": "1.5", "display_answer": "1.5 cups", "explanation": "Doubling 3/4 gives 6/4, or 1 1/2 cups."},
            {"prompt": "What is 35% written as a decimal?", "answer": "0.35", "display_answer": "0.35", "explanation": "Percent means per hundred, so 35% = 35/100 = 0.35."},
            {"prompt": "A $40 book is discounted by 25%. How many dollars do you save?", "answer": "10", "display_answer": "$10", "explanation": "One quarter of $40 is $10."},
            {"prompt": "Which is larger: 2/3 or 3/5? Enter the fraction.", "answer": "2/3", "display_answer": "2/3", "explanation": "2/3 ≈ 0.667, while 3/5 = 0.6."},
        ],
    },
    {
        "id": "ecosystem-rescue", "name": "Ecosystem Rescue", "subject": "Biology",
        "tagline": "Choose interventions that help a food web recover.", "kind": "choice",
        "questions": [
            {"prompt": "Wolves disappear from a valley. Deer overgraze young trees. Which action best addresses the cause?", "choices": ["Plant only faster-growing grass", "Restore a balanced predator population and monitor deer", "Remove every shrub from the valley"], "answer": "Restore a balanced predator population and monitor deer", "explanation": "Restoring a missing predator can help rebalance the food web; monitoring checks for unintended effects."},
            {"prompt": "A pond has an algae bloom after fertilizer washes in. What is the strongest first response?", "choices": ["Reduce fertilizer runoff at the source", "Add more fertilizer to grow competing plants", "Remove all fish"], "answer": "Reduce fertilizer runoff at the source", "explanation": "Excess nutrients fuel algae. Preventing runoff tackles the cause before it reaches the pond."},
            {"prompt": "A small habitat has very few plant species. Which change usually improves resilience?", "choices": ["Plant several native species suited to the site", "Plant one fast-growing species everywhere", "Cover bare soil with plastic"], "answer": "Plant several native species suited to the site", "explanation": "Biodiversity gives an ecosystem more ways to keep functioning when conditions change."},
        ],
    },
    {
        "id": "source-sleuth", "name": "Source Sleuth", "subject": "Media literacy",
        "tagline": "Spot evidence, expertise, and missing context.", "kind": "choice",
        "questions": [
            {"prompt": "A post claims a new study proves a supplement works, but links no study. What is the best next step?", "choices": ["Share it because it sounds scientific", "Find the original study and check its methods", "Trust it if many people repost it"], "answer": "Find the original study and check its methods", "explanation": "Following a claim to its primary evidence lets you examine who was studied and what the results actually show."},
            {"prompt": "Which detail most helps you assess a health statistic?", "choices": ["The font used in the graphic", "The sample size and how participants were selected", "How many comments the post has"], "answer": "The sample size and how participants were selected", "explanation": "Sample size and selection affect how confidently results can be generalized."},
            {"prompt": "Two headlines describe the same study differently. What should you compare?", "choices": ["The study's actual results and limitations", "Which headline is shorter", "Which site has more ads"], "answer": "The study's actual results and limitations", "explanation": "Headlines can simplify or exaggerate; the study itself shows what it measured and what it cannot conclude."},
        ],
    },
    {
        "id": "logic-link", "name": "Logic Link", "subject": "Computer science",
        "tagline": "Trace a tiny program without running it.", "kind": "text",
        "questions": [
            {"prompt": "Trace: x = 2; y = x + 3; print(y * 2). What does it print?", "answer": "10", "display_answer": "10", "explanation": "First y becomes 5. Then 5 × 2 prints 10."},
            {"prompt": "A loop adds 3 once for each of 4 rounds. Starting at 0, what is the final total?", "answer": "12", "display_answer": "12", "explanation": "Four additions of 3 give 3 + 3 + 3 + 3 = 12."},
            {"prompt": "If score = 7, is score >= 10 true or false?", "answer": "false", "display_answer": "false", "explanation": "7 is less than 10, so the comparison is false."},
            {"prompt": "A function returns n + 1. What does it return when n is 8?", "answer": "9", "display_answer": "9", "explanation": "Substitute 8 for n: 8 + 1 = 9."},
        ],
    },
    {
        "id": "timeline-tactician", "name": "Timeline Tactician", "subject": "History",
        "tagline": "Put turning points in chronological order.", "kind": "sequence",
        "questions": [
            {"prompt": "Tap the events from earliest to latest.", "events": {"A": "US Declaration of Independence (1776)", "B": "Louisiana Purchase (1803)", "C": "US Civil War begins (1861)"}, "answer": ["A", "B", "C"], "display_answer": "Declaration → Louisiana Purchase → Civil War", "explanation": "1776 comes before 1803, which comes before 1861."},
            {"prompt": "Tap the events from earliest to latest.", "events": {"A": "First powered flight (1903)", "B": "First Moon landing (1969)", "C": "First iPhone released (2007)"}, "answer": ["A", "B", "C"], "display_answer": "Powered flight → Moon landing → iPhone", "explanation": "The Wright brothers flew in 1903, Apollo 11 landed in 1969, and the iPhone launched in 2007."},
            {"prompt": "Tap the events from earliest to latest.", "events": {"A": "Printing press spreads in Europe (c. 1450)", "B": "American Revolution begins (1775)", "C": "First modern Olympic Games (1896)"}, "answer": ["A", "B", "C"], "display_answer": "Printing press → Revolution → modern Olympics", "explanation": "Around 1450 is earlier than 1775, which is earlier than 1896."},
        ],
    },
]


def evaluate_answer(game_id: str, question_index: int, answer: str) -> dict:
    """Check a player's answer and return the teaching feedback for that turn."""
    game = next((item for item in GAME_CATALOG if item["id"] == game_id), None)
    if game is None:
        raise ValueError(f"Unknown game: {game_id}")
    if not isinstance(question_index, int) or not 0 <= question_index < len(game["questions"]):
        raise ValueError("Question index is out of range")
    question = game["questions"][question_index]
    supplied = str(answer).strip()
    expected = question["answer"]
    if game["kind"] == "sequence":
        given = [part.strip().upper() for part in supplied.replace(">", ",").split(",") if part.strip()]
        correct = given == expected
    elif game["kind"] == "text":
        correct = supplied.casefold().replace(" ", "") == str(expected).casefold().replace(" ", "")
        if not correct:
            try:
                correct = Decimal(supplied) == Decimal(str(expected))
            except InvalidOperation:
                pass
    else:
        correct = supplied.casefold() == str(expected).casefold()
    return {
        "correct": correct,
        "answer": question.get("display_answer", ", ".join(expected) if isinstance(expected, list) else str(expected)),
        "explanation": question["explanation"],
    }


class LearningArcade:
    """Small Tkinter arcade with five self-contained learning activities."""

    BG = "#f4f5f1"
    INK = "#20302a"
    GREEN = "#d9efc4"
    MUTED = "#718075"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.palette = get_theme_palette()
        root.title("Campus Suite · Learning Arcade")
        root.geometry("820x650")
        root.minsize(660, 520)
        self._apply_palette(self.palette)
        root.configure(bg=self.BG)
        self.game = None
        self.screen = "home"
        self.index = 0
        self.score = 0
        self.sequence = []
        self.feedback = None
        self.answer_entry = None
        self.content = tk.Frame(root, bg=self.BG, padx=28, pady=22)
        self.content.pack(fill="both", expand=True)
        self._show_home()
        watch_theme(root, self._theme_changed)

    def _apply_palette(self, palette):
        self.palette = palette
        self.BG = palette["BG"]
        self.INK = palette["TEXT"]
        self.GREEN = palette["GREEN"]
        self.MUTED = palette["TEXT_MUTED"]

    def _theme_changed(self, mode):
        old_feedback = self.feedback.cget("text") if self.feedback is not None else ""
        self._apply_palette(get_theme_palette(mode))
        self.content.configure(bg=self.BG)
        sync_native_theme(self.root)
        if self.screen == "home":
            self._show_home()
        elif self.screen == "complete":
            self._show_complete()
        elif old_feedback:
            self._show_question()
            self.feedback.configure(text=old_feedback,
                                    fg=self.palette["GREEN"] if old_feedback.startswith("Correct") else self.palette["TITLE"])
        else:
            self._show_question()

    def _clear(self):
        for child in self.content.winfo_children():
            child.destroy()

    def _label(self, parent, text, *, size=12, color=None, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=kwargs.pop("bg", self.BG), fg=color or self.INK,
                        font=("Segoe UI", size, "bold" if bold else "normal"),
                        wraplength=720, justify="left", **kwargs)

    def _button(self, parent, text, command, *, primary=False, **kwargs):
        width = 720 if len(text) > 40 else 170
        return RoundedTkButton(parent, text, command, variant="primary" if primary else "secondary",
                               width=width, height=46, font=("Segoe UI", 10, "bold" if primary else "normal"))

    def _show_home(self):
        self.screen = "home"
        self._clear()
        self._label(self.content, "CAMPUS SUITE  /  PLAY & LEARN", size=9, color=self.MUTED).pack(anchor="w")
        self._label(self.content, "Learning Arcade", size=30, bold=True).pack(anchor="w", pady=(8, 4))
        self._label(self.content, "Five quick challenges. Every answer comes with a reason.", size=12, color=self.MUTED).pack(anchor="w", pady=(0, 18))
        for game in GAME_CATALOG:
            card = tk.Frame(self.content, bg=self.palette["CARD"], padx=14, pady=10, highlightthickness=1, highlightbackground=self.palette["BORDER"])
            card.pack(fill="x", pady=5)
            info = tk.Frame(card, bg=self.palette["CARD"])
            info.pack(side="left", fill="x", expand=True)
            self._label(info, f"{game['subject'].upper()}  ·  {game['name']}", size=12, bold=True, bg=self.palette["CARD"]).pack(anchor="w")
            self._label(info, game["tagline"], size=10, color=self.MUTED, bg=self.palette["CARD"]).pack(anchor="w", pady=(3, 0))
            self._button(card, "Play →", lambda selected=game: self._start_game(selected), primary=True).pack(side="right", padx=5)

    def _start_game(self, game):
        self.game, self.index, self.score = game, 0, 0
        self.screen = "question"
        self._show_question()

    def _show_question(self):
        self.screen = "question"
        self._clear()
        question = self.game["questions"][self.index]
        header = tk.Frame(self.content, bg=self.BG)
        header.pack(fill="x")
        self._button(header, "← Games", self._show_home).pack(side="left")
        self._label(header, f"{self.game['name']}  ·  {self.index + 1}/{len(self.game['questions'])}  ·  Score {self.score}", size=10, color=self.MUTED).pack(side="right", pady=12)
        self._label(self.content, question["prompt"], size=20, bold=True).pack(anchor="w", pady=(42, 22))
        self._button(
            self.content,
            "Ask AI for a hint",
            lambda current=question: self._ask_ai_hint(current),
        ).pack(anchor="w", pady=(0, 8))
        self.feedback = tk.Label(self.content, text="", bg=self.BG, fg=self.INK, font=("Segoe UI", 11), wraplength=700, justify="left")
        self.feedback.pack(anchor="w", fill="x", pady=18)
        self.answer_entry = None
        self.sequence = []
        if self.game["kind"] == "choice":
            for choice in question["choices"]:
                self._button(self.content, choice, lambda item=choice: self._submit(item)).pack(fill="x", pady=5, anchor="w")
        elif self.game["kind"] == "sequence":
            self._label(self.content, "Your order (tap items to add them):", size=10, color=self.MUTED).pack(anchor="w")
            self.order_label = self._label(self.content, "No events selected", size=11, bg=self.palette["CARD"], padx=12, pady=10)
            self.order_label.pack(fill="x", pady=8)
            for key, event in question["events"].items():
                self._button(self.content, f"{key}  ·  {event}", lambda item=key: self._add_sequence(item)).pack(fill="x", pady=3)
            controls = tk.Frame(self.content, bg=self.BG)
            controls.pack(anchor="w", pady=10)
            self._button(controls, "Undo", self._undo_sequence).pack(side="left", padx=(0, 7))
            self._button(controls, "Check order", lambda: self._submit(",".join(self.sequence)), primary=True).pack(side="left")
        else:
            row = tk.Frame(self.content, bg=self.BG)
            row.pack(anchor="w")
            self.answer_entry = tk.Entry(row, width=28, font=("Segoe UI", 14), relief="flat", bg=self.palette["CARD"], fg=self.INK, insertbackground=self.INK)
            self.answer_entry.pack(side="left", ipady=9, padx=(0, 9))
            self.answer_entry.bind("<Return>", lambda _event: self._submit(self.answer_entry.get()))
            self._button(row, "Check", lambda: self._submit(self.answer_entry.get()), primary=True).pack(side="left")
            self.answer_entry.focus_set()

    def _add_sequence(self, key):
        if key not in self.sequence:
            self.sequence.append(key)
            self.order_label.configure(text="  →  ".join(self.sequence))

    def _undo_sequence(self):
        if self.sequence:
            self.sequence.pop()
            self.order_label.configure(text="  →  ".join(self.sequence) or "No events selected")

    def _submit(self, answer):
        if self.feedback.cget("text"):
            return
        result = evaluate_answer(self.game["id"], self.index, answer)
        if result["correct"]:
            self.score += 1
        icon = "Correct ✓" if result["correct"] else f"Answer: {result['answer']}"
        self.feedback.configure(text=f"{icon}\n{result['explanation']}", fg=self.palette["GREEN"] if result["correct"] else self.palette["TITLE"])
        if self.answer_entry:
            self.answer_entry.configure(state="disabled")
        self._button(self.content, "Next challenge →" if self.index + 1 < len(self.game["questions"]) else "Finish game →", self._next, primary=True).pack(anchor="w", pady=10)

    def _ask_ai_hint(self, question):
        # Deliberately exclude the stored answer and explanation so the hint
        # request does not leak the game key to the model.
        safe_question = {key: question[key] for key in ("prompt", "choices", "events") if key in question}
        context = f"Game: {self.game['name']}\nChallenge: {safe_question}\nGive a hint without revealing the answer."
        ask_about_result(self.root, "Learning Arcade", context)

    def _next(self):
        self.index += 1
        if self.index >= len(self.game["questions"]):
            self._show_complete()
        else:
            self._show_question()

    def _show_complete(self):
        self.screen = "complete"
        self._clear()
        self._label(self.content, "CHALLENGE COMPLETE", size=10, color=self.MUTED).pack(anchor="w", pady=(60, 10))
        self._label(self.content, f"{self.score} / {len(self.game['questions'])}", size=38, bold=True).pack(anchor="w")
        self._label(self.content, "Every attempt builds a stronger recall path. Pick another challenge when you're ready.", size=12, color=self.MUTED).pack(anchor="w", pady=10)
        self._button(self.content, "Choose another game", self._show_home, primary=True).pack(anchor="w", pady=12)


def main():
    root = tk.Tk()
    LearningArcade(root)
    root.mainloop()


if __name__ == "__main__":
    main()
