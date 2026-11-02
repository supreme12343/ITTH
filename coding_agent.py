"""
agent_core/coding_agent.py

Coding Agent — 5 dedicated Gemini keys working as a pipeline:

    🧠 Thinker  (key 1) — breaks the request into a short plan.
    🎨 Designer (key 2) — turns the plan into a concrete technical approach.
    💻 Coding Swarm (ALL 5 keys) — the Designer's approach gets split into
                           subtasks, and ALL FIVE keys write code in
                           parallel (one subtask per key), each with real
                           file tools scoped to the workspace. Every
                           worker's diff also gets an immediate peer
                           review from a *different* key before it's
                           considered done — a fast ensemble check that
                           catches more than one flash-lite call alone
                           would, and spreads quota use across all 5
                           accounts so one key hitting its limit doesn't
                           strand the whole build.
    🔍 Checker  (key 4) — reviews the merged diff, in ~75-line windows,
                           against the original ask. One full fix pass
                           (re-run through the swarm) if it finds problems.
    👔 CEO      (key 5) — writes the final report the student sees.
"""

import concurrent.futures
import difflib
import json
import logging
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from google.genai import types

from key_manager import AiApi, RoleKeyManager

logger = logging.getLogger("coding_agent")

MODEL = "gemini-3.1-flash-lite"
CHUNK_LINES = 75
SWARM_WORKERS = 5
OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
OMNIROUTE_MODEL = os.getenv("OMNIROUTE_MODEL", "auto")

MAX_TOOL_LOOP_STEPS = 10
MAX_RETRIES = 3
BACKOFF_BASE = 1.0
BACKOFF_CAP = 30.0
MAX_CONTEXT_TOKENS_EST = 80000

ROLE_THINKER, ROLE_DESIGNER, ROLE_CODER, ROLE_CHECKER, ROLE_CEO = 0, 1, 2, 3, 4
ROLE_NAMES = {0: "🧠 Thinker", 1: "🎨 Designer", 2: "💻 Coder", 3: "🔍 Checker", 4: "👔 CEO"}


def _backoff_delay(attempt: int, base: float = BACKOFF_BASE, cap: float = BACKOFF_CAP) -> float:
    delay = min(base * (2 ** attempt), cap)
    jitter = random.uniform(0, delay * 0.3)
    return delay + jitter


def _is_transient_error(exc: Exception) -> bool:
    s = str(exc)
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    return any(k in s for k in ("429", "503", "RESOURCE_EXHAUSTED", "rate", "overloaded", "timeout"))


def _estimate_tokens(text: str) -> int:
    return max(len(text) // 4, 1)


def _estimate_history_tokens(contents: list) -> int:
    """Estimate text and structured tool payloads without building a large copy."""
    total_chars = 0
    for content in contents:
        for part in getattr(content, "parts", []):
            total_chars += len(getattr(part, "text", "") or "")
            function_call = getattr(part, "function_call", None)
            if function_call:
                total_chars += len(json.dumps(dict(getattr(function_call, "args", {}) or {}), default=str))
            function_response = getattr(part, "function_response", None)
            if function_response:
                total_chars += len(json.dumps(
                    dict(getattr(function_response, "response", {}) or {}), default=str
                ))
    return max(total_chars // 4, 1)


def _summarize_history(contents: list, keep_last: int = 3) -> list:
    if len(contents) <= keep_last + 1:
        return contents
    system_part = contents[0] if contents else None
    kept = contents[-keep_last:]
    older = contents[1:-keep_last] if system_part else contents[:-keep_last]
    summary_text = ""
    for c in older:
        for p in getattr(c, "parts", []):
            t = getattr(p, "text", None)
            if t:
                summary_text += t[:200] + "... "
    if summary_text:
        summary_content = types.Content(
            role="user",
            parts=[types.Part(text=f"[Earlier context summarized: {summary_text[:600]}]")]
        )
        result = [system_part] if system_part else []
        result.append(summary_content)
        result.extend(kept)
        return result
    return contents


class Workspace:
    """Sandboxes the Coder's file tools to one project folder."""

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, rel_path: str) -> Path:
        p = (self.root / rel_path).resolve()
        if self.root != p and self.root not in p.parents:
            raise ValueError("Path escapes the project workspace.")
        return p

    def list_files(self):
        return [str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()]

    def read_file(self, rel_path: str, start_line: int = 1, max_lines: int = 800) -> dict:
        """Read a bounded line range so large files do not exhaust the model context."""
        if start_line < 1 or max_lines < 1:
            raise ValueError("start_line and max_lines must be positive integers")
        max_lines = min(max_lines, 2000)
        lines = self._safe(rel_path).read_text(encoding="utf-8").splitlines()
        start = min(start_line - 1, len(lines))
        end = min(start + max_lines, len(lines))
        return {
            "content": "\n".join(lines[start:end]),
            "start_line": start + 1,
            "end_line": end,
            "total_lines": len(lines),
            "truncated": start > 0 or end < len(lines),
        }

    def write_file(self, rel_path: str, content: str):
        p = self._safe(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        before = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(content, encoding="utf-8")
        return before, content

    def delete_file(self, rel_path: str):
        path = self._safe(rel_path)
        before = path.read_text(encoding="utf-8") if path.is_file() else None
        path.unlink(missing_ok=True)
        return before


def _coding_instruction_tool(tool_name: str, **kwargs):
    labels = {
        "review_code": "Code Review",
        "generate_tests": "Test Generation",
        "suggest_refactor": "Refactoring Suggestions",
        "explain_code": "Code Explanation",
        "find_bugs": "Bug Hunt",
        "generate_docstring": "Docstring Draft",
        "plan_patch": "Patch Plan",
    }
    details = "\n".join(f"{k}: {v}" for k, v in kwargs.items() if v)
    return {
        "instruction_for_model": (
            f"Run this helper mode: {labels.get(tool_name, tool_name)}.\n{details}\n\n"
            f"Use any provided code or previously read file content. Return a concise, practical result. "
            f"If this helper suggests edits, do not claim the file changed unless you also call write_file."
        )
    }


def _omniroute_chat(prompt: str, system: str = "", model: str = ""):
    api_key = os.getenv("OMNIROUTE_API_KEY")
    if not api_key:
        return {
            "error": (
                "OMNIROUTE_API_KEY is not set. Set it in your environment before running "
                "the Coding Agent. No API key is hardcoded in this project."
            )
        }

    payload = {
        "model": model or OMNIROUTE_MODEL,
        "messages": [
            {"role": "system", "content": system or "You are a careful coding assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OMNIROUTE_BASE_URL}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"content": content, "model": payload["model"], "base_url": OMNIROUTE_BASE_URL}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return {"error": f"OmniRoute HTTP {e.code}: {detail}"}
    except Exception as e:
        return {"error": f"OmniRoute request failed: {e}"}


CODER_TOOL = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="list_files", description="List all files currently in the project workspace.",
        parameters=types.Schema(type="OBJECT", properties={})),
    types.FunctionDeclaration(
        name="read_file", description="Read a workspace file or a line range. Defaults to the first 800 lines; use start_line and max_lines to inspect further sections.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "start_line": types.Schema(type="INTEGER"),
                                             "max_lines": types.Schema(type="INTEGER")}, required=["path"])),
    types.FunctionDeclaration(
        name="write_file", description="Create or overwrite a file in the workspace with new content.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "content": types.Schema(type="STRING")},
                                 required=["path", "content"])),
    types.FunctionDeclaration(
        name="delete_file", description="Delete a file from the workspace.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING")}, required=["path"])),
    types.FunctionDeclaration(
        name="review_code", description="Review a workspace file or snippet for correctness, maintainability, readability, and security issues.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "code": types.Schema(type="STRING"),
                                             "focus": types.Schema(type="STRING")},
                                 required=[])),
    types.FunctionDeclaration(
        name="generate_tests", description="Generate test cases or a test file draft for a target file/function.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "function_name": types.Schema(type="STRING"),
                                             "framework": types.Schema(type="STRING")},
                                 required=[])),
    types.FunctionDeclaration(
        name="suggest_refactor", description="Suggest behavior-preserving refactors for a file or snippet.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "code": types.Schema(type="STRING"),
                                             "goal": types.Schema(type="STRING")},
                                 required=[])),
    types.FunctionDeclaration(
        name="explain_code", description="Explain what a file or snippet does in student-friendly language.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "code": types.Schema(type="STRING"),
                                             "detail_level": types.Schema(type="STRING")},
                                 required=[])),
    types.FunctionDeclaration(
        name="find_bugs", description="Scan a file or snippet for likely bugs and edge cases.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "code": types.Schema(type="STRING"),
                                             "scenario": types.Schema(type="STRING")},
                                 required=[])),
    types.FunctionDeclaration(
        name="generate_docstring", description="Create a docstring or concise comments matching the existing code style.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "function_name": types.Schema(type="STRING"),
                                             "code": types.Schema(type="STRING")},
                                 required=[])),
    types.FunctionDeclaration(
        name="plan_patch", description="Create a focused patch plan before editing a target file.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"path": types.Schema(type="STRING"),
                                             "request": types.Schema(type="STRING"),
                                             "constraints": types.Schema(type="STRING")},
                                 required=["request"])),
    types.FunctionDeclaration(
        name="omniroute_chat", description="Ask OmniRoute for extra coding analysis. Uses OMNIROUTE_API_KEY from the environment.",
        parameters=types.Schema(type="OBJECT",
                                 properties={"prompt": types.Schema(type="STRING"),
                                             "system": types.Schema(type="STRING"),
                                             "model": types.Schema(type="STRING")},
                                 required=["prompt"])),
])


class CodingAgent:
    def __init__(self, key_manager, workspace_dir: str):
        # Accept either RoleKeyManager or AiApi
        if hasattr(key_manager, '_km'):
            self.km = key_manager._km
        else:
            self.km = key_manager
        self.ws = Workspace(workspace_dir)
        self.changes = {}  # path -> (before, after), merged across all swarm workers
        self._ws_lock = threading.Lock()  # workers run in parallel threads, file IO must be serialized

    def _call(self, role_index, system_instruction, user_text, tools=None):
        client, label = self.km.client_for_role(role_index)
        config = types.GenerateContentConfig(system_instruction=system_instruction,
                                              temperature=0.3, tools=tools)
        contents = [types.Content(role="user", parts=[types.Part(text=user_text)])]
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = client.models.generate_content(model=MODEL, contents=contents, config=config)
                label = getattr(client, "last_label", label)
                if not response.candidates:
                    raise RuntimeError("Empty response from model (no candidates)")
                return response, label
            except Exception as e:
                last_error = e
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning("Transient error on %s (attempt %d/%d), retrying in %.1fs: %s",
                                   ROLE_NAMES.get(role_index, "?"), attempt + 1, MAX_RETRIES, delay, e)
                    time.sleep(delay)
                    continue
                raise
        raise last_error

    # ---- Thinker ------------------------------------------------------------
    def _think(self, request: str) -> str:
        existing_files = self.ws.list_files()
        context = f"Existing project files: {existing_files[:30]}" if existing_files else "Empty project directory."
        resp, _ = self._call(
            ROLE_THINKER,
            "You are the Thinker. Break the coding request into a short, numbered plan "
            "(3-6 steps). Consider existing files in the project. No code yet — just the plan.",
            f"PROJECT STATE:\n{context}\n\nREQUEST:\n{request}")
        plan = "".join(p.text or "" for p in resp.candidates[0].content.parts)
        reflect_resp, _ = self._call(
            ROLE_THINKER,
            "You are the Thinker doing a self-check. Review this plan for completeness, "
            "potential issues, and missing edge cases. Output the FINAL revised plan only. "
            "If the plan is already good, return it unchanged.",
            f"ORIGINAL REQUEST:\n{request}\n\nDRAFT PLAN:\n{plan}")
        return "".join(p.text or "" for p in reflect_resp.candidates[0].content.parts)

    # ---- Designer -------------------------------------------------------------
    def _design(self, request: str, plan: str) -> str:
        resp, _ = self._call(
            ROLE_DESIGNER,
            "You are the Designer. Given the request and the Thinker's plan, decide the "
            "concrete technical approach: which files to touch, what functions/structures "
            "to add or change, and any tradeoffs. Be specific enough a coder needs no guessing.",
            f"REQUEST:\n{request}\n\nPLAN:\n{plan}")
        return "".join(p.text or "" for p in resp.candidates[0].content.parts)

    # ---- Coding Swarm: task splitter --------------------------------------------
    SUBTASK_SYSTEM = (
        "You are a task splitter for a coding swarm. Given a technical approach, "
        "break it into independent-as-possible subtasks so multiple workers can "
        "tackle them in parallel without stepping on each other (usually one "
        "subtask per file, or per clearly separable piece of work). Reply with "
        "ONLY a JSON array, no prose, no markdown fences, like: "
        '[{"file": "path/to/file.py", "task": "what to do in this file"}, ...]. '
        "3-6 subtasks is typical. If the work genuinely can't be split, return a "
        "single-item array covering the whole thing."
    )

    def _split_into_subtasks(self, request: str, plan: str, design: str) -> list:
        prompt = f"REQUEST:\n{request}\n\nPLAN:\n{plan}\n\nAPPROACH:\n{design}"
        config = types.GenerateContentConfig(system_instruction=self.SUBTASK_SYSTEM, temperature=0.2)
        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        response, _ = self.km.generate(RoleKeyManager.ALL_POOL, model=MODEL, contents=contents, config=config)
        if not getattr(response, "candidates", None):
            return [{"file": "(whole project)", "task": design or request}]
        text = "".join(p.text or "" for p in response.candidates[0].content.parts).strip()
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        try:
            subtasks = json.loads(text)
            if isinstance(subtasks, list) and subtasks:
                # A splitter can return malformed entries, an excessive number of
                # workers, or multiple workers assigned to the same file. Normalize
                # this before launching parallel writers into the shared workspace.
                normalized = []
                by_file = {}
                for item in subtasks:
                    if not isinstance(item, dict):
                        continue
                    file_name = item.get("file")
                    task = item.get("task")
                    if not isinstance(file_name, str) or not isinstance(task, str):
                        continue
                    file_name, task = file_name.strip(), task.strip()
                    if not file_name or not task:
                        continue
                    key = os.path.normcase(os.path.normpath(file_name))
                    if key in by_file:
                        existing = by_file[key]
                        existing["task"] += f"\nAlso: {task}"
                    else:
                        entry = {"file": file_name[:500], "task": task[:4000]}
                        by_file[key] = entry
                        normalized.append(entry)
                    if len(normalized) >= SWARM_WORKERS:
                        break
                if normalized:
                    return normalized
        except Exception:
            pass
        return [{"file": "(whole project)", "task": design}]

    # ---- Coding Swarm: one worker's tool loop for one subtask ---------------------
    CODER_SYSTEM = (
        "You are one of several coder workers in a swarm, all working on the same "
        "project at once. You have file tools (list_files, read_file, write_file, "
        "delete_file) scoped to the project workspace, plus helper tools for review, "
        "test generation, refactoring suggestions, explanations, bug finding, docstrings, "
        "patch planning, and optional OmniRoute analysis. Before writing to a file that "
        "might already exist, READ it first so you don't clobber another worker's "
        "changes or break the existing style/conventions. Use helper tools when they "
        "make your edit safer or clearer, but remember they are advisory; only write_file "
        "or delete_file changes disk. Think through edge cases briefly before writing, "
        "then make the actual tool calls — don't just describe changes in prose. "
        "read_file returns at most 800 lines by default and includes a truncated flag; "
        "when truncated, request additional ranges with start_line and max_lines before "
        "making edits that depend on unseen code. Stay focused on YOUR subtask only; "
        "other workers are handling the rest of the project."
    )

    def _run_subtask_worker(self, key_index: int, request: str, design: str,
                             subtask: dict, fix_note: str = ""):
        client, label = self.km.client_for_role(key_index)
        system = self.CODER_SYSTEM
        if fix_note:
            system += f"\n\nA peer reviewer flagged a problem with this work — fix exactly this:\n{fix_note}"
        config = types.GenerateContentConfig(system_instruction=system, tools=[CODER_TOOL], temperature=0.2)
        contents = [types.Content(role="user", parts=[types.Part(
            text=f"REQUEST (whole project):\n{request}\n\nAPPROACH:\n{design}\n\n"
                 f"YOUR SUBTASK:\nFile: {subtask.get('file', '(n/a)')}\nTask: {subtask.get('task', '')}"
        )])]

        local_changes = {}
        loop_detection = {}
        completed = False
        for step in range(MAX_TOOL_LOOP_STEPS):
            # Include file contents returned through structured tool responses.
            if _estimate_history_tokens(contents) > MAX_CONTEXT_TOKENS_EST:
                contents = _summarize_history(contents, keep_last=4)

            last_error = None
            response = None
            for attempt in range(MAX_RETRIES):
                try:
                    response = client.models.generate_content(model=MODEL, contents=contents, config=config)
                    label = getattr(client, "last_label", label)
                    break
                except Exception as e:
                    last_error = e
                    if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                        time.sleep(_backoff_delay(attempt))
                        continue
                    raise
            if response is None:
                raise last_error
            if not getattr(response, "candidates", None):
                raise RuntimeError(f"Model returned no candidates for subtask {subtask.get('file', '?')}")
            candidate = response.candidates[0]
            parts = candidate.content.parts
            fcalls = [p.function_call for p in parts if getattr(p, "function_call", None)]
            if not fcalls:
                contents.append(candidate.content)
                completed = True
                break
            contents.append(candidate.content)
            resp_parts = []
            for fc in fcalls:
                call_sig = f"{fc.name}:{json.dumps(dict(fc.args) if fc.args else {}, sort_keys=True)}"
                loop_detection[call_sig] = loop_detection.get(call_sig, 0) + 1
                if loop_detection[call_sig] > 3:
                    resp_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={"error": "Loop detected: this exact call has been made 3+ times. Try a different approach."}
                    ))
                    continue

                args = dict(fc.args) if fc.args else {}
                try:
                    with self._ws_lock:
                        if fc.name == "list_files":
                            result = {"files": self.ws.list_files()}
                        elif fc.name == "read_file":
                            result = self.ws.read_file(
                                args["path"],
                                start_line=int(args.get("start_line", 1)),
                                max_lines=int(args.get("max_lines", 800)),
                            )
                        elif fc.name == "write_file":
                            before, after = self.ws.write_file(args["path"], args["content"])
                            original_before = local_changes.get(args["path"], (before, after))[0]
                            local_changes[args["path"]] = (original_before, after)
                            workspace_before = self.changes.get(args["path"], (original_before, after))[0]
                            self.changes[args["path"]] = (workspace_before, after)
                            result = {"status": "written", "path": args["path"]}
                        elif fc.name == "delete_file":
                            before = self.ws.delete_file(args["path"])
                            original_before = local_changes.get(args["path"], (before, None))[0]
                            local_changes[args["path"]] = (original_before, None)
                            workspace_before = self.changes.get(args["path"], (original_before, None))[0]
                            self.changes[args["path"]] = (workspace_before, None)
                            result = {"status": "deleted", "path": args["path"]}
                        elif fc.name in {
                            "review_code", "generate_tests", "suggest_refactor", "explain_code",
                            "find_bugs", "generate_docstring", "plan_patch"
                        }:
                            if args.get("path") and not args.get("code"):
                                try:
                                    args["code"] = self.ws.read_file(args["path"])["content"]
                                except Exception as read_error:
                                    args["read_error"] = str(read_error)
                            result = _coding_instruction_tool(fc.name, **args)
                        elif fc.name == "omniroute_chat":
                            result = _omniroute_chat(
                                prompt=args.get("prompt", ""),
                                system=args.get("system", ""),
                                model=args.get("model", ""),
                            )
                        else:
                            result = {"error": f"Unknown tool {fc.name}"}
                except Exception as e:
                    result = {"error": str(e)}
                resp_parts.append(types.Part.from_function_response(name=fc.name, response=result))
            contents.append(types.Content(role="user", parts=resp_parts))
        if not completed:
            message = (f"Subtask worker hit step cap ({MAX_TOOL_LOOP_STEPS}) without a final response: "
                       f"{subtask.get('file', '?')}")
            logger.warning(message)
            raise RuntimeError(message)
        return label, local_changes

    # ---- Coding Swarm: fast peer review by a DIFFERENT key --------------------------
    def _peer_review(self, key_index: int, subtask: dict, local_changes: dict) -> str:
        if not local_changes:
            return ""
        diff_chunks = []
        for path, (before, after) in local_changes.items():
            if after is None:
                continue
            diff_chunks.extend(self._diff_chunks(path, before, after))
        if not diff_chunks:
            return ""
        client, _ = self.km.client_for_role(key_index)
        system = (
            "You are a fast peer reviewer inside a coding swarm. Check this worker's diff "
            "against their assigned subtask ONLY (not the whole project). Reply 'OK' if it's "
            "correct, or 'ISSUE: ...' with one precise, short fix instruction."
        )
        prompt = f"SUBTASK:\n{subtask.get('task', '')}\n\nDIFF:\n" + "\n---\n".join(diff_chunks[:3])
        config = types.GenerateContentConfig(system_instruction=system, temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        resp = client.models.generate_content(model=MODEL, contents=contents, config=config)
        text = "".join(p.text or "" for p in resp.candidates[0].content.parts).strip()
        return text if text.upper().startswith("ISSUE") else ""

    # ---- Coding Swarm: orchestrates the parallel pass -------------------------------
    def _code(self, request: str, plan: str, design: str, fix_note: str = "") -> list:
        # Keep the first version from before the initial pass so checker/report
        # continue to see the complete diff after a repair pass.
        previous_changes = dict(self.changes) if fix_note else {}
        self.changes = dict(previous_changes)

        def record_changes(worker_changes):
            for path, (before, after) in worker_changes.items():
                original_before = previous_changes.get(path, (before, after))[0]
                self.changes[path] = (original_before, after)

        if fix_note:
            # A full-project fix pass — one subtask, still runs through the swarm helper
            # (key 0) so it's consistent, but no need to re-split what's already scoped.
            subtasks = [{"file": "(fix pass)", "task": fix_note}]
        else:
            subtasks = self._split_into_subtasks(request, plan, design)

        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(SWARM_WORKERS, len(subtasks))) as pool:
            futures = {}
            for i, subtask in enumerate(subtasks):
                key_idx = i % 5
                futures[pool.submit(self._run_subtask_worker, key_idx, request, design, subtask, fix_note)] = (i, subtask, key_idx)
            for fut in concurrent.futures.as_completed(futures):
                i, subtask, key_idx = futures[fut]
                label, local_changes = fut.result()
                results[i] = (subtask, key_idx, local_changes)
                record_changes(local_changes)

        # One immediate peer-review + self-fix round per subtask, BEFORE the deeper
        # Checker pass — a different key checks each worker's own diff.
        for i, (subtask, key_idx, local_changes) in results.items():
            reviewer_idx = (key_idx + 2) % 5
            note = self._peer_review(reviewer_idx, subtask, local_changes)
            if note:
                _, fixed_changes = self._run_subtask_worker(key_idx, request, design, subtask, fix_note=note)
                record_changes(fixed_changes)

        return list(self.changes.items())

    # ---- Checker (chunked diff review) -----------------------------------------
    def _diff_chunks(self, path, before, after):
        diff_lines = list(difflib.unified_diff(
            (before or "").splitlines(), (after or "").splitlines(),
            fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm=""))
        for i in range(0, len(diff_lines), CHUNK_LINES):
            yield "\n".join(diff_lines[i:i + CHUNK_LINES])

    def _check(self, request: str, changes: list) -> str:
        client, _ = self.km.client_for_role(ROLE_CHECKER)
        system = ("You are the Checker. You review real code diffs against the original "
                   "request. Reply with 'OK' if a chunk is correct and matches the ask. "
                   "Otherwise reply starting with 'ISSUE:' followed by a precise, actionable "
                   "description of what's wrong. Be terse — one or two sentences.")
        issues = []
        for path, (before, after) in changes:
            if after is None:
                continue
            for chunk in self._diff_chunks(path, before, after):
                if not chunk.strip():
                    continue
                config = types.GenerateContentConfig(system_instruction=system, temperature=0.1)
                contents = [types.Content(role="user", parts=[types.Part(
                    text=f"ORIGINAL REQUEST:\n{request}\n\nDIFF CHUNK ({path}):\n{chunk}")])]
                text = ""
                for attempt in range(MAX_RETRIES):
                    try:
                        resp = client.models.generate_content(model=MODEL, contents=contents, config=config)
                        if not getattr(resp, "candidates", None):
                            raise RuntimeError("Checker returned no candidates")
                        text = "".join(p.text or "" for p in resp.candidates[0].content.parts).strip()
                        break
                    except Exception as e:
                        if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                            time.sleep(_backoff_delay(attempt))
                            continue
                        logger.warning("Checker failed on %s after %d attempts: %s", path, attempt + 1, e)
                        raise RuntimeError(f"Checker could not review {path}: {e}") from e
                if text.upper().startswith("ISSUE"):
                    issues.append(f"[{path}] {text}")
                elif not text.upper().startswith("OK"):
                    raise RuntimeError(f"Checker gave an inconclusive response for {path}: {text or '(empty response)'}")
        return "\n".join(issues)

    # ---- CEO (final report) -------------------------------------------------------
    def _report(self, request: str, plan: str, design: str, changes: list, issues_found: str) -> str:
        client, _ = self.km.client_for_role(ROLE_CEO)
        files_summary = "\n".join(
            f"- {p}: {'deleted' if a is None else ('created' if not b else 'modified')}"
            for p, (b, a) in changes
        ) or "(no files changed)"
        system = ("You are the CEO. Write the final report the student sees: what was "
                   "built/changed and why, in clear student-friendly language — not internal "
                   "jargon about 'roles' or 'agents'. Clearly distinguish verified work from "
                   "work that could not be reviewed; never claim a review passed if it failed.")
        review_status = (
            f"REVIEW STATUS:\n{issues_found}" if issues_found.startswith("Review incomplete:")
            else (f"FIX PASS NEEDED; remaining review findings:\n{issues_found}" if issues_found
                  else "Review found no outstanding issues.")
        )
        user_text = (f"REQUEST:\n{request}\n\nPLAN:\n{plan}\n\nAPPROACH:\n{design}\n\n"
                     f"FILES CHANGED:\n{files_summary}\n\n"
                     f"{review_status}")
        config = types.GenerateContentConfig(system_instruction=system, temperature=0.4)
        contents = [types.Content(role="user", parts=[types.Part(text=user_text)])]
        resp = client.models.generate_content(model=MODEL, contents=contents, config=config)
        return "".join(p.text or "" for p in resp.candidates[0].content.parts)

    # ---- Public entrypoint: runs the full pipeline ---------------------------------
    def run(self, request: str, on_step=None) -> dict:
        def emit(role, detail=""):
            if on_step:
                on_step(role, detail)

        try:
            plan = self._think(request)
        except Exception as e:
            emit("⚠️ Thinker Error", str(e))
            return {"plan": "", "design": "", "changes": [], "issues": str(e), "report": f"Thinker failed: {e}"}
        emit(ROLE_NAMES[ROLE_THINKER], plan)

        try:
            design = self._design(request, plan)
        except Exception as e:
            emit("⚠️ Designer Error", str(e))
            return {"plan": plan, "design": "", "changes": [], "issues": str(e), "report": f"Designer failed: {e}"}
        emit(ROLE_NAMES[ROLE_DESIGNER], design)

        try:
            changes = self._code(request, plan, design)
        except Exception as e:
            emit("⚠️ Coder Error", str(e))
            partial_changes = list(self.changes.items())
            return {"plan": plan, "design": design, "changes": partial_changes,
                    "issues": str(e), "report": f"Coding stopped before completion: {e}. "
                    f"{len(partial_changes)} file(s) may have been changed."}
        emit("💻 Coding Swarm (all 5 keys)", f"{len(changes)} file(s) touched across parallel workers")

        max_fix_passes = 2
        issues = ""
        for fix_pass in range(max_fix_passes):
            try:
                issues = self._check(request, changes)
            except Exception as e:
                issues = f"Review incomplete: {e}"
                emit("⚠️ Checker Error", str(e))
                break
            pass_label = f" (pass {fix_pass + 1})" if fix_pass > 0 else ""
            emit(ROLE_NAMES[ROLE_CHECKER] + pass_label, issues or "No issues found")

            if not issues:
                break

            try:
                changes = self._code(request, plan, design, fix_note=issues)
                emit(f"💻 Coding Swarm (fix pass {fix_pass + 1})", f"{len(changes)} file(s) touched")
            except Exception as e:
                emit("⚠️ Fix Pass Error", str(e))
                break

        try:
            report = self._report(request, plan, design, changes, issues)
        except Exception as e:
            report = f"Report generation failed: {e}. {len(changes)} files were changed."
        emit(ROLE_NAMES[ROLE_CEO], report)

        return {"plan": plan, "design": design, "changes": changes,
                "issues": issues, "report": report}
