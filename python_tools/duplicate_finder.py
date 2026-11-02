"""Find duplicate files by SHA-256 content hash; this tool never deletes files."""
import argparse, hashlib
from collections import defaultdict
from pathlib import Path

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.digest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", type=Path); p.add_argument("--recursive", action="store_true")
    a = p.parse_args()
    if not a.folder.is_dir(): p.error("folder must be an existing directory")
    files = a.folder.rglob("*") if a.recursive else a.folder.iterdir()
    by_size = defaultdict(list)
    for f in files:
        if f.is_file():
            try: by_size[f.stat().st_size].append(f)
            except OSError: pass
    found = 0
    for same_size in by_size.values():
        if len(same_size) < 2: continue
        hashes = defaultdict(list)
        for f in same_size:
            try: hashes[digest(f)].append(f)
            except OSError as exc: print(f"Skipped {f}: {exc}")
        for group in hashes.values():
            if len(group) > 1:
                found += 1; print("\nDuplicate group:")
                for f in group: print(f"  {f.resolve()}")
    print(f"\nFound {found} duplicate group(s). No files were changed.")

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("duplicate_finder", main)
