"""Create a dated copy of a folder before risky edits or cleanup."""
import argparse
import shutil
from datetime import datetime
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Folder to back up")
    parser.add_argument("--to", type=Path, default=Path("backups"), help="Backup parent folder (default: ./backups)")
    parser.add_argument("--apply", action="store_true", help="Create the copy; without this, show a preview")
    args = parser.parse_args()
    source = args.source.resolve()
    destination_parent = args.to.resolve()
    if not source.is_dir():
        parser.error("source must be an existing directory")
    if source == destination_parent or source in destination_parent.parents:
        parser.error("backup destination cannot be the source folder or a folder inside it")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = destination_parent / f"{source.name}-backup-{timestamp}"
    print(f"Source:      {source}")
    print(f"Destination: {destination}")
    if not args.apply:
        print("Preview only. Add --apply to create the backup.")
        return
    destination_parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    except (OSError, shutil.Error) as exc:
        parser.error(f"backup failed: {exc}")
    print(f"Backup created at {destination}")

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("backup_snapshot", main)
