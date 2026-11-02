"""Clean a CSV by trimming cells and removing blank or duplicate rows."""
import argparse, csv
from pathlib import Path

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path); p.add_argument("output", type=Path)
    p.add_argument("--delimiter", default=",", help="Single-character delimiter (default: comma)")
    a = p.parse_args()
    if len(a.delimiter) != 1: p.error("delimiter must be one character")
    if a.input.resolve() == a.output.resolve(): p.error("output must be a different file; input is never overwritten")
    try:
        with a.input.open(encoding="utf-8-sig", newline="") as f: rows = list(csv.reader(f, delimiter=a.delimiter))
        if not rows: p.error("input CSV is empty")
        cleaned = [[cell.strip() for cell in row] for row in rows]
        header, data = cleaned[0], cleaned[1:]
        seen, result = set(), []
        for row in data:
            if not any(row): continue
            key = tuple(row)
            if key not in seen: seen.add(key); result.append(row)
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with a.output.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=a.delimiter); writer.writerow(header); writer.writerows(result)
        print(f"Wrote {len(result)} data rows to {a.output} (removed {len(data) - len(result)} blank/duplicate rows).")
    except (OSError, csv.Error) as exc: p.error(str(exc))

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("csv_cleaner", main)
