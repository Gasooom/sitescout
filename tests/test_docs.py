"""Documentation stays in step with the code: every layer, column, source URL and decision."""

import re

import pytest

from sitescout.config import PROJECT_ROOT, load_config
from sitescout.ingest.acquire import raw_sources
from sitescout.ingest.layers import LAYERS
from sitescout.ingest.raster import POPULATION_LAYER

DATA_SOURCES = (PROJECT_ROOT / "docs" / "data_sources.md").read_text(encoding="utf-8")
DECISIONS = (PROJECT_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")


def _section(name: str) -> str:
    """The data_sources.md section for one processed layer, up to the next heading."""
    match = re.search(rf"^### `{name}`\n(.*?)(?=^#)", DATA_SOURCES, re.S | re.M)
    assert match, f"docs/data_sources.md has no '### `{name}`' section"
    return match.group(1)


@pytest.mark.parametrize("name", sorted(LAYERS))
def test_every_processed_layer_and_column_is_documented(name):
    section = _section(name)
    for column in LAYERS[name].column_names:
        assert f"`{column}`" in section, f"{name}.{column} is not documented"


def test_the_population_raster_is_documented():
    section = _section(POPULATION_LAYER)
    for fact in ("EPSG:4326", "-99999", "3 arc-seconds", "GeoTIFF"):
        assert fact in section


@pytest.mark.parametrize("source_id", sorted(raw_sources(load_config().settings)))
def test_every_source_url_licence_and_credit_is_documented(source_id):
    download = raw_sources(load_config().settings)[source_id].download
    assert download.url in DATA_SOURCES, source_id
    assert download.licence_url in DATA_SOURCES, source_id


def test_every_decision_cited_in_code_or_config_exists():
    cited = set()
    for path in [
        *(PROJECT_ROOT / "src").rglob("*.py"),
        *(PROJECT_ROOT / "config").glob("*.yaml"),
        *(PROJECT_ROOT / "tests").glob("*.py"),
    ]:
        cited |= set(re.findall(r"\bD-\d{3}\b", path.read_text(encoding="utf-8")))
    recorded = set(re.findall(r"^## (D-\d{3}):", DECISIONS, re.M))
    assert cited - recorded == set()
