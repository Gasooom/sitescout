"""The whole Milestone 1 pipeline on SYNTHETIC sources: idempotency and failure isolation."""

import hashlib
import json
from pathlib import Path

import pytest

from sitescout.ingest.layers import metadata_path
from sitescout.ingest.pipeline import (
    OUTPUTS,
    SOURCES,
    Dirs,
    fetch_sources,
    process_sources,
    validate_outputs,
)
from synthetic import VALID_CHARGER_ROWS, charger_csv, install_all_raw, install_raw


def _dirs(dirs, processed: str = "processed") -> Dirs:
    (dirs["work"].parent / processed).mkdir(exist_ok=True)
    return Dirs(
        raw=dirs["raw"],
        processed=dirs["work"].parent / processed,
        chargers_csv=dirs["work"] / "manual" / "chargers.csv",
    )


def _snapshot(directory: Path) -> dict[str, str]:
    """SHA-256 of every file in a directory, by name."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
    }


def _statuses(reports) -> dict[str, str]:
    return {report.source: report.status for report in reports}


@pytest.fixture
def ready(config, dirs):
    install_all_raw(config, dirs["raw"], dirs["work"])
    charger_csv(dirs["work"] / "manual" / "chargers.csv", VALID_CHARGER_ROWS)
    return _dirs(dirs)


def test_processing_twice_gives_byte_identical_outputs(config, ready):
    first = process_sources(config, SOURCES, dirs=ready)
    assert _statuses(first) == dict.fromkeys(SOURCES, "ok")
    before = _snapshot(ready.processed)
    second = process_sources(config, SOURCES, dirs=ready)
    after = _snapshot(ready.processed)
    assert after == before, "a second run changed, added or removed an output file"
    assert [layer.fingerprint for r in first for layer in r.layers] == [
        layer.fingerprint for r in second for layer in r.layers
    ]
    assert [layer.rows for r in first for layer in r.layers] == [
        layer.rows for r in second for layer in r.layers
    ], "a second run appended rows"


def test_outputs_do_not_depend_on_the_working_directory(config, ready, dirs, monkeypatch):
    process_sources(config, SOURCES, dirs=ready)
    monkeypatch.chdir(dirs["work"])
    other = _dirs(dirs, "processed-elsewhere")
    process_sources(config, SOURCES, dirs=other)
    assert _snapshot(other.processed) == _snapshot(ready.processed)


def test_every_source_writes_exactly_its_declared_outputs(config, ready):
    process_sources(config, SOURCES, dirs=ready)
    expected = set()
    for names in OUTPUTS.values():
        for name in names:
            expected.add(f"{name}.meta.json")
            expected.add(f"{name}.tif" if name == "population_worldpop" else f"{name}.parquet")
    assert set(_snapshot(ready.processed)) == expected


def test_metadata_holds_no_processing_timestamp(config, ready):
    process_sources(config, SOURCES, dirs=ready)
    for path in ready.processed.glob("*.meta.json"):
        text = path.read_text(encoding="utf-8")
        assert "processed_at" not in text and "generated_at" not in text, path.name
        for source in json.loads(text).get("sources", []):
            assert source["retrieved_at"], "retrieval time is kept as source metadata"


def test_validate_rereads_every_output(config, ready):
    process_sources(config, SOURCES, dirs=ready)
    reports = validate_outputs(config, SOURCES, dirs=ready)
    assert _statuses(reports) == dict.fromkeys(SOURCES, "ok")
    rows = {layer.name: layer.rows for r in reports for layer in r.layers}
    assert rows["admin_districts"] == 4 and rows["chargers_manual"] == 2


def test_a_failing_source_stops_alone_and_leaves_no_stale_output(config, ready, dirs):
    process_sources(config, SOURCES, dirs=ready)
    # Replace the grid source with a corrupt file (re-fetched as a rolling change is not
    # possible for a fixed source, so the raw directory is rebuilt).
    for path in (dirs["raw"] / "grid_transmission").iterdir():
        path.unlink()
    install_raw(config, dirs["raw"], "grid_transmission", b"SYNTHETIC: not a zip file")
    reports = process_sources(config, SOURCES, dirs=ready)
    statuses = _statuses(reports)
    assert statuses["grid"] == "failed"
    assert all(statuses[s] == "ok" for s in SOURCES if s != "grid")
    failed = next(r for r in reports if r.source == "grid")
    assert "failed validation" in failed.error
    assert not (ready.processed / "grid_transmission_lines.parquet").exists()
    assert not metadata_path(ready.processed, "grid_transmission_lines").exists()


def test_a_missing_charger_csv_is_reported_not_invented(config, ready):
    ready.chargers_csv.unlink()
    reports = process_sources(config, ("chargers",), dirs=ready)
    assert _statuses(reports) == {"chargers": "missing"}
    assert not (ready.processed / "chargers_manual.parquet").exists()
    assert _statuses(validate_outputs(config, ("chargers",), dirs=ready)) == {"chargers": "missing"}


def test_fetch_reuses_downloaded_files_without_the_network(config, ready):
    # Every raw file is already installed, so no download (and no network) is attempted.
    reports = fetch_sources(config, SOURCES, dirs=ready)
    assert _statuses(reports) == dict.fromkeys(("boundaries", "population", "grid", "osm"), "ok")


def test_processing_without_raw_files_reports_them_missing(config, dirs):
    reports = process_sources(config, ("osm",), dirs=_dirs(dirs))
    assert reports[0].status == "failed"
    assert "run the fetch step" in reports[0].error
