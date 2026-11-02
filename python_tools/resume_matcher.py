"""Compare plain-text resume and job-description files using keyword coverage."""
import argparse
import re
from pathlib import Path

STOP = set("a an and are as at be by for from in into is it of on or our that the their this to with you your we will experience work skills role team".split())

def words(text):
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z+#.-]{1,}", text.lower()) if w not in STOP}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume", type=Path, help="Plain-text resume file")
    parser.add_argument("job_description", type=Path, help="Plain-text job description file")
    args = parser.parse_args()
    try:
        resume, job = args.resume.read_text(encoding="utf-8"), args.job_description.read_text(encoding="utf-8")
    except OSError as exc:
        parser.error(str(exc))
    required = words(job)
    present = words(resume)
    matched, missing = sorted(required & present), sorted(required - present)
    score = round(100 * len(matched) / max(1, len(required)))
    print(f"Keyword coverage: {score}% ({len(matched)} of {len(required)} terms)")
    print("\nFound: " + (", ".join(matched) or "none"))
    print("\nNot found (review these manually): " + (", ".join(missing) or "none"))
    print("\nThis is a simple keyword comparison, not an ATS score or hiring prediction.")

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("resume_match", main)
