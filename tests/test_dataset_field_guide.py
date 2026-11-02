import csv

from python_tools.dataset_field_guide import analyze_csv, report_to_markdown


def test_csv_profile_summarizes_missing_numeric_and_category_fields(tmp_path):
    source = tmp_path / "class-results.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["student", "score", "year"])
        writer.writerow(["Ari", "10", "2025"])
        writer.writerow(["Bea", "", "2026"])
        writer.writerow(["Ari", "20", "2026"])

    report = analyze_csv(source)
    columns = {column["name"]: column for column in report["columns"]}
    assert report["row_count"] == 3
    assert columns["score"]["missing"] == 1
    assert columns["score"]["kind"] == "Number"
    assert columns["score"]["minimum"] == 10
    assert columns["score"]["maximum"] == 20
    assert columns["score"]["mean"] == 15
    assert columns["student"]["kind"] == "Category / text"
    assert columns["student"]["common_values"][0] == ("Ari", 2)


def test_markdown_export_contains_field_notes():
    report = {"filename": "grades.csv", "row_count": 1, "columns": [
        {"name": "score", "kind": "Number", "filled": 1, "missing": 0,
         "unique": 1, "mean": 92, "minimum": 92, "maximum": 92,
         "common_values": [("92", 1)], "suggestion": "Explore the numeric range"}
    ], "warnings": []}
    markdown = report_to_markdown(report)
    assert "grades.csv" in markdown
    assert "Explore the numeric range" in markdown


def test_header_only_csv_is_an_empty_dataset_not_an_error(tmp_path):
    source = tmp_path / "empty.csv"
    source.write_text("name,score\n", encoding="utf-8")
    report = analyze_csv(source)
    assert report["row_count"] == 0
    assert all(column["kind"] == "Empty" for column in report["columns"])


def test_headers_are_trimmed_without_losing_their_values(tmp_path):
    source = tmp_path / "spaced.csv"
    source.write_text(" score , group \n91,Blue\n", encoding="utf-8")
    report = analyze_csv(source)
    assert [column["name"] for column in report["columns"]] == ["score", "group"]
    assert report["columns"][0]["mean"] == 91
    assert report["columns"][1]["common_values"] == [("Blue", 1)]
