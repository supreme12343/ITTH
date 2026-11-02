import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from fpdf import FPDF
import re
import threading
from campus_theme import ACCENT, ACCENT_HOVER, BG, CARD_LIGHT, TEXT, RoundedTkButton, apply_tk_theme, get_theme_mode, watch_theme

BG_COLOR = BG
FG_COLOR = TEXT
ACCENT_COLOR = ACCENT
INPUT_BG = CARD_LIGHT
INPUT_FG = TEXT
BUTTON_BG = ACCENT
BUTTON_ACTIVE_BG = ACCENT_HOVER

PDF_TITLE_COLOR = (0, 51, 102)
PDF_CONTACT_COLOR = (60, 60, 60)
PDF_SECTION_TITLE_COLOR = (0, 102, 204)
PDF_SECTION_BOX_FILL = (245, 245, 245)
PDF_CONTENT_COLOR = (0, 0, 0)


def generate_resume_pdf(data, filename):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(10, 10, 10)

    pdf.set_font("Arial", 'B', 16)
    pdf.set_text_color(*PDF_TITLE_COLOR)
    pdf.cell(0, 10, data['name'], ln=True, align='C')

    pdf.set_font("Arial", '', 11)
    pdf.set_text_color(*PDF_CONTACT_COLOR)
    contact_info = f"{data['email']} | {data['phone']}"
    if data.get("linkedin"): contact_info += f" | LinkedIn: {data['linkedin']}"
    if data.get("github"): contact_info += f" | GitHub: {data['github']}"
    pdf.cell(0, 10, contact_info, ln=True, align='C')
    pdf.ln(5)

    def draw_box(title, content, x, y, w, h):
        pdf.set_xy(x, y)
        pdf.set_fill_color(*PDF_SECTION_BOX_FILL)
        pdf.rect(x, y, w, h, style="D")
        pdf.set_font("Arial", 'B', 12)
        pdf.set_text_color(*PDF_SECTION_TITLE_COLOR)
        pdf.set_xy(x + 2, y + 2)
        pdf.multi_cell(w - 4, 6, title)

        pdf.set_font("Arial", '', 10)
        pdf.set_text_color(*PDF_CONTENT_COLOR)
        pdf.set_xy(x + 2, y + 10)
        pdf.multi_cell(w - 4, 5, content)

    col_w = 95
    x_pos = [10, 110]
    y = 40
    section_order = [
        ("Objective", data['objective']),
        ("Experience", data['experience']),
        ("Education", data['education']),
        ("Skills", data['skills']),
        ("Certifications", data['certifications']),
        ("Achievements", data['achievements']),
        ("Volunteer Work", data['volunteer']),
        ("Languages", data['languages']),
        ("Hobbies", data['hobbies']),
    ]

    for i, (title, content) in enumerate(section_order):
        if not content.strip():
            continue
        height = max(35, 5 + 5 * content.count('\n'))
        x = x_pos[i % 2]
        draw_box(title, content.strip(), x, y, col_w, height)
        if i % 2 == 1:
            y += height + 10

    pdf.output(filename)

# Sections the AI rewriter is allowed to touch — contact details are never sent.
AI_SECTIONS = ["objective", "experience", "education", "skills",
               "certifications", "achievements", "volunteer"]

AI_SYSTEM_PROMPT = (
    "You are a professional resume editor. Rewrite each section you are given into crisp, "
    "achievement-focused resume bullet points. Rules: start every bullet with a strong action verb; "
    "keep the numbers, tools, dates and employer names the writer supplied; never invent employers, "
    "dates, metrics, degrees or qualifications; keep each bullet to one line; write '- ' before every "
    "bullet. Return ONLY the rewritten sections, each introduced by the same [SECTION: name] marker "
    "you were given, with no commentary, preamble or markdown code fences."
)


def build_ai_prompt(drafted):
    """Turn the filled-in resume sections into a single marked-up rewrite request."""
    blocks = [f"[SECTION: {key}]\n{text}" for key, text in drafted.items()]
    return "Rewrite the following resume sections.\n\n" + "\n\n".join(blocks)


def parse_ai_sections(text):
    """Read [SECTION: name] blocks back out of the AI reply."""
    sections, current = {}, None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("[SECTION:") and stripped.endswith("]"):
            current = stripped[9:-1].strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items() if "\n".join(lines).strip()}

def open_resume_builder():
    window = tk.Tk()
    window.title("🎓 Resume Builder")
    window.geometry("720x750")
    window.configure(bg=BG_COLOR)

    canvas = tk.Canvas(window, bg=BG_COLOR, highlightthickness=0)
    scrollbar = tk.Scrollbar(window, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas, bg=BG_COLOR)

    scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    entries = {}

    status_label = tk.Label(scrollable_frame, text="", bg=BG_COLOR, fg=FG_COLOR, font=("Helvetica", 10, "italic"))
    status_label.grid(row=100, column=0, columnspan=2, pady=(5, 10))

    def submit():
        data = {
            key: entries[key].get("1.0", tk.END).strip() if isinstance(entries[key], tk.Text)
            else entries[key].get()
            for key in entries
        }
        required = ['name', 'email', 'phone', 'objective']
        if not all(data[k] for k in required):
            messagebox.showerror("Input Error", "Please fill in Name, Email, Phone, and Objective/Summary.")
            status_label.config(text="❌ Required fields missing!", fg="#dc3545")
            return

        if not re.match(r"[^@]+@[^@]+\.[^@]+", data['email']):
            messagebox.showerror("Invalid Email", "Email format should be xyz@abc.def")
            status_label.config(text="❌ Invalid email format!", fg="#dc3545")
            return

        file_path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")])
        if file_path:
            try:
                generate_resume_pdf(data, file_path)
                status_label.config(text=f"✅ Resume saved to: {file_path.split('/')[-1]}", fg="#28a745")
            except Exception as e:
                messagebox.showerror("PDF Generation Error", f"Failed to generate PDF: {e}")
                status_label.config(text="❌ PDF generation failed!", fg="#dc3545")
        else:
            status_label.config(text="ℹ️ Save cancelled.", fg="#6c757d")

    def improve_with_ai():
        """Rewrite the filled-in sections into stronger bullet points. Opt-in, never automatic."""
        drafted = {key: entries[key].get("1.0", tk.END).strip()
                   for key in AI_SECTIONS
                   if isinstance(entries.get(key), tk.Text) and entries[key].get("1.0", tk.END).strip()}
        if not drafted:
            messagebox.showinfo("Nothing to rewrite",
                                "Fill in Objective, Work Experience, or another section first.")
            return
        if not messagebox.askyesno(
                "Rewrite with Campus AI?",
                "This sends the resume sections you filled in to the configured Campus Suite AI "
                "provider so it can rewrite them as stronger bullet points.\n\n"
                "Your name, email, phone, LinkedIn and GitHub are not sent. Review the suggestion "
                "before applying it."):
            return

        status_label.config(text="✨ Rewriting your sections with Campus AI…", fg=ACCENT_COLOR)

        preview = tk.Toplevel(window)
        preview.title("Campus AI · Resume Bullets")
        preview.geometry("720x560")
        preview.configure(bg=BG_COLOR)
        tk.Label(preview, text="Suggested resume bullets", font=("Helvetica", 15, "bold"),
                 bg=BG_COLOR, fg=ACCENT_COLOR).pack(anchor="w", padx=16, pady=(14, 4))
        preview_status = tk.Label(preview, text="Asking the configured Campus Suite AI provider…",
                                  bg=BG_COLOR, fg=FG_COLOR, font=("Helvetica", 10))
        preview_status.pack(anchor="w", padx=16, pady=(0, 8))
        preview_text = scrolledtext.ScrolledText(preview, wrap=tk.WORD, font=("Helvetica", 10),
                                                 bg=INPUT_BG, fg=INPUT_FG, relief=tk.FLAT,
                                                 borderwidth=1, insertbackground=ACCENT_COLOR)
        preview_text.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))
        preview_text.insert("1.0", "Working…")
        preview_text.config(state="disabled")

        button_row = tk.Frame(preview, bg=BG_COLOR)
        button_row.pack(fill=tk.X, padx=16, pady=(0, 14))
        apply_holder = {"sections": {}}

        def apply_suggestion():
            applied = 0
            for key, text in apply_holder["sections"].items():
                widget = entries.get(key)
                if isinstance(widget, tk.Text):
                    widget.delete("1.0", tk.END)
                    widget.insert("1.0", text)
                    applied += 1
            if applied:
                status_label.config(text=f"✨ Applied AI bullets to {applied} section(s).", fg="#28a745")
                preview.destroy()
            else:
                messagebox.showinfo("Nothing to apply",
                                    "The reply did not contain any recognised sections.", parent=preview)

        apply_button = tk.Button(button_row, text="Apply to form", command=apply_suggestion,
                                 state="disabled", relief=tk.FLAT, bg=BUTTON_BG, fg="#ffffff",
                                 activebackground=BUTTON_ACTIVE_BG, font=("Helvetica", 10, "bold"),
                                 padx=14, pady=6)
        apply_button.pack(side=tk.LEFT)
        tk.Button(button_row, text="Close", command=preview.destroy, relief=tk.FLAT,
                  bg=INPUT_BG, fg=INPUT_FG, font=("Helvetica", 10), padx=14, pady=6).pack(side=tk.LEFT, padx=8)

        def worker():
            try:
                from campus_ai import ask_campus_ai
                reply = ask_campus_ai(AI_SYSTEM_PROMPT, build_ai_prompt(drafted), role="reporter")
                sections = parse_ai_sections(reply)
                error = False
            except Exception as exc:
                reply, sections, error = str(exc), {}, True

            def finish():
                if not preview.winfo_exists():
                    return
                preview_status.config(text="Campus AI could not answer" if error
                                      else "Review the suggestion, then apply it if you like it")
                preview_text.config(state="normal")
                preview_text.delete("1.0", tk.END)
                preview_text.insert("1.0", reply)
                preview_text.config(state="disabled")
                apply_holder["sections"] = sections
                if sections:
                    apply_button.config(state="normal")
                status_label.config(text="❌ Campus AI rewrite failed." if error
                                    else "✨ AI bullets ready — review them in the Campus AI window.",
                                    fg="#dc3545" if error else "#28a745")

            try:
                window.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    tk.Label(scrollable_frame, text="🎓 Resume Builder", font=("Helvetica", 26, "bold"),
             bg=BG_COLOR, fg=ACCENT_COLOR).grid(row=0, column=0, columnspan=2, pady=(30, 30))

    def add_row(label, key, row, height=3, entry_type="entry"):
        tk.Label(scrollable_frame, text=label, bg=BG_COLOR, fg=FG_COLOR,
                 font=("Helvetica", 11), anchor="w").grid(row=row, column=0, sticky="w", padx=20, pady=5)
        if entry_type == "entry":
            entry = tk.Entry(scrollable_frame, width=55, font=("Helvetica", 10),
                             bg=INPUT_BG, fg=INPUT_FG, insertbackground=ACCENT_COLOR,
                             relief=tk.FLAT, borderwidth=1, highlightthickness=1,
                             highlightbackground=BG_COLOR, highlightcolor=ACCENT_COLOR)
        else:
            entry = tk.Text(scrollable_frame, width=55, height=height, font=("Helvetica", 10),
                            bg=INPUT_BG, fg=INPUT_FG, insertbackground=ACCENT_COLOR,
                            relief=tk.FLAT, borderwidth=1, highlightthickness=1,
                            highlightbackground=BG_COLOR, highlightcolor=ACCENT_COLOR)
        entry.grid(row=row, column=1, padx=10, pady=5, sticky="w")
        entries[key] = entry

    fields = [
        ("Full Name", "name", "entry"),
        ("Email", "email", "entry"),
        ("Phone", "phone", "entry"),
        ("LinkedIn", "linkedin", "entry"),
        ("GitHub", "github", "entry"),
        ("Objective", "objective", "text"),
        ("Work Experience", "experience", "text"),
        ("Education", "education", "text"),
        ("Skills", "skills", "text"),
        ("Certifications", "certifications", "text"),
        ("Achievements", "achievements", "text"),
        ("Volunteer Work", "volunteer", "text"),
        ("Languages Known", "languages", "text"),
        ("Hobbies", "hobbies", "text"),
    ]

    for i, (label, key, etype) in enumerate(fields):
        add_row(label, key, i + 1, height=4, entry_type=etype)

    ai_button = RoundedTkButton(scrollable_frame, "✨ Rewrite Bullets with AI", improve_with_ai,
                                variant="secondary", width=270, height=44,
                                font=("Helvetica", 11, "bold"))
    ai_button.grid(row=len(fields)+2, column=1, pady=(30, 0))

    generate_button = RoundedTkButton(scrollable_frame, "📄 Generate Resume PDF", submit,
                                      variant="primary", width=270, height=48,
                                      font=("Helvetica", 12, "bold"))
    generate_button.grid(row=len(fields)+3, column=1, pady=(12, 30))

    def refresh_theme(mode):
        apply_tk_theme(window)
        generate_button.apply_theme(mode)
        ai_button.apply_theme(mode)

    refresh_theme(get_theme_mode())
    watch_theme(window, refresh_theme)
    window.mainloop()

open_resume_builder()

