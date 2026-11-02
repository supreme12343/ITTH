"""Review a project folder for common GitHub and portfolio submission essentials."""
import argparse
import subprocess
from pathlib import Path

SKIP = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}
DOCS = {"readme", "readme.md", "readme.txt", "readme.rst"}
MANIFESTS = {"package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "go.mod", "pom.xml", "build.gradle", "gemfile"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    root = args.folder.expanduser().resolve()
    if not root.is_dir(): parser.error("folder must be an existing directory")
    files = []
    for path in root.rglob("*"):
        if any(part in SKIP for part in path.relative_to(root).parts): continue
        if path.is_file(): files.append(path)
    names = {path.name.lower() for path in files}
    issues, good = [], []
    (good if names & DOCS else issues).append("README with setup, usage, and screenshots")
    (good if names & MANIFESTS else issues).append("dependency or project manifest")
    (good if any(name.startswith("license") or name == "copying" for name in names) else issues).append("license file (when sharing code publicly)")
    (good if ".gitignore" in names else issues).append(".gitignore for generated files and local secrets")
    (good if any("test" in path.name.lower() or "test" in path.parent.name.lower() for path in files) else issues).append("tests or a documented manual verification checklist")
    sensitive = [p.relative_to(root).as_posix() for p in files if p.name.lower() in {".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}]
    print(f"Project readiness: {root}\nFiles checked: {len(files)}\n")
    print("Present:")
    for item in good: print(f"  ✓ {item}")
    print("\nSuggested before sharing:")
    for item in issues: print(f"  • Add or review {item}")
    if sensitive:
        print("\nSensitive-looking files to check before publishing:")
        for item in sensitive: print(f"  ! {item}")
    if (root / ".git").exists():
        result = subprocess.run(["git", "-C", str(root), "status", "--short"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            changes = result.stdout.strip()
            print("\nGit working tree: " + (f"{len(changes.splitlines())} changed or untracked path(s)" if changes else "clean"))
    print("\nThis checklist is a reminder, not a security or hiring certification.")

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("repo_readiness", main)
