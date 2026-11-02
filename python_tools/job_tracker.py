"""Add or list job applications in a local CSV file."""
import argparse, csv
from datetime import date
from pathlib import Path

FIELDS = ["date", "company", "role", "status", "link", "notes"]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path("job_applications.csv"), help="CSV path (default: ./job_applications.csv)")
    sub = p.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="Record an application")
    add.add_argument("company"); add.add_argument("role")
    add.add_argument("--status", default="Applied", choices=["Interested", "Applied", "Interview", "Offer", "Rejected", "Withdrawn"])
    add.add_argument("--link", default=""); add.add_argument("--notes", default="")
    sub.add_parser("list", help="List recent applications")
    a = p.parse_args()
    try:
        rows = list(csv.DictReader(a.db.open(encoding="utf-8", newline=""))) if a.db.exists() else []
        if a.action == "add":
            rows.append({"date": date.today().isoformat(), "company": a.company, "role": a.role, "status": a.status, "link": a.link, "notes": a.notes})
            with a.db.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS); writer.writeheader(); writer.writerows(rows)
            print(f"Saved application to {a.db.resolve()}")
        elif not rows:
            print("No applications recorded yet.")
        else:
            for i, row in enumerate(reversed(rows), 1):
                print(f"{i:>3}. {row.get('date','')} | {row.get('company','')} — {row.get('role','')} [{row.get('status','')}]")
                if row.get("link"): print(f"     {row['link']}")
    except (OSError, csv.Error) as exc:
        p.error(str(exc))

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("job_tracker", main)
