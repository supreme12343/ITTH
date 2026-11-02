import tkinter as tk
from tkinter import messagebox
import mimetypes
import threading
from campus_theme import ACCENT, ACCENT_HOVER, BG, CARD_LIGHT, RED, TEXT, RoundedTkButton, apply_tk_theme, get_theme_mode, watch_theme


def calculate_grade():
    try:
        out_of_value = out_of_entry.get()
        if out_of_value.strip() == "":
            raise ValueError("Please enter 'marks out of' value.")

        out_of = float(out_of_value)
        if out_of <= 0:
            raise ValueError("'Marks out of' must be greater than zero.")

        total_obtained = 0
        subject_count = 0

        for subject, entry in subject_entries:
            mark_text = entry.get()
            if mark_text.strip() == "":
                raise ValueError(f"Please enter marks for {subject}.")

            mark = float(mark_text)
            if not (0 <= mark <= out_of):
                raise ValueError(f"Marks for {subject} must be between 0 and {out_of}.")

            total_obtained += mark
            subject_count += 1

        total_max = out_of * subject_count
        percentage = (total_obtained / total_max) * 100

        if percentage >= 90:
            grade = "A+"
        elif percentage >= 80:
            grade = "A"
        elif percentage >= 70:
            grade = "B"
        elif percentage >= 60:
            grade = "C"
        elif percentage >= 50:
            grade = "D"
        else:
            grade = "F"

        result_label.config(text=f"Total: {total_obtained}/{total_max}\n"
                                 f"Percentage: {percentage:.2f}%\n"
                                 f"Grade: {grade}")

        mime_type, _ = mimetypes.guess_type("gradesheet.pdf")
        mime_display_text = f"MIME type of gradesheet.pdf: {mime_type if mime_type else 'unknown'}"
        mime_label.config(text=mime_display_text)

    except ValueError as e:
        messagebox.showerror("Input Error", str(e))

def clear_entries():
    """Clears all input fields and resets result labels."""
    out_of_entry.delete(0, tk.END)
    out_of_entry.insert(0, "100")

    for _, entry in subject_entries:
        entry.delete(0, tk.END)

    result_label.config(text="")
    mime_label.config(text="")

def ask_campus_ai():
    """Optional, opt-in AI help with the grade result currently on screen."""
    if not result_label.cget("text").strip():
        messagebox.showinfo("Nothing to ask about", "Calculate a grade first.")
        return

    marks = "\n".join(f"{subject}: {entry.get().strip() or '(blank)'}"
                      for subject, entry in subject_entries)
    context = (f"Marks out of: {out_of_entry.get().strip()}\n\n"
               f"Marks entered:\n{marks}\n\n"
               f"Calculated result:\n{result_label.cget('text')}")

    # campus_ai pulls in the shared key manager, which is slow to import, so load
    # it on a background thread and keep this window responsive meanwhile.
    ask_ai_button.set_text("Preparing AI…")

    def loader():
        try:
            from campus_ai import ask_about_result
            error = None
        except Exception as e:
            ask_about_result, error = None, str(e)

        def finish():
            if not root.winfo_exists():
                return
            ask_ai_button.set_text("Ask Campus AI")
            if error:
                messagebox.showerror("Campus AI unavailable", f"Could not load Campus Suite AI:\n{error}")
                return
            ask_about_result(
                root,
                "Grade Estimation",
                context,
                privacy_note="This sends the marks you typed in and the calculated grade. Remove anything you do not want shared.",
            )

        try:
            root.after(0, finish)
        except Exception:
            pass

    threading.Thread(target=loader, daemon=True).start()

BG_COLOR = BG
FG_COLOR = TEXT
ACCENT_COLOR = ACCENT
BUTTON_BG_CALCULATE = ACCENT
BUTTON_ACTIVE_BG_CALCULATE = ACCENT_HOVER
BUTTON_BG_CLEAR = RED
BUTTON_ACTIVE_BG_CLEAR = "#d92d24"

root = tk.Tk()
root.title("Grade Estimation")
root.geometry("470x560")
root.configure(bg=BG_COLOR)

title_label = tk.Label(root, text="Enter Your Marks Below", font=('Arial', 16, 'bold'),
                         bg=BG_COLOR, fg=ACCENT_COLOR, relief=tk.FLAT, borderwidth=0)
title_label.pack(pady=(20, 15))

out_of_frame = tk.Frame(root, bg=BG_COLOR, relief=tk.FLAT, borderwidth=0)
out_of_frame.pack(pady=10)

tk.Label(out_of_frame, text="Marks out of:", width=15, anchor='w',
         bg=BG_COLOR, fg=FG_COLOR, relief=tk.FLAT, borderwidth=0).pack(side=tk.LEFT, padx=5)
out_of_entry = tk.Entry(out_of_frame, width=10,
                         bg="#3a3a4c", fg=FG_COLOR, insertbackground=ACCENT_COLOR, 
                         relief=tk.FLAT, borderwidth=1, highlightthickness=1, highlightbackground=BG_COLOR, highlightcolor=ACCENT_COLOR)
out_of_entry.pack(side=tk.LEFT, padx=5)
out_of_entry.insert(0, "100") 

subjects_frame = tk.Frame(root, bg=BG_COLOR, relief=tk.FLAT, borderwidth=0)
subjects_frame.pack(pady=10)

subjects = ["Math", "English", "French", "Computer Science", "Social Science", "Physics", "Chemistry"] 
subject_entries = []

for subject in subjects:
    row = tk.Frame(subjects_frame, bg=BG_COLOR, relief=tk.FLAT, borderwidth=0)
    row.pack(pady=7, fill=tk.X) 

    tk.Label(row, text=subject + ":", width=18, anchor='w',
             bg=BG_COLOR, fg=FG_COLOR, relief=tk.FLAT, borderwidth=0).pack(side=tk.LEFT, padx=5)
    entry = tk.Entry(row, width=10,
                     bg="#3a3a4c", fg=FG_COLOR, insertbackground=ACCENT_COLOR, 
                     relief=tk.FLAT, borderwidth=1, highlightthickness=1, highlightbackground=BG_COLOR, highlightcolor=ACCENT_COLOR)
    entry.pack(side=tk.LEFT, padx=5)
    subject_entries.append((subject, entry))

button_frame = tk.Frame(root, bg=BG_COLOR)
button_frame.pack(pady=25)

calculate_button = RoundedTkButton(button_frame, "Calculate Grade", calculate_grade,
                                   variant="primary", width=150, height=44,
                                   font=("Arial", 11, "bold"))
calculate_button.pack(side=tk.LEFT, padx=10) 

clear_button = RoundedTkButton(button_frame, "Clear All", clear_entries,
                               variant="danger", width=112, height=44,
                               font=("Arial", 11, "bold"))
clear_button.pack(side=tk.LEFT, padx=10)

ask_ai_button = RoundedTkButton(button_frame, "Ask Campus AI", ask_campus_ai,
                                variant="secondary", width=130, height=44,
                                font=("Arial", 11, "bold"))
ask_ai_button.pack(side=tk.LEFT, padx=10)

result_label = tk.Label(root, text="", font=('Arial', 14, 'bold'), fg=ACCENT_COLOR, bg=BG_COLOR, relief=tk.FLAT, borderwidth=0)
result_label.pack(pady=(10, 5))

mime_label = tk.Label(root, text="", font=('Arial', 10), fg=FG_COLOR, bg=BG_COLOR, relief=tk.FLAT, borderwidth=0)
mime_label.pack(pady=(5, 15))

def refresh_theme(mode):
    apply_tk_theme(root)
    calculate_button.apply_theme(mode)
    clear_button.apply_theme(mode)
    ask_ai_button.apply_theme(mode)

refresh_theme(get_theme_mode())
watch_theme(root, refresh_theme)
root.mainloop()
