"""
Blender 3D Agent — Campus Suite  (Professional Edition)
========================================================
Engine chain: OmniRoute (local) → Gemini 5-key swarm (cloud)

Key capabilities:
  • Natural language → professional Blender 3D animations & scenes
  • Self-correcting agentic loop (up to N retries with error context)
  • Viewport screenshot after every execution
  • Live diff panel with green/red syntax highlighting
  • Full structured JSON event log + rotating file log
  • BlenderMCP add-on protocol: https://github.com/ahujasid/blender-mcp
"""

from __future__ import annotations

# ── stdlib ──────────────────────────────────────────────────────────────────
import base64, difflib, json, logging, os, random, re, socket, sys, time, threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from tkinter import filedialog

# ── third-party ─────────────────────────────────────────────────────────────
import customtkinter as ctk
import requests
from PIL import Image
import io as _io

try:
    import psutil
except ImportError:
    psutil = None

try:
    from google import genai as google_genai
except ImportError:
    google_genai = None

try:
    from openai import OpenAI
    OPENAI_OK = True
except ImportError:
    OPENAI_OK = False

# ── Local modules ───────────────────────────────────────────────────────────
try:
    from key_manager import AiApi, RoleKeyManager
except ImportError:
    AiApi = None
    RoleKeyManager = None

try:
    from coding_agent import CodingAgent, Workspace
except ImportError:
    CodingAgent = None
    Workspace = None

# ── PIL availability flag ────────────────────────────────────────────────────
try:
    from PIL import Image as PilImage
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ===========================================================================
# PATHS & STRUCTURED LOGGING
# ===========================================================================
APP_DIR       = Path(__file__).resolve().parent
LOG_DIR       = APP_DIR / "logs"
WORKSPACE_DIR = APP_DIR / "blender_agent_workspace"
SCREENSHOT_DIR= WORKSPACE_DIR / "screenshots"
CODING_WORKSPACE_DIR = WORKSPACE_DIR / "coding_agent_workspace"
for _d in (LOG_DIR, WORKSPACE_DIR, SCREENSHOT_DIR, CODING_WORKSPACE_DIR):
    _d.mkdir(exist_ok=True)

SCENE_SCRIPT = WORKSPACE_DIR / "scene_script.py"
PROJECT_MEMORY = WORKSPACE_DIR / "project_memory.json"
EVENT_LOG    = LOG_DIR / "blender_agent_events.jsonl"
ERROR_LOG    = LOG_DIR / "blender_agent_errors.log"

# Enhanced logging with error tracking
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "blender_agent.log", encoding="utf-8"),
        logging.FileHandler(ERROR_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("blender_agent")

# Separate error logger
error_logger = logging.getLogger("blender_agent.errors")
error_handler = logging.FileHandler(ERROR_LOG, encoding="utf-8")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter("%(asctime)s [ERROR] %(message)s"))
error_logger.addHandler(error_handler)
error_logger.setLevel(logging.ERROR)


def log_event(kind: str, **data):
    """Write one JSON line to the structured event log (no key material)."""
    safe = {k: v for k, v in data.items() if "key" not in k.lower() and "token" not in k.lower()}
    rec  = {"t": datetime.now().isoformat(timespec="seconds"), "event": kind, **safe}
    try:
        with EVENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Event log write failed: %s", exc)
    logger.info("⚡ %s %s", kind, safe)


def log_error(context: str, error: Exception, **extra):
    """Log detailed error information for debugging."""
    error_data = {
        "context": context,
        "error_type": type(error).__name__,
        "error_message": str(error),
        **extra
    }
    error_logger.error(json.dumps(error_data, ensure_ascii=False))
    log_event("error", **error_data)


# ===========================================================================
# CONFIGURATION
# ===========================================================================
HOST             = "localhost"
PORT             = 9876
SOCKET_TIMEOUT   = 8.0
RESPONSE_TIMEOUT = 600.0
SCAN_MS          = 2500
MAX_FIX_ATTEMPTS = 4          # up from 3 — more retries for complex animations
MAX_VISUAL_RETRIES = 3        # max retries for visual verification failures

# Task decomposition thresholds
SIMPLE_TASK_THRESHOLD = 1     # single agent call
COMPLEX_TASK_THRESHOLD = 3    # use multi-agent pipeline
HUGE_TASK_THRESHOLD = 5       # full decomposition + iterative refinement

# Legacy setting retained for compatibility; routed calls use the shared
# Codex → Gemini → local Campus Suite AI → OmniRoute priority.
AI_ENGINE = "auto"

# OmniRoute — local OpenAI-compatible AI gateway  ───────────────────────────
def _omniroute_settings_from_env(env=None):
    env = os.environ if env is None else env
    host = env.get("OMNIROUTE_HOST", "localhost")
    try:
        port = int(env.get("OMNIROUTE_PORT", "20128"))
    except (TypeError, ValueError):
        port = 20128
    base_url = env.get("OMNIROUTE_BASE_URL") or env.get("OMNIROUTE_BASE") or f"http://{host}:{port}/v1"
    api_key = env.get("OMNIROUTE_API_KEY") or env.get("OMNIROUTE_TOKEN") or env.get("OMNIROUTE_KEY") or ""
    model = env.get("OMNIROUTE_MODEL", "auto/coding")
    try:
        timeout = float(env.get("OMNIROUTE_TIMEOUT_SECONDS", env.get("OMNIROUTE_TIMEOUT", "30")))
    except (TypeError, ValueError):
        timeout = 30.0
    return host, port, base_url, api_key, model, timeout


OMNIROUTE_HOST, OMNIROUTE_PORT, OMNIROUTE_BASE, OMNIROUTE_API_KEY, OMNIROUTE_MODEL, OMNIROUTE_TIMEOUT = (
    _omniroute_settings_from_env()
)

# Gemini key slots come only from configuration; absent keys leave this provider unavailable.
def _gemini_keys_from_env(env=None) -> list[str]:
    env = os.environ if env is None else env
    numbered = [env.get(f"GEMINI_API_KEY_{index}") for index in range(1, 6)]
    primary = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
    if not numbered[0] and primary:
        numbered[0] = primary
    return [str(key).strip() for key in numbered if key and str(key).strip()]


GEMINI_KEYS = _gemini_keys_from_env()
GEMINI_MODELS = [
    model for model in (
        os.environ.get("CAMPUS_GEMINI_MODEL"),
        os.environ.get("GEMINI_MODEL"),
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
    ) if model
]

# COLOUR PALETTE  (VS-Code Dark+ inspired, premium dark mode)
# ===========================================================================
BG          = "#0b0d14"
SIDEBAR     = "#0f1318"
PANEL       = "#131720"
CARD        = "#1a2030"
CARD_HOVER  = "#202840"
BORDER      = "#252d3d"
TEXT        = "#e8eaf6"
MUTED       = "#7986a3"
USER_BG     = "#1a3a5c"
AGENT_BG    = "#141c2b"
SYSTEM_BG   = "#0e1420"
ERROR_BG    = "#2c1010"
SUCCESS_BG  = "#0d2118"
WARN_BG     = "#241e0a"
ACCENT      = "#4fc3f7"
CYAN        = "#00e5ff"
GREEN       = "#4caf50"
RED         = "#f44336"
YELLOW      = "#ffb300"
PURPLE      = "#ce93d8"
PINK        = "#f06292"
DIFF_ADD    = "#22c55e"
DIFF_REM    = "#ef4444"
DIFF_META   = "#a78bfa"
DIFF_HUNK   = "#64748b"

# ===========================================================================
# SYSTEM PROMPT  — professional animation & scene master prompt
# ===========================================================================
BLENDER_5_SYSTEM_PROMPT_INJECT = """
VENKAT & SHAAN'S PURPOSE:
You are part of a creative studio platform founded by Venkat and Shaan to empower students 
with AI-driven creative tools. Your core mission is to make advanced 3D generation intuitive, 
educational, and accessible for everyone.

CRITICAL BLENDER 5.2 LTS API CONSTRAINTS:
1. SHADER NODES:
   - Use 'ShaderNodeTexGradient' (NEVER 'ShaderNodeGradientTexture').
   - Use 'ShaderNodeTexNoise' (NEVER 'ShaderNodeNoiseTexture').
   - For Mix Shader/RGB, use 'ShaderNodeMix' with data_type='RGBA'. Access sockets safely via safe_get_input or index: inputs[0] (Factor), inputs[6] (A), inputs[7] (B).

2. PRINCIPLED BSDF (BLENDER 4.0+ / 5.x API):
   - Access Principled BSDF inputs SAFELY using safe_get_input(bsdf, "Emission Color", "Emission", "Color") or direct socket indexes.
   - Example safe socket assignment:
     def set_socket(node, possible_names, value):
         for name in possible_names:
             if name in node.inputs:
                 node.inputs[name].default_value = value
                 return
     set_socket(principled, ["Emission Color", "Emission"], (0, 0.8, 1, 1))

3. EEVEE NEXT (BLENDER 4.2+ / 5.2 LTS):
   - DO NOT set bpy.context.scene.eevee.use_bloom = True (attribute REMOVED).
   - DO NOT access scene.eevee.bloom_threshold or scene.eevee.bloom_intensity.
   - To make materials glow in EEVEE Next, set high Emission Strength (> 5.0) on the material.

4. TEXT OBJECT ALIGNMENT (CRITICAL):
   - Vertical alignment: text_obj.data.align_y = 'CENTER' (NEVER 'MIDDLE' — valid values: 'TOP', 'TOP_BASELINE', 'CENTER', 'BOTTOM_BASELINE', 'BOTTOM').
   - Horizontal alignment: text_obj.data.align_x = 'CENTER' (valid values: 'LEFT', 'CENTER', 'RIGHT', 'JUSTIFY', 'FLUSH').

5. CAMERA DOF & ATTRIBUTES:
   - Enable DOF via cam.data.dof.use_dof = True (NEVER cam.use_dof).

6. MODIFIERS:
   - Ensure modifiers exist before editing attributes. Use standard attributes only.

7. BLENDER MCP EXTENSIONS ACTIVE:
   - Poly Haven and Sketchfab asset integrations are enabled in BlenderMCP N-panel.
   - The script can generate high-quality procedural geometry while taking full advantage of any loaded assets or studio environments.
""".strip()

SYSTEM_PROMPT = r"""
You are an expert Blender Python (bpy) developer and 3D animator integrated into a
student AI creative studio. You produce professional-grade Blender scripts that run
inside Blender via the BlenderMCP add-on.

PROJECT-SAFE EDITING CONTRACT:
• The open .blend file is an existing project unless the user explicitly asks for a new scene.
• Inspect `bpy.data` first. Preserve all unrelated objects, collections, materials, cameras,
  lights, animation, and render settings.
• Put new generated assets in a collection named `AI_Agent_Work` (create it only if absent),
  so subsequent edits have a clear, non-destructive scope.
• For a correction, return a minimal patch for the failed element; never regenerate or replace
  the entire scene. Do not call object delete, datablock remove, purge, or open another blend file.

╔══════════════════════════════════════════════════════════════════════════════╗
║  OUTPUT RULE — HARD REQUIREMENT                                            ║
║  Return ONE fenced code block:  ```python  ...  ```                          ║
║  Followed by a friendly, 2-3 sentence conversational summary explaining      ║
║  the 3D models, camera angles, lighting, and animation you created.          ║
╚══════════════════════════════════════════════════════════════════════════════╝

══════════════════════════════════════════════════════════════════════════════
TARGET ENVIRONMENT: BLENDER 5.x / 4.x LTS (including 5.2.0 LTS)
══════════════════════════════════════════════════════════════════════════════
• RENDER ENGINE: Use `scene.render.engine = 'BLENDER_EEVEE'` or `'CYCLES'`. NEVER write `'BLENDER_EEVEE_NEXT'`.
• EEVEE FEATURES: In Blender 4.2+ / 5.x, legacy attributes `use_gtao`, `use_bloom`, `use_ssr` DO NOT exist on `scene.eevee`. Always use `if hasattr(scene.eevee, attr)` before setting EEVEE properties.
• SHADER NODE TYPE NAMES:
  - Gradient Texture: `ShaderNodeTexGradient` (NEVER `ShaderNodeGradientTexture`)
  - Noise Texture: `ShaderNodeTexNoise` (NEVER `ShaderNodeNoiseTexture`)
  - Voronoi Texture: `ShaderNodeTexVoronoi`
  - Wave Texture: `ShaderNodeTexWave`
  - Image Texture: `ShaderNodeTexImage`
  - Mix Node: `ShaderNodeMix` or `ShaderNodeMixRGB` (sockets: `"Factor"` / `"Fac"`)
• MESH PRIMITIVE OPERATOR KWARGS (STRICT):
  - Torus: `bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.25, major_segments=48, minor_segments=16)` (NEVER use keyword `rings` or `segment`)
  - UV Sphere: `bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=32, ring_count=16)` (NEVER use keyword `rings`)
  - Cylinder: `bpy.ops.mesh.primitive_cylinder_add(radius=1.0, depth=2.0, vertices=32)`
  - Ico Sphere: `bpy.ops.mesh.primitive_ico_sphere_add(radius=1.0, subdivisions=2)`
• MODIFIER PROPERTIES (STRICT):
  - WaveModifier: properties are `height`, `width`, `narrowness`, `speed`, `dampening`, `falloff_radius` (NEVER `damp`).
  - OceanModifier: property for generation mode is `geometry_mode = 'GENERATE'` or `'DISPLACE'` (NEVER `geometry_final`, `geometry_generation`, or `'MODIFY'`).
  - Always use `if hasattr(mod, "prop_name")` before writing to modifier properties.

══════════════════════════════════════════════════════════════════════════════
CRITICAL: EXECUTION NAMESPACE & SAFE ACCESS (PREVENT CRASHES)
══════════════════════════════════════════════════════════════════════════════
The script runs via:   exec(code, {"bpy": bpy})
Therefore you MUST import everything you need at the top of the code block:

    import bpy, math, mathutils, random
    from mathutils import Vector, Euler, Matrix

Always define or use this safe node input lookup helper to avoid KeyError:

    def safe_get_input(node, *names):
        if not node or not hasattr(node, "inputs"):
            return None
        for name in names:
            inp = node.inputs.get(name)
            if inp is not None:
                return inp
        return None

══════════════════════════════════════════════════════════════════════════════
ANIMATION RULES  (critical — FCurve errors come from breaking these)
══════════════════════════════════════════════════════════════════════════════
1. ALWAYS set the scene frame range FIRST:
       scene = bpy.context.scene
       scene.frame_start = 1
       scene.frame_end   = <N>
       scene.frame_current = 1

2. To insert a keyframe on an object:
       # Step 1: make it active
       bpy.context.view_layer.objects.active = obj
       obj.select_set(True)
       # Step 2: go to the frame
       scene.frame_current = frame_number
       # Step 3: insert keyframe
       obj.keyframe_insert(data_path="location",        frame=frame_number)
       obj.keyframe_insert(data_path="rotation_euler",  frame=frame_number)
       obj.keyframe_insert(data_path="scale",           frame=frame_number)

3. To animate a material node input (e.g., emission strength):
       node  = mat.node_tree.nodes.get("Emission") or mat.node_tree.nodes.get("Principled BSDF")
       if node:
           inp = safe_get_input(node, "Strength", "Emission Strength")
           if inp:
               inp.default_value = 5.0
               inp.keyframe_insert(data_path="default_value", frame=1)

4. To change interpolation to BEZIER (smooth easing):
       if obj.animation_data and obj.animation_data.action:
           fcurves = getattr(obj.animation_data.action, "fcurves", None)
           if fcurves:
               for fc in fcurves:
                   for kp in fc.keyframe_points:
                       kp.interpolation = 'BEZIER'
                       kp.handle_left_type  = 'AUTO_CLAMPED'
                       kp.handle_right_type = 'AUTO_CLAMPED'

5. Rotation: always use radians.  math.radians(90)  not 90.

══════════════════════════════════════════════════════════════════════════════
SCENE SETUP  (always do this for new scenes)
══════════════════════════════════════════════════════════════════════════════
    import bpy, math, mathutils, random

    # --- 1. Preserve the existing project by default ---
    # Never clear, purge, or delete unrelated scene data unless the user explicitly
    # asks for a new/blank scene. Add or edit only the objects named in the request.

    # --- 2. Frame range ---
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end   = 120  # adjust as needed

    # --- 3. Render engine (EEVEE for speed) ---
    scene.render.engine = 'BLENDER_EEVEE'  # MUST be 'BLENDER_EEVEE' or 'CYCLES' (NEVER 'BLENDER_EEVEE_NEXT')
    for attr in ("use_gtao", "use_bloom", "use_ssr"):
        if hasattr(scene.eevee, attr):
            setattr(scene.eevee, attr, True)

    # --- 4. World / Background ---
    world = bpy.data.worlds.get("World")
    if world:
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            c_input = safe_get_input(bg, "Color")
            s_input = safe_get_input(bg, "Strength")
            if c_input: c_input.default_value = (0.05, 0.07, 0.12, 1)
            if s_input: s_input.default_value = 0.3

══════════════════════════════════════════════════════════════════════════════
MATERIAL  (Principled BSDF pattern — always attach to object)
══════════════════════════════════════════════════════════════════════════════
    def safe_get_input(node, *names):
        if not node or not hasattr(node, "inputs"):
            return None
        for name in names:
            inp = node.inputs.get(name)
            if inp is not None:
                return inp
        return None

    def make_mat(name, base_color, roughness=0.4, metallic=0.0,
                 emission_color=None, emission_strength=0.0, alpha=1.0):
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            b_color = safe_get_input(bsdf, "Base Color", "BaseColor", "Color")
            r_input = safe_get_input(bsdf, "Roughness")
            m_input = safe_get_input(bsdf, "Metallic")
            if b_color: b_color.default_value = (*base_color, 1.0) if len(base_color) == 3 else base_color
            if r_input: r_input.default_value = roughness
            if m_input: m_input.default_value = metallic

            if emission_color:
                e_color = safe_get_input(bsdf, "Emission Color", "Emission", "Color")
                e_str   = safe_get_input(bsdf, "Emission Strength", "Strength")
                if e_color: e_color.default_value = (*emission_color, 1.0) if len(emission_color) == 3 else emission_color
                if e_str: e_str.default_value = emission_strength

            a_input = safe_get_input(bsdf, "Alpha")
            if a_input: a_input.default_value = alpha

        if alpha < 1.0:
            mat.blend_method = 'BLEND'
        return mat

    # Attach:  obj.data.materials.append(mat)

══════════════════════════════════════════════════════════════════════════════
LIGHTING  (use real lights — not emission-only)
══════════════════════════════════════════════════════════════════════════════
    # Sun light
    bpy.ops.object.light_add(type='SUN', location=(0, 0, 10))
    sun = bpy.context.active_object
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(45), 0, math.radians(45))

    # Area light (soft, cinematic)
    bpy.ops.object.light_add(type='AREA', location=(5, -5, 8))
    key = bpy.context.active_object
    key.data.energy = 800
    if hasattr(key.data, "size"):
        key.data.size = 3

    # Point light (accent)
    bpy.ops.object.light_add(type='POINT', location=(-3, 3, 4))
    fill = bpy.context.active_object
    fill.data.energy = 200
    fill.data.color  = (0.6, 0.8, 1.0)

══════════════════════════════════════════════════════════════════════════════
CAMERA SETUP
══════════════════════════════════════════════════════════════════════════════
    bpy.ops.object.camera_add(location=(7, -7, 5))
    cam = bpy.context.active_object
    cam.rotation_euler = (math.radians(63), 0, math.radians(46))
    cam.data.lens = 35            # focal length in mm
    if hasattr(cam.data, "dof"):
        cam.data.dof.use_dof = True
        cam.data.dof.focus_distance = 9.0
        cam.data.dof.aperture_fstop = 2.8
    scene.camera = cam

══════════════════════════════════════════════════════════════════════════════
SMOOTH SHADING  (run AFTER object creation, in OBJECT mode)
══════════════════════════════════════════════════════════════════════════════
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.shade_smooth()

══════════════════════════════════════════════════════════════════════════════
PROFESSIONAL ANIMATION PATTERNS
══════════════════════════════════════════════════════════════════════════════
ORBIT / CIRCULAR PATH:
    import math
    for f in range(1, 121):
        angle = math.radians(f * 3)    # 3°/frame = 1 revolution / 120f
        radius = 5.0
        obj.location = (math.cos(angle)*radius, math.sin(angle)*radius, 1.5)
        scene.frame_current = f
        obj.keyframe_insert(data_path="location", frame=f)

BOUNCE (sine easing):
    import math
    for f in range(1, 121):
        z = abs(math.sin(f * math.pi / 30)) * 3
        obj.location.z = z
        scene.frame_current = f
        obj.keyframe_insert(data_path="location", frame=f)

SPIN:
    for f in range(1, 121):
        obj.rotation_euler.z = math.radians(f * 3)
        scene.frame_current = f
        obj.keyframe_insert(data_path="rotation_euler", frame=f)

SCALE PULSE:
    import math
    for f in range(1, 121):
        s = 1.0 + 0.3 * math.sin(f * math.pi / 20)
        obj.scale = (s, s, s)
        scene.frame_current = f
        obj.keyframe_insert(data_path="scale", frame=f)

CAMERA DOLLY (move camera):
    for f in range(1, 121):
        cam.location.z = 5 + f * 0.02
        scene.frame_current = f
        cam.keyframe_insert(data_path="location", frame=f)

══════════════════════════════════════════════════════════════════════════════
INCREMENTAL EDIT (when LIVE SCENE INFO is provided)
══════════════════════════════════════════════════════════════════════════════
When scene objects are listed, reference them directly by checking first:
    obj = bpy.data.objects.get("ExistingObjectName")
    if obj:
        # do your changes
Do NOT recreate the whole scene from scratch if an object already exists.

══════════════════════════════════════════════════════════════════════════════
FORBIDDEN
══════════════════════════════════════════════════════════════════════════════
  ✗  Network calls (requests, urllib, etc.)
  ✗  File I/O to paths outside the Blender project
  ✗  import subprocess, os.system, etc.
  ✗  bpy.ops without setting context (crashes)
  ✗  Forgetting to import math / mathutils / random
  ✗  Using 'BLENDER_EEVEE_NEXT' for render engine (always use 'BLENDER_EEVEE' or 'CYCLES')
  ✗  Direct obj.animation_data.action.fcurves access without checking if animation_data and action exist
  ✗  Setting scene.eevee.use_gtao, use_bloom, or use_ssr directly (not supported in Blender 4.2+; use hasattr check or omit)
  ✗  Direct dictionary-style access for inputs without using safe_get_input or .get() (e.g. NEVER do `nodes["Emission Color"]` — ALWAYS do `safe_get_input(nodes, "Emission Color", "Emission", "Color")`).
  ✗  Using `rings` parameter for `primitive_torus_add` (use `major_segments` and `minor_segments`).
  ✗  Using `ShaderNodeGradientTexture` (use `ShaderNodeTexGradient`).
  ✗  Using `WaveModifier.damp` (use `dampening`).
  ✗  Using invalid `OceanModifier` geometry mode (must be `'GENERATE'` or `'DISPLACE'`).
""".strip()

# ===========================================================================
# ANIMATION PRESETS  (rich, professional-level prompts)
# ===========================================================================
PRESETS = [
    ("🏠 Low-Poly Village",
     "Build a charming low-poly village: 3 houses with coloured roofs, a cobblestone road, "
     "pine trees, a water well, and streetlamps. Add a sun light and a camera framing the scene. "
     "Animate the sun rotating 90° over 120 frames."),

    ("🌌 Solar System",
     "Create an animated solar system: glowing yellow Sun at origin, then Mercury (grey), "
     "Venus (orange), Earth (blue+green), Mars (red), Jupiter (tan/brown), Saturn (gold with rings) "
     "at increasing orbit radii. Animate all planets orbiting the Sun at different speeds over 240 frames. "
     "Camera looks down at a slight angle. Add starfield via small point lights."),

    ("🚀 Sci-Fi Drone",
     "Create a futuristic quadcopter drone: dark metallic body, 4 rotor arms ending in cyan "
     "glowing turbines, underbelly sensor pod. Animate it hovering (gentle Z oscillation) and "
     "slowly rotating over 150 frames. Add a dramatic key light and a neon blue fill light."),

    ("🏰 Medieval Castle",
     "Build a detailed medieval castle: 4 cylindrical towers with conical roofs, thick stone "
     "walls with crenellations, arched gatehouse, drawbridge, inner courtyard. "
     "Grass terrain surrounding it. Add a warm sun and a moody fog atmosphere. "
     "Animate a slow cinematic camera orbit over 200 frames."),

    ("⚡ Atom Animation",
     "Create a realistic atom model: large glowing nucleus sphere, 3 electron shells (rings) "
     "at 45°/90°/135° tilts, 6 small electron spheres orbiting at different speeds and phases. "
     "Animate all electrons completing their orbits over 120 frames. "
     "Dark background, neon blue glow materials, EEVEE bloom effect."),

    ("☕ Coffee Mug Still Life",
     "Model a ceramic coffee mug with a handle, filled with dark coffee, wisps of steam "
     "(simple animated planes with transparency), sitting on a wooden coaster on a marble table. "
     "Three-point lighting: warm key, cool fill, rim light. Camera with shallow DOF. "
     "Animate steam wisps rising slowly over 80 frames."),

    ("🌊 Ocean Wave",
     "Create an animated low-poly ocean: displaced grid mesh with vertex-animated wave motion "
     "over 120 frames using driver/keyframe approach, deep blue-green material with high roughness, "
     "a wooden boat bobbing on the waves, seagull placeholder shapes in distance, "
     "dramatic sunset sky (orange-to-dark gradient world background)."),

    ("🎆 Particle Burst",
     "Create an explosion/burst animation: central sphere that scales from 0 to 1 in frame 1-10, "
     "then 20 small debris cubes fly outward in random directions with rotation, "
     "keyframed over frames 10-120. Each debris has a different vivid colour. "
     "Add a point light flash at origin that fades from energy=5000 to 0 over frames 10-30."),

    ("🏗 DNA Helix",
     "Build a DNA double-helix: 20 pairs of coloured sphere 'base pairs' connected by "
     "cylinder struts, arranged in a helix pattern using math.cos/sin. "
     "Animate the whole structure rotating around its vertical axis over 120 frames. "
     "Bright emission materials in a dark scene, camera looking slightly upward."),

    ("🌆 City Flyover",
     "Create a mini city: 8 buildings of varying heights (rectangles with flat roofs), "
     "roads between them, streetlights, a central park (green plane with trees). "
     "Animate a camera doing a dramatic flyover from ground level rising to bird's-eye view "
     "over 180 frames. Evening lighting with warm orange area lights."),
]

# ===========================================================================
# DATA CLASSES
# ===========================================================================
@dataclass
class AIResult:
    text:   str
    engine: str
    trail:  list = field(default_factory=list)
    
@dataclass
class ExecResult:
    success:  bool
    message:  str
    stdout:   str = ""
    raw:      dict = field(default_factory=dict)
    
    # ===========================================================================
    # SCENE SCRIPT PERSISTENCE
    # ===========================================================================
def read_scene_script() -> str:
    return SCENE_SCRIPT.read_text(encoding="utf-8") if SCENE_SCRIPT.exists() else ""

def append_scene_script(code: str):
    before = read_scene_script()
    sep    = "\n\n" if before and not before.endswith("\n\n") else ""
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    after  = before + sep + f"# ── {ts} ────────────────────\n{code.rstrip()}\n"
    SCENE_SCRIPT.write_text(after, encoding="utf-8")
    return before, after


class ProjectMemory:
    """Durable, user-owned context for an existing Blender project."""
    MAX_ENTRIES = 20

    def __init__(self, path: Path = PROJECT_MEMORY):
        self.path = path
        self.data = {"version": 1, "entries": []}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
                self.data = loaded
        except (OSError, json.JSONDecodeError):
            pass

    def remember(self, request: str, scene_info: str, outcome: str):
        self.data["entries"].append({"at": datetime.now().isoformat(timespec="seconds"), "request": request[:500], "scene": scene_info[:1500], "outcome": outcome[:500]})
        self.data["entries"] = self.data["entries"][-self.MAX_ENTRIES:]
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def context(self) -> str:
        entries = self.data.get("entries", [])[-6:]
        if not entries:
            return "No saved project history yet; inspect the live scene before editing."
        return "\n".join(f"- {e['at']}: request={e['request']}; outcome={e['outcome']}" for e in entries)


def _is_explicit_reset_request(prompt: str) -> bool:
    return bool(re.search(r"\b(new|blank|empty|start over|reset|clear)\s+(scene|project)|\bclear (the |this )?scene\b", prompt, re.I))


def _validate_scene_edit(code: str, allow_destructive: bool) -> None:
    """Block an unrequested whole-project wipe before Blender executes it."""
    if len(code) > 100_000:
        raise RuntimeError("Safety check stopped an unexpectedly large Blender script.")
    forbidden = (r"\b(?:eval|exec|__import__)\s*\(", r"\bsubprocess\b", r"\bos\.system\s*\(",
                 r"\bshutil\.rmtree\s*\(", r"\bbpy\.ops\.wm\.open_mainfile\s*\(")
    if any(re.search(pattern, code) for pattern in forbidden):
        raise RuntimeError("Safety check stopped code that can execute arbitrary commands or replace the open project.")
    destructive = (r"bpy\.ops\.object\.delete\s*\(", r"bpy\.data\.objects\.remove\s*\(", r"bpy\.ops\.outliner\.orphans_purge", r"bpy\.data\.(?:meshes|materials|collections)\.remove\s*\(")
    if not allow_destructive and any(re.search(pattern, code) for pattern in destructive):
        raise RuntimeError("Safety check stopped a destructive Blender script. Explicitly ask to clear/reset the scene to permit it.")

def make_diff(before: str, after: str) -> str:
    return "\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile="scene_script.py (before)", tofile="scene_script.py (after)",
        lineterm="",
    ))

def extract_code_and_explanation(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    m = re.search(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        code = m.group(1).strip()
        explanation = text[m.end():].strip()
        return code, explanation
    m = re.search(r"```\s*(.*?)```", text, re.DOTALL)
    if m:
        code = m.group(1).strip()
        explanation = text[m.end():].strip()
        return code, explanation
    if text.strip().startswith("import ") or text.strip().startswith("bpy."):
        return text.strip(), ""
    return "", text.strip()

def extract_code(text: str) -> str:
    code, _ = extract_code_and_explanation(text)
    return code

# ===========================================================================
# AI ENGINES
# ===========================================================================
def omniroute_available() -> bool:
    """Fast check that configured OmniRoute credentials and host are available."""
    if not OMNIROUTE_API_KEY or not OPENAI_OK:
        return False
    try:
        with socket.create_connection((OMNIROUTE_HOST, OMNIROUTE_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def call_omniroute(sys_p: str, usr_p: str, image_path: Path | str | None = None) -> tuple[str, str]:
    if not OMNIROUTE_API_KEY:
        raise RuntimeError("OmniRoute is not configured.")
    if not OPENAI_OK:
        raise RuntimeError("OpenAI-compatible client is unavailable.")
    if not omniroute_available():
        raise RuntimeError("OmniRoute server offline/unreachable")

    client = OpenAI(
        base_url=OMNIROUTE_BASE,
        api_key=OMNIROUTE_API_KEY,
        timeout=OMNIROUTE_TIMEOUT,
    )

    messages = [{"role": "system", "content": sys_p}]

    usr_content = usr_p or "Recreate this image as a 3D model/scene in Blender."
    if image_path and Path(image_path).exists():
        try:
            ext = Path(image_path).suffix.lower().lstrip(".") or "png"
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            usr_content = [
                {"type": "text", "text": usr_p or "Recreate this image as a 3D model/scene in Blender."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            ]
        except Exception as exc:
            logger.warning("OmniRoute image could not be encoded (%s)", type(exc).__name__)

    messages.append({"role": "user", "content": usr_content})

    models_to_try = ["auto/coding:free", "claudecode", OMNIROUTE_MODEL,
                      "auto/best-coding", "auto/coding", "auto"]
    failed_models = []

    for model in dict.fromkeys(models_to_try):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.15,
            )
            content = resp.choices[0].message.content if resp.choices else ""
            if content and content.strip():
                log_event("omniroute_ok", model=model)
                return content, f"OmniRoute ({model})"
            else:
                failed_models.append(model)
        except Exception as exc:
            failed_models.append(model)

    raise RuntimeError(f"OmniRoute could not complete the request ({len(failed_models)} model attempts).")


def call_gemini(sys_p: str, usr_p: str, image_path: Path | str | None = None) -> tuple[str, str]:
    if google_genai is None:
        raise RuntimeError("Gemini client is unavailable.")
    if not GEMINI_KEYS:
        raise RuntimeError("Gemini is not configured.")
    full = f"{sys_p}\n\nUser request:\n{usr_p}"
    contents = [full]
    if image_path and Path(image_path).exists() and PIL_OK:
        try:
            img = PilImage.open(image_path)
            contents.append(img)
        except Exception as exc:
            logger.warning("Gemini image could not be loaded (%s)", type(exc).__name__)

    keys = list(GEMINI_KEYS)
    random.shuffle(keys)
    for ki, key in enumerate(keys, 1):
        for model in GEMINI_MODELS:
            try:
                client = google_genai.Client(api_key=key)
                resp   = client.models.generate_content(model=model, contents=contents)
                txt    = getattr(resp, "text", "") or (
                    resp.candidates[0].content.parts[0].text
                    if getattr(resp, "candidates", None) else ""
                )
                if txt:
                    log_event("gemini_ok", key_index=ki, model=model)
                    return txt, f"Gemini key #{ki} / {model}"
            except Exception as exc:
                s = str(exc)
                if "429" in s or "RESOURCE_EXHAUSTED" in s:
                    continue
                break
    raise RuntimeError("Gemini could not complete the request.")


REFERENCE_ANALYSIS_PROMPT = """You are a precise technical illustrator and Blender scene analyst.
Analyse the supplied reference image as a diagram or product/scene reference. Return concise JSON
only with: canvas_aspect_ratio, view_type, major_components (name, relative_position, relative_size,
shape), connectors_or_relationships, visible_labels, palette, depth_order, camera, and accuracy_notes.
Do not invent hidden detail; identify uncertainty explicitly. This specification will drive an editable
Blender reconstruction, so prioritize relative positions, proportions, hierarchy, and labels."""


def _blender_image_part(image_path: Path | str):
    """Read an image once and create a Gemini-shaped inline image part."""
    path = Path(image_path)
    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError("Unable to read the supplied Blender reference image.") from exc
    if not image_bytes:
        raise ValueError("The supplied Blender reference image is empty.")
    ext = path.suffix.lower()
    mime = "image/jpeg" if ext in {".jpg", ".jpeg"} else f"image/{ext.lstrip('.') or 'png'}"
    try:
        from google.genai import types
        return types.Part.from_bytes(data=image_bytes, mime_type=mime)
    except (ImportError, AttributeError):
        return {"inline_data": {"mime_type": mime, "data": image_bytes}}


def _shared_blender_generate(
    system_prompt: str,
    user_prompt: str,
    image_path: Path | str | None = None,
    manager=None,
    *,
    temperature: float = 0.15,
    max_tokens: int = 8192,
) -> tuple[str, str]:
    """Route Blender generation and vision through the shared provider policy."""
    route_manager = manager
    if route_manager is not None and hasattr(route_manager, "_km"):
        route_manager = route_manager._km
    if route_manager is None:
        if RoleKeyManager is None:
            raise RuntimeError("The shared AI provider router is unavailable.")
        route_manager = RoleKeyManager()
    if not hasattr(route_manager, "generate"):
        raise RuntimeError("The shared AI provider router is unavailable.")

    parts = [{"text": user_prompt}]
    if image_path:
        parts.append(_blender_image_part(image_path))
    config = type("_BlenderGenerateConfig", (), {
        "system_instruction": system_prompt,
        "temperature": temperature,
        "top_p": 0.9,
        "max_output_tokens": max_tokens,
    })()
    pool_name = getattr(route_manager, "ALL_POOL", "all")
    response, provider_label = route_manager.generate(
        pool_name,
        model=BLENDER_MODEL,
        contents=[{"role": "user", "parts": parts}],
        config=config,
    )
    try:
        text = getattr(response, "text", None)
    except Exception:
        text = None
    if not isinstance(text, str) or not text.strip():
        text_parts = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text:
                    text_parts.append(part_text)
        text = "\n".join(text_parts)
    if not text.strip():
        raise RuntimeError("The selected AI provider returned no text.")
    return text, str(provider_label)


def analyse_reference_image(image_path: Path | str | None, manager=None) -> str:
    """Turn an attached image into a compact, explicit build specification."""
    if not image_path or not Path(image_path).is_file():
        return ""
    dimensions = "unknown"
    if PIL_OK:
        try:
            with PilImage.open(image_path) as image:
                dimensions = f"{image.width}x{image.height}"
        except Exception:
            pass
    request = ("Create an accurate reconstruction specification for this image. "
               "The user needs an editable 3D diagram, not a flat image. "
               f"Reference dimensions: {dimensions}.")
    try:
        text, _ = _shared_blender_generate(
            REFERENCE_ANALYSIS_PROMPT, request, image_path=image_path, manager=manager,
            temperature=0.1, max_tokens=4096,
        )
        return text.strip()[:12_000]
    except Exception as exc:
        log_event("reference_analysis_unavailable", error_type=type(exc).__name__)
        return f"Reference image attached ({dimensions}). Inspect it directly and preserve its proportions, labels, and layout."


def call_ai(
    user_prompt: str,
    image_path:     Path | str | None = None,
    error_context:  str | None = None,
    scene_info:     str | None = None,
    current_script: str | None = None,
    max_retries:    int = 3,
    retry_delay:    float = 60.0,  # 1 minute base delay
    manager=None,
) -> AIResult:
    """Generate through the shared Codex → Gemini → local → OmniRoute policy."""
    del max_retries, retry_delay  # Retained in the public signature for callers.
    sys_p = f"{SYSTEM_PROMPT}\n\n{BLENDER_5_SYSTEM_PROMPT_INJECT}"
    if image_path and Path(image_path).exists():
        sys_p += (
            "\n\n════ VISUAL REFERENCE IMAGE ATTACHED ════\n"
            "The user provided a reference image. Analyze its 3D geometry, shapes, colors, layout, "
            "materials, and lighting, and generate Python code (bpy) that recreates this scene in Blender."
        )
    if scene_info:
        sys_p += f"\n\n════ LIVE BLENDER SCENE INFO ════\n{scene_info}"
    if current_script:
        tail = current_script[-8000:]
        sys_p += f"\n\n════ CURRENT PERSISTED SCENE SCRIPT ════\n```python\n{tail}\n```"
    if error_context:
        sys_p += (
            f"\n\n════ ⚠ PREVIOUS ERROR — YOU MUST FIX THIS ════\n"
            f"{error_context}\n"
            "Analyse the error carefully. Common fixes:\n"
            "  • Emission Color / Base Color socket error: Use safe_get_input(bsdf, 'Emission Color', 'Emission', 'Color')\n"
            "  • 'rings' keyword error: primitive_torus_add uses major_segments/minor_segments. primitive_uv_sphere_add uses segments/ring_count.\n"
            "  • 'Factor' socket error: Mix shader node factor input is .get('Factor') or .get('Fac')\n"
            "  • 'WaveModifier' has no 'damp': Use 'dampening' property\n"
            "  • 'OceanModifier' geometry error: Use mod.geometry_mode = 'GENERATE' or 'DISPLACE'\n"
            "  • 'ShaderNodeGradientTexture' undefined: Use 'ShaderNodeTexGradient'\n"
            "  • Forgot `import math` / `import mathutils` / `import random`\n"
            "  • Set scene.render.engine to BLENDER_EEVEE_NEXT (MUST be 'BLENDER_EEVEE')\n"
            "Return ONLY the corrected ```python``` block."
        )
    text, engine = _shared_blender_generate(
        sys_p, user_prompt, image_path=image_path, manager=manager,
        temperature=0.15, max_tokens=8192,
    )
    return AIResult(text=text, engine=engine, trail=[f"{engine} ✓"])


def audit_visual_quality(screenshot_path: Path, prompt: str, manager=None) -> tuple[bool | None, str]:
    """
    Pass the viewport screenshot to multimodal AI to evaluate visual quality,
    framing, lighting, and camera positioning.
    Returns (passed: bool, feedback: str).
    """
    if not screenshot_path or not screenshot_path.exists():
        return True, "No screenshot available for visual audit."

    audit_prompt = (
        f"You are a 3D Visual Quality Auditor inspecting a Blender viewport render for the prompt: '{prompt}'.\n"
        "Evaluate the screenshot carefully:\n"
        "1. Is the subject clearly visible and properly framed in the viewport? (Not pitch black, empty void, or severe clipping)\n"
        "2. Is the camera pointing at the object with adequate lighting?\n"
        "3. Are there obvious visual defects (e.g. object completely out of frame, pitch dark rendering)?\n\n"
        "Respond with EXACTLY 'QUALITY_PASS' if acceptable.\n"
        "If unacceptable, respond with 'QUALITY_FAIL: <1-2 sentences explaining what to fix in camera framing, lighting, or mesh location>'."
    )
    sys_p = "You are a precise 3D Blender visual inspector. Reply with QUALITY_PASS or QUALITY_FAIL: <reason>."
    try:
        txt, _ = _shared_blender_generate(
            sys_p, audit_prompt, image_path=screenshot_path, manager=manager,
            temperature=0.1, max_tokens=1024,
        )

        if "QUALITY_PASS" in txt:
            return True, "Visual quality verified."
        elif "QUALITY_FAIL:" in txt:
            reason = txt.split("QUALITY_FAIL:", 1)[1].strip()
            return False, reason
        else:
            return True, "Visual audit completed."
    except Exception as exc:
        logger.warning("Visual audit unavailable (%s)", type(exc).__name__)
        log_event("visual_audit_unavailable", error_type=type(exc).__name__)
        return None, "Visual audit unavailable; no multimodal provider completed the request."


# ===========================================================================
# BLENDER MULTI-AGENT PIPELINE  (5 Gemini keys + OmniRoute)
# ===========================================================================
# Mirrors coding_agent.py but for Blender script generation.
# Splits the script into independent sections, assigns each to a key.
# ─── Role assignment ────────────────────────────────────────────────────────
#   key-1 (SEARCH_POOL)  → SCENE PLANNER        : analyzes request, plans object hierarchy, layout
#   key-2 (SEARCH_POOL)  → GEOMETRY ARCHITECT  : creates meshes, curves, modifiers, object setup
#   key-3 (TOOL_POOL)    → MATERIAL ARTIST     : shaders, nodes, textures, UV, emission
#   key-4 (TOOL_POOL)    → ANIMATION DIRECTOR  : keyframes, drivers, constraints, rigging
#   key-5 (TOOL_POOL)    → LIGHTING & CAMERA   : lights, world, camera, render settings
#   REVIEWER (key-4)     → CODE REVIEWER       : checks for Blender API errors, deprecated calls
#   DIRECTOR (key-5)     → FINAL DIRECTOR      : merges sections, writes summary
# ─────────────────────────────────────────────────────────────────────────────
#
# Each worker generates ONE section of the script. Sections are concatenated
# in dependency order: Scene Setup → Geometry → Materials → Animation → Lighting/Camera.
# Reviewer checks the merged script; Director produces the final explanation.

import concurrent.futures
from google.genai import types

BLENDER_MODEL = "gemini-3.1-flash-lite"
BLENDER_SECTIONS = [
    ("scene_setup", "Scene Setup & Object Hierarchy",
     "Inspect the existing scene and add only the required foundation; preserve unrelated objects, frame range, and render settings, "
     "world background, collection hierarchy, empty parents for organization."),
    ("geometry", "Geometry & Mesh Creation",
     "Create all meshes, curves, text objects, modifiers (Subdivision, Solidify, Array, etc.), "
     "object transforms, naming. NO materials, NO animation, NO lights here."),
    ("materials", "Materials & Shaders",
     "Create ALL materials with Principled BSDF or node trees. Use safe_get_input for sockets. "
     "Assign materials to objects by name. Handle emission, transparency, UV maps."),
    ("animation", "Animation & Rigging",
     "Keyframe insertion for location/rotation/scale. Drivers, constraints (Track To, Follow Path), "
     "shape keys, modifiers with animation. NO material or lighting changes."),
    ("lighting_camera", "Lighting, World & Camera",
     "Lights (Sun, Area, Point, Spot), world background nodes, camera placement, DOF, "
     "render settings (samples, resolution, color management)."),
]

# ─── System prompts per role ────────────────────────────────────────────────

PLANNER_SYSTEM = """You are the SCENE PLANNER for a Blender 3D pipeline.
Analyze the user's request and the current scene state. Produce a concise JSON plan:
{
  "objects": [{"name": "obj_name", "type": "MESH|CURVE|FONT|EMPTY", "purpose": "..."}, ...],
  "hierarchy": {"parent_name": ["child1", "child2"]},
  "animation_plan": "brief description of what animates",
  "material_plan": "brief description of material needs",
  "lighting_plan": "brief description of lighting needs",
  "camera_plan": "brief description of camera needs",
  "frame_range": [1, N],
  "render_engine": "BLENDER_EEVEE" or "CYCLES"
}
Return ONLY the JSON. No prose."""

GEOMETRY_SYSTEM = """You are the GEOMETRY ARCHITECT. Write ONLY the Geometry section of a Blender Python script.
Create meshes, curves, text, empties, modifiers. Use bpy.ops for primitives, bpy.data for data-blocks.
Follow Blender 5.2 API strictly:
- primitive_torus_add(major_radius, minor_radius, major_segments, minor_segments) — NO rings/segments
- primitive_uv_sphere_add(radius, segments, ring_count) — NO rings
- Use safe_get_input for any node sockets
- Object naming: explicit, descriptive
- NO materials, NO animation, NO lights, NO camera, NO world
Return ONLY a ```python``` code block."""

MATERIAL_SYSTEM = """You are the MATERIAL ARTIST. Write ONLY the Materials section of a Blender Python script.
Create all materials, node trees, assign to objects by name. Use safe_get_input for sockets.
Principled BSDF: Base Color, Roughness, Metallic, Emission Color, Emission Strength, Alpha.
For emission: set Emission Strength > 5.0 for EEVEE glow (no bloom attr).
For transparency: blend_method='BLEND', Shadow Method='HASHED'.
Return ONLY a ```python``` code block."""

ANIMATION_SYSTEM = """You are the ANIMATION DIRECTOR. Write ONLY the Animation section.
Insert keyframes for location, rotation_euler, scale. Use drivers, constraints (Track To, Follow Path).
Set frame_current before keyframe_insert. Use radians for rotation.
Handle shape keys, animated modifiers. NO material/lighting/camera changes.
Return ONLY a ```python``` code block."""

LIGHTING_CAMERA_SYSTEM = """You are the LIGHTING & CAMERA SPECIALIST. Write ONLY this section.
Lights: Sun, Area, Point, Spot — set energy, size, color. Use real units.
World: node tree with Background, gradients, HDRI if path given.
Camera: location, rotation, lens, DOF (cam.data.dof.use_dof = True).
Render: engine, samples, resolution, color management (Filmic).
Return ONLY a ```python``` code block."""

REVIEWER_SYSTEM = """You are the CODE REVIEWER for Blender 5.2 Python.
Check the FULL merged script for:
- Deprecated API: ShaderNodeGradientTexture→ShaderNodeTexGradient, ShaderNodeNoiseTexture→ShaderNodeTexNoise
- 'rings' in torus/sphere, 'damp' in WaveModifier, 'geometry_final' in OceanModifier
- 'BLENDER_EEVEE_NEXT', scene.eevee.use_bloom/use_gtao/use_ssr (removed)
- cam.use_dof (use cam.data.dof.use_dof)
- text_obj.data.align_y = 'MIDDLE' (valid: TOP, CENTER, BOTTOM_BASELINE, etc.)
- Direct dict access on node inputs (must use safe_get_input or .get())
- Missing imports: bpy, math, mathutils, random
- bpy.ops without context (must set active object, select)
Return 'OK' if clean, or 'ISSUE: <specific fix>' if problems found."""

DIRECTOR_SYSTEM = """You are the FINAL DIRECTOR. The script has passed review.
Write a 3-4 sentence student-friendly summary explaining:
- What 3D scene was created (objects, style)
- Key animation/lighting/camera choices
- Any notable techniques used
Do NOT mention internal roles, keys, or pipeline. Just describe the result."""


class BlenderWorkspace:
    """Shared workspace for script sections."""
    
    def __init__(self):
        self.sections: dict[str, str] = {s[0]: "" for s in BLENDER_SECTIONS}
        self._lock = threading.Lock()
        self.plan: dict = {}
    
    def write_section(self, section_id: str, code: str):
        with self._lock:
            self.sections[section_id] = code
    
    def read_section(self, section_id: str) -> str:
        with self._lock:
            return self.sections.get(section_id, "")
    
    def get_merged_script(self) -> str:
        """Concatenate sections in dependency order."""
        with self._lock:
            order = [s[0] for s in BLENDER_SECTIONS]
            parts = []
            for sid in order:
                code = self.sections.get(sid, "").strip()
                if code:
                    parts.append(f"# ═══ {sid.upper()} ═══\n{code}")
            return "\n\n".join(parts)
    
    def set_plan(self, plan: dict):
        with self._lock:
            self.plan = plan
    
    def get_plan(self) -> dict:
        with self._lock:
            return dict(self.plan)


class BlenderProjectManager:
    """Manages multiple Blender projects concurrently."""
    
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._projects: dict[str, dict] = {}  # project_id -> {workspace, metadata, status}
        self._lock = threading.Lock()
    
    def create_project(self, project_id: str, name: str = "") -> BlenderWorkspace:
        """Create a new project with its own workspace."""
        with self._lock:
            project_dir = self.base_dir / project_id
            project_dir.mkdir(exist_ok=True)
            
            workspace = BlenderWorkspace()
            self._projects[project_id] = {
                "workspace": workspace,
                "name": name or project_id,
                "dir": project_dir,
                "created": datetime.now().isoformat(),
                "status": "active",
                "scene_script": project_dir / "scene_script.py",
                "screenshots": project_dir / "screenshots",
            }
            self._projects[project_id]["screenshots"].mkdir(exist_ok=True)
            return workspace
    
    def get_project(self, project_id: str) -> dict | None:
        with self._lock:
            return self._projects.get(project_id)
    
    def list_projects(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": pid,
                    "name": p["name"],
                    "created": p["created"],
                    "status": p["status"],
                }
                for pid, p in self._projects.items()
            ]
    
    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            if project_id in self._projects:
                del self._projects[project_id]
                return True
            return False
    
    def get_workspace(self, project_id: str) -> BlenderWorkspace | None:
        with self._lock:
            p = self._projects.get(project_id)
            return p["workspace"] if p else None


class BlenderCodingAgentAdapter:
    """
    Adapter that wraps the CodingAgent pipeline for Blender bpy code generation.
    Uses the 5-role pipeline: Thinker → Designer → Coding Swarm → Checker → CEO
    """
    
    def __init__(self, key_manager, workspace_dir: str):
        # Accept either RoleKeyManager or AiApi
        if hasattr(key_manager, '_km'):
            km = key_manager._km
        else:
            km = key_manager
        self.coding_agent = CodingAgent(km, workspace_dir)
        self.workspace_dir = workspace_dir
    
    def generate_blender_script(self, prompt: str, scene_info: str = "", 
                                 image_path: Path | None = None,
                                 progress_cb: Callable[[str, str], None] = None) -> dict:
        """
        Generate a complete Blender bpy script using the 5-role coding agent pipeline.
        """
        # Enhanced prompt for Blender-specific generation
        blender_request = f"""
Generate a complete Blender Python (bpy) script for the following request:

{prompt}

CURRENT SCENE INFO:
{scene_info}

REQUIREMENTS:
- Output MUST be a single ```python``` code block that runs via exec(code, {{"bpy": bpy}})
- Include all necessary imports: bpy, math, mathutils, random
- Use safe_get_input helper for node socket access
- Follow Blender 5.2 LTS API constraints strictly
- Set scene frame range first, then create objects, materials, animation, lighting, camera
- Use BLENDER_EEVEE or CYCLES render engine (NEVER BLENDER_EEVEE_NEXT)
- Handle errors gracefully with try/except where appropriate
"""
        
        def on_step(role_label, detail):
            if progress_cb:
                progress_cb(role_label, detail[:200])
        
        try:
            result = self.coding_agent.run(blender_request, on_step=on_step)
            return result
        except Exception as e:
            logger.error(f"Coding agent pipeline failed: {e}")
            return {"error": str(e), "changes": []}


class BlenderAgentPipeline:
    """Multi-agent Blender script generator using 5 Gemini keys + OmniRoute."""
    
    def __init__(self, key_manager):
        # Accept either RoleKeyManager or AiApi
        if hasattr(key_manager, '_km'):
            self.km = key_manager._km
        else:
            self.km = key_manager
        self.ws = BlenderWorkspace()
        self.trail: list[str] = []
    
    # ─── Role-specific calls (fixed key per role) ───────────────────────────
    
    def _call_role(self, role_idx: int, system: str, user: str, image_path: Path | None = None) -> tuple[str, str]:
        """Call a specific key (0-4) with system prompt. Returns (text, key_label)."""
        client, label = self.km.client_for_role(role_idx)
        config = types.GenerateContentConfig(system_instruction=system, temperature=0.2)
        contents = [types.Content(role="user", parts=[types.Part(text=user)])]
        if image_path and Path(image_path).exists():
            try:
                from PIL import Image as PILImage
                img = PILImage.open(image_path)
                contents[0].parts.append(img)
            except Exception:
                pass
        resp = client.models.generate_content(model=BLENDER_MODEL, contents=contents, config=config)
        label = getattr(client, "last_label", label)
        text = "".join(p.text or "" for p in resp.candidates[0].content.parts)
        return text, label
    
    def _call_pool(self, pool: str, system: str, user: str, image_path: Path | None = None) -> tuple[str, str]:
        """Round-robin within a pool. Returns (text, key_label)."""
        response, label = self.km.generate(pool, model=BLENDER_MODEL,
                                           contents=[types.Content(role="user", parts=[types.Part(text=user)])],
                                           config=types.GenerateContentConfig(system_instruction=system, temperature=0.2))
        text = "".join(p.text or "" for p in response.candidates[0].content.parts)
        return text, label
    
    # ─── Pipeline stages ────────────────────────────────────────────────────
    
    def plan(self, prompt: str, scene_info: str, image_path: Path | None = None) -> dict:
        """Stage 1: Scene Planner (key-1)."""
        user = f"REQUEST:\n{prompt}\n\nSCENE INFO:\n{scene_info}"
        text, label = self._call_role(0, PLANNER_SYSTEM, user, image_path)
        self.trail.append(f"Planner ({label}) ✓")
        try:
            plan = json.loads(text.strip().strip("`").lstrip("json").strip())
            self.ws.set_plan(plan)
            return plan
        except Exception:
            # Fallback minimal plan
            plan = {"objects": [], "frame_range": [1, 120], "render_engine": "BLENDER_EEVEE"}
            self.ws.set_plan(plan)
            return plan
    
    def _write_section_worker(self, section_id: str, section_name: str, system: str,
                               prompt: str, plan: dict, image_path: Path | None,
                               role_idx: int, fix_note: str = "") -> str:
        """Single worker for one script section."""
        sys = system
        if fix_note:
            sys += f"\n\nREVIEWER FLAGGED: {fix_note}. Fix exactly this."
        user = (f"SECTION: {section_name}\n"
                f"PLAN: {json.dumps(plan, indent=2)}\n"
                f"REQUEST: {prompt}\n\n"
                f"Write ONLY this section. Assume other sections exist. "
                f"Reference objects by name from plan. Return ONE ```python``` block.")
        text, label = self._call_role(role_idx, sys, user, image_path)
        # Extract code block
        m = re.search(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        code = m.group(1).strip() if m else text.strip()
        self.ws.write_section(section_id, code)
        return label
    
    def generate_sections(self, prompt: str, scene_info: str, image_path: Path | None = None,
                          progress_cb: Callable[[str, str], None] = None) -> list[str]:
        """Stage 2: Parallel section generation (keys 1-4 for geometry, materials, animation, lighting)."""
        plan = self.ws.get_plan()
    
        # Determine which keys to use for which sections
        # key-1 (idx 0) = planner, key-2 (idx 1) = geometry, key-3 (idx 2) = materials,
        # key-4 (idx 3) = animation, key-5 (idx 4) = lighting_camera
        section_assignments = [
            ("geometry", "Geometry & Mesh Creation", GEOMETRY_SYSTEM, 1),
            ("materials", "Materials & Shaders", MATERIAL_SYSTEM, 2),
            ("animation", "Animation & Rigging", ANIMATION_SYSTEM, 3),
            ("lighting_camera", "Lighting, World & Camera", LIGHTING_CAMERA_SYSTEM, 4),
        ]
    
        if progress_cb:
            progress_cb("🧱 Geometry Architect", "Writing mesh/modifier code...")
        self._write_section_worker(*section_assignments[0], prompt, plan, image_path)
        self.trail.append(f"Geometry (key-2) ✓")
    
        if progress_cb:
            progress_cb("🎨 Material Artist", "Building shaders & node trees...")
        self._write_section_worker(*section_assignments[1], prompt, plan, image_path)
        self.trail.append(f"Materials (key-3) ✓")
    
        if progress_cb:
            progress_cb("🎬 Animation Director", "Keyframing & constraints...")
        self._write_section_worker(*section_assignments[2], prompt, plan, image_path)
        self.trail.append(f"Animation (key-4) ✓")
    
        if progress_cb:
            progress_cb("💡 Lighting & Camera", "Lights, world, camera, render...")
        self._write_section_worker(*section_assignments[3], prompt, plan, image_path)
        self.trail.append(f"Lighting/Camera (key-5) ✓")
    
        return [s[0] for s in section_assignments]
    
    def review(self, prompt: str, progress_cb: Callable[[str, str], None] = None) -> str:
        """Stage 3: Code Reviewer (key-4)."""
        if progress_cb:
            progress_cb("🔍 Code Reviewer", "Checking for Blender API errors...")
        full_script = self.ws.get_merged_script()
        user = f"ORIGINAL REQUEST:\n{prompt}\n\nFULL SCRIPT TO REVIEW:\n```python\n{full_script}\n```"
        text, label = self._call_role(3, REVIEWER_SYSTEM, user)
        self.trail.append(f"Reviewer ({label}) ✓")
        return text.strip() if text.strip().upper().startswith("ISSUE") else ""
    
    def fix_section(self, section_id: str, section_name: str, system: str,
                    prompt: str, plan: dict, fix_note: str,
                    progress_cb: Callable[[str, str], None] = None):
        """Re-write a specific section after reviewer feedback."""
        if progress_cb:
            progress_cb(f"🔧 Fixing {section_name}", fix_note[:80])
        self._write_section_worker(section_id, section_name, system, prompt, plan, None, 3, fix_note)
    
    def final_report(self, prompt: str, progress_cb: Callable[[str, str], None] = None) -> str:
        """Stage 4: Final Director (key-5)."""
        if progress_cb:
            progress_cb("👔 Director", "Writing final summary...")
        full_script = self.ws.get_merged_script()
        user = f"ORIGINAL REQUEST:\n{prompt}\n\nFINAL SCRIPT:\n```python\n{full_script}\n```"
        text, label = self._call_role(4, DIRECTOR_SYSTEM, user)
        self.trail.append(f"Director ({label}) ✓")
        return text.strip()
    
# ─── Public entrypoint ──────────────────────────────────────────────────
    
    def run(self, prompt: str, scene_info: str, image_path: Path | None = None,
            progress_cb: Callable[[str, str], None] = None) -> dict:
        """Run the full pipeline."""
        self.trail = []
    
        # Stage 1: Plan
        if progress_cb:
            progress_cb("📋 Scene Planner", "Analyzing request & scene...")
        self.plan(prompt, scene_info, image_path)
    
        # Stage 2: Parallel section generation
        self.generate_sections(prompt, scene_info, image_path, progress_cb)
    
        # Stage 3: Review + optional fix
        max_fix_rounds = 2
        for round_num in range(max_fix_rounds):
            issue = self.review(prompt, progress_cb)
            if not issue:
                break
            if progress_cb:
                progress_cb(f"🔧 Fix Round {round_num+1}", issue[:100])
            # Fix the problematic section (simplified: re-generate all for now)
            self.generate_sections(prompt, scene_info, image_path, progress_cb)
    
        # Stage 4: Final report
        explanation = self.final_report(prompt, progress_cb)
    
        full_script = self.ws.get_merged_script()
        code, _ = extract_code_and_explanation(full_script)
    
        return {
            "code": code,
            "explanation": explanation,
            "trail": self.trail,
            "plan": self.ws.get_plan(),
        }


# ===========================================================================
# VISUAL VERIFICATION & ITERATIVE CORRECTION
# ===========================================================================

class VisualVerificationEngine:
    """
    Handles visual verification of Blender output using multimodal AI.
    Captures screenshots, evaluates quality, and provides corrective feedback.
    """
    
    def __init__(self, blender_socket: BlenderSocket, key_manager=None):
        self.client = blender_socket
        self.key_manager = key_manager
        self.history: list[dict] = []
    
    def capture_and_evaluate(self, prompt: str, attempt: int = 0) -> tuple[bool | None, str, Path | None]:
        """
        Capture viewport screenshot and evaluate visual quality.
        Returns (passed, feedback, screenshot_path).
        """
        # Capture screenshot
        screenshot = self.client.get_viewport_screenshot()
        
        if not screenshot or not screenshot.exists():
            # Execution can still be valid, but do not misrepresent it as visually verified.
            return None, "Visual verification unavailable: no viewport screenshot was returned.", None
        
        # Evaluate with multimodal AI
        passed, feedback = audit_visual_quality(screenshot, prompt, manager=self.key_manager)
        
        # Record in history
        self.history.append({
            "attempt": attempt,
            "screenshot": str(screenshot),
            "passed": passed,
            "feedback": feedback,
            "timestamp": datetime.now().isoformat()
        })
        
        return passed, feedback, screenshot
    
    def generate_correction_prompt(self, original_prompt: str, feedback: str, 
                                    attempt: int) -> str:
        """Generate an enhanced prompt for correction based on visual feedback."""
        return f"""
ORIGINAL REQUEST: {original_prompt}

VISUAL AUDIT FEEDBACK (Attempt {attempt + 1}): {feedback}

You are fixing a Blender scene that failed visual quality check. The feedback above describes
what's wrong with the current render (framing, lighting, object visibility, etc.).

Generate a minimal corrective bpy patch that addresses these issues while preserving all existing work:
- Fix camera positioning/framing if mentioned
- Fix lighting if too dark/bright or wrong direction
- Fix object placement if out of frame or clipped
- Fix materials if they don't look right
- Ensure the subject is clearly visible and well-lit

Return ONLY the corrected ```python``` code block.
"""
    
    def get_history_summary(self) -> str:
        """Get a summary of visual verification attempts."""
        if not self.history:
            return "No visual verification history."
        lines = ["Visual Verification History:"]
        for h in self.history:
            status = "✓ PASS" if h["passed"] else "✗ FAIL"
            lines.append(f"  Attempt {h['attempt']}: {status} - {h['feedback'][:100]}")
        return "\n".join(lines)


# ===========================================================================
# TASK COMPLEXITY ANALYZER & DECOMPOSER
# ===========================================================================

class TaskAnalyzer:
    """
    Analyzes task complexity and determines the appropriate generation strategy.
    """
    
    @staticmethod
    def analyze_complexity(prompt: str) -> tuple[int, str]:
        """
        Analyze prompt complexity and return (score, strategy).
        Score: 1-10, Strategy: 'simple' | 'pipeline' | 'decomposed'
        """
        prompt_lower = prompt.lower()
        score = 1
        
        # Count complexity indicators
        complexity_keywords = {
            'animation': 2, 'animate': 2, 'keyframe': 2, 'rig': 2, 'armature': 2,
            'particle': 2, 'fluid': 2, 'smoke': 2, 'simulation': 2,
            'multiple': 1, 'several': 1, 'many': 1, 'complex': 2, 'detailed': 1,
            'procedural': 2, 'node': 1, 'shader': 1, 'material': 1,
            'lighting': 1, 'camera': 1, 'render': 1, 'scene': 1,
            'character': 2, 'creature': 2, 'vehicle': 2, 'building': 2,
            'city': 3, 'landscape': 2, 'environment': 2,
            'orbit': 1, 'rotate': 1, 'move': 1, 'follow': 1, 'path': 1,
        }
        
        for kw, weight in complexity_keywords.items():
            if kw in prompt_lower:
                score += weight
        
        # Count objects mentioned (rough heuristic)
        object_indicators = ['sphere', 'cube', 'cylinder', 'plane', 'torus', 'cone', 
                            'object', 'mesh', 'model', 'character', 'building',
                            'car', 'tree', 'house', 'light', 'camera']
        obj_count = sum(1 for obj in object_indicators if obj in prompt_lower)
        score += min(obj_count, 5)
        
        # Determine strategy
        if score <= SIMPLE_TASK_THRESHOLD:
            strategy = 'simple'
        elif score <= COMPLEX_TASK_THRESHOLD:
            strategy = 'pipeline'
        else:
            strategy = 'decomposed'
        
        return min(score, 10), strategy
    
    @staticmethod
    def decompose_task(prompt: str, max_subtasks: int = 5) -> list[str]:
        """
        Decompose a complex task into ordered subtasks.
        Returns list of sub-prompts.
        """
        # This would ideally use an AI call, but for now use rule-based decomposition
        prompt_lower = prompt.lower()
        subtasks = []
        
        # Base scene setup always first
        subtasks.append("Inspect the existing scene and create only the required base additions; preserve unrelated objects and settings")
        
        # Geometry creation
        if any(kw in prompt_lower for kw in ['create', 'build', 'model', 'make', 'generate', 'object', 'mesh']):
            subtasks.append("Create all geometry: meshes, curves, text objects, modifiers")
        
        # Materials
        if any(kw in prompt_lower for kw in ['material', 'shader', 'color', 'texture', 'emission', 'glow', 'metal', 'glass']):
            subtasks.append("Create and assign materials with proper shaders and node trees")
        
        # Animation
        if any(kw in prompt_lower for kw in ['animate', 'animation', 'keyframe', 'move', 'rotate', 'orbit', 'spin', 'bounce']):
            subtasks.append("Add animation keyframes and drivers for object motion")
        
        # Lighting & Camera
        if any(kw in prompt_lower for kw in ['light', 'lighting', 'camera', 'render', 'dof', 'focus']):
            subtasks.append("Set up lighting, world environment, camera with DOF, and render settings")
        
        # Ensure we have at least the base subtasks
        if len(subtasks) == 1:
            subtasks.extend([
                "Create all geometry and objects",
                "Create and assign materials",
                "Add animation if needed",
                "Set up lighting, camera, and render settings"
            ])
        
        return subtasks[:max_subtasks]


# ===========================================================================
# MASTER ORCHESTRATOR - Combines everything
# ===========================================================================

class BlenderMasterOrchestrator:
    """
    Main orchestrator that combines:
    1. Task complexity analysis
    2. Coding Agent pipeline for code generation
    3. Visual verification loop with iterative correction
    4. BlenderMCP execution
    """
    
    def __init__(self, blender_socket: BlenderSocket, key_manager):
        self.client = blender_socket
        # Accept either RoleKeyManager or AiApi
        if hasattr(key_manager, '_km'):
            self.km = key_manager._km
        else:
            self.km = key_manager
        self.visual_engine = VisualVerificationEngine(blender_socket, self.km)
        self.task_analyzer = TaskAnalyzer()
        self.memory = ProjectMemory()
        
        # Initialize coding agent adapter if available
        self.coding_adapter = None
        if CodingAgent and Workspace:
            workspace_dir = str(WORKSPACE_DIR / "coding_agent_workspace")
            self.coding_adapter = BlenderCodingAgentAdapter(key_manager, workspace_dir)
    
    def execute_task(self, prompt: str, image_path: Path | None = None,
                     progress_cb: Callable[[str, str], None] = None) -> dict:
        """
        Execute a complete task with full pipeline: analyze → generate → execute → verify → correct.
        """
        # Persisted context informs the model, while live Blender inspection remains
        # authoritative. This prevents a new chat from treating an existing file as blank.
        original_request = prompt
        reference_spec = analyse_reference_image(image_path, manager=self.km)
        self.original_request = original_request
        self.allow_destructive = _is_explicit_reset_request(original_request)
        prompt = (f"{original_request}\n\nPROJECT MEMORY (reference only):\n{self.memory.context()}\n\n"
                  "EDIT POLICY: preserve this existing project. Inspect and modify only relevant "
                  "objects; never clear/delete/purge unrelated data unless the request explicitly "
                  "asks for a new or reset scene.")
        if reference_spec:
            prompt += ("\n\nREFERENCE IMAGE BUILD SPECIFICATION (use this to match the attached image; "
                       "do not invent details marked uncertain):\n" + reference_spec)
        # Analyze task complexity
        complexity_score, strategy = self.task_analyzer.analyze_complexity(prompt)
        
        if progress_cb:
            progress_cb("🧠 Task Analyzer", f"Complexity: {complexity_score}/10, Strategy: {strategy}")
        
        log_event("task_start", prompt=prompt[:100], complexity=complexity_score, strategy=strategy)
        
        # Choose execution strategy
        try:
            if strategy == 'simple':
                return self._execute_simple(prompt, image_path, progress_cb)
            elif strategy == 'pipeline':
                return self._execute_pipeline(prompt, image_path, progress_cb)
            else:  # decomposed
                return self._execute_decomposed(prompt, image_path, progress_cb)
        except Exception as exc:
            log_error("execute_task_failed", exc, prompt=prompt[:200], strategy=strategy)
            return {
                "code": "",
                "explanation": f"Task execution failed: {exc}",
                "trail": [],
                "visual_pass": False,
                "attempts": 0
            }
    
    def _execute_simple(self, prompt: str, image_path: Path | None,
                        progress_cb: Callable[[str, str], None] = None) -> dict:
        """Simple single-pass generation with visual verification."""
        return self._execute_with_verification(prompt, image_path, progress_cb, use_pipeline=False)
    
    def _execute_pipeline(self, prompt: str, image_path: Path | None,
                          progress_cb: Callable[[str, str], None] = None) -> dict:
        """Use coding agent pipeline for generation with visual verification."""
        return self._execute_with_verification(prompt, image_path, progress_cb, use_pipeline=True)
    
    def _execute_decomposed(self, prompt: str, image_path: Path | None,
                            progress_cb: Callable[[str, str], None] = None) -> dict:
        """Decompose into subtasks, execute each with pipeline, then combine."""
        try:
            subtasks = self.task_analyzer.decompose_task(prompt)
            
            if progress_cb:
                progress_cb("📋 Task Decomposer", f"Split into {len(subtasks)} subtasks")
            
            combined_code = ""
            all_explanations = []
            all_trails = []
            
            for i, subtask in enumerate(subtasks):
                if progress_cb:
                    progress_cb(f"📦 Subtask {i+1}/{len(subtasks)}", subtask[:80])
                
                # Each subtask gets full pipeline + verification
                result = self._execute_with_verification(
                    f"{subtask}\n\nContext: This is part {i+1} of {len(subtasks)} for: {prompt}",
                    image_path if i == 0 else None,  # Only first subtask gets reference image
                    progress_cb,
                    use_pipeline=True
                )
                
                if result.get("code"):
                    combined_code += f"\n\n# ═══ SUBTASK {i+1} ═══\n{result['code']}"
                if result.get("explanation"):
                    all_explanations.append(result["explanation"])
                if result.get("trail"):
                    all_trails.extend(result["trail"])
            
            return {
                "code": combined_code.strip(),
                "explanation": "\n\n".join(all_explanations) if all_explanations else "Complex task completed in multiple stages.",
                "trail": all_trails,
                "strategy": "decomposed",
                "subtasks": len(subtasks)
            }
        except Exception as exc:
            log_error("decomposed_execution_failed", exc, prompt=prompt[:200])
            raise
    
    def _execute_with_verification(self, prompt: str, image_path: Path | None,
                                    progress_cb: Callable[[str, str], None] = None,
                                    use_pipeline: bool = True) -> dict:
        """
        Core execution loop: generate → execute → verify → correct (repeat).
        """
        error_context = None
        visual_history = ""
        
        for attempt in range(MAX_VISUAL_RETRIES + 1):
            try:
                if progress_cb:
                    progress_cb("🔄 Generation", f"Attempt {attempt + 1}/{MAX_VISUAL_RETRIES + 1}")
                
                # Get current scene info
                scene_info = self.client.get_scene_info()
                
                # Generate code
                if use_pipeline and self.coding_adapter:
                    # Use the 5-role coding agent pipeline
                    gen_result = self.coding_adapter.generate_blender_script(
                        prompt, scene_info, image_path, progress_cb
                    )
                    
                    if gen_result.get("error"):
                        raise RuntimeError(f"Pipeline generation failed: {gen_result['error']}")
                    
                    code = self._extract_code_from_changes(gen_result.get("changes", []))
                    explanation = gen_result.get("report", "Generated via coding agent pipeline.")
                    trail = [f"Pipeline: {gen_result.get('plan', '')[:50]}"]
                    
                else:
                    # Use original single-call AI generation
                    result = call_ai(prompt, image_path=image_path, error_context=error_context,
                                    scene_info=scene_info, current_script=read_scene_script(),
                                    manager=self.km)
                    code, explanation = extract_code_and_explanation(result.text)
                    trail = result.trail
                
                if not code:
                    raise RuntimeError("No executable code generated")

                _validate_scene_edit(code, getattr(self, "allow_destructive", False))
                
                # Execute in Blender
                if progress_cb:
                    progress_cb("⚡ Blender Execution", "Running code in Blender...")
                
                exec_result = self.client.execute(code)
                
                if not exec_result.success:
                    error_context = exec_result.message
                    if progress_cb:
                        progress_cb("❌ Execution Failed", error_context[:100])
                    log_event("execution_failed", attempt=attempt, error=error_context)
                    log_error("blender_execution_failed", RuntimeError(error_context), attempt=attempt)
                    continue
                
                # Save script
                before, after = append_scene_script(code)
                if progress_cb:
                    progress_cb("📝 Diff", "Script saved, capturing viewport...")
                
                # Visual verification
                if progress_cb:
                    progress_cb("👁 Visual Verification", "Analyzing render quality...")
                
                passed, feedback, screenshot = self.visual_engine.capture_and_evaluate(prompt, attempt)
                
                if passed is not False:
                    message = explanation or "Blender scene created and verified successfully."
                    message += f"\n\nAI route: {' → '.join(trail)}"
                    message += f"\n\nVisual audit: {feedback}"
                    if visual_history:
                        message += f"\n\n{visual_history}"
                    
                    log_event("task_success", attempt=attempt, visual_pass=passed)
                    self.memory.remember(getattr(self, "original_request", prompt), scene_info, "verified edit completed")
                    return {
                        "code": code,
                        "explanation": message,
                        "trail": trail,
                        "screenshot": screenshot,
                        "visual_pass": passed,
                        "attempts": attempt + 1
                    }
                else:
                    # Visual verification failed - prepare for correction
                    visual_history = self.visual_engine.get_history_summary()
                    error_context = f"VISUAL QUALITY FAILURE: {feedback}"
                    
                    if progress_cb:
                        progress_cb("🔧 Visual Correction Needed", feedback[:100])
                    
                    # Enhance prompt for next attempt with visual feedback
                    prompt = self.visual_engine.generate_correction_prompt(prompt, feedback, attempt)
                    log_event("visual_fail", attempt=attempt, feedback=feedback)
                    
            except Exception as exc:
                error_context = str(exc)
                log_event("attempt_exception", attempt=attempt, error=error_context)
                log_error("execution_loop_exception", exc, attempt=attempt, prompt=prompt[:200])
                if progress_cb:
                    progress_cb("❌ Error", str(exc)[:100])
        
        # All attempts exhausted
        final_msg = f"Failed after {MAX_VISUAL_RETRIES + 1} attempts.\nLast error: {error_context}"
        if visual_history:
            final_msg += f"\n\n{visual_history}"
        
        log_event("task_failed", attempts=MAX_VISUAL_RETRIES + 1, final_error=error_context)
        return {
            "code": "",
            "explanation": final_msg,
            "trail": [],
            "visual_pass": False,
            "attempts": MAX_VISUAL_RETRIES + 1
        }
    
    def _extract_code_from_changes(self, changes: list) -> str:
        """Extract merged code from coding agent changes."""
        if not changes:
            return ""
        
        # Changes are (path, (before, after)) tuples
        code_parts = []
        for path, (before, after) in changes:
            if after is not None:
                code_parts.append(f"# File: {path}\n{after}")
        
        return "\n\n".join(code_parts)


# ===========================================================================
# BLENDER SOCKET PROTOCOL
    # Talks directly to the BlenderMCP add-on (ahujasid/blender-mcp).
    # Response format: {"status": "success", "result": {...}} or {"status": "error", "message": "..."}
    # ===========================================================================
def blender_running() -> bool:
    if psutil is None:
        return False
    try:
        for p in psutil.process_iter(attrs=["name"]):
            if (p.info.get("name") or "").lower() in {"blender.exe", "blender"}:
                return True
    except Exception:
        pass
    return False

def socket_alive() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=SOCKET_TIMEOUT):
            return True
    except Exception:
        return False


class BlenderSocket:
    """Thread-safe persistent TCP connection to the BlenderMCP add-on."""
    
    def __init__(self):
        self.sock = None
        self.lock = threading.Lock()
    
    def _connect(self):
        s = socket.create_connection((HOST, PORT), timeout=SOCKET_TIMEOUT)
        s.settimeout(SOCKET_TIMEOUT)
        self.sock = s
        log_event("socket_connected", host=HOST, port=PORT)
    
    def close(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
                log_event("socket_closed")
    
    def _recv_json(self) -> dict:
        """Read one complete JSON response from the BlenderMCP stream."""
        chunks = []
        deadline = time.monotonic() + RESPONSE_TIMEOUT
        self.sock.settimeout(min(SOCKET_TIMEOUT, RESPONSE_TIMEOUT))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for BlenderMCP response")
            self.sock.settimeout(min(remaining, SOCKET_TIMEOUT))
            try:
                chunk = self.sock.recv(8192)
            except socket.timeout as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for BlenderMCP response") from exc
                continue
            if not chunk:
                if not chunks:
                    raise ConnectionError("BlenderMCP closed the connection before responding")
                raise ConnectionError("BlenderMCP closed the connection with incomplete JSON")
            chunks.append(chunk)
            data = b"".join(chunks)
            try:
                response = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(response, dict):
                raise ValueError("BlenderMCP response was not a JSON object")
            return response
    
    def send(self, cmd: str, params: dict | None = None) -> dict:
        """
        Send one command and return the parsed BlenderMCP response.
        """
        payload = json.dumps(
            {"type": cmd, "params": params or {}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        with self.lock:
            try:
                if self.sock is None:
                    self._connect()
                self.sock.sendall(payload)
                return self._recv_json()
            except Exception:
                self.close()
                raise
    
    def execute(self, code: str) -> ExecResult:
        """Run code in Blender and return a structured ExecResult."""
        try:
            resp   = self.send("execute_code", {"code": code})
            status = resp.get("status", "unknown")
            result = resp.get("result", {})
            if status == "success":
                stdout  = (result.get("result") or "") if isinstance(result, dict) else str(result)
                return ExecResult(success=True, message="Execution succeeded.", stdout=stdout, raw=resp)
            else:
                msg = resp.get("message", str(resp))
                return ExecResult(success=False, message=msg, raw=resp)
        except Exception as exc:
            return ExecResult(success=False, message=str(exc))
    
    def get_scene_info(self) -> str:
        """Return scene info as a JSON string (capped at 4000 chars)."""
        try:
            resp = self.send("get_scene_info")
            result = resp.get("result", resp)
            return json.dumps(result, ensure_ascii=False)[:4000]
        except Exception as exc:
            log_event("scene_info_failed", error=str(exc))
            return f"Scene info unavailable ({exc})."
    
    def get_viewport_screenshot(self) -> Path | None:
        """
        Ask Blender to save a viewport screenshot, then return its local path.
        Returns None on failure.
        """
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"viewport_{ts}.png"
        try:
            resp = self.send("get_viewport_screenshot", {
                "filepath": str(path).replace("\\", "/"),
                "format": "png",
                "max_size": 900,
            })
            if resp.get("status") == "success" and path.exists():
                log_event("screenshot_ok", path=str(path))
                return path
        except Exception as exc:
            log_event("screenshot_failed", error=str(exc))
        return None
    
    
    # ===========================================================================
    # CHAT BUBBLE WIDGET
    # ===========================================================================
class ChatBubble(ctk.CTkFrame):
    ROLES = {
        "user":      (USER_BG,    TEXT,    "e",  "You",           YELLOW),
        "assistant": (AGENT_BG,   TEXT,    "w",  "Blender Agent", ACCENT),
        "system":    (SYSTEM_BG,  MUTED,   "w",  "System",        MUTED),
        "error":     (ERROR_BG,   RED,     "w",  "Error",         RED),
        "success":   (SUCCESS_BG, GREEN,   "w",  "Success",       GREEN),
        "warn":      (WARN_BG,    YELLOW,  "w",  "Warning",       YELLOW),
        "screenshot":(SYSTEM_BG,  TEXT,    "w",  "Viewport",      PURPLE),
    }
    
    def __init__(self, parent, role: str, text: str, timestamp: str = "",
                 screenshot: Path | None = None):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
    
        bg, fg, side, label, label_color = self.ROLES.get(role, self.ROLES["assistant"])
    
        pad_l = (8, 80) if side == "w" else (80, 8)
        bubble = ctk.CTkFrame(self, fg_color=bg, corner_radius=14,
                              border_width=1, border_color=BORDER)
        bubble.grid(row=0, column=0, sticky="ew", padx=pad_l, pady=(3, 3))
    
        # ── meta row ──
        meta = ctk.CTkFrame(bubble, fg_color="transparent")
        meta.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(meta, text=label,
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=label_color).pack(side="left")
        if timestamp:
            ctk.CTkLabel(meta, text=timestamp,
                         font=ctk.CTkFont("Segoe UI", 10), text_color=MUTED).pack(side="right")
    
        # ── body text ──
        if text:
            ctk.CTkLabel(bubble, text=text, text_color=fg,
                         font=ctk.CTkFont("Segoe UI", 13),
                         wraplength=560, justify="left", anchor="w").pack(
                padx=14, pady=(5, screenshot is None and 12 or 6), fill="x")
    
        # ── embedded screenshot ──
        if screenshot and screenshot.exists() and PIL_OK:
            try:
                img   = PilImage.open(screenshot)
                img.thumbnail((480, 300), PilImage.LANCZOS)
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                       size=(img.width, img.height))
                lbl = ctk.CTkLabel(bubble, image=ctk_img, text="")
                lbl.pack(padx=14, pady=(0, 12))
            except Exception:
                pass
    
    
    # ===========================================================================
    # TYPING INDICATOR
    # ===========================================================================
class TypingIndicator(ctk.CTkFrame):
    def __init__(self, parent, text: str = "Agent is thinking"):
        super().__init__(parent, fg_color=AGENT_BG, corner_radius=14,
                         border_width=1, border_color=BORDER)
        self._dots = 0
        self._base_text = text
        self._lbl  = ctk.CTkLabel(self, text=f"{text} ●○○",
                                   font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=CYAN)
        self._lbl.pack(padx=16, pady=10)
        self._tick()
    
    def set_text(self, text: str):
        self._base_text = text
    
    def _tick(self):
        patterns = ["●○○", "○●○", "○○●"]
        self._dots = (self._dots + 1) % 3
        self._lbl.configure(text=f"{self._base_text} {patterns[self._dots]}")
        self.after(500, self._tick)
    
    
    # ===========================================================================

# MAIN APPLICATION
class BlenderAgentApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Blender 3D Agent — Campus Suite Pro")
        self.geometry("1220x820")
        self.minsize(1000, 680)
        self.configure(fg_color=BG)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        self.client = BlenderSocket()
        self.connected = False
        self.busy = False
        self._executing = False
        self.warned = False
        self.diff_open = False
        self.uploaded_image = None
        self.chat_row = 0
        self._typing_wdg = None
        self.history = []
        self.km = AiApi() if AiApi else None
        self._build_ui()
        self.after(SCAN_MS, self._scan)
        log_event("app_started")

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        sidebar = ctk.CTkFrame(self, fg_color=SIDEBAR, width=270, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        ctk.CTkLabel(sidebar, text="Blender Agent", font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT).pack(padx=20, pady=(24, 4), anchor="w")
        self.status_lbl = ctk.CTkLabel(sidebar, text="Scanning…", text_color=MUTED, anchor="w")
        self.status_lbl.pack(fill="x", padx=20, pady=4)
        self.engine_lbl = ctk.CTkLabel(sidebar, text="OmniRoute → Gemini", text_color=MUTED, anchor="w")
        self.engine_lbl.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(sidebar, text="SCENE PRESETS", text_color=MUTED).pack(padx=20, pady=(8, 4), anchor="w")
        presets = ctk.CTkScrollableFrame(sidebar, fg_color="transparent", height=300)
        presets.pack(fill="x", padx=8)
        for name, prompt in PRESETS:
            ctk.CTkButton(presets, text=name, anchor="w", fg_color=CARD, hover_color=CARD_HOVER, command=lambda p=prompt: self._set_preset(p)).pack(fill="x", padx=4, pady=2)
        ctk.CTkButton(sidebar, text="Screenshot Viewport", command=self._manual_screenshot).pack(fill="x", padx=16, pady=(18, 4))
        ctk.CTkButton(sidebar, text="Clear Scene Log", command=self._clear_scene_log).pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(sidebar, text="Reconnect Blender", command=self._reconnect).pack(fill="x", padx=16, pady=4)

        main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(main, text="Natural Language → Blender 3D", font=ctk.CTkFont(size=24, weight="bold"), text_color=TEXT).grid(row=0, column=0, padx=28, pady=(22, 10), sticky="w")
        self.chat_f = ctk.CTkScrollableFrame(main, fg_color=PANEL, corner_radius=16)
        self.chat_f.grid(row=1, column=0, padx=28, pady=(0, 10), sticky="nsew")
        self.chat_f.grid_columnconfigure(0, weight=1)
        self.diff_box = ctk.CTkTextbox(main, height=150, fg_color="#080c14", text_color=TEXT)
        self.diff_box.grid(row=2, column=0, padx=28, pady=(0, 10), sticky="ew")
        self.diff_box.configure(state="disabled")
        input_bar = ctk.CTkFrame(main, fg_color=BG)
        input_bar.grid(row=3, column=0, padx=28, pady=(0, 22), sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(input_bar, placeholder_text="Describe what to build in Blender…", height=52)
        self.entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.entry.bind("<Return>", self._send)
        ctk.CTkButton(input_bar, text="Image", width=90, command=self._pick_image).grid(row=0, column=1, padx=(0, 8))
        self.send_btn = ctk.CTkButton(input_bar, text="Generate", width=130, command=self._send)
        self.send_btn.grid(row=0, column=2)
        self._sys("Open Blender, enable the BlenderMCP add-on, start its server, then describe a scene.")

    def _scan(self):
        if self._executing:
            self.after(SCAN_MS, self._scan)
            return
        live = socket_alive()
        if live:
            self.connected = True
            self._set_status(f"Connected {HOST}:{PORT}", GREEN)
            if not self.warned:
                self.warned = True
                self._sys("BlenderMCP execute_code runs Python inside Blender. Use it only on your own machine.")
        elif blender_running():
            self.connected = False
            self._set_status("Blender open — start the MCP server", YELLOW)
        else:
            self.connected = False
            self._set_status("Blender not detected", RED)
        self.after(SCAN_MS, self._scan)

    def _send(self, _event=None):
        if self.busy:
            return
        prompt = self.entry.get().strip()
        image_path = self.uploaded_image
        if not prompt and image_path:
            prompt = "Recreate this reference image as a 3D scene in Blender."
        if not prompt:
            return
        if not self.connected:
            self._bubble("error", "Not connected to BlenderMCP. Start the add-on server first.")
            return
        self.entry.delete(0, "end")
        self._bubble("user", prompt)
        self.uploaded_image = None
        self.busy = True
        self.send_btn.configure(text="Working…", state="disabled")
        self._show_typing("Initializing master orchestrator…")
        threading.Thread(target=self._run_agent, args=(prompt, image_path), daemon=True).start()

    def _run_agent(self, prompt, image_path=None):
        self._executing = True
        try:
            # Initialize the master orchestrator
            orchestrator = BlenderMasterOrchestrator(self.client, self.km)
            
            def progress_callback(stage: str, detail: str):
                self._update_typing(f"{stage}: {detail}")
            
            # Execute the full task with orchestration
            result = orchestrator.execute_task(prompt, image_path, progress_callback)
            
            if result.get("visual_pass", False):
                screenshot = result.get("screenshot")
                message = result.get("explanation", "Task completed successfully.")
                self.after(0, lambda m=message, s=screenshot: self._safe_hide_typing_and_bubble("assistant", m, s))
            else:
                error_msg = result.get("explanation", "Task failed.")
                self.after(0, lambda e=error_msg: self._bubble("error", f"Agent stopped after all attempts.\n{e}"))
                
        except Exception as exc:
            log_error("agent_main_exception", exc, prompt=prompt[:200])
            self.after(0, lambda e=str(exc): self._bubble("error", f"Agent error: {e}"))
        finally:
            self._executing = False
            self._finish()

    def _pick_image(self):
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if path:
            self.uploaded_image = Path(path)
            self._sys(f"Attached reference image: {self.uploaded_image.name}")

    def _manual_screenshot(self):
        if not self.connected:
            self._bubble("error", "Not connected to Blender.")
            return
        def capture():
            path = self.client.get_viewport_screenshot()
            if path:
                self.after(0, lambda: self._bubble("screenshot", "Viewport captured.", path))
            else:
                self.after(0, lambda: self._bubble("warn", "Screenshot failed."))
        threading.Thread(target=capture, daemon=True).start()

    def _clear_scene_log(self):
        SCENE_SCRIPT.write_text("", encoding="utf-8")
        self._render_diff("# scene log cleared")

    def _reconnect(self):
        self.client.close()
        self.connected = False
        self._set_status("Reconnecting…", YELLOW)

    def _set_preset(self, prompt):
        self.entry.delete(0, "end")
        self.entry.insert(0, prompt)
        self.entry.focus()

    def _set_status(self, text, color):
        self.status_lbl.configure(text=text, text_color=color)

    def _sys(self, text):
        self._bubble("system", text)

    def _bubble(self, role, text, screenshot=None):
        bubble = ChatBubble(self.chat_f, role, text, timestamp=datetime.now().strftime("%H:%M"), screenshot=screenshot)
        bubble.grid(row=self.chat_row, column=0, sticky="ew", pady=2, padx=6)
        self.chat_row += 1

    def _show_typing(self, text="Agent is thinking"):
        if self._typing_wdg:
            self._typing_wdg.set_text(text)
            return
        self._typing_wdg = TypingIndicator(self.chat_f, text)
        self._typing_wdg.grid(row=self.chat_row, column=0, sticky="w", padx=14, pady=2)
        self.chat_row += 1

    def _update_typing(self, text):
        if self._typing_wdg:
            self.after(0, lambda: self._typing_wdg.set_text(text))

    def _hide_typing(self):
        if self._typing_wdg:
            self._typing_wdg.destroy()
            self._typing_wdg = None

    def _render_diff(self, text):
        self.diff_box.configure(state="normal")
        self.diff_box.delete("1.0", "end")
        self.diff_box.insert("1.0", text)
        self.diff_box.configure(state="disabled")

    def _safe_hide_typing_and_bubble(self, role, text, screenshot=None):
        self._hide_typing()
        self._bubble(role, text, screenshot)

    def _finish(self):
        self.busy = False
        self.after(0, self._hide_typing)
        self.after(0, lambda: self.send_btn.configure(text="Generate", state="normal"))

if __name__ == "__main__":
    app = BlenderAgentApp()
    app.mainloop()
