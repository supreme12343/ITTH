"""Scan a project for common accidentally committed credential patterns."""
import argparse
import re
from pathlib import Path

SKIP = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next"}
MAX_FILE_BYTES = 1_000_000
PATTERNS = [
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("OpenAI-style secret key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Credential assignment", re.compile(r"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\b\s*[:=]\s*['\"]?([A-Za-z0-9_./+=-]{16,})")),
]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    root = args.folder.expanduser().resolve()
    if not root.is_dir(): parser.error("folder must be an existing directory")
    findings, scanned = [], 0
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP for part in path.relative_to(root).parts): continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES: continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError: continue
        scanned += 1
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS:
                if pattern.search(line): findings.append((path.relative_to(root).as_posix(), line_number, label)); break
        if path.name.lower() in {".env", ".env.local", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}:
            findings.append((path.relative_to(root).as_posix(), 0, "sensitive filename (review contents and Git tracking)"))
    print(f"Scanned {scanned} small text files in {root}\n")
    if not findings:
        print("No common credential patterns found. This does not guarantee the project contains no secrets.")
        return
    print(f"Found {len(findings)} item(s) to review. Values were not printed:\n")
    for filename, line, label in findings:
        suffix = f":{line}" if line else ""
        print(f"  {filename}{suffix} — {label}")
    print("\nRotate exposed credentials and remove them from Git history before publishing.")

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("secret_scanner", main)
