import csv

import pytest

from teacher_tools.exit_ticket_analyzer import analyze_responses, report_to_markdown


def write_responses(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["question", "answer", "expected", "student"])
        writer.writeheader()
        writer.writerows(rows)


def test_analyzer_finds_repeated_misconceptions_without_exporting_student_names(tmp_path):
    source = tmp_path / "responses.csv"
    write_responses(source, [
        {"question": "What is 3 times 4?", "answer": "12", "expected": "12", "student": "Ari-Private-019"},
        {"question": "What is 3 times 4?", "answer": "7", "expected": "12", "student": "Bea-Private-029"},
        {"question": "What is 3 times 4?", "answer": "7", "expected": "12", "student": "Cory-Private-039"},
        {"question": "What is 3 times 4?", "answer": " 12 ", "expected": "12", "student": "Devi-Private-049"},
        {"question": "Name a mammal", "answer": "cat", "expected": "cat", "student": "Emi-Private-059"},
    ])

    report = analyze_responses(source)
    multiplication = report["questions"][0]
    assert multiplication["total"] == 4
    assert multiplication["correct"] == 2
    assert multiplication["accuracy_percent"] == 50
    assert multiplication["common_incorrect_answers"] == [("7", 2)]
    markdown = report_to_markdown(report)
    assert "7 (2 responses)" in markdown
    assert "Ari-Private-019" not in markdown
    assert "Bea-Private-029" not in markdown
    assert "revisit" in markdown.lower()


def test_analyzer_counts_blank_responses_as_unanswered(tmp_path):
    source = tmp_path / "blank.csv"
    write_responses(source, [
        {"question": "Define a noun", "answer": "", "expected": "person place or thing", "student": "A"},
    ])
    result = analyze_responses(source)["questions"][0]
    assert result["unanswered"] == 1
    assert result["accuracy_percent"] == 0


def test_analyzer_requires_the_three_anonymous_response_columns(tmp_path):
    source = tmp_path / "missing.csv"
    source.write_text("question,answer\nQ,A\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected"):
        analyze_responses(source)


def test_analyzer_skips_missing_prompt_or_key_and_rejects_conflicting_keys(tmp_path):
    source = tmp_path / "incomplete.csv"
    write_responses(source, [
        {"question": "", "answer": "A", "expected": "A", "student": "hidden"},
        {"question": "Q", "answer": "A", "expected": "", "student": "hidden"},
    ])
    report = analyze_responses(source)
    assert report["skipped_rows"] == 2
    assert report["questions"] == []

    write_responses(source, [
        {"question": "Q", "answer": "A", "expected": "A", "student": "hidden"},
        {"question": "Q", "answer": "B", "expected": "B", "student": "hidden"},
    ])
    with pytest.raises(ValueError, match="more than one expected answer"):
        analyze_responses(source)
