"""
Video Editing Agent — AI-powered After Effects control with role-based pipeline.

Architecture:
- AI Role Pipeline (5 roles via OmniRoute/Gemini):
  🧠 Planner    — Breaks request into steps, creates execution plan
  🎨 Designer   — Decides visual approach, effect parameters
  💻 Coder      — Generates ExtendScript (JSX) for After Effects
  🔍 Reviewer   — Validates JSX, checks for errors
  📝 Reporter   — Writes final chat response
- File Bridge: Python writes .jsx files → AE panel executes them
- Bridge folder: %TEMP%\\ae_video_agent_bridge
- OmniRoute (Antigravity) is used as primary; Gemini keys as fallback.

Usage:
    from video_editing_agent import VideoEditingAgent
    agent = VideoEditingAgent(key_manager)  # role key manager
    agent.start_server()
    jsx = agent.execute_prompt("add text Hello World, then add glow effect")
    print("Result:", agent.get_last_response())
    agent.stop_server()
"""

import hashlib
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable
from urllib.error import HTTPError

try:
    from google.genai import errors as genai_errors
    from google.genai import types
    _GENAI_TYPES_AVAILABLE = True
except ImportError:
    genai_errors = None
    types = None
    _GENAI_TYPES_AVAILABLE = False

from key_manager import find_ae_scriptui_panels_dir, AiApi


def _make_vision_part(data: bytes, mime_type: str):
    if _GENAI_TYPES_AVAILABLE:
        try:
            return types.Part.from_bytes(data=data, mime_type=mime_type)
        except Exception:
            pass
    class _VisionPart:
        def __init__(self, data, mime_type):
            self.data = data
            self.mime_type = mime_type
            self.text = None
    return _VisionPart(data, mime_type)


# Define Tool Schemas for Dynamic Tool-Calling Agent
if _GENAI_TYPES_AVAILABLE:
    AE_TOOLS = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="check_file_bridge_status",
                description="Returns the live state of the file bridge folder, active connection status, and whether After Effects is listening/polling.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={})
            ),
            types.FunctionDeclaration(
                name="execute_extendscript",
                description="Queues and writes ExtendScript (JSX) commands to the AE bridge folder for execution inside After Effects. Use for ALL edits: adding text, effects, animations, color grades, etc.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "jsx_code": types.Schema(type=types.Type.STRING, description="The valid ExtendScript (JSX) code block to execute in After Effects."),
                        "description": types.Schema(type=types.Type.STRING, description="Human-readable description of what this JSX does (e.g., 'Add glow effect to selected layer').")
                    },
                    required=["jsx_code", "description"]
                )
            ),
            types.FunctionDeclaration(
                name="get_active_composition_info",
                description="Requests AE project metadata/layer structures via the bridge. Returns composition info, layers, selected layers, and effects.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={})
            ),
            types.FunctionDeclaration(
                name="list_selected_layers",
                description="Returns name, type, and key properties of currently selected layers in the active AE composition.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={})
            ),
            types.FunctionDeclaration(
                name="get_effect_presets",
                description="Returns valid AE match-name and parameter info for a requested effect so the model uses correct names.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "effect_name": types.Schema(type=types.Type.STRING, description="The effect display name or keyword, e.g. glow, blur, lumetri.")
                    },
                    required=["effect_name"]
                )
            ),
            types.FunctionDeclaration(
                name="search_project_footage",
                description="Finds layers/footage items by name in the current AE project.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "query": types.Schema(type=types.Type.STRING, description="Search term for layer/footage name.")
                    },
                    required=["query"]
                )
            ),
            types.FunctionDeclaration(
                name="undo_last_action",
                description="Undoes the last action in After Effects.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={})
            ),
            types.FunctionDeclaration(
                name="take_screenshot_and_verify",
                description="Captures the current frame via the AE bridge and returns the saved screenshot filename for visual verification.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={})
            ),
        ]
    )
else:
    AE_TOOLS = None


# Free-tier Gemini limits per key (assumes separate GCP projects per key)
RPM_PER_KEY = 15
TPM_PER_KEY = 1_000_000
RPD_PER_KEY = 1500
NUM_KEYS = 5
MAX_RETRIES_429 = 5
MAX_RETRIES_503 = 3
BACKOFF_BASE = 1.0
BACKOFF_CAP = 32.0

# Role indices matching the 5 API keys
ROLE_PLANNER = 0       # Key 1
ROLE_DESIGNER = 1      # Key 2
ROLE_CODER = 2         # Key 3
ROLE_REVIEWER = 3      # Key 4
ROLE_REPORTER = 4      # Key 5
ROLE_ANALYST = 5       # Virtual — uses pool key (key 5)

ROLE_NAMES = {
    0: "🧠 Planner",
    1: "🎨 Designer",
    2: "💻 Coder",
    3: "🔍 Reviewer",
    4: "📝 Reporter",
    5: "🎥 Analyst"
}

MODEL = "gemini-2.5-flash"
VISION_MODEL = "gemini-2.5-flash"

# Analyst role
ROLE_ANALYST_LABEL = "🎥 Analyst"


# ============================================================
# INTENT CLASSIFICATION — Meta/System vs AE Timeline Queries
# ============================================================
META_INTENT_KEYWORDS = (
    "bridge", "connected", "connection", "status", "working",
    "what can you do", "what do you do", "capabilities", "abilities",
    "help", "how does this work", "system", "health", "file bridge",
    "ae bridge", "is the bridge", "bridge status", "polling"
)

AE_ACTION_KEYWORDS = (
    "add ", "apply ", "animate", "blur", "color grade", "create ",
    "delete ", "edit ", "glow", "inspect", "render", "remove ",
    "set ", "text ", "title", "track", "trim", "make a comp",
    "layer", "effect", "keyframe", "opacity", "position", "scale",
    "rotation", "anchor", "mask", "shape", "solid", "null",
    "adjustment", "precomp", "pre-comp", "footage", "import",
    "export", "queue", "output", "composition", "comp "
)

def classify_intent(prompt: str) -> str:
    """
    Classify user prompt intent:
    - 'meta': System/bridge status questions, capabilities, help
    - 'ae_action': Actual After Effects editing requests
    - 'conversational': Greetings, general chat
    """
    text = prompt.lower().strip()
    if not text:
        return "conversational"
    
    # Check for meta/system queries first
    if any(kw in text for kw in META_INTENT_KEYWORDS):
        # Special case: "inspect" alone could be AE action, but "inspect project" is often meta
        if "inspect" in text and ("bridge" in text or "status" in text or "connection" in text):
            return "meta"
        if any(kw in text for kw in ("what can you", "what do you", "capabilities", "abilities", "help")):
            return "meta"
        return "meta"
    
    # Check for AE action keywords
    if any(kw in text for kw in AE_ACTION_KEYWORDS):
        return "ae_action"
    
    # Conversational fallthrough
    conversational_terms = (
        "hello", "hi ", "hey", "who are you", "thanks", "thank you",
        "good morning", "good afternoon", "good evening", "bye"
    )
    if any(term in text for term in conversational_terms):
        return "conversational"
    
    # Default to conversational for questions
    if text.endswith("?"):
        return "conversational"
    
    return "conversational"


# Free-tier Gemini limits per key (assumes separate GCP projects per key)
RPM_PER_KEY = 15
TPM_PER_KEY = 1_000_000
RPD_PER_KEY = 1500
NUM_KEYS = 5
MAX_RETRIES_429 = 5
MAX_RETRIES_503 = 3
BACKOFF_BASE = 1.0
BACKOFF_CAP = 32.0


class GeminiRateLimiter:
    """
    Token-bucket / sliding-window rate limiter for Gemini API calls.

    Tracks per-API-key:
      - requests in the current minute (sliding window)
      - requests today (persisted to quota_state.json, reset at midnight local)
      - tokens in the current minute

    Free-tier assumptions (separate GCP projects per key):
      15 RPM / 1M TPM / 1500 RPD per key.
    If keys share a project, the pool acts as ONE combined set — this class
    tracks per-key so the caller can aggregate.
    """

    def __init__(self, key_labels: List[str], bridge_dir: Path,
                 rpm: int = RPM_PER_KEY, tpm: int = TPM_PER_KEY,
                 rpd: int = RPD_PER_KEY):
        self.key_labels = key_labels
        self.num_keys = len(key_labels)
        self.rpm = rpm
        self.tpm = tpm
        self.rpd = rpd

        self.quota_file = bridge_dir / "quota_state.json"
        self._lock = threading.Lock()

        # Per-key sliding-window request timestamps (for RPM)
        self._request_times: Dict[int, List[float]] = {i: [] for i in range(self.num_keys)}
        # Per-key token counts in current minute
        self._token_times: Dict[int, List[Tuple[float, int]]] = {i: [] for i in range(self.num_keys)}

        # Daily counts (persisted)
        self._daily_counts = self._load_daily_counts()

    def _load_daily_counts(self) -> Dict[str, Any]:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.quota_file.exists():
            try:
                data = json.loads(self.quota_file.read_text())
                if data.get("date") == today:
                    return data
            except Exception:
                pass
        return {"date": today, "per_key": {i: 0 for i in range(self.num_keys)}, "total_today": 0}

    def _save_daily_counts(self):
        tmp = self.quota_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._daily_counts))
        tmp.replace(self.quota_file)

    def _reset_if_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_counts.get("date") != today:
            self._daily_counts = {
                "date": today,
                "per_key": {i: 0 for i in range(self.num_keys)},
                "total_today": 0,
            }
            self._save_daily_counts()

    def _prune_window(self, timestamps: List[float], now: float, window: float = 60.0):
        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

    def _prune_tokens(self, key_idx: int, now: float):
        cutoff = now - 60.0
        self._token_times[key_idx] = [(t, c) for t, c in self._token_times[key_idx] if t >= cutoff]

    def _estimate_tokens(self, model_input: str, vision_images: bool = False) -> int:
        """Rough token estimate for a Gemini request (chars ~ 0.25 tokens)."""
        char_count = len(model_input) if model_input else 0
        tokens = int(char_count * 0.25)
        if vision_images:
            tokens += 8192  # rough overhead for a vision image
        return max(tokens, 1)

    def check_and_wait(self, key_idx: int, estimated_tokens: int = 0) -> None:
        """
        Block until the call is allowed under RPM/TPM limits.
        Raises QuotaExhaustedError if RPD would be exceeded.
        """
        with self._lock:
            self._reset_if_new_day()

        day_key = str(key_idx)
        with self._lock:
            if self._daily_counts["per_key"].get(day_key, 0) >= self.rpd:
                raise QuotaExhaustedError(
                    f"Key {key_idx} has hit its daily request limit ({self.rpd} RPD). "
                    f"Try again after midnight or use a different key."
                )

        while True:
            now = time.time()
            with self._lock:
                self._prune_window(self._request_times[key_idx], now)
                self._prune_tokens(key_idx, now)

                requests_this_min = len(self._request_times[key_idx])
                tokens_this_min = sum(c for _, c in self._token_times[key_idx])

                rpm_ok = requests_this_min < self.rpm
                tpm_ok = (tokens_this_min + estimated_tokens) <= self.tpm

                if rpm_ok and tpm_ok:
                    # Record the request
                    self._request_times[key_idx].append(now)
                    self._token_times[key_idx].append((now, estimated_tokens))
                    self._daily_counts["per_key"][day_key] = self._daily_counts["per_key"].get(day_key, 0) + 1
                    self._daily_counts["total_today"] += 1
                    self._save_daily_counts()
                    return

                # Determine wait time
                if not rpm_ok:
                    # Wait until oldest request exits the window
                    wait = self._request_times[key_idx][0] + 60.0 - now + 0.1
                else:
                    # TPM issue — wait for token window to clear
                    wait = self._token_times[key_idx][0][0] + 60.0 - now + 0.1

                wait = max(0.1, min(wait, 60.0))

            time.sleep(wait)

    def remaining_today(self) -> int:
        with self._lock:
            self._reset_if_new_day()
            total_today = self._daily_counts.get("total_today", 0)
            pool_total_rpd = self.rpd * self.num_keys
            return max(0, pool_total_rpd - total_today)

    def remaining_per_key(self) -> List[int]:
        with self._lock:
            self._reset_if_new_day()
            return [max(0, self.rpd - self._daily_counts["per_key"].get(str(i), 0))
                    for i in range(self.num_keys)]


class QuotaExhaustedError(Exception):
    """Raised when daily RPD limit is reached for a key."""
    pass


class QuotaState:
    """Simple container for persisted quota state, exposed to the UI."""
    pass


# Common After Effects effect match names (verified from Adobe docs)
AE_EFFECTS = """
Effect Match Names:
- ADBE Glo2          (Glow)
- ADBE Lumetri        (Lumetri Color)
- ADBE CurvesCustom   (Curves)
- ADBE Gaussian Blur 2 (Gaussian Blur)
- ADBE Levels2        (Levels)
- ADBE HUE SATURATION (Hue/Saturation)
- ADBE Tint           (Tint)
- ADBE Drop Shadow    (Drop Shadow)
- ADBE Fractal Noise  (Fractal Noise)
- ADBE Corner Pin     (Corner Pin)
- ADBE Fast Box Blur 2 (Fast Box Blur)
- ADPS Shadow         (Drop Shadow)
- ADBE Mosaic         (Mosaic)
- ADBE Shift Channels (Shift Channels)
- ADBE Fill           (Fill)
- ADBE Stroke         (Stroke)
- ADBE Trim Paths     (Trim Paths - shape layers)
- ADBE Wave Warp      (Wave Warp)

Property Group Match Names:
- "ADBE Effect Parade"      (Effects)
- "ADBE Transform Group"    (Transform)
- "ADBE Position"           (Position)
- "ADBE Scale"              (Scale)
- "ADBE Rotate Z"           (Rotation)
- "ADBE Opacity"            (Opacity)
- "ADBE Anchor Point"       (Anchor Point)
- "ADBE Source Text"        (Text Source)
"""


class VideoEditingAgent:
    """AI-powered Video Editing Agent with 6-role Gemini pipeline."""
    
    def __init__(self, key_manager=None, bridge_dir: Optional[str] = None):
        # Accept either RoleKeyManager or AiApi
        if key_manager is not None and hasattr(key_manager, '_km'):
            # It's an AiApi instance, extract the underlying key manager
            self.km = key_manager._km
            self._api = key_manager
        else:
            self.km = key_manager
            self._api = None
        
        if bridge_dir is None:
            bridge_dir = os.path.join(os.environ.get('TEMP', '/tmp'), 'ae_video_agent_bridge')
        
        self.bridge_dir = Path(bridge_dir)
        self.commands_dir = self.bridge_dir / "commands"
        self.responses_dir = self.bridge_dir / "responses"
        self.processed_dir = self.bridge_dir / "processed"
        self.screenshots_dir = self.bridge_dir / "screenshots"
        self.analysis_cache_dir = self.bridge_dir / "analysis_cache"
        
        for d in [self.commands_dir, self.responses_dir, self.processed_dir,
                   self.screenshots_dir, self.analysis_cache_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        self._command_queue = queue.Queue()
        self._running = False
        self._log_callback = None
        self._poll_thread = None
        self._last_response = None
        self._processed_ids = set()
        self._last_queued_cmd_id = None
        
        # Rate limiter — one entry per key
        key_labels = [f"key-{i+1}" for i in range(NUM_KEYS)]
        self.rate_limiter = GeminiRateLimiter(key_labels, self.bridge_dir)
        
        # Analysis cache
        self._analysis_cache: Dict[str, Any] = {}
        
        # UI toggle for vision-check
        self._vision_check_enabled = False
    
    def set_log_callback(self, callback):
        self._log_callback = callback
    
    def _log(self, msg):
        if self._log_callback:
            self._log_callback(msg)
        else:
            print(f"[VideoEditingAgent] {msg}")
    
    def has_api_keys(self) -> bool:
        return self.km is not None
    
    def start_server(self):
        if self._running:
            self._log("Bridge already running")
            return True
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        try:
            self._poll_thread.start()
        except RuntimeError as e:
            self._running = False
            self._poll_thread = None
            self._log(f"Could not start file bridge: {e}")
            return False
        self._log(f"File bridge started at: {self.bridge_dir}")
        return True
    
    def stop_server(self):
        if not self._running:
            return
        self._running = False
        self._log("Bridge stopped")
    
    def _poll_loop(self):
        while self._running:
            try:
                try:
                    cmd_data = self._command_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                cmd_id = cmd_data['id']
                
                if cmd_id in self._processed_ids:
                    continue
                self._processed_ids.add(cmd_id)
                
                jsx_code = cmd_data['jsx']
                response_text = cmd_data.get('response_text', '')
                
                # Write command
                cmd_file = self.commands_dir / f"cmd_{cmd_id}.jsx"
                meta_file = self.commands_dir / f"cmd_{cmd_id}.json"
                
                cmd_file.write_text(jsx_code, encoding='utf-8')
                meta_file.write_text(json.dumps({
                    'id': cmd_id,
                    'timestamp': time.time(),
                    'prompt': cmd_data.get('prompt', ''),
                    'response_text': response_text
                }), encoding='utf-8')
                
                self._log(f"Wrote command {cmd_id} to bridge")
                
                # Wait for response
                response_file = self.responses_dir / f"cmd_{cmd_id}.json"
                start_time = time.time()
                timeout = 30
                
                while self._running and (time.time() - start_time) < timeout:
                    if response_file.exists():
                        try:
                            response_text = response_file.read_text(encoding='utf-8')
                            response = json.loads(response_text)
                            self._last_response = response
                            
                            # Move to processed
                            try:
                                cmd_file.rename(self.processed_dir / cmd_file.name)
                                meta_file.rename(self.processed_dir / meta_file.name)
                                response_file.rename(self.processed_dir / response_file.name)
                            except:
                                pass
                            
                            self._log(f"Received response for {cmd_id}: {response.get('status', 'unknown')}")
                        except Exception as e:
                            self._log(f"Error reading response: {e}")
                        break
                    time.sleep(0.1)
                else:
                    self._log(f"Timeout waiting for response {cmd_id} — After Effects didn't respond. Is the panel open?")
                    
            except Exception as e:
                self._log(f"Poll loop error: {e}")
                time.sleep(0.5)
    
    @staticmethod
    def is_conversational_prompt(prompt: str) -> bool:
        text = prompt.lower().strip()
        if not text:
            return True
        action_terms = (
            "add ", "apply ", "animate", "blur", "color grade", "create ",
            "delete ", "edit ", "glow", "inspect", "render", "remove ",
            "set ", "text ", "title", "track", "trim", "make a comp",
        )
        if any(term in text for term in action_terms):
            return False
        conversational_terms = (
            "hello", "hi ", "hey", "what can you do", "what do you do",
            "what are your abilities", "your capabilities", "who are you",
            "how can you help", "can you help", "help me", "what is this",
            "tell me about yourself", "thanks", "thank you", "good morning",
            "good afternoon", "good evening",
        )
        if any(term in text for term in conversational_terms):
            return True
        return text.endswith("?") and any(
            term in text for term in ("can you", "what ", "how ", "who ", "why ")
        )

    @staticmethod
    def conversational_response() -> str:
        return (
            "I can help edit the active After Effects composition by adding and styling text, "
            "applying effects, creating intro and outro animations, inspecting project layers, "
            "capturing frames, assisting with tracking, analyzing footage, generating captions, "
            "and preparing JSX commands. Ask me to perform an edit when you are ready."
        )

    def send_prompt(self, prompt: str, on_role_complete=None, on_response=None, on_step=None) -> str:
        """
        Send a prompt through the dynamic tool-calling agent.
        Returns a cmd_id if JSX was queued, or "" if no action needed.
        """
        if not prompt.strip():
            return ""

        if not self._running:
            self.start_server()
        
        # Reset the tracked cmd_id for this call
        self._last_queued_cmd_id = None
        
        try:
            # classify_intent only decides whether AE tools are enabled;
            # every prompt still goes through the model.
            intent = classify_intent(prompt)
            use_tools = intent == "ae_action"
            response = self._run_tool_calling_loop(
                prompt, on_role_complete, on_response, on_step, use_tools=use_tools
            )
        except Exception as e:
            self._log(f"Agent error: {e}")
            err_msg = f"Sorry, I encountered an error: {e}"
            if on_response:
                on_response(err_msg)
            if on_role_complete:
                on_role_complete("⚠️ Error", err_msg)
            return ""
        
        # Return the cmd_id only if a JSX command was actually queued
        return self._last_queued_cmd_id or ""
    
    def _handle_meta_query(self, prompt: str) -> str:
        """Handle meta/system queries about the bridge, capabilities, etc."""
        bridge_active = self._running
        status = self.get_bridge_status()
        
        if "what can you do" in prompt.lower() or "capabilities" in prompt.lower() or "abilities" in prompt.lower():
            return (
                "I can help edit After Effects compositions by generating and executing ExtendScript commands. "
                "I can add text, apply effects (glow, blur, color grade, etc.), create animations, "
                "inspect project layers, and more. Just tell me what edit you'd like to make!"
            )
        
        if "bridge" in prompt.lower() or "connected" in prompt.lower() or "status" in prompt.lower():
            bridge_path = str(self.bridge_dir)
            pending = status.get("pending_commands", 0)
            processed = status.get("processed", 0)
            return (
                f"File Bridge Status:\n"
                f"• Bridge folder: {bridge_path}\n"
                f"• Bridge active: {'Yes' if bridge_active else 'No'}\n"
                f"• Pending commands: {pending}\n"
                f"• Processed commands: {processed}\n"
                f"• API keys loaded: {'Yes' if self.km else 'No'}"
            )
        
        return "I'm running and ready to help with After Effects edits. Ask me to perform an action like 'add text Hello' or 'apply glow effect'."
    
    def _run_tool_calling_loop(self, prompt: str, on_role_complete=None, on_response=None, on_step=None, max_iterations: int = 5, use_tools: bool = True) -> str:
        """
        Dynamic tool-calling loop: the LLM decides which tools to call.
        Tools: check_file_bridge_status, execute_extendscript, get_active_composition_info,
               list_selected_layers, get_effect_presets, search_project_footage,
               undo_last_action, take_screenshot_and_verify
        """
        if not self.km:
            return "No API keys available. Please configure Gemini keys."
        
        client, label = self.km.client_for_role(0)
        
        system_instruction = """You are an After Effects AI assistant with access to tools.
You help users edit After Effects compositions by generating and executing ExtendScript (JSX) code.

TOOLS:
- check_file_bridge_status() - Check if the file bridge to After Effects is active
- get_active_composition_info() - Get info about the active composition, layers, and effects
- execute_extendscript(jsx_code: str, description: str) - Execute JSX code in After Effects
- list_selected_layers() - Return name/type/properties of currently selected layers
- get_effect_presets(effect_name: str) - Return valid AE match-name + parameter info for a requested effect
- search_project_footage(query: str) - Find layers/footage items by name in the project
- undo_last_action() - Undo the last action in After Effects
- take_screenshot_and_verify() - Capture current frame and return saved screenshot filename

RULES:
- For AE edit requests (add text, glow, color grade, etc.), call execute_extendscript with valid JSX
- For status questions, call check_file_bridge_status
- For inspection requests, call get_active_composition_info or list_selected_layers
- If unsure about an effect name, call get_effect_presets first
- Always provide a friendly text response along with tool calls
- JSX must be valid ExtendScript (ES3, no arrow functions, no let/const)
- Wrap JSX in app.beginUndoGroup()/app.endUndoGroup()
- Start JSX with: var comp = app.project.activeItem; if (!comp || !(comp instanceof CompItem)) { throw new Error("No active composition"); }
- Use correct AE match names: ADBE Glo2, ADBE Lumetri, ADBE Gaussian Blur 2, ADBE Box Blur2, ADBE Directional Blur, ADBE Sharpen, ADBE Mosaic, ADBE Hue/Saturation, ADBE CurvesCustom, ADBE Easy Levels2
- After executing a visual change, consider take_screenshot_and_verify to confirm the result"""

        # Build initial contents using proper GenAI types
        if _GENAI_TYPES_AVAILABLE:
            contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        else:
            # Fallback simple format
            class _SimplePart:
                def __init__(self, text=None):
                    self.text = text
            class _SimpleContent:
                def __init__(self, role, parts):
                    self.role = role
                    self.parts = parts
            contents = [_SimpleContent(role="user", parts=[_SimplePart(text=prompt)])]
        
        final_response = ""
        retry_attempted = False
        
        for iteration in range(max_iterations):
            try:
                # Step: thinking
                if on_step:
                    on_step("thinking", "Reasoning about your request...")
                
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.3,
                    top_p=0.9,
                )
                if use_tools and AE_TOOLS:
                    config.tools = [AE_TOOLS]
                
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=contents,
                    config=config,
                )
                
                # Extract text response
                text_parts = []
                tool_calls = []
                
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text') and part.text:
                            text_parts.append(part.text)
                        if hasattr(part, 'function_call') and part.function_call:
                            tool_calls.append(part.function_call)
                
                final_response = "\n".join(text_parts) if text_parts else final_response
                
                if not tool_calls:
                    # No more tool calls, return the text response
                    break
                
                if not use_tools:
                    # If tools are disabled but model returned tool calls, ignore them
                    break
                
                # Execute tool calls
                tool_results = []
                for tc in tool_calls:
                    # Step: tool_call
                    if on_step:
                        on_step("tool_call", f"Calling {tc.name}", args=tc.args)
                    
                    result = self._execute_tool(tc.name, tc.args)
                    
                    # Step: tool_result
                    if on_step:
                        on_step("tool_result", f"{tc.name} finished", result=result)
                    
                    if _GENAI_TYPES_AVAILABLE:
                        tool_results.append(
                            types.Content(
                                role="tool",
                                parts=[types.Part.from_function_response(
                                    name=tc.name,
                                    response={"result": result},
                                    id=getattr(tc, 'id', None),
                                )]
                            )
                        )
                    else:
                        tool_results.append({
                            "role": "tool",
                            "parts": [{
                                "function_response": {
                                    "name": tc.name,
                                    "response": {"result": result}
                                }
                            }]
                        })
                
                # Add assistant response and tool results to contents
                if _GENAI_TYPES_AVAILABLE:
                    contents.append(response.candidates[0].content)
                    contents.extend(tool_results)
                else:
                    # Fallback
                    contents.append({"role": "model", "content": response.candidates[0].content.parts})
                    contents.extend(tool_results)
                
                retry_attempted = False
                
            except Exception as e:
                self._log(f"Tool calling iteration {iteration} failed: {e}")
                if on_step:
                    on_step("warning", f"Retrying after error: {e}")
                
                if not retry_attempted and iteration < max_iterations - 1:
                    retry_attempted = True
                    continue
                
                if not final_response:
                    final_response = f"I encountered an error: {e}"
                break
        
        # Step: done
        if on_step:
            on_step("done", "Response ready")
        
        if not final_response:
            final_response = "I processed your request but didn't generate a text response."
        
        if on_response:
            on_response(final_response)
        
        return final_response
    
    def _execute_tool(self, tool_name: str, args: Dict) -> str:
        """Execute a tool call and return the result string."""
        if tool_name == "check_file_bridge_status":
            status = self.get_bridge_status()
            return json.dumps(status, indent=2)
        
        elif tool_name == "get_active_composition_info":
            return self._get_composition_info_jsx()
        
        elif tool_name == "list_selected_layers":
            return self._list_selected_layers_jsx()
        
        elif tool_name == "get_effect_presets":
            effect_name = args.get("effect_name", "")
            return self._get_effect_presets_info(effect_name)
        
        elif tool_name == "search_project_footage":
            query = args.get("query", "")
            return self._search_project_footage_jsx(query)
        
        elif tool_name == "undo_last_action":
            return self._undo_last_action_jsx()
        
        elif tool_name == "take_screenshot_and_verify":
            return self._take_screenshot_and_verify_jsx()
        
        elif tool_name == "execute_extendscript":
            jsx_code = args.get("jsx_code", "")
            description = args.get("description", "AE edit")
            
            if not jsx_code.strip():
                return "Error: No JSX code provided"
            
            cmd_id = str(uuid.uuid4())[:8]
            cmd_data = {
                'id': cmd_id,
                'jsx': jsx_code,
                'prompt': description,
                'response_text': f"Executed: {description}"
            }
            self._command_queue.put(cmd_data)
            self._log(f"Queued JSX command {cmd_id}: {description}")
            # Track that a JSX command was actually queued
            self._last_queued_cmd_id = cmd_id
            return f"Successfully queued command '{description}' (id: {cmd_id})"
        
        else:
            return f"Unknown tool: {tool_name}"
    
    def _list_selected_layers_jsx(self) -> str:
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            return JSON.stringify({error: "No active composition"});
        }
        var layers = [];
        for (var i = 1; i <= comp.numLayers; i++) {
            var layer = comp.layer(i);
            if (layer.selected) {
                layers.push({
                    name: layer.name,
                    matchName: layer.matchName,
                    type: layer instanceof AVLayer ? "AVLayer" : (layer instanceof TextLayer ? "TextLayer" : (layer instanceof ShapeLayer ? "ShapeLayer" : "Layer")),
                    inPoint: layer.inPoint,
                    outPoint: layer.outPoint,
                    startTime: layer.startTime
                });
            }
        }
        JSON.stringify({selectedLayers: layers}, null, 2);
        '''
    
    def _get_effect_presets_info(self, effect_name: str) -> str:
        presets = {
            "glow": {"matchName": "ADBE Glo2", "params": ["ADBE Glo2-0001", "ADBE Glo2-0002", "ADBE Glo2-0003"]},
            "blur": {"matchName": "ADBE Gaussian Blur 2", "params": ["ADBE Gaussian Blur 2-0001"]},
            "fast box blur": {"matchName": "ADBE Box Blur2", "params": ["ADBE Box Blur2-0001"]},
            "directional blur": {"matchName": "ADBE Motion Blur", "params": ["ADBE Motion Blur-0001"]},
            "sharpen": {"matchName": "ADBE Sharpen", "params": ["ADBE Sharpen-0001"]},
            "mosaic": {"matchName": "ADBE Mosaic", "params": ["ADBE Mosaic-0001"]},
            "lumetri": {"matchName": "ADBE Lumetri", "params": ["ADBE Lumetri Color-0001"]},
            "hue/saturation": {"matchName": "ADBE HUE SATURATION", "params": ["ADBE HUE SATURATION-0001"]},
            "curves": {"matchName": "ADBE CurvesCustom", "params": ["ADBE CurvesCustom-0001"]},
            "levels": {"matchName": "ADBE Easy Levels2", "params": ["ADBE Easy Levels2-0001"]},
        }
        key = effect_name.lower().strip()
        match = presets.get(key)
        if not match:
            return json.dumps({"error": f"Unknown effect: {effect_name}", "available": list(presets.keys())})
        return json.dumps({"effect": effect_name, **match}, indent=2)
    
    def _search_project_footage_jsx(self, query: str) -> str:
        return f'''
        var project = app.project;
        var results = [];
        var term = "{query}".toLowerCase();
        for (var i = 1; i <= project.numItems; i++) {{
            var item = project.item(i);
            if (item && item.name && item.name.toLowerCase().indexOf(term) !== -1) {{
                results.push({{name: item.name, type: item.matchName || "Unknown"}});
            }}
        }}
        JSON.stringify({{query: "{query}", results: results}}, null, 2);
        '''
    
    def _undo_last_action_jsx(self) -> str:
        return '''
        try {
            app.undoManager.undo();
            return JSON.stringify({status: "success", message: "Undo performed"});
        } catch(e) {
            return JSON.stringify({status: "error", message: e.toString()});
        }
        '''
    
    def _take_screenshot_and_verify_jsx(self) -> str:
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            return JSON.stringify({error: "No active composition"});
        }
        var fileName = "screenshot_" + new Date().getTime() + ".png";
        var bridgeDir = new Folder(Folder.temp.fsName + "/ae_video_agent_bridge");
        var screenshotsDir = new Folder(bridgeDir.fsName + "/screenshots");
        if (!screenshotsDir.exists) screenshotsDir.create();
        var outFile = new File(screenshotsDir.fsName + "/" + fileName);
        try {
            comp.saveFrameToPng(comp.time, outFile);
            return JSON.stringify({status: "success", filename: fileName, path: outFile.fsName});
        } catch(e) {
            return JSON.stringify({status: "error", message: e.toString()});
        }
        '''
    
    def _get_composition_info_jsx(self) -> str:
        """Generate JSX to inspect the active composition."""
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            return JSON.stringify({error: "No active composition"});
        }
        var info = {
            name: comp.name,
            width: comp.width,
            height: comp.height,
            duration: comp.duration,
            frameRate: comp.frameRate,
            numLayers: comp.numLayers,
            selectedLayers: [],
            allLayers: []
        };
        for (var i = 1; i <= comp.numLayers; i++) {
            var layer = comp.layer(i);
            var layerInfo = {name: layer.name, type: layer.matchName};
            info.allLayers.push(layerInfo);
        }
        for (var j = 0; j < comp.selectedLayers.length; j++) {
            info.selectedLayers.push(comp.selectedLayers[j].name);
        }
        JSON.stringify(info, null, 2);
        '''
    
    # ==================== AI ROLES ====================
    
    def _call(self, role_index: int, system: str, user_text: str) -> str:
        """Call Gemini with a specific role's key, through rate limiter + backoff."""
        return self._call_with_backoff(
            role_index=role_index,
            system=system,
            user_text=user_text,
            model=MODEL,
            vision_parts=None,
            max_retries_429=MAX_RETRIES_429,
            max_retries_503=MAX_RETRIES_503,
        )

    def _call_vision(self, role_index: int, system: str, user_text: str,
                     vision_parts: list) -> str:
        """Call Gemini with vision (multimodal) parts, through rate limiter + backoff."""
        return self._call_with_backoff(
            role_index=role_index,
            system=system,
            user_text=user_text,
            model=VISION_MODEL,
            vision_parts=vision_parts,
            max_retries_429=MAX_RETRIES_429,
            max_retries_503=MAX_RETRIES_503,
        )

    def _call_with_backoff(
        self,
        role_index: int,
        system: str,
        user_text: str,
        model: str,
        vision_parts=None,
        max_retries_429: int = MAX_RETRIES_429,
        max_retries_503: int = MAX_RETRIES_503,
    ) -> str:
        """
        Core call wrapped with retry + backoff.
        Works with both OmniRoute (via shim) and native Gemini clients.
        """
        client, label = self.km.client_for_role(role_index)

        if _GENAI_TYPES_AVAILABLE:
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.3,
                top_p=0.9
            )
            parts = []
            if vision_parts:
                parts.extend(vision_parts)
            parts.append(types.Part(text=user_text))
            contents = [types.Content(role="user", parts=parts)]
        else:
            class _SimpleConfig:
                def __init__(self, system_instruction, temperature, top_p):
                    self.system_instruction = system_instruction
                    self.temperature = temperature
                    self.top_p = top_p
            class _SimplePart:
                def __init__(self, text=None):
                    self.text = text
            class _SimpleContent:
                def __init__(self, role, parts):
                    self.role = role
                    self.parts = parts
            config = _SimpleConfig(system_instruction=system, temperature=0.3, top_p=0.9)
            parts = []
            if vision_parts:
                parts.extend(vision_parts)
            parts.append(_SimplePart(text=user_text))
            contents = [_SimpleContent(role="user", parts=parts)]

        estimated_tokens = self.rate_limiter._estimate_tokens(system + user_text, vision_images=bool(vision_parts))
        self.rate_limiter.check_and_wait(role_index, estimated_tokens)

        backoff = BACKOFF_BASE
        last_error = None

        for attempt in range(max_retries_429):
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
                return "".join(p.text or "" for p in response.candidates[0].content.parts)
            except QuotaExhaustedError:
                raise
            except Exception as e:
                error_code = getattr(e, "code", None) or getattr(e, "status_code", None)
                is_rate_limit = error_code == 429 or "429" in str(e) or "rate" in str(e).lower()
                is_server_err = error_code in (500, 502, 503) or "503" in str(e)

                if is_rate_limit and attempt < max_retries_429 - 1:
                    sleep_time = min(backoff, BACKOFF_CAP)
                    self._log(f"  Rate limited, backing off {sleep_time:.1f}s (attempt {attempt+1}/{max_retries_429})")
                    time.sleep(sleep_time)
                    backoff *= 2
                    self.rate_limiter.check_and_wait(role_index, estimated_tokens)
                    continue
                elif is_server_err and attempt < max_retries_503:
                    sleep_time = min(backoff, BACKOFF_CAP)
                    self._log(f"  Server error, backing off {sleep_time:.1f}s")
                    time.sleep(sleep_time)
                    backoff *= 2
                    continue

                last_error = e
                if attempt < max_retries_429 - 1:
                    time.sleep(min(backoff, BACKOFF_CAP))
                    backoff *= 2
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("Exhausted all retry attempts")
    
    def _call_planner(self, prompt: str) -> str:
        system = f"""You are 🧠 Planner, the first role in a 5-role AI video editing agent pipeline.
Your job: Analyze the user's request. 
IMPORTANT: If the user's prompt is a general question, greetings, or conversational ask (e.g. "what can you do?", "hi", "how does this work?"), DO NOT generate an execution plan for After Effects. 
Instead, output "NO_AE_ACTION_NEEDED" followed by a short, friendly explanation of your video editing abilities.

If the prompt IS an action or inspection request for After Effects, break it into a clear, actionable plan focusing on:
- What layers need to be created/modified
- What effects should be applied and in what order
- What property changes are needed
- Whether the request is an inspection (reading project state) or an action

Use these After Effects facts:
{AE_EFFECTS}

Respond with ONLY the plan as numbered steps (or NO_AE_ACTION_NEEDED <response> if conversational). Keep it concise."""
        return self._call(ROLE_PLANNER, system, f"REQUEST: {prompt}")
    
    def _call_designer(self, prompt: str, plan: str) -> str:
        system = f"""You are 🎨 Designer, the second role in the AI video editing pipeline.
Your job: Turn the plan into a concrete technical approach.
Decide:
- Which layers to create (solids, text, null, adjustment layers)
- Effect match names and how to apply them
- Property values to set (positions, scales, colors)
- Whether to target selected layers or create new ones

Use these After Effects effect match names:
{AE_EFFECTS}

Respond with ONLY the technical approach. Include specific JSX snippets for key steps."""
        return self._call(ROLE_DESIGNER, system, f"REQUEST:\n{prompt}\n\nPLAN:\n{plan}")
    
    def _call_coder(self, prompt: str, plan: str, design: str) -> str:
        system = f"""You are 💻 Coder, the third role in the AI video editing pipeline.
Your job: Generate valid ExtendScript (JSX) code for After Effects.

Key rules:
1. Always start with: var comp = app.project.activeItem;
2. Check: if (!comp || !(comp instanceof CompItem)) {{ throw new Error("No active composition"); }}
3. Use "ADBE Effect Parade" not "Effects" for adding effects
4. Use CORRECT match names: ADBE Glo2, ADBE Lumetri, ADBE CurvesCustom, etc.
5. Wrap in app.beginUndoGroup()/app.endUndoGroup()
6. For Lumetri: add effect but DO NOT set deep properties (causes errors)
7. End with JSON.stringify(...) if returning data, otherwise just return undefined

Use these effects:
{AE_EFFECTS}

Respond with ONLY the JSX code, no explanation, no markdown fences."""
        return self._call(ROLE_CODER, system, 
            f"REQUEST:\n{prompt}\n\nPLAN:\n{plan}\n\nAPPROACH:\n{design}")
    
    def _call_reviewer(self, jsx: str, plan: str) -> str:
        system = """You are 🔍 Reviewer, the fourth role in the AI video editing pipeline.
Your job: Review the JSX code for correctness and fix issues.
Check for:
1. Correct effect match names (ADBE Glo2 not ADBE Glow, ADBE Lumetri not ADBE Color)
2. Proper "ADBE Effect Parade" usage (not "Effects")
3. Comp existence checks
4. Proper undo group usage
5. ES3-compatible code (no arrow functions, no const/let, no .trim())
6. No Lumetri deep property access

Respond with ONLY the corrected/final JSX code, no explanation."""
        return self._call(ROLE_REVIEWER, system, f"CODE TO REVIEW:\n{jsx}\n\nPLAN:\n{plan}")
    
    def _call_reporter(self, prompt: str, plan: str, design: str, jsx: str) -> str:
        system = """You are 📝 Reporter, the final role in the AI video editing pipeline.
Your job: Write a concise, friendly response to the user explaining what was done.
Mention what actions were taken and what to expect in After Effects.
Keep it to 1-3 sentences. No markdown."""
        return self._call(ROLE_REPORTER, system,
            f"REQUEST: {prompt}\n\nPLAN: {plan}\n\nAPPROACH: {design}\n\nCODE: {jsx[:500]}")
    
    # ==================== ANALYST (Role 6) & Vision ====================
    
    def _call_analyst(self, video_path: str, user_request: str) -> Dict[str, Any]:
        """
        🎥 Analyst role: OpenCV scene-change detection + Gemini vision.
        Returns analysis dict with scene list, story structure, redundant flags.
        """
        cache_key = self._cache_key_for_file(video_path)
        cached = self._load_cached_analysis(cache_key)
        if cached:
            self._log("🎥 Analyst: Using cached analysis")
            return cached
        
        self._log("🎥 Analyst: Detecting scenes with OpenCV...")
        scene_changes = self._detect_scenes(video_path)
        self._log(f"🎥 Analyst: Found {len(scene_changes)} scene boundaries")
        
        if not scene_changes:
            result = {"scenes": [], "story_structure": [], "redundant_segments": []}
            self._save_cached_analysis(cache_key, result)
            return result
        
        # Batch scene frames (3-5 per call) to conserve RPD
        scene_frames = self._extract_scene_frames(video_path, scene_changes, max_frames=5)
        
        self._log(f"🎥 Analyst: Sending {len(scene_frames)} frames to Gemini vision (batched)...")
        
        segment_jsons = []
        for i in range(0, len(scene_frames), 4):
            batch = scene_frames[i:i+4]
            if not batch:
                break
            segments = self._batch_analyze_scenes(batch, user_request, video_path)
            segment_jsons.extend(segments)
        
        # Aggregation call
        self._log("🎥 Analyst: Aggregating scene analysis...")
        aggregated = self._aggregate_analysis(segment_jsons, user_request)
        
        result = {
            "scenes": aggregated.get("scenes", segment_jsons),
            "story_structure": aggregated.get("story_structure", []),
            "redundant_segments": aggregated.get("redundant_segments", []),
            "scene_count": len(segment_jsons),
        }
        
        self._save_cached_analysis(cache_key, result)
        return result
    
    def _cache_key_for_file(self, video_path: str) -> str:
        """Generate cache key from file hash + mtime."""
        try:
            stat = os.stat(video_path)
            mtime = str(stat.st_mtime)
            file_hash = hashlib.md5(Path(video_path).read_bytes()[:4096]).hexdigest()
            return f"{file_hash}_{mtime}"
        except Exception:
            return hashlib.md5(video_path.encode()).hexdigest()
    
    def _load_cached_analysis(self, cache_key: str) -> Optional[Dict]:
        cache_file = self.analysis_cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass
        return None
    
    def _save_cached_analysis(self, cache_key: str, data: Dict) -> None:
        cache_file = self.analysis_cache_dir / f"{cache_key}.json"
        cache_file.write_text(json.dumps(data, indent=2))
    
    def _detect_scenes(self, video_path: str, sample_interval: float = 0.5,
                       diff_threshold: float = 30.0) -> List[Dict]:
        """
        OpenCV-based scene change detection.
        Samples every sample_interval seconds, computes frame diff histogram.
        Only returns frames where diff crosses threshold (RPD-friendly).
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            self._log("⚠️ OpenCV not available for scene detection")
            return []
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self._log(f"⚠️ Cannot open video: {video_path}")
            return []
        
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        
        scene_changes = []
        prev_frame = None
        frame_idx = 0
        sample_step = max(1, int(sample_interval * fps))
        
        while True:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break
            
            timestamp = frame_idx / fps
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (128, 72))  # small for speed
            
            if prev_frame is not None:
                # Compute histogram difference
                hist_prev = cv2.calcHist([prev_frame], [0], None, [16], [0, 256])
                hist_curr = cv2.calcHist([gray], [0], None, [16], [0, 256])
                diff = cv2.compareHist(hist_prev, hist_curr, cv2.HISTCMP_BHATTACHARYYA)
                
                if diff > diff_threshold / 100.0:  # normalize threshold
                    scene_changes.append({
                        "timestamp": round(timestamp, 2),
                        "frame_idx": frame_idx,
                        "hist_diff": float(diff),
                    })
            
            prev_frame = gray.copy()
            frame_idx += sample_step
            
            if frame_idx >= total_frames:
                break
        
        cap.release()
        
        # Merge very close scene changes (<0.3s apart)
        merged = []
        for sc in scene_changes:
            if merged and sc["timestamp"] - merged[-1]["timestamp"] < 0.3:
                merged[-1] = sc  # keep the latest
            else:
                merged.append(sc)
        
        return merged
    
    def _extract_scene_frames(self, video_path: str, scene_changes: List[Dict],
                              max_frames: int = 5) -> List[Dict]:
        """Extract JPEG bytes for scene-change frames, up to max_frames per batch."""
        try:
            import cv2
        except ImportError:
            return []
        
        cap = cv2.VideoCapture(video_path)
        frames = []
        for sc in scene_changes[:max_frames]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, sc["frame_idx"])
            ret, frame = cap.read()
            if ret:
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                frames.append({
                    "bytes": buffer.tobytes(),
                    "timestamp": sc["timestamp"],
                })
        cap.release()
        return frames
    
    def _batch_analyze_scenes(self, frames: List[Dict], user_request: str,
                              video_path: str) -> List[Dict]:
        """Send 3-5 scene frames in one Gemini vision call, get segment JSON."""
        vision_parts = []
        for f in frames:
            vision_parts.append(_make_vision_part(
                data=f["bytes"],
                mime_type="image/jpeg",
            ))
        
        timestamp_list = ", ".join(str(f["timestamp"]) for f in frames)
        system = f"""You are 🎥 Analyst, the vision/analysis role in a video editing agent pipeline.
Analyze these scene-change frames from a video (sampled at timestamps: {timestamp_list}).
The user's request is: '{user_request}'

Return ONLY valid JSON — an array of segment objects:
[
  {{
    "start_time": <seconds>,
    "end_time": <seconds>,
    "description": "What is happening in this scene — visual content, mood, energy",
    "suggested_edit_points": ["cut", "keep", "slow_down", "speed_up", "add_broll", "trim"]
  }}
]

Be concise. Do not include any text outside the JSON."""
        
        user_text = f"Video file: {os.path.basename(video_path)}. Analyze these {len(frames)} scene frames."
        
        response = self._call_vision(
            role_index=ROLE_ANALYST,
            system=system,
            user_text=user_text,
            vision_parts=vision_parts,
        )
        
        # Clean up response — extract just the JSON
        response = response.strip()
        if response.startswith("```json"):
            response = response[6:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            self._log(f"🎥 Analyst: Failed to parse JSON, attempting cleanup")
            # Try to extract JSON block
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
            return [{"start_time": 0, "end_time": 0, "description": "Parse error", "suggested_edit_points": ["keep"]}]
    
    def _aggregate_analysis(self, segments: List[Dict], user_request: str) -> Dict:
        """Final aggregation call: merge segments into story structure + redundant flags."""
        segments_json = json.dumps(segments, indent=2)
        
        system = f"""You are 🎥 Analyst (Aggregation). 
You have per-segment analysis of a video. Merge into:
1. A clean scene list (start_time, end_time, description)
2. A rough story structure (act/beat breakdown — intro, rising action, climax, resolution)
3. Flag weak/redundant segments (dead air, duplicates, low energy)

Respond with ONLY valid JSON:
{{
  "scenes": [{"start_time": float, "end_time": float, "description": str}],
  "story_structure": [{"beat": str, "start_time": float, "end_time": float}],
  "redundant_segments": [{"start_time": float, "end_time": float, "reason": str}]
}}"""
        
        user_text = f"User request: {user_request}\nSegments:\n{segments_json}"
        
        response = self._call(
            role_index=ROLE_ANALYST % NUM_KEYS,  # Analyst uses pool key
            system=system,
            user_text=user_text,
        )
        
        response = response.strip()
        if response.startswith("```json"):
            response = response[6:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
            return {"scenes": segments, "story_structure": [], "redundant_segments": []}
    
    # ==================== Auto-Cut / Rough-Cut ====================
    
    def suggest_cuts(self, analysis: Dict) -> List[Dict]:
        """
        Pure-logic cut suggestions (no Gemini calls).
        Flags dead air, duplicate takes, low-motion filler from analysis data.
        """
        cuts = []
        
        # Dead air detection from redundant segments
        for seg in analysis.get("redundant_segments", []):
            if "dead air" in seg.get("reason", "").lower():
                cuts.append({
                    "type": "cut",
                    "start_time": seg["start_time"],
                    "end_time": seg["end_time"],
                    "reason": seg["reason"],
                })
        
        # Low-energy segments (from scene descriptions)
        scenes = analysis.get("scenes", [])
        for i, scene in enumerate(scenes):
            desc = scene.get("description", "").lower()
            if any(word in desc for word in ["static", "still", "motionless", "quiet", "empty"]):
                duration = scene["end_time"] - scene["start_time"]
                if duration > 2.0:
                    cuts.append({
                        "type": "trim",
                        "start_time": scene["start_time"],
                        "end_time": scene["start_time"] + min(1.0, duration / 2),
                        "reason": "Low-energy segment — trim start for pacing",
                    })
        
        # Duplicate/similar adjacent scenes
        for i in range(1, len(scenes)):
            prev = scenes[i-1]
            curr = scenes[i]
            if abs(curr["start_time"] - prev["end_time"]) < 0.5:
                prev_words = set(prev.get("description", "").lower().split())
                curr_words = set(curr.get("description", "").lower().split())
                if prev_words and curr_words:
                    overlap = len(prev_words & curr_words) / max(len(prev_words | curr_words), 1)
                    if overlap > 0.7:
                        cuts.append({
                            "type": "merge",
                            "start_time": prev["start_time"],
                            "end_time": curr["end_time"],
                            "reason": "Duplicate/similar adjacent scene — consider merging",
                        })
        
        return cuts
    
    # ==================== Screenshot / Visual Verification ====================
    
    def set_vision_check(self, enabled: bool) -> None:
        """Enable/disable the Reviewer vision-check pass."""
        self._vision_check_enabled = enabled
        self._log(f"Vision check: {'enabled' if enabled else 'disabled'}")
    
    def _capture_screenshot(self, screenshot_name: str) -> Optional[str]:
        """
        Send a command to AE panel to capture current comp frame to PNG.
        Returns the path where the screenshot will be saved.
        """
        cmd_id = str(uuid.uuid4())[:8]
        screenshot_path = str(self.screenshots_dir / f"{screenshot_name}.png")
        
        # This is sent as a special command; the AE panel has a registered handler
        jsx = (
            'var comp = app.project.activeItem;\n'
            'if (comp && comp instanceof CompItem) {\n'
            '    var outFile = new File("' + screenshot_path.replace('\\', '\\\\') + '");\n'
            '    try {\n'
            '        comp.saveFrameToPng(comp.time, outFile);\n'
            '        outFile.exists;\n'
            '    } catch(e) {\n'
            '        // Fallback: not available, signal error\n'
            '        throw new Error("saveFrameToPng not available");\n'
            '    }\n'
            '}'
        )
        
        cmd_data = {
            'id': cmd_id,
            'jsx': jsx,
            'prompt': f'Screenshot capture: {screenshot_name}',
            'response_text': '',
        }
        self._command_queue.put(cmd_data)
        return screenshot_path
    
    def _call_reviewer_vision_check(self, jsx: str, plan: str, comp_time: float = 0) -> str:
        """
        OPTIONAL role: Reviewer does a vision check — captures frame, sends to Gemini
        vision with the original request, asks 'does this match intent'.
        """
        screenshot_path = self._capture_screenshot(f"review_check_{int(time.time())}")
        if not screenshot_path or not os.path.exists(screenshot_path):
            self._log("⚠️ Screenshot not available for vision check")
            return jsx
        
        try:
            image_bytes = Path(screenshot_path).read_bytes()
        except Exception:
            self._log("⚠️ Could not read screenshot for vision check")
            return jsx
        
        vision_part = _make_vision_part(data=image_bytes, mime_type="image/png")
        
        system = """You are 🔍 Reviewer (Vision Check).
After executing JSX in After Effects, you receive the current comp frame.
Does the visual result match the intended edit? Report yes/no, and if no,
suggest what JSX fix is needed."""
        
        user_text = f"Original plan: {plan}\nExecuted JSX:\n{jsx[:300]}\nCurrent comp time: {comp_time}s\nDoes the visual match intent?"
        
        response = self._call_vision(
            role_index=ROLE_REVIEWER,
            system=system,
            user_text=user_text,
            vision_parts=[vision_part],
        )
        
        self._log(f"🔍 Reviewer (vision check): {response[:100]}")
        
        # If reviewer says it doesn't match, generate corrected JSX
        if "no" in response.lower() or "doesn't" in response.lower():
            self._log("🔍 Reviewer: Mismatch detected — regenerating JSX")
            return self._call_reviewer(jsx, plan)  # fallback to text review
        
        return jsx
    
    def _get_element_placement(self, user_text: str) -> Optional[Dict]:
        """
        Vision-based element placement: capture current frame, send to Gemini
        with placement instruction, get normalized coordinates back.
        """
        screenshot_path = self._capture_screenshot(f"placement_{int(time.time())}")
        if not screenshot_path or not os.path.exists(screenshot_path):
            return None
        
        try:
            image_bytes = Path(screenshot_path).read_bytes()
        except Exception:
            return None
        
        vision_part = _make_vision_part(data=image_bytes, mime_type="image/png")
        
        system = """You are 💻 Coder (Element Placement).
Given a comp frame and a placement instruction, determine where to place
the element. Return ONLY JSON: {"x": float(0-1), "y": float(0-1), "anchor_x": float(0-1), "anchor_y": float(0-1)}
where (0,0) is top-left and (1,1) is bottom-right of the frame.
Normalized coordinates — no units."""
        
        response = self._call_vision(
            role_index=ROLE_CODER,
            system=system,
            user_text=user_text,
            vision_parts=[vision_part],
        )
        
        try:
            return json.loads(response.strip().strip("```").strip())
        except (json.JSONDecodeError, ValueError):
            self._log(f"⚠️ Placement parse failed: {response[:100]}")
            return None
    
    # ==================== Camera-Tracking Assist ====================
    
    def _detect_track_points(self, video_path: str, max_points: int = 20) -> List[Dict]:
        """
        Pure OpenCV feature detection — no Gemini calls.
        Uses goodFeaturesToTrack (Shi-Tomasi corner detection).
        Returns list of {x, y, frame_idx} trackable points.
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            self._log("⚠️ OpenCV not available for track point detection")
            return []
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        
        # Use first frame for track point detection
        ret, frame = cap.read()
        if not ret:
            cap.release()
            return []
        
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners = cv2.goodFeaturesToTrack(
            gray,
            maxCorners=max_points,
            qualityLevel=0.05,
            minDistance=30,
            blockSize=5,
        )
        
        cap.release()
        
        track_points = []
        if corners is not None:
            h, w = gray.shape[:2]
            for i, corner in enumerate(corners):
                x, y = corner.ravel()
                track_points.append({
                    "x": float(x) / w,  # normalized
                    "y": float(y) / h,
                    "frame_idx": 0,
                })
        
        return track_points
    
    def _generate_tracking_jsx(self, track_points: List[Dict]) -> str:
        """
        Generate JSX to:
        1. Create a Null layer (tracking target)
        2. Trigger AE's native Camera Tracker
        """
        if not track_points:
            return ""
        
        # Pick top 5 strongest points for null placement
        pts = track_points[:5]
        coords_str = ", ".join(f"[{p['x']}, {p['y']}]" for p in pts)
        
        return f'''
var comp = app.project.activeItem;
if (!comp || !(comp instanceof CompItem)) {{
    throw new Error("No active composition");
}}
app.beginUndoGroup("AI Tracking Assist");

// Create null layer for tracking target
var nullLayer = comp.layers.addNull();
nullLayer.name = "AI_Track_Point";
nullLayer.property("ADBE Transform Group").property("ADBE Position").setValue(
    [comp.width/2, comp.height/2]
);

// Trigger native Camera Tracker
app.executeCommand(app.findMenuCommandId("Track Camera"));

// Store track point data on the null for reference
nullLayer.comment = "Track points (normalized): [{coords_str}]";

app.endUndoGroup();
'''
    
    # ==================== Pro Editing Features ====================
    
    def _color_match_clips(self, clip_paths: List[str]) -> str:
        """
        Pure OpenCV: sample average color/Luminance per segment,
        generate Lumetri JSX to normalize exposure across clips.
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            return ""
        
        averages = []
        for clip in clip_paths:
            cap = cv2.VideoCapture(clip)
            if not cap.isOpened():
                averages.append(None)
                continue
            # Sample 5 frames from the middle 50% of each clip
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            start = int(total * 0.25)
            end = int(total * 0.75)
            step = max(1, (end - start) // 5)
            
            frames_mean = []
            for i in range(start, end, step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ret, frame = cap.read()
                if ret:
                    # Convert to LAB for perceptual uniformity
                    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                    mean_l = float(np.mean(lab[:,:,0]))
                    mean_a = float(np.mean(lab[:,:,1]))
                    mean_b = float(np.mean(lab[:,:,2]))
                    frames_mean.append((mean_l, mean_a, mean_b))
            
            if frames_mean:
                avg = tuple(np.mean(frames_mean, axis=0))
                averages.append(avg)
            else:
                averages.append(None)
            cap.release()
        
        # Find target = clip closest to median brightness
        valid = [(i, a) for i, a in enumerate(averages) if a is not None]
        if not valid or len(valid) < 2:
            return ""
        
        median_l = np.median([a[0] for _, a in valid])
        target_idx = min(valid, key=lambda x: abs(x[1][0] - median_l))[0]
        target_avg = averages[target_idx]
        
        # Generate Lumetri correction per clip
        jsx_lines = [
            'var comp = app.project.activeItem;',
            'if (!comp || !(comp instanceof CompItem)) {',
            '    throw new Error("No active composition");',
            '}',
            'app.beginUndoGroup("AI Color Match");',
        ]
        
        for i, avg in enumerate(averages):
            if avg is None:
                continue
            dl = target_avg[0] - avg[0]
            da = target_avg[1] - avg[1]
            db = target_avg[2] - avg[2]
            # Convert LAB delta to approximate Lumetri offset
            exposure_offset = dl / 10.0  # rough mapping
            jsx_lines.extend([
                f'var layer_{i} = comp.layer({i+1});',
                f'if (layer_{i}) {{',
                f'    var fx = layer_{i}.property("ADBE Effect Parade").addProperty("ADBE Lumetri");',
                f'    // Exposure offset: {exposure_offset:.2f}, tint: a={da:.1f} b={db:.1f}',
                f'}}',
            ])
        
        jsx_lines.extend([
            'app.endUndoGroup();',
        ])
        
        return '\n'.join(jsx_lines)
    
    def _beat_sync_cuts(self, video_path: str, target_bpm: Optional[int] = None) -> List[Dict]:
        """
        librosa onset detection on audio → align cut points to beats.
        No Gemini calls — pure audio analysis.
        """
        try:
            import librosa
            import numpy as np
        except ImportError:
            self._log("⚠️ librosa not available for beat-sync")
            return []
        
        try:
            y, sr = librosa.load(video_path, sr=None, mono=True, duration=120)
        except Exception as e:
            self._log(f"⚠️ librosa failed: {e}")
            return []
        
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        beat_times = librosa.frames_to_time(beats, sr=sr)
        
        cuts = []
        for i in range(1, len(beat_times)):
            cuts.append({
                "type": "cut",
                "time": round(float(beat_times[i]), 2),
                "reason": "Beat-synced cut point",
                "bpm": round(float(tempo)),
            })
        
        return cuts
    
    def _auto_captions(self, video_path: str, style: str = "subtitle") -> str:
        """
        Whisper transcription with timestamps → JSX text layers.
        """
        try:
            import whisper
        except ImportError:
            self._log("⚠️ openai-whisper not installed — auto-captions unavailable")
            return self._empty_captions_jsx()
        
        try:
            model = whisper.load_model("base")
            result = model.transcribe(video_path, word_timestamps=True)
        except Exception as e:
            self._log(f"⚠️ Whisper transcription failed: {e}")
            return self._empty_captions_jsx()
        
        # Generate JSX text layers for each segment
        jsx_lines = [
            'var comp = app.project.activeItem;',
            'if (!comp || !(comp instanceof CompItem)) {',
            '    throw new Error("No active composition");',
            '}',
            'app.beginUndoGroup("AI Auto Captions");',
        ]
        
        segments = result.get("segments", [])
        for seg in segments:
            text = seg.get("text", "").strip()
            start = seg.get("start", 0)
            end = seg.get("end", 0)
            if not text:
                continue
            safe_text = text.replace('"', '\\"').replace('\n', ' ')
            y_offset = 50 + (len(jsx_lines) * 30) % (1080 - 100)
            jsx_lines.extend([
                f'var txt = comp.layers.addText("{safe_text}");',
                f'txt.name = "Caption: {start:.1f}s";',
                f'txt.property("ADBE Transform Group").property("ADBE Position").setValue([{max(320, 0)}, {y_offset}]);',
                f'txt.inPoint = {start:.2f};',
                f'txt.outPoint = {end:.2f};',
                f'txt.property("ADBE Transform Group").property("ADBE Opacity").setValue(100);',
            ])
        
        jsx_lines.append('app.endUndoGroup();')
        return '\n'.join(jsx_lines)
    
    def _empty_captions_jsx(self) -> str:
        return (
            'var comp = app.project.activeItem;\n'
            'if (!comp || !(comp instanceof CompItem)) {\n'
            '    throw new Error("No active composition");\n'
            '}\n'
            'app.beginUndoGroup("AI Auto Captions");\n'
            '// Whisper not available — skipping auto captions\n'
            'app.endUndoGroup();\n'
        )
    
    def _suggest_broll(self, analysis: Dict) -> List[Dict]:
        """
        Pure-logic B-roll suggestions based on Analyst data.
        Flags low visual-variety stretches and suggests cutaway insertion points.
        """
        suggestions = []
        scenes = analysis.get("scenes", [])
        
        for i, scene in enumerate(scenes):
            desc = scene.get("description", "").lower()
            duration = scene.get("end_time", 0) - scene.get("start_time", 0)
            
            # Low visual variety = same subject, static
            low_variety = any(word in desc for word in ["still", "static", "empty", "quiet", "motionless"])
            if low_variety and duration > 3.0:
                suggestions.append({
                    "insert_at": scene["start_time"] + duration / 2,
                    "scene_start": scene["start_time"],
                    "scene_end": scene["end_time"],
                    "reason": "Low visual variety — insert cutaway/B-roll",
                    "type": "broll_insertion",
                })
        
        return suggestions
    
    def _intro_outro_jsx(self, template: str = "cinematic", text: str = "AI Generated") -> str:
        """
        Reuse existing JSX helpers as building blocks for intro/outro templates.
        """
        safe_text = text.replace('"', '\\"')
        
        if template == "cinematic":
            return f'''
var comp = app.project.activeItem;
if (!comp || !(comp instanceof CompItem)) {{
    throw new Error("No active composition");
}}
app.beginUndoGroup("AI Intro");

// Create solid background
var bg = comp.layers.addSolid([0, 0, 0], "Intro_BG", comp.width, comp.height, comp.pixelAspect, 3.0);
bg.inPoint = 0;
bg.outPoint = 3.0;

// Add text
var txt = comp.layers.addText("{safe_text}");
txt.property("ADBE Transform Group").property("ADBE Position").setValue([comp.width / 2, comp.height / 2]);

// Add glow (reuse existing helper pattern)
var glowFx = txt.property("ADBE Effect Parade").addProperty("ADBE Glo2");

// Fade in/out keyframes
var txtProp = txt.property("ADBE Transform Group");
var opacity = txtProp.property("ADBE Opacity");
opacity.setValueAtTime(0, 0);
opacity.setValueAtTime(0.5, 100);
opacity.setValueAtTime(2.5, 100);
opacity.setValueAtTime(3.0, 0);

// Slide-up position keyframes
var posProp = txtProp.property("ADBE Position");
var startY = comp.height * 0.7;
var endY = comp.height * 0.5;
posProp.setValueAtTime(0, [comp.width / 2, startY]);
posProp.setValueAtTime(0.5, [comp.width / 2, endY]);
posProp.setValueAtTime(2.5, [comp.width / 2, endY]);
posProp.setValueAtTime(3.0, [comp.width / 2, startY + 100]);

app.endUndoGroup();
'''
        elif template == "minimal":
            return f'''
var comp = app.project.activeItem;
if (!comp || !(comp instanceof CompItem)) {{
    throw new Error("No active composition");
}}
app.beginUndoGroup("AI Outro");

var txt = comp.layers.addText("{safe_text}");
txt.property("ADBE Transform Group").property("ADBE Position").setValue([comp.width / 2, comp.height * 0.7]);

var lumetri = txt.property("ADBE Effect Parade").addProperty("ADBE Lumetri");

app.endUndoGroup();
'''
        else:
            return self._glow_jsx()  # fallback to existing
    
    # ==================== Fallback template-based parsing ====================
    
    def _template_parse_prompt(self, prompt_text: str) -> str:
        """Template-based JSX generation (used when no API keys available)."""
        prompt = prompt_text.lower().strip()
        
        if "text" in prompt or "title" in prompt:
            clean_text = prompt_text
            for kw in ["add text", "text", "title"]:
                clean_text = clean_text.replace(kw, "").strip()
            if not clean_text:
                clean_text = prompt_text
            safe_text = clean_text.replace('"', '\\"').replace('\n', '\\n')
            return (
                'var comp = app.project.activeItem;\n'
                'if (!comp || !(comp instanceof CompItem)) {\n'
                '    throw new Error("No active composition");\n'
                '}\n'
                'app.beginUndoGroup("AI Agent Command");\n'
                'var txt = comp.layers.addText("' + safe_text + '");\n'
                'txt.property("ADBE Transform Group").property("ADBE Position").setValue([comp.width / 2, comp.height / 2]);\n'
                'app.endUndoGroup();\n'
            )
        elif "glow" in prompt or "shine" in prompt:
            return self._glow_jsx()
        elif "color" in prompt or "grade" in prompt or "lumetri" in prompt:
            return self._lumetri_jsx()
        elif "curves" in prompt:
            return self._curves_jsx()
        elif "blur" in prompt:
            return self._blur_jsx()
        elif "levels" in prompt:
            return self._levels_jsx()
        elif "hue" in prompt or "saturation" in prompt:
            return self._hue_sat_jsx()
        elif "tint" in prompt:
            return self._tint_jsx()
        elif "render" in prompt:
            return (
                'var comp = app.project.activeItem;\n'
                'if (comp && comp instanceof CompItem) {\n'
                '    app.project.renderQueue.items.add(comp);\n'
                '}'
            )
        elif "inspect" in prompt or "list" in prompt or "project" in prompt:
            return self._inspection_jsx()
        else:
            safe_text = prompt_text.replace('"', '\\"').replace('\n', '\\n')
            return (
                'var comp = app.project.activeItem;\n'
                'if (!comp || !(comp instanceof CompItem)) {\n'
                '    throw new Error("No active composition");\n'
                '}\n'
                'app.beginUndoGroup("AI Agent Command");\n'
                'comp.layers.addSolid([0.2, 0.4, 0.8], "' + safe_text + '", comp.width, comp.height, 1, comp.duration);\n'
                'app.endUndoGroup();\n'
            )
    
    def _glow_jsx(self):
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            throw new Error("No active composition");
        }
        app.beginUndoGroup("AI Agent Command");
        if (comp.selectedLayers.length > 0) {
            for (var i = 0; i < comp.selectedLayers.length; i++) {
                comp.selectedLayers[i].property("ADBE Effect Parade").addProperty("ADBE Glo2");
            }
        } else {
            var adj = comp.layers.addSolid([1,1,1], "AI_Glow_Pass", comp.width, comp.height, comp.pixelAspect, comp.duration);
            adj.adjustmentLayer = true;
            adj.property("ADBE Effect Parade").addProperty("ADBE Glo2");
        }
        app.endUndoGroup();
        '''
    
    def _lumetri_jsx(self):
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            throw new Error("No active composition");
        }
        app.beginUndoGroup("AI Agent Command");
        if (comp.selectedLayers.length > 0) {
            for (var i = 0; i < comp.selectedLayers.length; i++) {
                comp.selectedLayers[i].property("ADBE Effect Parade").addProperty("ADBE Lumetri");
            }
        } else {
            var adj = comp.layers.addSolid([1,1,1], "AI_Color_Grade", comp.width, comp.height, comp.pixelAspect, comp.duration);
            adj.adjustmentLayer = true;
            adj.property("ADBE Effect Parade").addProperty("ADBE Lumetri");
        }
        app.endUndoGroup();
        '''
    
    def _curves_jsx(self):
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            throw new Error("No active composition");
        }
        app.beginUndoGroup("AI Agent Command");
        if (comp.selectedLayers.length > 0) {
            for (var i = 0; i < comp.selectedLayers.length; i++) {
                comp.selectedLayers[i].property("ADBE Effect Parade").addProperty("ADBE CurvesCustom");
            }
        } else {
            var adj = comp.layers.addSolid([1,1,1], "AI_Curves", comp.width, comp.height, comp.pixelAspect, comp.duration);
            adj.adjustmentLayer = true;
            adj.property("ADBE Effect Parade").addProperty("ADBE CurvesCustom");
        }
        app.endUndoGroup();
        '''
    
    def _blur_jsx(self):
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            throw new Error("No active composition");
        }
        app.beginUndoGroup("AI Agent Command");
        if (comp.selectedLayers.length > 0) {
            for (var i = 0; i < comp.selectedLayers.length; i++) {
                comp.selectedLayers[i].property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
            }
        } else {
            var adj = comp.layers.addSolid([1,1,1], "AI_Blur", comp.width, comp.height, comp.pixelAspect, comp.duration);
            adj.adjustmentLayer = true;
            adj.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
        }
        app.endUndoGroup();
        '''
    
    def _levels_jsx(self):
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            throw new Error("No active composition");
        }
        app.beginUndoGroup("AI Agent Command");
        if (comp.selectedLayers.length > 0) {
            for (var i = 0; i < comp.selectedLayers.length; i++) {
                comp.selectedLayers[i].property("ADBE Effect Parade").addProperty("ADBE Levels2");
            }
        } else {
            var adj = comp.layers.addSolid([1,1,1], "AI_Levels", comp.width, comp.height, comp.pixelAspect, comp.duration);
            adj.adjustmentLayer = true;
            adj.property("ADBE Effect Parade").addProperty("ADBE Levels2");
        }
        app.endUndoGroup();
        '''
    
    def _hue_sat_jsx(self):
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            throw new Error("No active composition");
        }
        app.beginUndoGroup("AI Agent Command");
        if (comp.selectedLayers.length > 0) {
            for (var i = 0; i < comp.selectedLayers.length; i++) {
                comp.selectedLayers[i].property("ADBE Effect Parade").addProperty("ADBE HUE SATURATION");
            }
        } else {
            var adj = comp.layers.addSolid([1,1,1], "AI_HueSat", comp.width, comp.height, comp.pixelAspect, comp.duration);
            adj.adjustmentLayer = true;
            adj.property("ADBE Effect Parade").addProperty("ADBE HUE SATURATION");
        }
        app.endUndoGroup();
        '''
    
    def _tint_jsx(self):
        return '''
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {
            throw new Error("No active composition");
        }
        app.beginUndoGroup("AI Agent Command");
        if (comp.selectedLayers.length > 0) {
            for (var i = 0; i < comp.selectedLayers.length; i++) {
                comp.selectedLayers[i].property("ADBE Effect Parade").addProperty("ADBE Tint");
            }
        } else {
            var adj = comp.layers.addSolid([1,1,1], "AI_Tint", comp.width, comp.height, comp.pixelAspect, comp.duration);
            adj.adjustmentLayer = true;
            adj.property("ADBE Effect Parade").addProperty("ADBE Tint");
        }
        app.endUndoGroup();
        '''
    
    def _inspection_jsx(self):
        return '''
        var inspectionResult = {
            project: {},
            activeComp: null,
            selectedLayers: [],
            allLayers: []
        };
        
        inspectionResult.project = {
            name: app.project.file ? app.project.file.name : "Untitled",
            numItems: app.project.numItems,
            renderQueueItems: app.project.renderQueue.numItems
        };
        
        var comp = app.project.activeItem;
        if (comp && comp instanceof CompItem) {
            inspectionResult.activeComp = {
                name: comp.name,
                width: comp.width,
                height: comp.height,
                duration: comp.duration,
                frameRate: comp.frameRate,
                numLayers: comp.numLayers,
                currentTime: comp.time
            };
            
            for (var i = 1; i <= comp.numLayers; i++) {
                var layer = comp.layer(i);
                var layerInfo = {
                    index: i,
                    name: layer.name,
                    type: layer.matchName,
                    effects: []
                };
                
                var effects = layer.property("ADBE Effect Parade");
                if (effects && effects.numProperties > 0) {
                    for (var j = 1; j <= effects.numProperties; j++) {
                        var effect = effects.property(j);
                        layerInfo.effects.push({
                            name: effect.name,
                            matchName: effect.matchName
                        });
                    }
                }
                
                var transform = layer.property("ADBE Transform Group");
                if (transform) {
                    layerInfo.position = transform.property("ADBE Position").value;
                    layerInfo.opacity = transform.property("ADBE Opacity").value;
                }
                
                inspectionResult.allLayers.push(layerInfo);
            }
            
            if (comp.selectedLayers.length > 0) {
                for (var i = 0; i < comp.selectedLayers.length; i++) {
                    var layer = comp.selectedLayers[i];
                    inspectionResult.selectedLayers.push({
                        index: layer.index,
                        name: layer.name,
                        type: layer.matchName
                    });
                }
            }
        }
        
        JSON.stringify(inspectionResult, null, 2);
        '''
    
    def get_ae_panel_script(self) -> str:
        """Return the ExtendScript for the AE panel."""
        
        return f'''// AI Video Editing Agent - After Effects Panel
// WARNING: Go to Preferences -> Scripting & Expressions and enable
// "Allow Scripts to Write Files and Access Network" for this panel to work.
(function(thisObj) {{
    if (!thisObj) {{
        alert("This script must be run as a ScriptUI Panel (Window > AI Video Editor)");
        return;
    }}
    
    var win = (thisObj instanceof Panel) ? thisObj : new Window("palette", "AI Video Editor", undefined, {{resizeable: true}});
    win.orientation = "column";
    win.alignChildren = "fill";
    win.preferredSize = [500, 400];
    
    // Permissions warning banner
    var warnBanner = win.add("group");
    warnBanner.add("statictext", undefined, "⚠️ Enable: Preferences > Scripting & Expressions > 'Allow Scripts to Write Files and Access Network'");
    
    var statusText = win.add("statictext", undefined, "AI Engine: Ready");
    statusText.alignment = ["fill", "top"];
    
    var logArea = win.add("edittext", undefined, "", {{multiline: true, readonly: true}});
    logArea.preferredSize = [500, 200];
    
    var btnRow = win.add("group");
    var testBtn = btnRow.add("button", undefined, "Refresh");
    var clearBtn = btnRow.add("button", undefined, "Clear Log");
    var screenshotBtn = btnRow.add("button", undefined, "📸 Screenshot");
    var forceScanBtn = btnRow.add("button", undefined, "Force Scan Bridge");
    
    var bridgeDir = new Folder(Folder.temp.fsName + "/ae_video_agent_bridge");
    var commandsDir = new Folder(bridgeDir.fsName + "/commands");
    var responsesDir = new Folder(bridgeDir.fsName + "/responses");
    var processedDir = new Folder(bridgeDir.fsName + "/processed");
    var screenshotsDir = new Folder(bridgeDir.fsName + "/screenshots");
    
    function ensureFolder(folder) {{
        if (!folder.exists) {{
            try {{ folder.create(); }} catch(e) {{}}
        }}
    }}
    
    ensureFolder(bridgeDir);
    ensureFolder(commandsDir);
    ensureFolder(responsesDir);
    ensureFolder(processedDir);
    ensureFolder(screenshotsDir);
    
    var processing = {{}};
    var lastProcessedCommandId = "";
    var isChecking = false;
    var checkInterval = 1000;
    
    function log(msg) {{
        var ts = new Date().toLocaleTimeString();
        logArea.text += "\\n[" + ts + "] " + msg;
        logArea.selection = logArea.text.length;
    }}
    
    function processCommand(cmdFile) {{
        var cmdId = cmdFile.name.replace("cmd_", "").replace(".jsx", "");
        if (processing[cmdId]) return;
        processing[cmdId] = true;
        
        var responseFile = new File(responsesDir.fsName + "/cmd_" + cmdId + ".json");
        var metaFile = new File(commandsDir.fsName + "/cmd_" + cmdId + ".json");
        
        try {{
            log("Processing: " + cmdId);
            
            cmdFile.encoding = "UTF-8";
            cmdFile.open("r");
            var jsxCode = cmdFile.read();
            cmdFile.close();
            
            var result = {{status: "success"}};
            try {{
                app.beginUndoGroup("AI Agent Command");
                var evalResult = eval(jsxCode);
                app.endUndoGroup();
                result.message = "Executed successfully";
                
                if (evalResult !== undefined && evalResult !== "") {{
                    result.data = evalResult;
                }}
                
                log(">>> EXECUTED: " + cmdId);
            }} catch(e) {{
                result.status = "error";
                result.message = e.toString();
                log(">>> ERROR: " + e.toString());
            }}
            
            responseFile.open("w");
            responseFile.write(JSON.stringify(result));
            responseFile.close();
            
            cmdFile.remove();
            if (metaFile.exists) metaFile.remove();
            
        }} catch(e) {{
            log("Error: " + e.toString());
            try {{
                responseFile.open("w");
                responseFile.write(JSON.stringify({{status: "error", message: e.toString()}}));
                responseFile.close();
            }} catch(e2) {{}}
        }} finally {{
            delete processing[cmdId];
        }}
    }}
    
    function pollBridgeFolder() {{
        try {{
            if (isChecking) return;
            isChecking = true;
            
            try {{
                if (commandsDir.exists) {{
                    var files = commandsDir.getFiles("cmd_*.jsx");
                    for (var i = 0; i < files.length; i++) {{
                        processCommand(files[i]);
                    }}
                }}
            }} catch(e) {{
                // Silently handle polling errors
            }}
        }} finally {{
            isChecking = false;
        }}
        
        // Schedule next check cycle
        try {{
            app.scheduleTask("pollBridgeFolder()", checkInterval, false);
        }} catch(e) {{
            // If scheduling fails, log it
            log("Schedule error: " + e.toString());
        }}
    }}
    
    function initLastProcessedCommand() {{
        try {{
            if (commandsDir.exists) {{
                var cmdFiles = commandsDir.getFiles("cmd_*.jsx");
                if (cmdFiles.length > 0) {{
                    var latest = cmdFiles[cmdFiles.length - 1];
                    latest.encoding = "UTF-8";
                    latest.open("r");
                    var content = latest.read();
                    latest.close();
                    if (content) {{
                        var cmdData = JSON.parse(content);
                        lastProcessedCommandId = cmdData.id || "";
                        log("Ignored pre-existing command: " + lastProcessedCommandId);
                    }}
                }}
            }}
        }} catch(e) {{
            // Ignore errors during init
        }}
    }}
    
    function startCommandChecker() {{
        // Cancel any previous scheduled task to avoid duplicates on panel reopen
        try {{
            if ($.global.veCheckTaskId != null) {{
                app.cancelTask($.global.veCheckTaskId);
            }}
        }} catch(e) {{}}
        $.global.veCheckTaskId = app.scheduleTask("pollBridgeFolder()", checkInterval, true);
    }}
    
    forceScanBtn.onClick = function() {{
        pollBridgeFolder();
        log("Force scan completed");
    }};
    
    screenshotBtn.onClick = function() {{
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {{
            log("Screenshot: No active comp");
            return;
        }}
        ensureFolder(screenshotsDir);
        var fileName = "screenshot_" + new Date().getTime() + ".png";
        var outFile = new File(screenshotsDir.fsName + "/" + fileName);
        try {{
            comp.saveFrameToPng(comp.time, outFile);
            log("Screenshot saved: " + fileName);
        }} catch(e) {{
            log("saveFrameToPng failed: " + e.toString());
        }}
    }};
    
    testBtn.onClick = function() {{
        log("=== Status ===");
        log("Bridge: " + bridgeDir.fsName);
        log("Commands: " + commandsDir.getFiles("cmd_*.jsx").length);
        log("Responses: " + responsesDir.getFiles("cmd_*.json").length);
        log("Screenshots dir: " + screenshotsDir.fsName);
    }};
    clearBtn.onClick = function() {{ logArea.text = ""; }};
    
    if (!String.prototype.trim) {{
        String.prototype.trim = function() {{
            return this.replace(/^\\s+|\\s+$/g, '');
        }};
    }}
    
    // Initialize dedup and start polling
    initLastProcessedCommand();
    startCommandChecker();
    
    win.layout.layout(true);
    if (win instanceof Window) {{ win.show(); }}
    log("=== AI Video Editor Ready ===");
    log("Bridge: " + bridgeDir.fsName);
    return win;
}})(this);'''
    
    def get_bridge_status(self) -> Dict[str, Any]:
        cmd_files = list(self.commands_dir.glob("cmd_*.jsx"))
        resp_files = list(self.responses_dir.glob("cmd_*.json"))
        proc_files = list(self.processed_dir.glob("*"))
        quota_remaining = self.rate_limiter.remaining_today() if hasattr(self, 'rate_limiter') else None
        return {
            "bridge_dir": str(self.bridge_dir),
            "pending_commands": len(cmd_files),
            "pending_responses": len(resp_files),
            "processed": len(proc_files),
            "running": self._running,
            "has_api_keys": self.km is not None,
            "quota_remaining": quota_remaining,
        }
    
    def get_quota_status(self) -> Dict[str, Any]:
        """Return current quota status for UI display."""
        if not hasattr(self, 'rate_limiter'):
            return {"quota_enabled": False, "remaining_today": 0}
        return {
            "quota_enabled": True,
            "remaining_today": self.rate_limiter.remaining_today(),
            "per_key_remaining": self.rate_limiter.remaining_per_key(),
            "max_rpd": RPD_PER_KEY,
            "num_keys": NUM_KEYS,
            "pool_total_rpd": RPD_PER_KEY * NUM_KEYS,
        }
    
    def analyze_video(self, video_path: str, user_request: str,
                      on_role_complete=None) -> str:
        """
        Public entry point: run the full Analyst pipeline on a video file.
        Returns a cmd_id for tracking.
        """
        if not self._running:
            self.start_server()
        
        cmd_id = str(uuid.uuid4())[:8]
        
        def run_analysis():
            try:
                analysis = self._call_analyst(video_path, user_request)
                
                if on_role_complete:
                    on_role_complete("🎥 Analyst", json.dumps(analysis, indent=2)[:300])
                
                # Generate auto-cut suggestions from analysis
                cuts = self.suggest_cuts(analysis)
                
                # Generate camera tracking JSX
                track_points = self._detect_track_points(video_path)
                tracking_jsx = self._generate_tracking_jsx(track_points)
                
                # Generate camera tracking command
                if tracking_jsx:
                    track_cmd_id = str(uuid.uuid4())[:8]
                    self._command_queue.put({
                        'id': track_cmd_id,
                        'jsx': tracking_jsx,
                        'prompt': 'Camera tracking assist',
                        'response_text': f'Detected {len(track_points)} track points. Camera tracker activated.',
                    })
                
                # Send analysis to planner for JSX generation
                cuts_json = json.dumps(cuts, indent=2)
                scenes_json = json.dumps(analysis.get("scenes", []), indent=2)
                
                combined_context = f"Footage analysis:\nScenes: {scenes_json}\n\nAuto-cuts: {cuts_json}"
                
                if self.km:
                    # Run Planner with footage context
                    if on_role_complete:
                        on_role_complete("🧠 Planner", "Planning based on footage analysis...")
                    plan = self._call_planner(f"{user_request}\n\n{combined_context}")
                    if on_role_complete:
                        on_role_complete("🧠 Planner", plan)
                    
                    if on_role_complete:
                        on_role_complete("🎨 Designer", "Designing edit approach...")
                    design = self._call_designer(f"{user_request}\n\n{combined_context}", plan)
                    if on_role_complete:
                        on_role_complete("🎨 Designer", design)
                    
                    if on_role_complete:
                        on_role_complete("💻 Coder", "Generating JSX...")
                    jsx = self._call_coder(f"{user_request}\n\n{combined_context}", plan, design)
                    if on_role_complete:
                        on_role_complete("💻 Coder", jsx[:200])
                    
                    if on_role_complete:
                        on_role_complete("🔍 Reviewer", "Reviewing JSX...")
                    reviewed = self._call_reviewer(jsx, plan)
                    if on_role_complete:
                        on_role_complete("🔍 Reviewer", reviewed[:200])
                    
                    final_jsx = reviewed
                else:
                    final_jsx = self._template_parse_prompt(user_request)
                
                self._command_queue.put({
                    'id': cmd_id,
                    'jsx': final_jsx,
                    'prompt': user_request,
                    'response_text': f"Analyzed {len(analysis.get('scenes', []))} scenes. Generated JSX.",
                })
                
            except Exception as e:
                self._log(f"Analysis pipeline error: {e}")
                self._command_queue.put({
                    'id': cmd_id,
                    'jsx': self._template_parse_prompt(user_request),
                    'prompt': user_request,
                    'response_text': f"Error: {e}",
                })
        
        threading.Thread(target=run_analysis, daemon=True).start()
        return cmd_id
    
    def generate_intro(self, text: str = "AI Generated", template: str = "cinematic") -> str:
        """Generate intro animation JSX."""
        jsx = self._intro_outro_jsx(template, text)
        cmd_id = str(uuid.uuid4())[:8]
        
        def run():
            try:
                if self.km:
                    reviewed = self._call_reviewer(jsx, "Intro template")
                    jsx = reviewed
            except Exception as e:
                self._log(f"Intro review error: {e}")
            
            self._command_queue.put({
                'id': cmd_id,
                'jsx': jsx,
                'prompt': f'Intro: {text}',
                'response_text': f"Intro generated with template '{template}'",
            })
        
        threading.Thread(target=run, daemon=True).start()
        return cmd_id
    
    def generate_outro(self, text: str = "The End", template: str = "minimal") -> str:
        """Generate outro animation JSX."""
        jsx = self._intro_outro_jsx(template, text)
        cmd_id = str(uuid.uuid4())[:8]
        
        def run():
            try:
                if self.km:
                    reviewed = self._call_reviewer(jsx, "Outro template")
                    jsx = reviewed
            except Exception as e:
                self._log(f"Outro review error: {e}")
            
            self._command_queue.put({
                'id': cmd_id,
                'jsx': jsx,
                'prompt': f'Outro: {text}',
                'response_text': f"Outro generated with template '{template}'",
            })
        
        threading.Thread(target=run, daemon=True).start()
        return cmd_id
    
    def get_available_analysis(self, video_path: str) -> Optional[Dict]:
        """Check if there's cached analysis for this video file."""
        cache_key = self._cache_key_for_file(video_path)
        return self._load_cached_analysis(cache_key)


if __name__ == "__main__":
    import tempfile
    from key_manager import RoleKeyManager
    agent = VideoEditingAgent(key_manager=RoleKeyManager())  
    agent.set_log_callback(lambda m: print(m))
    agent.start_server()
    
    time.sleep(1)
    agent.send_prompt("add text Hello World")
    time.sleep(2)
    agent.send_prompt("glow effect")
    time.sleep(2)
    agent.send_prompt("color grade")
    time.sleep(2)
    
    print("\nStatus:", agent.get_bridge_status())
    agent.stop_server()
