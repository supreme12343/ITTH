import re

import pytest

from python_tools.regex_workbench import inspect_pattern


def test_regex_workbench_reports_positions_and_named_groups():
    result = inspect_pattern(r"(?P<word>[A-Z][a-z]+)@(?P<domain>[\w.]+)", "Email Ada@school.edu today")
    assert result["count"] == 1
    assert result["matches"][0]["text"] == "Ada@school.edu"
    assert result["matches"][0]["groups"] == {"word": "Ada", "domain": "school.edu"}
    assert result["matches"][0]["start"] == 6


def test_regex_workbench_can_ignore_case_and_caps_displayed_matches():
    result = inspect_pattern("cat", "Cat cat CAT", ignore_case=True, max_matches=2)
    assert result["count"] == 3
    assert len(result["matches"]) == 2
    assert result["truncated"] is True


def test_regex_workbench_rejects_empty_pattern_and_reports_invalid_syntax():
    with pytest.raises(ValueError, match="pattern"):
        inspect_pattern("", "some text")
    with pytest.raises(re.error):
        inspect_pattern("[", "some text")
