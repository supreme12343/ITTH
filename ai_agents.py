import json
import os
import threading
import subprocess
import sys
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from google import genai
from google.genai import types

from key_manager import AiApi, MockRoleKeyManager, RoleKeyManager
from study_agent import StudyAgent
from agents.personal_growth_mentor import PersonalGrowthMentor
from coding_agent import CodingAgent
from video_editing_agent import VideoEditingAgent
from unified_memory import record_activity
from campus_theme import get_theme_mode, get_theme_palette, watch_theme
from agent_history import append_message, list_conversations, load_conversation, new_conversation
try:
    from blender_agent import BlenderMasterOrchestrator, BlenderSocket, ProjectMemory
    _BLENDER_AGENT_AVAILABLE = True
except Exception:
    BlenderMasterOrchestrator = BlenderSocket = ProjectMemory = None
    _BLENDER_AGENT_AVAILABLE = False

try:
    from markitdown import MarkItDown
    _mid = MarkItDown()
    _MARKITDOWN_AVAILABLE = True
except ImportError:
    _mid = None
    _MARKITDOWN_AVAILABLE = False

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
SCRIPT_DIR     = Path(__file__).resolve().parent
KITS_DB_PATH   = SCRIPT_DIR / "kits_db.json"

DEMO_MODE = False          # flipped by the Demo Mode toggle in the GUI

_ai_api = None
_ai_api_error = None
try:
    _ai_api = AiApi()
except Exception as e:
    _ai_api_error = str(e)


def get_ai_api():
    """Return the active AiApi instance — real or mock depending on DEMO_MODE."""
    if DEMO_MODE:
        return AiApi.create_mock()
    if _ai_api is None and _ai_api_error:
        raise RuntimeError(f"AI API unavailable: {_ai_api_error}")
    return _ai_api


def _get_manager():
    """Backward compatibility: return the underlying key manager."""
    return get_ai_api()._km

# ---------------------------------------------------------------------------
# THEME & COLORS
# ---------------------------------------------------------------------------
ctk.set_appearance_mode("Dark" if get_theme_mode() == "dark" else "Light")
ctk.set_default_color_theme("green")

# Keep every agent inside the Campus Suite visual language.  Individual
# agents still get their own action colour, but surfaces, typography and the
# navigation treatment stay recognisably part of the same application.
BG           = "#1e1e2f"   # Campus Suite window background
CARD         = "#27273b"
CARD_LIGHT   = "#34344d"
BORDER       = "#4a4a68"
ACCENT       = "#007aff"   # Campus Suite AI Agents blue
ACCENT_HOVER = "#0060c4"
TEXT         = "#f1f5f9"
TEXT_MUTED   = "#b1b1c7"
TITLE        = "#61dafb"   # Campus Suite cyan heading
GREEN        = "#34c759"
RED          = "#ff3b30"
GOLD         = "#ffd60a"
CORNER       = 25
SECTION_CORNER = 18

# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------
def load_kits_db():
    if KITS_DB_PATH.exists():
        try:
            with open(KITS_DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def combined_markdown(kit):
    """Concatenate all stored markdown files for a kit."""
    parts = []
    for fp in kit.get("files", []):
        try:
            p = Path(fp)
            if not p.is_absolute():
                p = SCRIPT_DIR / p
            parts.append(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# MAIN APPLICATION
# ---------------------------------------------------------------------------
def _apply_agent_palette(mode=None):
    global BG, CARD, CARD_LIGHT, BORDER, ACCENT, ACCENT_HOVER
    global TEXT, TEXT_MUTED, TITLE, GREEN, RED, GOLD
    palette = get_theme_palette(mode)
    BG, CARD, CARD_LIGHT, BORDER = palette["BG"], palette["CARD"], palette["CARD_LIGHT"], palette["BORDER"]
    ACCENT, ACCENT_HOVER = palette["ACCENT"], palette["ACCENT_HOVER"]
    TEXT, TEXT_MUTED, TITLE = palette["TEXT"], palette["TEXT_MUTED"], palette["TITLE"]
    GREEN, RED = palette["GREEN"], palette["RED"]
    GOLD = "#ee9b62" if mode != "dark" else "#f0b57d"
    ctk.set_appearance_mode("Dark" if (mode or get_theme_mode()) == "dark" else "Light")


_apply_agent_palette()


class AIAgentsApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Campus Suite — AI Agents")
        self.geometry("980x780")
        self.minsize(700, 600)
        self.configure(fg_color=BG)
        self._theme_mode = get_theme_mode()
        self._theme_cancel = watch_theme(self, self._on_theme_change)
        self._active_agent_id = None

        # Main view container
        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.pack(fill="both", expand=True)
        self.main.grid_rowconfigure(0, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        self._show_agents_hub()

    def _clear_main(self):
        for w in self.main.winfo_children():
            w.destroy()

    def _on_theme_change(self, mode):
        previous = get_theme_palette(self._theme_mode)
        updated = get_theme_palette(mode)
        self._theme_mode = mode
        _apply_agent_palette(mode)
        try:
            self.configure(fg_color=BG)
            replacements = {previous[key]: updated[key] for key in previous if key in updated}
            self._recolor_agent_widgets(self, replacements)
            if self._active_agent_id is None:
                self._show_agents_hub()
        except Exception:
            pass

    def _recolor_agent_widgets(self, parent, replacements):
        for child in parent.winfo_children():
            try:
                options = child.configure()
                updates = {}
                for option in ("fg_color", "bg_color", "border_color", "text_color", "hover_color", "button_color", "button_hover_color", "progress_color"):
                    if option not in options:
                        continue
                    value = child.cget(option)
                    if isinstance(value, tuple):
                        converted = tuple(replacements.get(item, item) for item in value)
                        if converted != value:
                            updates[option] = converted
                    elif value in replacements:
                        updates[option] = replacements[value]
                if updates:
                    child.configure(**updates)
                self._recolor_agent_widgets(child, replacements)
            except Exception:
                continue

    def _hide_agent_rail(self):
        refresh_job = getattr(self, "_agent_history_refresh_job", None)
        if refresh_job is not None:
            try:
                self.after_cancel(refresh_job)
            except Exception:
                pass
            self._agent_history_refresh_job = None
        self.agent_history_list = None
        rail = getattr(self, "agent_rail", None)
        if rail is not None:
            rail.destroy()
            self.agent_rail = None
        self.main.place_forget()
        self.main.pack(fill="both", expand=True)

    def _show_agent_rail(self, agent_id, title, view, conversation_id=None):
        self._active_agent_id = agent_id
        rail = getattr(self, "agent_rail", None)
        if rail is not None:
            rail.destroy()
        self.main.pack_forget()
        self.main.place(relx=0.245, rely=0, relwidth=0.755, relheight=1)

        rail = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, border_width=0)
        rail.place(relx=0, rely=0, relwidth=0.245, relheight=1)
        self.agent_rail = rail
        ctk.CTkLabel(rail, text="campus suite", font=ctk.CTkFont("Arial", 18, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=22, pady=(24, 3))
        ctk.CTkLabel(rail, text="AI AGENTS  /  " + title.upper(), font=ctk.CTkFont("Arial", 9, "bold"),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 18))
        ctk.CTkButton(rail, text="← All agents", command=self._show_agents_hub,
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT,
                      corner_radius=10, height=36, anchor="w").pack(fill="x", padx=14, pady=(0, 18))
        ctk.CTkFrame(rail, height=1, fg_color=BORDER).pack(fill="x", padx=14, pady=(0, 16))
        ctk.CTkLabel(rail, text="CHAT HISTORY", font=ctk.CTkFont("Arial", 9, "bold"),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=20, pady=(0, 8))

        ctk.CTkButton(rail, text="＋  New conversation", command=lambda: self._open_agent_workspace(agent_id, title, view),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white" if self._theme_mode == "light" else "#233326",
                      corner_radius=10, height=36).pack(fill="x", padx=14, pady=(0, 10))
        history_list = ctk.CTkScrollableFrame(rail, fg_color="transparent")
        history_list.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self.agent_history_list = history_list
        self._agent_history_context = (agent_id, title, view)
        self._refresh_agent_history_items()
        ctk.CTkLabel(rail, text="Your conversations stay separate for each agent.",
                     font=ctk.CTkFont("Arial", 9), text_color=TEXT_MUTED,
                     wraplength=190, justify="left").pack(anchor="w", padx=20, pady=(4, 18))

    def _refresh_agent_history_items(self):
        history_list = getattr(self, "agent_history_list", None)
        context = getattr(self, "_agent_history_context", None)
        if history_list is None or context is None:
            return
        agent_id, title, view = context
        for child in history_list.winfo_children():
            child.destroy()
        for item in list_conversations(agent_id):
            def open_conversation(selected=item):
                if agent_id == "study":
                    self._show_study_agent_view(conversation_id=selected["id"])
                elif agent_id == "personal-growth":
                    self._show_personal_growth_mentor_view(conversation_id=selected["id"])
                elif agent_id == "video-editing":
                    self._show_video_editing_agent_view(conversation_id=selected["id"])
                else:
                    self._show_saved_conversation(agent_id, title, view, selected["id"])
            ctk.CTkButton(history_list, text="▤  " + (item.get("title") or "New conversation"),
                          command=open_conversation, fg_color="transparent", hover_color=CARD_LIGHT,
                          text_color=TEXT, corner_radius=8, height=34, anchor="w").pack(fill="x", padx=4, pady=2)
        try:
            self._agent_history_refresh_job = self.after(1800, self._refresh_agent_history_items)
        except Exception:
            self._agent_history_refresh_job = None

    def _show_saved_conversation(self, agent_id, title, view, conversation_id):
        conversation = load_conversation(agent_id, conversation_id)
        if not conversation:
            return
        self._clear_main()
        page = ctk.CTkFrame(self.main, fg_color=BG)
        page.pack(fill="both", expand=True, padx=30, pady=24)
        header = ctk.CTkFrame(page, fg_color="transparent")
        header.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(header, text=conversation.get("title") or title,
                     font=ctk.CTkFont("Arial", 20, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkButton(header, text="New conversation", command=lambda: self._open_agent_workspace(agent_id, title, view),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white" if self._theme_mode == "light" else "#233326",
                      corner_radius=10, height=34).pack(side="right")
        transcript = ctk.CTkScrollableFrame(page, fg_color="transparent")
        transcript.pack(fill="both", expand=True)
        for message in conversation.get("messages", []):
            role = message.get("role", "assistant")
            is_user = role == "user"
            bubble = ctk.CTkFrame(transcript, fg_color=ACCENT if is_user else CARD, corner_radius=14)
            bubble.pack(fill="x", padx=(100, 0) if is_user else (0, 100), pady=5)
            ctk.CTkLabel(bubble, text=message.get("content", ""), text_color="white" if is_user else TEXT,
                         font=ctk.CTkFont("Arial", 12), wraplength=620, justify="left").pack(anchor="w", padx=14, pady=11)
        self._active_agent_id = agent_id
        self._refresh_agent_rail(agent_id, title, view)

    def _refresh_agent_rail(self, agent_id, title, view):
        rail = getattr(self, "agent_rail", None)
        if rail is None:
            self._show_agent_rail(agent_id, title, view)
            return
        self._agent_history_context = (agent_id, title, view)
        self._refresh_agent_history_items()

    def _open_agent_workspace(self, agent_id, title, view):
        pending = getattr(self, "_workspace_open_job", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except Exception:
                pass
        self._active_agent_id = agent_id
        self._hide_agent_rail()
        self._clear_main()
        loading = ctk.CTkFrame(self.main, fg_color=BG)
        loading.grid(row=0, column=0, sticky="nsew")
        loading.grid_rowconfigure(0, weight=1)
        loading.grid_columnconfigure(0, weight=1)
        panel = ctk.CTkFrame(loading, fg_color=CARD, corner_radius=18)
        panel.grid(row=0, column=0, padx=32, pady=32)
        ctk.CTkLabel(panel, text=f"Opening {title}", font=ctk.CTkFont("Arial", 20, "bold"),
                     text_color=TITLE).pack(padx=32, pady=(28, 6))
        ctk.CTkLabel(panel, text="Preparing your workspace…", font=ctk.CTkFont("Arial", 12),
                     text_color=TEXT_MUTED).pack(padx=32, pady=(0, 16))
        progress = ctk.CTkProgressBar(panel, mode="indeterminate", width=220)
        progress.pack(padx=32, pady=(0, 28))
        progress.start()

        def finish_open():
            if self._active_agent_id != agent_id:
                return
            progress.stop()
            try:
                view()
            except Exception as exc:
                self._clear_main()
                error_panel = ctk.CTkFrame(self.main, fg_color=CARD, corner_radius=18)
                error_panel.grid(row=0, column=0, padx=32, pady=32)
                ctk.CTkLabel(error_panel, text=f"{title} could not open", font=ctk.CTkFont("Arial", 20, "bold"),
                             text_color=RED).pack(padx=28, pady=(24, 8))
                ctk.CTkLabel(error_panel, text=str(exc), font=ctk.CTkFont("Arial", 12),
                             text_color=TEXT_MUTED, wraplength=500).pack(padx=28, pady=(0, 20))
                ctk.CTkButton(error_panel, text="← All agents", command=self._show_agents_hub,
                              fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(padx=28, pady=(0, 24))
            self._show_agent_rail(agent_id, title, view)

        self._workspace_open_job = self.after(60, finish_open)

    # -----------------------------------------------------------------------
    # VIEW: AI AGENTS HUB
    # -----------------------------------------------------------------------
    def _show_agents_hub(self):
        global DEMO_MODE
        self._active_agent_id = None
        self._hide_agent_rail()
        record_activity("ai-agents", "opened-hub")
        self._clear_main()
        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # ---- Header row (title + Demo Mode toggle) ----
        hdr_row = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr_row.pack(fill="x", padx=36, pady=(32, 4))

        ctk.CTkLabel(hdr_row, text="Campus Suite  /  AI & Create",
                     font=ctk.CTkFont("Arial", 24, "bold"),
                     text_color=TEXT).pack(side="left")

        # Demo Mode toggle
        demo_color  = GOLD if DEMO_MODE else CARD_LIGHT
        demo_text   = "🔵 Demo Mode ON" if DEMO_MODE else "🔵 Demo Mode"
        demo_hover  = "#b89400" if DEMO_MODE else BORDER

        def toggle_demo():
            global DEMO_MODE
            DEMO_MODE = not DEMO_MODE
            self._show_agents_hub()   # re-render

        ctk.CTkButton(hdr_row, text=demo_text,
                      font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=demo_color, hover_color=demo_hover,
                      text_color=TEXT, corner_radius=14,
                      height=32, width=150,
                      command=toggle_demo).pack(side="right")

        ctk.CTkLabel(scroll,
                     text="Choose a specialist and pick up where you left off. Your conversations are saved separately for each agent." +
                          ("  (Running in DEMO MODE — no real API calls)" if DEMO_MODE else ""),
                     font=ctk.CTkFont("Arial", 12),
                     text_color=GOLD if DEMO_MODE else TEXT_MUTED).pack(anchor="w", padx=36, pady=(0, 20))

# ---- Error banner (only when no real keys AND not in demo mode) ----
        api = get_ai_api() if not DEMO_MODE else None
        status = api.get_status() if api else {"keys": [], "omniroute_available": False}
        has_real_keys = api is not None and (status['omniroute_available'] or any(k.get("available") and k.get("mode") == "fallback" for k in status['keys']))
        
        if not has_real_keys and not DEMO_MODE:
            warn = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=16, border_color=RED, border_width=1)
            warn.pack(fill="x", padx=36, pady=(0, 20))
            ctk.CTkLabel(warn, text="AI Agents unavailable — no AI connection",
                         font=ctk.CTkFont("Arial", 13, "bold"), text_color=RED).pack(anchor="w", padx=20, pady=(14, 2))

            # Check if we have Gemini keys but OmniRoute is down
            has_gemini = any(k.get("available") and k.get("mode") == "fallback" for k in status['keys'])
            if has_gemini:
                ctk.CTkLabel(warn, text="OmniRoute disconnected — Gemini fallback keys are loaded and active.",
                             font=ctk.CTkFont("Arial", 11), text_color=GOLD,
                             wraplength=700, justify="left").pack(anchor="w", padx=20, pady=(0, 14))
            else:
                ctk.CTkLabel(warn, text=_ai_api_error or "OmniRoute not connected and no Gemini keys",
                             font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED,
                             wraplength=700, justify="left").pack(anchor="w", padx=20, pady=(0, 14))

            ctk.CTkLabel(warn,
                         text="Tip: Toggle Demo Mode above to explore the full UI without real keys.",
                         font=ctk.CTkFont("Arial", 11, "bold"), text_color=GOLD).pack(anchor="w", padx=20, pady=(0, 14))

        ctk.CTkLabel(scroll, text="Your agents", font=ctk.CTkFont("Arial", 18, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=36, pady=(0, 9))
        cards_row = ctk.CTkFrame(scroll, fg_color="transparent")
        cards_row.pack(fill="x", padx=30, pady=(0, 20))

        _can_use = DEMO_MODE or has_real_keys

        agent_defs = [
            ("🎓", "Study Agent", ACCENT,
             "Ask anything. Factual questions get search-grounded + double-checked "
             "(keys 1-2). Study tasks — quiz, flashcards, summaries, plans, grading — "
             "run through a tool-calling loop (keys 3-5).",
             self._show_study_agent_view),
            ("🌱", "Personal Growth Mentor", "#20a387",
             "Students explore IT strengths and track learning goals; teachers get practical prompts for supportive mentoring.",
             self._show_personal_growth_mentor_view),
            ("🎯", "Quiz Generator", "#34c759",
             "Upload any PDF, DOCX, or PPTX and instantly generate a formatted quiz. "
             "Pick topic, difficulty, question count, and type. Export the quiz as a "
             "text file for printing or sharing.",
             self._show_quiz_generator_view),
            ("💻", "Coding Agent", "#007aff",
             "Thinker → Designer → Coder → Checker → CEO. Five dedicated keys, five "
             "roles, one pipeline. Coder is the only one with real file access; "
             "Checker reviews every diff before the CEO reports back.",
             self._show_coding_agent_view),
            ("🎬", "Video Editing Agent", "#e84c3d",
             "Live After Effects control via HTTP + ExtendScript. Send natural-language "
             "prompts (add text, glow, color grade, render) — executed instantly in AE.",
             self._show_video_editing_agent_view),
            ("🧊", "Blender Agent", "#26a272",
             "Project-aware BlenderMCP editing. It inspects the open .blend file, remembers verified "
             "work, and preserves existing scene data unless you explicitly request a reset.",
             self._show_blender_agent_view),
            ("🏫", "MAIC Classroom", "#722ed1",
             "Generate interactive lessons, slides, quizzes, simulations, and project-based learning workspaces in the Campus Suite classroom.",
             self._launch_maic),
            ("📊", "Teacher Audit Logs", GOLD,
             "View step-by-step logs of all key invocations. Inspect the exact "
             "prompts, responses, and token counts used by the Study and Coding Agents.",
             self._show_audit_logs_view),
        ]
        for i, (icon, name, color, desc, cmd) in enumerate(agent_defs):
            card = ctk.CTkFrame(cards_row, fg_color=CARD, corner_radius=CORNER, border_color=BORDER, border_width=1)
            row_index, column_index = divmod(i, 3)
            card.grid(row=row_index, column=column_index, padx=7, pady=7, sticky="nsew")
            cards_row.grid_columnconfigure(column_index, weight=1, uniform="agent-cards")
            cards_row.grid_rowconfigure(row_index, weight=1)

            icon_box = ctk.CTkFrame(card, fg_color=color, width=50, height=50, corner_radius=12)
            icon_box.pack(anchor="w", padx=24, pady=(24, 10))
            icon_box.pack_propagate(False)
            ctk.CTkLabel(icon_box, text=icon, font=ctk.CTkFont("Arial", 22)).place(relx=0.5, rely=0.5, anchor="center")

            ctk.CTkLabel(card, text=name, font=ctk.CTkFont("Arial", 17, "bold"),
                         text_color=TEXT).pack(anchor="w", padx=24)
            ctk.CTkLabel(card, text=desc, font=ctk.CTkFont("Arial", 11),
                         text_color=TEXT_MUTED, wraplength=200, justify="left").pack(anchor="w", padx=24, pady=(8, 20))

            btn_state = "normal" if (name == "Teacher Audit Logs" or _can_use) else "disabled"
            def open_agent(agent_name=name, agent_cmd=cmd):
                if agent_name == "MAIC Classroom":
                    agent_cmd()
                    return
                agent_id = {
                    "Study Agent": "study", "Quiz Generator": "quiz",
                    "Personal Growth Mentor": "personal-growth",
                    "Coding Agent": "coding", "Video Editing Agent": "video-editing",
                    "Blender Agent": "blender", "Teacher Audit Logs": "audit",
                }.get(agent_name, "general")
                self._open_agent_workspace(agent_id, agent_name, agent_cmd)

            ctk.CTkButton(card, text="Open agent  →", font=ctk.CTkFont("Arial", 12, "bold"),
                          fg_color=color, hover_color=ACCENT_HOVER, text_color="white",
                          corner_radius=18, height=38, width=132,
                          state=btn_state,
                          command=open_agent).pack(anchor="w", padx=24, pady=(0, 24))

        # ---- Key status strip ----
        mgr = _get_manager()
        if mgr:
            status_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=16)
            status_card.pack(fill="x", padx=30, pady=(0, 30))
            lbl_text = "Key Status  (DEMO — simulated)" if DEMO_MODE else "Key Status"
            ctk.CTkLabel(status_card, text=lbl_text,
                         font=ctk.CTkFont("Arial", 12, "bold"),
                         text_color=GOLD if DEMO_MODE else TEXT).pack(anchor="w", padx=20, pady=(14, 6))
            row = ctk.CTkFrame(status_card, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=(0, 16))
            for s in mgr.status():
                dot_col = GREEN if s["available"] else RED
                chip = ctk.CTkFrame(row, fg_color=CARD_LIGHT, corner_radius=10)
                chip.pack(side="left", padx=4)
                inner = ctk.CTkFrame(chip, fg_color="transparent")
                inner.pack(padx=10, pady=6)
                ctk.CTkFrame(inner, fg_color=dot_col, width=8, height=8, corner_radius=4).pack(side="left", padx=(0, 6))
                ctk.CTkLabel(inner, text=s["label"], font=ctk.CTkFont("Arial", 11), text_color=TEXT).pack(side="left")


    def _launch_maic(self):
        bridge = SCRIPT_DIR / "maic_bridge.py"
        if not bridge.exists():
            messagebox.showerror("MAIC unavailable", "The MAIC launcher was not found.")
            return
        try:
            subprocess.Popen([sys.executable, str(bridge)], cwd=str(SCRIPT_DIR))
            record_activity("ai-agents", "opened-maic")
        except OSError as exc:
            messagebox.showerror("Could not start MAIC", str(exc))

    def _agent_kit_picker(self, parent):
        """Small dropdown of existing kits, used as optional context for the Study Agent."""
        db = load_kits_db()
        flat = [(f"{k['name']}  ·  {cat}", k) for cat, kits in db.items() for k in kits]
        names = ["No kit — general chat"] + [n for n, _ in flat]
        var = ctk.StringVar(value=names[0])
        menu = ctk.CTkOptionMenu(parent, values=names, variable=var,
                                  fg_color=CARD_LIGHT, button_color=BORDER,
                                  button_hover_color=ACCENT, text_color=TEXT,
                                  font=ctk.CTkFont("Arial", 12), width=240)
        lookup = {n: k for n, k in flat}
        return menu, var, lookup

    # -----------------------------------------------------------------------
    # VIEW: STUDY AGENT — chat, search-grounded QA + tool-calling
    # -----------------------------------------------------------------------
    def _show_study_agent_view(self, kit=None, conversation_id=None):
        self._clear_main()
        self._sa_history = []          # list[types.Content]
        self._sa_selected_kit = None
        saved_conversation = load_conversation("study", conversation_id) if conversation_id else None
        if saved_conversation is None:
            saved_conversation = new_conversation("study")
        self._sa_conversation_id = saved_conversation["id"]

        outer = ctk.CTkFrame(self.main, fg_color=BG)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(24, 8))
        ctk.CTkLabel(hdr, text="🎓  Study Agent",
                     font=ctk.CTkFont("Arial", 22, "bold"), text_color=TITLE).pack(side="left")
        ctk.CTkButton(hdr, text="← Agents", font=ctk.CTkFont("Arial", 11),
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                      corner_radius=14, height=30, width=90,
                      command=self._show_agents_hub).pack(side="left", padx=(14, 0))

        picker_menu, picker_var, kit_lookup = self._agent_kit_picker(hdr)
        picker_menu.pack(side="right")
        ctk.CTkLabel(hdr, text="Context:", font=ctk.CTkFont("Arial", 11),
                     text_color=TEXT_MUTED).pack(side="right", padx=(0, 8))

        chat_scroll = ctk.CTkScrollableFrame(outer, fg_color=BG)
        chat_scroll.grid(row=1, column=0, sticky="nsew")
        chat_scroll.grid_columnconfigure(0, weight=1)

        def add_bubble(role, text, tag=None):
            is_user = (role == "user")
            row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=20)
            bubble = ctk.CTkFrame(row, fg_color=ACCENT if is_user else CARD, corner_radius=16)
            bubble.pack(anchor="e" if is_user else "w", padx=(120, 16) if is_user else (16, 120))
            if tag and not is_user:
                ctk.CTkLabel(bubble, text=tag, font=ctk.CTkFont("Arial", 9, "bold"),
                             text_color=ACCENT).pack(anchor="w", padx=14, pady=(8, 0))
            ctk.CTkLabel(bubble, text=text, font=ctk.CTkFont("Arial", 12),
                         text_color="white" if is_user else TEXT,
                         justify="left", wraplength=560).pack(padx=14, pady=(2, 10) if tag else 10)
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

        if saved_conversation["messages"]:
            for saved_message in saved_conversation["messages"]:
                role = saved_message.get("role", "assistant")
                content = saved_message.get("content", "")
                add_bubble("user" if role == "user" else "ai", content,
                           tag=(saved_message.get("metadata") or {}).get("tag"))
                if role in ("user", "assistant"):
                    self._sa_history.append(types.Content(
                        role="user" if role == "user" else "model",
                        parts=[types.Part(text=content)],
                    ))
        else:
            add_bubble("ai", "Hi! Ask me anything — general questions get search-grounded and "
                              "double-checked before you see them. Say things like 'quiz me on...' "
                              "or 'make flashcards for...' to trigger the study tools.")

        input_bar = ctk.CTkFrame(outer, fg_color=CARD, corner_radius=0)
        input_bar.grid(row=2, column=0, sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(input_bar, placeholder_text="Ask the Study Agent…",
                               font=ctk.CTkFont("Arial", 13), fg_color=CARD_LIGHT,
                               border_color=BORDER, text_color=TEXT, height=48, corner_radius=14)
        entry.grid(row=0, column=0, padx=(16, 8), pady=14, sticky="ew")
        send_btn = ctk.CTkButton(input_bar, text="Send  ➤", font=ctk.CTkFont("Arial", 13, "bold"),
                                  fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                                  corner_radius=14, height=44, width=110)
        send_btn.grid(row=0, column=1, padx=(0, 16), pady=14)

        key_manager = _get_manager()
        if key_manager is None:
            raise RuntimeError(_role_key_error or "The role key manager could not be initialized.")
        agent = StudyAgent(key_manager)

        def send(event=None):
            msg = entry.get().strip()
            if not msg:
                return
            entry.delete(0, "end")
            send_btn.configure(state="disabled", text="…")
            add_bubble("user", msg)
            append_message("study", self._sa_conversation_id, "user", msg)

            typing_row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            typing_row.pack(fill="x", pady=4, padx=20, anchor="w")
            typing_bubble = ctk.CTkFrame(typing_row, fg_color=CARD, corner_radius=16)
            typing_bubble.pack(anchor="w", padx=(16, 120))
            ctk.CTkLabel(typing_bubble, text="Agent is working…", font=ctk.CTkFont("Arial", 12, slant="italic"),
                         text_color=TEXT_MUTED).pack(padx=14, pady=10)
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

            selected_kit = kit_lookup.get(picker_var.get())
            material = combined_markdown(selected_kit) if selected_kit else "(no study material selected)"

            def task():
                try:
                    result = agent.chat(material, self._sa_history, msg)

                    def done():
                        typing_row.destroy()
                        self._sa_history = result["history"]
                        tag = ("🔎 search-grounded (" + " + ".join(result["keys_used"]) + ")"
                               if result["mode"] == "search"
                               else "🛠️ tools (" + " + ".join(result["keys_used"]) + ")")
                        add_bubble("ai", result["reply"], tag=tag)
                        append_message("study", self._sa_conversation_id, "assistant", result["reply"],
                                       metadata={"tag": tag})
                        send_btn.configure(state="normal", text="Send  ➤")

                    self.after(0, done)
                except Exception as e:
                    err = str(e)
                    def fail():
                        typing_row.destroy()
                        add_bubble("ai", f"⚠️ {err}")
                        append_message("study", self._sa_conversation_id, "assistant", f"⚠️ {err}")
                        send_btn.configure(state="normal", text="Send  ➤")
                    self.after(0, fail)

            threading.Thread(target=task, daemon=True).start()

        send_btn.configure(command=send)
        entry.bind("<Return>", send)

    # -----------------------------------------------------------------------
    # VIEW: PERSONAL GROWTH MENTOR — student goals + tool-backed check-ins
    # -----------------------------------------------------------------------
    def _show_personal_growth_mentor_view(self, conversation_id=None):
        self._clear_main()
        saved_conversation = load_conversation("personal-growth", conversation_id) if conversation_id else None
        if saved_conversation is None:
            saved_conversation = new_conversation("personal-growth")
        self._pgm_conversation_id = saved_conversation["id"]
        self._pgm_history = []

        outer = ctk.CTkFrame(self.main, fg_color=BG)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(24, 8))
        ctk.CTkLabel(hdr, text="🌱  Personal Growth Mentor",
                     font=ctk.CTkFont("Arial", 22, "bold"), text_color=TITLE).pack(side="left")
        ctk.CTkButton(hdr, text="← Agents", font=ctk.CTkFont("Arial", 11),
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                      corner_radius=14, height=30, width=90,
                      command=self._show_agents_hub).pack(side="left", padx=(14, 0))

        chat_scroll = ctk.CTkScrollableFrame(outer, fg_color=BG)
        chat_scroll.grid(row=1, column=0, sticky="nsew")
        chat_scroll.grid_columnconfigure(0, weight=1)

        def add_bubble(role, content, tag=None):
            is_user = role == "user"
            row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=20)
            bubble = ctk.CTkFrame(row, fg_color=ACCENT if is_user else CARD, corner_radius=16)
            bubble.pack(anchor="e" if is_user else "w", padx=(120, 16) if is_user else (16, 120))
            if tag and not is_user:
                ctk.CTkLabel(bubble, text=tag, font=ctk.CTkFont("Arial", 9, "bold"),
                             text_color=ACCENT).pack(anchor="w", padx=14, pady=(8, 0))
            ctk.CTkLabel(bubble, text=content, font=ctk.CTkFont("Arial", 12),
                         text_color="white" if is_user else TEXT,
                         justify="left", wraplength=560).pack(padx=14, pady=(2, 10) if tag else 10)
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

        if saved_conversation["messages"]:
            for saved_message in saved_conversation["messages"]:
                role = saved_message.get("role", "assistant")
                content = saved_message.get("content", "")
                add_bubble("user" if role == "user" else "ai", content,
                           tag=(saved_message.get("metadata") or {}).get("tag"))
                if role in ("user", "assistant"):
                    self._pgm_history.append(types.Content(
                        role="user" if role == "user" else "model",
                        parts=[types.Part(text=content)],
                    ))
        else:
            add_bubble("ai", "Hey! I can help you figure out which IT skills interest you, make a learning goal that fits your schedule, and keep track of progress. Teachers can use me to shape a supportive check-in or a right-sized challenge. Try: ‘I want to learn Python but only have 20 minutes a day.’")

        input_bar = ctk.CTkFrame(outer, fg_color=CARD, corner_radius=0)
        input_bar.grid(row=2, column=0, sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(input_bar, placeholder_text="Talk through a goal, habit, or IT skill you want to explore…",
                             font=ctk.CTkFont("Arial", 13), fg_color=CARD_LIGHT,
                             border_color=BORDER, text_color=TEXT, height=48, corner_radius=14)
        entry.grid(row=0, column=0, padx=(16, 8), pady=14, sticky="ew")
        send_btn = ctk.CTkButton(input_bar, text="Send  ➤", font=ctk.CTkFont("Arial", 13, "bold"),
                                 fg_color="#20a387", hover_color=ACCENT_HOVER, text_color="white",
                                 corner_radius=14, height=44, width=110)
        send_btn.grid(row=0, column=1, padx=(0, 16), pady=14)

        key_manager = _get_manager()
        if key_manager is None:
            raise RuntimeError(_role_key_error or "The role key manager could not be initialized.")
        mentor = PersonalGrowthMentor(key_manager, scope_id=self._pgm_conversation_id)

        def send(event=None):
            message = entry.get().strip()
            if not message:
                return
            entry.delete(0, "end")
            send_btn.configure(state="disabled", text="…")
            add_bubble("user", message)
            append_message("personal-growth", self._pgm_conversation_id, "user", message)

            typing_row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            typing_row.pack(fill="x", pady=4, padx=20, anchor="w")
            typing_bubble = ctk.CTkFrame(typing_row, fg_color=CARD, corner_radius=16)
            typing_bubble.pack(anchor="w", padx=(16, 120))
            ctk.CTkLabel(typing_bubble, text="Thinking through a useful next step…",
                         font=ctk.CTkFont("Arial", 12, slant="italic"),
                         text_color=TEXT_MUTED).pack(padx=14, pady=10)

            def task():
                try:
                    result = mentor.chat(self._pgm_history, message)

                    def done():
                        if not typing_row.winfo_exists():
                            return
                        typing_row.destroy()
                        self._pgm_history = result["history"]
                        tags = []
                        if result.get("tool_calls"):
                            tags.append(f"🌱 goal tools · {len(result['tool_calls'])}")
                        if result.get("keys_used"):
                            tags.append("AI · " + ", ".join(result["keys_used"]))
                        tag = "  /  ".join(tags) or None
                        add_bubble("ai", result["reply"], tag=tag)
                        append_message("personal-growth", self._pgm_conversation_id, "assistant",
                                       result["reply"], metadata={"tag": tag} if tag else None)
                        send_btn.configure(state="normal", text="Send  ➤")

                    self.after(0, done)
                except Exception as exc:
                    error = str(exc)
                    def fail():
                        if not typing_row.winfo_exists():
                            return
                        typing_row.destroy()
                        add_bubble("ai", f"I couldn't complete that just now: {error}")
                        append_message("personal-growth", self._pgm_conversation_id, "assistant",
                                       f"I couldn't complete that just now: {error}")
                        send_btn.configure(state="normal", text="Send  ➤")
                    self.after(0, fail)

            threading.Thread(target=task, daemon=True).start()

        send_btn.configure(command=send)
        entry.bind("<Return>", send)

    # -----------------------------------------------------------------------
    # VIEW: CODING AGENT — 5-role pipeline
    # -----------------------------------------------------------------------
    def _show_coding_agent_view(self):
        self._clear_main()

        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=32, pady=(24, 8))
        ctk.CTkLabel(hdr, text="Campus Suite  /  Coding Agent",
                     font=ctk.CTkFont("Arial", 24, "bold"), text_color=TITLE).pack(side="left")
        ctk.CTkButton(hdr, text="← All agents", font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT,
                      corner_radius=16, height=32, width=110,
                      command=self._show_agents_hub).pack(side="left", padx=(14, 0))

        # A compact "vibe coding" brief makes the purpose and workflow clear
        # before the student reaches the project controls.
        hero = ctk.CTkFrame(scroll, fg_color="#202f4a", corner_radius=CORNER,
                            border_color="#3c82c8", border_width=1)
        hero.pack(fill="x", padx=32, pady=(8, 16))
        ctk.CTkLabel(hero, text="Describe it. The team builds it.",
                     font=ctk.CTkFont("Arial", 20, "bold"), text_color=TITLE).pack(
                         anchor="w", padx=24, pady=(20, 2))
        ctk.CTkLabel(hero,
                     text="Your request is planned, implemented, reviewed, and reported back in one focused run.",
                     font=ctk.CTkFont("Arial", 12), text_color="#d6e9ff").pack(
                         anchor="w", padx=24, pady=(0, 14))
        flow = ctk.CTkFrame(hero, fg_color="transparent")
        flow.pack(anchor="w", padx=24, pady=(0, 20))
        for index, (label, color) in enumerate([
            ("1  Think", ACCENT), ("2  Design", "#af52de"),
            ("3  Build", "#34c759"), ("4  Review", GOLD), ("5  Report", "#61dafb"),
        ]):
            ctk.CTkLabel(flow, text=label, font=ctk.CTkFont("Arial", 10, "bold"),
                         text_color="#101827", fg_color=color, corner_radius=12,
                         width=86, height=28).pack(side="left", padx=(0, 7))
            if index < 4:
                ctk.CTkLabel(flow, text="→", font=ctk.CTkFont("Arial", 13, "bold"),
                             text_color="#8fb9e5").pack(side="left", padx=(0, 7))

        setup_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=CORNER,
                                  border_color=BORDER, border_width=1)
        setup_card.pack(fill="x", padx=32, pady=(10, 16))

        ctk.CTkLabel(setup_card, text="Start a build",
                     font=ctk.CTkFont("Arial", 17, "bold"), text_color=TEXT).pack(anchor="w", padx=22, pady=(20, 2))
        ctk.CTkLabel(setup_card, text="Choose the project folder, then tell the team what you want to create or improve.",
                     font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 14))
        ctk.CTkLabel(setup_card, text="Project folder",
                     font=ctk.CTkFont("Arial", 12, "bold"), text_color=TEXT).pack(anchor="w", padx=22, pady=(0, 4))
        folder_row = ctk.CTkFrame(setup_card, fg_color="transparent")
        folder_row.pack(fill="x", padx=22, pady=(0, 16))
        self._ca_folder_var = ctk.StringVar(value="")
        folder_lbl = ctk.CTkLabel(folder_row, text="No folder selected", font=ctk.CTkFont("Arial", 12),
                                   text_color=TEXT_MUTED, anchor="w")
        folder_lbl.pack(side="left", fill="x", expand=True)

        def pick_folder():
            d = filedialog.askdirectory(title="Choose (or create) a project folder")
            if d:
                self._ca_folder_var.set(d)
                folder_lbl.configure(text=d, text_color=TEXT)

        ctk.CTkButton(folder_row, text="Choose folder…", font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                      corner_radius=16, height=34, width=130, command=pick_folder).pack(side="right")

        ctk.CTkLabel(setup_card, text="What should we build?",
                     font=ctk.CTkFont("Arial", 12, "bold"), text_color=TEXT).pack(anchor="w", padx=22, pady=(0, 4))
        request_box = ctk.CTkTextbox(setup_card, font=ctk.CTkFont("Arial", 13),
                                      fg_color=CARD_LIGHT, border_color=BORDER,
                                      text_color=TEXT, height=110, corner_radius=16)
        request_box.pack(fill="x", padx=22, pady=(0, 14))
        request_hint = "Describe the feature, bug fix, or app you want to make…"
        request_box.insert("1.0", request_hint)
        request_box.configure(text_color=TEXT_MUTED)

        def clear_request_hint(event=None):
            if request_box.get("1.0", "end").strip() == request_hint:
                request_box.delete("1.0", "end")
                request_box.configure(text_color=TEXT)

        request_box.bind("<FocusIn>", clear_request_hint)

        run_btn = ctk.CTkButton(setup_card, text="Build with Coding Agent  ▶",
                                 font=ctk.CTkFont("Arial", 13, "bold"),
                                 fg_color="#007aff", hover_color="#0060c4", text_color="white",
                                 corner_radius=21, height=46, width=240)
        run_btn.pack(anchor="w", padx=22, pady=(0, 22))

        log_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        log_frame.pack(fill="x", padx=32, pady=(0, 40))

        role_colors = {"🧠 Thinker": ACCENT, "🎨 Designer": "#af52de", "💻 Coder": "#007aff",
                        "🔍 Checker": GOLD, "👔 CEO": GREEN}

        def add_step_card(role_label, detail):
            base_role = role_label.split(" (")[0]
            color = role_colors.get(base_role, ACCENT)
            card = ctk.CTkFrame(log_frame, fg_color=CARD, corner_radius=SECTION_CORNER,
                                border_color=BORDER, border_width=1)
            card.pack(fill="x", pady=5)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=18, pady=(12, 2))
            chip = ctk.CTkFrame(top, fg_color=color, corner_radius=8)
            chip.pack(side="left")
            ctk.CTkLabel(chip, text=f"  {role_label}  ", font=ctk.CTkFont("Arial", 10, "bold"),
                         text_color="white").pack(padx=2, pady=2)
            ctk.CTkLabel(card, text=detail, font=ctk.CTkFont("Arial", 12),
                         text_color=TEXT_MUTED, wraplength=780, justify="left").pack(
                             anchor="w", padx=18, pady=(4, 14))

        def run_pipeline():
            folder = self._ca_folder_var.get()
            request = request_box.get("1.0", "end").strip()
            if request == request_hint:
                request = ""
            if not folder:
                messagebox.showwarning("No folder", "Pick a project folder first.")
                return
            if not request:
                messagebox.showwarning("No request", "Describe what you want built or changed.")
                return

            key_manager = _get_manager()
            if key_manager is None:
                messagebox.showwarning("Agents unavailable", "Configure the Gemini API keys first.")
                return

            for w in log_frame.winfo_children():
                w.destroy()
            run_btn.configure(state="disabled", text="Running…")
            conversation = new_conversation("coding", title=request[:120])
            self._ca_conversation_id = conversation["id"]
            append_message("coding", self._ca_conversation_id, "user", request,
                           metadata={"project_folder": folder})
            step_history = []

            def on_step(role_label, detail):
                step_history.append((role_label, detail))
                append_message("coding", self._ca_conversation_id, "tool", detail,
                               metadata={"agent_role": role_label})
                self.after(0, lambda: add_step_card(role_label, detail))

            def task():
                try:
                    agent = CodingAgent(key_manager, folder)
                    agent.run(request, on_step=on_step)
                    append_message("coding", self._ca_conversation_id, "assistant",
                                   "Coding pipeline completed.",
                                   metadata={"steps": len(step_history)})
                except Exception as e:
                    err = str(e)
                    append_message("coding", self._ca_conversation_id, "assistant",
                                   "⚠️ " + err, metadata={"status": "error"})
                    self.after(0, lambda: add_step_card("⚠️ Error", err))
                finally:
                    self.after(0, lambda: run_btn.configure(state="normal", text="Build with Coding Agent  ▶"))

            threading.Thread(target=task, daemon=True).start()

        run_btn.configure(command=run_pipeline)

    # -----------------------------------------------------------------------
    # VIEW: TEACHER AUDIT LOGS — scrollable API calls and token counts
    # -----------------------------------------------------------------------
    def _show_audit_logs_view(self):
        self._clear_main()
        
        outer = ctk.CTkFrame(self.main, fg_color=BG)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(24, 8))
        
        ctk.CTkLabel(hdr, text="📊  Teacher Audit Logs",
                     font=ctk.CTkFont("Arial", 22, "bold"), text_color=TITLE).pack(side="left")
        ctk.CTkButton(hdr, text="← Agents", font=ctk.CTkFont("Arial", 11),
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                      corner_radius=14, height=30, width=90,
                      command=self._show_agents_hub).pack(side="left", padx=(14, 0))

        # Action buttons
        actions = ctk.CTkFrame(hdr, fg_color="transparent")
        actions.pack(side="right")
        
        # Log path
        log_path = Path(__file__).resolve().parent / "agent_logs.json"

        # Scrollable container for logs
        scroll = ctk.CTkScrollableFrame(outer, fg_color=BG)
        scroll.grid(row=1, column=0, sticky="nsew", padx=32, pady=(0, 20))
        scroll.grid_columnconfigure(0, weight=1)

        def render_loaded_logs(logs):
            # Clear scroll
            for w in scroll.winfo_children():
                w.destroy()
                    
            if not logs:
                ctk.CTkLabel(scroll, text="No logged API invocations found.",
                             font=ctk.CTkFont("Arial", 14), text_color=TEXT_MUTED).pack(pady=40)
                stats_lbl.configure(text="Total Calls: 0  |  Total Tokens Consumed: 0")
                return

            # Display stats
            total_calls = len(logs)
            total_tokens = sum(log.get("total_tokens", 0) for log in logs)
            stats_lbl.configure(text=f"Total Calls: {total_calls}  |  Total Tokens Consumed: {total_tokens}")

            # Render from newest to oldest
            for log in reversed(logs):
                card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=14, border_color=BORDER, border_width=1)
                card.pack(fill="x", pady=6, padx=4)
                
                # Header row
                header_row = ctk.CTkFrame(card, fg_color="transparent")
                header_row.pack(fill="x", padx=16, pady=(12, 4))
                
                # Time
                ctk.CTkLabel(header_row, text=log.get("timestamp", ""),
                             font=ctk.CTkFont("Arial", 11, "bold"), text_color=TEXT_MUTED).pack(side="left")
                             
                # Key label chip
                label = log.get("key_label", "key-?")
                key_colors = {
                    "key-1": ("#6c63ff", "Thinker/Asker"),
                    "key-2": ("#af52de", "Designer/Verifier"),
                    "key-3": ("#007aff", "Coder/Tool-Key-3"),
                    "key-4": (GOLD, "Checker/Tool-Key-4"),
                    "key-5": (GREEN, "CEO/Tool-Key-5")
                }
                color_info = key_colors.get(label, (ACCENT, label))
                chip = ctk.CTkFrame(header_row, fg_color=color_info[0], corner_radius=8)
                chip.pack(side="left", padx=12)
                ctk.CTkLabel(chip, text=f" {label} ({color_info[1]}) ",
                             font=ctk.CTkFont("Arial", 10, "bold"), text_color="white").pack(padx=2, pady=2)
                             
                # Model
                ctk.CTkLabel(header_row, text=log.get("model", ""),
                             font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED).pack(side="left")
                             
                # Tokens info
                tokens_text = f"Prompt: {log.get('prompt_tokens', 0)}  |  Completion: {log.get('completion_tokens', 0)}  |  Total: {log.get('total_tokens', 0)}"
                ctk.CTkLabel(header_row, text=tokens_text,
                             font=ctk.CTkFont("Arial", 11, "bold"), text_color=GOLD).pack(side="right")
                             
                # Content frame
                content_frame = ctk.CTkFrame(card, fg_color="transparent")
                content_frame.pack(fill="x", padx=16, pady=(4, 12))
                
                # Left side: Prompt
                left_col = ctk.CTkFrame(content_frame, fg_color="transparent")
                left_col.pack(side="left", fill="both", expand=True, padx=(0, 6))
                ctk.CTkLabel(left_col, text="Raw Prompt:", font=ctk.CTkFont("Arial", 11, "bold"), text_color=TEXT_MUTED).pack(anchor="w")
                prompt_txt = ctk.CTkTextbox(left_col, height=120, font=ctk.CTkFont("Consolas", 10),
                                             fg_color=CARD_LIGHT, border_color=BORDER, text_color=TEXT)
                prompt_txt.pack(fill="both", expand=True, pady=4)
                prompt_txt.insert("1.0", log.get("prompt", ""))
                prompt_txt.configure(state="disabled")
                
                # Right side: Response
                right_col = ctk.CTkFrame(content_frame, fg_color="transparent")
                right_col.pack(side="right", fill="both", expand=True, padx=(6, 0))
                ctk.CTkLabel(right_col, text="Raw Response:", font=ctk.CTkFont("Arial", 11, "bold"), text_color=TEXT_MUTED).pack(anchor="w")
                resp_txt = ctk.CTkTextbox(right_col, height=120, font=ctk.CTkFont("Consolas", 10),
                                           fg_color=CARD_LIGHT, border_color=BORDER, text_color=TEXT)
                resp_txt.pack(fill="both", expand=True, pady=4)
                resp_txt.insert("1.0", log.get("response", ""))
                resp_txt.configure(state="disabled")

        def load_and_render_logs():
            for widget in scroll.winfo_children():
                widget.destroy()
            ctk.CTkLabel(scroll, text="Loading audit logs…", font=ctk.CTkFont("Arial", 14),
                         text_color=TEXT_MUTED).pack(pady=40)
            stats_lbl.configure(text="Loading audit logs…")

            def read_logs():
                try:
                    with open(log_path, "r", encoding="utf-8") as log_file:
                        logs = json.load(log_file) if log_path.exists() else []
                except FileNotFoundError:
                    logs = []
                except Exception:
                    logs = []
                try:
                    self.after(0, lambda: render_loaded_logs(logs) if scroll.winfo_exists() else None)
                except Exception:
                    pass

            threading.Thread(target=read_logs, daemon=True).start()

        def clear_logs():
            if messagebox.askyesno("Clear Logs", "Are you sure you want to permanently delete all API call logs?"):
                try:
                    if log_path.exists():
                        log_path.unlink()
                    load_and_render_logs()
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to clear logs: {e}")

        # Top summary stats
        stats_lbl = ctk.CTkLabel(hdr, text="Total Calls: 0  |  Total Tokens Consumed: 0",
                                 font=ctk.CTkFont("Arial", 12), text_color=TEXT)
        stats_lbl.pack(side="left", padx=20)

        # Actions
        ctk.CTkButton(actions, text="Refresh", font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                      corner_radius=12, height=30, width=80, command=load_and_render_logs).pack(side="left", padx=4)
                      
        ctk.CTkButton(actions, text="Clear Logs", font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=RED, hover_color="#cc2a20", text_color="white",
                      corner_radius=12, height=30, width=95, command=clear_logs).pack(side="left", padx=4)

        load_and_render_logs()

    # -----------------------------------------------------------------------
    # VIEW: QUIZ GENERATOR — upload PDF/DOCX/PPTX and generate quizzes
    # -----------------------------------------------------------------------
    def _show_quiz_generator_view(self):
        self._clear_main()

        outer = ctk.CTkFrame(self.main, fg_color=BG)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(24, 8))
        ctk.CTkLabel(hdr, text="🎯  Quiz Generator",
                     font=ctk.CTkFont("Arial", 22, "bold"), text_color=TITLE).pack(side="left")
        ctk.CTkButton(hdr, text="← Agents", font=ctk.CTkFont("Arial", 11),
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                      corner_radius=14, height=30, width=90,
                      command=self._show_agents_hub).pack(side="left", padx=(14, 0))

        scroll = ctk.CTkScrollableFrame(outer, fg_color=BG)
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # File upload section
        file_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=18)
        file_card.pack(fill="x", padx=32, pady=(10, 16))

        ctk.CTkLabel(file_card, text="Upload Source Material",
                     font=ctk.CTkFont("Arial", 13, "bold"), text_color=TEXT).pack(anchor="w", padx=20, pady=(18, 4))

        self._qg_file_var = ctk.StringVar(value="")
        file_lbl = ctk.CTkLabel(file_card, text="No file selected", font=ctk.CTkFont("Arial", 12),
                                 text_color=TEXT_MUTED, anchor="w")
        file_lbl.pack(anchor="w", padx=20, pady=(0, 8))

        def pick_file():
            f = filedialog.askopenfilename(
                title="Choose PDF, DOCX, or PPTX",
                filetypes=[("Documents", "*.pdf *.docx *.pptx"), ("All files", "*.*")]
            )
            if f:
                self._qg_file_var.set(f)
                file_lbl.configure(text=f, text_color=TEXT)

        ctk.CTkButton(file_card, text="Browse…", font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                      corner_radius=12, height=30, width=90, command=pick_file).pack(anchor="w", padx=20, pady=(0, 18))

        # Options section
        opts_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=18)
        opts_card.pack(fill="x", padx=32, pady=(0, 16))

        ctk.CTkLabel(opts_card, text="Quiz Settings",
                     font=ctk.CTkFont("Arial", 13, "bold"), text_color=TEXT).pack(anchor="w", padx=20, pady=(18, 10))

        # Topic
        topic_row = ctk.CTkFrame(opts_card, fg_color="transparent")
        topic_row.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(topic_row, text="Topic:", font=ctk.CTkFont("Arial", 12), text_color=TEXT).pack(side="left")
        self._qg_topic_var = ctk.StringVar(value="")
        ctk.CTkEntry(topic_row, textvariable=self._qg_topic_var, placeholder_text="e.g. Photosynthesis, WWII, Python basics",
                     font=ctk.CTkFont("Arial", 12), fg_color=CARD_LIGHT, border_color=BORDER, text_color=TEXT,
                     corner_radius=10, height=36).pack(side="left", padx=(10, 0), fill="x", expand=True)

        # Difficulty + count row
        diff_row = ctk.CTkFrame(opts_card, fg_color="transparent")
        diff_row.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(diff_row, text="Difficulty:", font=ctk.CTkFont("Arial", 12), text_color=TEXT).pack(side="left")
        self._qg_diff_var = ctk.StringVar(value="Medium")
        ctk.CTkOptionMenu(diff_row, values=["Easy", "Medium", "Hard"], variable=self._qg_diff_var,
                          fg_color=CARD_LIGHT, button_color=BORDER, button_hover_color=ACCENT,
                          text_color=TEXT, font=ctk.CTkFont("Arial", 12), width=120).pack(side="left", padx=(10, 20))

        ctk.CTkLabel(diff_row, text="Question Count:", font=ctk.CTkFont("Arial", 12), text_color=TEXT).pack(side="left")
        self._qg_count_var = ctk.StringVar(value="10")
        ctk.CTkOptionMenu(diff_row, values=["5", "10", "15", "20"], variable=self._qg_count_var,
                          fg_color=CARD_LIGHT, button_color=BORDER, button_hover_color=ACCENT,
                          text_color=TEXT, font=ctk.CTkFont("Arial", 12), width=80).pack(side="left", padx=(10, 20))

        # Question types
        type_row = ctk.CTkFrame(opts_card, fg_color="transparent")
        type_row.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkLabel(type_row, text="Types:", font=ctk.CTkFont("Arial", 12), text_color=TEXT).pack(side="left")
        self._qg_type_vars = {}
        for t in ["Multiple Choice", "True/False", "Short Answer"]:
            var = ctk.BooleanVar(value=True)
            self._qg_type_vars[t] = var
            ctk.CTkCheckBox(type_row, text=t, variable=var, font=ctk.CTkFont("Arial", 11),
                            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=TEXT).pack(side="left", padx=(10, 0))

        # Generate button
        gen_btn = ctk.CTkButton(opts_card, text="Generate Quiz  ▶",
                                 font=ctk.CTkFont("Arial", 13, "bold"),
                                 fg_color="#34c759", hover_color="#28a745", text_color="white",
                                 corner_radius=18, height=42, width=220)
        gen_btn.pack(anchor="w", padx=20, pady=(0, 20))

        # Output section
        out_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=18)
        out_card.pack(fill="x", padx=32, pady=(0, 16))

        ctk.CTkLabel(out_card, text="Generated Quiz",
                     font=ctk.CTkFont("Arial", 13, "bold"), text_color=TEXT).pack(anchor="w", padx=20, pady=(18, 4))

        self._qg_output = ctk.CTkTextbox(out_card, font=ctk.CTkFont("Consolas", 11),
                                          fg_color=CARD_LIGHT, border_color=BORDER, text_color=TEXT,
                                          height=250, corner_radius=12)
        self._qg_output.pack(fill="x", padx=20, pady=(0, 10))

        btn_row = ctk.CTkFrame(out_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkButton(btn_row, text="Copy", font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT,
                      corner_radius=10, height=32, width=80,
                      command=lambda: self._copy_to_clipboard(self._qg_output.get("1.0", "end").strip())).pack(side="left")
        ctk.CTkButton(btn_row, text="Save as .txt", font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                      corner_radius=10, height=32, width=110,
                      command=lambda: self._save_quiz_to_file(self._qg_output.get("1.0", "end").strip())).pack(side="left", padx=(8, 0))

        def generate_quiz():
            file_path = self._qg_file_var.get()
            topic = self._qg_topic_var.get().strip()
            difficulty = self._qg_diff_var.get()
            count = int(self._qg_count_var.get())
            types = [t for t, v in self._qg_type_vars.items() if v.get()]

            if not file_path and not topic:
                messagebox.showwarning("Missing Input", "Please upload a file or enter a topic.")
                return
            if not types:
                messagebox.showwarning("No Types", "Select at least one question type.")
                return

            gen_btn.configure(state="disabled", text="Generating…")
            self._qg_output.delete("1.0", "end")
            self._qg_output.insert("1.0", "Generating quiz…")
            conversation = new_conversation("quiz", title=topic or Path(file_path).name)
            conversation_id = conversation["id"]
            append_message("quiz", conversation_id, "user", topic or "Generate quiz from uploaded material",
                           metadata={"file_name": Path(file_path).name if file_path else None,
                                     "difficulty": difficulty, "question_count": count,
                                     "question_types": types})

            def task():
                try:
                    from quiz_generator import QuizGenerator
                    gen = QuizGenerator(_get_manager())
                    result = gen.generate(file_path, topic, difficulty, count, types)
                    append_message("quiz", conversation_id, "assistant", result)
                    self.after(0, lambda: self._on_quiz_generated(result))
                except Exception as e:
                    append_message("quiz", conversation_id, "assistant", "⚠️ " + str(e),
                                   metadata={"status": "error"})
                    self.after(0, lambda: self._on_quiz_error(str(e)))

            threading.Thread(target=task, daemon=True).start()

        gen_btn.configure(command=generate_quiz)

    def _on_quiz_generated(self, quiz_text: str):
        self._qg_output.delete("1.0", "end")
        self._qg_output.insert("1.0", quiz_text)
        # Re-enable button (find it in the output card)
        for w in self.main.winfo_children():
            self._find_and_enable_generate_btn(w)

    def _on_quiz_error(self, err: str):
        self._qg_output.delete("1.0", "end")
        self._qg_output.insert("1.0", f"⚠️ Error: {err}")
        for w in self.main.winfo_children():
            self._find_and_enable_generate_btn(w)

    def _find_and_enable_generate_btn(self, widget):
        try:
            if isinstance(widget, ctk.CTkButton) and "Generate" in widget.cget("text"):
                widget.configure(state="normal", text="Generate Quiz  ▶")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._find_and_enable_generate_btn(child)

    def _copy_to_clipboard(self, text: str):
        if text and not text.startswith("⚠️"):
            self.clipboard_clear()
            self.clipboard_append(text)
            messagebox.showinfo("Copied", "Quiz copied to clipboard.")

    def _save_quiz_to_file(self, text: str):
        if not text or text.startswith("⚠️"):
            messagebox.showwarning("Nothing to save", "Generate a quiz first.")
            return
        f = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if f:
            try:
                Path(f).write_text(text, encoding="utf-8")
                messagebox.showinfo("Saved", f"Quiz saved to {f}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save: {e}")


# -----------------------------------------------------------------------
    # VIEW: VIDEO EDITING AGENT — Chat + terminal log
    # -----------------------------------------------------------------------
    def _show_blender_agent_view(self):
        """Embedded Blender workspace; Blender itself remains the source of truth."""
        self._clear_main()
        outer = ctk.CTkFrame(self.main, fg_color=BG)
        outer.pack(fill="both", expand=True)
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", padx=32, pady=(26, 10))
        ctk.CTkButton(header, text="← Agents", width=90, command=self._show_agents_hub,
                      fg_color=CARD_LIGHT, hover_color=BORDER).pack(side="left")
        ctk.CTkLabel(header, text="🧊 Blender Agent", font=ctk.CTkFont("Arial", 24, "bold"),
                     text_color=TITLE).pack(side="left", padx=16)

        if not _BLENDER_AGENT_AVAILABLE:
            ctk.CTkLabel(outer, text="Blender Agent dependencies could not be loaded. Check blender_agent.py and its Python packages.",
                         text_color=RED, wraplength=700).pack(padx=32, pady=32)
            return

        safety = ctk.CTkFrame(outer, fg_color="#173d38", corner_radius=16, border_color="#26a272", border_width=1)
        safety.pack(fill="x", padx=32, pady=(0, 12))
        ctk.CTkLabel(safety, text="Preserve mode is on", font=ctk.CTkFont("Arial", 14, "bold"), text_color="#7ee8c1").pack(anchor="w", padx=18, pady=(12, 2))
        ctk.CTkLabel(safety, text="The agent reads the open Blender scene, keeps durable project memory, and blocks whole-scene deletion unless your request explicitly says to reset or clear the scene.",
                     font=ctk.CTkFont("Arial", 11), text_color=TEXT, wraplength=840, justify="left").pack(anchor="w", padx=18, pady=(0, 12))

        memory = ProjectMemory()
        memory_label = ctk.CTkLabel(outer, text=f"Saved project memory: {len(memory.data.get('entries', []))} verified edits",
                                     font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED)
        memory_label.pack(anchor="w", padx=38, pady=(0, 6))
        prompt_box = ctk.CTkTextbox(outer, height=100, fg_color=CARD, border_color=BORDER, border_width=1,
                                    text_color=TEXT, font=ctk.CTkFont("Arial", 13))
        prompt_box.pack(fill="x", padx=32)
        prompt_box.insert("1.0", "Describe an edit to the currently open Blender project…")
        attached_image = {"path": None}
        image_status = ctk.CTkLabel(outer, text="No reference image attached", font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED)
        image_status.pack(anchor="w", padx=38, pady=(6, 0))

        def choose_reference_image():
            selected = filedialog.askopenfilename(
                title="Choose a diagram or reference image",
                filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp")],
            )
            if selected:
                attached_image["path"] = Path(selected)
                image_status.configure(text=f"Reference image: {Path(selected).name}", text_color="#7ee8c1")

        ctk.CTkButton(outer, text="Attach diagram / image", command=choose_reference_image,
                      fg_color=CARD_LIGHT, hover_color=BORDER, height=30).pack(anchor="w", padx=32, pady=(4, 4))
        output = ctk.CTkTextbox(outer, fg_color=CARD, text_color=TEXT, font=ctk.CTkFont("Consolas", 11))
        output.pack(fill="both", expand=True, padx=32, pady=12)

        def write(message):
            output.configure(state="normal")
            output.insert("end", message + "\n")
            output.see("end")
            output.configure(state="disabled")

        def run_task():
            request = prompt_box.get("1.0", "end").strip()
            if not request or request == "Describe an edit to the currently open Blender project…":
                return
            conversation = new_conversation("blender", title=request[:120])
            conversation_id = conversation["id"]
            append_message("blender", conversation_id, "user", request,
                           metadata={"reference_image": str(attached_image["path"]) if attached_image["path"] else None})
            send.configure(state="disabled", text="Working…")
            write(f"You: {request}")

            def worker():
                try:
                    client = BlenderSocket()
                    orchestrator = BlenderMasterOrchestrator(client, get_ai_api())
                    result = orchestrator.execute_task(request, image_path=attached_image["path"],
                                                       progress_cb=lambda stage, detail: self.after(0, lambda: write(f"{stage}: {detail}")))
                    explanation = result.get("explanation", "Completed.")
                    append_message("blender", conversation_id, "assistant", explanation,
                                   metadata={"status": result.get("status", "completed")})
                    self.after(0, lambda: write("\n" + explanation))
                    self.after(0, lambda: memory_label.configure(text=f"Saved project memory: {len(ProjectMemory().data.get('entries', []))} verified edits"))
                except Exception as exc:
                    append_message("blender", conversation_id, "assistant", f"⚠️ Blender task failed: {exc}",
                                   metadata={"status": "error"})
                    self.after(0, lambda: write(f"⚠️ Blender task failed: {exc}"))
                finally:
                    self.after(0, lambda: send.configure(state="normal", text="Apply edit  →"))
            threading.Thread(target=worker, daemon=True).start()

        send = ctk.CTkButton(outer, text="Apply edit  →", command=run_task, fg_color="#26a272", hover_color="#1a6e50",
                             font=ctk.CTkFont("Arial", 13, "bold"), height=40)
        send.pack(anchor="e", padx=32, pady=(0, 24))
        output.configure(state="disabled")

    def _show_video_editing_agent_view(self, conversation_id=None):
        self._clear_main()

        outer = ctk.CTkFrame(self.main, fg_color=BG)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(24, 8))
        ctk.CTkLabel(hdr, text="🎬  Video Editing Agent",
                      font=ctk.CTkFont("Arial", 22, "bold"), text_color=TITLE).pack(side="left")
        ctk.CTkButton(hdr, text="← Agents", font=ctk.CTkFont("Arial", 11),
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                      corner_radius=14, height=30, width=90,
                      command=self._show_agents_hub).pack(side="left", padx=(14, 0))

        # Initialize video editing agent with file bridge and API keys
        import tempfile
        bridge_dir = os.path.join(tempfile.gettempdir(), 'ae_video_agent_bridge')
        self._ve_agent = VideoEditingAgent(key_manager=_get_manager(), bridge_dir=bridge_dir)
        self._ve_agent.set_log_callback(self._ve_log)
        self._ve_server_running = False
        self._ve_history = []
        saved_conversation = load_conversation("video-editing", conversation_id) if conversation_id else None
        if saved_conversation is None:
            saved_conversation = new_conversation("video-editing")
        self._ve_conversation_id = saved_conversation["id"]

        # Chat area (scrollable)
        chat_scroll = ctk.CTkScrollableFrame(outer, fg_color=BG)
        chat_scroll.grid(row=1, column=0, sticky="nsew", padx=(32, 32), pady=(0, 8))
        chat_scroll.grid_columnconfigure(0, weight=1)

        def add_bubble(role, text, tag=None):
            is_user = (role == "user")
            row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=20)
            bubble = ctk.CTkFrame(row, fg_color=ACCENT if is_user else CARD, corner_radius=16)
            bubble.pack(anchor="e" if is_user else "w", padx=(120, 16) if is_user else (16, 120))
            if tag and not is_user:
                ctk.CTkLabel(bubble, text=tag, font=ctk.CTkFont("Arial", 9, "bold"),
                             text_color=ACCENT).pack(anchor="w", padx=14, pady=(8, 0))
            ctk.CTkLabel(bubble, text=text, font=ctk.CTkFont("Arial", 12),
                         text_color="white" if is_user else TEXT,
                         justify="left", wraplength=560).pack(padx=14, pady=(2, 10) if tag else 10)
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

        # Step bubble renderer for live agent activity feed
        step_icons = {
            "thinking": "🧠",
            "tool_call": "🔧",
            "tool_result": "📄",
            "editing": "✏️",
            "done": "✅",
            "warning": "⚠️",
        }
        step_colors = {
            "thinking": "#6c63ff",
            "tool_call": "#007aff",
            "tool_result": "#58a6ff",
            "editing": GOLD,
            "done": GREEN,
            "warning": RED,
        }
        step_bubbles = []  # track step bubbles for this turn for collapse

        def add_step_bubble(step_type, label, detail=None):
            """Add a small, muted, expandable step bubble to the chat."""
            icon = step_icons.get(step_type, "•")
            color = step_colors.get(step_type, TEXT_MUTED)
            
            row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2, padx=20)
            
            # Main step row
            bubble = ctk.CTkFrame(row, fg_color=CARD_LIGHT, corner_radius=10)
            bubble.pack(anchor="w", padx=(16, 120))
            
            top = ctk.CTkFrame(bubble, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 0))
            
            ctk.CTkLabel(top, text=f"{icon} {label}", 
                         font=ctk.CTkFont("Arial", 10), text_color=color).pack(side="left")
            
            # Expandable detail area
            if detail:
                detail_box = ctk.CTkTextbox(bubble, font=ctk.CTkFont("Consolas", 8),
                              fg_color="#0d0d14", border_color=CARD, text_color="#9399b2",
                              height=0, corner_radius=6, wrap="word")
                detail_box.pack(fill="x", padx=10, pady=(0, 6))
                detail_box.insert("1.0", str(detail))
                detail_box.configure(state="disabled")
                detail_box.pack_forget()  # start collapsed
                
                def toggle():
                    if detail_box.winfo_ismapped():
                        detail_box.pack_forget()
                        arrow.configure(text="▶")
                    else:
                        detail_box.pack(fill="x", padx=10, pady=(0, 6), before=top)
                        arrow.configure(text="▼")
                
                arrow = ctk.CTkLabel(top, text="▶", font=ctk.CTkFont("Arial", 9),
                                     text_color=TEXT_MUTED, cursor="hand2")
                arrow.pack(side="right")
                arrow.bind("<Button-1>", lambda e: toggle())
            
            step_bubbles.append((row, detail_box if detail else None))
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

        def collapse_step_bubbles():
            """Collapse all step bubbles from this turn into a single toggle."""
            if not step_bubbles:
                return
            # Create a single summary row
            summary_row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            summary_row.pack(fill="x", pady=4, padx=20)
            summary_card = ctk.CTkFrame(summary_row, fg_color=CARD, corner_radius=12)
            summary_card.pack(anchor="w", padx=(16, 120))
            
            count = len(step_bubbles)
            summary_label = ctk.CTkLabel(summary_card, 
                                         text=f"▶ Show reasoning ({count} steps)",
                                         font=ctk.CTkFont("Arial", 10, "italic"),
                                         text_color=TEXT_MUTED, cursor="hand2")
            summary_label.pack(padx=14, pady=8)
            
            def expand_summary():
                summary_row.destroy()
                for row, detail_box in step_bubbles:
                    row.pack(fill="x", pady=2, padx=20, in_=chat_scroll)
                    if detail_box:
                        detail_box.pack(fill="x", padx=10, pady=(0, 6))
            
            summary_label.bind("<Button-1>", lambda e: expand_summary())
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

        # Role progress frame (for AI pipeline steps)
        role_bubbles_frame = ctk.CTkFrame(chat_scroll, fg_color="transparent")
        role_bubbles_frame.pack(fill="x", pady=4, padx=20)

        role_label_colors = {"🧠 Planner": "#6c63ff", "🎨 Designer": "#af52de", "💻 Coder": "#007aff", 
                             "🔍 Reviewer": GOLD, "📝 Reporter": GREEN, "🎥 Analyst": "#58a6ff"}

        def add_role_card(frame, role_label, content):
            color = role_label_colors.get(role_label, ACCENT)
            card = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=12, border_width=1)
            card.pack(fill="x", pady=2)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 0))
            ctk.CTkLabel(top, text=role_label, font=ctk.CTkFont("Arial", 10, "bold"),
                         text_color=color).pack(side="left")
            
            content_box = ctk.CTkTextbox(card, font=ctk.CTkFont("Consolas", 9),
                           fg_color="#11111b", border_color=CARD_LIGHT, text_color="#cdd6f4",
                           height=80, corner_radius=8, wrap="word")
            content_box.pack(fill="x", padx=10, pady=(2, 6))
            content_box.insert("1.0", content)
            content_box.configure(state="disabled")
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

        # Terminal log area (below chat)
        terminal_card = ctk.CTkFrame(outer, fg_color=CARD, corner_radius=18)
        terminal_card.grid(row=2, column=0, sticky="nsew", padx=(32, 32), pady=(0, 12))
        terminal_card.grid_rowconfigure(1, weight=1)
        terminal_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(terminal_card, text="Terminal — File Bridge",
                     font=ctk.CTkFont("Arial", 11, "bold"), text_color=TEXT_MUTED).pack(anchor="w", padx=20, pady=(14, 4))

        # Bridge controls row
        ctrl_row = ctk.CTkFrame(terminal_card, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=20, pady=(0, 8))

        self._ve_start_btn = ctk.CTkButton(ctrl_row, text="▶ Start Bridge",
                                            font=ctk.CTkFont("Arial", 11, "bold"),
                                            fg_color=GREEN, hover_color="#28a745", text_color="white",
                                            corner_radius=10, height=32, width=140,
                                            command=self._ve_toggle_server)
        self._ve_start_btn.pack(side="left")

        ctk.CTkButton(ctrl_row, text="🔍 Test Bridge",
                      font=ctk.CTkFont("Arial", 11),
                      fg_color=GOLD, hover_color="#b89400", text_color="#1a1a00",
                      corner_radius=10, height=32, width=110,
                      command=self._ve_test_server).pack(side="left", padx=(8, 0))

        ctk.CTkButton(ctrl_row, text="📋 Copy AE Script",
                      font=ctk.CTkFont("Arial", 11),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                      corner_radius=10, height=32, width=120,
                      command=self._ve_copy_ae_script).pack(side="left", padx=(8, 0))

        ctk.CTkButton(ctrl_row, text="📂 Save to AE Panels",
                      font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color="#af52de", hover_color="#9046c7", text_color="white",
                      corner_radius=10, height=32, width=150,
                      command=self._ve_save_to_ae_panels).pack(side="left", padx=(8, 0))

        # Quota indicator
        self._ve_quota_label = ctk.CTkLabel(ctrl_row, text="",
                       font=ctk.CTkFont("Arial", 10), text_color=TEXT_MUTED)
        self._ve_quota_label.pack(side="left", padx=(12, 0))

        # Analyze Footage button
        self._ve_analyze_btn = ctk.CTkButton(ctrl_row, text="🎥 Analyze Footage",
                      font=ctk.CTkFont("Arial", 11, "bold"),
                      fg_color="#58a6ff", hover_color="#4290d6", text_color="white",
                      corner_radius=10, height=32, width=150,
                      command=self._ve_analyze_footage)
        self._ve_analyze_btn.pack(side="left", padx=(8, 0))
        self._ve_analyze_btn.configure(state="disabled")

        # Capture & Check toggle
        self._ve_capture_check = ctk.CTkCheckBox(ctrl_row, text="Capture & Check",
                      font=ctk.CTkFont("Arial", 11),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      border_color=BORDER, checkmark_color=ACCENT)
        self._ve_capture_check.pack(side="left", padx=(8, 0))
        self._ve_capture_check.configure(state="disabled")

        # Terminal log textbox
        self._ve_console = ctk.CTkTextbox(terminal_card, font=ctk.CTkFont("Consolas", 10),
                                           fg_color="#11111b", border_color=BORDER, text_color="#a6e3a1",
                                           height=150, corner_radius=10)
        self._ve_console.pack(fill="both", expand=True, padx=20, pady=(0, 14))

        # Chat input bar (bottom)
        input_bar = ctk.CTkFrame(outer, fg_color=CARD, corner_radius=0)
        input_bar.grid(row=3, column=0, sticky="ew", padx=32, pady=(0, 16))
        input_bar.grid_columnconfigure(0, weight=1)

        self._ve_entry = ctk.CTkEntry(input_bar,
                                       placeholder_text="e.g. 'add text Hello World', 'glow effect', 'color grade', 'blur background', 'inspect project'",
                                       font=ctk.CTkFont("Arial", 13), fg_color=CARD_LIGHT,
                                       border_color=BORDER, text_color=TEXT, height=48, corner_radius=14)
        self._ve_entry.grid(row=0, column=0, padx=(16, 8), pady=14, sticky="ew")

        send_btn = ctk.CTkButton(input_bar, text="Send  ➤",
                                 font=ctk.CTkFont("Arial", 13, "bold"),
                                 fg_color="#e84c3d", hover_color="#d03c2d", text_color="white",
                                 corner_radius=14, height=44, width=110)
        send_btn.grid(row=0, column=1, padx=(0, 16), pady=14)

        if saved_conversation["messages"]:
            for saved_message in saved_conversation["messages"]:
                role = saved_message.get("role", "assistant")
                add_bubble("user" if role == "user" else "ai", saved_message.get("content", ""),
                           tag=(saved_message.get("metadata") or {}).get("tag"))
        else:
            # Initial welcome message
            add_bubble("ai", "Hello! I'm your AI Video Editing Agent for After Effects.\n\n"
                                "I use a 6-role pipeline (🧠 Planner → 🎨 Designer → 💻 Coder → 🔍 Reviewer → 📝 Reporter → 🎥 Analyst) "
                                "to intelligently plan and generate ExtendScript commands.\n\n"
                                "1. Click '▶ Start Bridge' to create the file bridge\n"
                                "2. Open AI_Live_Receiver.jsx in After Effects (Window → Extensions)\n"
                                "3. Load a video file and click '🎥 Analyze Footage' for AI-powered scene analysis\n"
                                "4. Chat naturally: 'add text Hello World', 'glow effect on selected', 'color grade', 'inspect project'",
                            tag="🤖 AI Video Agent (6 Roles)")

        status = self._ve_agent.get_bridge_status()
        if status['has_api_keys']:
            self._ve_log("API keys detected - AI pipeline ENABLED")
        else:
            self._ve_log("No API keys - using template-based fallback")
        self._ve_log(f"Bridge folder: {status['bridge_dir']}")
        self._ve_log("Click 'Start Bridge' to begin.")

        # Update quota indicator
        self._ve_update_quota_display()

        def send_chat(event=None):
            msg = self._ve_entry.get().strip()
            if not msg:
                return
            self._ve_entry.delete(0, "end")
            send_btn.configure(state="disabled", text="⏳")
            add_bubble("user", msg)
            append_message("video-editing", self._ve_conversation_id, "user", msg)
            
            # Reset step bubbles for this turn
            step_bubbles.clear()

            # Show "Agent is working" typing indicator
            typing_row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            typing_row.pack(fill="x", pady=4, padx=20, anchor="w")
            typing_bubble = ctk.CTkFrame(typing_row, fg_color=CARD, corner_radius=16)
            typing_bubble.pack(anchor="w", padx=(16, 120))
            ctk.CTkLabel(typing_bubble, text="Agent is working…",
                         font=ctk.CTkFont("Arial", 12, slant="italic"),
                         text_color=TEXT_MUTED).pack(padx=14, pady=10)
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

            # Run agent pipeline in background
            def on_role_complete(role_label, content):
                """Callback to add role result as a step card in terminal."""
                self._ve_log(f"[{role_label}] {content[:150]}")
                self.after(0, lambda r=role_label, c=content: add_role_card(role_bubbles_frame, r, c))

            response_holder = []

            def on_response(response):
                response_holder.append(response)

            def on_step(step_type, label, **detail):
                """Live agent activity feed callback."""
                self.after(0, lambda: add_step_bubble(step_type, label, detail))

            def task():
                try:
                    self._ve_log("[PIPELINE] Classifying request...")
                    cmd_id = self._ve_agent.send_prompt(
                        msg,
                        on_role_complete=on_role_complete,
                        on_response=on_response,
                        on_step=on_step,
                    )
                    
                    def done():
                        try:
                            if typing_row.winfo_exists():
                                typing_row.destroy()
                            
                            # Always show the Reporter's response (or conversational response)
                            response_text = response_holder[0] if response_holder else "I can help with After Effects edits."
                            
                            # Add tag only if JSX was actually queued for AE execution
                            tag = "🛠️ JSX → AE Bridge" if cmd_id else None
                            
                            add_bubble("ai", response_text, tag=tag)
                            append_message("video-editing", self._ve_conversation_id, "assistant", response_text,
                                           metadata={"tag": tag} if tag else None)
                            
                            # Collapse step bubbles into a single toggle
                            collapse_step_bubbles()
                            
                            send_btn.configure(state="normal", text="Send  ➤")
                        except Exception:
                            return
                    
                    self.after(0, done)
                    
                except Exception as e:
                    err = str(e)
                    def fail():
                        typing_row.destroy()
                        add_bubble("ai", "⚠️ " + err)
                        append_message("video-editing", self._ve_conversation_id, "assistant", "⚠️ " + err)
                        send_btn.configure(state="normal", text="Send  ➤")
                    self.after(0, fail)

            threading.Thread(target=task, daemon=True).start()

        send_btn.configure(command=send_chat)
        self._ve_entry.bind("<Return>", send_chat)

    def _ve_toggle_server(self):
        if not self._ve_server_running:
            self._ve_agent.start_server()
            self._ve_server_running = True
            self._ve_start_btn.configure(text="■ Stop Bridge", fg_color=RED, hover_color="#cc2a20")
            # Enable Analyst and Capture & Check controls
            if self._ve_analyze_btn:
                self._ve_analyze_btn.configure(state="normal" if self._ve_agent.has_api_keys() else "disabled")
            if self._ve_capture_check:
                self._ve_capture_check.configure(state="normal")
            self._ve_update_quota_display()
        else:
            self._ve_agent.stop_server()
            self._ve_server_running = False
            self._ve_start_btn.configure(text="▶ Start Bridge", fg_color=GREEN, hover_color="#28a745")
            if self._ve_analyze_btn:
                self._ve_analyze_btn.configure(state="disabled")
            if self._ve_capture_check:
                self._ve_capture_check.configure(state="disabled")

    def _ve_test_server(self):
        status = self._ve_agent.get_bridge_status()
        self._ve_log(f"Bridge folder: {status['bridge_dir']}")
        self._ve_log(f"Running: {status['running']}")
        self._ve_log(f"Pending commands: {status['pending_commands']}")
        self._ve_log(f"Pending responses: {status['pending_responses']}")
        self._ve_log(f"Processed: {status['processed']}")

    def _ve_copy_ae_script(self):
        self.clipboard_clear()
        self.clipboard_append(self._ve_agent.get_ae_panel_script())
        messagebox.showinfo("Copied", "AE panel script copied. Paste into a .jsx file and run in After Effects.")

    def _ve_save_to_ae_panels(self):
        """Save AE panel script to AE ScriptUI Panels folder."""
        default_path = Path(r"I:\Adobe After Effects 2020\Support Files\Scripts\ScriptUI Panels")
        
        if not default_path.exists():
            common_paths = [
                Path(r"C:\Program Files\Adobe\Adobe After Effects 2020\Support Files\Scripts\ScriptUI Panels"),
                Path(r"C:\Program Files\Adobe\Adobe After Effects 2021\Support Files\Scripts\ScriptUI Panels"),
                Path(r"C:\Program Files\Adobe\Adobe After Effects 2022\Support Files\Scripts\ScriptUI Panels"),
                Path(r"C:\Program Files\Adobe\Adobe After Effects 2023\Support Files\Scripts\ScriptUI Panels"),
                Path(r"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\Scripts\ScriptUI Panels"),
                Path.home() / "AppData/Roaming/Adobe/After Effects/20.0/Scripts/ScriptUI Panels",
                Path.home() / "AppData/Roaming/Adobe/After Effects/21.0/Scripts/ScriptUI Panels",
            ]
            for p in common_paths:
                if p.exists():
                    default_path = p
                    break
            else:
                folder = filedialog.askdirectory(title="Select AE ScriptUI Panels folder")
                if not folder:
                    return
                default_path = Path(folder)
        
        script_file = default_path / "AI_Live_Receiver.jsx"
        try:
            # Save with LF line endings for ExtendScript compatibility
            script = self._ve_agent.get_ae_panel_script().replace('\r\n', '\n')
            script_file.write_text(script, encoding="utf-8")
            self._ve_log(f"[OK] Saved to: {script_file}")
            self._ve_log("Restart After Effects to auto-load the panel")
            messagebox.showinfo("Saved", f"AE Panel saved to:\n{script_file}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")

    def _ve_log(self, text):
        def append_log():
            try:
                console = getattr(self, '_ve_console', None)
                if console is None or not console.winfo_exists():
                    return
                console.insert("end", f"> {text}\n")
                console.see("end")
            except Exception:
                return

        try:
            if self.winfo_exists():
                self.after(0, append_log)
        except Exception:
            return
    
    def _ve_update_quota_display(self):
        """Update the quota indicator label in the UI."""
        if not hasattr(self, '_ve_quota_label') or not self._ve_quota_label:
            return
        try:
            status = self._ve_agent.get_quota_status()
            if status.get("quota_enabled"):
                remaining = status["remaining_today"]
                total = status["pool_total_rpd"]
                label_text = f"Quota: {remaining}/{total} left today"
                color = GREEN if remaining > total * 0.3 else (GOLD if remaining > total * 0.1 else RED)
                self._ve_quota_label.configure(text=label_text, text_color=color)
            else:
                self._ve_quota_label.configure(text="Quota: N/A (no API keys)")
        except Exception:
            self._ve_quota_label.configure(text="Quota: unavailable")
        # Schedule next update
        if hasattr(self, '_main_running') and self._main_running:
            self.after(5000, self._ve_update_quota_display)
    
    def _ve_analyze_footage(self):
        """Open file dialog and run Analyst analysis on the selected video."""
        video_path = filedialog.askopenfilename(
            title="Select video file for analysis",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv"), ("All files", "*.*")]
        )
        if not video_path:
            return
        
        self._ve_log(f"Analyzing: {video_path}")
        self._ve_analyze_btn.configure(state="disabled", text="🎥 Analyzing...")
        
        def on_role_complete(role_label, content):
            self._ve_log(f"[Analyst] {role_label}: {content[:200]}")
            self.after(0, self._ve_update_quota_display)
        
        def task():
            try:
                cmd_id = self._ve_agent.analyze_video(video_path, "Analyze this footage for editing", on_role_complete=on_role_complete)
                self._ve_log(f"Analysis queued (id: {cmd_id})")
                
                def done():
                    self._ve_analyze_btn.configure(state="normal" if self._ve_agent.has_api_keys() else "disabled",
                                                   text="🎥 Analyze Footage")
                self.after(0, done)
            except Exception as e:
                self._ve_log(f"Analysis error: {e}")
                def fail():
                    self._ve_analyze_btn.configure(state="normal" if self._ve_agent.has_api_keys() else "disabled",
                                                   text="🎥 Analyze Footage")
                self.after(0, fail)
        
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    app = AIAgentsApp()
    app.mainloop()
