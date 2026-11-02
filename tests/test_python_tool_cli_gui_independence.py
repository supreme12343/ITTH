"""CLI help stays usable when desktop GUI packages are unavailable."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CLI_TOOLS = (
    "api_tester.py",
    "backup_snapshot.py",
    "csv_cleaner.py",
    "duplicate_finder.py",
    "interview_practice.py",
    "job_tracker.py",
    "json_helper.py",
    "repo_readiness.py",
    "resume_matcher.py",
    "secret_scanner.py",
)


def test_cli_help_does_not_import_gui_packages():
    blocker = r"""
import runpy
import sys
from pathlib import Path

class BlockGuiImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "customtkinter" or fullname.split(".", 1)[0] == "tkinter":
            raise ModuleNotFoundError("GUI import blocked for CLI regression", name=fullname)

sys.meta_path.insert(0, BlockGuiImports())
script = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(script.parent))
sys.argv = [str(script), "--cli", "--help"]
runpy.run_path(str(script), run_name="__main__")
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    for filename in CLI_TOOLS:
        script = ROOT / "python_tools" / filename
        completed = subprocess.run(
            [sys.executable, "-c", blocker, str(script)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )
        assert completed.returncode == 0, (
            f"{filename} --cli --help failed with GUI imports blocked:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
        assert "usage:" in completed.stdout.lower(), (
            f"{filename} did not show argparse help:\n{completed.stdout}\n{completed.stderr}"
        )


def test_offline_interview_cli_runs_with_gui_imports_blocked():
    blocker = r"""
import runpy
import sys
from pathlib import Path

class BlockGuiImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "customtkinter" or fullname.split(".", 1)[0] == "tkinter":
            raise ModuleNotFoundError("GUI import blocked for CLI regression", name=fullname)

sys.meta_path.insert(0, BlockGuiImports())
script = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(script.parent))
sys.argv = [str(script), "--cli", "--offline", "--count", "1", "--list"]
runpy.run_path(str(script), run_name="__main__")
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", blocker, str(ROOT / "python_tools" / "interview_practice.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "OFFLINE INTERVIEW PRACTICE" in completed.stdout
