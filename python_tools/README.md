# Standalone Python tools

These tools are independent CustomTkinter desktop apps that use the shared Campus Suite colors and live theme preference. Open one from the Campus Suite launcher or run `python python_tools/<tool>.py` from the project root. Their command-line interfaces are also available with `python python_tools/<tool>.py --cli --help`.

1. `resume_matcher.py` — compare a resume with a job description and show matched and missing keywords.
2. `job_tracker.py` — keep a local CSV record of applications and update their status.
3. `interview_practice.py` — generate fresh role-specific interview questions in the desktop app; use its optional AI follow-up to review an answer or ask for another question.
4. `duplicate_finder.py` — find identical files by content hash without modifying anything.
5. `csv_cleaner.py` — trim whitespace, remove blank rows, and deduplicate CSV data.
6. `json_helper.py` — validate, pretty-print, or minify JSON files.
7. `backup_snapshot.py` — preview and create a dated backup of a folder.
8. `api_tester.py` — make REST requests and inspect status, timing, headers, and response body.
9. `repo_readiness.py` — check portfolio projects for common sharing and submission essentials.
10. `secret_scanner.py` — find common credential patterns before publishing a project.
11. `source_ledger.py` — organize research claims with evidence, source details, and limitations; export a Markdown research log.
12. `dataset_field_guide.py` — explore CSV fields, missing values, numeric ranges, and common categories without changing the source.
13. `regex_workbench.py` — test regular expressions on local sample text and inspect matches and capture groups without saving data.

The standalone desktop tools include an optional **Ask AI** follow-up that uses Campus Suite's key manager. It sends only the result or selected item shown in that tool and your follow-up prompt, after asking you to confirm. Review the content and remove private information first. The command-line interview mode keeps a small offline question set.

Tools that change files show a preview by default or write to a separate output file. The shared CustomTkinter helper lives in `ctk_tools.py`.
