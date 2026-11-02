"""
Agent Orchestrator — Unified Multi-Agent System for Campus Suite
================================================================

Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │  ORCHESTRATOR (CEO)                                         │
  │  • Receives high-level user request                         │
  │  • Plans & decomposes into independent subtasks            │
  │  • Dispatches to specialist agents via task queue          │
  │  • Aggregates results, handles failures, retries           │
  └─────────────────────────────────────────────────────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
  │ STUDY AGENT │    │ CODING AGENT│    │ BLENDER AGENT│
  │ (keys 1-2)  │    │ (all 5 keys)│    │ (OmniRoute + │
  │             │    │  swarm      │    │  Gemini fb) │
  └─────────────┘    └─────────────┘    └─────────────┘
         ▲                  ▲                  ▲
         │                  │                  │
  ┌──────┴──────┐    ┌─────┴─────┐    ┌──────┴──────┐
  │ PPT AGENT   │    │ AUDITOR   │    │  TOOL AGENT │
  │ (voice/vision)│  │ (logs)    │    │ (web/search)│
  └─────────────┘    └─────────────┘    └─────────────┘

Key Features:
- 5 dedicated Gemini keys managed by RoleKeyManager (fixed roles + pools)
- OmniRoute as primary for coding/Blender, Gemini as fallback
- Dynamic task spawning with priority queue
- Real-time progress callbacks for UI
- Cross-agent context sharing via shared workspace
- Automatic retry with exponential backoff
- Structured logging to agent_logs.json
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

# ─── Core dependencies ────────────────────────────────────────────────────────
from key_manager import RoleKeyManager, MockRoleKeyManager

# ─── Optional agent imports (lazy to avoid circular deps) ────────────────────
try:
    from coding_agent import CodingAgent, Workspace
except ImportError:
    CodingAgent = None
    Workspace = None

try:
    from study_agent import StudyAgent
except ImportError:
    StudyAgent = None

try:
    from agents.personal_growth_mentor import PersonalGrowthMentor
except ImportError:
    PersonalGrowthMentor = None

try:
    from blender_agent import BlenderSocket, call_ai, extract_code_and_explanation, log_event
except ImportError:
    BlenderSocket = None
    call_ai = None
    extract_code_and_explanation = None
    log_event = lambda *a, **k: None

# ─── Constants & Configuration ──────────────────────────────────────────────
ORCHESTRATOR_DIR = Path(__file__).resolve().parent / "orchestrator_workspace"
ORCHESTRATOR_DIR.mkdir(exist_ok=True)
TASK_LOG = ORCHESTRATOR_DIR / "task_log.jsonl"
SHARED_WORKSPACE = ORCHESTRATOR_DIR / "shared"
SHARED_WORKSPACE.mkdir(exist_ok=True)

# Model assignments per agent type
MODEL_CODING = "gemini-3.1-flash-lite"
MODEL_STUDY_SEARCH = "gemini-2.5-flash"
MODEL_STUDY_TOOLS = "gemini-3.1-flash-lite"
MODEL_BLENDER = "auto/coding:free"  # OmniRoute

# ─── Enums & Data Classes ───────────────────────────────────────────────────


class AgentType(Enum):
    STUDY = "study"
    PERSONAL_GROWTH = "personal_growth"
    CODING = "coding"
    BLENDER = "blender"
    PPT = "ppt"
    TOOL = "tool"
    AUDITOR = "auditor"
    CEO = "ceo"


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class TaskPriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: AgentType = AgentType.CEO
    prompt: str = ""
    payload: dict = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    assigned_key: Optional[str] = None
    result: Any = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retries: int = 0
    max_retries: int = 2
    parent_id: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)  # task IDs this depends on

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "prompt": self.prompt,
            "payload": self.payload,
            "priority": self.priority.name,
            "status": self.status.value,
            "assigned_key": self.assigned_key,
            "result": str(self.result)[:500] if self.result else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retries": self.retries,
            "parent_id": self.parent_id,
            "depends_on": self.depends_on,
        }


@dataclass
class AgentWorker:
    """A worker bound to a specific API key."""
    key_label: str
    agent_type: AgentType
    client: Any = None
    busy: bool = False
    current_task_id: Optional[str] = None
    last_heartbeat: float = field(default_factory=time.time)

    def mark_busy(self, task_id: str):
        self.busy = True
        self.current_task_id = task_id
        self.last_heartbeat = time.time()

    def mark_free(self):
        self.busy = False
        self.current_task_id = None
        self.last_heartbeat = time.time()


# ─── Task Queue with Priority & Dependencies ────────────────────────────────


class TaskQueue:
    """Thread-safe priority queue with dependency resolution."""

    def __init__(self):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._pending: dict[str, Task] = {}
        self._running: dict[str, Task] = {}
        self._completed: dict[str, Task] = {}
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)

    def add(self, task: Task) -> str:
        with self._lock:
            self._pending[task.id] = task
            # PriorityQueue uses (priority, count, item) — negative priority for max-heap
            self._queue.put((-task.priority.value, time.time(), task.id))
            self._cv.notify()
            return task.id

    def get_next_ready(self) -> Optional[Task]:
        """Get next task whose dependencies are satisfied."""
        with self._lock:
            deferred = []
            # Scan only the current queue snapshot. Re-enqueuing blocked tasks
            # inside an unbounded loop otherwise spins forever on dependencies.
            for _ in range(self._queue.qsize()):
                _, _, task_id = self._queue.get()
                task = self._pending.get(task_id)
                if not task:
                    continue  # already taken
                known_ids = set(self._pending) | set(self._running) | set(self._completed)
                missing_deps = [dep_id for dep_id in task.depends_on if dep_id not in known_ids]
                if missing_deps:
                    task.status = TaskStatus.FAILED
                    task.error = f"Unknown dependency task(s): {', '.join(missing_deps)}"
                    task.completed_at = datetime.now().isoformat()
                    del self._pending[task_id]
                    self._completed[task_id] = task
                    self._log_task(task)
                    self._cv.notify_all()
                    continue

                failed_deps = [dep_id for dep_id in task.depends_on
                               if dep_id in self._completed and self._completed[dep_id].status != TaskStatus.COMPLETED]
                if failed_deps:
                    task.status = TaskStatus.FAILED
                    task.error = f"Dependency failed: {', '.join(failed_deps)}"
                    task.completed_at = datetime.now().isoformat()
                    del self._pending[task_id]
                    self._completed[task_id] = task
                    self._log_task(task)
                    self._cv.notify_all()
                    continue

                deps_met = all(dep_id in self._completed for dep_id in task.depends_on)
                if deps_met:
                    task.status = TaskStatus.RUNNING
                    task.started_at = datetime.now().isoformat()
                    del self._pending[task_id]
                    self._running[task_id] = task
                    for item in deferred:
                        self._queue.put(item)
                    return task
                else:
                    deferred.append((-task.priority.value, time.time(), task_id))
            for item in deferred:
                self._queue.put(item)
            return None

    def complete(self, task_id: str, result: Any = None, error: Optional[str] = None):
        with self._lock:
            task = self._running.pop(task_id, None) or self._pending.pop(task_id, None)
            if task:
                task.status = TaskStatus.COMPLETED if error is None else TaskStatus.FAILED
                task.result = result
                task.error = error
                task.completed_at = datetime.now().isoformat()
                self._completed[task_id] = task
                self._log_task(task)
                self._cv.notify_all()

    def retry(self, task_id: str):
        with self._lock:
            task = self._running.pop(task_id, None) or self._completed.pop(task_id, None) or self._pending.pop(task_id, None)
            if task and task.retries < task.max_retries:
                task.retries += 1
                task.status = TaskStatus.RETRYING
                task.error = None
                self._pending[task_id] = task
                self._queue.put((-task.priority.value, time.time(), task_id))
                self._cv.notify()

    def get_status(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return (self._pending.get(task_id) or
                    self._running.get(task_id) or
                    self._completed.get(task_id))

    def wait_for(self, task_id: str, timeout: float = 300) -> Optional[Task]:
        with self._cv:
            deadline = time.time() + timeout
            while time.time() < deadline:
                task = self.get_status(task_id)
                if task and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    return task
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=min(1.0, remaining))
            return self.get_status(task_id)

    def _log_task(self, task: Task):
        try:
            with open(TASK_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(task.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass


# ─── Key Manager Wrapper for Orchestrator ───────────────────────────────────


class OrchestratorKeyManager:
    """Wraps RoleKeyManager to provide worker-friendly interface."""

    def __init__(self, use_mock: bool = False):
        if use_mock:
            self.km = MockRoleKeyManager()
        else:
            self.km = RoleKeyManager()

        self._workers: dict[str, AgentWorker] = {}
        self._lock = threading.Lock()

        # Create workers for each key
        for i in range(5):
            key_label = f"key-{i+1}"
            # Assign default agent types based on key index
            if i < 2:
                default_type = AgentType.STUDY
            elif i < 4:
                default_type = AgentType.CODING
            else:
                default_type = AgentType.CEO

            self._workers[key_label] = AgentWorker(key_label=key_label, agent_type=default_type)

    def get_worker(self, agent_type: AgentType) -> Optional[AgentWorker]:
        """Get a free worker suitable for the agent type."""
        with self._lock:
            # Prefer workers of matching type
            for w in self._workers.values():
                if not w.busy and (w.agent_type == agent_type or w.agent_type == AgentType.CEO):
                    return w
            # Fallback: any free worker
            for w in self._workers.values():
                if not w.busy:
                    return w
            return None

    def release_worker(self, key_label: str):
        with self._lock:
            if key_label in self._workers:
                self._workers[key_label].mark_free()

    def get_client_for_worker(self, worker: AgentWorker):
        """Get or create a GenAI client for the worker's key."""
        if worker.client is None:
            # Find the key state
            for state in self.km._states:
                if state.label == worker.key_label:
                    worker.client = self.km._client_for(state)
                    break
        return worker.client

    def get_all_status(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "label": w.key_label,
                    "type": w.agent_type.value,
                    "busy": w.busy,
                    "task": w.current_task_id,
                }
                for w in self._workers.values()
            ]


# ─── Specialist Agent Runners ───────────────────────────────────────────────


def run_study_task(worker: AgentWorker, task: Task, km: OrchestratorKeyManager,
                   progress_cb: Optional[Callable[[str, str], None]] = None) -> Any:
    """Run a Study Agent task (search-grounded QA or tool-calling)."""
    if progress_cb:
        progress_cb("🎓 Study Agent", f"Starting: {task.prompt[:60]}...")

    if StudyAgent is None:
        raise RuntimeError("StudyAgent not available")

    # StudyAgent uses SEARCH_POOL (keys 1-2) or TOOL_POOL (keys 3-5)
    # We route based on worker's key
    key_num = int(worker.key_label.split("-")[1])
    if key_num <= 2:
        pool = RoleKeyManager.SEARCH_POOL
    else:
        pool = RoleKeyManager.TOOL_POOL

    # Create a study agent with a wrapper that uses our specific worker
    agent = StudyAgent(km.km)

    material = task.payload.get("material", "")
    history = task.payload.get("history", [])

    if progress_cb:
        progress_cb("🎓 Study Agent", f"Processing with {worker.key_label} ({pool})...")

    result = agent.chat(material, history, task.prompt)

    if progress_cb:
        progress_cb("🎓 Study Agent", f"Done. Keys used: {result.get('keys_used', [])}")

    return result


def run_personal_growth_task(worker: AgentWorker, task: Task, km: OrchestratorKeyManager,
                             progress_cb: Optional[Callable[[str, str], None]] = None) -> Any:
    """Run the student-focused Personal Growth Mentor with scoped goal tools."""
    if progress_cb:
        progress_cb("🌱 Personal Growth Mentor", f"Starting: {task.prompt[:60]}...")
    if PersonalGrowthMentor is None:
        raise RuntimeError("Personal Growth Mentor not available")

    agent = PersonalGrowthMentor(
        km.km,
        scope_id=task.payload.get("scope_id") or task.id,
    )
    result = agent.chat(task.payload.get("history", []), task.prompt)
    if progress_cb:
        progress_cb("🌱 Personal Growth Mentor", f"Done. Goal tools used: {len(result.get('tool_calls', []))}")
    return result


def run_coding_task(worker: AgentWorker, task: Task, km: OrchestratorKeyManager,
                    progress_cb: Optional[Callable[[str, str], None]] = None) -> Any:
    """Run a Coding Agent task (5-role pipeline)."""
    if progress_cb:
        progress_cb("💻 Coding Agent", f"Starting: {task.prompt[:60]}...")

    if CodingAgent is None or Workspace is None:
        raise RuntimeError("CodingAgent not available")

    workspace_dir = task.payload.get("workspace", str(SHARED_WORKSPACE / "coding"))
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)

    agent = CodingAgent(km.km, workspace_dir)

    def on_step(role, detail):
        if progress_cb:
            progress_cb(f"💻 {role}", detail[:120])

    result = agent.run(task.prompt, on_step=on_step)

    if progress_cb:
        progress_cb("💻 Coding Agent", f"Done. Files changed: {len(result.get('changes', []))}")

    return result


def run_blender_task(worker: AgentWorker, task: Task, km: OrchestratorKeyManager,
                     progress_cb: Optional[Callable[[str, str], None]] = None) -> Any:
    """Run a Blender 3D generation task via BlenderMCP."""
    if progress_cb:
        progress_cb("🧊 Blender Agent", f"Starting: {task.prompt[:60]}...")

    if BlenderSocket is None or call_ai is None:
        raise RuntimeError("Blender Agent not available")

    socket = BlenderSocket()
    try:
        # Gather scene context
        if progress_cb:
            progress_cb("🧊 Blender Agent", "Getting scene info...")
        scene_info = socket.get_scene_info()
        current_script = ""  # Could load from workspace

        if progress_cb:
            progress_cb("🧊 Blender Agent", "Generating Blender Python code...")

        result = call_ai(
            user_prompt=task.prompt,
            image_path=task.payload.get("image_path"),
            scene_info=scene_info,
            current_script=current_script,
        )

        code, explanation = extract_code_and_explanation(result.text)
        if not code:
            raise RuntimeError("No Python code block returned by AI")

        if progress_cb:
            progress_cb("🧊 Blender Agent", "Executing in Blender...")

        exec_r = socket.execute(code)

        if not exec_r.success:
            raise RuntimeError(f"Blender execution failed: {exec_r.message}")

        if progress_cb:
            progress_cb("🧊 Blender Agent", "Capturing screenshot...")
        screenshot = socket.get_viewport_screenshot()

        return {
            "code": code,
            "explanation": explanation,
            "stdout": exec_r.stdout,
            "screenshot": str(screenshot) if screenshot else None,
            "engine": result.engine,
            "trail": result.trail,
        }
    finally:
        socket.close()


def run_ppt_task(worker: AgentWorker, task: Task, km: OrchestratorKeyManager,
                 progress_cb: Optional[Callable[[str, str], None]] = None) -> Any:
    """Run a PPT Controller task (voice/vision control)."""
    if progress_cb:
        progress_cb("🎤 PPT Agent", f"Starting: {task.prompt[:60]}...")

    # This would integrate with pptcontroller.py
    # For now, return a placeholder
    return {"status": "PPT Agent not yet integrated in orchestrator", "prompt": task.prompt}


def run_tool_task(worker: AgentWorker, task: Task, km: OrchestratorKeyManager,
                  progress_cb: Optional[Callable[[str, str], None]] = None) -> Any:
    """Run a generic tool task (web search, file ops, etc.)."""
    if progress_cb:
        progress_cb("🔧 Tool Agent", f"Starting: {task.prompt[:60]}...")

    # Use the worker's key for a direct Gemini call
    client = km.get_client_for_worker(worker)
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction="You are a helpful tool agent. Execute the requested task and return structured results.",
        temperature=0.3,
    )
    contents = [types.Content(role="user", parts=[types.Part(text=task.prompt)])]

    response = client.models.generate_content(
        model=MODEL_STUDY_TOOLS,
        contents=contents,
        config=config,
    )

    text = "".join(p.text or "" for p in response.candidates[0].content.parts)

    if progress_cb:
        progress_cb("🔧 Tool Agent", "Done.")

    return {"reply": text, "model": MODEL_STUDY_TOOLS}


def run_auditor_task(worker: AgentWorker, task: Task, km: OrchestratorKeyManager,
                     progress_cb: Optional[Callable[[str, str], None]] = None) -> Any:
    """Run an audit/logging task."""
    if progress_cb:
        progress_cb("📊 Auditor", f"Starting: {task.prompt[:60]}...")

    log_path = Path(__file__).resolve().parent / "agent_logs.json"
    if not log_path.exists():
        return {"logs": [], "message": "No logs found"}

    with open(log_path, "r", encoding="utf-8") as f:
        logs = json.load(f)

    # Filter/summarize based on task.payload
    filter_agent = task.payload.get("agent")
    filter_key = task.payload.get("key")
    limit = task.payload.get("limit", 50)

    filtered = logs
    if filter_agent:
        filtered = [l for l in filtered if l.get("agent") == filter_agent]
    if filter_key:
        filtered = [l for l in filtered if l.get("key_label") == filter_key]

    filtered = filtered[-limit:]

    if progress_cb:
        progress_cb("📊 Auditor", f"Returned {len(filtered)} log entries.")

    return {"logs": filtered, "total": len(logs), "filtered": len(filtered)}


# ─── CEO Planner (decomposes high-level requests) ───────────────────────────


def ceo_plan(request: str, km: OrchestratorKeyManager,
             progress_cb: Optional[Callable[[str, str], None]] = None) -> list[Task]:
    """CEO uses a dedicated key to plan and decompose the request into subtasks."""
    if progress_cb:
        progress_cb("👔 CEO", "Planning task decomposition...")

    # Get CEO worker (key 5)
    ceo_worker = None
    for w in km._workers.values():
        if w.agent_type == AgentType.CEO and not w.busy:
            ceo_worker = w
            break
    if not ceo_worker:
        # Fallback: use any free worker
        ceo_worker = next((w for w in km._workers.values() if not w.busy), None)
    if not ceo_worker:
        raise RuntimeError("No free worker for CEO planning")

    client = km.get_client_for_worker(ceo_worker)
    from google.genai import types

    system = """You are the CEO of a multi-agent AI system. Decompose the user's request into independent subtasks 
that can be executed in parallel by specialist agents. 

Available agent types:
- study: Search-grounded QA, quiz generation, flashcards, summaries, study plans
- personal_growth: Student goals, learning habits, IT skill exploration, and progress check-ins
- coding: Full software development (Thinker→Designer→Coder→Checker→CEO pipeline)
- blender: 3D modeling, animation, scene creation in Blender via BlenderMCP
- ppt: PowerPoint control via voice/vision
- tool: Generic tool use (web search, file ops, calculations)
- auditor: Log analysis, API call inspection

Reply with ONLY a JSON array of subtasks, no prose:
[
  {"type": "study|personal_growth|coding|blender|ppt|tool|auditor", "prompt": "specific task for this agent", 
   "payload": {"key": "value"}, "priority": "low|normal|high|critical", "depends_on": []}
]

Rules:
- Make subtasks as independent as possible
- Use depends_on only when strictly necessary
- Assign appropriate priorities
- Keep prompts specific and actionable"""

    config = types.GenerateContentConfig(system_instruction=system, temperature=0.2)
    contents = [types.Content(role="user", parts=[types.Part(text=f"USER REQUEST:\n{request}")])]

    response = client.models.generate_content(
        model=MODEL_CODING,
        contents=contents,
        config=config,
    )

    text = "".join(p.text or "" for p in response.candidates[0].content.parts).strip()
    text = text.strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()

    try:
        subtasks_data = json.loads(text)
        if not isinstance(subtasks_data, list):
            raise ValueError("Not a list")
    except Exception:
        # Fallback: single task
        subtasks_data = [{"type": "tool", "prompt": request, "payload": {}, "priority": "normal"}]

    tasks = []
    for i, st in enumerate(subtasks_data):
        if not isinstance(st, dict):
            continue
        try:
            task_type = AgentType(st.get("type", "tool"))
        except (ValueError, TypeError):
            task_type = AgentType.TOOL
        try:
            priority = TaskPriority[str(st.get("priority", "normal")).upper()]
        except KeyError:
            priority = TaskPriority.NORMAL
        payload = st.get("payload", {})
        dependencies = st.get("depends_on", [])
        t = Task(
            type=task_type,
            prompt=st.get("prompt", request),
            payload=payload if isinstance(payload, dict) else {},
            priority=priority,
            depends_on=[str(dep) for dep in dependencies] if isinstance(dependencies, list) else [],
        )
        tasks.append(t)
    if not tasks:
        tasks = [Task(type=AgentType.TOOL, prompt=request)]

    if progress_cb:
        progress_cb("👔 CEO", f"Planned {len(tasks)} subtasks")

    return tasks


# ─── Main Orchestrator Class ────────────────────────────────────────────────


class AgentOrchestrator:
    """Main orchestrator that manages the multi-agent system."""

    def __init__(self, use_mock: bool = False, max_workers: int = 5):
        self.km = OrchestratorKeyManager(use_mock=use_mock)
        self.queue = TaskQueue()
        self.max_workers = max_workers
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._running = True
        self._dispatcher_thread: Optional[threading.Thread] = None
        self._progress_callback: Optional[Callable[[str, str], None]] = None
        self._task_futures: dict[str, concurrent.futures.Future] = {}

        # Agent runner mapping
        self._runners = {
            AgentType.STUDY: run_study_task,
            AgentType.PERSONAL_GROWTH: run_personal_growth_task,
            AgentType.CODING: run_coding_task,
            AgentType.BLENDER: run_blender_task,
            AgentType.PPT: run_ppt_task,
            AgentType.TOOL: run_tool_task,
            AgentType.AUDITOR: run_auditor_task,
        }

    def set_progress_callback(self, cb: Callable[[str, str], None]):
        self._progress_callback = cb

    def start(self):
        """Start the dispatcher loop."""
        if self._dispatcher_thread and self._dispatcher_thread.is_alive():
            return
        self._running = True
        self._dispatcher_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher_thread.start()

    def stop(self, timeout: float = 30):
        """Stop the orchestrator."""
        self._running = False
        if self._dispatcher_thread:
            self._dispatcher_thread.join(timeout=timeout)
        self._executor.shutdown(wait=True, cancel_futures=True)

    def submit(self, request: str, payload: dict = None, auto_plan: bool = True,
               agent_type: AgentType = AgentType.TOOL) -> str:
        """
        Submit a high-level request. If auto_plan=True, CEO decomposes it.
        Returns the root task ID (or single task ID if no planning).
        """
        if auto_plan:
            # CEO plans the decomposition
            root_task = Task(type=AgentType.CEO, prompt=request, payload=payload or {}, priority=TaskPriority.HIGH)
            root_id = self.queue.add(root_task)
            # The dispatcher will handle CEO planning and spawn subtasks
            return root_id
        else:
            # Direct single task; callers can choose a specialist explicitly.
            task = Task(type=agent_type, prompt=request, payload=payload or {})
            return self.queue.add(task)

    def wait_for(self, task_id: str, timeout: float = 300) -> Optional[Task]:
        return self.queue.wait_for(task_id, timeout)

    def get_status(self, task_id: str) -> Optional[Task]:
        return self.queue.get_status(task_id)

    def get_all_tasks(self) -> list[Task]:
        with self.queue._lock:
            return list(self.queue._pending.values()) + list(self.queue._running.values()) + list(self.queue._completed.values())

    def get_worker_status(self) -> list[dict]:
        return self.km.get_all_status()

    def _dispatch_loop(self):
        """Main dispatcher: pulls ready tasks, assigns workers, runs them."""
        while self._running:
            try:
                task = self.queue.get_next_ready()
                if not task:
                    time.sleep(0.1)
                    continue

                # Special handling for CEO tasks
                if task.type == AgentType.CEO:
                    self._handle_ceo_task(task)
                    continue

                # Get a suitable worker
                worker = self.km.get_worker(task.type)
                if not worker:
                    # No free worker — re-queue and wait
                    time.sleep(0.5)
                    with self.queue._lock:
                        self.queue._pending[task.id] = task
                        self.queue._queue.put((-task.priority.value, time.time(), task.id))
                    continue

                worker.mark_busy(task.id)
                task.assigned_key = worker.key_label

                # Submit to executor
                future = self._executor.submit(self._run_task, worker, task)
                self._task_futures[task.id] = future

            except Exception as e:
                if self._progress_callback:
                    self._progress_callback("⚠️ Orchestrator", f"Dispatch error: {e}")
                time.sleep(1)

    def _handle_ceo_task(self, task: Task):
        """CEO task: plan and spawn subtasks."""
        try:
            subtasks = ceo_plan(task.prompt, self.km, self._progress_callback)
            # Add subtasks with dependency on CEO task
            for st in subtasks:
                st.depends_on = [task.id] + st.depends_on
                st.parent_id = task.id
                self.queue.add(st)
            self.queue.complete(task.id, result={"subtasks": [s.id for s in subtasks]})
        except Exception as e:
            self.queue.complete(task.id, error=f"CEO planning failed: {e}")

    def _run_task(self, worker: AgentWorker, task: Task):
        """Execute a single task with the assigned worker."""
        runner = self._runners.get(task.type)
        if not runner:
            self.queue.complete(task.id, error=f"No runner for agent type {task.type.value}")
            self.km.release_worker(worker.key_label)
            return

        try:
            result = runner(worker, task, self.km, self._progress_callback)
            self.queue.complete(task.id, result=result)
        except Exception as e:
            err = str(e)
            if task.retries < task.max_retries:
                if self._progress_callback:
                    self._progress_callback(f"🔄 Retry {task.retries+1}/{task.max_retries}", f"{task.type.value}: {err[:80]}")
                # Return the task to the queue and let dispatch lease a fresh
                # worker; recursively reusing this worker races with finally.
                time.sleep(min(2 ** task.retries, 4))
                self.queue.retry(task.id)
            else:
                self.queue.complete(task.id, error=err)
        finally:
            self.km.release_worker(worker.key_label)
            self._task_futures.pop(task.id, None)


# ─── Convenience Functions for Direct Use ───────────────────────────────────


def quick_study(question: str, material: str = "", use_mock: bool = False) -> dict:
    """One-off study question without full orchestrator."""
    km = OrchestratorKeyManager(use_mock=use_mock)
    worker = km.get_worker(AgentType.STUDY)
    if not worker:
        return {"error": "No free study worker"}
    worker.mark_busy("quick_study")
    try:
        task = Task(type=AgentType.STUDY, prompt=question, payload={"material": material, "history": []})
        return run_study_task(worker, task, km)
    finally:
        km.release_worker(worker.key_label)


def quick_personal_growth(prompt: str, scope_id: str = "quick-session",
                          use_mock: bool = False) -> dict:
    """One-off student coaching request without starting the dispatcher."""
    km = OrchestratorKeyManager(use_mock=use_mock)
    worker = km.get_worker(AgentType.PERSONAL_GROWTH)
    if not worker:
        return {"error": "No free Personal Growth Mentor worker"}
    worker.mark_busy("quick_personal_growth")
    try:
        task = Task(type=AgentType.PERSONAL_GROWTH, prompt=prompt,
                    payload={"scope_id": scope_id})
        return run_personal_growth_task(worker, task, km)
    finally:
        km.release_worker(worker.key_label)


def quick_code(request: str, workspace: str = "", use_mock: bool = False) -> dict:
    """One-off coding task without full orchestrator."""
    km = OrchestratorKeyManager(use_mock=use_mock)
    worker = km.get_worker(AgentType.CODING)
    if not worker:
        return {"error": "No free coding worker"}
    worker.mark_busy("quick_code")
    try:
        task = Task(type=AgentType.CODING, prompt=request, payload={"workspace": workspace or str(SHARED_WORKSPACE / "coding")})
        return run_coding_task(worker, task, km)
    finally:
        km.release_worker(worker.key_label)


def quick_blender(prompt: str, image_path: str = "", use_mock: bool = False) -> dict:
    """One-off Blender generation without full orchestrator."""
    km = OrchestratorKeyManager(use_mock=use_mock)
    worker = km.get_worker(AgentType.BLENDER)
    if not worker:
        return {"error": "No free blender worker"}
    worker.mark_busy("quick_blender")
    try:
        task = Task(type=AgentType.BLENDER, prompt=prompt, payload={"image_path": image_path} if image_path else {})
        return run_blender_task(worker, task, km)
    finally:
        km.release_worker(worker.key_label)


# ─── Demo / Test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("🤖 Agent Orchestrator Demo")
    print("=" * 50)

    # Demo mode uses mock keys
    orch = AgentOrchestrator(use_mock=True, max_workers=3)
    orch.set_progress_callback(lambda role, detail: print(f"  [{role}] {detail}"))

    orch.start()

    # Submit a complex request that needs multiple agents
    request = "Create a study guide for Python async programming with a quiz, then write a Python script that demonstrates asyncio patterns, and finally create a 3D visualization of an async event loop in Blender"
    task_id = orch.submit(request, auto_plan=True)

    print(f"\n📋 Submitted task: {task_id}")
    print("Waiting for completion...")

    # Wait for root task
    result = orch.wait_for(task_id, timeout=120)
    if result:
        print(f"\n✅ Root task completed: {result.status.value}")
        if result.result:
            print(f"   Result: {result.result}")

    # Check all subtasks
    all_tasks = orch.get_all_tasks()
    print(f"\n📊 Total tasks: {len(all_tasks)}")
    for t in all_tasks:
        print(f"  {t.id[:8]} | {t.type.value:8} | {t.status.value:10} | {t.prompt[:50]}")

    orch.stop()
    print("\n🛑 Orchestrator stopped")
