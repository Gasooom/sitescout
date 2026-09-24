"""The repository is public: everything git could commit must be safe to publish."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sitescout.config import PROJECT_ROOT

MAX_FILE_BYTES = 5 * 1024 * 1024  # CLAUDE.md: never commit a file larger than 5 MB
FIXTURES = "tests/fixtures/"  # the only place labelled synthetic test data may live
DATASET_SUFFIXES = {
    ".csv",
    ".parquet",
    ".geoparquet",
    ".gpkg",
    ".geojson",
    ".shp",
    ".dbf",
    ".tif",
    ".tiff",
    ".pbf",
    ".osm",
    ".nc",
    ".xlsx",
    ".xls",
    ".zip",
    ".gz",
    ".7z",
}

GRID_DISCLAIMER = "Actual grid connection feasibility requires utility confirmation."
PUBLIC_DATA_NOTICE = (
    "This analysis uses public data. It does not establish grid approval, land availability, "
    "permitting approval, or commercial viability."
)

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "assigned secret": re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
    ),
}


def _git() -> str:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    return git


def _committable_files() -> list[Path]:
    """Tracked files plus untracked files that are not ignored: all that git could commit."""
    result = subprocess.run(
        [_git(), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return [PROJECT_ROOT / name for name in names if (PROJECT_ROOT / name).is_file()]


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def test_no_committable_file_is_larger_than_5_mb():
    too_big = [_relative(p) for p in _committable_files() if p.stat().st_size > MAX_FILE_BYTES]
    assert too_big == []


@pytest.mark.parametrize(
    "path",
    [
        "data/raw/rwanda-latest.osm.pbf",
        "data/processed/candidates.parquet",
        "data/manual/chargers.csv",
        "data/export/sitescout.json",
        ".venv/pyvenv.cfg",
        ".env",
        ".env.local",
        "run.log",
    ],
)
def test_data_environments_and_secrets_are_ignored(path):
    result = subprocess.run([_git(), "check-ignore", "-q", "--no-index", path], cwd=PROJECT_ROOT)
    assert result.returncode == 0, f"{path} is not ignored by .gitignore"


def test_no_dataset_files_outside_test_fixtures():
    datasets = [
        _relative(p)
        for p in _committable_files()
        if p.suffix.lower() in DATASET_SUFFIXES and not _relative(p).startswith(FIXTURES)
    ]
    assert datasets == []


def test_no_secrets_in_committable_files():
    findings = []
    for path in _committable_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary files are covered by the dataset and size checks
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{_relative(path)}: {label}")
    assert findings == []


def test_readme_contains_the_mandatory_statements():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert GRID_DISCLAIMER in readme
    assert PUBLIC_DATA_NOTICE in readme


def _documents_with_wording_rules() -> list[Path]:
    docs = sorted((PROJECT_ROOT / "docs").glob("*.md"))
    return [PROJECT_ROOT / "README.md", *(path for path in docs if path.name != "SPEC.md")]


@pytest.mark.parametrize("path", _documents_with_wording_rules(), ids=lambda path: path.name)
def test_documents_avoid_prohibited_wording(path):
    text = path.read_text(encoding="utf-8").replace(GRID_DISCLAIMER, "").lower()
    assert "feasibility" not in text, "say 'grid evidence'; only the exact disclaimer may say it"
    assert "accuracy" not in text, "evaluation is a 'retrospective plausibility test'"
