from python_tools.source_ledger import assess_source, export_markdown, normalize_entry


def sample_entry(**updates):
    entry = {
        "claim": "Native plants support more pollinators.",
        "title": "Urban pollinator study",
        "publisher": "City University",
        "url": "https://example.edu/pollinators",
        "published_date": "2025-04-10",
        "source_type": "Research paper",
        "evidence": "Field counts across 12 gardens",
        "limitations": "One city, one growing season",
    }
    return {**entry, **updates}


def test_source_assessment_explains_strengths_and_gaps():
    strong = assess_source(normalize_entry(sample_entry()))
    weak = assess_source(normalize_entry(sample_entry(
        publisher="", url="", evidence="", published_date="not a date"
    )))
    assert strong["score"] > weak["score"]
    assert any("publishing organization" in item.lower() for item in weak["gaps"])
    assert any("evidence" in item.lower() for item in weak["gaps"])
    assert any("valid publication date" in item.lower() for item in weak["gaps"])


def test_source_entry_validation_rejects_missing_claim_and_bad_url():
    try:
        normalize_entry(sample_entry(claim="  "))
    except ValueError as error:
        assert "claim" in str(error).lower()
    else:
        raise AssertionError("A blank claim should be rejected")

    try:
        normalize_entry(sample_entry(url="javascript:alert(1)"))
    except ValueError as error:
        assert "http" in str(error).lower()
    else:
        raise AssertionError("Non-web URLs should be rejected")


def test_markdown_export_escapes_table_cells():
    output = export_markdown([sample_entry(title="Study | notes")])
    assert "Study \\| notes" in output
    assert "Study \\| notes" in output
