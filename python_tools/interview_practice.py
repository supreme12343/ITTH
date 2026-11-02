"""Practice a short set of interview questions by role."""
import argparse, json, random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# campus_ai is imported only when generating questions. CLI help and explicit
# offline practice do not load the optional provider stack.

QUESTIONS = {
    "general": ["Tell me about a project you are proud of.", "Describe a difficult problem and how you solved it.", "What would you like to learn in your next role?", "Tell me about a time you received critical feedback.", "What questions do you have for the interviewer?"],
    "software": ["Walk me through a technical project and your contribution.", "How do you investigate a bug you cannot reproduce reliably?", "Explain a trade-off you made in a system design.", "How do you review code and respond to review feedback?", "Describe how you would test a new API endpoint."],
    "data": ["How do you handle missing or inconsistent data?", "Describe an analysis that changed a decision.", "How would you explain a model's limits to a nontechnical audience?", "How do you check whether a metric is misleading?", "Tell me about a data pipeline you built or improved."],
    "product": ["How do you prioritize competing product requests?", "Describe a product decision made with incomplete evidence.", "How would you measure success for a new feature?", "Tell me about a disagreement with a stakeholder.", "How do you decide what not to build?"]
}
OFFLINE_EXTRA_QUESTIONS = [
    "How do you set priorities when several deadlines compete?",
    "Tell me about a time you had to learn an unfamiliar skill quickly.",
    "What kind of feedback helps you do your best work?",
    "Describe a decision you changed after seeing new evidence.",
    "What would you like to know about how this team works?",
]


def fallback_questions(role: str, count: int) -> list[str]:
    """Return usable local questions when the configured AI provider is offline."""
    count = max(1, min(int(count), 10))
    base = QUESTIONS.get(role, QUESTIONS["general"])
    return (base + OFFLINE_EXTRA_QUESTIONS)[:count]


def generate_ai_questions(role: str, target_role: str, count: int) -> list[str]:
    """Generate a fresh role-specific set through Campus Suite's key manager."""
    from campus_ai import ask_campus_ai
    count = max(1, min(int(count), 10))
    focus = target_role.strip() or role
    system = (
        "You are an experienced interviewer. Create realistic, distinct interview questions "
        "for the candidate context. Mix role knowledge with behavioral questions where useful. "
        "Do not include answers, introductions, or commentary. Return only a JSON array of strings."
    )
    prompt = (
        f"Interview category: {role}\n"
        f"Target job or specialization: {focus}\n"
        f"Create exactly {count} questions. Keep them suitable for a real interview and avoid generic filler."
    )
    # Keep this request quick and bounded: roughly 64 output tokens per
    # question, with a small buffer for short or behavioral questions.
    answer = ask_campus_ai(system, prompt, max_tokens=count * 64 + 32)
    start = answer.find("[")
    if start < 0:
        raise RuntimeError("Campus Suite AI did not return a question list. Please try again.")
    try:
        parsed, _end = json.JSONDecoder().raw_decode(answer[start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Campus Suite AI returned an unreadable question list. Please try again.") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("questions")
    if not isinstance(parsed, list):
        raise RuntimeError("Campus Suite AI did not return a question list. Please try again.")
    questions = [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
    if len(questions) < count:
        raise RuntimeError(f"Campus Suite AI returned {len(questions)} of {count} requested questions. Please try again.")
    return questions[:count]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role", choices=QUESTIONS, default="general")
    p.add_argument("--target-role", default="", help="Job title or specialization to tailor questions")
    p.add_argument("--count", type=int, default=5, help="Number of questions (default: 5; maximum: 10)")
    p.add_argument("--shuffle", action="store_true", help="Choose a random order")
    p.add_argument("--list", action="store_true", help="Print questions without interactive pauses")
    p.add_argument("--offline", action="store_true", help="Use built-in questions without contacting Campus Suite AI")
    a = p.parse_args()
    if not 1 <= a.count <= 10: p.error("count must be from 1 to 10")
    used_ai = False
    if a.offline:
        qs = fallback_questions(a.role, a.count)
    else:
        try:
            qs = generate_ai_questions(a.role, a.target_role, a.count)
            used_ai = True
        except Exception:
            qs = fallback_questions(a.role, a.count)
    if a.shuffle: random.SystemRandom().shuffle(qs)
    focus = a.target_role.strip() or a.role.title()
    mode = "AI-GENERATED INTERVIEW PRACTICE" if used_ai else "OFFLINE INTERVIEW PRACTICE"
    print(f"{mode} · {focus}\n")
    if not used_ai:
        note = "Offline mode is enabled." if a.offline else "Campus Suite AI is unavailable."
        print(f"{note} Built-in questions are ready for practice.\n")
    for i, q in enumerate(qs[:a.count], 1):
        if a.list: print(f"{i}. {q}")
        else: input(f"{i}. {q}\n   Press Enter when ready for the next question…")
    print("\nPractice complete. Try answering with a clear situation, action, and result.")

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("interview_practice", main)
