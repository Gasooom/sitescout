"""Downloading raw files: manifests, idempotency and unexpected source changes (no network)."""

import logging
import urllib.error
from datetime import UTC, datetime

import pytest

from sitescout.config import load_config
from sitescout.ingest import (
    DataValidationError,
    SourceChangedError,
    SourceError,
    SourceMissingError,
)
from sitescout.ingest.acquire import fetch, load_raw, manifest_path, raw_path, raw_sources
from sitescout.ingest.metadata import read_manifest
from synthetic import FIXED_TIME, serve

SOURCES = raw_sources(load_config().settings)
DATA = b"SYNTHETIC raw bytes"


def _fetch(source_id, raw_dir, data=DATA, *, refresh=False, headers=None, now=FIXED_TIME):
    return fetch(
        SOURCES[source_id],
        raw_dir,
        refresh=refresh,
        opener=serve(data, headers),
        now=lambda: now,
    )


def test_every_configured_download_is_a_raw_source():
    assert set(SOURCES) == {
        "osm",
        "worldpop",
        "boundaries_adm2",
        "boundaries_adm1",
        "grid_transmission",
    }
    assert SOURCES["osm"].filename == "rwanda-latest.osm.pbf"
    assert SOURCES["osm"].download.versioning == "rolling"
    assert all(SOURCES[s].download.versioning == "fixed" for s in SOURCES if s != "osm"), (
        "every source except the rolling OSM extract names one release"
    )


def test_fetch_writes_the_file_and_a_complete_manifest(temp_dir):
    manifest = _fetch("worldpop", temp_dir, headers={"Last-Modified": "Mon, 04 Aug 2025"})
    assert raw_path(SOURCES["worldpop"], temp_dir).read_bytes() == DATA
    assert read_manifest(manifest_path(SOURCES["worldpop"], temp_dir)) == manifest
    assert manifest.bytes == len(DATA)
    assert manifest.retrieved_at == "2026-01-01T00:00:00+00:00"
    assert manifest.http_last_modified == "Mon, 04 Aug 2025"
    assert manifest.licence == "CC-BY-4.0"
    assert "WorldPop" in manifest.credit


def test_fetch_is_idempotent_and_does_not_download_again(temp_dir):
    first = _fetch("worldpop", temp_dir)

    def no_network(request):
        raise AssertionError("fetch downloaded a file that was already present")

    second = fetch(SOURCES["worldpop"], temp_dir, opener=no_network)
    assert second == first
    assert sorted(p.name for p in (temp_dir / "worldpop").iterdir()) == [
        "rwa_pop_2025_CN_100m_R2025A_v1.tif",
        "source.json",
    ]


def test_a_changed_fixed_release_is_an_error_and_keeps_the_recorded_file(temp_dir):
    _fetch("boundaries_adm2", temp_dir)
    with pytest.raises(SourceChangedError, match="fixed release"):
        _fetch("boundaries_adm2", temp_dir, b"SYNTHETIC changed bytes", refresh=True)
    path, manifest = load_raw(SOURCES["boundaries_adm2"], temp_dir)
    assert path.read_bytes() == DATA
    assert manifest.retrieved_at == "2026-01-01T00:00:00+00:00"


def test_a_changed_rolling_source_is_logged_and_replaced(temp_dir, caplog):
    first = _fetch("osm", temp_dir)
    later = datetime(2026, 2, 1, tzinfo=UTC)
    with caplog.at_level(logging.WARNING):
        second = _fetch("osm", temp_dir, b"SYNTHETIC newer extract", refresh=True, now=later)
    assert "rolling source changed upstream" in caplog.text
    assert second.sha256 != first.sha256
    assert second.retrieved_at == "2026-02-01T00:00:00+00:00"


def test_a_truncated_download_is_rejected_and_leaves_nothing(temp_dir):
    with pytest.raises(SourceError, match="server announced"):
        _fetch("worldpop", temp_dir, headers={"Content-Length": "999"})
    assert not raw_path(SOURCES["worldpop"], temp_dir).exists()
    assert not manifest_path(SOURCES["worldpop"], temp_dir).exists()


def test_an_empty_download_is_rejected(temp_dir):
    with pytest.raises(SourceError, match="empty file"):
        _fetch("worldpop", temp_dir, b"")


def test_http_errors_are_reported_with_the_url(temp_dir):
    def not_found(request):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    with pytest.raises(SourceError, match=r"HTTP 404 Not Found for https://data\.worldpop"):
        fetch(SOURCES["worldpop"], temp_dir, opener=not_found)


def test_a_raw_file_edited_after_download_is_detected(temp_dir):
    _fetch("grid_transmission", temp_dir)
    raw_path(SOURCES["grid_transmission"], temp_dir).write_bytes(b"SYNTHETIC tampered")
    with pytest.raises(DataValidationError, match="differs from the manifest"):
        load_raw(SOURCES["grid_transmission"], temp_dir)


def test_a_missing_raw_file_is_reported(temp_dir):
    with pytest.raises(SourceMissingError, match="run the fetch step"):
        load_raw(SOURCES["osm"], temp_dir)


def test_a_file_without_its_manifest_is_refused(temp_dir):
    path = raw_path(SOURCES["osm"], temp_dir)
    path.parent.mkdir(parents=True)
    path.write_bytes(DATA)
    with pytest.raises(SourceError, match="without its manifest"):
        _fetch("osm", temp_dir)
