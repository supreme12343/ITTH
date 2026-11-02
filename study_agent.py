# hello world
"""
agent_core/study_agent.py

Study Agent â€” the "smart tutor" mode of Campus Suite. Two paths, two
different key pools, because they need different things:

1. General / factual questions  -> SEARCH_POOL (keys 1-2), advanced model.
     Asker    (key 1) drafts an answer WITH Google Search grounding on.
     Verifier (key 2) independently re-checks that draft against the
                       question before the student ever sees it â€” a
                       single ungrounded pass is exactly where mistakes
                       creep in, so a second key double-checks first.

2. Tool-shaped requests (quiz/flashcards/summary/explain/plan/grade)
   -> TOOL_POOL (keys 3-5), lighter model, same declare-tools /
   model-picks-tool / execute / feed-back-in loop pattern as your
   original agent.py.

NOTE: MODEL_ADVANCED is set to a placeholder string â€” point it at
whatever advanced/search-capable model your 5 accounts actually have
access to (e.g. a Gemini 3 Flash / Pro variant).
"""

import logging
import random
import time

from google.genai import types

from key_manager import AiApi, RoleKeyManager
from study_tools import STUDY_AGENT_TOOL, TOOL_IMPLEMENTATIONS

logger = logging.getLogger("study_agent")

MODEL_ADVANCED = "gemini-3.1-flash-lite"
MODEL_LITE = "gemini-3.1-flash-lite"
MAX_STEPS = 6
MAX_TOOL_CALLS_PER_STEP = 4
MAX_RETRIES = 3
BACKOFF_BASE = 1.0
BACKOFF_CAP = 30.0
MAX_HISTORY_TURNS = 12

SYSTEM_INSTRUCTION = """You are Study Companion, an AI study agent inside a student's \
Campus Suite app. You help students learn from material THEY uploaded.

Rules:
- Ground every answer in the provided study material when it's relevant.
- When a request matches a tool, call that tool instead of freehanding the format yourself.
  Tools include Smart Study quizzes, flashcards, summaries, concept explanations, study plans,
  answer grading, revision sheets, practice schedules, progress tracking, mnemonics, and
  worksheet search/sync.
- For quiz requests, prefer the Smart Study quiz tool and pick the requested question type
  (MCQ, multi-select, match-the-following, short answer, or mixed).
- For plain questions with no matching tool, just answer directly and clearly.
- Keep tone encouraging, clear, and appropriately concise for a student.
"""

VERIFIER_INSTRUCTION = """You are a fact-checker for a student study app. You'll get a \
question and a DRAFT ANSWER that was already grounded with web search. Your only job:
1. Check the draft for factual errors or claims the search results don't actually back up.
2. If it's correct, return it nearly as-is (light polish only).
3. If something is wrong, correct it and briefly flag what changed.
Don't introduce new unverified claims. Be concise â€” the student reads this directly, not \
a QA report about your process. Never mention "the draft" or that you're verifying.
"""


def _backoff_delay(attempt: int) -> float:
    delay = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
    jitter = random.uniform(0, delay * 0.3)
    return delay + jitter


def _is_transient_error(exc: Exception) -> bool:
    s = str(exc)
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    return any(k in s for k in ("429", "503", "RESOURCE_EXHAUSTED", "rate", "overloaded"))


def _trim_history(history: list, max_turns: int = MAX_HISTORY_TURNS) -> list:
    if len(history) <= max_turns:
        return history
    return history[-max_turns:]


def _looks_like_tool_request(message: str) -> bool:
    keywords = [
        "quiz", "test me", "practice question", "mcq", "multi-select", "multiselect",
        "match the following", "short answer", "flashcard", "summar", "explain",
        "study plan", "schedule", "practice schedule", "pomodoro", "grade my",
        "check my answer", "revision sheet", "cram sheet", "cheat sheet", "mnemonic",
        "memory trick", "progress", "weak topic", "confidence score", "worksheet", "sync school",
    ]
    m = message.lower()
    return any(k in m for k in keywords)


class StudyAgent:
    def __init__(self, key_manager):
        # Accept either RoleKeyManager or AiApi
        if hasattr(key_manager, '_km'):
            self.km = key_manager._km
        else:
            self.km = key_manager

    # ---- Path 1: search-grounded QA (keys 1 + 2) --------------------------
    def _answer_with_search(self, material_context: str, question: str, history_text: str):
        search_tool = types.Tool(google_search=types.GoogleSearch())
        draft_config = types.GenerateContentConfig(
            system_instruction=(
                "You are a careful study tutor. Use Google Search grounding to verify facts "
                "before answering. Ground your answer in the study material below where "
                "relevant, and in reliable search results otherwise.\n\n"
                f"--- STUDY MATERIAL ---\n{material_context}\n--- END MATERIAL ---\n\n"
                f"CONVERSATION SO FAR:\n{history_text}"
            ),
            tools=[search_tool],
            temperature=0.3,
        )
        draft_text = ""
        asker_label = "?"
        for attempt in range(MAX_RETRIES):
            try:
                draft_resp, asker_label = self.km.generate(
                    RoleKeyManager.SEARCH_POOL, model=MODEL_ADVANCED,
                    contents=[types.Content(role="user", parts=[types.Part(text=question)])],
                    config=draft_config,
                )
                draft_text = "".join(p.text or "" for p in draft_resp.candidates[0].content.parts)
                break
            except Exception as e:
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    logger.warning("Search asker retry %d/%d: %s", attempt + 1, MAX_RETRIES, e)
                    time.sleep(_backoff_delay(attempt))
                    continue
                raise

        if not draft_text.strip():
            return "I couldn't generate an answer. Please try again.", [asker_label]

        verify_config = types.GenerateContentConfig(system_instruction=VERIFIER_INSTRUCTION, temperature=0.2)
        verify_prompt = f"QUESTION: {question}\n\nDRAFT ANSWER:\n{draft_text}"
        final_text = draft_text
        verifier_label = "?"
        for attempt in range(MAX_RETRIES):
            try:
                verify_resp, verifier_label = self.km.generate(
                    RoleKeyManager.SEARCH_POOL, model=MODEL_ADVANCED,
                    contents=[types.Content(role="user", parts=[types.Part(text=verify_prompt)])],
                    config=verify_config,
                )
                final_text = "".join(p.text or "" for p in verify_resp.candidates[0].content.parts)
                break
            except Exception as e:
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    logger.warning("Verifier retry %d/%d: %s", attempt + 1, MAX_RETRIES, e)
                    time.sleep(_backoff_delay(attempt))
                    continue
                logger.warning("Verifier failed, using unverified draft: %s", e)
                final_text = draft_text
                verifier_label = "(skipped)"
                break
        return final_text, [asker_label, verifier_label]

    # ---- Path 2: tool-calling loop (keys 3, 4, 5) --------------------------
    def _answer_with_tools(self, material_context: str, contents: list):
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION + f"\n\n--- STUDY MATERIAL ---\n{material_context}\n--- END MATERIAL ---",
            tools=[STUDY_AGENT_TOOL],
            temperature=0.4,
        )
        tool_calls_made, keys_used = [], []
        for _ in range(MAX_STEPS):
            response, key_label = self.km.generate(
                RoleKeyManager.TOOL_POOL, model=MODEL_LITE, contents=contents, config=config)
            keys_used.append(key_label)
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                return ("I couldn't get a usable response from the study service. Please try again.",
                        contents, tool_calls_made, keys_used)
            candidate = candidates[0]
            parts = candidate.content.parts
            function_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

            if not function_calls:
                final_text = "".join(p.text or "" for p in parts)
                contents.append(candidate.content)
                return final_text, contents, tool_calls_made, keys_used

            contents.append(candidate.content)
            response_parts = []
            for fc in function_calls[:MAX_TOOL_CALLS_PER_STEP]:
                fn = TOOL_IMPLEMENTATIONS.get(fc.name)
                try:
                    args = dict(fc.args) if fc.args else {}
                except (TypeError, ValueError):
                    args = {}
                if fn is None:
                    result = {
                        "status": "error",
                        "summary": f"Unknown tool: {fc.name}",
                        "next_actions": ["Choose a tool from the declared study tools."],
                        "artifacts": [],
                    }
                else:
                    try:
                        result = fn(**args)
                        if not isinstance(result, dict):
                            result = {"result": result}
                    except (TypeError, ValueError) as exc:
                        result = {
                            "status": "error",
                            "summary": f"Invalid arguments for {fc.name}: {exc}",
                            "next_actions": ["Correct the arguments and retry once."],
                            "artifacts": [],
                        }
                    except Exception as exc:
                        logger.exception("Study tool %s failed", fc.name)
                        result = {
                            "status": "error",
                            "summary": f"{fc.name} failed: {exc}",
                            "next_actions": ["Explain the issue and try a safer alternative."],
                            "artifacts": [],
                        }
                if result.get("status") not in {"success", "warning", "error"}:
                    details = dict(result)
                    error = details.pop("error", None)
                    result = {
                        "status": "error" if error else "success",
                        "summary": str(error) if error else details.pop("summary", "Tool completed."),
                        "next_actions": (["Explain the issue and offer a safe retry."] if error else []),
                        "artifacts": details.pop("artifacts", []),
                        **details,
                    }
                tool_calls_made.append({"tool": fc.name, "args": args, "status": result.get("status", "success")})
                response_parts.append(types.Part.from_function_response(name=fc.name, response=result))
            for fc in function_calls[MAX_TOOL_CALLS_PER_STEP:]:
                response_parts.append(types.Part.from_function_response(
                    name=fc.name,
                    response={
                        "status": "warning",
                        "summary": "This response exceeded the per-step tool-call limit.",
                        "next_actions": ["Continue with only the most relevant tools."],
                        "artifacts": [],
                    },
                ))
            contents.append(types.Content(role="user", parts=response_parts))

        return ("I got stuck chaining steps for that one â€” try rephrasing or splitting it "
                 "into two requests."), contents, tool_calls_made, keys_used

    # ---- Public entrypoint --------------------------------------------------
    def chat(self, material_context: str, history: list, user_message: str) -> dict:
        if _looks_like_tool_request(user_message):
            contents = list(history) + [types.Content(role="user", parts=[types.Part(text=user_message)])]
            reply, new_history, tool_calls, keys_used = self._answer_with_tools(material_context, contents)
            return {"reply": reply, "history": new_history, "tool_calls": tool_calls,
                    "keys_used": keys_used, "mode": "tools"}

        history_text = "\n".join(
            f"{c.role}: {''.join(p.text or '' for p in c.parts)}" for c in history[-6:]
        )
        reply, keys_used = self._answer_with_search(material_context, user_message, history_text)
        new_history = list(history) + [
            types.Content(role="user", parts=[types.Part(text=user_message)]),
            types.Content(role="model", parts=[types.Part(text=reply)]),
        ]
        return {"reply": reply, "history": new_history, "tool_calls": [],
                "keys_used": keys_used, "mode": "search"}

