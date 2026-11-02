import os
import re
import sys
import json
import time
import math
import queue
import socket
import ipaddress
import threading
import requests
import pyautogui
import grpc
import riva.client
import customtkinter as ctk
from campus_theme import apply_ctk_theme, get_theme_mode, get_theme_palette, watch_theme
from PIL import Image, ImageDraw, ImageFont
import qrcode
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Optional
import uvicorn
import urllib3
import base64
import subprocess
import asyncio
import hashlib
from contextlib import asynccontextmanager

# Suppress self-signed certificate warnings in the console log stream
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure win32 bindings map dynamically across structural architectures
if sys.platform == "win32":
    import win32com.client
    import pythoncom
    import win32gui

# --- CONFIGURATION & API KEYS ---

def _optional_provider_credential(*names: str) -> Optional[str]:
    """Return a configured provider secret without using a source-code fallback."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None



PARAKEET_FUNCTION_ID = os.getenv("PARAKEET_FUNCTION_ID", "71203149-d3b7-4460-8231-1be2543a1fca")
PARAKEET_SERVER = os.getenv("PARAKEET_SERVER") or os.getenv("RIVA_SERVER", "grpc.nvcf.nvidia.com:443")


GEMMA_URL = os.getenv("GEMMA_URL", "https://integrate.api.nvidia.com/v1/chat/completions")



SAMPLING_RATE = 16000
PYTHON_BACKEND_PORT = int(os.getenv("PYTHON_BACKEND_PORT", "9005"))
NODE_FRONTEND_PORT = int(os.getenv("NODE_FRONTEND_PORT", "8000"))
UDP_AUDIO_PORT = int(os.getenv("UDP_AUDIO_PORT", "5005"))

# --- DIRECTORY SETUP ---
_default_thumbnails_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "thumbnails")
NODE_THUMBNAILS_DIR = os.getenv("NODE_THUMBNAILS_DIR", _default_thumbnails_dir)
try:
    os.makedirs(NODE_THUMBNAILS_DIR, exist_ok=True)
except Exception as _dir_err:
    print(f"[WARNING] Could not create thumbnails directory '{NODE_THUMBNAILS_DIR}': {_dir_err}")
    # Fallback to local workspace thumbnails dir
    NODE_THUMBNAILS_DIR = _default_thumbnails_dir
    try:
        os.makedirs(NODE_THUMBNAILS_DIR, exist_ok=True)
    except Exception:
        pass

# Threading Control Queues & State Flags
command_queue = queue.Queue()       # Raw voice transcripts
action_queue = queue.Queue()        # Unified structural hardware execution requests
gui_log_queue = queue.Queue()       # Inter-thread UI safe logger
audio_stream_queue = queue.Queue(maxsize=5000)

_state_lock = threading.Lock()
current_slide_state = {"current": 1, "max": 999}

_buffer_lock = threading.Lock()
semantic_buffer = ""                # Rolling buffer for the AI Similarity Monitor

_transcript_lock = threading.Lock()
last_transcript = ""                # Last recognised speech (displayed live in GUI)

_similarity_lock = threading.Lock()
last_similarity_score = 0.0         # Latest Nemotron score (drives confidence bar)

# Service management
services_started = False
services_attached_to_existing = False
node_process = None
node_log_threads = []
node_log_stop_event = None
service_threads = {}
# Cooperative shutdown event for Python worker loops
stop_event = threading.Event()
# Uvicorn server handle for graceful shutdown
uvicorn_server = None

# --- AUTOMATIC IP DISCOVERY ---
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"

COMPUTER_IP = get_local_ip()
# Allow switching to HTTP for local development (set USE_HTTP=true)
USE_HTTP = os.environ.get('USE_HTTP', 'false').lower() == 'true' or os.environ.get('NODE_ENV', '').lower() == 'development'
PROTOCOL = 'http' if USE_HTTP else 'https'
# Must be https:// in production to allow mobile microphone ingestion permissions
NODE_UI_URL = f"{PROTOCOL}://{COMPUTER_IP}:{NODE_FRONTEND_PORT}"

from collections import defaultdict

# --- ANNOTATION STORE & DISK PERSISTENCE ---
slide_annotations = defaultdict(list)
annotation_redo = defaultdict(list)
ANNOTATION_LOCK = threading.Lock()
ANNOTATION_FILE_PATH = os.getenv(
    "ANNOTATION_FILE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage", "annotations.json"),
)
_annotation_save_lock = threading.Lock()
_annotation_save_event = threading.Event()
_annotation_save_job = None
_annotation_save_thread = None

def _load_annotations_from_disk():
    global slide_annotations
    try:
        if os.path.exists(ANNOTATION_FILE_PATH):
            with open(ANNOTATION_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    slide_annotations[int(k)] = v
            gui_log_queue.put(f"[ANNOTATION] Loaded persisted annotations from disk: {len(slide_annotations)} slide(s)")
    except Exception as e:
        print(f"[ANNOTATION] Error loading annotations disk cache: {e}")

def _annotation_snapshot():
    """Copy the in-memory strokes while ANNOTATION_LOCK is held."""
    return {
        str(slide_index): [
            stroke.model_dump() if isinstance(stroke, BaseModel) else dict(stroke)
            for stroke in strokes
        ]
        for slide_index, strokes in slide_annotations.items()
    }


def _save_annotations_to_disk(path, serializable):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary_path = f"{path}.{os.getpid()}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, separators=(",", ":"))
        os.replace(temporary_path, path)
    except Exception as e:
        print(f"[ANNOTATION] Error persisting annotations to disk: {e}")


def _annotation_save_worker():
    global _annotation_save_job
    while True:
        _annotation_save_event.wait()
        with _annotation_save_lock:
            job = _annotation_save_job
            _annotation_save_job = None
            _annotation_save_event.clear()
        if job is not None:
            _save_annotations_to_disk(*job)


def _queue_annotation_save():
    """Queue the newest complete snapshot without disk I/O on the request path."""
    global _annotation_save_job, _annotation_save_thread
    snapshot = _annotation_snapshot()
    with _annotation_save_lock:
        _annotation_save_job = (ANNOTATION_FILE_PATH, snapshot)
        if _annotation_save_thread is None or not _annotation_save_thread.is_alive():
            _annotation_save_thread = threading.Thread(
                target=_annotation_save_worker, name="annotation-save", daemon=True
            )
            _annotation_save_thread.start()
        _annotation_save_event.set()

# Load initial annotations on script startup
with ANNOTATION_LOCK:
    _load_annotations_from_disk()

# --- ANNOTATION MODELS ---
class AnnotationPoint(BaseModel):
    x: float = Field(ge=0, le=10000, allow_inf_nan=False)
    y: float = Field(ge=0, le=10000, allow_inf_nan=False)
    pressure: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)

class AnnotationStroke(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    tool: str = Field(pattern=r"^(pen|highlighter|eraser|laser|arrow|rectangle|circle|text)$")
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    width: int = Field(ge=1, le=60)
    points: list[AnnotationPoint] = Field(min_length=2, max_length=10000)
    slide_index: int = Field(ge=0, le=9999)
    completed: bool = True
    text: Optional[str] = Field(default=None, min_length=1, max_length=160)
    font_size: Optional[int] = Field(default=None, ge=10, le=96)
    slide_aspect: Optional[float] = Field(default=None, gt=0.2, le=5.0, allow_inf_nan=False)
    # Normalized slide coordinates used to draw the stroke in PowerPoint.
    # `points` remain in controller-canvas pixels for rendering the phone UI.
    ppt_points: Optional[list[AnnotationPoint]] = None
    # Keep the source canvas dimensions so strokes created by older clients can
    # still be mapped onto the PowerPoint slide when ppt_points is absent.
    canvas_width: Optional[float] = Field(default=None, gt=0, le=20000, allow_inf_nan=False)
    canvas_height: Optional[float] = Field(default=None, gt=0, le=20000, allow_inf_nan=False)

class AnnotationRequest(AnnotationStroke):
    @model_validator(mode="before")
    @classmethod
    def accept_relay_payload(cls, value):
        if isinstance(value, dict):
            value = dict(value.get("stroke", value))
            if "stroke_id" in value and "id" not in value:
                value["id"] = value.pop("stroke_id")
        return value

    @model_validator(mode="after")
    def require_text_for_text_tool(self):
        if self.tool == "text" and not self.text:
            raise ValueError("Enter text before placing a text annotation")
        return self

class AnnotationPreviewRequest(AnnotationStroke):
    point_offset: int = Field(ge=0, le=10000)

    @model_validator(mode="before")
    @classmethod
    def accept_relay_payload(cls, value):
        if isinstance(value, dict):
            value = dict(value.get("stroke", value))
            if "stroke_id" in value and "id" not in value:
                value["id"] = value.pop("stroke_id")
        return value

    @model_validator(mode="after")
    def restrict_preview_tools(self):
        if self.tool not in {"pen", "highlighter"}:
            raise ValueError("Only pen and highlighter strokes support live preview")
        return self

class AnnotationPreviewCancelRequest(BaseModel):
    stroke_id: str = Field(min_length=1, max_length=128)
    slide_index: int = Field(ge=0, le=9999)

class AnnotationClearRequest(BaseModel):
    slide_index: int

class AnnotationUndoRequest(BaseModel):
    slide_index: int = Field(ge=0, le=9999)

# --- PRESENTATION CONTROLLER LAYER ---
class PresentationController:
    def __init__(self):
        self.use_com = False
        self.current_presentation_name = ""
        self.current_presentation_id = None
        self.connection_status = "PowerPoint has not been checked yet."
        self.thumbnail_revision = 0
        self._thumbnail_cache_key = None
        self._slideshow_hwnd = None
        self._sync_session = requests.Session()
        self._sync_lock = threading.Lock()
        self._sync_event = threading.Event()
        self._pending_sync = None
        self._notes_lock = threading.Lock()
        self._notes_event = threading.Event()
        self._pending_notes_slide = None
        # Start the idle worker during controller setup so slide navigation
        # never pays Python's thread-bootstrap cost on its first state update.
        self._sync_thread = threading.Thread(
            target=self._sync_worker, name="ppt-node-sync", daemon=True
        )
        self._sync_thread.start()
        self._notes_thread = threading.Thread(
            target=self._speaker_notes_worker, name="ppt-speaker-notes", daemon=True
        )
        self._notes_thread.start()
        if sys.platform == "win32":
            self.use_com = True
            gui_log_queue.put("[SYSTEM] PowerPoint COM Management Engine initialized.")
        else:
            gui_log_queue.put("[SYSTEM] Non-Windows OS detected. Absolute Keystroke Fallback mode active.")

    def _clear_active_presentation(self):
        had_active_presentation = self.current_presentation_id is not None
        self.current_presentation_name = ""
        self.current_presentation_id = None
        self._slideshow_hwnd = None
        if had_active_presentation:
            with _state_lock:
                current_slide_state["current"] = 1
                current_slide_state["max"] = 0
            self.sync_state_to_node()

    def check_connection(self, force_export=False):
        if not self.use_com:
            self._clear_active_presentation()
            self.connection_status = "PowerPoint integration is only available on Windows."
            return False, "Not Available (Non-Windows Environment)"
        com_initialized = False
        try:
            pythoncom.CoInitialize()
            com_initialized = True
            try:
                ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
            except Exception as exc:
                self._clear_active_presentation()
                self.connection_status = f"PowerPoint COM is unavailable: {str(exc)[:180]}"
                return False, self.connection_status

            if ppt_app.Presentations.Count == 0:
                self._clear_active_presentation()
                self.connection_status = "PowerPoint is connected, but no presentation is open."
                return False, self.connection_status

            active_pres = ppt_app.ActivePresentation
            pres_name = active_pres.Name
            try:
                presentation_path = active_pres.FullName
            except Exception:
                presentation_path = pres_name
            self.current_presentation_name = str(pres_name)
            self.current_presentation_id = hashlib.sha256(
                os.path.normcase(str(presentation_path)).encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            current_slide_state["max"] = max(1, active_pres.Slides.Count)

            self.export_deck_thumbnails(active_pres, force=force_export)

            try:
                ss_window = active_pres.SlideShowWindow
                current_pos = ss_window.View.CurrentShowPosition
                self._slideshow_hwnd = self._find_slideshow_hwnd()
                self._record_slide_position(current_pos)
                self.fetch_speaker_notes(active_pres, current_pos)
                self.sync_state_to_node()
                self.connection_status = f"Live Show Active ({pres_name})"
                return True, f"Live Show Active ({pres_name})"
            except Exception:
                self._slideshow_hwnd = None
                self.sync_state_to_node()
                self.connection_status = f"Edit Mode: {pres_name} (Press F5 first)"
                return False, f"Edit Mode: {pres_name} (Press F5 first)"

        except Exception as e:
            self.connection_status = f"PowerPoint connection failed: {str(e)[:180]}"
            return False, f"Bridge Error: {str(e)[:40]}"
        finally:
            if com_initialized:
                pythoncom.CoUninitialize()

    def sync_state_to_node(self):
        """Coalesce state pushes so navigation never waits for the Node server."""
        with _state_lock:
            payload = {
                "currentSlide": current_slide_state["current"],
                "totalSlides": current_slide_state["max"],
                "presentationName": self.current_presentation_name,
                "presentationId": self.current_presentation_id,
                "thumbnailRevision": self.thumbnail_revision,
            }
        with self._sync_lock:
            self._pending_sync = payload
            if self._sync_thread is None or not self._sync_thread.is_alive():
                self._sync_thread = threading.Thread(
                    target=self._sync_worker, name="ppt-node-sync", daemon=True
                )
                self._sync_thread.start()
            self._sync_event.set()

    def _sync_worker(self):
        while not stop_event.is_set():
            if not self._sync_event.wait(timeout=0.5):
                continue
            with self._sync_lock:
                payload = self._pending_sync
                self._pending_sync = None
                self._sync_event.clear()
            if payload is not None:
                self._push_state_to_node(payload)

    def _push_state_to_node(self, payload):
        """Push coalesced state with retries on the background sync worker."""

        # Try configured protocol first, then fall back to plain HTTP for local development
        urls_to_try = [f"{PROTOCOL}://127.0.0.1:{NODE_FRONTEND_PORT}/sync"]
        if PROTOCOL == "https":
            urls_to_try.append(f"http://127.0.0.1:{NODE_FRONTEND_PORT}/sync")

        last_exc = None
        for attempt in range(3):
            for url in urls_to_try:
                try:
                    start = time.perf_counter()
                    # State sync runs off the navigation path, so allow local
                    # TLS startup and a busy Node event loop a little room.
                    res = self._sync_session.post(
                        url, json=payload, timeout=(0.75, 1.5), verify=False
                    )
                    elapsed = int((time.perf_counter() - start) * 1000)
                    gui_log_queue.put(
                        f"[SYNC] Pushed state to Node @ {url} "
                        f"(status:{res.status_code}) in {elapsed}ms"
                    )
                    if res.ok or res.status_code < 500:
                        return
                    last_exc = RuntimeError(f"Node returned HTTP {res.status_code}")
                except Exception as exc:
                    last_exc = exc
                    gui_log_queue.put(
                        f"[SYNC] Attempt {attempt + 1} to {url} failed: {str(exc)[:200]}"
                    )
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))

        # All attempts failed
        gui_log_queue.put(
            f"[SYNC] Failed pushing state to Node UI after 3 attempts across "
            f"{len(urls_to_try)} URL(s). Last error: {str(last_exc)[:200]}"
        )

    def export_deck_thumbnails(self, active_presentation, force=False):
        try:
            slide_count = active_presentation.Slides.Count
            try:
                presentation_path = active_presentation.FullName
            except Exception:
                presentation_path = active_presentation.Name
            try:
                saved_at = str(active_presentation.LastSavedTime)
            except Exception:
                saved_at = None
            cache_key = (presentation_path, slide_count, saved_at)
            thumbnails_exist = all(
                os.path.isfile(os.path.join(NODE_THUMBNAILS_DIR, f"slide_{index:02d}.png"))
                for index in range(1, slide_count + 1)
            )
            if not force and cache_key == self._thumbnail_cache_key and thumbnails_exist:
                return False

            if os.path.exists(NODE_THUMBNAILS_DIR):
                for file in os.listdir(NODE_THUMBNAILS_DIR):
                    if file.startswith("slide_") and file.endswith(".png"):
                        try:
                            os.remove(os.path.join(NODE_THUMBNAILS_DIR, file))
                        except Exception:
                            pass

            gui_log_queue.put(f"[EXPORT] Extracting {slide_count} slides to asset ecosystem...")

            for idx, slide in enumerate(active_presentation.Slides):
                slide_num = idx + 1
                target_filename = f"slide_{slide_num:02d}.png"
                target_path = os.path.join(NODE_THUMBNAILS_DIR, target_filename)
                slide.Export(target_path, "PNG")

            gui_log_queue.put(f"[SUCCESS] Remote asset matrix built at: {NODE_THUMBNAILS_DIR}")
            self._thumbnail_cache_key = cache_key
            self.thumbnail_revision += 1
            return True

        except Exception as e:
            gui_log_queue.put(f"[ERROR] Thumbnail pipeline interrupted: {e}")
            return False

    def get_active_powerpoint(self):
        if not self.use_com:
            return None
        try:
            ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
            if ppt_app.Presentations.Count > 0:
                return ppt_app.ActivePresentation
        except Exception:
            return None
        return None

    def fetch_speaker_notes(self, ppt, slide_num):
        try:
            slide = ppt.Slides(slide_num)
            if slide.HasNotesPage:
                notes_text = slide.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text.strip()
                if notes_text:
                    gui_log_queue.put(f"[SPEAKER NOTES] Slide {slide_num}:\n{notes_text}\n---")
                else:
                    gui_log_queue.put(f"[SPEAKER NOTES] Slide {slide_num}: No notes provided.")
        except Exception:
            pass

    def _schedule_speaker_notes(self, slide_num):
        """Refresh notes off the navigation path, coalescing rapid slide changes."""
        with self._notes_lock:
            self._pending_notes_slide = slide_num
            self._notes_event.set()

    def _speaker_notes_worker(self):
        if sys.platform != "win32":
            return
        initialized = False
        try:
            pythoncom.CoInitialize()
            initialized = True
            while not stop_event.is_set():
                if not self._notes_event.wait(timeout=0.5):
                    continue
                # Let a rapid sequence settle so we only fetch the final slide's notes.
                time.sleep(0.05)
                with self._notes_lock:
                    slide_num = self._pending_notes_slide
                    self._pending_notes_slide = None
                    self._notes_event.clear()
                if slide_num is None:
                    continue
                presentation = self.get_active_powerpoint()
                if presentation:
                    try:
                        actual_slide = int(
                            presentation.SlideShowWindow.View.CurrentShowPosition
                        )
                    except Exception:
                        actual_slide = slide_num
                    self._record_slide_position(actual_slide)
                    if actual_slide != slide_num:
                        self.sync_state_to_node()
                    self.fetch_speaker_notes(presentation, actual_slide)
        except Exception as exc:
            gui_log_queue.put(f"[SPEAKER NOTES] Background refresh stopped: {exc}")
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    def _find_slideshow_hwnd(self):
        """Find the real visible slideshow HWND; SlideShowWindow has no HWND property."""
        if sys.platform != "win32":
            return None
        candidates = []

        def collect(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if "powerpoint slide show" in title.lower():
                candidates.append((hwnd, title))
            return True

        try:
            win32gui.EnumWindows(collect, None)
        except Exception:
            return None

        presentation_name = self.current_presentation_name.casefold()
        matching = [
            hwnd for hwnd, title in candidates
            if presentation_name and presentation_name in title.casefold()
        ]
        if len(matching) == 1:
            return matching[0]
        if len(candidates) == 1:
            return candidates[0][0]
        return None

    def _post_slide_navigation(self, direction):
        """Queue a navigation key to PowerPoint and return without waiting on COM."""
        hwnd = self._slideshow_hwnd
        if not hwnd or not win32gui.IsWindow(hwnd):
            self._slideshow_hwnd = None
            return None

        with _state_lock:
            current = current_slide_state["current"]
            maximum = current_slide_state["max"]
            target = min(maximum, current + 1) if direction == "next" else max(1, current - 1)
        if target == current:
            return current

        virtual_key = 0x27 if direction == "next" else 0x25  # VK_RIGHT / VK_LEFT
        keydown = 0x0100
        keyup = 0x0101
        try:
            # PostMessage returns immediately; PowerPoint processes the key in its own UI loop.
            down_posted = win32gui.PostMessage(hwnd, keydown, virtual_key, 1)
            up_posted = win32gui.PostMessage(hwnd, keyup, virtual_key, 0xC0000001)
            if down_posted is False or up_posted is False:
                raise RuntimeError("PowerPoint rejected a posted key message")
        except Exception as exc:
            gui_log_queue.put(f"[EXECUTE] Posted navigation failed, using COM fallback: {exc}")
            self._slideshow_hwnd = None
            return None

        self._record_slide_position(target)
        self._schedule_speaker_notes(target)
        self.sync_state_to_node()
        label = "Next" if direction == "next" else "Previous"
        gui_log_queue.put(f"[EXECUTE] Native Win32 PostMessage: {label} Slide ({target})")
        return target

    def force_focus_powerpoint(self):
        """Brings the live slide show window explicitly to the foreground to safeguard keystrokes."""
        if not self.use_com:
            return
        try:
            def window_enumeration_handler(hwnd, extra):
                title = win32gui.GetWindowText(hwnd)
                if "PowerPoint Slide Show" in title:
                    win32gui.SetForegroundWindow(hwnd)
            win32gui.EnumWindows(window_enumeration_handler, None)
        except Exception:
            pass

    def _record_slide_position(self, slide_num):
        if isinstance(slide_num, int) and slide_num > 0:
            with _state_lock:
                current_slide_state["current"] = slide_num

    def refresh_slideshow_position(self):
        """Read the live slide position so external PowerPoint navigation stays in sync."""
        if not self.use_com or sys.platform != "win32":
            return None

        initialized = False
        try:
            pythoncom.CoInitialize()
            initialized = True
            ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
            if ppt_app.Presentations.Count == 0 or ppt_app.SlideShowWindows.Count == 0:
                return None

            active_presentation = ppt_app.ActivePresentation
            try:
                active_path = active_presentation.FullName
            except Exception:
                active_path = active_presentation.Name
            active_id = hashlib.sha256(
                os.path.normcase(str(active_path)).encode("utf-8", errors="replace")
            ).hexdigest()[:16]

            # If the user switched presentations in PowerPoint, refresh the deck
            # identity and thumbnail state before looking for its slide show.
            if active_id != self.current_presentation_id:
                self.check_connection(force_export=False)

            target_id = self.current_presentation_id
            current_show_position = None
            for index in range(1, ppt_app.SlideShowWindows.Count + 1):
                show_window = ppt_app.SlideShowWindows(index)
                presentation = show_window.Presentation
                try:
                    path = presentation.FullName
                except Exception:
                    path = presentation.Name
                presentation_id = hashlib.sha256(
                    os.path.normcase(str(path)).encode("utf-8", errors="replace")
                ).hexdigest()[:16]
                if presentation_id == target_id:
                    current_show_position = int(show_window.View.CurrentShowPosition)
                    break

            if current_show_position is None or current_show_position < 1:
                return None

            with _state_lock:
                previous_position = current_slide_state["current"]
            if current_show_position != previous_position:
                self._record_slide_position(current_show_position)
                self._schedule_speaker_notes(current_show_position)
                self.sync_state_to_node()
                gui_log_queue.put(
                    f"[SYNC] PowerPoint slide position refreshed: {current_show_position}"
                )
            return current_show_position
        except Exception:
            # PowerPoint can briefly reject COM reads during slide transitions.
            # The next monitor tick or annotation request will retry.
            return None
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    def move_next(self):
        if self._slideshow_hwnd:
            if self._post_slide_navigation("next") is not None:
                return
        ppt = self.get_active_powerpoint()
        if ppt:
            try:
                ppt.SlideShowWindow.View.Next()
                pos = ppt.SlideShowWindow.View.CurrentShowPosition
                self._record_slide_position(pos)
                self._schedule_speaker_notes(pos)
                self.sync_state_to_node()
                gui_log_queue.put(f"[EXECUTE] Native COM: Next Slide ({pos})")
                return
            except Exception:
                pass

        self.force_focus_powerpoint()
        pyautogui.press('right')
        with _state_lock:
            cur_slide = current_slide_state["current"]
            max_slide = current_slide_state["max"]
        self._record_slide_position(min(max_slide, cur_slide + 1))
        self.sync_state_to_node()
        gui_log_queue.put("[EXECUTE] Keyboard Fallback: Sent [Right Arrow]")

    def move_previous(self):
        if self._slideshow_hwnd:
            if self._post_slide_navigation("previous") is not None:
                return
        ppt = self.get_active_powerpoint()
        if ppt:
            try:
                ppt.SlideShowWindow.View.Previous()
                pos = ppt.SlideShowWindow.View.CurrentShowPosition
                self._record_slide_position(pos)
                self._schedule_speaker_notes(pos)
                self.sync_state_to_node()
                gui_log_queue.put(f"[EXECUTE] Native COM: Previous Slide ({pos})")
                return
            except Exception:
                pass

        self.force_focus_powerpoint()
        pyautogui.press('left')
        with _state_lock:
            cur_slide = current_slide_state["current"]
        self._record_slide_position(max(1, cur_slide - 1))
        self.sync_state_to_node()
        gui_log_queue.put("[EXECUTE] Keyboard Fallback: Sent [Left Arrow]")

    def jump_to_slide(self, slide_num):
        if not isinstance(slide_num, int) or slide_num < 1:
            return

        with _state_lock:
            max_slide = current_slide_state["max"]

        if slide_num > max_slide:
            slide_num = max_slide

        ppt = self.get_active_powerpoint()
        if ppt:
            try:
                ppt.SlideShowWindow.View.GotoSlide(slide_num)
                self._record_slide_position(slide_num)
                self.fetch_speaker_notes(ppt, slide_num)
                self.sync_state_to_node()
                gui_log_queue.put(f"[EXECUTE] Native COM: Jumped to Slide {slide_num}")
                return
            except Exception:
                pass

        self.force_focus_powerpoint()
        pyautogui.typewrite(str(slide_num), interval=0.02)
        pyautogui.press('enter')
        self._record_slide_position(slide_num)
        self.sync_state_to_node()
        gui_log_queue.put(f"[EXECUTE] Keyboard Fallback: Keyed Jump sequence to Slide {slide_num}")

    def toggle_blackout(self):
        """Toggles a black screen over the active presentation to focus audience attention."""
        ppt = self.get_active_powerpoint()
        if ppt:
            try:
                state = ppt.SlideShowWindow.View.State
                ppt.SlideShowWindow.View.State = 3 if state != 3 else 1
                gui_log_queue.put("[EXECUTE] Native COM: Toggled Presentation Blackout View state.")
                return
            except Exception:
                pass
        self.force_focus_powerpoint()
        pyautogui.press('b')
        gui_log_queue.put("[EXECUTE] Keyboard Fallback: Emitted Blackout Keystroke [B]")

    def toggle_laser(self):
        """Toggles the presentation hardware laser pointer interface pointer overlay."""
        self.force_focus_powerpoint()
        pyautogui.hotkey('ctrl', 'l')
        gui_log_queue.put("[EXECUTE] Emitted Hotkey Combo: Native Laser Pointer [Ctrl + L]")

hardware_controller = PresentationController()

# --- INTENT PROCESSING ENGINE ---
def extract_slide_target(text: str):
    match = re.search(r"\b(?:slide|goto|go to|jump to|page|page number)\s+(\d+)\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\b", text)
    if re.search(r"\b(next|forward|continue|siguiente|ahead|skip|back|previous|return|anterior|behind)\b", text):
        return None
    return int(match.group(1)) if match else None

from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4)

def process_text_intent(text: str):
    clean_text = text.lower().strip()
    if not clean_text:
        return

    gui_log_queue.put(f"[VOICE AI] Stream Processing: \"{text}\"")

    target_slide = extract_slide_target(clean_text)
    if target_slide is not None:
        action_queue.put({"command": "JUMP", "target": target_slide, "origin": "Voice"})
        return

    if re.search(r"\b(next|forward|continue|siguiente|ahead|skip)\b", clean_text):
        action_queue.put({"command": "NEXT", "target": None, "origin": "Voice"})
        return
    if re.search(r"\b(back|previous|return|anterior|behind)\b", clean_text):
        action_queue.put({"command": "PREVIOUS", "target": None, "origin": "Voice"})
        return
    if re.search(r"\b(blackout|blank|dark|hide presentation)\b", clean_text):
        action_queue.put({"command": "BLACKOUT", "target": None, "origin": "Voice"})
        return

    executor.submit(_perform_ai_inference, text)

def _perform_ai_inference(text: str):
    api_key = _optional_provider_credential("GEMMA_API_KEY")
    if not api_key:
        gui_log_queue.put(
            "[GEMMA] Voice intent inference unavailable: GEMMA_API_KEY is not configured."
        )
        return
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    system_prompt = (
        "You are an interface router component. Interpret semantic intents into actions: "
        "'NEXT', 'PREVIOUS', 'BLACKOUT', or 'JUMP'. Return valid JSON only: {\"action\": \"STR\", \"target\": INT_OR_NULL}"
    )
    payload = {
        "model": "google/gemma-3n-e2b-it",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Analyze: '{text}'"}
        ],
        "max_tokens": 50,
        "temperature": 0.1
    }

    try:
        start = time.time()
        res = requests.post(GEMMA_URL, headers=headers, json=payload, timeout=3.5)
        elapsed_ms = int((time.time() - start) * 1000)
        if res.status_code == 200:
            ai_response = res.json()['choices'][0]['message']['content'].strip()
            ai_response = re.sub(r"```json|```", "", ai_response).strip()
            gui_log_queue.put(f"[GEMMA] status:{res.status_code} latency:{elapsed_ms}ms preview:{ai_response[:120]}")

            # Sanitize common non-JSON tokens the model may emit (e.g., NULL, TRUE/FALSE, None)
            sanitized = re.sub(r"(?<!\")\b(NULL|None)\b(?!\")", 'null', ai_response, flags=re.IGNORECASE)
            sanitized = re.sub(r"(?<!\")\b(True|TRUE|False|FALSE)\b(?!\")", lambda m: m.group(1).lower(), sanitized, flags=re.IGNORECASE)

            try:
                data = json.loads(sanitized)
            except Exception:
                try:
                    fallback = sanitized.replace('null', 'None').replace('true', 'True').replace('false', 'False')
                    import ast
                    data = ast.literal_eval(fallback)
                except Exception:
                    data = {}

            action = data.get("action") if isinstance(data, dict) else None
            target = data.get("target") if isinstance(data, dict) else None

            if action in ["NEXT", "PREVIOUS", "BLACKOUT"]:
                action_queue.put({"command": action, "target": None, "origin": "Neural Parser"})
            elif action == "JUMP" and target:
                try:
                    action_queue.put({"command": "JUMP", "target": int(target), "origin": "Neural Parser"})
                except Exception:
                    pass
    except Exception as e:
        gui_log_queue.put(f"[GEMMA] Request failed: {str(e)[:200]}")
        print(f"[GEMMA] Request failed: {e}")

# --- FASTAPI NETWORK BACKEND ---
server_app = FastAPI()
PPT_CONTROLLER_API_VERSION = 2
PPT_CONTROLLER_NODE_VERSION = 2


@server_app.get("/controller/health", include_in_schema=False)
async def controller_health():
    """Identify this API build so launchers never attach to a stale worker."""
    return {"status": "ok", "api_version": PPT_CONTROLLER_API_VERSION}

class CommandModel(BaseModel):
    command: str
    target: Optional[int] = None

@server_app.post("/command")
async def receive_node_router_command(payload: CommandModel):
    cmd = payload.command.upper()
    target = payload.target

    gui_log_queue.put(f"[NETWORK LINK] Node.js Interface Request Received: {cmd} (Target: {target})")
    start = time.time()

    # Backend still fully supports all commands for future HTML updates
    if cmd in ["NEXT", "PREVIOUS", "BLACKOUT", "LASER"]:
        action_queue.put({"command": cmd, "target": None, "origin": "HTTP Uplink", "queued_at": time.perf_counter()})
    elif cmd == "JUMP" and target is not None:
        action_queue.put({"command": "JUMP", "target": target, "origin": "HTTP Uplink", "queued_at": time.perf_counter()})
    elif cmd == "SYNC_COMPLETE":
        return {"status": "assets_synchronized"}
    else:
        raise HTTPException(status_code=400, detail="Malformed structure validation dropped.")

    elapsed_ms = int((time.time() - start) * 1000)
    gui_log_queue.put(f"[NETWORK LINK] Processed {cmd} in {elapsed_ms}ms")
    print(f"[NETWORK LINK] /command {cmd} processed in {elapsed_ms}ms")

    return {"status": "action_queued", "received": True, "latency_ms": elapsed_ms}

# --- ACTIVE PRESENTATION / DECK SETUP ---
def _active_deck_snapshot():
    deck_id = hardware_controller.current_presentation_id
    if not deck_id:
        return None
    with _state_lock:
        current_slide = current_slide_state["current"]
        total_slides = current_slide_state["max"]
    return {
        "id": deck_id,
        "name": hardware_controller.current_presentation_name or "Untitled presentation",
        "current_slide": current_slide,
        "total_slides": total_slides,
        "is_active": True,
    }


def _slide_aspect(stroke):
    try:
        return min(5.0, max(0.2, float(stroke.get("slide_aspect") or (16 / 9))))
    except (TypeError, ValueError):
        return 16 / 9


def _normalized_stroke_points(stroke):
    points = stroke.get("ppt_points") or []
    if points:
        return [
            (min(1.0, max(0.0, float(point["x"]))), min(1.0, max(0.0, float(point["y"]))))
            for point in points
        ]

    points = stroke.get("points") or []
    if not points:
        return []
    canvas_width = max(1.0, float(stroke.get("canvas_width") or 1000))
    canvas_height = max(1.0, float(stroke.get("canvas_height") or 562))
    aspect = _slide_aspect(stroke)
    display_width = min(canvas_width, canvas_height * aspect)
    display_height = display_width / aspect
    offset_x = (canvas_width - display_width) / 2
    offset_y = (canvas_height - display_height) / 2
    return [
        (
            min(1.0, max(0.0, (float(point["x"]) - offset_x) / display_width)),
            min(1.0, max(0.0, (float(point["y"]) - offset_y) / display_height)),
        )
        for point in points
    ]


def _distance_to_segment(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared))
    return math.hypot(point[0] - (start[0] + t * dx), point[1] - (start[1] + t * dy))


def _orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_distance(a1, a2, b1, b2):
    o1, o2 = _orientation(a1, a2, b1), _orientation(a1, a2, b2)
    o3, o4 = _orientation(b1, b2, a1), _orientation(b1, b2, a2)
    def on_segment(start, point, end):
        return (min(start[0], end[0]) - 1e-12 <= point[0] <= max(start[0], end[0]) + 1e-12 and
                min(start[1], end[1]) - 1e-12 <= point[1] <= max(start[1], end[1]) + 1e-12)

    if (o1 * o2 < 0 and o3 * o4 < 0) or \
            (abs(o1) <= 1e-12 and on_segment(a1, b1, a2)) or \
            (abs(o2) <= 1e-12 and on_segment(a1, b2, a2)) or \
            (abs(o3) <= 1e-12 and on_segment(b1, a1, b2)) or \
            (abs(o4) <= 1e-12 and on_segment(b1, a2, b2)):
        return 0.0
    return min(
        _distance_to_segment(a1, b1, b2),
        _distance_to_segment(a2, b1, b2),
        _distance_to_segment(b1, a1, a2),
        _distance_to_segment(b2, a1, a2),
    )


def _stroke_outline(stroke):
    """Return approximate normalized outline points for a non-freehand mark."""
    points = _normalized_stroke_points(stroke)
    if len(points) < 2:
        return points
    aspect = _slide_aspect(stroke)
    metric = [(x, y / aspect) for x, y in points]
    tool = stroke.get("tool")
    if tool == "arrow":
        start, end = metric[0], metric[-1]
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        head = 0.025
        return [start, end,
                (end[0] - head * math.cos(angle - math.pi / 6), end[1] - head * math.sin(angle - math.pi / 6)),
                end,
                (end[0] - head * math.cos(angle + math.pi / 6), end[1] - head * math.sin(angle + math.pi / 6))]
    if tool == "rectangle":
        x1, y1 = metric[0]
        x2, y2 = metric[-1]
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    if tool == "circle":
        center = metric[0]
        radius = math.hypot(metric[-1][0] - center[0], metric[-1][1] - center[1])
        return [(center[0] + radius * math.cos(i * math.tau / 48),
                 center[1] + radius * math.sin(i * math.tau / 48)) for i in range(49)]
    if tool == "text":
        anchor_x, anchor_y = metric[0]
        font_px = float(stroke.get("font_size") or float(stroke.get("width") or 3) * 8)
        scale_width = _stroke_scale_width(stroke, aspect)
        text_width = min(1.0, max(0.01, len(str(stroke.get("text") or "")) * font_px * 0.58 / scale_width))
        text_height = min(0.5, max(0.01, font_px / scale_width))
        return [(anchor_x, anchor_y), (anchor_x + text_width, anchor_y),
                (anchor_x + text_width, anchor_y + text_height),
                (anchor_x, anchor_y + text_height), (anchor_x, anchor_y)]
    return metric


def _stroke_scale_width(stroke, aspect):
    canvas_width = max(1.0, float(stroke.get("canvas_width") or 1000))
    canvas_height = max(1.0, float(stroke.get("canvas_height") or canvas_width / aspect))
    return max(1.0, min(canvas_width, canvas_height * aspect))


def _path_hits(path, eraser_path, radius):
    if not path or not eraser_path:
        return False
    path_segments = list(zip(path, path[1:])) or [(path[0], path[0])]
    eraser_segments = list(zip(eraser_path, eraser_path[1:])) or [(eraser_path[0], eraser_path[0])]
    return any(_segments_distance(a, b, c, d) <= radius
               for a, b in path_segments for c, d in eraser_segments)


def _fragment_from_normalized(stroke, points, index, eraser_id):
    fragment = dict(stroke)
    aspect = _slide_aspect(stroke)
    canvas_width = max(1.0, float(stroke.get("canvas_width") or 1000))
    canvas_height = max(1.0, float(stroke.get("canvas_height") or canvas_width / aspect))
    display_width = min(canvas_width, canvas_height * aspect)
    display_height = display_width / aspect
    offset_x = (canvas_width - display_width) / 2
    offset_y = (canvas_height - display_height) / 2
    fragment["id"] = f"{str(stroke.get('id') or 'stroke')[:70]}:erase:{str(eraser_id)[:32]}:{index}"
    fragment["slide_aspect"] = aspect
    fragment["ppt_points"] = [{"x": x, "y": y, "pressure": 1.0} for x, y in points]
    fragment["points"] = [
        {"x": offset_x + x * display_width, "y": offset_y + y * display_height, "pressure": 1.0}
        for x, y in points
    ]
    return fragment


def _erase_freehand_stroke(stroke, eraser):
    aspect = _slide_aspect(stroke)
    stroke_points = _normalized_stroke_points(stroke)
    eraser_points = _normalized_stroke_points(eraser)
    if len(stroke_points) < 2 or not eraser_points:
        return [stroke]

    # Use slide-width units so the eraser stays circular on 16:9 and 4:3 decks.
    target_path = [(x, y / aspect) for x, y in stroke_points]
    eraser_path = [(x, y / aspect) for x, y in eraser_points]
    target_scale = _stroke_scale_width(stroke, aspect)
    eraser_scale = _stroke_scale_width(eraser, aspect)
    radius = (float(eraser.get("width") or 9) / eraser_scale +
              float(stroke.get("width") or 3) / target_scale) / 2
    radius += 1.5 / min(target_scale, eraser_scale)
    sample_spacing = max(0.0012, radius * 0.35)

    chunks = []
    current = []
    changed = False
    for start, end in zip(target_path, target_path[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        count = min(128, max(1, int(math.ceil(length / sample_spacing))))
        for step in range(count + 1):
            t = step / count
            point = (start[0] + (end[0] - start[0]) * t,
                     start[1] + (end[1] - start[1]) * t)
            erased = _path_hits([point], eraser_path, radius)
            if erased:
                changed = True
                if len(current) >= 2:
                    chunks.append(current)
                current = []
            else:
                normalized = (point[0], point[1] * aspect)
                if not current or math.hypot(normalized[0] - current[-1][0], normalized[1] - current[-1][1]) > 1e-6:
                    current.append(normalized)
    if len(current) >= 2:
        chunks.append(current)
    if not changed:
        return [stroke]
    eraser_id = eraser.get("id") or "eraser"
    return [_fragment_from_normalized(stroke, chunk, index, eraser_id)
            for index, chunk in enumerate(chunks)]


def _effective_annotation_strokes(strokes):
    """Apply saved eraser paths to earlier marks without mutating undo history."""
    if not any(stroke.get("tool") == "eraser" for stroke in strokes):
        return list(strokes)
    effective = []
    for stroke in strokes:
        if stroke.get("tool") != "eraser":
            effective.append(stroke)
            continue
        aspect = _slide_aspect(stroke)
        eraser_path = [(x, y / aspect) for x, y in _normalized_stroke_points(stroke)]
        if not eraser_path:
            continue
        remaining = []
        for previous in effective:
            if previous.get("tool") in {"pen", "highlighter"}:
                remaining.extend(_erase_freehand_stroke(previous, stroke))
                continue
            outline = _stroke_outline(previous)
            target_aspect = _slide_aspect(previous)
            target_scale = _stroke_scale_width(previous, target_aspect)
            eraser_scale = _stroke_scale_width(stroke, aspect)
            radius = (float(stroke.get("width") or 9) / eraser_scale +
                      float(previous.get("width") or 3) / target_scale) / 2
            radius += 1.5 / min(target_scale, eraser_scale)
            crosses_text_box = False
            if previous.get("tool") == "text" and outline:
                left, right = min(point[0] for point in outline), max(point[0] for point in outline)
                top, bottom = min(point[1] for point in outline), max(point[1] for point in outline)
                crosses_text_box = any(left - radius <= x <= right + radius and
                                       top - radius <= y <= bottom + radius
                                       for x, y in eraser_path)
            if not crosses_text_box and not _path_hits(outline, eraser_path, radius):
                remaining.append(previous)
        effective = remaining
    return effective


def _simplify_annotation_points(points, tolerance):
    if len(points) <= 2:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        distance_max = tolerance
        index = None
        start, end = points[first], points[last]
        for candidate in range(first + 1, last):
            distance = _distance_to_segment(points[candidate], start, end)
            if distance > distance_max:
                index, distance_max = candidate, distance
        if index is not None:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    simplified = [point for index, point in enumerate(points) if keep[index]]
    if len(simplified) > 180:
        simplified = [simplified[round(i * (len(simplified) - 1) / 179)] for i in range(180)]
    return simplified


def _text_line_segments(stroke, slide_width, slide_height):
    text = str(stroke.get("text") or "").strip()
    points = _normalized_stroke_points(stroke)
    if not text or not points:
        return []
    aspect = _slide_aspect(stroke)
    canvas_width = max(1.0, float(stroke.get("canvas_width") or 1000))
    canvas_height = max(1.0, float(stroke.get("canvas_height") or canvas_width / aspect))
    displayed_width = min(canvas_width, canvas_height * aspect)
    pixels_per_point = displayed_width / slide_width
    font_css_px = int(min(96, max(12, stroke.get("font_size") or float(stroke.get("width") or 3) * 8)))
    supersample = 2
    font_px = font_css_px * supersample
    try:
        font = ImageFont.truetype("arial.ttf", font_px)
    except OSError:
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", font_px)
        except OSError:
            font = ImageFont.load_default()
    bounds = font.getbbox(text)
    image_width = min(8192, max(1, math.ceil(bounds[2] - bounds[0] + 4)))
    image_height = min(512, max(1, math.ceil(bounds[3] - bounds[1] + 4)))
    mask = Image.new("L", (image_width, image_height), 0)
    ImageDraw.Draw(mask).text((-bounds[0] + 2, -bounds[1] + 2), text, fill=255, font=font)
    pixels = mask.load()
    x0, y0 = points[0][0] * slide_width, points[0][1] * slide_height
    px_to_points = 1.0 / max(0.01, pixels_per_point * supersample)
    segments = []
    for y in range(image_height):
        x = 0
        while x < image_width:
            while x < image_width and pixels[x, y] < 100:
                x += 1
            start = x
            while x < image_width and pixels[x, y] >= 100:
                x += 1
            if x - start >= 2:
                segments.append(((x0 + start * px_to_points, y0 + y * px_to_points),
                                 (x0 + (x - 1) * px_to_points, y0 + y * px_to_points)))
    return segments


_ppt_rendered_annotation_ids = defaultdict(set)
_ppt_render_lock = threading.RLock()


def _draw_stroke_in_slideshow(view, stroke, slide_width, slide_height):
    """Render one colored, width-aware stroke as temporary slideshow ink."""
    tool = stroke.get("tool", "pen")
    if tool in {"eraser", "laser"}:
        return 0

    if tool == "text":
        segments = _text_line_segments(stroke, slide_width, slide_height)
    else:
        normalized = _normalized_stroke_points(stroke)
        if len(normalized) < 2:
            return 0
        if tool == "arrow":
            start, end = normalized[0], normalized[-1]
            dx, dy = end[0] - start[0], end[1] - start[1]
            angle = math.atan2(dy, dx)
            head = 0.025
            path = [start, end,
                    (end[0] - head * math.cos(angle - math.pi / 6), end[1] - head * math.sin(angle - math.pi / 6)),
                    end,
                    (end[0] - head * math.cos(angle + math.pi / 6), end[1] - head * math.sin(angle + math.pi / 6))]
        elif tool in {"rectangle", "circle"}:
            path = _stroke_outline(stroke)
            aspect = _slide_aspect(stroke)
            path = [(x, y * aspect) for x, y in path]
        else:
            aspect = _slide_aspect(stroke)
            metric_points = [(x, y / aspect) for x, y in normalized]
            tolerance = max(0.0005, min(0.02, float(stroke.get("width") or 3) * 0.5 /
                                         _stroke_scale_width(stroke, aspect)))
            path = [(x, y * aspect) for x, y in _simplify_annotation_points(metric_points, tolerance)]
        segments = [((start[0] * slide_width, start[1] * slide_height),
                     (end[0] * slide_width, end[1] * slide_height))
                    for start, end in zip(path, path[1:])]

    color = str(stroke.get("color") or "#FF3B30").lstrip("#")
    if len(color) != 6:
        color = "FF3B30"
    red, green, blue = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    color_ref = red | (green << 8) | (blue << 16)
    # PowerPoint resets the pointer color when switching into pen mode, so set
    # the selected color first. This helper is kept for older callers; the
    # annotation renderer below uses editable slide shapes for exact width.
    view.PointerColor.RGB = color_ref
    view.PointerType = 2  # ppSlideShowPointerPen

    # DrawLine has no width argument. Parallel pen lines approximate the
    # selected CSS-pixel width after mapping it to PowerPoint's point units.
    aspect = _slide_aspect(stroke)
    displayed_width = _stroke_scale_width(stroke, aspect)
    points_per_css_pixel = slide_width / displayed_width
    requested_width_points = max(1.0, float(stroke.get("width") or 3) * points_per_css_pixel)
    parallel_lines = min(24, max(1, int(math.ceil(requested_width_points / 2.0))))
    drawn = 0
    for start, end in segments:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 0.1:
            continue
        normal_x, normal_y = -dy / length, dx / length
        if parallel_lines == 1:
            offsets = (0.0,)
        else:
            spacing = requested_width_points / (parallel_lines - 1)
            offsets = ((index - (parallel_lines - 1) / 2) * spacing
                       for index in range(parallel_lines))
        for offset in offsets:
            view.DrawLine(
                start[0] + normal_x * offset,
                start[1] + normal_y * offset,
                end[0] + normal_x * offset,
                end[1] + normal_y * offset,
            )
            drawn += 1
    return drawn


_PPT_ANNOTATION_TAG = "PPTControllerAnnotation:"
_PPT_PREVIEW_TAG = "PPTControllerPreview:"
_ppt_annotation_previews = {}


def _ole_rgb(color):
    value = str(color or "#FF3B30").lstrip("#")
    if len(value) != 6:
        value = "FF3B30"
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return red | (green << 8) | (blue << 16)


def _annotation_shape_tag(stroke_id, preview=False, chunk=None):
    prefix = _PPT_PREVIEW_TAG if preview else _PPT_ANNOTATION_TAG
    tag = prefix + str(stroke_id or "unknown")
    return f"{tag}:{chunk}" if preview and chunk is not None else tag


def _shape_collection_item(shapes, index):
    try:
        return shapes.Item(index)
    except (AttributeError, TypeError):
        return shapes(index)


def _delete_controller_shapes(shapes, tag_prefix):
    """Delete only shapes created by this controller, preserving user content."""
    removed = 0
    for index in range(int(shapes.Count), 0, -1):
        try:
            shape = _shape_collection_item(shapes, index)
            if str(getattr(shape, "AlternativeText", "")).startswith(tag_prefix):
                shape.Delete()
                removed += 1
        except Exception:
            continue
    return removed


def _annotation_slide_path(stroke, slide_width, slide_height):
    normalized = _normalized_stroke_points(stroke)
    if len(normalized) < 2:
        return []
    tool = stroke.get("tool", "pen")
    aspect = _slide_aspect(stroke)
    if tool in {"arrow", "rectangle", "circle"}:
        metric_path = _stroke_outline(stroke)
        return [(x * slide_width, y * aspect * slide_height) for x, y in metric_path]
    metric_points = [(x, y / aspect) for x, y in normalized]
    tolerance = max(
        0.0005,
        min(0.02, float(stroke.get("width") or 3) * 0.5 /
            _stroke_scale_width(stroke, aspect)),
    )
    simplified = _simplify_annotation_points(metric_points, tolerance)
    return [(x * slide_width, y * aspect * slide_height) for x, y in simplified]


def _add_controller_polyline(shapes, points, stroke, slide_width, preview=False, chunk=None):
    if len(points) < 2:
        return None
    coordinate_pairs = tuple((float(x), float(y)) for x, y in points)
    if sys.platform == "win32":
        # PowerPoint's COM signature requires a two-dimensional SAFEARRAY of
        # Singles. A plain tuple-of-tuples is rejected as the wrong VARIANT.
        coordinate_pairs = win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R4, coordinate_pairs
        )
    shape = shapes.AddPolyline(coordinate_pairs)
    line = shape.Line
    line.ForeColor.RGB = _ole_rgb(stroke.get("color"))
    # Do not inherit arrowhead styling from PowerPoint's current theme/defaults.
    line.BeginArrowheadStyle = 1  # msoArrowheadNone
    line.EndArrowheadStyle = 1    # msoArrowheadNone
    aspect = _slide_aspect(stroke)
    points_per_css_pixel = slide_width / _stroke_scale_width(stroke, aspect)
    line.Weight = max(1.0, float(stroke.get("width") or 3) * points_per_css_pixel)
    if stroke.get("tool") == "highlighter":
        line.Transparency = 0.55
    stroke_id = stroke.get("id") or stroke.get("stroke_id")
    shape.AlternativeText = _annotation_shape_tag(stroke_id, preview, chunk)
    return shape


def _add_controller_textbox(shapes, stroke, slide_width, slide_height):
    text = str(stroke.get("text") or "").strip()
    normalized = _normalized_stroke_points(stroke)
    if not text or not normalized:
        return None
    aspect = _slide_aspect(stroke)
    left = normalized[0][0] * slide_width
    top = normalized[0][1] * slide_height
    css_to_points = slide_width / _stroke_scale_width(stroke, aspect)
    font_size = max(8.0, float(stroke.get("font_size") or 24) * css_to_points)
    width = min(max(1.0, slide_width - left), max(font_size * 2, len(text) * font_size * 0.7))
    height = min(max(1.0, slide_height - top), font_size * 1.5)
    shape = shapes.AddTextbox(1, left, top, width, height)
    shape.TextFrame.MarginLeft = 0
    shape.TextFrame.MarginRight = 0
    shape.TextFrame.MarginTop = 0
    shape.TextFrame.MarginBottom = 0
    shape.TextFrame.WordWrap = False
    shape.TextFrame.TextRange.Text = text
    shape.TextFrame.TextRange.Font.Name = "Arial"
    shape.TextFrame.TextRange.Font.Size = font_size
    shape.TextFrame.TextRange.Font.Color.RGB = _ole_rgb(stroke.get("color"))
    shape.Fill.Visible = 0
    shape.Line.Visible = 0
    shape.AlternativeText = _annotation_shape_tag(stroke.get("id") or stroke.get("stroke_id"))
    return shape


def _draw_controller_annotation(shapes, stroke, slide_width, slide_height,
                                preview=False, chunk=None, points_override=None):
    """Create one editable PowerPoint object with the phone's chosen style."""
    tool = stroke.get("tool", "pen")
    if tool in {"eraser", "laser"}:
        return None
    if tool == "text":
        return _add_controller_textbox(shapes, stroke, slide_width, slide_height)
    if points_override is None:
        points = _annotation_slide_path(stroke, slide_width, slide_height)
    else:
        points = [(float(x) * slide_width, float(y) * slide_height) for x, y in points_override]
    return _add_controller_polyline(shapes, points, stroke, slide_width, preview, chunk)


def _matching_slideshow_presentation(slide_index, require_current_slide=True):
    app = win32com.client.GetActiveObject("PowerPoint.Application")
    target_id = hardware_controller.current_presentation_id
    for index in range(1, app.SlideShowWindows.Count + 1):
        window = app.SlideShowWindows(index)
        presentation = window.Presentation
        try:
            path = presentation.FullName
        except Exception:
            path = presentation.Name
        presentation_id = hashlib.sha256(
            os.path.normcase(str(path)).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        if not target_id or presentation_id != target_id:
            continue
        if require_current_slide and int(window.View.CurrentShowPosition) != slide_index + 1:
            return None
        return window, presentation
    return None


def _render_slideshow_annotation_preview(stroke, point_offset):
    """Project the latest pen/highlighter segment while the user is drawing."""
    if sys.platform != "win32" or not hardware_controller.use_com:
        return False
    initialized = False
    try:
        with _ppt_render_lock:
            pythoncom.CoInitialize()
            initialized = True
            target = _matching_slideshow_presentation(stroke["slide_index"])
            if target is None:
                return False
            _, presentation = target
            slide = presentation.Slides(stroke["slide_index"] + 1)
            shapes = slide.Shapes
            stroke_id = stroke.get("id") or stroke.get("stroke_id")
            key = (hardware_controller.current_presentation_id, stroke["slide_index"], stroke_id)
            prior = _ppt_annotation_previews.get(key)
            current_points = _normalized_stroke_points(stroke)
            if len(current_points) < 2:
                return False
            expected_offset = len(prior["points"]) - 1 if prior else 0
            if point_offset != expected_offset:
                return False
            if prior:
                points = [prior["points"][-1], *current_points[1:]]
                chunk_index = prior["chunk_count"]
            else:
                points = current_points
                chunk_index = 0
            if len(points) < 2:
                return False
            _draw_controller_annotation(
                shapes, stroke, float(presentation.PageSetup.SlideWidth),
                float(presentation.PageSetup.SlideHeight), preview=True,
                chunk=chunk_index, points_override=points,
            )
            _ppt_annotation_previews[key] = {
                "points": (prior["points"] + current_points[1:]) if prior else current_points,
                "chunk_count": chunk_index + 1,
            }
            return True
    except Exception as exc:
        gui_log_queue.put(f"[ANNOTATION] Live preview failed: {exc}")
        return False
    finally:
        if initialized:
            pythoncom.CoUninitialize()


def _cancel_slideshow_annotation_preview(stroke_id, slide_index):
    if sys.platform != "win32" or not hardware_controller.use_com:
        return False
    initialized = False
    try:
        with _ppt_render_lock:
            pythoncom.CoInitialize()
            initialized = True
            target = _matching_slideshow_presentation(slide_index, require_current_slide=False)
            key = (hardware_controller.current_presentation_id, slide_index, stroke_id)
            _ppt_annotation_previews.pop(key, None)
            if target is None:
                return False
            _, presentation = target
            slide = presentation.Slides(slide_index + 1)
            removed = _delete_controller_shapes(slide.Shapes, _annotation_shape_tag(stroke_id, preview=True))
            return removed > 0
    except Exception as exc:
        gui_log_queue.put(f"[ANNOTATION] Could not cancel live preview: {exc}")
        return False
    finally:
        if initialized:
            pythoncom.CoUninitialize()


def _render_slideshow_annotations(slide_index, strokes, replace=False):
    """Mirror annotations as tagged, editable shapes on the active slide."""
    if sys.platform != "win32" or not hardware_controller.use_com:
        return False

    initialized = False
    try:
        with _ppt_render_lock:
            pythoncom.CoInitialize()
            initialized = True
            target = _matching_slideshow_presentation(slide_index)
            if target is None:
                return False
            _, presentation = target
            slide = presentation.Slides(slide_index + 1)
            shapes = slide.Shapes
            key = (hardware_controller.current_presentation_id, slide_index)
            rendered = _ppt_rendered_annotation_ids[key]
            if replace:
                _delete_controller_shapes(shapes, _PPT_ANNOTATION_TAG)
                _delete_controller_shapes(shapes, _PPT_PREVIEW_TAG)
                rendered.clear()
                for preview_key in list(_ppt_annotation_previews):
                    if preview_key[:2] == key:
                        _ppt_annotation_previews.pop(preview_key, None)

            slide_width = float(presentation.PageSetup.SlideWidth)
            slide_height = float(presentation.PageSetup.SlideHeight)
            for stroke in strokes:
                stroke_id = stroke.get("id") or stroke.get("stroke_id")
                if stroke_id in rendered:
                    continue
                if stroke_id:
                    _delete_controller_shapes(
                        shapes, _annotation_shape_tag(stroke_id, preview=True)
                    )
                    _ppt_annotation_previews.pop(
                        (hardware_controller.current_presentation_id, slide_index, stroke_id),
                        None,
                    )
                _draw_controller_annotation(shapes, stroke, slide_width, slide_height)
                if stroke_id:
                    rendered.add(stroke_id)
            return True
    except Exception as exc:
        gui_log_queue.put(f"[ANNOTATION] Could not mirror stroke into PowerPoint: {exc}")
        return False
    finally:
        if initialized:
            pythoncom.CoUninitialize()


@server_app.get("/decks")
async def list_active_deck():
    deck = _active_deck_snapshot()
    return {"decks": [deck] if deck else []}


@server_app.post("/decks/sync")
async def sync_active_deck():
    """Refresh thumbnails for the currently open PowerPoint presentation."""
    previous_revision = hardware_controller.thumbnail_revision
    await asyncio.to_thread(hardware_controller.check_connection, True)
    deck = _active_deck_snapshot()
    if not deck:
        raise HTTPException(status_code=409, detail=hardware_controller.connection_status)
    if hardware_controller.thumbnail_revision == previous_revision:
        raise HTTPException(status_code=500, detail="PowerPoint did not export slide previews. Check the controller log.")
    return {
        "status": "ok",
        "deck": deck,
        "thumbnail_revision": hardware_controller.thumbnail_revision,
    }


@server_app.post("/decks/switch")
async def switch_active_deck(payload: dict):
    deck = _active_deck_snapshot()
    if not deck or payload.get("deck_id") != deck["id"]:
        raise HTTPException(status_code=404, detail="That presentation is no longer active in PowerPoint.")
    return {"status": "ok", "active_deck": deck["id"]}


# --- ANNOTATION ENDPOINTS ---
@server_app.post("/annotations/preview")
async def preview_annotation(req: AnnotationPreviewRequest):
    """Project one pen/highlighter delta without writing it to annotation storage."""
    if hardware_controller.current_presentation_id is None and hardware_controller.use_com:
        await asyncio.to_thread(hardware_controller.check_connection)
    started = time.perf_counter()
    # _render_slideshow_annotation_preview checks the actual SlideShowWindow
    # position while matching the presentation. Avoid refreshing COM state a
    # second time for every live chunk; that added a full COM round-trip before
    # the renderer performed the same current-slide check.
    projected = await asyncio.to_thread(
        _render_slideshow_annotation_preview,
        req.model_dump(),
        req.point_offset,
    )
    projection_ms = round((time.perf_counter() - started) * 1000, 1)
    return {"status": "ok", "projected": projected, "projection_ms": projection_ms}


@server_app.post("/annotations/preview/cancel")
async def cancel_annotation_preview(req: AnnotationPreviewCancelRequest):
    """Remove a pending preview stroke while leaving saved marks intact."""
    removed = await asyncio.to_thread(
        _cancel_slideshow_annotation_preview, req.stroke_id, req.slide_index
    )
    return {"status": "ok", "removed": removed}


@server_app.post("/annotations")
async def add_annotation(req: AnnotationRequest):
    """Add an annotation stroke to the current slide."""
    live_slide_position = await asyncio.to_thread(
        hardware_controller.refresh_slideshow_position
    )
    if live_slide_position is not None and req.slide_index != live_slide_position - 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"PowerPoint moved to slide {live_slide_position}. "
                "The controller has refreshed; draw the stroke again."
            ),
        )

    stroke_dict = req.model_dump()
    # A controller can receive strokes before the user presses Sync Deck.
    # Discover the active deck on demand so projection does not depend on a
    # cached presentation id from an earlier UI action.
    if hardware_controller.current_presentation_id is None and hardware_controller.use_com:
        await asyncio.to_thread(hardware_controller.check_connection)
    with ANNOTATION_LOCK:
        slide_annotations[req.slide_index].append(stroke_dict)
        annotation_redo[req.slide_index].clear()
        current_strokes = _effective_annotation_strokes(slide_annotations[req.slide_index])
    projection_started = time.perf_counter()
    projected = await asyncio.to_thread(
        _render_slideshow_annotations,
        req.slide_index,
        current_strokes if req.tool == "eraser" else [stroke_dict],
        req.tool == "eraser",
    )
    projection_ms = round((time.perf_counter() - projection_started) * 1000, 1)
    _queue_annotation_save()
    gui_log_queue.put(
        f"[ANNOTATION] Added {req.tool} stroke on slide {req.slide_index} "
        f"(id: {req.id}, {len(req.ppt_points or req.points)} points, "
        f"projection: {projection_ms}ms)"
    )
    return {
        "status": "ok",
        "stroke_id": req.id,
        "strokes": current_strokes,
        "projected": projected,
        "projection_ms": projection_ms,
        "projection_error": None if projected else hardware_controller.connection_status,
    }

@server_app.delete("/annotations/{slide_index}")
async def clear_annotations(slide_index: int):
    """Clear all annotations on a slide."""
    if slide_index < 0 or slide_index > 9999:
        raise HTTPException(status_code=400, detail="Invalid slide index")
    with ANNOTATION_LOCK:
        slide_annotations[slide_index] = []
        annotation_redo[slide_index].clear()
    await asyncio.to_thread(_render_slideshow_annotations, slide_index, [], True)
    _queue_annotation_save()
    gui_log_queue.put(f"[ANNOTATION] Cleared all annotations on slide {slide_index}")
    return {"status": "ok", "strokes": []}

@server_app.post("/annotations/undo")
async def undo_annotation(req: AnnotationUndoRequest):
    """Undo the last annotation on a slide."""
    changed = False
    with ANNOTATION_LOCK:
        if slide_annotations.get(req.slide_index):
            annotation_redo[req.slide_index].append(slide_annotations[req.slide_index].pop())
            changed = True
        current_strokes = _effective_annotation_strokes(slide_annotations.get(req.slide_index, []))
    if changed:
        await asyncio.to_thread(_render_slideshow_annotations, req.slide_index, current_strokes, True)
        _queue_annotation_save()
    gui_log_queue.put(f"[ANNOTATION] Undo last annotation on slide {req.slide_index}")
    return {"status": "ok", "strokes": current_strokes}


@server_app.post("/annotations/redo")
async def redo_annotation(req: AnnotationUndoRequest):
    """Restore the last undone annotation on a slide."""
    changed = False
    with ANNOTATION_LOCK:
        if annotation_redo.get(req.slide_index):
            slide_annotations[req.slide_index].append(annotation_redo[req.slide_index].pop())
            changed = True
        current_strokes = _effective_annotation_strokes(slide_annotations.get(req.slide_index, []))
    if changed:
        await asyncio.to_thread(_render_slideshow_annotations, req.slide_index, current_strokes, True)
        _queue_annotation_save()
    return {"status": "ok", "strokes": current_strokes}

@server_app.get("/annotations/{slide_index}")
async def get_annotations(slide_index: int):
    """Get all annotations for a slide."""
    if slide_index < 0 or slide_index > 9999:
        raise HTTPException(status_code=400, detail="Invalid slide index")
    with ANNOTATION_LOCK:
        strokes = _effective_annotation_strokes(slide_annotations.get(slide_index, []))
    await asyncio.to_thread(_render_slideshow_annotations, slide_index, strokes)
    return {"strokes": strokes}

def run_backend_server():
    global uvicorn_server
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["loggers"]["uvicorn"]["level"] = "INFO"
    config = uvicorn.Config(server_app, host="127.0.0.1", port=PYTHON_BACKEND_PORT, log_config=log_config)
    server = uvicorn.Server(config)
    uvicorn_server = server
    server.run()


def _check_service_ports_available():
    """Fail before spawning workers when another process owns a service port."""
    ports = [
        (socket.SOCK_STREAM, "127.0.0.1", PYTHON_BACKEND_PORT, "Python API"),
        (socket.SOCK_STREAM, "0.0.0.0", NODE_FRONTEND_PORT, "Node frontend"),
        (socket.SOCK_DGRAM, "127.0.0.1", UDP_AUDIO_PORT, "audio receiver"),
    ]
    for kind, host, port, name in ports:
        _check_service_port_available(kind, host, port, name)


def _check_service_port_available(kind, host, port, name):
    probe = socket.socket(socket.AF_INET, kind)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(
            f"Cannot start: {name} port {port} on {host} is already in use. "
            "An existing instance may be running; check its status before starting another."
        ) from exc
    finally:
        probe.close()


def _existing_api_is_healthy(
    *, retries=3, request_timeout=(0.4, 2.0), retry_delay=0.2
):
    """Return True only when the existing API build supports this controller."""
    health_url = f"http://127.0.0.1:{PYTHON_BACKEND_PORT}/controller/health"
    api_url = f"http://127.0.0.1:{PYTHON_BACKEND_PORT}/openapi.json"
    api_paths = {
        "/annotations/{slide_index}", "/annotations/preview",
        "/annotations/preview/cancel", "/decks/sync",
    }
    # Service startup can race another controller's Uvicorn/Node bind. Retry
    # briefly so that a listener which has just opened is treated as reusable.
    for attempt in range(max(1, retries)):
        try:
            health_response = requests.get(health_url, timeout=request_timeout)
            health_response.raise_for_status()
            health = health_response.json()
            api_compatible = (
                health.get("status") == "ok"
                and health.get("api_version") == PPT_CONTROLLER_API_VERSION
            )

            api_response = requests.get(api_url, timeout=request_timeout)
            api_response.raise_for_status()
            paths = api_response.json().get("paths", {})
            api_healthy = api_compatible and api_paths.issubset(paths)
        except (requests.RequestException, ValueError, TypeError):
            api_healthy = False

        if api_healthy:
            return True
        if attempt + 1 < retries and retry_delay > 0:
            time.sleep(retry_delay)
    return False


def _existing_api_has_incompatible_build():
    """Detect an answering API worker that predates the current API contract."""
    try:
        response = requests.get(
            f"http://127.0.0.1:{PYTHON_BACKEND_PORT}/controller/health",
            timeout=(0.2, 0.6),
        )
    except requests.RequestException:
        return False

    # Older workers answer other routes but do not expose the versioned health
    # endpoint. Treat that as stale so startup never silently reuses old code.
    if response.status_code == 404:
        return True
    if not response.ok:
        return False
    try:
        health = response.json()
    except (ValueError, TypeError):
        return True
    return health.get("api_version") != PPT_CONTROLLER_API_VERSION


def _wait_for_api_port_resolution(timeout=6.0):
    """Wait for an occupied API port to become healthy or become available."""
    deadline = time.monotonic() + max(0.0, timeout)
    last_bind_error = None
    while True:
        if _existing_api_is_healthy(
            retries=1, request_timeout=(0.2, 0.6), retry_delay=0
        ):
            return "healthy"

        try:
            _check_service_port_available(
                socket.SOCK_STREAM, "127.0.0.1", PYTHON_BACKEND_PORT, "Python API"
            )
            return "available"
        except RuntimeError as exc:
            last_bind_error = exc

        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Python API port {PYTHON_BACKEND_PORT} on 127.0.0.1 stayed occupied "
                f"but did not answer the controller health check for {timeout:.1f}s. "
                "No running process was stopped; close the stale controller window "
                "or free the port, then retry."
            ) from last_bind_error
        time.sleep(0.25)


def _existing_node_is_healthy():
    """Return True when Node serves the UI on a phone-reachable interface."""
    # The frontend may use a self-signed HTTPS certificate or plain HTTP.
    for scheme in ("https", "http"):
        try:
            health_response = requests.get(
                f"{scheme}://127.0.0.1:{NODE_FRONTEND_PORT}/health",
                timeout=(0.4, 2.0),
                verify=False,
            )
            health_response.raise_for_status()
            health = health_response.json()
            if (
                health.get("status") != "ok"
                or health.get("controller_version") != PPT_CONTROLLER_NODE_VERSION
            ):
                continue
            bind_host = str(health.get("bind_host", "")).strip().lower()
            if bind_host in {"0.0.0.0", "::"}:
                lan_reachable = True
            else:
                try:
                    lan_reachable = not ipaddress.ip_address(bind_host).is_loopback
                except ValueError:
                    lan_reachable = bind_host not in {"", "localhost"}
            if not lan_reachable:
                continue

            page_response = requests.get(
                f"{scheme}://127.0.0.1:{NODE_FRONTEND_PORT}/",
                timeout=(0.4, 2.0),
                verify=False,
            )
            if page_response.ok and "Smart PPT Controller" in page_response.text:
                return True
        except requests.RequestException:
            continue
    return False


def _existing_node_has_incompatible_build():
    """Detect an old or loopback-only Node worker that cannot serve phone clients."""
    for scheme in ("https", "http"):
        try:
            response = requests.get(
                f"{scheme}://127.0.0.1:{NODE_FRONTEND_PORT}/health",
                timeout=(0.2, 0.6),
                verify=False,
            )
        except requests.RequestException:
            continue

        if response.status_code == 404:
            return True
        if not response.ok:
            continue
        try:
            health = response.json()
        except (ValueError, TypeError):
            return True
        if health.get("controller_version") != PPT_CONTROLLER_NODE_VERSION:
            return True
        bind_host = str(health.get("bind_host", "")).strip().lower()
        if bind_host in {"0.0.0.0", "::"}:
            return False
        try:
            return ipaddress.ip_address(bind_host).is_loopback
        except ValueError:
            return bind_host in {"", "localhost"}
    return False


def _existing_controller_is_healthy():
    """Return True when a complete PPT Controller is already serving both UIs."""
    return _existing_api_is_healthy() and _existing_node_is_healthy()


def _attach_to_existing_controller():
    """Mark this window as connected without launching duplicate services."""
    global services_started, services_attached_to_existing
    services_started = True
    services_attached_to_existing = True
    gui_log_queue.put(
        f"[SERVICES] Existing PPT Controller is healthy on ports "
        f"{PYTHON_BACKEND_PORT}/{NODE_FRONTEND_PORT}; connected without "
        "starting duplicate workers."
    )
    return "connected-existing"


def _launch_node_frontend():
    """Launch this workspace's Node UI and forward its logs to the controller."""
    global node_process, node_log_stop_event, node_log_threads
    script_dir = os.path.dirname(os.path.abspath(__file__))
    server_js_path = os.path.join(script_dir, "server.js")
    if not os.path.isfile(server_js_path):
        gui_log_queue.put(f"[SERVICES] server.js not found at {server_js_path}.")
        return False
    try:
        node_process = subprocess.Popen(
            ["node", "server.js"], cwd=script_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        gui_log_queue.put(f"[SERVICES] Launched Node server (pid={node_process.pid})")
        node_log_stop_event = threading.Event()

        def _read_stream(stream, prefix):
            try:
                while not node_log_stop_event.is_set():
                    line = stream.readline()
                    if not line:
                        break
                    gui_log_queue.put(f"[NODE]{prefix} {line.decode(errors='replace').rstrip()}")
                stream.close()
            except Exception as exc:
                gui_log_queue.put(f"[NODE] Log reader error: {exc}")

        stdout_thread = threading.Thread(
            target=_read_stream, args=(node_process.stdout, "OUT"), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_read_stream, args=(node_process.stderr, "ERR"), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        node_log_threads = [stdout_thread, stderr_thread]
        return True
    except Exception as exc:
        node_process = None
        gui_log_queue.put(f"[SERVICES] Failed to start Node server: {exc}")
        return False


def _stop_node_frontend():
    """Stop only the Node child launched by this controller window."""
    global node_process, node_log_stop_event, node_log_threads
    if node_log_stop_event:
        node_log_stop_event.set()
    if node_process:
        try:
            node_process.terminate()
            node_process.wait(timeout=3)
        except Exception:
            try:
                node_process.kill()
            except Exception:
                pass
        gui_log_queue.put("[SERVICES] Node server stopped.")
    node_process = None
    node_log_threads = []
    node_log_stop_event = None


def _probe_powerpoint_after_node_start():
    """Wait for Node's listener, then publish the live deck state once it can accept it."""
    deadline = time.monotonic() + 12
    while not stop_event.is_set() and time.monotonic() < deadline:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.4)
        try:
            probe.connect(("127.0.0.1", NODE_FRONTEND_PORT))
            _, message = hardware_controller.check_connection(force_export=False)
            gui_log_queue.put(f"[SYSTEM] Post-start PowerPoint probe: {message}")
            return
        except OSError:
            time.sleep(0.25)
        finally:
            probe.close()
    if not stop_event.is_set():
        gui_log_queue.put("[SYNC] Node did not become reachable; PowerPoint state was not synced.")


def powerpoint_slide_monitor_worker():
    """Keep the remote annotation view aligned with slides navigated in PowerPoint."""
    while not stop_event.wait(0.35):
        hardware_controller.refresh_slideshow_position()


def _start_python_service_workers():
    """Start the Python API and workers without assuming this window owns Node."""
    global service_threads
    service_threads = {
        'backend': threading.Thread(target=run_backend_server, daemon=True),
        'executor': threading.Thread(target=centralized_presentation_executor, daemon=True),
        'udp_rx': threading.Thread(target=udp_audio_receiver_worker, daemon=True),
        'riva': threading.Thread(target=riva_streaming_pipeline_worker, daemon=True),
        'processor': threading.Thread(target=process_queue_worker, daemon=True),
        'similarity': threading.Thread(target=ai_similarity_monitor_worker, daemon=True),
        'slide_monitor': threading.Thread(
            target=powerpoint_slide_monitor_worker,
            name="ppt-slide-monitor",
            daemon=True,
        ),
    }
    for worker in service_threads.values():
        worker.start()


def start_services():
    """Start Python worker threads and the Node server (if available)."""
    global services_started, services_attached_to_existing, node_process, service_threads, node_log_stop_event, node_log_threads
    if services_started:
        if _existing_controller_is_healthy():
            return "already-running"
        gui_log_queue.put(
            "[SERVICES] A service marked running did not pass its health check; recovering missing service(s)."
        )
        services_started = False
        services_attached_to_existing = False
    if _existing_api_has_incompatible_build():
        raise RuntimeError(
            f"An outdated Python API is still running on port {PYTHON_BACKEND_PORT}. "
            "Stop the existing PPT Controller service, then start it again. "
            "PowerPoint can stay open."
        )
    if _existing_node_has_incompatible_build():
        raise RuntimeError(
            f"An outdated or loopback-only Node frontend is still running on port {NODE_FRONTEND_PORT}. "
            "Restart Services from the window that launched it so Node can rebind for phone access. "
            "PowerPoint can stay open."
        )
    if _existing_api_is_healthy():
        # The Python API may outlive a failed Node process. Reuse it and restore
        # only the missing frontend; do not start a second API or audio socket.
        if _existing_node_is_healthy():
            return _attach_to_existing_controller()
        try:
            _check_service_port_available(
                socket.SOCK_STREAM, "0.0.0.0", NODE_FRONTEND_PORT, "Node frontend"
            )
        except RuntimeError:
            if _existing_node_is_healthy():
                return _attach_to_existing_controller()
            raise
        stop_event.clear()
        services_attached_to_existing = True
        if not _launch_node_frontend():
            services_attached_to_existing = False
            raise RuntimeError("The Python API is healthy, but the Node frontend could not start.")
        services_started = True
        gui_log_queue.put(
            f"[SERVICES] Reused the existing Python API on {PYTHON_BACKEND_PORT} "
            f"and restored the Node frontend on {NODE_FRONTEND_PORT}."
        )
        threading.Thread(
            target=_probe_powerpoint_after_node_start,
            name="ppt-post-start-sync",
            daemon=True,
        ).start()
        return "connected-existing"

    if _existing_node_is_healthy():
        # The Python window may have closed while its separately spawned Node
        # child remained alive. Reuse that UI and restore only the missing API.
        try:
            api_port_state = _wait_for_api_port_resolution()
            if api_port_state == "healthy":
                return _attach_to_existing_controller()
            _check_service_port_available(
                socket.SOCK_DGRAM, "127.0.0.1", UDP_AUDIO_PORT, "audio receiver"
            )
        except RuntimeError:
            if _existing_controller_is_healthy():
                return _attach_to_existing_controller()
            raise

        services_attached_to_existing = False
        stop_event.clear()
        _start_python_service_workers()
        services_started = True
        gui_log_queue.put(
            f"[SERVICES] Reused the existing Node frontend on {NODE_FRONTEND_PORT} "
            f"and restored the Python API on {PYTHON_BACKEND_PORT}."
        )
        threading.Thread(
            target=_probe_powerpoint_after_node_start,
            name="ppt-post-start-sync",
            daemon=True,
        ).start()
        return "started"

    if _existing_controller_is_healthy():
        # A second GUI window can use the first instance's API and frontend. Do not
        # start a second audio socket, worker pool, or Node process.
        return _attach_to_existing_controller()
    try:
        _check_service_ports_available()
    except RuntimeError:
        # Close the check/bind race: another instance can finish starting
        # between our first health probe and the port check.
        if _existing_controller_is_healthy():
            return _attach_to_existing_controller()
        raise
    services_attached_to_existing = False
    # Clear any previous stop signal
    stop_event.clear()

    # Start Python backend and workers
    _start_python_service_workers()

    _launch_node_frontend()

    services_started = True
    threading.Thread(
        target=_probe_powerpoint_after_node_start,
        name="ppt-post-start-sync",
        daemon=True,
    ).start()
    return "started"


def stop_services():
    """Stop Node process and mark services as stopped. Python worker threads are daemon and will exit when the app quits."""
    global services_started, services_attached_to_existing, node_process, node_log_stop_event, node_log_threads
    if not services_started:
        gui_log_queue.put("[SERVICES] Services are not running.")
        return

    if services_attached_to_existing:
        # In API-recovery mode this window may own the replacement Node child.
        # Stop that child while leaving the existing Python API untouched.
        _stop_node_frontend()
        services_started = False
        services_attached_to_existing = False
        gui_log_queue.put(
            "[SERVICES] Disconnected this window; the existing Python API remains running."
        )
        return

    # Stop Node logging threads
    try:
        # Signal Python workers to stop
        stop_event.set()

        if node_log_stop_event:
            node_log_stop_event.set()
        # Terminate Node process
        if node_process:
            try:
                node_process.terminate()
                node_process.wait(timeout=3)
            except Exception:
                try:
                    node_process.kill()
                except Exception:
                    pass
            gui_log_queue.put(f"[SERVICES] Node server stopped.")
    except Exception as e:
        gui_log_queue.put(f"[SERVICES] Error stopping services: {e}")

    node_process = None
    node_log_threads = []
    node_log_stop_event = None
    # Wait for Python threads to exit gracefully
    for name, t in list(service_threads.items()):
        try:
            t.join(timeout=2.0)
        except Exception:
            pass

    # Stop uvicorn server if running
    try:
        global uvicorn_server
        if uvicorn_server:
            uvicorn_server.should_exit = True
            # give it a moment
            time.sleep(0.2)
            uvicorn_server = None
    except Exception:
        pass

    services_started = False
    services_attached_to_existing = False


def restart_services():
    stop_services()
    # Small delay to ensure resources released
    time.sleep(0.5)
    return start_services()


# --- UNIFIED EXECUTOR PIPELINE ---
def centralized_presentation_executor():
    """Consumes commands sequentially on an isolated apartment thread to fix COM and timing bugs."""
    com_initialized = False
    try:
        if sys.platform == "win32":
            pythoncom.CoInitialize()
            com_initialized = True

        gui_log_queue.put("[SYSTEM] Unified Presentation Execution Loop Online and Engaged.")
        while not stop_event.is_set():
            try:
                task = action_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            started = time.perf_counter()
            try:
                cmd = task["command"]
                target = task.get("target")
                origin = task.get("origin", "Unknown")

                if cmd == "NEXT":
                    hardware_controller.move_next()
                elif cmd == "PREVIOUS":
                    hardware_controller.move_previous()
                elif cmd == "JUMP":
                    hardware_controller.jump_to_slide(target)
                elif cmd == "BLACKOUT":
                    hardware_controller.toggle_blackout()
                elif cmd == "LASER":
                    hardware_controller.toggle_laser()
                else:
                    gui_log_queue.put(f"[EXECUTE] Ignored unsupported command: {cmd}")
                    continue
                elapsed_ms = (time.perf_counter() - started) * 1000
                queued_ms = (started - task.get("queued_at", started)) * 1000
                gui_log_queue.put(f"[EXECUTE] {cmd} from {origin}: queued {queued_ms:.2f}ms, ran {elapsed_ms:.2f}ms")
            except Exception as e:
                gui_log_queue.put(f"[CRITICAL EXECUTION CORRUPTION] Processing error: {e}")
            finally:
                action_queue.task_done()
    finally:
        if com_initialized:
            pythoncom.CoUninitialize()

# --- UDP AUDIO INGESTION PIPELINE ---
def udp_audio_receiver_worker():
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp_sock.bind(("127.0.0.1", UDP_AUDIO_PORT))
        gui_log_queue.put(f"[SYSTEM] UDP Streaming Pipeline socket assigned to Port: {UDP_AUDIO_PORT}")
    except Exception as e:
        gui_log_queue.put(f"[CRITICAL] Operational failure assigning audio sockets: {e}")
        return
    udp_sock.settimeout(1.0)
    while not stop_event.is_set():
        try:
            audio_data, _ = udp_sock.recvfrom(16384)
            if not audio_data:
                continue
            if audio_stream_queue.full():
                try:
                    audio_stream_queue.get_nowait()
                except queue.Empty:
                    pass
            audio_stream_queue.put_nowait(audio_data)
        except socket.timeout:
            continue
        except Exception as e:
            gui_log_queue.put(f"[UDP RX ERROR] Core drop: {e}")
            continue
    try:
        udp_sock.close()
    except Exception:
        pass

# --- RIVA STREAMING PIPELINE WORKER ---
def riva_streaming_pipeline_worker():
    api_key = _optional_provider_credential("PARAKEET_API_KEY", "RIVA_API_KEY")
    if not api_key:
        gui_log_queue.put(
            "[RIVA] Streaming speech recognition disabled: PARAKEET_API_KEY or RIVA_API_KEY is not configured."
        )
        return
    grpc_metadata = [["function-id", PARAKEET_FUNCTION_ID], ["authorization", f"Bearer {api_key}"]]
    auth = riva.client.auth.Auth(use_ssl=True, uri=PARAKEET_SERVER, metadata_args=grpc_metadata)
    riva_client = riva.client.ASRService(auth)

    gui_log_queue.put("[SYSTEM] Riva Context Core connection initialized.")

    config = riva.client.RecognitionConfig(
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
        sample_rate_hertz=SAMPLING_RATE,
        language_code="en-US",
        max_alternatives=1,
        enable_automatic_punctuation=True
    )
    streaming_config = riva.client.StreamingRecognitionConfig(config=config, interim_results=False)

    def audio_chunk_generator():
        while True:
            try:
                yield audio_stream_queue.get(timeout=2.0)
            except queue.Empty:
                continue

    while not stop_event.is_set():
        try:
            responses = riva_client.streaming_response_generator(audio_chunk_generator(), streaming_config)
            for response in responses:
                if stop_event.is_set():
                    break
                for result in response.results:
                    if not result.is_final:
                        continue
                    transcript = result.alternatives[0].transcript
                    if len(transcript.strip()) > 0:
                        command_queue.put(transcript)
        except grpc.RpcError as rpc_e:
            gui_log_queue.put(f"[RIVA ERROR] gRPC Pipeline disconnected: {rpc_e}")
            if stop_event.wait(2.0):
                break
            continue
        except Exception as gen_e:
            gui_log_queue.put(f"[RIVA FATAL] Unexpected stream exception: {gen_e}")
            if stop_event.wait(2.0):
                break
            continue

# --- COORDINATION LIFECYCLE ROUTINE ---
def process_queue_worker():
    global semantic_buffer, last_transcript
    while not stop_event.is_set():
        try:
            text_payload = command_queue.get(timeout=0.1)
            # Update the live transcript display variable
            with _transcript_lock:
                last_transcript = text_payload
            # Process direct explicit commands normally
            process_text_intent(text_payload)

            # Append spoken text to the rolling buffer for the Similarity Monitor
            with _buffer_lock:
                semantic_buffer += " " + text_payload
                # Keep buffer from growing infinitely (store approx the last ~350-400 words)
                if len(semantic_buffer) > 2500:
                    semantic_buffer = semantic_buffer[-2500:]

        except queue.Empty:
            continue

# --- AI SIMILARITY MONITOR (NEMOTRON VL RERANKER) ---
def ai_similarity_monitor_worker():
    global semantic_buffer
    invoke_url = "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-vl-1b-v2/reranking"
    api_key = _optional_provider_credential("RERANK_API_KEY", "NEMOTRON_API_KEY")
    if not api_key:
        gui_log_queue.put(
            "[SYSTEM] Similarity Monitor disabled: RERANK_API_KEY or NEMOTRON_API_KEY is not configured."
        )
        return
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    gui_log_queue.put("[SYSTEM] Nemotron VL Similarity Monitor Online.")

    while not stop_event.is_set():
        # Run analysis every 4 seconds to avoid spamming the API, but exit early if stop_event set
        if stop_event.wait(4.0):
            break

        with _buffer_lock:
            current_text = semantic_buffer.strip()
        
        # Ensure we have spoken at least a few words recently before checking similarity
        if len(current_text.split()) < 6:
            continue

        with _state_lock:
            current_slide = current_slide_state["current"]
            next_slide = current_slide + 1
            max_slides = current_slide_state["max"]

        # Stop checking if we are at the end of the presentation
        if next_slide > max_slides or max_slides == 999:
            continue

        # Target the thumbnail of the NEXT slide for visual-language mapping
        target_filename = f"slide_{next_slide:02d}.png"
        target_path = os.path.join(NODE_THUMBNAILS_DIR, target_filename)

        if not os.path.exists(target_path):
            continue

        try:
            with open(target_path, "rb") as f:
                # Convert thumbnail to base64 for the vision reranker
                b64_image = base64.b64encode(f.read()).decode()

            # Grab the most recent ~500 characters of speech (roughly the last 3-4 sentences spoken)
            recent_context = current_text[-500:]

            payload = {
                "model": "nvidia/llama-nemotron-rerank-vl-1b-v2",
                "query": {
                    "text": recent_context
                },
                "passages": [
                    {
                        "text": "Presentation slide content.",
                        "image": f"data:image/png;base64,{b64_image}"
                    }
                ]
            }

            response = requests.post(invoke_url, headers=headers, json=payload, timeout=5)
            if response.status_code == 200:
                resp_data = response.json()
                score = 0

                # Extract similarity ranking score
                if 'rankings' in resp_data and len(resp_data['rankings']) > 0:
                    score = resp_data['rankings'][0].get('score', resp_data['rankings'][0].get('relevance_score', 0))

                # Store globally so the GUI can read it
                with _similarity_lock:
                    global last_similarity_score
                    last_similarity_score = score

                gui_log_queue.put(f"[AI SIMILARITY] Next Slide ({next_slide}) Relevance Score: {score:.3f}")

                # THRESHOLD: If context matches next slide with > 80% confidence, advance automatically
                if score > 0.80:
                    gui_log_queue.put(f"[AI SIMILARITY] High context match! Auto-advancing to Slide {next_slide}.")
                    action_queue.put({"command": "NEXT", "target": None, "origin": "Similarity AI"})

                    # Clear the buffer so it doesn't trigger repeatedly on the same sentence
                    with _buffer_lock:
                        semantic_buffer = ""
                    with _similarity_lock:
                        last_similarity_score = 0.0
                    time.sleep(3)  # Wait for slide to change and user to begin new topic
        except requests.RequestException as req_e:
             # Silently catch timeouts or API drops to prevent crashing the monitor thread
            gui_log_queue.put(f"[AI SIMILARITY] API request error: {req_e}")
        except Exception as e:
            # Silently catch timeouts or API drops to prevent crashing the monitor thread
            gui_log_queue.put(f"[AI SIMILARITY] Monitor loop error: {e}")
            pass

# --- MODERN CONTROLLER GUI LAYOUT ---
class OrchestratorGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Smart PPT Orchestrator Core")
        self.geometry("950x620")
        self.configure(fg_color="#1e1e2f")
        self.resizable(False, False)
        self.theme_mode = get_theme_mode()
        self.theme_palette = get_theme_palette(self.theme_mode)
        self._service_action_results = queue.Queue()
        self._service_action_running = False

        self.grid_columnconfigure(0, weight=5)
        self.grid_columnconfigure(1, weight=4)
        self.grid_rowconfigure(0, weight=1)

        # --- LEFT PANEL: Status & Logs ---
        self.left_container = ctk.CTkFrame(self, fg_color="transparent")
        self.left_container.grid(row=0, column=0, padx=25, pady=25, sticky="nsew")

        title_label = ctk.CTkLabel(self.left_container, text="Smart Deck Controller", font=ctk.CTkFont(family="Arial", size=26, weight="bold"), text_color="#61dafb")
        title_label.pack(anchor="w", pady=(0, 15))

        self.conn_card = ctk.CTkFrame(self.left_container, fg_color="#252538", corner_radius=15, border_width=2, border_color="#3c3c54")
        self.conn_card.pack(fill="x", pady=5)
        self.conn_card.grid_columnconfigure(0, weight=1)

        self.ppt_status_lbl = ctk.CTkLabel(self.conn_card, text="[Status] Scanning system link...", font=ctk.CTkFont(family="Arial", size=14, weight="bold"), text_color="#FFC107")
        self.ppt_status_lbl.grid(row=0, column=0, padx=15, pady=15, sticky="w")

        self.sync_btn = ctk.CTkButton(
            self.conn_card, text="Sync Deck", width=110, fg_color="#324092", hover_color="#3F51B5", font=ctk.CTkFont(weight="bold"),
            command=self.refresh_ppt_link
        )
        self.sync_btn.grid(row=0, column=1, padx=15, pady=15, sticky="e")

        self.slide_status_lbl = ctk.CTkLabel(self.conn_card, text="Current Slide: --", font=ctk.CTkFont(family="Courier", size=13, weight="bold"), text_color="#A78BFA")
        self.slide_status_lbl.grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")

        # Live Manual Macro Control Deck (Laser removed, Blackout fills frame)
        self.macro_frame = ctk.CTkFrame(self.left_container, fg_color="transparent")
        self.macro_frame.pack(fill="x", pady=10)

        self.btn_black = ctk.CTkButton(
            self.macro_frame, text="Toggle Blackout View State", fg_color="#607d8b", hover_color="#455a64", font=ctk.CTkFont(weight="bold"),
            command=lambda: action_queue.put({"command": "BLACKOUT", "target": None, "origin": "GUI Macro"})
        )
        self.btn_black.pack(fill="x", expand=True, padx=0)

        console_lbl = ctk.CTkLabel(self.left_container, text="System Logs & Extraction Stream:", font=ctk.CTkFont(family="Arial", size=12, weight="bold"), text_color="#8888A0")
        console_lbl.pack(anchor="w", pady=(10, 2))

        self.console_output = ctk.CTkTextbox(self.left_container, height=210, fg_color="#11111b", text_color="#A5B4FC", font=ctk.CTkFont(family="Courier", size=12), corner_radius=10)
        self.console_output.pack(fill="both", expand=True, pady=5)

        # --- Live Transcription Display ---
        live_lbl = ctk.CTkLabel(self.left_container, text="🎤 Live Speech Transcription:", font=ctk.CTkFont(family="Arial", size=12, weight="bold"), text_color="#8888A0")
        live_lbl.pack(anchor="w", pady=(8, 2))
        self.transcription_lbl = ctk.CTkLabel(
            self.left_container,
            text="(waiting for speech...)",
            font=ctk.CTkFont(family="Arial", size=13, slant="italic"),
            text_color="#61dafb",
            wraplength=460,
            justify="left",
        )
        self.transcription_lbl.pack(anchor="w", padx=4)

        # --- AI Similarity Confidence Bar ---
        sim_lbl = ctk.CTkLabel(self.left_container, text="🧠 Next-Slide AI Confidence:", font=ctk.CTkFont(family="Arial", size=12, weight="bold"), text_color="#8888A0")
        sim_lbl.pack(anchor="w", pady=(10, 2))
        self.confidence_bar = ctk.CTkProgressBar(self.left_container, height=18, corner_radius=9, fg_color="#252538", progress_color="#50FA7B")
        self.confidence_bar.set(0)
        self.confidence_bar.pack(fill="x", pady=(0, 2))
        self.confidence_pct_lbl = ctk.CTkLabel(self.left_container, text="0%  — speak to build context", font=ctk.CTkFont(family="Courier", size=11), text_color="#8888A0")
        self.confidence_pct_lbl.pack(anchor="w")

        # --- RIGHT PANEL: Network QR Mapping ---
        self.right_container = ctk.CTkFrame(self, fg_color="#151522", corner_radius=20, border_width=1, border_color="#2D2D44")
        self.right_container.grid(row=0, column=1, padx=25, pady=25, sticky="nsew")

        qr_title = ctk.CTkLabel(self.right_container, text="Mobile Remote Access Portal", font=ctk.CTkFont(family="Arial", size=18, weight="bold"), text_color="white")
        qr_title.pack(pady=(20, 5))

        url_lbl = ctk.CTkLabel(self.right_container, text=NODE_UI_URL, font=ctk.CTkFont(family="Courier", size=12), text_color="#00BCD4")
        url_lbl.pack()

        qr_img = qrcode.make(NODE_UI_URL).resize((220, 220))
        self.ctk_qr = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=(220, 220))

        self.qr_display = ctk.CTkLabel(self.right_container, image=self.ctk_qr, text="")
        self.qr_display.pack(pady=15)

        # Start/Stop Services Button
        self.start_services_btn = ctk.CTkButton(
            self.right_container, text="Start All Services", width=200, fg_color="#2E7D32", hover_color="#388E3C", font=ctk.CTkFont(weight="bold"),
            command=self.start_services_action
        )
        self.start_services_btn.pack(pady=(6, 12))

        self.stop_services_btn = ctk.CTkButton(
            self.right_container, text="Stop Services", width=200, fg_color="#B71C1C", hover_color="#C62828", font=ctk.CTkFont(weight="bold"),
            command=self.stop_services_action
        )
        self.stop_services_btn.pack(pady=(0, 6))

        self.restart_services_btn = ctk.CTkButton(
            self.right_container, text="Restart Services", width=200, fg_color="#1565C0", hover_color="#1976D2", font=ctk.CTkFont(weight="bold"),
            command=self.restart_services_action
        )
        self.restart_services_btn.pack(pady=(0, 12))

        instructions = (
            "1. Click 'Start All Services'.\n"
            "2. Open your .pptx in PowerPoint and press F5.\n"
            "3. Click 'Sync Deck' to refresh slide previews.\n"
            "4. Scan the QR code and open Advanced Mode.\n"
            "5. Use Menu -> Sync Open Deck if you change presentations."
        )
        step_info = ctk.CTkLabel(self.right_container, text=instructions, font=ctk.CTkFont(family="Arial", size=13), text_color="#8888A0", justify="left")
        step_info.pack(pady=10)

        self.after(500, lambda: self.refresh_ppt_link(force_export=False))
        self.listen_for_logs()

    def refresh_ppt_link(self, force_export=True):
        gui_log_queue.put("[SYSTEM] Probing environment runtime hooks for PowerPoint...")
        is_connected, message = hardware_controller.check_connection(force_export=force_export)

        colors = get_theme_palette(get_theme_mode())
        if is_connected:
            self.ppt_status_lbl.configure(text=f"[CONNECTED] {message}", text_color=colors["GREEN"])
        else:
            if "Editing" in message:
                self.ppt_status_lbl.configure(text=f"[EDITING] {message}", text_color=colors["TITLE"])
            else:
                self.ppt_status_lbl.configure(text=f"[DISCONNECTED] {message}", text_color=colors["RED"])

    def apply_theme(self, mode=None):
        """Refresh the window and retain the selected palette for live updates."""
        self.theme_mode = mode or get_theme_mode()
        self.theme_palette = get_theme_palette(self.theme_mode)
        ctk.set_appearance_mode(self.theme_mode.capitalize())
        apply_ctk_theme(self, self.theme_mode)

    def start_services_action(self):
        global services_started
        if self._service_action_running:
            return
        if services_started:
            self._launch_service_action(
                "start",
                "[SERVICES] Checking controller services and restoring any missing service...",
            )
            return

        self._launch_service_action("start", "[SERVICES] Starting backend workers and Node server...")

    def _launch_service_action(self, action, start_message):
        """Run slow service checks away from Tk so progress feedback can render."""
        if self._service_action_running:
            return
        self._service_action_running = True
        palette = get_theme_palette(get_theme_mode())
        progress_label = "Starting services…" if action == "start" else "Restarting services…"
        self.start_services_btn.configure(text=progress_label, fg_color=palette["GREEN"], state="disabled")
        self.stop_services_btn.configure(text="Please wait…", state="disabled")
        self.restart_services_btn.configure(state="disabled")
        gui_log_queue.put(start_message)
        threading.Thread(
            target=self._run_service_action,
            args=(action,),
            name=f"ppt-gui-{action}-services",
            daemon=True,
        ).start()

    def _run_service_action(self, action):
        try:
            result = restart_services() if action == "restart" else start_services()
            self._service_action_results.put((action, result, None))
        except Exception as error:
            self._service_action_results.put((action, None, str(error)))

    def _set_service_buttons(self, result=None):
        palette = get_theme_palette(get_theme_mode())
        attached = services_attached_to_existing or result == "connected-existing"
        running = services_started or result == "already-running"
        if running:
            start_label = "Using Existing Services" if attached else "Services Running"
            stop_label = "Disconnect This Window" if attached else "Stop Services"
            self.start_services_btn.configure(text=start_label, fg_color=palette["GREEN"], state="disabled")
            self.stop_services_btn.configure(text=stop_label, fg_color=palette["RED"], state="normal")
        else:
            self.start_services_btn.configure(text="Start All Services", fg_color=palette["GREEN"], state="normal")
            self.stop_services_btn.configure(text="Stop Services", fg_color=palette["RED"], state="disabled")
        self.restart_services_btn.configure(text="Restart Services", state="normal")

    def stop_services_action(self):
        try:
            was_attached = services_attached_to_existing
            stop_services()
            palette = get_theme_palette(get_theme_mode())
            self.start_services_btn.configure(text="Start All Services", fg_color=palette["GREEN"], state="normal")
            stop_label = "Disconnected" if was_attached else "Stopped"
            self.stop_services_btn.configure(text=stop_label, fg_color=palette["RED"], state="disabled")
            self.restart_services_btn.configure(state="normal")
            if not was_attached:
                gui_log_queue.put("[SERVICES] Services stopped via GUI.")
        except Exception as e:
            gui_log_queue.put(f"[SERVICES] Failed to stop services: {e}")

    def restart_services_action(self):
        self._launch_service_action("restart", "[SERVICES] Restarting services...")

    def listen_for_logs(self):
        while not gui_log_queue.empty():
            msg = gui_log_queue.get_nowait()
            self.console_output.insert("end", f"{msg}\n")
            self.console_output.see("end")

        while True:
            try:
                action, result, error = self._service_action_results.get_nowait()
            except queue.Empty:
                break
            self._service_action_running = False
            if error:
                gui_log_queue.put(f"[SERVICES] Failed to {action} services: {error}")
                self._set_service_buttons()
            else:
                self._set_service_buttons(result=result)
                if result == "connected-existing":
                    gui_log_queue.put("[SERVICES] Reconnected to the existing controller; its services were left running.")
                elif result == "already-running":
                    gui_log_queue.put("[SERVICES] Services are already running.")
                elif action == "restart":
                    gui_log_queue.put("[SERVICES] Services restarted.")
                else:
                    gui_log_queue.put("[SERVICES] All services started.")

        with _state_lock:
            curr_pos = current_slide_state['current']
            max_pos = current_slide_state['max']

        self.slide_status_lbl.configure(text=f"Current Deck Position: Slide {curr_pos} / {max_pos if max_pos != 999 else '--'}")

        # Update live transcription label
        with _transcript_lock:
            curr_transcript = last_transcript

        if curr_transcript:
            short = curr_transcript[:100] + ("…" if len(curr_transcript) > 100 else "")
            self.transcription_lbl.configure(text=f"\"  {short}  \"")

        # Update confidence bar (clamp to 0-1)
        with _similarity_lock:
            curr_score = last_similarity_score

        score = max(0.0, min(1.0, curr_score))
        self.confidence_bar.set(score)
        pct = int(score * 100)
        palette = get_theme_palette(get_theme_mode())
        if pct >= 80:
            self.confidence_bar.configure(progress_color=palette["RED"])
            status = f"{pct}%  — 🚀 AUTO-ADVANCING!"
        elif pct >= 50:
            self.confidence_bar.configure(progress_color=palette["TITLE"])
            status = f"{pct}%  — context building..."
        else:
            self.confidence_bar.configure(progress_color=palette["GREEN"])
            status = f"{pct}%  — speak to build context"
        self.confidence_pct_lbl.configure(text=status)

        self.after(100, self.listen_for_logs)

if __name__ == "__main__":
    # Keep the normal GUI opt-in, but allow recovery launches to start services
    # after the window and initial PowerPoint probe have initialized.
    start_services_on_launch = "--start-services" in sys.argv[1:]
    ctk.set_appearance_mode(get_theme_mode().capitalize())
    app = OrchestratorGUI()
    app.apply_theme(get_theme_mode())
    watch_theme(app, app.apply_theme)
    if start_services_on_launch:
        app.after(750, app.start_services_action)
    app.mainloop()
