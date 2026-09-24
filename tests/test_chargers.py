"""The hand-filled charger CSV: schema, malformed rows, a missing file and provenance columns."""

import json

import pytest

from sitescout.ingest import DataValidationError, SourceMissingError
from sitescout.ingest.chargers import charger_id, process_chargers, read_charger_csv
from sitescout.ingest.layers import layer_path, metadata_path, read_layer
from synthetic import CHARGER_HEADER, VALID_CHARGER_ROWS, charger_csv


def _problems(config, dirs, rows, header=CHARGER_HEADER) -> str:
    path = charger_csv(dirs["work"] / "chargers.csv", rows, header)
    with pytest.raises(DataValidationError) as raised:
        read_charger_csv(path, config.settings)
    return str(raised.value)


def test_a_valid_csv_becomes_a_point_layer_without_provenance_names(config, dirs):
    path = charger_csv(dirs["work"] / "chargers.csv", VALID_CHARGER_ROWS)
    result = process_chargers(config.settings, path, dirs["processed"])
    assert result.status == "ok" and result.rows == 2
    layer = read_layer("chargers_manual", dirs["processed"], config.settings)
    assert list(layer.columns) == [
        "charger_id",
        "source_row",
        "source_url",
        "date_retrieved",
        "geometry",
    ]
    assert "operator_public_name" not in layer.columns and "name" not in layer.columns
    assert layer.crs.to_epsg() == 4326
    assert sorted(layer["source_row"]) == [2, 3]
    assert (layer.geometry.x.round(4).tolist(), layer.geometry.y.round(4).tolist()) in [
        ([30.05, 30.06], [-1.95, -1.96]),
        ([30.06, 30.05], [-1.96, -1.95]),
    ]


def test_provenance_names_never_reach_processed_files(config, dirs):
    path = charger_csv(dirs["work"] / "chargers.csv", VALID_CHARGER_ROWS)
    process_chargers(config.settings, path, dirs["processed"])
    for output in (
        layer_path(dirs["processed"], "chargers_manual"),
        metadata_path(dirs["processed"], "chargers_manual"),
    ):
        content = output.read_bytes()
        assert b"SYNTHETIC Op" not in content
        assert b"SYNTHETIC Charger" not in content


def test_a_missing_csv_is_reported_as_missing_and_nothing_is_invented(config, dirs):
    result = process_chargers(config.settings, dirs["work"] / "absent.csv", dirs["processed"])
    assert result.status == "missing" and result.rows == 0
    assert not layer_path(dirs["processed"], "chargers_manual").exists()
    metadata = json.loads(metadata_path(dirs["processed"], "chargers_manual").read_text())
    assert metadata["status"] == "missing"
    assert "No charger records were created" in metadata["reason"]
    with pytest.raises(SourceMissingError, match="unavailable"):
        read_layer("chargers_manual", dirs["processed"], config.settings)


def test_a_csv_removed_after_an_earlier_run_leaves_no_stale_layer(config, dirs):
    path = charger_csv(dirs["work"] / "chargers.csv", VALID_CHARGER_ROWS)
    process_chargers(config.settings, path, dirs["processed"])
    path.unlink()
    process_chargers(config.settings, path, dirs["processed"])
    assert not layer_path(dirs["processed"], "chargers_manual").exists()


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("name,lat,lon,source_url,date_retrieved", "missing columns: ['operator_public_name']"),
        (CHARGER_HEADER + ",notes", "unexpected columns: ['notes']"),
        (CHARGER_HEADER + ",lat", "duplicated columns: ['lat']"),
    ],
    ids=["missing-column", "unexpected-column", "duplicated-column"],
)
def test_the_header_must_match_the_configured_columns(config, dirs, header, message):
    rows = [row + ",x" for row in VALID_CHARGER_ROWS] if header.count(",") > 5 else []
    assert message in _problems(config, dirs, rows or VALID_CHARGER_ROWS, header)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("A,-1.95,30.05,https://example.org/a,2026-01-01", "5 fields, expected 6"),
        (",-1.95,30.05,https://example.org/a,2026-01-01,", "name is blank"),
        ("A,,30.05,https://example.org/a,2026-01-01,", "lat is blank"),
        ("A,south,30.05,https://example.org/a,2026-01-01,", "lat 'south' is not a number"),
        ("A,-95,30.05,https://example.org/a,2026-01-01,", "lat '-95' is out of range"),
        ("A,-1.29,36.82,https://example.org/a,2026-01-01,", "outside the Rwanda envelope"),
        ("A,30.05,-1.95,https://example.org/a,2026-01-01,", "(30.05, -1.95) is outside"),
        ("A,-1.95,30.05,example.org/a,2026-01-01,", "is not an http(s) URL"),
        ("A,-1.95,30.05,https://example.org/a,01/02/2026,", "is not a YYYY-MM-DD date"),
        ("A,-1.95,30.05,https://example.org/a,2026-02-30,", "is not a YYYY-MM-DD date"),
        ("A,nan,30.05,https://example.org/a,2026-01-01,", "lat 'nan' is out of range"),
    ],
    ids=[
        "short-row",
        "blank-name",
        "blank-lat",
        "text-lat",
        "lat-out-of-range",
        "outside-rwanda",
        "swapped-lat-lon",
        "bad-url",
        "bad-date-format",
        "impossible-date",
        "nan-lat",
    ],
)
def test_malformed_rows_are_rejected_with_their_line_number(config, dirs, row, message):
    problems = _problems(config, dirs, [VALID_CHARGER_ROWS[0], row])
    assert "line 3:" in problems
    assert message in problems


def test_every_bad_row_is_reported_at_once(config, dirs):
    problems = _problems(
        config, dirs, ["A,x,30,https://e.org,2026-01-01,", "B,-1.9,y,https://e.org,2026-01-01,"]
    )
    assert "line 2:" in problems and "line 3:" in problems


def test_duplicate_coordinates_are_rejected(config, dirs):
    duplicate = VALID_CHARGER_ROWS[0].replace("SYNTHETIC Charger A", "SYNTHETIC Copy")
    assert "same coordinates as line 2" in _problems(
        config, dirs, [VALID_CHARGER_ROWS[0], duplicate]
    )


def test_a_header_only_or_empty_file_is_rejected(config, dirs):
    assert "no charger rows" in _problems(config, dirs, [])
    path = dirs["work"] / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(DataValidationError, match="the file is empty"):
        read_charger_csv(path, config.settings)


def test_operator_public_name_may_be_blank_but_stays_provenance_only(config):
    csv_settings = config.settings.sources.charger_csv
    assert csv_settings.provenance_only_columns == ("operator_public_name",)
    assert "operator_public_name" in csv_settings.columns


def test_charger_ids_are_deterministic_from_coordinates():
    assert charger_id(-1.95, 30.05) == charger_id(-1.95000001, 30.05)
    assert charger_id(-1.95, 30.05) != charger_id(-1.95, 30.06)
    assert charger_id(-1.95, 30.05).startswith("csv-")


def test_a_utf8_bom_from_spreadsheet_tools_is_accepted(config, dirs):
    path = dirs["work"] / "chargers.csv"
    path.write_text("﻿" + "\n".join([CHARGER_HEADER, *VALID_CHARGER_ROWS]) + "\n", encoding="utf-8")
    assert len(read_charger_csv(path, config.settings)) == 2
