"""app/index.html is a read-only view over the export (SPEC §10; D-048)."""

import json
import re

import pytest

from sitescout.config import PROJECT_ROOT

PAGE = (PROJECT_ROOT / "app" / "index.html").read_text(encoding="utf-8")
EXPORT = PROJECT_ROOT / "data" / "export" / "sitescout.json"


def test_the_page_loads_the_export_by_script_so_it_works_from_disk():
    assert '<script src="../data/export/sitescout.js"></script>' in PAGE
    assert "window.SITESCOUT" in PAGE
    assert "fetch(" not in PAGE and "XMLHttpRequest" not in PAGE


def test_the_page_uses_no_external_resource():
    urls = set(re.findall(r"https?://[^\s\"'<>]+", PAGE))
    assert urls == {"http://www.w3.org/2000/svg"}  # the SVG namespace, not a request


def test_the_page_inserts_text_safely():
    assert "innerHTML" not in PAGE and "outerHTML" not in PAGE


@pytest.mark.skipif(not EXPORT.is_file(), reason="run scripts/export.py first")
def test_no_result_from_the_export_is_written_into_the_page():
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    headline = data["headline"]
    values = {headline["exact_vs_greedy_gap"]}
    for selection in headline["selections"]:
        values |= {selection["population_display"], selection["mean_score_display"]}
    values |= {site["score"] for site in data["sites"] if site["selected_mclp"]}
    values |= {site["unique_coverage"] for site in data["sites"] if site["unique_coverage"]}
    written = sorted(value for value in values if value in PAGE)
    assert written == []
    for text in data["meta"]["disclaimers"]:
        assert text not in PAGE  # disclaimers come from the export too
