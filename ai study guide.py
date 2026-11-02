"""
Campus Suite — Study Companion
========================
Features:
  - Dashboard with coloured subject cards
  - Kit Detail page (Image 1 match): title, category badge, file list, + Add, Start studying
  - Multi-file upload — add/remove files per kit at any time
  - AI Study Guide (Image 2 match): structured notes rendered from material
  - Smart Study MCQ mode with adaptive tiers + session summary
  - Test mode: pick a question count + types, get a straight quiz

Requirements:
    pip install customtkinter markitdown google-genai

API key:
    Windows cmd:   set GEMINI_API_KEY=your-key-here
    Windows PS:    $env:GEMINI_API_KEY="your-key-here"
    macOS/Linux:   export GEMINI_API_KEY=your-key-here
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, Canvas

import customtkinter as ctk
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from google import genai
from google.genai import types
from campus_theme import ACCENT, ACCENT_HOVER, BG, BORDER, CARD, CARD_LIGHT, GREEN, RED, TEXT, TEXT_MUTED, TITLE

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
SCRIPT_DIR     = Path(__file__).resolve().parent

# NOTE FOR THE TEACHERS:
# The Gemini API key is set directly below (as plain text) so it's visible
# in the source for grading / review purposes. If you ever share this file
# outside the class (e.g. push it to a public GitHub repo), swap this back
# to an environment variable and rotate the key first — a key sitting in
# plain text in a public file can be used by anyone who finds it.
GEMINI_API_KEY = "AQ.Ab8RN6JpNOD9qL2I-tyU4j6NPf_RsY9PPI29lGQg7udSJgA_1A"
MODEL_NAME     = "gemini-3.1-flash-lite"
KITS_DB_PATH   = SCRIPT_DIR / "kits_db.json"
STORAGE_DIR    = SCRIPT_DIR / "storage"

STORAGE_DIR.mkdir(exist_ok=True)

_client = None
if GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE":
    _client = genai.Client(api_key=GEMINI_API_KEY)

try:
    from markitdown import MarkItDown
    _markitdown_available = True
except ImportError:
    _markitdown_available = False



SUPPORTED_FILE_TYPES = [
    ("Documents", "*.pdf *.docx *.pptx *.xlsx *.txt *.html *.htm *.csv"),
    ("All files", "*.*"),
]

# ---------------------------------------------------------------------------
# THEME
# ---------------------------------------------------------------------------
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

SIDEBAR_BG   = CARD
GOLD         = "#ffd60a"
BLUE_PROG    = "#007aff"

COLOR_MAP = {
    "red":    "#ff3b30",
    "blue":   "#007aff",
    "yellow": "#ffcc00",
}
TEXT_ON_YELLOW = "#1a1a00"


# ---------------------------------------------------------------------------
# CRYPTO UTILITIES (Encrypted Study Vault)
# ---------------------------------------------------------------------------
SALT_FILE = STORAGE_DIR / ".vault_salt"
KEY_FILE = STORAGE_DIR / ".vault_key"
KIT_KEYS_FILE = STORAGE_DIR / ".kit_keys.json"  # kit_id -> encrypted key


def _get_or_create_salt() -> bytes:
    """Get or create a persistent salt for key derivation."""
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = secrets.token_bytes(16)
    SALT_FILE.write_bytes(salt)
    return salt


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a Fernet key from passphrase using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
    return key


def _load_kit_keys() -> dict:
    """Load the encrypted kit keys mapping."""
    if KIT_KEYS_FILE.exists():
        try:
            return json.loads(KIT_KEYS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_kit_keys(keys: dict):
    """Save the encrypted kit keys mapping."""
    KIT_KEYS_FILE.write_text(json.dumps(keys, indent=2), encoding="utf-8")


def encrypt_kit(passphrase: str, kit_data: dict) -> dict:
    """Encrypt a kit's markdown files. Returns dict with encrypted files + metadata."""
    salt = _get_or_create_salt()
    key = _derive_key(passphrase, salt)
    fernet = Fernet(key)

    kit_keys = _load_kit_keys()
    encrypted_files = {}

    for fp in kit_data.get("files", []):
        p = Path(fp)
        if not p.is_absolute():
            p = SCRIPT_DIR / p
        if p.exists():
            plaintext = p.read_bytes()
            encrypted = fernet.encrypt(plaintext)
            encrypted_files[fp] = base64.b64encode(encrypted).decode()

    kit_id = kit_data.get("id", "unknown")
    fingerprint = hashlib.sha256(key).hexdigest()[:16]
    kit_keys[kit_id] = fingerprint
    _save_kit_keys(kit_keys)

    return {
        "encrypted": True,
        "files": encrypted_files,
        "key_fingerprint": fingerprint,
    }


def decrypt_kit(passphrase: str, kit_data: dict) -> bool:
    """Decrypt a kit's markdown files in place. Returns True on success."""
    if not kit_data.get("encrypted"):
        return True

    salt = _get_or_create_salt()
    key = _derive_key(passphrase, salt)
    fernet = Fernet(key)

    kit_keys = _load_kit_keys()
    kit_id = kit_data.get("id", "unknown")
    expected_fp = kit_keys.get(kit_id)
    actual_fp = hashlib.sha256(key).hexdigest()[:16]

    if expected_fp and expected_fp != actual_fp:
        return False

    decrypted_files = []
    for fp, enc_b64 in kit_data.get("files", {}).items():
        try:
            encrypted = base64.b64decode(enc_b64.encode())
            plaintext = fernet.decrypt(encrypted)
            p = Path(fp)
            if not p.is_absolute():
                p = SCRIPT_DIR / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(plaintext)
            decrypted_files.append(str(p.relative_to(SCRIPT_DIR)))
        except Exception:
            for df in decrypted_files:
                try:
                    (SCRIPT_DIR / df).unlink(missing_ok=True)
                except Exception:
                    pass
            return False

    kit_data["encrypted"] = False
    kit_data["files"] = decrypted_files
    return True


def secure_delete_file(filepath: Path):
    """Overwrite file with random data before deletion (3 passes)."""
    if not filepath.exists():
        return
    try:
        size = filepath.stat().st_size
        for _ in range(3):
            filepath.write_bytes(secrets.token_bytes(size))
        filepath.unlink()
    except Exception:
        try:
            filepath.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------
def load_kits_db():
    if KITS_DB_PATH.exists():
        try:
            with open(KITS_DB_PATH, "r", encoding="utf-8") as f:
                db = json.load(f)
                changed = False
                for cat, kits in list(db.items()):
                    for kit in kits:
                        # Migrate older schema: file_path -> files
                        if "file_path" in kit and "files" not in kit:
                            kit["files"] = [kit["file_path"]]
                            changed = True
                        if "file_path" in kit:
                            del kit["file_path"]
                            changed = True
                        
                        # Ensure category matches key if missing
                        if "category" not in kit:
                            kit["category"] = cat
                            changed = True
                            
                        # Convert files to relative paths if they are absolute/nested
                        if "files" in kit:
                            migrated = []
                            for path_str in kit["files"]:
                                p = Path(path_str)
                                if p.is_absolute():
                                    try:
                                        # If it's inside the workspace storage directory, keep relative path
                                        if SCRIPT_DIR in p.parents or p.parts[-2] == "storage":
                                            p = Path("storage") / p.name
                                            changed = True
                                    except Exception:
                                        pass
                                migrated.append(str(p))
                            kit["files"] = migrated
                if changed:
                    save_kits_db(db)
                return db
        except Exception:
            pass
    return {}


def save_kits_db(data):
    with open(KITS_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def find_kit_in_db(kit_id):
    """Return (category, kit_dict) or (None, None)."""
    db = load_kits_db()
    for cat, kits in db.items():
        for k in kits:
            if k["id"] == kit_id:
                return cat, k
    return None, None


def update_kit_in_db(kit_id, updated_kit):
    db = load_kits_db()
    for cat, kits in db.items():
        for i, k in enumerate(kits):
            if k["id"] == kit_id:
                db[cat][i] = updated_kit
                save_kits_db(db)
                return


# ---------------------------------------------------------------------------
# FILE CONVERSION
# ---------------------------------------------------------------------------
def convert_file_to_markdown(filepath):
    p = Path(filepath)
    suffix = p.suffix.lower()
    if suffix in [".txt", ".md", ".csv", ".json", ".html", ".htm"]:
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            try:
                return p.read_text(encoding="latin-1")
            except Exception:
                pass
    
    if not _markitdown_available:
        raise Exception("MarkItDown is not installed for binary files.\nRun: pip install markitdown")
    md = MarkItDown()
    result = md.convert(filepath)
    text = getattr(result, "text_content", None) or getattr(result, "markdown", None)
    if not text or not text.strip():
        raise Exception("MarkItDown returned no extractable text from this file.")
    return text.strip()


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
# AI — METADATA EXTRACTION
# ---------------------------------------------------------------------------
TIER_LABELS = ["Standard", "Intermediate", "Advanced"]


# ===========================================================================
# STUDY GUIDE RENDERER
# Parses simple markdown (##, ###, -, *) into CTk label widgets
# ===========================================================================

# ── typography scale (change these two constants to resize everything) ──────
_FONT_BASE   = 14   # body / bullet text
_FONT_H2     = 20   # ## headings
_FONT_H3     = 16   # ### sub-headings
_WRAP        = 780  # wraplength in px — increase if text still truncates

def _strip_bold(text: str) -> str:
    """Remove markdown bold markers while preserving the text."""
    return text.replace("**", "")


def _make_label(parent, text, font, color, wrap=None, anchor="w", justify="left"):
    kw = dict(text=text, font=font, text_color=color,
              anchor=anchor, justify=justify)
    if wrap:
        kw["wraplength"] = wrap
    return ctk.CTkLabel(parent, **kw)


def render_study_guide(parent, guide_text):
    current_container = parent

    for raw_line in guide_text.split("\n"):
        line     = raw_line.rstrip()
        stripped = line.strip()

        # ── blank line → small spacer ──────────────────────────────────────
        if not stripped:
            ctk.CTkFrame(current_container, fg_color="transparent", height=8).pack(anchor="w")
            continue

        # ── H2  (## Heading) ───────────────────────────────────────────────
        if stripped.startswith("## "):
            text = _strip_bold(stripped[3:])
            
            # Spacer above every block
            ctk.CTkFrame(parent, fg_color="transparent", height=12).pack(anchor="w")
            
            # Create a card container for this section
            card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16, border_color=BORDER, border_width=1)
            card.pack(fill="x", pady=10, padx=4)
            
            # Header container inside card
            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.pack(fill="x", padx=20, pady=(20, 8))
            
            bar = ctk.CTkFrame(hdr, fg_color=ACCENT, width=4, corner_radius=2)
            bar.pack(side="left", fill="y", padx=(0, 10))
            
            _make_label(hdr, text,
                        ctk.CTkFont("Arial", _FONT_H2, "bold"),
                        TITLE).pack(side="left", anchor="w")
            
            # Body container for subsequent elements of this section
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(fill="x", padx=20, pady=(0, 20))
            current_container = body
            continue

        # ── H3  (### Sub-heading) ──────────────────────────────────────────
        if stripped.startswith("### "):
            text = _strip_bold(stripped[4:])
            ctk.CTkFrame(current_container, fg_color="transparent", height=6).pack(anchor="w")
            _make_label(current_container, text,
                        ctk.CTkFont("Arial", _FONT_H3, "bold"),
                        ACCENT, wrap=_WRAP - 40).pack(anchor="w", pady=(4, 2))
            continue

        # ── detect bullet / sub-bullet ─────────────────────────────────────
        indent    = len(line) - len(line.lstrip())
        is_bullet = stripped.startswith("- ") or stripped.startswith("* ")
        is_sub_o  = stripped.startswith("o ")

        # ── Sub-bullet (indented ≥ 2 spaces) ──────────────────────────────
        if indent >= 2 and (is_bullet or is_sub_o):
            text = _strip_bold(stripped[2:])
            row  = ctk.CTkFrame(current_container, fg_color="transparent")
            row.pack(fill="x", padx=(30, 0), pady=2)
            _make_label(row, "◦",
                        ctk.CTkFont("Arial", _FONT_BASE),
                        TEXT_MUTED, wrap=None).pack(side="left", anchor="nw", padx=(0, 8), pady=1)
            _make_label(row, text,
                        ctk.CTkFont("Arial", _FONT_BASE),
                        TEXT, wrap=_WRAP - 80).pack(side="left", fill="x", expand=True)
            continue

        # ── Top-level bullet ───────────────────────────────────────────────
        if is_bullet:
            text = _strip_bold(stripped[2:])
            row  = ctk.CTkFrame(current_container, fg_color="transparent")
            row.pack(fill="x", padx=(4, 0), pady=3)
            _make_label(row, "•",
                        ctk.CTkFont("Arial", _FONT_BASE + 2),
                        ACCENT, wrap=None).pack(side="left", anchor="nw", padx=(0, 10), pady=1)
            _make_label(row, text,
                        ctk.CTkFont("Arial", _FONT_BASE),
                        TEXT, wrap=_WRAP - 40).pack(side="left", fill="x", expand=True)
            continue

        # ── Plain paragraph ────────────────────────────────────────────────
        text = _strip_bold(stripped)
        _make_label(current_container, text,
                    ctk.CTkFont("Arial", _FONT_BASE),
                    TEXT, wrap=_WRAP - 40).pack(anchor="w", pady=2)


# ===========================================================================
# MAIN APP
# ===========================================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Campus Suite — Study Companion")
        self.geometry("1200x760")
        self.minsize(960, 640)
        self.configure(fg_color=BG)
        
        # Navigation state
        self.active_kit = None
        self.active_tab = None

        # Quiz state is created lazily when a practice session starts, but
        # initialise it here so renderers can safely access it on every view.
        self.quiz_questions = []
        self.quiz_current_idx = 0
        self.quiz_responses = []
        
        self._build_layout()
        self._show_dashboard()

    # -----------------------------------------------------------------------
    # LAYOUT SKELETON
    # -----------------------------------------------------------------------
    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, fg_color=SIDEBAR_BG, width=210, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self._update_sidebar(None)

        self.main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)

    def _update_sidebar(self, active_kit=None):
        for w in self.sidebar.winfo_children():
            w.destroy()
            

        # Nexus logo — clickable home button
        nexus_btn = ctk.CTkButton(
            self.sidebar, text="nexus",
            font=ctk.CTkFont("Arial", 30, "bold"),
            text_color=ACCENT, fg_color="transparent",
            hover_color=CARD, anchor="w",
            corner_radius=8, height=44,
            command=self._show_dashboard)
        nexus_btn.pack(pady=(28, 2), padx=12, anchor="w", fill="x")
        ctk.CTkLabel(self.sidebar, text="campus suite",
                     font=ctk.CTkFont("Arial", 11),
                     text_color=TEXT_MUTED).pack(padx=20, anchor="w")
        ctk.CTkFrame(self.sidebar, fg_color=BORDER, height=1).pack(fill="x", padx=16, pady=20)

        if active_kit is None:
            # Global / Home view
            for label, cmd in [
                ("🏠  Home", self._show_dashboard),
                ("➕  New Study Kit", self._show_create_kit),
            ]:
                ctk.CTkButton(self.sidebar, text=label,
                              font=ctk.CTkFont("Arial", 13),
                              fg_color="transparent", hover_color=CARD,
                              text_color=TEXT, anchor="w",
                              corner_radius=10, height=42,
                              command=cmd).pack(fill="x", padx=10, pady=2)
        else:
            # Kit specific view - "🏠 All Kits" at top
            ctk.CTkButton(self.sidebar, text="🏠  All Kits",
                          font=ctk.CTkFont("Arial", 13),
                          fg_color="transparent", hover_color=CARD,
                          text_color=TEXT, anchor="w",
                          corner_radius=10, height=42,
                          command=self._show_dashboard).pack(fill="x", padx=10, pady=2)

            ctk.CTkFrame(self.sidebar, fg_color=BORDER, height=1).pack(fill="x", padx=16, pady=10)

            # Active kit title
            color = active_kit.get("color", ACCENT)
            ctk.CTkLabel(self.sidebar, text=active_kit["name"].upper(),
                         font=ctk.CTkFont("Arial", 11, "bold"),
                         text_color=color, justify="left", wraplength=190).pack(padx=16, pady=(5, 10), anchor="w")

            # Navigation buttons for the kit
            for tab_id, label, cmd in [
                ("materials",   "📁  Materials",    lambda k=active_kit: self._show_kit_detail(k)),
                ("guide",       "📖  Study Guide",  lambda k=active_kit: self._show_study_guide(k)),
                ("summary",     "📝  Summaries",    lambda k=active_kit: self._show_summaries(k)),
                ("flashcards",  "🃏  Flashcards",   lambda k=active_kit: self._show_flashcards(k)),
                ("smart_study", "🧠  Smart Study",  lambda k=active_kit: self._show_smart_study(k)),
                ("test",        "🧪  Test",         lambda k=active_kit: self._show_test_mode(k)),
                ("ctf",         "🏴  CTF Practice", lambda k=active_kit: self._show_ctf_practice(k)),
                ("ask_ai",      "💬  Ask AI",       lambda k=active_kit: self._show_ask_ai(k)),
            ]:
                is_active = (self.active_tab == tab_id)
                fg = ACCENT if is_active else "transparent"
                txt_col = "white" if is_active else TEXT
                ctk.CTkButton(self.sidebar, text=label,
                              font=ctk.CTkFont("Arial", 13, "bold" if is_active else "normal"),
                              fg_color=fg, hover_color=ACCENT_HOVER if is_active else CARD,
                              text_color=txt_col, anchor="w",
                              corner_radius=10, height=42,
                              command=cmd).pack(fill="x", padx=10, pady=2)

            # Spacer to push Delete Kit to the bottom
            spacer = ctk.CTkFrame(self.sidebar, fg_color="transparent", height=10)
            spacer.pack(fill="both", expand=True)

            ctk.CTkButton(self.sidebar, text="🗑️  Delete Kit",
                          font=ctk.CTkFont("Arial", 12),
                          fg_color="transparent", hover_color="#3a1e1e",
                          text_color=RED, anchor="w",
                          corner_radius=10, height=36,
                          command=lambda k=active_kit: self._delete_kit(k)).pack(fill="x", padx=10, pady=10)

        if _client is None:
            ctk.CTkLabel(self.sidebar,
                         text="No GEMINI_API_KEY\nUsing Campus Suite AI",
                         font=ctk.CTkFont("Arial", 10),
                         text_color=GOLD, justify="left").pack(padx=16, pady=(20, 10), anchor="w")

    def _clear_main(self):
        for w in self.main.winfo_children():
            w.destroy()

    # -----------------------------------------------------------------------
    # VIEW: DASHBOARD
    # -----------------------------------------------------------------------
    def _show_dashboard(self):
        self._clear_main()
        self.active_kit = None
        self.active_tab = None
        self._update_sidebar(None)
        db = load_kits_db()

        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=36, pady=(32, 8))
        ctk.CTkLabel(hdr, text="Your Study Kits",
                     font=ctk.CTkFont("Arial", 28, "bold"),
                     text_color=TITLE).pack(side="left")
        ctk.CTkButton(hdr, text="+ New Kit",
                      font=ctk.CTkFont("Arial", 13, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="white", corner_radius=20, height=36, width=120,
                      command=self._show_create_kit).pack(side="right")

        if not db:
            empty = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=20)
            empty.pack(fill="x", padx=36, pady=40)
            ctk.CTkLabel(empty, text="🎓",
                         font=ctk.CTkFont("Arial", 40)).pack(pady=(40, 8))
            ctk.CTkLabel(empty,
                         text="No study kits yet.",
                         font=ctk.CTkFont("Arial", 18, "bold"),
                         text_color=TEXT).pack()
            ctk.CTkLabel(empty,
                         text="Click '+ New Kit' to upload your notes and get started.",
                         font=ctk.CTkFont("Arial", 13),
                         text_color=TEXT_MUTED, justify="center").pack(pady=(4, 48))
            return

        for category, kits in db.items():
            # Category header
            cat_hdr = ctk.CTkFrame(scroll, fg_color="transparent")
            cat_hdr.pack(fill="x", padx=36, pady=(28, 6))
            ctk.CTkLabel(cat_hdr, text=category.upper(),
                         font=ctk.CTkFont("Arial", 11, "bold"),
                         text_color=TEXT_MUTED).pack(side="left", anchor="w")

            row_frame = ctk.CTkFrame(scroll, fg_color="transparent")
            row_frame.pack(fill="x", padx=30, pady=(0, 8))

            for i, kit in enumerate(kits):
                col  = kit.get("color", ACCENT)
                tcol = TEXT_ON_YELLOW if col == COLOR_MAP["yellow"] else "white"
                file_count = len(kit.get("files", []))

                card_btn = ctk.CTkFrame(row_frame, fg_color=CARD,
                                        corner_radius=16, border_color=BORDER, border_width=1)
                card_btn.grid(row=0, column=i, padx=8, pady=6)

                inner_pad = ctk.CTkFrame(card_btn, fg_color="transparent",
                                          width=210, height=140)
                inner_pad.pack(padx=2, pady=2)
                inner_pad.pack_propagate(False)

                # Small colour dot
                dot_row = ctk.CTkFrame(inner_pad, fg_color="transparent")
                dot_row.pack(anchor="w", padx=16, pady=(16, 6))
                ctk.CTkFrame(dot_row, fg_color=col,
                             width=10, height=10, corner_radius=5).pack(side="left")
                ctk.CTkLabel(dot_row,
                             text=f"  {file_count} file{'s' if file_count != 1 else ''}",
                             font=ctk.CTkFont("Arial", 10),
                             text_color=TEXT_MUTED).pack(side="left")

                ctk.CTkLabel(inner_pad,
                             text=kit["name"],
                             font=ctk.CTkFont("Arial", 13, "bold"),
                             text_color=TEXT, wraplength=180,
                             justify="left").pack(anchor="w", padx=16, pady=(0, 8))

                open_btn = ctk.CTkButton(inner_pad, text="Open →",
                                          font=ctk.CTkFont("Arial", 10, "bold"),
                                          fg_color=col, hover_color=ACCENT_HOVER,
                                          text_color=tcol, corner_radius=12,
                                          height=28, width=88,
                                          command=lambda k=kit: self._show_kit_detail(k))
                open_btn.pack(anchor="w", padx=16, pady=(0, 12))

                # Make entire card clickable
                for widget in [card_btn, inner_pad]:
                    widget.bind("<Button-1>", lambda e, k=kit: self._show_kit_detail(k))
                    widget.configure(cursor="hand2")

    # -----------------------------------------------------------------------
    # VIEW: KIT DETAIL  — matches Image 1
    # -----------------------------------------------------------------------
    def _show_kit_detail(self, kit):
        self._clear_main()
        # Always reload freshest data
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        # Handle encrypted kit
        if kit.get("encrypted"):
            if not self._prompt_decrypt_kit(kit):
                self._show_dashboard()
                return
            # Reload after decryption
            _, kit = find_kit_in_db(kit["id"])
            if kit is None:
                self._show_dashboard()
                return

        self.active_kit = kit
        self.active_tab = "materials"
        self._update_sidebar(kit)

        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # --- Kit header block ---
        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=30, pady=(30, 4))

        # Coloured icon
        icon_box = ctk.CTkFrame(hdr, fg_color=kit.get("color", ACCENT),
                                width=60, height=60, corner_radius=14)
        icon_box.pack(side="left", padx=(0, 20))
        icon_box.pack_propagate(False)
        ctk.CTkLabel(icon_box, text="🔒" if kit.get("encrypted") else "🖥",
                     font=ctk.CTkFont("Arial", 24)).place(relx=0.5, rely=0.5, anchor="center")

        title_col = ctk.CTkFrame(hdr, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(title_col, text=kit["name"].upper(),
                     font=ctk.CTkFont("Arial", 22, "bold"),
                     text_color=TEXT, anchor="w").pack(anchor="w")

        # Category badge — matches the yellow folder badge in Image 1
        badge_frame = ctk.CTkFrame(title_col, fg_color=CARD_LIGHT, corner_radius=8)
        badge_frame.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(badge_frame,
                     text=f"  {kit.get('category', '')}  ",
                     font=ctk.CTkFont("Arial", 11, "bold"),
                     text_color=TEXT_MUTED).pack(padx=6, pady=4)

        if kit.get("encrypted"):
            lock_label = ctk.CTkLabel(title_col, text="  🔒 Encrypted",
                                      font=ctk.CTkFont("Arial", 11, "bold"),
                                      text_color=GOLD)
            lock_label.pack(anchor="w", pady=(4, 0))
            ctk.CTkButton(title_col, text="Lock Kit", font=ctk.CTkFont("Arial", 10),
                          fg_color=RED, hover_color="#cc2a20", text_color="white",
                          corner_radius=10, height=28, width=80,
                          command=lambda k=kit: self._lock_kit(k)).pack(anchor="w", pady=(4, 0))

        # --- Materials section ---
        ctk.CTkFrame(scroll, fg_color=BORDER, height=1).pack(fill="x", padx=30, pady=(18, 0))

        mat_hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        mat_hdr.pack(fill="x", padx=30, pady=(14, 6))

        file_count = len(kit.get("files", []))
        ctk.CTkLabel(mat_hdr,
                     text=f"{file_count} study material{'s' if file_count != 1 else ''}",
                     font=ctk.CTkFont("Arial", 15, "bold"),
                     text_color=TEXT).pack(side="left")

        ctk.CTkButton(mat_hdr, text="+ Add",
                      font=ctk.CTkFont("Arial", 12, "bold"),
                      fg_color=CARD_LIGHT, hover_color=BORDER,
                      text_color=TEXT, corner_radius=16,
                      height=32, width=80,
                      command=lambda: self._add_file_to_kit(kit),
                      ).pack(side="left", padx=(14, 0))

        # File list — stored so we can refresh it
        self._kit_files_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._kit_files_frame.pack(fill="x", padx=30, pady=(0, 30))
        self._render_file_list(kit)

    # -----------------------------------------------------------------------
    # ENCRYPTION HELPERS
    # -----------------------------------------------------------------------
    def _prompt_decrypt_kit(self, kit):
        """Prompt for passphrase to decrypt an encrypted kit. Returns True on success."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Unlock Encrypted Kit")
        dialog.geometry("400x200")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=BG)

        ctk.CTkLabel(dialog, text=f"🔒  {kit['name']} is encrypted",
                     font=ctk.CTkFont("Arial", 16, "bold"), text_color=TITLE).pack(pady=(20, 5))
        ctk.CTkLabel(dialog, text="Enter passphrase to unlock:",
                     font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED).pack(pady=(0, 15))

        passphrase_var = ctk.StringVar()
        entry = ctk.CTkEntry(dialog, textvariable=passphrase_var, show="●",
                             font=ctk.CTkFont("Arial", 13), fg_color=CARD_LIGHT,
                             border_color=BORDER, text_color=TEXT, height=40, width=300,
                             corner_radius=12)
        entry.pack(pady=5)
        entry.focus()

        result = {"success": False}

        def on_ok():
            if decrypt_kit(passphrase_var.get(), kit):
                update_kit_in_db(kit["id"], kit)
                result["success"] = True
                dialog.destroy()
            else:
                messagebox.showerror("Failed", "Incorrect passphrase or decryption error.", parent=dialog)

        def on_cancel():
            dialog.destroy()

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(pady=15)
        ctk.CTkButton(btn_row, text="Cancel", command=on_cancel,
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                      corner_radius=12, height=36, width=100).pack(side="left", padx=10)
        ctk.CTkButton(btn_row, text="Unlock", command=on_ok,
                      fg_color=GREEN, hover_color="#2e7d32", text_color="white",
                      corner_radius=12, height=36, width=120).pack(side="left", padx=10)

        self.wait_window(dialog)
        return result["success"]

    def _lock_kit(self, kit):
        """Re-encrypt a decrypted kit (lock it again)."""
        # Get the stored fingerprint to derive the same key
        kit_keys = _load_kit_keys()
        kit_id = kit.get("id", "unknown")
        fingerprint = kit_keys.get(kit_id)
        
        if not fingerprint:
            messagebox.showerror("Error", "Cannot lock: key fingerprint not found.")
            return

        # We need the passphrase again to re-encrypt
        dialog = ctk.CTkToplevel(self)
        dialog.title("Lock Kit")
        dialog.geometry("400x200")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=BG)

        ctk.CTkLabel(dialog, text=f"🔒  Lock {kit['name']}",
                     font=ctk.CTkFont("Arial", 16, "bold"), text_color=TITLE).pack(pady=(20, 5))
        ctk.CTkLabel(dialog, text="Enter passphrase to re-encrypt:",
                     font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED).pack(pady=(0, 15))

        passphrase_var = ctk.StringVar()
        entry = ctk.CTkEntry(dialog, textvariable=passphrase_var, show="●",
                             font=ctk.CTkFont("Arial", 13), fg_color=CARD_LIGHT,
                             border_color=BORDER, text_color=TEXT, height=40, width=300,
                             corner_radius=12)
        entry.pack(pady=5)
        entry.focus()

        def on_ok():
            # First decrypt with the passphrase to verify, then re-encrypt
            if decrypt_kit(passphrase_var.get(), kit):
                # Now re-encrypt
                enc_result = encrypt_kit(passphrase_var.get(), kit)
                kit["encrypted"] = True
                kit["files"] = enc_result["files"]
                kit["key_fingerprint"] = enc_result["key_fingerprint"]
                update_kit_in_db(kit["id"], kit)
                dialog.destroy()
                self._show_kit_detail(kit)
            else:
                messagebox.showerror("Failed", "Incorrect passphrase.", parent=dialog)

        def on_cancel():
            dialog.destroy()

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(pady=15)
        ctk.CTkButton(btn_row, text="Cancel", command=on_cancel,
                      fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                      corner_radius=12, height=36, width=100).pack(side="left", padx=10)
        ctk.CTkButton(btn_row, text="Lock", command=on_ok,
                      fg_color=RED, hover_color="#cc2a20", text_color="white",
                      corner_radius=12, height=36, width=120).pack(side="left", padx=10)

        self.wait_window(dialog)

    def _render_file_list(self, kit):
        for w in self._kit_files_frame.winfo_children():
            w.destroy()

        files = kit.get("files", [])
        is_encrypted = kit.get("encrypted", False)

        if is_encrypted and isinstance(files, dict):
            # Encrypted: files is a dict {path: base64_encrypted_content}
            for fp, enc_b64 in files.items():
                p = Path(fp)
                display_name = p.stem.rsplit("_", 1)[0].replace("_", " ")
                suffix = p.suffix if p.suffix else ".pdf"

                row = ctk.CTkFrame(self._kit_files_frame,
                                   fg_color=CARD_LIGHT, corner_radius=10, height=46)
                row.pack(fill="x", pady=3)
                row.pack_propagate(False)

                icon_badge = ctk.CTkFrame(row, fg_color=BORDER, corner_radius=6, width=38, height=32)
                icon_badge.pack(side="left", padx=(10, 8))
                icon_badge.pack_propagate(False)
                ctk.CTkLabel(icon_badge, text="🔒" if "pdf" in suffix.lower() else "🔒",
                             font=ctk.CTkFont("Arial", 8, "bold"),
                             text_color=GOLD).place(relx=0.5, rely=0.5, anchor="center")

                ctk.CTkLabel(row, text=f"{display_name}  (encrypted)",
                             font=ctk.CTkFont("Arial", 12),
                             text_color=TEXT_MUTED, anchor="w").pack(side="left", fill="x", expand=True)

                # No remove button for encrypted files (would need full decrypt/encrypt cycle)
                ctk.CTkLabel(row, text="🔒",
                             font=ctk.CTkFont("Arial", 13),
                             text_color=GOLD).pack(side="right", padx=8)
        else:
            # Normal: files is a list of paths
            for fp in files:
                p = Path(fp)
                display_name = p.stem.rsplit("_", 1)[0].replace("_", " ")
                suffix = p.suffix if p.suffix else ".pdf"

                row = ctk.CTkFrame(self._kit_files_frame,
                                   fg_color=CARD_LIGHT, corner_radius=10, height=46)
                row.pack(fill="x", pady=3)
                row.pack_propagate(False)

                icon_badge = ctk.CTkFrame(row, fg_color=BORDER, corner_radius=6, width=38, height=32)
                icon_badge.pack(side="left", padx=(10, 8))
                icon_badge.pack_propagate(False)
                ctk.CTkLabel(icon_badge, text="PDF" if "pdf" in suffix.lower() else "DOC",
                             font=ctk.CTkFont("Arial", 8, "bold"),
                             text_color=ACCENT).place(relx=0.5, rely=0.5, anchor="center")

                ctk.CTkLabel(row, text=display_name,
                             font=ctk.CTkFont("Arial", 12),
                             text_color=TEXT, anchor="w").pack(side="left", fill="x", expand=True)

                ctk.CTkButton(row, text="  ✕",
                              font=ctk.CTkFont("Arial", 13),
                              fg_color="transparent", hover_color=CARD,
                              text_color=TEXT_MUTED,
                              width=40, height=40, corner_radius=20,
                              command=lambda f=fp: self._remove_file_from_kit(kit, f),
                              ).pack(side="right", padx=8)

    def _add_file_to_kit(self, kit):
        if kit.get("encrypted"):
            messagebox.showwarning("Kit Locked", "Cannot add files to an encrypted kit.\nUnlock it first using 'Lock Kit' button.")
            return
        if not _markitdown_available:
            messagebox.showerror("MarkItDown missing", "Run: pip install markitdown")
            return
        paths = filedialog.askopenfilenames(title="Add files to kit",
                                             filetypes=SUPPORTED_FILE_TYPES)
        if not paths:
            return

        # Show converting status inline
        prog_lbl = ctk.CTkLabel(self._kit_files_frame,
                                 text=f"Converting {len(paths)} file(s)…",
                                 font=ctk.CTkFont("Arial", 12), text_color=TEXT_MUTED)
        prog_lbl.pack(pady=6)

        def task():
            errors = []
            for path in paths:
                try:
                    # Text fallback parsing is handled inside convert_file_to_markdown
                    md = convert_file_to_markdown(path)
                    stem = Path(path).stem
                    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in stem)
                    store = STORAGE_DIR / f"{safe}_{uuid.uuid4().hex[:4]}.md"
                    store.write_text(md, encoding="utf-8")
                    # Store relative path for portability
                    rel_path = store.relative_to(SCRIPT_DIR)
                    kit.setdefault("files", []).append(str(rel_path))
                except Exception as e:
                    errors.append(f"{Path(path).name}: {e}")

            # Invalidate cached Study Guide and Summary since files changed
            kit.pop("study_guide", None)
            kit.pop("summary", None)
            update_kit_in_db(kit["id"], kit)

            def done():
                if errors:
                    messagebox.showwarning("Some files failed", "\n".join(errors))
                self._show_kit_detail(kit)

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def _remove_file_from_kit(self, kit, filepath):
        if kit.get("encrypted"):
            messagebox.showwarning("Kit Locked", "Cannot remove files from an encrypted kit.\nUnlock it first using 'Lock Kit' button.")
            return
        if len(kit.get("files", [])) <= 1:
            messagebox.showwarning("Cannot remove",
                                    "A kit must keep at least one file.")
            return
        p_path = Path(filepath)
        if not messagebox.askyesno("Remove file",
                                    f"Remove '{p_path.stem.rsplit('_', 1)[0].replace('_', ' ')}' from this kit?"):
            return
        kit["files"] = [f for f in kit["files"] if f != filepath]
        
        # Delete file on disk (make absolute if relative)
        try:
            abs_fp = p_path if p_path.is_absolute() else SCRIPT_DIR / p_path
            abs_fp.unlink(missing_ok=True)
        except Exception:
            pass
            
        # Invalidate cache
        kit.pop("study_guide", None)
        kit.pop("summary", None)
        update_kit_in_db(kit["id"], kit)
        self._show_kit_detail(kit)

    def _delete_kit(self, kit):
        if not messagebox.askyesno("Delete Kit",
                                    f"Delete '{kit['name']}' and all its files?"):
            return
        db = load_kits_db()
        cat = kit.get("category", "")
        if cat in db:
            db[cat] = [k for k in db[cat] if k["id"] != kit["id"]]
            if not db[cat]:
                del db[cat]
        save_kits_db(db)
        
        # Remove encryption keys
        kit_keys = _load_kit_keys()
        kit_id = kit.get("id", "unknown")
        if kit_id in kit_keys:
            del kit_keys[kit_id]
            _save_kit_keys(kit_keys)

        for fp in kit.get("files", []):
            try:
                p_path = Path(fp)
                abs_fp = p_path if p_path.is_absolute() else SCRIPT_DIR / p_path
                if kit.get("encrypted"):
                    secure_delete_file(abs_fp)
                else:
                    abs_fp.unlink(missing_ok=True)
            except Exception:
                pass
        self._show_dashboard()

    # -----------------------------------------------------------------------
    # VIEW: STUDY GUIDE — matches Image 2
    # -----------------------------------------------------------------------
    def _show_study_guide(self, kit):
        self._clear_main()
        # Always reload freshest data
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        self.active_kit = kit
        self.active_tab = "guide"
        self._update_sidebar(kit)

        # Scrollable content
        content = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(content, fg_color="transparent")
        inner.pack(fill="x", padx=48, pady=(28, 50))

        # Check cache
        cached_guide = kit.get("study_guide")
        if cached_guide:
            # Kit title
            ctk.CTkLabel(inner, text=kit["name"],
                         font=ctk.CTkFont("Arial", 26, "bold"),
                         text_color=TEXT, anchor="w").pack(anchor="w", pady=(0, 6))
            render_study_guide(inner, cached_guide)
            return

        # Loading indicators
        loading_lbl = ctk.CTkLabel(inner, text="Generating Study Guide…",
                                    font=ctk.CTkFont("Arial", 14), text_color=TEXT_MUTED)
        loading_lbl.pack(pady=36)
        loading_bar = ctk.CTkProgressBar(inner, mode="indeterminate",
                                          progress_color=ACCENT, fg_color=CARD_LIGHT)
        loading_bar.pack(fill="x", padx=60)
        loading_bar.start()

        md_text = combined_markdown(kit)

        def task():
            try:
                guide = generate_study_guide(md_text, kit.get("category", ""))
                
                # Update cache in kit db
                kit["study_guide"] = guide
                update_kit_in_db(kit["id"], kit)

                def render():
                    loading_lbl.destroy()
                    loading_bar.destroy()

                    # Kit title
                    ctk.CTkLabel(inner, text=kit["name"],
                                 font=ctk.CTkFont("Arial", 26, "bold"),
                                 text_color=TEXT, anchor="w").pack(anchor="w", pady=(0, 6))

                    render_study_guide(inner, guide)

                self.after(0, render)

            except Exception as e:
                err = str(e)
                self.after(0, lambda: loading_lbl.configure(
                    text=f"Error generating guide: {err}", text_color=RED))
                self.after(0, loading_bar.destroy)

        threading.Thread(target=task, daemon=True).start()

    # -----------------------------------------------------------------------
    # VIEW: STUDY SUMMARIES
    # -----------------------------------------------------------------------
    def _show_summaries(self, kit):
        self._clear_main()
        # Always reload freshest data
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        self.active_kit = kit
        self.active_tab = "summary"
        self._update_sidebar(kit)

        # Scrollable content
        content = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(content, fg_color="transparent")
        inner.pack(fill="x", padx=48, pady=(28, 50))

        # Check cache
        cached_summary = kit.get("summary")
        if cached_summary:
            # Kit title
            ctk.CTkLabel(inner, text=f"{kit['name']} - Summary",
                         font=ctk.CTkFont("Arial", 26, "bold"),
                         text_color=TEXT, anchor="w").pack(anchor="w", pady=(0, 6))
            render_study_guide(inner, cached_summary)
            return

        # Loading indicators
        loading_lbl = ctk.CTkLabel(inner, text="Generating Summary…",
                                    font=ctk.CTkFont("Arial", 14), text_color=TEXT_MUTED)
        loading_lbl.pack(pady=36)
        loading_bar = ctk.CTkProgressBar(inner, mode="indeterminate",
                                          progress_color=ACCENT, fg_color=CARD_LIGHT)
        loading_bar.pack(fill="x", padx=60)
        loading_bar.start()

        md_text = combined_markdown(kit)

        def task():
            try:
                summary = generate_summary(md_text, kit.get("category", ""))
                
                # Update cache in kit db
                kit["summary"] = summary
                update_kit_in_db(kit["id"], kit)

                def render():
                    loading_lbl.destroy()
                    loading_bar.destroy()

                    # Kit title
                    ctk.CTkLabel(inner, text=f"{kit['name']} - Summary",
                                 font=ctk.CTkFont("Arial", 26, "bold"),
                                 text_color=TEXT, anchor="w").pack(anchor="w", pady=(0, 6))

                    render_study_guide(inner, summary)

                self.after(0, render)

            except Exception as e:
                err = str(e)
                self.after(0, lambda: loading_lbl.configure(
                    text=f"Error generating summary: {err}", text_color=RED))
                self.after(0, loading_bar.destroy)

        threading.Thread(target=task, daemon=True).start()

    # -----------------------------------------------------------------------
    # VIEW: CREATE KIT
    # -----------------------------------------------------------------------
    def _show_create_kit(self):
        self._clear_main()
        self.active_kit = None
        self.active_tab = None
        self._update_sidebar(None)

        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(scroll, text="Create a Study Kit",
                     font=ctk.CTkFont("Arial", 26, "bold"),
                     text_color=TITLE).pack(anchor="w", padx=40, pady=(36, 4))
        ctk.CTkLabel(scroll,
                     text="Upload one or more files — Campus Suite analyses them and builds your kit automatically.",
                     font=ctk.CTkFont("Arial", 12),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=40, pady=(0, 24))

        # Upload card
        upload_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=20)
        upload_card.pack(fill="x", padx=40, pady=(0, 16))

        files_hdr = ctk.CTkFrame(upload_card, fg_color="transparent")
        files_hdr.pack(fill="x", padx=24, pady=(20, 10))
        ctk.CTkLabel(files_hdr, text="Source Files",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkButton(files_hdr, text="+ Add Files",
                      font=ctk.CTkFont("Arial", 12, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="white", corner_radius=14,
                      height=32, width=110,
                      command=self._ck_browse).pack(side="right")

        self._ck_files = []   # list of (original_path, markdown_text)
        self._ck_files_frame = ctk.CTkFrame(upload_card, fg_color="transparent")
        self._ck_files_frame.pack(fill="x", padx=24, pady=(0, 20))
        self._ck_empty_lbl = ctk.CTkLabel(self._ck_files_frame,
                                           text="No files added yet",
                                           font=ctk.CTkFont("Arial", 12),
                                           text_color=TEXT_MUTED)
        self._ck_empty_lbl.pack(pady=10)

        if not _markitdown_available:
            ctk.CTkLabel(upload_card,
                         text="  markitdown not installed — run: pip install markitdown",
                         font=ctk.CTkFont("Arial", 11), text_color=RED,
                         ).pack(anchor="w", padx=24, pady=(0, 14))

        self._ck_status = ctk.CTkLabel(scroll, text="",
                                        font=ctk.CTkFont("Arial", 12), text_color=TEXT_MUTED)
        self._ck_status.pack(padx=40, pady=(4, 8))

        self._ck_create_btn = ctk.CTkButton(
            scroll, text="Analyse & Create Kit",
            font=ctk.CTkFont("Arial", 15, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", corner_radius=22,
            height=50, width=260,
            command=self._ck_create)
        self._ck_create_btn.pack(pady=4)

        ctk.CTkButton(scroll, text="Back",
                      font=ctk.CTkFont("Arial", 12),
                      fg_color="transparent", hover_color=CARD_LIGHT,
                      text_color=TEXT_MUTED, corner_radius=16, height=36,
                      command=self._show_dashboard).pack(pady=8)

    def _ck_render_files(self):
        for w in self._ck_files_frame.winfo_children():
            w.destroy()
        if not self._ck_files:
            ctk.CTkLabel(self._ck_files_frame, text="No files added yet",
                         font=ctk.CTkFont("Arial", 12), text_color=TEXT_MUTED).pack(pady=10)
            return
        for idx, (path, _) in enumerate(self._ck_files):
            row = ctk.CTkFrame(self._ck_files_frame, fg_color=CARD_LIGHT,
                               corner_radius=10, height=42)
            row.pack(fill="x", pady=3)
            row.pack_propagate(False)
            ctk.CTkLabel(row, text="📄",
                         font=ctk.CTkFont("Arial", 14)).pack(side="left", padx=(12, 6))
            ctk.CTkLabel(row, text=Path(path).name,
                         font=ctk.CTkFont("Arial", 12), text_color=TEXT,
                         anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="✕",
                          font=ctk.CTkFont("Arial", 12, "bold"),
                          fg_color="transparent", hover_color=CARD,
                          text_color=TEXT_MUTED, width=32, height=32, corner_radius=8,
                          command=lambda i=idx: self._ck_remove_file(i),
                          ).pack(side="right", padx=6)

    def _ck_remove_file(self, idx):
        self._ck_files.pop(idx)
        self._ck_render_files()

    def _ck_browse(self):
        if not _markitdown_available:
            messagebox.showerror("MarkItDown missing", "Run: pip install markitdown")
            return
        paths = filedialog.askopenfilenames(title="Select study material",
                                             filetypes=SUPPORTED_FILE_TYPES)
        if not paths:
            return
        self._ck_status.configure(text=f"Converting {len(paths)} file(s)…", text_color=TEXT_MUTED)
        self._ck_create_btn.configure(state="disabled")

        def task():
            errors = []
            for path in paths:
                try:
                    md = convert_file_to_markdown(path)
                    self._ck_files.append((path, md))
                except Exception as e:
                    errors.append(f"{Path(path).name}: {e}")

            def done():
                self._ck_render_files()
                self._ck_create_btn.configure(state="normal")
                if errors:
                    messagebox.showwarning("Some files failed", "\n".join(errors))
                    self._ck_status.configure(
                        text="Some files could not be converted.", text_color=RED)
                else:
                    self._ck_status.configure(
                        text=f"{len(self._ck_files)} file(s) ready.", text_color=GREEN)

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def _ck_create(self):
        if not self._ck_files:
            messagebox.showerror("No files", "Please add at least one file.")
            return
        # No Gemini key is fine — _generate_text falls back to the shared
        # Campus Suite key manager and surfaces any failure in the status line.

        # Ask about encryption
        encrypt = messagebox.askyesno("Encrypt Kit", 
            "Encrypt this study kit with a passphrase?\n\n"
            "Encrypted kits require the passphrase to open. "
            "Files are encrypted with AES-256 (Fernet) via PBKDF2 key derivation.")
        passphrase = None
        if encrypt:
            # Simple passphrase dialog
            dialog = ctk.CTkToplevel(self)
            dialog.title("Set Passphrase")
            dialog.geometry("400x220")
            dialog.resizable(False, False)
            dialog.transient(self)
            dialog.grab_set()
            dialog.configure(fg_color=BG)
            
            ctk.CTkLabel(dialog, text="Enter Passphrase",
                         font=ctk.CTkFont("Arial", 16, "bold"), text_color=TITLE).pack(pady=(20, 10))
            ctk.CTkLabel(dialog, text="You will need this to open the kit later.",
                         font=ctk.CTkFont("Arial", 11), text_color=TEXT_MUTED).pack(pady=(0, 15))
            
            passphrase_var = ctk.StringVar()
            entry = ctk.CTkEntry(dialog, textvariable=passphrase_var, show="●",
                                 font=ctk.CTkFont("Arial", 13), fg_color=CARD_LIGHT,
                                 border_color=BORDER, text_color=TEXT, height=40, width=300,
                                 corner_radius=12)
            entry.pack(pady=5)
            entry.focus()
            
            confirm_var = ctk.StringVar()
            confirm_entry = ctk.CTkEntry(dialog, textvariable=confirm_var, show="●",
                                         font=ctk.CTkFont("Arial", 13), fg_color=CARD_LIGHT,
                                         border_color=BORDER, text_color=TEXT, height=40, width=300,
                                         corner_radius=12, placeholder_text="Confirm passphrase")
            confirm_entry.pack(pady=5)
            
            result = {"passphrase": None}
            
            def on_ok():
                if passphrase_var.get() != confirm_var.get():
                    messagebox.showerror("Mismatch", "Passphrases do not match.", parent=dialog)
                    return
                if len(passphrase_var.get()) < 8:
                    messagebox.showerror("Too Short", "Passphrase must be at least 8 characters.", parent=dialog)
                    return
                result["passphrase"] = passphrase_var.get()
                dialog.destroy()
            
            def on_cancel():
                dialog.destroy()
            
            btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
            btn_row.pack(pady=15)
            ctk.CTkButton(btn_row, text="Cancel", command=on_cancel,
                          fg_color=CARD_LIGHT, hover_color=BORDER, text_color=TEXT_MUTED,
                          corner_radius=12, height=36, width=100).pack(side="left", padx=10)
            ctk.CTkButton(btn_row, text="Create Kit", command=on_ok,
                          fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                          corner_radius=12, height=36, width=120).pack(side="left", padx=10)
            
            self.wait_window(dialog)
            passphrase = result["passphrase"]
            if passphrase is None:
                return  # User cancelled

        self._ck_create_btn.configure(state="disabled", text="Analysing…")
        self._ck_status.configure(text="Step 1 / 2 — Extracting kit metadata…",
                                   text_color=TEXT_MUTED)
        files_snapshot = list(self._ck_files)

        def task():
            try:
                first_md = files_snapshot[0][1]
                meta = extract_kit_metadata(first_md)
                kit_name   = meta["kit_name"]
                category   = meta["subject_category"]
                card_color = meta["card_color"]

                self.after(0, lambda: self._ck_status.configure(
                    text=f'Step 2 / 2 — Saving kit "{kit_name}"…', text_color=TEXT_MUTED))

                saved_paths = []
                for orig_path, md in files_snapshot:
                    safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                                   for c in Path(orig_path).stem)
                    store = STORAGE_DIR / f"{safe}_{uuid.uuid4().hex[:4]}.md"
                    store.write_text(md, encoding="utf-8")
                    rel_path = store.relative_to(SCRIPT_DIR)
                    saved_paths.append(str(rel_path))

                db = load_kits_db()
                kit = {
                    "id":       f"kit_{uuid.uuid4().hex[:8]}",
                    "name":     kit_name,
                    "color":    COLOR_MAP.get(card_color, COLOR_MAP["blue"]),
                    "files":    saved_paths,
                    "category": category,
                }
                
                # Encrypt if passphrase provided
                if passphrase:
                    enc_result = encrypt_kit(passphrase, kit)
                    kit["encrypted"] = True
                    kit["files"] = enc_result["files"]
                    kit["key_fingerprint"] = enc_result["key_fingerprint"]

                db.setdefault(category, []).append(kit)
                save_kits_db(db)

                self.after(0, self._show_dashboard)

            except Exception as e:
                err = str(e)
                self.after(0, lambda: self._ck_status.configure(
                    text=f"Error: {err}", text_color=RED))
                self.after(0, lambda: self._ck_create_btn.configure(
                    state="normal", text="Analyse & Create Kit"))

        threading.Thread(target=task, daemon=True).start()

    # -----------------------------------------------------------------------
    # VIEW: SMART STUDY
    # -----------------------------------------------------------------------
    def _show_smart_study(self, kit):
        self._clear_main()
        # Always reload freshest data
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        self.active_kit = kit
        self.active_tab = "smart_study"
        self._update_sidebar(kit)

        # Main scroll frame for Smart Study
        self.quiz_scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        self.quiz_scroll.pack(fill="both", expand=True)
        self.quiz_scroll.grid_columnconfigure(0, weight=1)

        # Options/Start Screen
        self._render_quiz_options_screen(kit)

    def _render_quiz_options_screen(self, kit):
        for w in self.quiz_scroll.winfo_children():
            w.destroy()

        inner = ctk.CTkFrame(self.quiz_scroll, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=40, pady=32)

        # ── Title row with gear icon ──────────────────────────────────────
        title_row = ctk.CTkFrame(inner, fg_color="transparent")
        title_row.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(title_row, text="🧠  Smart Study",
                     font=ctk.CTkFont("Arial", 26, "bold"),
                     text_color=TITLE).pack(side="left")

        gear_btn = ctk.CTkButton(title_row, text="⚙️  Settings",
                                 font=ctk.CTkFont("Arial", 12),
                                 fg_color=CARD_LIGHT, hover_color=BORDER,
                                 text_color=TEXT_MUTED, corner_radius=14,
                                 height=32, width=110)
        gear_btn.pack(side="right")

        ctk.CTkLabel(inner,
                     text="AI-generated questions across multiple formats — grounded in your kit materials.",
                     font=ctk.CTkFont("Arial", 13),
                     text_color=TEXT_MUTED).pack(anchor="w", pady=(0, 20))

        # ── Settings panel (collapsible) ──────────────────────────────────
        settings_panel = ctk.CTkFrame(inner, fg_color=CARD, corner_radius=16)
        settings_panel.pack(fill="x", pady=(0, 10))
        settings_panel.pack_forget()   # hidden by default

        sp_inner = ctk.CTkFrame(settings_panel, fg_color="transparent")
        sp_inner.pack(fill="x", padx=24, pady=18)

        # Difficulty slider
        ctk.CTkLabel(sp_inner, text="⚡  Difficulty",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._ss_difficulty = ctk.IntVar(value=3)
        diff_row = ctk.CTkFrame(sp_inner, fg_color="transparent")
        diff_row.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        sp_inner.grid_columnconfigure(0, weight=1)
        diff_slider = ctk.CTkSlider(diff_row, from_=1, to=5, number_of_steps=4,
                                    variable=self._ss_difficulty,
                                    fg_color=CARD_LIGHT, progress_color=ACCENT,
                                    button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                    width=280)
        diff_slider.pack(side="left")
        diff_labels = ["1 Basic", "2", "3 Mid", "4", "5 Hard"]
        diff_lbl = ctk.CTkLabel(diff_row, text=diff_labels[self._ss_difficulty.get() - 1],
                                font=ctk.CTkFont("Arial", 11), text_color=ACCENT, width=70)
        diff_lbl.pack(side="left", padx=10)
        def _on_diff(val):
            v = round(float(val))
            diff_lbl.configure(text=diff_labels[v - 1])
        diff_slider.configure(command=_on_diff)

        # Question type toggles
        ctk.CTkLabel(sp_inner, text="☑️  Question Types",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).grid(row=2, column=0, sticky="w", pady=(0, 6))
        types_frame = ctk.CTkFrame(sp_inner, fg_color="transparent")
        types_frame.grid(row=3, column=0, sticky="w", pady=(0, 14))
        self._ss_type_vars = {}
        type_defs = [
            ("mcq",         "🔘 Single-Choice MCQ"),
            ("multiselect", "☑️ Multi-Select"),
            ("match",       "🔀 Match the Following"),
            ("shortanswer", "✍️ Short Answer"),
        ]
        for col_i, (tid, tlabel) in enumerate(type_defs):
            var = ctk.BooleanVar(value=True)
            self._ss_type_vars[tid] = var
            ctk.CTkCheckBox(types_frame, text=tlabel,
                            variable=var,
                            font=ctk.CTkFont("Arial", 12),
                            fg_color=ACCENT, border_color=BORDER,
                            hover_color=ACCENT_HOVER,
                            text_color=TEXT).grid(row=0, column=col_i, padx=(0, 20), sticky="w")

        # Topic Focus entry
        ctk.CTkLabel(sp_inner, text="🎯  Topic Focus (optional)",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).grid(row=4, column=0, sticky="w", pady=(0, 4))
        self._ss_focus_entry = ctk.CTkEntry(
            sp_inner,
            placeholder_text='e.g. "Focus on the chronological timeline" or "Scenario questions"…',
            font=ctk.CTkFont("Arial", 12),
            fg_color=CARD_LIGHT, border_color=BORDER,
            text_color=TEXT, height=38)
        self._ss_focus_entry.grid(row=5, column=0, sticky="ew", pady=(0, 4))

        def toggle_settings():
            if settings_panel.winfo_ismapped():
                settings_panel.pack_forget()
                gear_btn.configure(text="⚙️  Settings", fg_color=CARD_LIGHT, text_color=TEXT_MUTED)
            else:
                settings_panel.pack(fill="x", pady=(0, 10), before=mode_card)
                gear_btn.configure(text="✕  Close", fg_color=ACCENT, text_color="white")
        gear_btn.configure(command=toggle_settings)

        # ── Mode Selection Card ──────────────────────────────────────────
        mode_card = ctk.CTkFrame(inner, fg_color=CARD, corner_radius=20)
        mode_card.pack(fill="x", pady=10)

        ctk.CTkLabel(mode_card, text="Choose Practice Mode",
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=24, pady=(20, 12))

        self.quiz_mode_var = ctk.StringVar(value="quick")

        modes = [
            ("quick",     "⚡  Quick Quiz (5 Questions)",
             "Fast review across all enabled question types."),
            ("deep",      "📚  Deep Practice (10 Questions)",
             "Comprehensive coverage — more formats, deeper concepts."),
            ("unlimited", "♾️  Unlimited Practice",
             "Continuous session. New questions pre-fetched as you go!"),
        ]

        for val, title, desc in modes:
            frame = ctk.CTkFrame(mode_card, fg_color="transparent")
            frame.pack(fill="x", padx=24, pady=8)
            rb = ctk.CTkRadioButton(frame, text=title,
                                    font=ctk.CTkFont("Arial", 13, "bold"),
                                    variable=self.quiz_mode_var, value=val,
                                    fg_color=ACCENT, border_color=BORDER)
            rb.pack(anchor="w")
            ctk.CTkLabel(frame, text=desc,
                         font=ctk.CTkFont("Arial", 11),
                         text_color=TEXT_MUTED).pack(anchor="w", padx=28, pady=(2, 0))

        # ── Type preview chips ───────────────────────────────────────────
        chips_frame = ctk.CTkFrame(inner, fg_color="transparent")
        chips_frame.pack(anchor="w", pady=(4, 0))
        for label in ["🔘 MCQ", "☑️ Multi-Select", "🔀 Match", "✍️ Short Answer"]:
            ctk.CTkLabel(chips_frame, text=label,
                         font=ctk.CTkFont("Arial", 10, "bold"),
                         fg_color=CARD, text_color=ACCENT,
                         corner_radius=10, padx=8, pady=4).pack(side="left", padx=4)

        # ── Start button ─────────────────────────────────────────────────
        start_btn = ctk.CTkButton(inner, text="Start Practice Session  ▶",
                                  font=ctk.CTkFont("Arial", 15, "bold"),
                                  fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                  text_color="white", corner_radius=22,
                                  height=50, width=260,
                                  command=lambda: self._start_quiz_session(kit))
        start_btn.pack(pady=28)

    def _start_quiz_session(self, kit):
        md = combined_markdown(kit)
        if not md.strip():
            messagebox.showerror("No content", "Could not read any file content from this kit.")
            return

        mode = self.quiz_mode_var.get()
        difficulty = getattr(self, "_ss_difficulty", None)
        difficulty = round(difficulty.get()) if difficulty else 3
        focus = ""
        if hasattr(self, "_ss_focus_entry"):
            try:
                focus = self._ss_focus_entry.get().strip()
            except Exception:
                pass
        enabled_types = [tid for tid, var in getattr(self, "_ss_type_vars", {}).items() if var.get()]
        if not enabled_types:
            enabled_types = ["mcq", "multiselect", "match", "shortanswer"]

        # Store session settings for pre-fetch
        self._ss_md = md
        self._ss_difficulty_val = difficulty
        self._ss_focus_val = focus
        self._ss_enabled_types = enabled_types

        # Clear view & show loading
        for w in self.quiz_scroll.winfo_children():
            w.destroy()

        inner = ctk.CTkFrame(self.quiz_scroll, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=40, pady=60)

        ctk.CTkLabel(inner, text="Preparing Practice Questions…",
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TITLE).pack(pady=20)
        ctk.CTkLabel(inner,
                     text=f"Difficulty: {difficulty}/5  ·  Types: {len(enabled_types)} active",
                     font=ctk.CTkFont("Arial", 12),
                     text_color=TEXT_MUTED).pack(pady=(0, 14))

        prog_bar = ctk.CTkProgressBar(inner, mode="indeterminate",
                                      progress_color=ACCENT, fg_color=CARD_LIGHT)
        prog_bar.pack(fill="x", padx=60)
        prog_bar.start()

        count = 5 if mode in ["quick", "unlimited"] else 10

        def task():
            try:
                questions = generate_smart_study_questions(
                    md, count=count,
                    difficulty=difficulty,
                    enabled_types=enabled_types,
                    focus=focus)
                if not questions:
                    raise Exception("No questions could be generated from this material.")

                def done():
                    self.quiz_questions = questions
                    self.quiz_current_idx = 0
                    self.quiz_responses = []
                    self.quiz_mode = mode
                    self.quiz_is_generating_more = False
                    self._load_quiz_question(kit)

                self.after(0, done)
            except Exception as e:
                err = str(e)
                def fail():
                    messagebox.showerror("Generation Error", err)
                    self._render_quiz_options_screen(kit)
                self.after(0, fail)

        threading.Thread(target=task, daemon=True).start()

    # -----------------------------------------------------------------------
    # VIEW: TEST MODE — pick a question count + types, straight quiz
    # -----------------------------------------------------------------------
    def _show_test_mode(self, kit):
        self._clear_main()
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        self.active_kit = kit
        self.active_tab = "test"
        self._update_sidebar(kit)

        # Reuse the same quiz_scroll infrastructure as Smart Study, since
        # question rendering (_render_mcq/_render_match/etc.) is shared.
        self.quiz_scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        self.quiz_scroll.pack(fill="both", expand=True)
        self.quiz_scroll.grid_columnconfigure(0, weight=1)

        self._render_test_options_screen(kit)

    def _render_test_options_screen(self, kit):
        for w in self.quiz_scroll.winfo_children():
            w.destroy()

        inner = ctk.CTkFrame(self.quiz_scroll, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=40, pady=32)

        ctk.CTkLabel(inner, text="🧪  Test",
                     font=ctk.CTkFont("Arial", 26, "bold"),
                     text_color=TITLE).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(inner,
                     text="Pick how many questions you want and which types to include — "
                          "generates a straight test from this kit's material.",
                     font=ctk.CTkFont("Arial", 13),
                     text_color=TEXT_MUTED, wraplength=640, justify="left").pack(anchor="w", pady=(0, 20))

        card = ctk.CTkFrame(inner, fg_color=CARD, corner_radius=20)
        card.pack(fill="x", pady=10)

        # ── Question count ───────────────────────────────────────────────
        ctk.CTkLabel(card, text="Number of Questions",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=24, pady=(20, 6))

        count_row = ctk.CTkFrame(card, fg_color="transparent")
        count_row.pack(anchor="w", padx=24, pady=(0, 14))

        self._test_count_var = ctk.IntVar(value=10)
        count_lbl = ctk.CTkLabel(count_row, text="10",
                                  font=ctk.CTkFont("Arial", 13, "bold"),
                                  text_color=ACCENT, width=36)

        def _on_count(val):
            v = round(float(val))
            self._test_count_var.set(v)
            count_lbl.configure(text=str(v))

        count_slider = ctk.CTkSlider(count_row, from_=1, to=30, number_of_steps=29,
                                      command=_on_count,
                                      fg_color=CARD_LIGHT, progress_color=ACCENT,
                                      button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                      width=280)
        count_slider.set(10)
        count_slider.pack(side="left")
        count_lbl.pack(side="left", padx=10)

        # ── Question types ──────────────────────────────────────────────
        ctk.CTkLabel(card, text="Question Types",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=24, pady=(6, 6))

        types_frame = ctk.CTkFrame(card, fg_color="transparent")
        types_frame.pack(anchor="w", padx=24, pady=(0, 20))
        self._test_type_vars = {}
        type_defs = [
            ("mcq",         "🔘 Single-Choice MCQ"),
            ("multiselect", "☑️ Multi-Select"),
            ("match",       "🔀 Match the Following"),
            ("shortanswer", "✍️ Short Answer"),
        ]
        for i, (tid, tlabel) in enumerate(type_defs):
            var = ctk.BooleanVar(value=True)
            self._test_type_vars[tid] = var
            ctk.CTkCheckBox(types_frame, text=tlabel,
                            variable=var,
                            font=ctk.CTkFont("Arial", 12),
                            fg_color=ACCENT, border_color=BORDER,
                            hover_color=ACCENT_HOVER,
                            text_color=TEXT).grid(row=i // 2, column=i % 2,
                                                   padx=(0, 24), pady=4, sticky="w")

        start_btn = ctk.CTkButton(inner, text="Start Test  ▶",
                                   font=ctk.CTkFont("Arial", 15, "bold"),
                                   fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                   text_color="white", corner_radius=22,
                                   height=50, width=220,
                                   command=lambda: self._start_test_session(kit))
        start_btn.pack(pady=28)

    def _start_test_session(self, kit):
        md = combined_markdown(kit)
        if not md.strip():
            messagebox.showerror("No content", "Could not read any file content from this kit.")
            return

        count = self._test_count_var.get()
        enabled_types = [tid for tid, var in self._test_type_vars.items() if var.get()]
        if not enabled_types:
            messagebox.showwarning("No types selected", "Pick at least one question type.")
            return

        for w in self.quiz_scroll.winfo_children():
            w.destroy()

        inner = ctk.CTkFrame(self.quiz_scroll, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=40, pady=60)

        ctk.CTkLabel(inner, text="Preparing Your Test…",
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TITLE).pack(pady=20)
        ctk.CTkLabel(inner,
                     text=f"{count} question(s)  ·  Types: {len(enabled_types)} active",
                     font=ctk.CTkFont("Arial", 12),
                     text_color=TEXT_MUTED).pack(pady=(0, 14))

        prog_bar = ctk.CTkProgressBar(inner, mode="indeterminate",
                                       progress_color=ACCENT, fg_color=CARD_LIGHT)
        prog_bar.pack(fill="x", padx=60)
        prog_bar.start()

        def task():
            try:
                questions = generate_smart_study_questions(
                    md, count=count,
                    difficulty=3,
                    enabled_types=enabled_types,
                    focus="")
                if not questions:
                    raise Exception("No questions could be generated from this material.")

                def done():
                    self.quiz_questions = questions
                    self.quiz_current_idx = 0
                    self.quiz_responses = []
                    self.quiz_mode = "test"
                    self.quiz_is_generating_more = False
                    self._load_quiz_question(kit)

                self.after(0, done)
            except Exception as e:
                err = str(e)
                def fail():
                    messagebox.showerror("Generation Error", err)
                    self._render_test_options_screen(kit)
                self.after(0, fail)

        threading.Thread(target=task, daemon=True).start()

    # ── shared quiz helpers ──────────────────────────────────────────────
    def _quiz_build_header(self, kit):
        """Render the progress header strip and return (header_widget, q_object)."""
        kit_color = kit.get("color", ACCENT)
        q = self.quiz_questions[self.quiz_current_idx]
        self.quiz_is_revealed = False

        header = ctk.CTkFrame(self.quiz_scroll, fg_color=CARD, corner_radius=14)
        header.pack(fill="x", padx=24, pady=(20, 10))

        hi = ctk.CTkFrame(header, fg_color="transparent")
        hi.pack(fill="x", padx=20, pady=(10, 4))

        mode_label = {"quick": "Quick Quiz", "deep": "Deep Practice",
                      "unlimited": "Unlimited", "test": "Test"}.get(self.quiz_mode, "Practice")

        # Question type chip
        type_icons = {"mcq": "🔘", "multiselect": "☑️", "match": "🔀", "shortanswer": "✍️"}
        qtype = q.get("type", "mcq")
        chip = ctk.CTkFrame(hi, fg_color=CARD_LIGHT, corner_radius=8)
        chip.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(chip, text=f"  {type_icons.get(qtype, '🔘')} {qtype.upper()}  ",
                     font=ctk.CTkFont("Arial", 9, "bold"),
                     text_color=ACCENT).pack(padx=2, pady=2)

        ctk.CTkLabel(hi, text=f"{kit['name']} — {mode_label}",
                     font=ctk.CTkFont("Arial", 14, "bold"),
                     text_color=TITLE).pack(side="left")

        total_qs = len(self.quiz_questions)
        if self.quiz_mode == "unlimited":
            attempts = len(self.quiz_responses)
            correct = sum(1 for r in self.quiz_responses 
            if r["correct"])
            accuracy = (correct / attempts * 100) if attempts > 0 else 0.0
            
            TARGET_QUESTIONS = 20
            TARGET_ACCURACY = 80.0
            
            q_progress = min((attempts / TARGET_QUESTIONS) * 100.0, 100.0)
            a_progress = min((accuracy / TARGET_ACCURACY) * 100.0, 100.0)
            prog_percent = int(0.5 * q_progress + 0.5 * a_progress)
            
            prog_text = f"Mastery: {prog_percent}%  (Q {self.quiz_current_idx + 1})"
            prog_val  = prog_percent / 100.0
        else:
            prog_text = f"Q {self.quiz_current_idx + 1} / {total_qs}"
            prog_val  = self.quiz_current_idx / total_qs

        ctk.CTkLabel(hi, text=prog_text,
                     font=ctk.CTkFont("Arial", 12), text_color=TEXT_MUTED).pack(side="right")

        self.quiz_prog_bar = ctk.CTkProgressBar(
            header, height=6, corner_radius=3,
            progress_color=kit_color, fg_color=CARD_LIGHT)
        self.quiz_prog_bar.set(prog_val)
        self.quiz_prog_bar.pack(fill="x", padx=20, pady=(4, 12))
        return q

    def _quiz_prefetch_if_needed(self, kit):
        """Trigger background pre-fetch for unlimited mode."""
        if self.quiz_mode != "unlimited" or self.quiz_is_generating_more:
            return
        if self.quiz_current_idx < len(self.quiz_questions) - 2:
            return
        self.quiz_is_generating_more = True
        md = getattr(self, "_ss_md", combined_markdown(kit))
        diff = getattr(self, "_ss_difficulty_val", 3)
        focus = getattr(self, "_ss_focus_val", "")
        etypes = getattr(self, "_ss_enabled_types", None)
        def prefetch_task():
            try:
                history = [q["q"] for q in self.quiz_questions]
                more = generate_smart_study_questions(
                    md, count=5, history_list=history,
                    difficulty=diff, focus=focus, enabled_types=etypes)
                if more:
                    self.after(0, lambda: self.quiz_questions.extend(more))
            except Exception as e:
                print("[prefetch]", e)
            finally:
                self.quiz_is_generating_more = False
        threading.Thread(target=prefetch_task, daemon=True).start()

    def _quiz_next_ctrl(self, kit, ctrl_frame):
        """Add Skip and (hidden) Next buttons to ctrl_frame."""
        self.quiz_next_btn = ctk.CTkButton(
            ctrl_frame, text="Next  →",
            font=ctk.CTkFont("Arial", 12, "bold"),
            fg_color=GREEN, hover_color="#2e7d32",
            text_color="black", corner_radius=18,
            height=36, width=110,
            command=lambda: self._next_mcq_question(kit))
        self.quiz_skip_btn = ctk.CTkButton(
            ctrl_frame, text="Skip",
            font=ctk.CTkFont("Arial", 12),
            fg_color=CARD_LIGHT, hover_color=BORDER,
            text_color=TEXT_MUTED, corner_radius=18,
            height=36, width=80)
        self.quiz_skip_btn.pack(side="left", padx=(0, 8))
        return self.quiz_skip_btn

    def _quiz_show_explanation(self, q, is_correct=True, extra_text=None):
        """Show the explanation card below the question card."""
        exp_frame = ctk.CTkFrame(self.quiz_scroll, fg_color=CARD, corner_radius=20)
        exp_frame.pack(fill="x", padx=24, pady=(10, 24))
        result_color = GREEN if is_correct else RED
        result_text  = "✓  Correct!" if is_correct else "✗  Incorrect"
        ctk.CTkLabel(exp_frame, text=result_text,
                     font=ctk.CTkFont("Arial", 14, "bold"),
                     text_color=result_color).pack(anchor="w", padx=22, pady=(16, 4))
        if extra_text:
            ctk.CTkLabel(exp_frame, text=extra_text,
                         font=ctk.CTkFont("Arial", 12),
                         text_color=TEXT_MUTED, justify="left", wraplength=640).pack(
                             anchor="w", padx=22, pady=(0, 8))
        bullets = q.get("e", [])
        if bullets:
            ctk.CTkLabel(exp_frame, text="Concept Explanation",
                         font=ctk.CTkFont("Arial", 12, "bold"),
                         text_color=TITLE).pack(anchor="w", padx=22, pady=(4, 2))
            exp_text = "\n".join(f"  • {b}" for b in bullets)
            ctk.CTkLabel(exp_frame, text=exp_text,
                         font=ctk.CTkFont("Arial", 12),
                         text_color=TEXT, justify="left", wraplength=640).pack(
                             anchor="w", padx=22, pady=(0, 18))

    # ── dispatcher ───────────────────────────────────────────────────────
    def _load_quiz_question(self, kit):
        for w in self.quiz_scroll.winfo_children():
            w.destroy()
        self._quiz_prefetch_if_needed(kit)
        q = self.quiz_questions[self.quiz_current_idx]
        qtype = q.get("type", "mcq")
        if qtype == "multiselect":
            self._render_multiselect(kit, q)
        elif qtype == "match":
            self._render_match(kit, q)
        elif qtype == "shortanswer":
            self._render_shortanswer(kit, q)
        else:
            self._render_mcq(kit, q)

    # ── MCQ renderer ─────────────────────────────────────────────────────
    def _render_mcq(self, kit, q):
        self._quiz_build_header(kit)
        card = ctk.CTkFrame(self.quiz_scroll, fg_color=CARD, corner_radius=20)
        card.pack(fill="x", padx=24, pady=10)

        ctk.CTkLabel(card, text=q["q"],
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TEXT, wraplength=660, justify="left").pack(
                         anchor="w", padx=24, pady=(24, 16))

        opts_frame = ctk.CTkFrame(card, fg_color="transparent")
        opts_frame.pack(fill="x", padx=20, pady=(0, 12))
        opts_frame.grid_columnconfigure((0, 1), weight=1)

        self.quiz_opt_btns = []
        for idx, opt_text in enumerate(q["o"][:4]):
            b = ctk.CTkButton(
                opts_frame, text=opt_text,
                font=ctk.CTkFont("Arial", 12),
                fg_color=CARD_LIGHT, hover_color=BORDER,
                text_color=TEXT, corner_radius=14, height=72, anchor="w",
                command=lambda i=idx: self._reveal_mcq(kit, q, i))
            b.grid(row=idx // 2, column=idx % 2, padx=8, pady=5, sticky="nsew")
            self.quiz_opt_btns.append(b)

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkButton(ctrl, text="Show Answer",
                      font=ctk.CTkFont("Arial", 12, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="white", corner_radius=18,
                      height=36, width=140,
                      command=lambda: self._reveal_mcq(kit, q, None)).pack(side="left", padx=(0, 8))
        self._quiz_next_ctrl(kit, ctrl)

    def _reveal_mcq(self, kit, q, selected_idx):
        if self.quiz_is_revealed:
            return
        self.quiz_is_revealed = True
        correct_text = q["a"].strip().lower()
        correct_idx = 0
        for i, opt in enumerate(q["o"]):
            if opt.strip().lower() == correct_text:
                correct_idx = i; break
        else:
            for i, opt in enumerate(q["o"]):
                if opt.strip().lower() in correct_text or correct_text in opt.strip().lower():
                    correct_idx = i; break
        is_correct = selected_idx is not None and selected_idx == correct_idx
        for i, btn in enumerate(self.quiz_opt_btns):
            if i == correct_idx:
                btn.configure(fg_color="#1b5e20", hover_color="#1b5e20", text_color=TEXT)
            elif selected_idx is not None and i == selected_idx:
                btn.configure(fg_color="#7f1515", hover_color="#7f1515", text_color=TEXT)
            else:
                btn.configure(fg_color=CARD_LIGHT, hover_color=CARD_LIGHT, text_color=TEXT_MUTED)
            btn.configure(state="disabled")
        self._record_response(q, is_correct)
        self.quiz_skip_btn.pack_forget()
        self.quiz_next_btn.pack(side="left", padx=(0, 8))
        self._quiz_show_explanation(q, is_correct)

    # ── Multi-Select renderer ─────────────────────────────────────────────
    def _render_multiselect(self, kit, q):
        self._quiz_build_header(kit)
        card = ctk.CTkFrame(self.quiz_scroll, fg_color=CARD, corner_radius=20)
        card.pack(fill="x", padx=24, pady=10)

        ctk.CTkLabel(card, text=q["q"],
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TEXT, wraplength=660, justify="left").pack(
                          anchor="w", padx=24, pady=(24, 4))
        ctk.CTkLabel(card, text="Select all options that apply:",
                     font=ctk.CTkFont("Arial", 12, slant="italic"),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=24, pady=(0, 14))

        check_vars = []
        for opt in q["o"]:
            var = ctk.BooleanVar(value=False)
            check_vars.append((opt, var))
            ctk.CTkCheckBox(card, text=opt, variable=var,
                            font=ctk.CTkFont("Arial", 12),
                            fg_color=ACCENT, border_color=BORDER,
                            hover_color=ACCENT_HOVER,
                            text_color=TEXT).pack(anchor="w", padx=24, pady=5)

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=(12, 14))

        def submit_multiselect():
            if self.quiz_is_revealed:
                return
            self.quiz_is_revealed = True
            selected = {opt for opt, var in check_vars if var.get()}
            correct_set = set(q["a"])
            is_correct = selected == correct_set
            # Show correct answers
            correct_str = "  ✓ Correct: " + ", ".join(q["a"])
            self._record_response(q, is_correct)
            submit_btn.configure(state="disabled")
            self.quiz_skip_btn.pack_forget()
            self.quiz_next_btn.pack(side="left", padx=(0, 8))
            self._quiz_show_explanation(q, is_correct,
                                        extra_text=correct_str if not is_correct else None)

        submit_btn = ctk.CTkButton(
            ctrl, text="Submit Answer",
            font=ctk.CTkFont("Arial", 12, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", corner_radius=18,
            height=36, width=150,
            command=submit_multiselect)
        submit_btn.pack(side="left", padx=(0, 8))
        self._quiz_next_ctrl(kit, ctrl)

    # ── Match-the-Following renderer ──────────────────────────────────────
    def _render_match(self, kit, q):
        self._quiz_build_header(kit)
        card = ctk.CTkFrame(self.quiz_scroll, fg_color=CARD, corner_radius=20)
        card.pack(fill="x", padx=24, pady=10)

        ctk.CTkLabel(card, text=q["q"],
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TEXT, wraplength=660, justify="left").pack(
                          anchor="w", padx=24, pady=(24, 14))

        items_l = q["l"]    # left items (fixed)
        correct_r = q["r"]  # correct matches indexed with l
        options_r = q["rs"] # shuffled right options

        selected_left = None
        selected_right = None
        pairs = {}  # left_idx -> right_idx

        PAIR_COLORS = ["#007aff", "#ff9500", "#af52de", "#5856d6"]

        grid_frame = ctk.CTkFrame(card, fg_color="transparent")
        grid_frame.pack(fill="x", padx=24, pady=(0, 12))
        grid_frame.grid_columnconfigure(0, weight=1)
        grid_frame.grid_columnconfigure(1, weight=0, minsize=180)
        grid_frame.grid_columnconfigure(2, weight=1)

        # Header row
        ctk.CTkLabel(grid_frame, text="Terms",
                     font=ctk.CTkFont("Arial", 11, "bold"),
                     text_color=TEXT_MUTED).grid(row=0, column=0, sticky="w", padx=8, pady=(0, 6))
        ctk.CTkLabel(grid_frame, text="Definitions",
                     font=ctk.CTkFont("Arial", 11, "bold"),
                     text_color=TEXT_MUTED).grid(row=0, column=2, sticky="w", padx=8, pady=(0, 6))

        # Canvas for curves
        canvas = Canvas(grid_frame, bg=CARD, highlightthickness=0, width=180)
        canvas.grid(row=1, column=1, rowspan=max(len(items_l), len(options_r)), sticky="nsew", padx=10)

        def draw_bezier(c, x1, y1, x2, y2, color, width=3, dash=None):
            dx = x2 - x1
            points = []
            steps = 24
            for step in range(steps + 1):
                t = step / steps
                cx = (1 - t)**3 * x1 + 3 * (1 - t)**2 * t * (x1 + dx/2) + 3 * (1 - t) * t**2 * (x1 + dx/2) + t**3 * x2
                cy = (1 - t)**3 * y1 + 3 * (1 - t)**2 * t * y1 + 3 * (1 - t) * t**2 * y2 + t**3 * y2
                points.append(cx)
                points.append(cy)
            c.create_line(*points, fill=color, width=width, smooth=True, dash=dash)

        def make_card(parent, text, side, idx, click_cmd):
            card_f = ctk.CTkFrame(
                parent, fg_color=CARD_LIGHT, border_width=1, border_color=BORDER,
                corner_radius=12, cursor="hand2"
            )
            lbl = ctk.CTkLabel(
                card_f, text=text, justify="left", wraplength=280,
                font=ctk.CTkFont("Arial", 12, "bold"), text_color=TEXT
            )
            lbl.pack(fill="both", expand=True, padx=16, pady=12)
            
            card_f.bind("<Button-1>", lambda e: click_cmd())
            lbl.bind("<Button-1>", lambda e: click_cmd())
            
            def enter(e):
                if self.quiz_is_revealed:
                    return
                # Glow border on hover if not selected/paired
                is_sel = (side == "l" and selected_left == idx) or (side == "r" and selected_right == idx)
                is_paired = (side == "l" and idx in pairs) or (side == "r" and any(v == idx for v in pairs.values()))
                if not is_sel and not is_paired:
                    card_f.configure(border_color=ACCENT)
                    
            def leave(e):
                if self.quiz_is_revealed:
                    return
                update_ui()
                
            card_f.bind("<Enter>", enter)
            card_f.bind("<Leave>", leave)
            lbl.bind("<Enter>", enter)
            lbl.bind("<Leave>", leave)
            
            return card_f

        def on_left_click(idx):
            nonlocal selected_left, selected_right
            if self.quiz_is_revealed:
                return
            if selected_left == idx:
                selected_left = None
            else:
                selected_left = idx
            
            if selected_left is not None and selected_right is not None:
                # 1-to-1 matching setup: clear previous pairings of these indices
                pairs.pop(selected_left, None)
                for l_idx, r_idx in list(pairs.items()):
                    if r_idx == selected_right:
                        pairs.pop(l_idx)
                pairs[selected_left] = selected_right
                selected_left = None
                selected_right = None
            update_ui()

        def on_right_click(idx):
            nonlocal selected_left, selected_right
            if self.quiz_is_revealed:
                return
            if selected_right == idx:
                selected_right = None
            else:
                selected_right = idx
            
            if selected_left is not None and selected_right is not None:
                pairs.pop(selected_left, None)
                for l_idx, r_idx in list(pairs.items()):
                    if r_idx == selected_right:
                        pairs.pop(l_idx)
                pairs[selected_left] = selected_right
                selected_left = None
                selected_right = None
            update_ui()

        def reset_match():
            nonlocal selected_left, selected_right
            if self.quiz_is_revealed:
                return
            selected_left = None
            selected_right = None
            pairs.clear()
            update_ui()

        def update_ui():
            grid_frame.update_idletasks()
            canvas.delete("all")
            canvas_w = canvas.winfo_width()
            if canvas_w <= 1:
                canvas_w = 180
            canvas_y = canvas.winfo_y()

            # Style left items
            for i, btn in enumerate(left_btns):
                if self.quiz_is_revealed:
                    correct_r_val = correct_r[i]
                    paired_r_idx = pairs.get(i)
                    if paired_r_idx is not None:
                        chosen = options_r[paired_r_idx]
                        ok = chosen.strip().lower() == correct_r_val.strip().lower()
                        border_color = GREEN if ok else RED
                        fg_color = "#1b5e20" if ok else "#7f1515"
                    else:
                        border_color = RED
                        fg_color = CARD_LIGHT
                    btn.configure(border_color=border_color, border_width=2, fg_color=fg_color)
                else:
                    if i == selected_left:
                        btn.configure(border_color=ACCENT, border_width=2, fg_color=CARD_LIGHT)
                    elif i in pairs:
                        color = PAIR_COLORS[i % len(PAIR_COLORS)]
                        btn.configure(border_color=color, border_width=2, fg_color=CARD_LIGHT)
                    else:
                        btn.configure(border_color=BORDER, border_width=1, fg_color=CARD_LIGHT)

            # Style right items
            for j, btn in enumerate(right_btns):
                if self.quiz_is_revealed:
                    paired_l_idx = next((k for k, v in pairs.items() if v == j), None)
                    if paired_l_idx is not None:
                        correct_r_val = correct_r[paired_l_idx]
                        chosen = options_r[j]
                        ok = chosen.strip().lower() == correct_r_val.strip().lower()
                        border_color = GREEN if ok else RED
                        fg_color = "#1b5e20" if ok else "#7f1515"
                    else:
                        border_color = BORDER
                        fg_color = CARD_LIGHT
                else:
                    is_paired = any(v == j for v in pairs.values())
                    if j == selected_right:
                        btn.configure(border_color=ACCENT, border_width=2, fg_color=CARD_LIGHT)
                    elif is_paired:
                        l_idx = next(k for k, v in pairs.items() if v == j)
                        color = PAIR_COLORS[l_idx % len(PAIR_COLORS)]
                        btn.configure(border_color=color, border_width=2, fg_color=CARD_LIGHT)
                    else:
                        btn.configure(border_color=BORDER, border_width=1, fg_color=CARD_LIGHT)

            # Draw left dots
            for i, btn in enumerate(left_btns):
                y = btn.winfo_y() + btn.winfo_height() // 2 - canvas_y
                color = TEXT_MUTED
                if not self.quiz_is_revealed:
                    if i == selected_left:
                        color = ACCENT
                    elif i in pairs:
                        color = PAIR_COLORS[i % len(PAIR_COLORS)]
                else:
                    paired_r_idx = pairs.get(i)
                    if paired_r_idx is not None:
                        chosen = options_r[paired_r_idx]
                        ok = chosen.strip().lower() == correct_r[i].strip().lower()
                        color = GREEN if ok else RED
                    else:
                        color = RED
                canvas.create_oval(4, y - 5, 14, y + 5, fill=color, outline="", tags="dots")

            # Draw right dots
            for j, btn in enumerate(right_btns):
                y = btn.winfo_y() + btn.winfo_height() // 2 - canvas_y
                color = TEXT_MUTED
                is_paired = any(v == j for v in pairs.values())
                if not self.quiz_is_revealed:
                    if j == selected_right:
                        color = ACCENT
                    elif is_paired:
                        l_idx = next(k for k, v in pairs.items() if v == j)
                        color = PAIR_COLORS[l_idx % len(PAIR_COLORS)]
                else:
                    paired_l_idx = next((k for k, v in pairs.items() if v == j), None)
                    if paired_l_idx is not None:
                        chosen = options_r[j]
                        ok = chosen.strip().lower() == correct_r[paired_l_idx].strip().lower()
                        color = GREEN if ok else RED
                canvas.create_oval(canvas_w - 14, y - 5, canvas_w - 4, y + 5, fill=color, outline="", tags="dots")

            # Draw curved bezier connection lines
            for i, j in pairs.items():
                btn_l = left_btns[i]
                btn_r = right_btns[j]
                y_l = btn_l.winfo_y() + btn_l.winfo_height() // 2 - canvas_y
                y_r = btn_r.winfo_y() + btn_r.winfo_height() // 2 - canvas_y
                
                line_color = PAIR_COLORS[i % len(PAIR_COLORS)]
                if self.quiz_is_revealed:
                    chosen = options_r[j]
                    ok = chosen.strip().lower() == correct_r[i].strip().lower()
                    line_color = GREEN if ok else RED
                    
                draw_bezier(canvas, 14, y_l, canvas_w - 14, y_r, color=line_color, width=3)

            # Draw correct paths if incorrect/missing during reveal
            if self.quiz_is_revealed:
                for i in range(len(items_l)):
                    correct_r_val = correct_r[i]
                    correct_j = None
                    for j, opt in enumerate(options_r):
                        if opt.strip().lower() == correct_r_val.strip().lower():
                            correct_j = j
                            break
                    
                    paired_j = pairs.get(i)
                    if paired_j != correct_j and correct_j is not None:
                        btn_l = left_btns[i]
                        btn_r = right_btns[correct_j]
                        y_l = btn_l.winfo_y() + btn_l.winfo_height() // 2 - canvas_y
                        y_r = btn_r.winfo_y() + btn_r.winfo_height() // 2 - canvas_y
                        draw_bezier(canvas, 14, y_l, canvas_w - 14, y_r, color="#1b5e20", width=1.5, dash=(4, 4))

        left_btns = []
        for i, left_item in enumerate(items_l):
            btn = make_card(grid_frame, left_item, "l", i, lambda idx=i: on_left_click(idx))
            btn.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=6)
            left_btns.append(btn)

        right_btns = []
        for j, right_item in enumerate(options_r):
            btn = make_card(grid_frame, right_item, "r", j, lambda idx=j: on_right_click(idx))
            btn.grid(row=j + 1, column=2, sticky="ew", padx=8, pady=6)
            right_btns.append(btn)

        # Re-draw lines when window is resized / configure changes
        canvas.bind("<Configure>", lambda e: update_ui())

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=(6, 14))

        def submit_match():
            if self.quiz_is_revealed:
                return
            if len(pairs) < len(items_l):
                messagebox.showwarning("Incomplete", "Please connect all matches before checking!")
                return
            self.quiz_is_revealed = True
            
            correct_count = 0
            result_lines = []
            for i in range(len(items_l)):
                left = items_l[i]
                expected = correct_r[i]
                paired_r_idx = pairs.get(i)
                if paired_r_idx is not None:
                    chosen = options_r[paired_r_idx]
                    ok = chosen.strip().lower() == expected.strip().lower()
                    if ok:
                        correct_count += 1
                    icon = "✓" if ok else "✗"
                    result_lines.append(f"{icon} {left} → {expected}")
                else:
                    result_lines.append(f"✗ {left} → [Not matched]")
            
            is_correct = correct_count == len(items_l)
            result_summary = "\n".join(result_lines)
            
            self._record_response(q, correct_count > len(items_l) // 2)
            submit_btn.configure(state="disabled")
            reset_btn.configure(state="disabled")
            
            update_ui()
            
            self.quiz_skip_btn.pack_forget()
            self.quiz_next_btn.pack(side="left", padx=(0, 8))
            self._quiz_show_explanation(
                q, is_correct,
                extra_text=f"Correct matches:\n{result_summary}")

        reset_btn = ctk.CTkButton(
            ctrl, text="↺  Reset",
            font=ctk.CTkFont("Arial", 12, "bold"),
            fg_color=CARD_LIGHT, hover_color=BORDER,
            text_color=TEXT_MUTED, corner_radius=18,
            height=36, width=110,
            command=reset_match)
        reset_btn.pack(side="left", padx=(0, 8))

        submit_btn = ctk.CTkButton(
            ctrl, text="Check Answers",
            font=ctk.CTkFont("Arial", 12, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", corner_radius=18,
            height=36, width=150,
            command=submit_match)
        submit_btn.pack(side="left", padx=(0, 8))

        self._quiz_next_ctrl(kit, ctrl)

    # ── Short Answer renderer ─────────────────────────────────────────────
    def _render_shortanswer(self, kit, q):
        self._quiz_build_header(kit)
        card = ctk.CTkFrame(self.quiz_scroll, fg_color=CARD, corner_radius=20)
        card.pack(fill="x", padx=24, pady=10)

        ctk.CTkLabel(card, text=q["q"],
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TEXT, wraplength=660, justify="left").pack(
                         anchor="w", padx=24, pady=(24, 14))
        ctk.CTkLabel(card, text="Type your answer below:",
                     font=ctk.CTkFont("Arial", 12, slant="italic"),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=24, pady=(0, 6))

        answer_box = ctk.CTkTextbox(
            card,
            font=ctk.CTkFont("Arial", 12),
            fg_color=CARD_LIGHT, border_color=BORDER,
            text_color=TEXT, height=120, corner_radius=12)
        answer_box.pack(fill="x", padx=24, pady=(0, 12))

        feedback_lbl = ctk.CTkLabel(card, text="",
                                    font=ctk.CTkFont("Arial", 12),
                                    text_color=TEXT_MUTED, wraplength=640, justify="left")
        feedback_lbl.pack(anchor="w", padx=24)

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=(8, 14))

        def submit_answer():
            if self.quiz_is_revealed:
                return
            student_ans = answer_box.get("1.0", "end").strip()
            if not student_ans:
                feedback_lbl.configure(text="Please write an answer first.", text_color=RED)
                return
            submit_btn.configure(state="disabled", text="Grading…")
            answer_box.configure(state="disabled")
            feedback_lbl.configure(text="AI Tutor is evaluating your answer…", text_color=TEXT_MUTED)

            def grade_task():
                try:
                    result = grade_short_answer(q["q"], q["a"], student_ans)
                    score   = result.get("score", 0)
                    fb_text = result.get("feedback", "")
                    is_correct = score >= 2
                    partial    = score == 1

                    def done():
                        self.quiz_is_revealed = True
                        if is_correct:
                            color, icon = GREEN, "✓  Excellent!"
                        elif partial:
                            color, icon = GOLD, "~ Partially Correct"
                        else:
                            color, icon = RED, "✗  Needs Work"
                        feedback_lbl.configure(
                            text=f"{icon}\n{fb_text}\n\nModel answer: {q['a']}",
                            text_color=color)
                        self._record_response(q, is_correct or partial)
                        self.quiz_skip_btn.pack_forget()
                        self.quiz_next_btn.pack(side="left", padx=(0, 8))
                        self._quiz_show_explanation(q, is_correct)
                    self.after(0, done)
                except Exception as e:
                    def fail():
                        self.quiz_is_revealed = True
                        feedback_lbl.configure(
                            text=f"Could not grade: {e}\n\nModel answer: {q['a']}",
                            text_color=RED)
                        self._record_response(q, False)
                        self.quiz_skip_btn.pack_forget()
                        self.quiz_next_btn.pack(side="left", padx=(0, 8))
                    self.after(0, fail)

            threading.Thread(target=grade_task, daemon=True).start()

        submit_btn = ctk.CTkButton(
            ctrl, text="Submit Answer",
            font=ctk.CTkFont("Arial", 12, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", corner_radius=18,
            height=36, width=150,
            command=submit_answer)
        submit_btn.pack(side="left", padx=(0, 8))
        self._quiz_next_ctrl(kit, ctrl)

    # ── common helpers ────────────────────────────────────────────────────
    def _select_mcq_option(self, kit, selected_idx):
        """Legacy shim — no longer used directly."""
        pass

    def _record_response(self, q, is_correct):
        if len(self.quiz_responses) == self.quiz_current_idx:
            self.quiz_responses.append({
                "question": q["q"],
                "correct":  is_correct,
                "correct_answer": q.get("a") if isinstance(q.get("a"), str)
                                  else " | ".join(q.get("a", [])),
            })

    def _next_mcq_question(self, kit):
        next_idx = self.quiz_current_idx + 1

        if self.quiz_mode == "unlimited":
            attempts = len(self.quiz_responses)
            correct = sum(1 for r in self.quiz_responses if r["correct"])
            accuracy = (correct / attempts * 100) if attempts > 0 else 0.0
            
            TARGET_QUESTIONS = 20
            TARGET_ACCURACY = 80.0
            
            q_progress = min((attempts / TARGET_QUESTIONS) * 100.0, 100.0)
            a_progress = min((accuracy / TARGET_ACCURACY) * 100.0, 100.0)
            prog_percent = int(0.5 * q_progress + 0.5 * a_progress)
            if prog_percent >= 100:
                self._show_quiz_summary(kit)
                return

        if next_idx >= len(self.quiz_questions):
            # Check if Unlimited and background thread is pre-fetching
            if self.quiz_mode == "unlimited":
                if self.quiz_is_generating_more:
                    # Show temporary loading
                    for w in self.quiz_scroll.winfo_children():
                        if w != self.quiz_prog_bar.master: # keep header
                            w.destroy()
                            
                    loading_inner = ctk.CTkFrame(self.quiz_scroll, fg_color="transparent")
                    loading_inner.pack(fill="both", expand=True, padx=40, pady=80)
                    
                    ctk.CTkLabel(loading_inner, text="Pre-fetching next questions…",
                                 font=ctk.CTkFont("Arial", 14, "bold"), text_color=TEXT_MUTED).pack(pady=10)
                    
                    prog = ctk.CTkProgressBar(loading_inner, mode="indeterminate", progress_color=ACCENT)
                    prog.pack(fill="x", padx=40)
                    prog.start()
                    
                    def wait_and_load():
                        if self.quiz_is_generating_more:
                            self.after(500, wait_and_load)
                        else:
                            if next_idx < len(self.quiz_questions):
                                self.quiz_current_idx = next_idx
                                self._load_quiz_question(kit)
                            else:
                                self._show_quiz_summary(kit)
                    
                    self.after(200, wait_and_load)
                    return
                else:
                    # Pre-fetching completed but no questions added (maybe API error or end of source)
                    self._show_quiz_summary(kit)
            else:
                # Quick/Deep/Test quiz complete
                self._show_quiz_summary(kit)
        else:
            self.quiz_current_idx = next_idx
            self._load_quiz_question(kit)

    def _show_quiz_summary(self, kit):
        for w in self.quiz_scroll.winfo_children():
            w.destroy()

        correct = sum(1 for r in self.quiz_responses if r["correct"])
        total = len(self.quiz_responses)
        accuracy = int((correct / total) * 100) if total else 0
        kit_color = kit.get("color", ACCENT)

        inner = ctk.CTkFrame(self.quiz_scroll, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=40, pady=20)

        title_text = "Practice Session Complete"
        if self.quiz_mode == "test":
            title_text = "Test Complete"
        if self.quiz_mode == "unlimited":
            attempts = len(self.quiz_responses)
            correct = sum(1 for r in self.quiz_responses if r["correct"])
            accuracy = (correct / attempts * 100) if attempts > 0 else 0.0
            
            TARGET_QUESTIONS = 20
            TARGET_ACCURACY = 80.0
            
            q_progress = min((attempts / TARGET_QUESTIONS) * 100.0, 100.0)
            a_progress = min((accuracy / TARGET_ACCURACY) * 100.0, 100.0)
            prog_percent = int(0.5 * q_progress + 0.5 * a_progress)
            if prog_percent >= 100:
                title_text = "🏆 Mastery Goal Achieved!"

        ctk.CTkLabel(inner, text=title_text,
                     font=ctk.CTkFont("Arial", 22, "bold"),
                     text_color=TITLE).pack(pady=(20, 2))
        ctk.CTkLabel(inner, text=kit["name"],
                     font=ctk.CTkFont("Arial", 12),
                     text_color=TEXT_MUTED).pack(pady=(0, 20))

        # Score Card
        sc = ctk.CTkFrame(inner, fg_color=CARD, corner_radius=22)
        sc.pack(fill="x", pady=10)

        sr = ctk.CTkFrame(sc, fg_color="transparent")
        sr.pack(fill="x", padx=28, pady=(24, 14))

        # Accuracy Badge
        badge = ctk.CTkFrame(sr, fg_color=kit_color, corner_radius=18, width=130, height=90)
        badge.pack(side="left")
        badge.pack_propagate(False)
        tcol = TEXT_ON_YELLOW if kit_color == COLOR_MAP["yellow"] else TEXT
        ctk.CTkLabel(badge, text=f"{accuracy}%",
                     font=ctk.CTkFont("Arial", 30, "bold"),
                     text_color=tcol).place(relx=0.5, rely=0.38, anchor="center")
        ctk.CTkLabel(badge, text="Accuracy",
                     font=ctk.CTkFont("Arial", 10),
                     text_color=tcol).place(relx=0.5, rely=0.72, anchor="center")

        # Stats Table
        stats = ctk.CTkFrame(sr, fg_color=CARD_LIGHT, corner_radius=14)
        stats.pack(side="left", padx=(18, 0), fill="both", expand=True)

        for lbl, val, col in [
            ("Correct Answers", str(correct), GREEN),
            ("Incorrect Answers", str(total - correct), RED),
            ("Total Answered", str(total), TEXT),
        ]:
            rw = ctk.CTkFrame(stats, fg_color="transparent")
            rw.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(rw, text=lbl, font=ctk.CTkFont("Arial", 12),
                         text_color=TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(rw, text=val, font=ctk.CTkFont("Arial", 12, "bold"),
                         text_color=col).pack(side="right")

        # Accuracy Progress Bar
        acc_bar = ctk.CTkProgressBar(sc, progress_color=GREEN if accuracy >= 70 else ACCENT,
                           fg_color=CARD_LIGHT, height=8)
        acc_bar.set(accuracy / 100)
        acc_bar.pack(fill="x", padx=28, pady=(4, 22))

        # Incorrect answers list for review
        wrong = [r for r in self.quiz_responses if not r["correct"]]
        if wrong:
            ctk.CTkLabel(inner, text="Review Incorrect Answers",
                         font=ctk.CTkFont("Arial", 14, "bold"),
                         text_color=TITLE).pack(anchor="w", padx=10, pady=(20, 6))

            for r in wrong:
                rc = ctk.CTkFrame(inner, fg_color=CARD, corner_radius=14)
                rc.pack(fill="x", pady=4)
                
                ctk.CTkLabel(rc, text=r["question"],
                             font=ctk.CTkFont("Arial", 12, "bold"),
                             text_color=TEXT, wraplength=580, justify="left").pack(anchor="w", padx=18, pady=(12, 4))
                ctk.CTkLabel(rc, text=f"Correct Answer: {r['correct_answer']}",
                             font=ctk.CTkFont("Arial", 11),
                             text_color=GREEN, justify="left", wraplength=580).pack(anchor="w", padx=18, pady=(0, 12))

        # Summary buttons
        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.pack(pady=30)

        if self.quiz_mode == "test":
            ctk.CTkButton(btns, text="New Test",
                          font=ctk.CTkFont("Arial", 13, "bold"),
                          fg_color=ACCENT, hover_color=ACCENT_HOVER,
                          text_color="white", corner_radius=20, height=42, width=150,
                          command=lambda: self._show_test_mode(kit)).pack(side="left", padx=8)
        else:
            ctk.CTkButton(btns, text="Practice Again",
                          font=ctk.CTkFont("Arial", 13, "bold"),
                          fg_color=ACCENT, hover_color=ACCENT_HOVER,
                          text_color="white", corner_radius=20, height=42, width=150,
                          command=lambda: self._show_smart_study(kit)).pack(side="left", padx=8)

        ctk.CTkButton(btns, text="Back to Materials",
                      font=ctk.CTkFont("Arial", 13),
                      fg_color=CARD_LIGHT, hover_color=BORDER,
                      text_color=TEXT_MUTED, corner_radius=20, height=42, width=150,
                      command=lambda: self._show_kit_detail(kit)).pack(side="left", padx=8)


    # -----------------------------------------------------------------------
    # VIEW: CTF PRACTICE MODE
    # -----------------------------------------------------------------------
    def _show_ctf_practice(self, kit):
        self._clear_main()
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        self.active_kit = kit
        self.active_tab = "ctf"
        self._update_sidebar(kit)

        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=36, pady=(28, 4))
        ctk.CTkLabel(hdr, text="🏴  CTF Practice Mode",
                     font=ctk.CTkFont("Arial", 24, "bold"),
                     text_color=TITLE).pack(side="left")
        ctk.CTkLabel(hdr, text=kit["name"],
                     font=ctk.CTkFont("Arial", 13),
                     text_color=TEXT_MUTED).pack(side="left", padx=(14, 0), anchor="s", pady=(0, 3))

        # Description
        desc = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=16)
        desc.pack(fill="x", padx=36, pady=(0, 16))
        ctk.CTkLabel(desc, text="Capture The Flag Practice",
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TITLE).pack(anchor="w", padx=20, pady=(16, 6))
        ctk.CTkLabel(desc,
                     text="Upload CTF writeups, challenge descriptions, or solution walkthroughs. "
                          "The AI will generate targeted practice questions, tool reference cards, "
                          "and technique flashcards mapped to MITRE ATT&CK.",
                     font=ctk.CTkFont("Arial", 12),
                     text_color=TEXT_MUTED, wraplength=700, justify="left").pack(anchor="w", padx=20, pady=(0, 16))

        # Upload section
        upload_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=16)
        upload_card.pack(fill="x", padx=36, pady=(0, 16))

        ctk.CTkLabel(upload_card, text="📄  Add CTF Materials",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=20, pady=(16, 8))

        self._ctf_files = []
        self._ctf_files_frame = ctk.CTkFrame(upload_card, fg_color="transparent")
        self._ctf_files_frame.pack(fill="x", padx=20, pady=(0, 16))

        def _ctf_render_files():
            for w in self._ctf_files_frame.winfo_children():
                w.destroy()
            if not self._ctf_files:
                ctk.CTkLabel(self._ctf_files_frame, text="No files added yet",
                             font=ctk.CTkFont("Arial", 12), text_color=TEXT_MUTED).pack(pady=10)
                return
            for idx, (path, _) in enumerate(self._ctf_files):
                row = ctk.CTkFrame(self._ctf_files_frame, fg_color=CARD_LIGHT,
                                   corner_radius=10, height=42)
                row.pack(fill="x", pady=3)
                row.pack_propagate(False)
                ctk.CTkLabel(row, text="📄",
                             font=ctk.CTkFont("Arial", 14)).pack(side="left", padx=(12, 6))
                ctk.CTkLabel(row, text=Path(path).name,
                             font=ctk.CTkFont("Arial", 12), text_color=TEXT,
                             anchor="w").pack(side="left", fill="x", expand=True)
                ctk.CTkButton(row, text="✕",
                              font=ctk.CTkFont("Arial", 12, "bold"),
                              fg_color="transparent", hover_color=CARD,
                              text_color=TEXT_MUTED, width=32, height=32, corner_radius=8,
                              command=lambda i=idx: self._ctf_remove_file(i)).pack(side="right", padx=6)

        def _ctf_remove_file(idx):
            self._ctf_files.pop(idx)
            _ctf_render_files()

        def _ctf_browse():
            if not _markitdown_available:
                messagebox.showerror("MarkItDown missing", "Run: pip install markitdown")
                return
            paths = filedialog.askopenfilenames(title="Select CTF writeups/challenges",
                                                 filetypes=SUPPORTED_FILE_TYPES)
            if not paths:
                return
            for path in paths:
                try:
                    md = convert_file_to_markdown(path)
                    self._ctf_files.append((path, md))
                except Exception as e:
                    messagebox.showerror("Error", f"{Path(path).name}: {e}")
            _ctf_render_files()

        ctk.CTkButton(upload_card, text="+ Add Writeups/Challenges",
                      font=ctk.CTkFont("Arial", 12, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="white", corner_radius=14,
                      height=36, width=200,
                      command=_ctf_browse).pack(anchor="w", padx=20, pady=(0, 16))

        _ctf_render_files()

        # Practice options
        options_card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=16)
        options_card.pack(fill="x", padx=36, pady=(0, 16))

        ctk.CTkLabel(options_card, text="⚙️  Practice Options",
                     font=ctk.CTkFont("Arial", 13, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=20, pady=(16, 8))

        # Focus area
        focus_frame = ctk.CTkFrame(options_card, fg_color="transparent")
        focus_frame.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(focus_frame, text="Focus Area:",
                     font=ctk.CTkFont("Arial", 12), text_color=TEXT).pack(side="left", padx=(0, 10))
        self._ctf_focus_var = ctk.StringVar(value="all")
        focus_options = [
            ("all", "🌐 All Categories"),
            ("web", "🌐 Web Exploitation"),
            ("crypto", "🔐 Cryptography"),
            ("rev", "🔧 Reverse Engineering"),
            ("pwn", "💾 Binary Exploitation (Pwn)"),
            ("forensics", "🔍 Digital Forensics"),
            ("misc", "📦 Miscellaneous"),
        ]
        for val, label in focus_options:
            rb = ctk.CTkRadioButton(focus_frame, text=label,
                                    font=ctk.CTkFont("Arial", 11),
                                    variable=self._ctf_focus_var, value=val,
                                    fg_color=ACCENT, border_color=BORDER)
            rb.pack(side="left", padx=8, pady=4)

        # Difficulty
        diff_frame = ctk.CTkFrame(options_card, fg_color="transparent")
        diff_frame.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(diff_frame, text="Difficulty:",
                     font=ctk.CTkFont("Arial", 12), text_color=TEXT).pack(side="left", padx=(0, 10))
        self._ctf_difficulty_var = ctk.IntVar(value=3)
        diff_slider = ctk.CTkSlider(diff_frame, from_=1, to=5, number_of_steps=4,
                                    variable=self._ctf_difficulty_var,
                                    fg_color=CARD_LIGHT, progress_color=ACCENT,
                                    button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                    width=200)
        diff_slider.pack(side="left", padx=10)
        diff_labels = ["1 Beginner", "2 Easy", "3 Medium", "4 Hard", "5 Expert"]
        diff_lbl = ctk.CTkLabel(diff_frame, text=diff_labels[self._ctf_difficulty_var.get() - 1],
                                font=ctk.CTkFont("Arial", 11), text_color=ACCENT, width=80)
        diff_lbl.pack(side="left", padx=10)
        def _on_ctf_diff(val):
            v = round(float(val))
            diff_lbl.configure(text=diff_labels[v - 1])
        diff_slider.configure(command=_on_ctf_diff)

        # Question count
        count_frame = ctk.CTkFrame(options_card, fg_color="transparent")
        count_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkLabel(count_frame, text="Questions:",
                     font=ctk.CTkFont("Arial", 12), text_color=TEXT).pack(side="left", padx=(0, 10))
        self._ctf_count_var = ctk.IntVar(value=10)
        count_slider = ctk.CTkSlider(count_frame, from_=5, to=30, number_of_steps=25,
                                     variable=self._ctf_count_var,
                                     fg_color=CARD_LIGHT, progress_color=ACCENT,
                                     button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                     width=200)
        count_slider.set(10)
        count_slider.pack(side="left", padx=10)
        count_lbl = ctk.CTkLabel(count_frame, text="10",
                                 font=ctk.CTkFont("Arial", 13, "bold"),
                                 text_color=ACCENT, width=36)
        count_lbl.pack(side="left", padx=10)
        def _on_ctf_count(val):
            v = round(float(val))
            self._ctf_count_var.set(v)
            count_lbl.configure(text=str(v))
        count_slider.configure(command=_on_ctf_count)

        # Start button
        start_btn = ctk.CTkButton(scroll, text="Start CTF Practice  ▶",
                                  font=ctk.CTkFont("Arial", 15, "bold"),
                                  fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                  text_color="white", corner_radius=22,
                                  height=50, width=260,
                                  command=lambda: self._start_ctf_session(kit))
        start_btn.pack(pady=28)

    def _start_ctf_session(self, kit):
        if not self._ctf_files:
            messagebox.showerror("No materials", "Please add at least one CTF writeup or challenge.")
            return

        md = ""
        for _, m in self._ctf_files:
            md += m + "\n\n---\n\n"
        md = md.strip()

        if not md:
            messagebox.showerror("No content", "Could not read any file content.")
            return

        focus = self._ctf_focus_var.get()
        difficulty = self._ctf_difficulty_var.get()
        count = self._ctf_count_var.get()

        self._clear_main()
        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(scroll, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=40, pady=32)

        ctk.CTkLabel(inner, text="Generating CTF Practice…",
                     font=ctk.CTkFont("Arial", 16, "bold"),
                     text_color=TITLE).pack(pady=20)
        ctk.CTkLabel(inner,
                     text=f"Focus: {focus}  ·  Difficulty: {difficulty}/5  ·  Questions: {count}",
                     font=ctk.CTkFont("Arial", 12),
                     text_color=TEXT_MUTED).pack(pady=(0, 14))
        prog_bar = ctk.CTkProgressBar(inner, mode="indeterminate",
                                       progress_color=ACCENT, fg_color=CARD_LIGHT)
        prog_bar.pack(fill="x", padx=60)
        prog_bar.start()

        def task():
            try:
                # Generate CTF-specific questions using the cybersecurity prompts
                questions = generate_smart_study_questions(
                    md, count=count,
                    difficulty=difficulty,
                    enabled_types=["mcq", "multiselect", "match", "shortanswer"],
                    focus=f"CTF {focus} category. Generate practical questions about tools, commands, techniques, and MITRE ATT&CK mappings.")
                
                if not questions:
                    raise Exception("No questions could be generated from this material.")

                def done():
                    prog_bar.destroy()
                    for w in inner.winfo_children():
                        w.destroy()
                    
                    # Store questions and start quiz using existing quiz infrastructure
                    self.quiz_questions = questions
                    self.quiz_current_idx = 0
                    self.quiz_responses = []
                    self.quiz_mode = "ctf"
                    self.quiz_is_generating_more = False
                    
                    # Reuse the quiz rendering
                    self.quiz_scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
                    self.quiz_scroll.pack(fill="both", expand=True)
                    self.quiz_scroll.grid_columnconfigure(0, weight=1)
                    self._load_quiz_question(kit)

                self.after(0, done)
            except Exception as e:
                err = str(e)
                def fail():
                    messagebox.showerror("Generation Error", err)
                    self._show_ctf_practice(kit)
                self.after(0, fail)

        threading.Thread(target=task, daemon=True).start()


    # -----------------------------------------------------------------------
    # VIEW: FLASHCARDS  (Leitner SRS — 4 tiers, 3-face rating)
    # -----------------------------------------------------------------------
    def _show_flashcards(self, kit):
        self._clear_main()
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        self.active_kit = kit
        self.active_tab = "flashcards"
        self._update_sidebar(kit)

        # ── outer scrollable frame ──────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(self.main, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # ── header ─────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=36, pady=(28, 4))
        ctk.CTkLabel(hdr, text="🃏  Flashcards",
                     font=ctk.CTkFont("Arial", 24, "bold"),
                     text_color=TITLE).pack(side="left")
        ctk.CTkLabel(hdr, text=kit["name"],
                     font=ctk.CTkFont("Arial", 13),
                     text_color=TEXT_MUTED).pack(side="left", padx=(14, 0), anchor="s", pady=(0, 3))

        # ── check cache ─────────────────────────────────────────────────────
        cached = kit.get("flashcards")
        if cached:
            self._fc_launch(scroll, kit, cached)
        else:
            self._fc_generate(scroll, kit)

    def _fc_generate(self, scroll, kit):
        """Show loading bar, call AI, then launch deck."""
        inner = ctk.CTkFrame(scroll, fg_color="transparent")
        inner.pack(fill="x", padx=60, pady=60)

        lbl = ctk.CTkLabel(inner, text="Generating flashcards…",
                           font=ctk.CTkFont("Arial", 14), text_color=TEXT_MUTED)
        lbl.pack(pady=20)
        bar = ctk.CTkProgressBar(inner, mode="indeterminate",
                                  progress_color=ACCENT, fg_color=CARD_LIGHT)
        bar.pack(fill="x", padx=80)
        bar.start()

        md_text = combined_markdown(kit)

        def task():
            try:
                cards = generate_flashcards(md_text, kit.get("category", ""))
                kit["flashcards"] = cards
                update_kit_in_db(kit["id"], kit)

                def done():
                    lbl.destroy(); bar.destroy()
                    self._fc_launch(scroll, kit, cards)

                self.after(0, done)
            except Exception as e:
                err = str(e)
                self.after(0, lambda: lbl.configure(
                    text=f"Error: {err}", text_color=RED))
                self.after(0, bar.destroy)

        threading.Thread(target=task, daemon=True).start()

    def _fc_launch(self, scroll, kit, cards):
        """Flashcard UI — Canvas-based card with working click + flip animation."""
        import tkinter as tk

        # ── deck setup ──────────────────────────────────────────────────────
        deck = [dict(c) for c in cards]
        for c in deck:
            c.setdefault("tier",   "learning")
            c.setdefault("streak", 0)

        CARD_W   = 780
        CARD_H   = 260
        RADIUS   = 22
        FRONT_BG = "#1e1e30"
        BACK_BG  = "#1a1a3a"
        CHIP_FG  = "#28283e"
        STEPS    = 10          # frames per half (shrink or grow)
        DELAY    = 14          # ms per frame → ~280 ms total flip

        state = {
            "card_idx":  0,
            "idx":       0,
            "face":      "front",
            "animating": False,
            "history":   [],
        }

        # ── helpers ──────────────────────────────────────────────────────────
        def counts():
            t = {"learning": 0, "reviewing": 0, "known": 0, "mastered": 0}
            for c in deck:
                t[c["tier"]] += 1
            return t

        def queue():
            active   = [i for i, c in enumerate(deck) if c["tier"] != "mastered"]
            learning = [i for i in active if deck[i]["tier"] == "learning"]
            rest     = [i for i in active if deck[i]["tier"] != "learning"]
            return learning + rest

        # ════════════════════════════════════════════════════════════════════
        # TIER BAR
        # ════════════════════════════════════════════════════════════════════
        tier_bar = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=14)
        tier_bar.pack(fill="x", padx=36, pady=(8, 4))

        tier_meta = [
            ("learning",  "📘 Learning",  ACCENT),
            ("reviewing", "🔄 Reviewing", GOLD),
            ("known",     "✅ Known",      GREEN),
            ("mastered",  "🏆 Mastered",  "#a78bfa"),
        ]
        tier_widgets = {}
        for t, label, color in tier_meta:
            col = ctk.CTkFrame(tier_bar, fg_color="transparent")
            col.pack(side="left", expand=True, padx=8, pady=10)
            n = ctk.CTkLabel(col, text="0",
                             font=ctk.CTkFont("Arial", 20, "bold"),
                             text_color=color)
            n.pack()
            ctk.CTkLabel(col, text=label,
                         font=ctk.CTkFont("Arial", 10),
                         text_color=TEXT_MUTED).pack()
            tier_widgets[t] = n

        def refresh_tier_bar():
            c = counts()
            for t, w in tier_widgets.items():
                w.configure(text=str(c[t]))

        refresh_tier_bar()

        # progress text
        prog_lbl = ctk.CTkLabel(scroll, text="",
                                 font=ctk.CTkFont("Arial", 12),
                                 text_color=TEXT_MUTED)
        prog_lbl.pack(pady=(6, 2))

        # ════════════════════════════════════════════════════════════════════
        # CANVAS CARD  — reliable clicks, smooth animation
        # ════════════════════════════════════════════════════════════════════
        canvas_holder = ctk.CTkFrame(scroll, fg_color="transparent")
        canvas_holder.pack(pady=(4, 6))

        cv = tk.Canvas(canvas_holder,
                       width=CARD_W, height=CARD_H,
                       bg=BG, highlightthickness=0,
                       cursor="hand2")
        cv.pack()

        # We'll draw everything ourselves on the canvas
        def _round_rect(x1, y1, x2, y2, r, **kw):
            """Draw a rounded rectangle and return its id."""
            pts = [
                x1+r, y1,  x2-r, y1,
                x2,   y1,  x2,   y1+r,
                x2,   y2-r, x2,  y2,
                x2-r, y2,  x1+r, y2,
                x1,   y2,  x1,   y2-r,
                x1,   y1+r, x1,  y1,
                x1+r, y1,
            ]
            return cv.create_polygon(pts, smooth=True, **kw)

        # Canvas item ids (stored so we can delete/update)
        ids = {}

        def _draw_card(face: str, width_frac: float = 1.0):
            """
            Re-draw the card at `width_frac` of full width (0→1).
            width_frac < 1 gives the squish effect during animation.
            """
            cv.delete("all")

            ci   = state["card_idx"]
            card = deck[ci]

            cx   = CARD_W / 2
            half = (CARD_W / 2) * width_frac
            x1, x2 = cx - half, cx + half
            bg   = FRONT_BG if face == "front" else BACK_BG

            # card body
            _round_rect(x1, 4, x2, CARD_H - 4, RADIUS,
                        fill=bg, outline="#3a3a5c", width=1)

            if width_frac < 0.15:
                return   # too narrow — skip text to avoid flicker

            # face chip
            chip_text = "FRONT" if face == "front" else "BACK"
            chip_col  = CHIP_FG if face == "front" else "#2e2e50"
            chip_tc   = TEXT_MUTED if face == "front" else ACCENT
            chip_w    = 70
            chip_x1   = cx - chip_w / 2
            chip_x2   = cx + chip_w / 2
            _round_rect(chip_x1, 18, chip_x2, 42, 8,
                        fill=chip_col, outline="")
            cv.create_text(cx, 30, text=chip_text,
                           font=("Arial", 9, "bold"),
                           fill=chip_tc)

            # main text
            if face == "front":
                main_text  = card["front"]
                main_font  = ("Arial", 17, "bold")
                main_color = TEXT
            else:
                main_text  = card["back"]
                main_font  = ("Arial", 14, "normal")
                main_color = TITLE

            cv.create_text(cx, CARD_H // 2 + 10,
                           text=main_text,
                           font=main_font,
                           fill=main_color,
                           width=int((x2 - x1) * 0.88),
                           justify="center")

            # hint
            if face == "front":
                hint = "Click anywhere on the card to flip  ↕"
            else:
                hint = "Rate yourself below · click card to flip back  ↕"
            cv.create_text(cx, CARD_H - 20,
                           text=hint,
                           font=("Arial", 9),
                           fill=TEXT_MUTED)

        # ════════════════════════════════════════════════════════════════════
        # FLIP ANIMATION
        # ════════════════════════════════════════════════════════════════════
        def _animate_flip(target_face: str, on_done=None):
            if state["animating"]:
                return
            state["animating"] = True

            # phase list: fracs from 1→0 (shrink) then 0→1 (grow)
            shrink = [1.0 - i / STEPS for i in range(1, STEPS + 1)]
            grow   = [i / STEPS       for i in range(1, STEPS + 1)]

            swapped = [False]

            def tick(frames):
                if not frames:
                    state["animating"] = False
                    state["face"] = target_face
                    _draw_card(target_face, 1.0)
                    _sync_rating_buttons()
                    if on_done:
                        on_done()
                    return

                frac = frames[0]

                # midpoint swap
                if frac <= 0.0 and not swapped[0]:
                    swapped[0] = True
                    state["face"] = target_face
                    _sync_rating_buttons()

                _draw_card(target_face if swapped[0] else state["face"],
                           max(0.01, abs(frac)))
                self.after(DELAY, lambda: tick(frames[1:]))

            tick(shrink + grow)

        # ════════════════════════════════════════════════════════════════════
        # RATING BUTTONS
        # ════════════════════════════════════════════════════════════════════
        rating_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        rating_frame.pack(pady=(4, 0))

        btn_sad  = ctk.CTkButton(rating_frame, text="😞  Forgot",
                                  font=ctk.CTkFont("Arial", 13, "bold"),
                                  fg_color=RED, hover_color="#c0392b",
                                  text_color="white", corner_radius=20,
                                  height=44, width=148)
        btn_meh  = ctk.CTkButton(rating_frame, text="😐  Almost",
                                  font=ctk.CTkFont("Arial", 13, "bold"),
                                  fg_color="#b8860b", hover_color="#7a5c00",
                                  text_color="white", corner_radius=20,
                                  height=44, width=148)
        btn_good = ctk.CTkButton(rating_frame, text="😊  Got it!",
                                  font=ctk.CTkFont("Arial", 13, "bold"),
                                  fg_color=GREEN, hover_color="#27ae60",
                                  text_color="white", corner_radius=20,
                                  height=44, width=148)

        def _sync_rating_buttons():
            """Show rating buttons only on back face."""
            if state["face"] == "back":
                if not btn_sad.winfo_ismapped():
                    btn_sad.pack(side="left",  padx=8)
                    btn_meh.pack(side="left",  padx=8)
                    btn_good.pack(side="left", padx=8)
            else:
                btn_sad.pack_forget()
                btn_meh.pack_forget()
                btn_good.pack_forget()

        # ════════════════════════════════════════════════════════════════════
        # NAV + UTILITY BUTTONS
        # ════════════════════════════════════════════════════════════════════
        nav_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        nav_frame.pack(pady=(8, 0))

        btn_prev = ctk.CTkButton(nav_frame, text="◀  Prev",
                                  font=ctk.CTkFont("Arial", 12),
                                  fg_color=CARD_LIGHT, hover_color=BORDER,
                                  text_color=TEXT_MUTED, corner_radius=16,
                                  height=36, width=120)
        btn_skip = ctk.CTkButton(nav_frame, text="Skip  ▶",
                                  font=ctk.CTkFont("Arial", 12),
                                  fg_color=CARD_LIGHT, hover_color=BORDER,
                                  text_color=TEXT_MUTED, corner_radius=16,
                                  height=36, width=120)
        btn_prev.pack(side="left", padx=8)
        btn_skip.pack(side="left", padx=8)

        bottom_row = ctk.CTkFrame(scroll, fg_color="transparent")
        bottom_row.pack(pady=(16, 24))

        ctk.CTkButton(bottom_row, text="🔄  Regenerate Cards",
                       font=ctk.CTkFont("Arial", 12),
                       fg_color=CARD_LIGHT, hover_color=BORDER,
                       text_color=TEXT_MUTED, corner_radius=16,
                       height=36, width=185,
                       command=lambda: self._fc_regen(kit)).pack(side="left", padx=6)

        ctk.CTkButton(bottom_row, text="↺  Reset Progress",
                       font=ctk.CTkFont("Arial", 12),
                       fg_color=CARD_LIGHT, hover_color=BORDER,
                       text_color=TEXT_MUTED, corner_radius=16,
                       height=36, width=165,
                       command=lambda: self._fc_reset(kit)).pack(side="left", padx=6)

        # ════════════════════════════════════════════════════════════════════
        # DECK LOGIC
        # ════════════════════════════════════════════════════════════════════
        def load_card(animate=True):
            q = queue()
            if not q:
                cv.delete("all")
                _round_rect(0, 4, CARD_W, CARD_H - 4, RADIUS,
                            fill="#0d2d1a", outline="#1b5e20", width=1)
                cv.create_text(CARD_W // 2, CARD_H // 2,
                               text="🏆  All cards mastered!\nIncredible work.",
                               font=("Arial", 20, "bold"),
                               fill=GREEN, justify="center")
                btn_sad.pack_forget()
                btn_meh.pack_forget()
                btn_good.pack_forget()
                btn_skip.configure(state="disabled")
                prog_lbl.configure(
                    text=f"Session complete — {len(deck)} cards mastered 🎉")
                return

            ci = q[state["idx"] % len(q)]
            state["card_idx"] = ci
            state["face"]     = "front"
            _sync_rating_buttons()

            mastered = counts()["mastered"]
            prog_lbl.configure(
                text=f"Card {state['idx'] + 1} / {len(q)} active  ·  "
                     f"{mastered}/{len(deck)} mastered")

            if animate:
                _animate_flip("front")
            else:
                _draw_card("front", 1.0)

        def toggle_flip(_event=None):
            if state["animating"]:
                return
            target = "back" if state["face"] == "front" else "front"
            _animate_flip(target)

        def rate(rating: str):
            if state["animating"]:
                return
            ci   = state["card_idx"]
            card = deck[ci]

            if rating == "sad":
                card["tier"]   = "learning"
                card["streak"] = 0
            elif rating == "meh":
                card["streak"] = max(0, card["streak"] - 1)
                if card["tier"] == "learning":
                    card["tier"] = "reviewing"
            else:
                card["streak"] += 1
                if card["streak"] >= 3:
                    if card["tier"] in ("learning", "reviewing"):
                        card["tier"]   = "known"
                        card["streak"] = 0
                    elif card["tier"] == "known":
                        card["tier"]   = "mastered"
                        card["streak"] = 0
                elif card["tier"] == "learning":
                    card["tier"] = "reviewing"

            state["history"].append((ci, rating))
            kit["flashcards"] = [
                {"front": c["front"], "back": c["back"],
                 "tier": c["tier"], "streak": c["streak"]}
                for c in deck
            ]
            update_kit_in_db(kit["id"], kit)
            refresh_tier_bar()

            q = queue()
            if q:
                state["idx"] = (state["idx"] + 1) % len(q)
            load_card(animate=True)

        def go_prev():
            if state["animating"] or not state["history"]:
                return
            ci, last_rating = state["history"].pop()
            card = deck[ci]
            if last_rating == "sad":
                card["tier"]   = "reviewing"
                card["streak"] = 0
            elif last_rating == "good":
                card["streak"] = max(0, card["streak"] - 1)
                if card["tier"] == "mastered":
                    card["tier"] = "known"
                elif card["tier"] == "known":
                    card["tier"] = "reviewing"
            refresh_tier_bar()
            state["idx"] = max(0, state["idx"] - 1)
            load_card(animate=True)

        def go_skip():
            if state["animating"]:
                return
            q = queue()
            if q:
                state["idx"] = (state["idx"] + 1) % len(q)
            load_card(animate=True)

        # ── wire up ──────────────────────────────────────────────────────────
        cv.bind("<Button-1>", toggle_flip)
        btn_sad.configure(command=lambda: rate("sad"))
        btn_meh.configure(command=lambda: rate("meh"))
        btn_good.configure(command=lambda: rate("good"))
        btn_prev.configure(command=go_prev)
        btn_skip.configure(command=go_skip)

        # draw first card immediately, no animation
        load_card(animate=False)

    # -----------------------------------------------------------------------
    # VIEW: ASK AI (Chat Q&A)
    # -----------------------------------------------------------------------
    def _show_ask_ai(self, kit):
        self._clear_main()
        _, kit = find_kit_in_db(kit["id"])
        if kit is None:
            self._show_dashboard()
            return

        self.active_kit = kit
        self.active_tab = "ask_ai"
        self._update_sidebar(kit)

        # Conversation history: list of {role: "user"|"ai", content: str}
        conversation = []
        md_text = combined_markdown(kit)

        # ── outer layout ────────────────────────────────────────────────────
        outer = ctk.CTkFrame(self.main, fg_color=BG)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_rowconfigure(0, weight=0)
        outer.grid_rowconfigure(2, weight=0)
        outer.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(28, 8))
        ctk.CTkLabel(hdr, text="💬  Ask AI Tutor",
                     font=ctk.CTkFont("Arial", 24, "bold"),
                     text_color=TITLE).pack(side="left")
        ctk.CTkLabel(hdr, text=kit["name"],
                     font=ctk.CTkFont("Arial", 13),
                     text_color=TEXT_MUTED).pack(side="left", padx=(14, 0), anchor="s", pady=(0, 3))

        # Chat messages scroll
        chat_scroll = ctk.CTkScrollableFrame(outer, fg_color=BG)
        chat_scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        chat_scroll.grid_columnconfigure(0, weight=1)

        # Welcome bubble from AI
        def add_bubble(role, text):
            """Add a chat bubble to the scroll frame."""
            is_user = (role == "user")
            bubble_color  = ACCENT if is_user else CARD
            text_color    = "white" if is_user else TEXT
            anchor_side   = "e" if is_user else "w"
            padx          = (120, 16) if is_user else (16, 120)

            row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=20)

            bubble = ctk.CTkFrame(row, fg_color=bubble_color, corner_radius=16)
            bubble.pack(anchor=anchor_side, padx=padx)

            ctk.CTkLabel(bubble, text=text,
                         font=ctk.CTkFont("Arial", 12),
                         text_color=text_color,
                         justify="left", wraplength=560).pack(padx=14, pady=10)

            # Auto scroll to bottom
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

        add_bubble("ai",
                   f"Hi! I'm your AI Tutor for **{kit['name']}**. "
                   f"I've read your study materials and I'm ready to answer any questions about them. "
                   f"What would you like to know?")

        # ── input bar ───────────────────────────────────────────────────────
        input_bar = ctk.CTkFrame(outer, fg_color=CARD, corner_radius=0)
        input_bar.grid(row=2, column=0, sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)

        input_entry = ctk.CTkEntry(
            input_bar,
            placeholder_text="Ask a question about your study material…",
            font=ctk.CTkFont("Arial", 13),
            fg_color=CARD_LIGHT, border_color=BORDER,
            text_color=TEXT, height=48, corner_radius=14)
        input_entry.grid(row=0, column=0, padx=(16, 8), pady=14, sticky="ew")

        send_btn = ctk.CTkButton(
            input_bar, text="Send  ➤",
            font=ctk.CTkFont("Arial", 13, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", corner_radius=14,
            height=44, width=110)
        send_btn.grid(row=0, column=1, padx=(0, 16), pady=14)

        def send_message(event=None):
            question = input_entry.get().strip()
            if not question:
                return
            input_entry.delete(0, "end")
            send_btn.configure(state="disabled", text="…")

            # Show user bubble
            add_bubble("user", question)

            # Show typing indicator
            typing_row = ctk.CTkFrame(chat_scroll, fg_color="transparent")
            typing_row.pack(fill="x", pady=4, padx=20, anchor="w")
            typing_bubble = ctk.CTkFrame(typing_row, fg_color=CARD, corner_radius=16)
            typing_bubble.pack(anchor="w", padx=(16, 120))
            typing_lbl = ctk.CTkLabel(typing_bubble, text="AI Tutor is thinking…",
                                       font=ctk.CTkFont("Arial", 12, slant="italic"),
                                       text_color=TEXT_MUTED)
            typing_lbl.pack(padx=14, pady=10)
            chat_scroll.after(50, lambda: chat_scroll._parent_canvas.yview_moveto(1.0))

            # Build history for context
            history_for_api = [
                {"role": m["role"], "content": m["content"]}
                for m in conversation
            ]

            def task():
                try:
                    answer = ask_ai_question(md_text, question, history_for_api)

                    def done():
                        typing_row.destroy()
                        conversation.append({"role": "user", "content": question})
                        conversation.append({"role": "ai", "content": answer})
                        add_bubble("ai", answer)
                        send_btn.configure(state="normal", text="Send  ➤")

                    self.after(0, done)
                except Exception as e:
                    err = str(e)
                    def fail():
                        typing_row.destroy()
                        add_bubble("ai", f"⚠️ Error: {err}")
                        send_btn.configure(state="normal", text="Send  ➤")
                    self.after(0, fail)

            threading.Thread(target=task, daemon=True).start()

        send_btn.configure(command=send_message)
        input_entry.bind("<Return>", send_message)

    def _fc_regen(self, kit):
        """Clear cached flashcards and regenerate."""
        kit.pop("flashcards", None)
        update_kit_in_db(kit["id"], kit)
        self._show_flashcards(kit)

    def _fc_reset(self, kit):
        """Reset all tier/streak progress to 'learning'."""
        cards = kit.get("flashcards", [])
        for c in cards:
            c["tier"]   = "learning"
            c["streak"] = 0
        kit["flashcards"] = cards
        update_kit_in_db(kit["id"], kit)
        self._show_flashcards(kit)


# ===========================================================================
if __name__ == "__main__":
    app = App()
    app.mainloop()


CYBER_METADATA_PROMPT = """\
You are an advanced cybersecurity content processor for a study module called Study Companion.
Analyze the following source text and determine the optimal metadata for a CYBERSECURITY study kit.

SOURCE MATERIAL:
---
{source}
---

Return a JSON object with exactly three fields:
1. "kit_name": Concise, engaging title (e.g., "Network Security Fundamentals", "Web App Pentesting", "Malware Analysis Basics"). No chapter numbers.
2. "subject_category": Must be "CYBERSECURITY" or a specific sub-domain like "CYBERSECURITY - NETWORK SECURITY", "CYBERSECURITY - WEB APPLICATION", "CYBERSECURITY - CRYPTOGRAPHY", "CYBERSECURITY - INCIDENT RESPONSE", "CYBERSECURITY - THREAT INTELLIGENCE".
3. "card_color": Choose ONLY from "yellow", "blue", or "red" based on topic feel (red for offensive/red team, blue for defensive/blue team, yellow for policy/governance).

Return ONLY the raw JSON. No markdown, no preamble, no code blocks.
"""

CYBER_STUDY_GUIDE_PROMPT = """\
You are a world-class cybersecurity instructor generating a Study Guide for Study Companion (Campus Suite).
Focus on practical, hands-on cybersecurity concepts.

SOURCE MATERIAL:
---
{source}
---

Create a comprehensive, well-structured Cybersecurity Study Guide from the material above.
Rules:
- Use ## for main headings and ### for sub-headings
- Use bullet points (- ) for lists of facts, tools, commands, or procedures
- Bold (**text**) key terms, tool names, commands, and techniques on first use
- Include a short introductory sentence under each main heading
- Do NOT invent facts outside the source material
- Cover every major concept in the material
- Include practical examples: command syntax, tool flags, code snippets where relevant
- Map concepts to MITRE ATT&CK where applicable
- Highlight detection/mitigation strategies for offensive topics

Return only the study guide content. No preamble.
"""

CYBER_QUIZ_PROMPT = """\
You are Campus Suite's Cybersecurity Smart Study engine. Generate exam-grade cybersecurity questions.

SOURCE MATERIAL:
---
{source}
---

PREVIOUSLY ASKED (avoid repeating):
{history}

DIFFICULTY: {difficulty}/5  (1=basic recall, 3=conceptual, 5=application/analysis)
TOPIC FOCUS: {focus}

RULES:
- Ground every question in the source material only.
- Focus on practical cybersecurity knowledge: tools, commands, techniques, mitigations
- Include MITRE ATT&CK technique IDs where applicable
- The correct answer (A:) MUST match one of the options (O:) EXACTLY.
- Generate EXACTLY {count} questions.
- No bold markdown inside fields.

FORMAT (one block per question, blank line between):
TYPE: mcq
Q: [Question text - e.g., "Which nmap flag performs a SYN scan?"]
A: [Exact correct answer]
O: [Option 1] | [Option 2] | [Option 3] | [Option 4]
E: [Explanation point with tool/command reference] | [Explanation point]
"""

CYBER_FLASHCARD_PROMPT = """\
You are a world-class cybersecurity tutor for Campus Suite — Study Companion. Extract key terms, tools, commands,
techniques, and definitions from the source material and convert them into concise two-sided flashcards.

SOURCE MATERIAL:
---
{source}
---

RULES:
- Each card must be grounded in the source material only.
- Front: a tool name, command, technique ID, acronym, or concept name (keep it short).
- Back: a clear, complete definition or explanation (1-3 sentences max) with practical context.
- Generate between 15 and 25 cards covering the full breadth of the material.
- Do NOT duplicate or overlap cards.
- Include MITRE ATT&CK technique IDs where relevant (e.g., "T1059.001").
- For tools: include common flags/syntax on the back.

Use this EXACT format, one card per block. No markdown, no numbering, no extra text.

F: [Front text]
B: [Back text]
"""

CYBER_SUMMARY_PROMPT = """\
You are an expert cybersecurity instructor. Generate a high-level, concise summary of the following material.
Focus on actionable cybersecurity knowledge.

SOURCE MATERIAL:
---
{source}
---

Rules:
- Use ## for main headings and ### for sub-headings
- Use bullet points (- ) for lists
- Bold (**text**) key terms, tools, commands, techniques on first use
- Do NOT include markdown code block syntax, just return raw markdown text.
- Do NOT invent facts outside the source material.
- Structure:
  1. Executive Summary: The overall gist in 2-3 sentences.
  2. Key Tools & Commands: Most critical tools with example syntax.
  3. MITRE ATT&CK Mapping: Relevant technique IDs and tactics.
  4. Detection & Mitigation: Key defensive measures.
  5. Essential Glossary: 4-5 key terms and brief definitions.

Return only the summary content. No preamble.
"""

METADATA_PROMPT = """\
You are an advanced educational content processor for a study module called Study Companion, part of Campus Suite.
Analyze the following source text and determine the optimal metadata.

SOURCE MATERIAL:
---
{source}
---

Return a JSON object with exactly three fields:
1. "kit_name": Concise, engaging title (e.g., "Computer Systems"). No chapter numbers.
2. "subject_category": Overarching subject (e.g., "COMPUTER SCIENCE - CLASS XI").
3. "card_color": Choose ONLY from "yellow", "blue", or "red" based on topic feel.

Return ONLY the raw JSON. No markdown, no preamble, no code blocks.
"""


# ---------------------------------------------------------------------------
# AI — MODEL CALL
# Gemini stays the primary path. When no GEMINI_API_KEY is configured the exact
# same prompt is sent through Campus Suite's shared key manager instead
# (campus_ai -> key_manager.AiApi), so every generator below keeps working on
# whichever provider the suite is set up with.
# ---------------------------------------------------------------------------
_CAMPUS_AI_SYSTEM = (
    "You are the Campus Suite Study Companion generator. Work only from the source material you "
    "are given, follow the requested output format exactly, and return just the requested content "
    "with no preamble, commentary, or markdown code fences."
)


def _generate_text(prompt, temperature=0.4, role="analyst"):
    """Return model text for a prompt — Gemini if keyed, else the shared key manager."""
    if _client is not None:
        response = _client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=temperature),
        )
        if not response.text or not response.text.strip():
            raise Exception("Empty response from Gemini.")
        return response.text.strip()

    # No Gemini key — route the prompt through the shared Campus Suite key manager.
    try:
        from campus_ai import ask_campus_ai
    except Exception as e:
        raise Exception(f"No GEMINI_API_KEY found and Campus Suite AI is unavailable: {e}")
    try:
        return ask_campus_ai(_CAMPUS_AI_SYSTEM, prompt, role=role)
    except Exception as e:
        raise Exception(f"No GEMINI_API_KEY found, and Campus Suite AI failed: {e}")


def extract_kit_metadata(markdown_text):
    # Check if content appears to be cybersecurity-related
    cyber_keywords = [
        "cybersecurity", "cyber security", "penetration test", "pentest", "vulnerability",
        "exploit", "malware", "ransomware", "phishing", "firewall", "ids", "ips",
        "siem", "soc", "incident response", "threat intelligence", "mitre", "att&ck",
        "nmap", "burp", "metasploit", "wireshark", "tcpdump", "hashcat", "john the ripper",
        "sql injection", "xss", "csrf", "rce", "lfi", "rfi", "ssrf", "xxe",
        "cryptography", "encryption", "tls", "ssl", "pki", "certificate",
        "blue team", "red team", "purple team", "threat hunting", "forensics",
        "reverse engineering", "static analysis", "dynamic analysis", "fuzzing"
    ]
    text_lower = markdown_text.lower()
    is_cyber = any(kw in text_lower for kw in cyber_keywords)
    
    prompt = CYBER_METADATA_PROMPT.format(source=markdown_text[:3000]) if is_cyber else METADATA_PROMPT.format(source=markdown_text[:3000])
    
    raw = _generate_text(prompt, temperature=0.4, role="reporter").replace("```json", "").replace("```", "").strip()
    meta = json.loads(raw)
    for key in ("kit_name", "subject_category", "card_color"):
        if key not in meta:
            raise Exception(f"Missing field in metadata response: {key}")
    return meta


# ---------------------------------------------------------------------------
# AI — STUDY GUIDE GENERATION
# ---------------------------------------------------------------------------
STUDY_GUIDE_PROMPT = """\
You are a world-class academic tutor generating a Study Guide for Study Companion (Campus Suite).

SOURCE MATERIAL:
---
{source}
---

Create a comprehensive, well-structured Study Guide from the material above.
Rules:
- Use ## for main headings and ### for sub-headings
- Use bullet points (- ) for lists of facts or components
- Bold (**text**) key terms on first use
- Include a short introductory sentence under each main heading
- Do NOT invent facts outside the source material
- Cover every major concept in the material

Return only the study guide content. No preamble.
"""


def generate_study_guide(markdown_text, category=""):
    prompt = CYBER_STUDY_GUIDE_PROMPT.format(source=markdown_text) if "CYBERSECURITY" in category.upper() else STUDY_GUIDE_PROMPT.format(source=markdown_text)
    return _generate_text(prompt, temperature=0.4, role="reporter")


# ---------------------------------------------------------------------------
# AI — STUDY SUMMARY GENERATION
# ---------------------------------------------------------------------------
SUMMARY_PROMPT = """\
You are an expert academic tutor. Generate a high-level, concise summary of the following material.
Focus on:
1. Executive Summary: The overall gist in 2-3 sentences.
2. Key Takeaways: High-level bullet points (using - ) of the most critical concepts, formulas, or rules.
3. Essential Glossary: 4-5 key terms and their brief definitions.

SOURCE MATERIAL:
---
{source}
---

Rules:
- Use ## for main headings and ### for sub-headings
- Use bullet points (- ) for lists
- Bold (**text**) key terms on first use
- Do NOT include markdown code block syntax (like ```markdown), just return the raw markdown text.
- Do NOT invent facts outside the source material.

Return only the summary content. No preamble.
"""


def generate_summary(markdown_text, category=""):
    prompt = CYBER_SUMMARY_PROMPT.format(source=markdown_text) if "CYBERSECURITY" in category.upper() else SUMMARY_PROMPT.format(source=markdown_text)
    return _generate_text(prompt, temperature=0.4, role="reporter")


# ---------------------------------------------------------------------------
# AI — ASK AI (CHAT Q&A)
# ---------------------------------------------------------------------------
ASK_AI_PROMPT = """\
You are Campus Suite's AI Tutor — an expert academic assistant embedded in a study app.
The student has uploaded their study materials and is asking you a question about them.

STUDY MATERIAL CONTEXT:
---
{source}
---

CONVERSATION HISTORY:
{history}

STUDENT'S CURRENT QUESTION:
{question}

Rules:
- Answer in a clear, concise, helpful way.
- Ground your answer in the study material provided.
- If the question is outside the material, say so politely and answer based on general knowledge.
- Use markdown formatting (bullet points, bold, headings) when helpful for clarity.
- Keep your response focused and educational.
- Maximum 3-4 paragraphs unless the topic genuinely requires more detail.

Respond now as the AI Tutor:
"""


def ask_ai_question(markdown_text, question, history_list=None):
    history_str = "None"
    if history_list:
        lines = []
        for msg in history_list:
            role = "Student" if msg["role"] == "user" else "AI Tutor"
            lines.append(f"{role}: {msg['content']}")
        history_str = "\n".join(lines)
    prompt = ASK_AI_PROMPT.format(
        source=markdown_text[:8000],
        history=history_str,
        question=question
    )
    return _generate_text(prompt, temperature=0.5, role="analyst")


# ---------------------------------------------------------------------------
# AI — SMART STUDY: MULTI-TYPE ENGINE
# ---------------------------------------------------------------------------

_MCQ_PROMPT = """\
You are Campus Suite's Smart Study engine. Generate exam-grade single-answer MCQs.

SOURCE MATERIAL:
---
{source}
---

PREVIOUSLY ASKED (avoid repeating):
{history}

DIFFICULTY: {difficulty}/5  (1=basic recall, 3=conceptual, 5=application/analysis)
TOPIC FOCUS: {focus}

RULES:
- Ground every question in the source material only.
- The correct answer (A:) MUST match one of the options (O:) EXACTLY.
- Generate EXACTLY {count} questions.
- No bold markdown inside fields.

FORMAT (one block per question, blank line between):
TYPE: mcq
Q: [Question text]
A: [Exact correct answer]
O: [Option 1] | [Option 2] | [Option 3] | [Option 4]
E: [Explanation point] | [Explanation point]
"""

_MULTISELECT_PROMPT = """\
You are Campus Suite's Smart Study engine. Generate multi-select questions where MORE THAN ONE option may be correct.

SOURCE MATERIAL:
---
{source}
---

PREVIOUSLY ASKED (avoid repeating):
{history}

DIFFICULTY: {difficulty}/5
TOPIC FOCUS: {focus}

RULES:
- Each question MUST have exactly 2 or 3 correct answers (not 1, not all 4).
- Correct answers (A:) listed separated by | — must match options exactly.
- Generate EXACTLY {count} questions.
- No bold markdown inside fields.

FORMAT:
TYPE: multiselect
Q: [Question — phrase it as "Select all that apply:" or "Which of the following are..."]
A: [Correct 1] | [Correct 2]
O: [Option 1] | [Option 2] | [Option 3] | [Option 4]
E: [Explanation point] | [Explanation point]
"""

_MATCH_PROMPT = """\
You are Campus Suite's Smart Study engine. Generate Match-the-Following concept-pairing questions.

SOURCE MATERIAL:
---
{source}
---

PREVIOUSLY ASKED (avoid repeating):
{history}

DIFFICULTY: {difficulty}/5
TOPIC FOCUS: {focus}

RULES:
- Create EXACTLY {count} matching questions.
- Each question has exactly 4 ITEMS on the left and 4 MATCHES on the right.
- Items on the right should be SHUFFLED (not in the same order as the left).
- No bold markdown inside fields.

FORMAT:
TYPE: match
Q: [Overarching question e.g. "Match each term to its definition:"]
L: [Item A] | [Item B] | [Item C] | [Item D]
R: [Match for A] | [Match for B] | [Match for C] | [Match for D]
RS: [Shuffled Match 1] | [Shuffled Match 2] | [Shuffled Match 3] | [Shuffled Match 4]
E: [Brief explanation of the pairing logic]
"""

_SHORT_ANSWER_PROMPT = """\
You are Campus Suite's Smart Study engine. Generate open-ended short-answer questions.

SOURCE MATERIAL:
---
{source}
---

PREVIOUSLY ASKED (avoid repeating):
{history}

DIFFICULTY: {difficulty}/5
TOPIC FOCUS: {focus}

RULES:
- Questions should require a 1-3 sentence typed explanation.
- Provide a model answer (A:) that an AI grader can compare against.
- Generate EXACTLY {count} questions.
- No bold markdown inside fields.

FORMAT:
TYPE: shortanswer
Q: [Question text]
A: [Model answer — a complete, ideal response]
E: [Key concept this tests]
"""

_GRADE_PROMPT = """\
You are a strict but fair AI academic grader for Campus Suite.

QUESTION: {question}
MODEL ANSWER: {model_answer}
STUDENT ANSWER: {student_answer}

Grade the student's answer on a scale of 0-2:
- 2 = Correct or substantially correct (core concepts captured)
- 1 = Partially correct (some key points present, some missing)
- 0 = Incorrect or missing the point entirely

Return a JSON object with exactly:
{{"score": <0|1|2>, "feedback": "<1-2 sentence feedback to student>"}}

Return ONLY the raw JSON. No markdown, no preamble.
"""


def _build_history_str(history_list):
    if not history_list:
        return "None"
    return "\n".join(f"- {q}" for q in history_list)


def _generate_questions_of_type(qtype, markdown_text, count, difficulty, focus, history_list):
    """Generate `count` questions of a specific type."""
    history_str = _build_history_str(history_list)
    src = markdown_text[:8000]
    prompts = {
        "mcq":         _MCQ_PROMPT,
        "multiselect": _MULTISELECT_PROMPT,
        "match":       _MATCH_PROMPT,
        "shortanswer": _SHORT_ANSWER_PROMPT,
    }
    prompt = prompts[qtype].format(
        source=src, count=count, history=history_str,
        difficulty=difficulty, focus=focus or "No specific focus — cover the material broadly."
    )
    raw = _generate_text(prompt, temperature=0.6 + (difficulty - 3) * 0.06, role="planner")
    return _parse_questions(raw)


def generate_smart_study_questions(
        markdown_text, count=5, history_list=None,
        difficulty=3, enabled_types=None, focus=""):
    """Dispatch question generation across enabled types."""
    if enabled_types is None:
        enabled_types = ["mcq", "multiselect", "match", "shortanswer"]
    if not enabled_types:
        enabled_types = ["mcq"]

    import math, random
    per_type = max(1, math.ceil(count / len(enabled_types)))
    results = []
    for qtype in enabled_types:
        try:
            batch = _generate_questions_of_type(
                qtype, markdown_text, per_type,
                difficulty, focus, history_list)
            results.extend(batch)
        except Exception as e:
            print(f"[SmartStudy] Failed to generate {qtype}: {e}")
    random.shuffle(results)
    return results[:count] if results else []


def _parse_questions(text):
    """Universal parser for all question types."""
    questions = []
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    for block in blocks:
        q = _parse_one_block(block)
        if q:
            questions.append(q)
    if not questions:
        questions = _parse_mcq_linewise(text)
    return questions


def _parse_one_block(block):
    """Parse a single question block (any type)."""
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    d = {}
    for line in lines:
        up = line.upper()
        if up.startswith("TYPE:"):
            d["type"] = line[5:].strip().lower()
        elif up.startswith("Q:"):
            d["q"] = line[2:].strip()
        elif up.startswith("A:"):
            d["a_raw"] = line[2:].strip()
        elif up.startswith("O:"):
            d["o"] = [x.strip() for x in line[2:].split("|") if x.strip()]
        elif up.startswith("L:"):
            d["l"] = [x.strip() for x in line[2:].split("|") if x.strip()]
        elif up.startswith("RS:"):
            d["rs"] = [x.strip() for x in line[3:].split("|") if x.strip()]
        elif up.startswith("R:") and "RS:" not in up:
            d["r"] = [x.strip() for x in line[2:].split("|") if x.strip()]
        elif up.startswith("E:"):
            d["e"] = [x.strip() for x in line[2:].split("|") if x.strip()]

    if not d.get("q"):
        return None

    qtype = d.get("type", "mcq")

    if qtype == "match":
        if not d.get("l") or not d.get("r"):
            return None
        rs = d.get("rs") or list(d["r"])
        return {
            "type": "match", "q": d["q"],
            "l": d["l"], "r": d["r"], "rs": rs,
            "e": d.get("e", []),
        }
    if qtype == "shortanswer":
        if not d.get("a_raw"):
            return None
        return {
            "type": "shortanswer", "q": d["q"],
            "a": d["a_raw"], "e": d.get("e", []),
        }
    if qtype == "multiselect":
        if not d.get("a_raw") or not d.get("o"):
            return None
        correct = [x.strip() for x in d["a_raw"].split("|") if x.strip()]
        return {
            "type": "multiselect", "q": d["q"],
            "a": correct, "o": d["o"], "e": d.get("e", []),
        }
    # Default: mcq
    if not d.get("a_raw") or not d.get("o"):
        return None
    return {
        "type": "mcq", "q": d["q"],
        "a": d["a_raw"], "o": d["o"], "e": d.get("e", []),
    }


def _parse_mcq_linewise(text):
    """Legacy line-by-line MCQ parser (fallback)."""
    quiz, current = [], {}
    for line in text.split("\n"):
        clean = line.replace("**", "").strip()
        if not clean:
            continue
        up = clean.upper()
        if up.startswith("Q:"):
            if current.get("q") and current.get("a") and current.get("o"):
                current.setdefault("e", [])
                current.setdefault("type", "mcq")
                quiz.append(current)
            current = {"q": clean[2:].strip(), "type": "mcq"}
        elif up.startswith("A:") and current:
            current["a"] = clean[2:].strip()
        elif up.startswith("O:") and current:
            current["o"] = [x.strip() for x in clean[2:].split("|") if x.strip()]
        elif up.startswith("E:") and current:
            current["e"] = [x.strip() for x in clean[2:].split("|") if x.strip()]
    if current.get("q") and current.get("a") and current.get("o"):
        current.setdefault("e", [])
        current.setdefault("type", "mcq")
        quiz.append(current)
    return quiz


def parse_mcqs(text):
    """Backward compat wrapper."""
    return _parse_mcq_linewise(text)


def grade_short_answer(question, model_answer, student_answer):
    """AI-grade a student's short answer. Returns {score:0-2, feedback:str}."""
    prompt = _GRADE_PROMPT.format(
        question=question,
        model_answer=model_answer,
        student_answer=student_answer,
    )
    raw = _generate_text(prompt, temperature=0.2, role="reviewer").replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)
    return result

# ---------------------------------------------------------------------------
# AI — FLASHCARD GENERATION
# ---------------------------------------------------------------------------
FLASHCARD_PROMPT = """\
You are a world-class tutor for Campus Suite — Study Companion. Extract key terms, concepts,
formulas, and definitions from the source material and convert them into
concise two-sided flashcards.

SOURCE MATERIAL:
---
{source}
---

RULES:
- Each card must be grounded in the source material only.
- Front: a term, acronym, formula label, or concept name (keep it short).
- Back: a clear, complete definition or explanation (1-3 sentences max).
- Generate between 15 and 25 cards covering the full breadth of the material.
- Do NOT duplicate or overlap cards.

Use this EXACT format, one card per block. No markdown, no numbering, no extra text.

F: [Front text]
B: [Back text]
"""


def generate_flashcards(markdown_text, category=""):
    prompt = CYBER_FLASHCARD_PROMPT.format(source=markdown_text[:9000]) if "CYBERSECURITY" in category.upper() else FLASHCARD_PROMPT.format(source=markdown_text[:9000])
    return parse_flashcards(_generate_text(prompt, temperature=0.4, role="designer"))


def parse_flashcards(text):
    cards, current = [], {}
    for line in text.split("\n"):
        clean = line.strip()
        if not clean:
            if current.get("front") and current.get("back"):
                cards.append({"front": current["front"], "back": current["back"],
                               "tier": "learning", "streak": 0})
                current = {}
            continue
        up = clean.upper()
        if up.startswith("F:"):
            if current.get("front") and current.get("back"):
                cards.append({"front": current["front"], "back": current["back"],
                               "tier": "learning", "streak": 0})
            current = {"front": clean[2:].strip()}
        elif up.startswith("B:") and current:
            current["back"] = clean[2:].strip()
    if current.get("front") and current.get("back"):
        cards.append({"front": current["front"], "back": current["back"],
                       "tier": "learning", "streak": 0})
    return cards


# ---------------------------------------------------------------------------
# AI — CYBERSECURITY SPECIALIZED PROMPTS
# ---------------------------------------------------------------------------


