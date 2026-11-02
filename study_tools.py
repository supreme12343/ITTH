"""
agent_core/study_tools.py

The 'skills' the Study Agent can choose to use for tool-shaped requests
(quiz, flashcards, summary, explain, plan, grade). Adapted from your
original tools.py — same idea, expanded tool set.
"""

from google.genai import types
import json


TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="generate_quiz",
        description=(
            "Create a Smart Study practice quiz from the student's uploaded material. "
            "Use this when the student asks to be quizzed, tested, or wants practice questions. "
            "Supports MCQ, multi-select, match-the-following, and short-answer formats."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "topic": types.Schema(type="STRING", description="Topic/chapter to focus on. Use 'all' if unspecified."),
                "num_questions": types.Schema(type="INTEGER", description="How many questions, default 5."),
                "difficulty": types.Schema(type="STRING", description="easy | medium | hard | exam"),
                "question_type": types.Schema(type="STRING", description="mcq | multiselect | match | short_answer | mixed"),
                "include_answer_key": types.Schema(type="BOOLEAN", description="Whether to include answers and explanations, default true."),
            },
            required=["topic"],
        ),
    ),
    types.FunctionDeclaration(
        name="generate_flashcards",
        description="Create flashcards from the study material for spaced repetition review.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "topic": types.Schema(type="STRING", description="Topic to make flashcards for."),
                "count": types.Schema(type="INTEGER", description="Number of flashcards, default 10."),
                "style": types.Schema(type="STRING", description="term_definition | question_answer | exam_review"),
            },
            required=["topic"],
        ),
    ),
    types.FunctionDeclaration(
        name="summarize_material",
        description="Summarize the uploaded study material or a specific section/topic of it, at a chosen depth.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "topic": types.Schema(type="STRING", description="Specific topic to summarize, or 'all'."),
                "depth": types.Schema(type="STRING", description="brief | detailed | exam_cram"),
            },
            required=["topic"],
        ),
    ),
    types.FunctionDeclaration(
        name="explain_concept",
        description="Explain a specific concept from the material in a clearer, more step-by-step way.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "concept": types.Schema(type="STRING", description="The concept the student is confused about."),
                "style": types.Schema(type="STRING", description="simple | analogy | exam_focused | step_by_step"),
                "include_example": types.Schema(type="BOOLEAN", description="Whether to include a worked example, default true."),
            },
            required=["concept"],
        ),
    ),
    types.FunctionDeclaration(
        name="create_study_plan",
        description="Build a day-by-day study plan/schedule for the uploaded material given a deadline or number of days.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "days_available": types.Schema(type="INTEGER", description="Number of days until the exam/deadline."),
                "hours_per_day": types.Schema(type="NUMBER", description="Study hours available per day, default 1.5."),
                "weak_topics": types.Schema(type="STRING", description="Topics the student feels weak in, if any."),
                "exam_date": types.Schema(type="STRING", description="Optional exam/deadline date as written by the student."),
            },
            required=["days_available"],
        ),
    ),
    types.FunctionDeclaration(
        name="grade_answer",
        description="Grade/check a student's written answer against the study material and give feedback.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "question": types.Schema(type="STRING", description="The question that was asked."),
                "student_answer": types.Schema(type="STRING", description="The student's answer to evaluate."),
                "strictness": types.Schema(type="STRING", description="encouraging | normal | strict"),
            },
            required=["question", "student_answer"],
        ),
    ),
    types.FunctionDeclaration(
        name="generate_revision_sheet",
        description="Create a one-page revision/cram sheet with key facts, terms, formulas, common mistakes, and must-know points.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "topic": types.Schema(type="STRING", description="Topic or 'all'."),
                "exam_focus": types.Schema(type="STRING", description="Optional exam or syllabus focus."),
            },
            required=["topic"],
        ),
    ),
    types.FunctionDeclaration(
        name="create_practice_schedule",
        description="Create a Pomodoro-style practice schedule with spaced repetition checkpoints.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "days_available": types.Schema(type="INTEGER", description="Number of days to schedule."),
                "minutes_per_session": types.Schema(type="INTEGER", description="Pomodoro/study block length, default 25."),
                "sessions_per_day": types.Schema(type="INTEGER", description="Number of sessions per day, default 3."),
                "focus_topics": types.Schema(type="STRING", description="Topics to prioritize."),
            },
            required=["days_available"],
        ),
    ),
    types.FunctionDeclaration(
        name="track_study_progress",
        description="Turn the student's progress update into completed topics, weak topics, confidence score, and next actions.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "progress_update": types.Schema(type="STRING", description="Student's description of what they completed and where they're stuck."),
                "goal": types.Schema(type="STRING", description="Optional goal/exam target."),
            },
            required=["progress_update"],
        ),
    ),
    types.FunctionDeclaration(
        name="make_mnemonics",
        description="Create memory tricks, acronyms, or vivid associations for lists/processes from the material.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "topic": types.Schema(type="STRING", description="Topic/list/process to memorize."),
                "style": types.Schema(type="STRING", description="acronym | story | visual | mixed"),
            },
            required=["topic"],
        ),
    ),
    types.FunctionDeclaration(
        name="sync_school_worksheets",
        description="Download worksheets from the school website (iswkoman.com) and save them locally. Use when student wants to update/sync school worksheets.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "year": types.Schema(type="STRING", description="Academic year to sync (e.g., '2026-2027'), or omit for all years."),
                "class_name": types.Schema(type="STRING", description="Class to filter (e.g., 'XI', 'XII'), or omit for all classes."),
                "subject": types.Schema(type="STRING", description="Subject to filter (e.g., 'Computer Science'), or omit for all subjects."),
                "max_items": types.Schema(type="INTEGER", description="Maximum worksheets to download per subject, default 10."),
            },
            required=[],
        ),
    ),
    types.FunctionDeclaration(
        name="search_school_worksheets",
        description="Search locally stored school worksheets by keyword. Use when student asks about specific topics from school worksheets.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(type="STRING", description="Search query (topic, keyword, concept)."),
                "year": types.Schema(type="STRING", description="Filter by academic year."),
                "class_name": types.Schema(type="STRING", description="Filter by class (e.g., 'XI')."),
                "subject": types.Schema(type="STRING", description="Filter by subject."),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_worksheet_content",
        description="Get the full content of a specific worksheet by its local file path. Use after searching to read a specific worksheet.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "path": types.Schema(type="STRING", description="Local file path of the worksheet (from search results)."),
            },
            required=["path"],
        ),
    ),
    types.FunctionDeclaration(
        name="analyze_code_security",
        description="Analyze source code for security vulnerabilities using static analysis (bandit/semgrep). Upload code files or a project directory to find security issues, get severity ratings, and remediation guidance.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "path": types.Schema(type="STRING", description="Path to source file or project directory to analyze."),
                "language": types.Schema(type="STRING", description="Programming language (python, javascript, java, go, etc.). Auto-detected if omitted."),
                "severity": types.Schema(type="STRING", description="Minimum severity to report: low, medium, high, critical. Default: medium."),
                "tool": types.Schema(type="STRING", description="Analysis tool: bandit (Python), semgrep (multi-language), or auto. Default: auto."),
            },
            required=["path"],
        ),
    ),
    types.FunctionDeclaration(
        name="generate_secure_coding_flashcards",
        description="Generate flashcards for secure coding practices based on real vulnerabilities found in code analysis.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "vulnerabilities": types.Schema(type="STRING", description="JSON string of vulnerabilities from analyze_code_security, or topic area (e.g., 'SQL injection', 'XSS', 'path traversal')."),
                "count": types.Schema(type="INTEGER", description="Number of flashcards to generate, default 10."),
            },
            required=["vulnerabilities"],
        ),
    ),
]

STUDY_AGENT_TOOL = types.Tool(function_declarations=TOOL_DECLARATIONS)

# ---------------------------------------------------------------------------
# AI — SHARED CAMPUS SUITE KEY MANAGER
# A tool result normally carries only an "instruction_for_model" that the Study
# Agent's own model renders. When a caller also hands the tool the study
# `material`, the tool does the generation itself through Campus Suite's shared
# key manager (campus_ai -> key_manager.AiApi), so these skills work outside the
# agent loop too. Without `material` nothing is sent anywhere and behaviour is
# unchanged. There is no tkinter UI in this module, so the call is synchronous;
# GUI callers must run it on a background thread.
# ---------------------------------------------------------------------------
CAMPUS_AI_SYSTEM = (
    "You are the Campus Suite Study Companion. Work strictly from the study material you are given, "
    "follow the requested format exactly, never invent facts that are not in the material, and return "
    "only the requested content with no preamble or markdown code fences."
)


def _render_with_campus_ai(instruction, material, role="reporter"):
    """Run a tool instruction through the shared key manager. Returns (content, error)."""
    try:
        from campus_ai import ask_campus_ai
    except Exception as exc:
        return None, f"Campus Suite AI is unavailable: {exc}"
    try:
        content = ask_campus_ai(
            CAMPUS_AI_SYSTEM,
            f"STUDY MATERIAL:\n---\n{str(material)[:16000]}\n---\n\nTASK:\n{instruction}",
            role=role,
        )
        return content, None
    except Exception as exc:
        return None, f"Campus Suite AI could not generate this: {exc}"


def _tool_result(instruction, material="", role="reporter"):
    """Always return the instruction; add real AI content when material was supplied."""
    payload = {"instruction_for_model": instruction}
    if str(material or "").strip():
        content, error = _render_with_campus_ai(instruction, material, role=role)
        if content:
            payload["content"] = content
        elif error:
            payload["ai_note"] = error
    return payload


def generate_quiz(topic: str, num_questions: int = 5, difficulty: str = "medium",
                  question_type: str = "mixed", include_answer_key: bool = True,
                  material: str = "", **_):
    type_map = {
        "mcq": "single-answer MCQs with 4 options labeled A-D",
        "multiselect": "multi-select questions where 2 or 3 options may be correct",
        "match": "match-the-following questions with 4 left items and 4 shuffled right matches",
        "short_answer": "short-answer questions that require 1-3 sentence responses",
        "mixed": "a balanced mix of MCQ, multi-select, match-the-following, and short-answer questions",
    }
    answer_key = (
        "After ALL questions, include an 'Answer Key' section with correct answers and one-line explanations."
        if include_answer_key else
        "Do not include answers until the student asks for them."
    )
    return _tool_result(
        f"Using ONLY the study material provided in context, write a Smart Study quiz with "
        f"exactly {num_questions} questions on '{topic}'. Difficulty: {difficulty}. Format: "
        f"{type_map.get(question_type, type_map['mixed'])}. Number each question clearly. "
        f"For MCQs use A-D options. For multi-select, say 'Select all that apply'. For matching, "
        f"show left items and shuffled right-side matches. For short answers, ask for a concise typed response. "
        f"{answer_key} Do not invent facts outside the uploaded material.",
        material, role="planner")


def generate_flashcards(topic: str, count: int = 10, style: str = "term_definition",
                        material: str = "", **_):
    style_map = {
        "term_definition": "Front = key term/concept, Back = definition or explanation",
        "question_answer": "Front = exam-style question, Back = concise answer",
        "exam_review": "Front = likely exam prompt, Back = scoring-point answer with keywords",
    }
    return _tool_result(
        f"Using ONLY the study material provided in context, create {count} flashcards on '{topic}'. "
        f"Style: {style_map.get(style, style_map['term_definition'])}. Format every card as:\n"
        f"F: ...\nB: ...\nKeep cards short enough to memorize and avoid duplicates.",
        material, role="designer")


def summarize_material(topic: str, depth: str = "brief", material: str = "", **_):
    length_map = {
        "brief": "3-5 bullet points with only the most important ideas",
        "detailed": "a structured summary with headings, covering all key points and examples",
        "exam_cram": "a fast exam-cram sheet: must-know facts, definitions, formulas, and likely question angles",
    }
    return _tool_result(
        f"Summarize the study material" + (f" on '{topic}'" if topic != "all" else "") +
        f" as {length_map.get(depth, length_map['brief'])}. Stay strictly grounded in the provided material.",
        material, role="reporter")


def explain_concept(concept: str, style: str = "simple", include_example: bool = True,
                    material: str = "", **_):
    style_map = {
        "simple": "in plain, simple language a student new to the topic can follow",
        "analogy": "using a clear real-world analogy first, then connecting it back to the real concept",
        "exam_focused": "focused on exactly what is likely to be tested and the terms an examiner expects",
        "step_by_step": "as a numbered step-by-step walkthrough with checkpoints",
    }
    example_text = " Include one short worked example grounded in the material." if include_example else ""
    return _tool_result(
        f"Explain '{concept}' {style_map.get(style, style_map['simple'])}, grounded in the uploaded "
        f"study material. If the material doesn't cover it, say so before giving a general explanation."
        f"{example_text}",
        material, role="reporter")


def create_study_plan(days_available: int, hours_per_day: float = 1.5,
                      weak_topics: str = "", exam_date: str = "", **_):
    weak_text = f" Prioritize these weak topics: {weak_topics}." if weak_topics else ""
    date_text = f" Target date: {exam_date}." if exam_date else ""
    return {
        "instruction_for_model": (
            f"Using the study material's topics/structure, build a day-by-day study plan spanning "
            f"{days_available} days at {hours_per_day} hours/day.{date_text}{weak_text} Break the material "
            f"into logical chunks, assign topics to specific days, and include review/self-test sessions."
        )
    }


def grade_answer(question: str, student_answer: str, strictness: str = "normal", **_):
    strict_map = {
        "encouraging": "Be gentle and motivational while still pointing out missing content.",
        "normal": "Be balanced: encouraging but clear about mistakes.",
        "strict": "Grade like an exam marker and be precise about missing scoring points.",
    }
    return {
        "instruction_for_model": (
            f"Grade this student answer against the study material.\nQuestion: {question}\n"
            f"Student's answer: {student_answer}\n"
            f"Strictness: {strict_map.get(strictness, strict_map['normal'])}\n"
            f"Give: (1) what's correct, (2) what's missing or wrong, (3) a corrected/model answer, "
            f"(4) a rough score out of 10."
        )
    }


def generate_revision_sheet(topic: str, exam_focus: str = "", material: str = "", **_):
    focus = f" Exam/syllabus focus: {exam_focus}." if exam_focus else ""
    return _tool_result(
        f"Create a one-page revision sheet for '{topic}' using ONLY the study material.{focus} "
        f"Use these sections: Must-Know Ideas, Key Terms, Formulas/Processes, Common Mistakes, "
        f"Likely Exam Questions, 5-Minute Final Review. Make it compact and easy to scan.",
        material, role="reporter")


def create_practice_schedule(days_available: int, minutes_per_session: int = 25,
                             sessions_per_day: int = 3, focus_topics: str = "", **_):
    focus = f" Prioritize: {focus_topics}." if focus_topics else ""
    return {
        "instruction_for_model": (
            f"Create a Pomodoro-style practice schedule for {days_available} days with {sessions_per_day} "
            f"sessions/day and {minutes_per_session}-minute sessions.{focus} Include what to study, what to quiz, "
            f"and spaced-repetition checkpoints (same day, next day, and final review)."
        )
    }


def track_study_progress(progress_update: str, goal: str = "", **_):
    goal_text = f" Goal/exam target: {goal}." if goal else ""
    return {
        "instruction_for_model": (
            f"Turn this progress update into a clear study progress snapshot.\nUpdate: {progress_update}\n{goal_text}\n"
            f"Return sections: Completed Topics, Weak Topics, Confidence Score (0-100 with reason), "
            f"Next 3 Actions, What to Review Tomorrow. If details are missing, ask one short follow-up question."
        )
    }


def make_mnemonics(topic: str, style: str = "mixed", **_):
    style_map = {
        "acronym": "use acronyms and first-letter memory aids",
        "story": "use a tiny memorable story",
        "visual": "use vivid visual associations",
        "mixed": "use a mix of acronyms, visuals, and tiny stories",
    }
    return {
        "instruction_for_model": (
            f"Create mnemonics for '{topic}' using the study material. Style: "
            f"{style_map.get(style, style_map['mixed'])}. Explain what each mnemonic maps to, and keep it easy to remember."
        )
    }


def sync_school_worksheets(year: str = None, class_name: str = None, subject: str = None, max_items: int = 10, **_):
    from school_scraper import sync_worksheets
    try:
        max_items = max(1, min(int(max_items), 25))
    except (TypeError, ValueError):
        max_items = 10
    result = sync_worksheets(year=year, class_name=class_name, subject=subject, max_items=max_items)
    return {"result": result}


def search_school_worksheets(query: str, year: str = None, class_name: str = None, subject: str = None, **_):
    from school_scraper import search_local_worksheets
    results = search_local_worksheets(query, year=year, class_name=class_name, subject=subject)
    return {"results": results}


def get_worksheet_content(path: str, **_):
    from school_scraper import get_worksheet_content
    content = get_worksheet_content(path)
    return {"content": content}


def analyze_code_security(path: str, language: str = "", severity: str = "medium", tool: str = "auto", **_):
    """Analyze source code for security vulnerabilities using bandit/semgrep."""
    import subprocess
    import json
    import os
    from pathlib import Path

    target = Path(path)
    if not target.exists():
        return {"error": f"Path not found: {path}"}

    # Auto-detect language if not specified
    if not language:
        ext_map = {".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java",
                   ".go": "go", ".php": "php", ".rb": "ruby", ".cs": "csharp", ".cpp": "cpp", ".c": "c"}
        language = ext_map.get(target.suffix.lower(), "python")

    # Auto-select tool
    if tool == "auto":
        tool = "bandit" if language == "python" else "semgrep"

    findings = []

    try:
        if tool == "bandit" and language == "python":
            # Run bandit
            cmd = ["bandit", "-r", str(target), "-f", "json", "-ll"]  # -ll = only low and higher
            if severity != "low":
                sev_map = {"medium": "-ii", "high": "-iii", "critical": "-iii"}
                cmd.append(sev_map.get(severity, "-ii"))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.stdout:
                data = json.loads(result.stdout)
                for item in data.get("results", []):
                    findings.append({
                        "tool": "bandit",
                        "file": item.get("filename", ""),
                        "line": item.get("line_number", 0),
                        "severity": item.get("issue_severity", "").lower(),
                        "confidence": item.get("issue_confidence", "").lower(),
                        "type": item.get("test_id", ""),
                        "message": item.get("issue_text", ""),
                        "code": item.get("code", ""),
                        "cwe": item.get("cwe", {}).get("id", "") if isinstance(item.get("cwe"), dict) else str(item.get("cwe", "")),
                    })

        elif tool == "semgrep":
            # Run semgrep with security rules
            config = "p/security-audit"  # Built-in security rules
            cmd = ["semgrep", "--config", config, "--json", "--error", str(target)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.stdout:
                data = json.loads(result.stdout)
                for item in data.get("results", []):
                    extra = item.get("extra", {})
                    findings.append({
                        "tool": "semgrep",
                        "file": item.get("path", ""),
                        "line": item.get("start", {}).get("line", 0),
                        "severity": extra.get("severity", "").lower(),
                        "type": item.get("check_id", ""),
                        "message": extra.get("message", ""),
                        "code": extra.get("lines", ""),
                        "cwe": extra.get("metadata", {}).get("cwe", "") if isinstance(extra.get("metadata"), dict) else "",
                    })

    except FileNotFoundError:
        return {"error": f"{tool} not installed. Run: pip install bandit  # or install semgrep"}
    except subprocess.TimeoutExpired:
        return {"error": "Analysis timed out"}
    except Exception as e:
        return {"error": f"Analysis failed: {e}"}

    # Format results for the model
    if not findings:
        return {"findings": [], "summary": "No security issues found at the specified severity level."}

    # Group by severity
    by_severity = {"critical": [], "high": [], "medium": [], "low": []}
    for f in findings:
        sev = f.get("severity", "low")
        if sev in by_severity:
            by_severity[sev].append(f)
        else:
            by_severity["low"].append(f)

    summary_lines = []
    for sev in ["critical", "high", "medium", "low"]:
        if by_severity[sev]:
            summary_lines.append(f"{sev.upper()}: {len(by_severity[sev])} findings")

    return {
        "findings": findings,
        "summary": f"Found {len(findings)} total issues.\n" + "\n".join(summary_lines),
        "by_severity": by_severity,
        "tool_used": tool,
        "language": language,
    }


def generate_secure_coding_flashcards(vulnerabilities: str, count: int = 10, **_):
    """Generate flashcards for secure coding based on vulnerabilities."""
    return {
        "instruction_for_model": (
            f"Create {count} secure coding flashcards based on these vulnerabilities/findings:\n{vulnerabilities}\n\n"
            f"Each card should teach a secure coding practice that prevents the vulnerability.\n"
            f"Format:\nF: [Vulnerability name / scenario]\nB: [Secure coding fix + explanation + code example]\n"
            f"Include: vulnerability type, vulnerable pattern, secure alternative, and prevention tip."
        )
    }


TOOL_IMPLEMENTATIONS = {
    "generate_quiz": generate_quiz,
    "generate_flashcards": generate_flashcards,
    "summarize_material": summarize_material,
    "explain_concept": explain_concept,
    "create_study_plan": create_study_plan,
    "grade_answer": grade_answer,
    "generate_revision_sheet": generate_revision_sheet,
    "create_practice_schedule": create_practice_schedule,
    "track_study_progress": track_study_progress,
    "make_mnemonics": make_mnemonics,
    "sync_school_worksheets": sync_school_worksheets,
    "search_school_worksheets": search_school_worksheets,
    "get_worksheet_content": get_worksheet_content,
    "analyze_code_security": analyze_code_security,
    "generate_secure_coding_flashcards": generate_secure_coding_flashcards,
}
