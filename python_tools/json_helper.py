"""Validate, pretty-print, or minify a JSON file."""
import argparse, json
from pathlib import Path

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path); p.add_argument("-o", "--output", type=Path, help="Output file; omit to print result")
    p.add_argument("--minify", action="store_true", help="Output compact JSON instead of indented JSON")
    a = p.parse_args()
    try: data = json.loads(a.input.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc: p.error(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    except OSError as exc: p.error(str(exc))
    result = json.dumps(data, ensure_ascii=False, separators=(",", ":") if a.minify else None, indent=None if a.minify else 2) + "\n"
    if a.output:
        if a.input.resolve() == a.output.resolve(): p.error("output must differ from input; input is never overwritten")
        try: a.output.write_text(result, encoding="utf-8")
        except OSError as exc: p.error(str(exc))
        print(f"Valid JSON written to {a.output}")
    else: print(result, end="")

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("json_helper", main)
