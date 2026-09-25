"""Milestone 7: structured evidence for the selected network sites (SPEC §9; D-047).

Every statement a brief can make is first built here as an evidence record, in SPEC §9's
shape plus an ``id`` and the exact ``display`` text the brief prints:

    {"id": "pop_5km", "claim": "Modelled population within 5 km", "type": "CALCULATED",
     "evidence": {"source": "WorldPop", "metric": "pop_5km", "value": 58240.9,
                  "unit": "people"}, "display": "58,241"}

Types (CLAUDE.md): RETRIEVED_FACT (read from a source: OSM tags, boundaries, dates, config),
CALCULATED (computed by the pipeline), INFERRED (a rule's judgement: confidence), UNKNOWN
(not answerable from public data: missing grid evidence, the universal unknowns). Records
come only from the M1-M6 layers, their metadata and the configuration; nothing is typed in.
Context records hold what all briefs share (radii, the network's coverage, data dates).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from sitescout.config import Config
from sitescout.ingest.layers import metadata_path, read_layer
from sitescout.ingest.metadata import read_json, write_json

EVIDENCE_FILE = "evidence.json"
EvidenceType = Literal["RETRIEVED_FACT", "CALCULATED", "INFERRED", "UNKNOWN"]
COMPONENT_NAMES = {
    "demand": "Demand",
    "access": "Access",
    "host_commercial": "Host / commercial",
    "charging_gap": "Charging gap",
    "grid_evidence": "Grid evidence",
}
OSM, WORLDPOP, BOUNDARIES = "OpenStreetMap", "WorldPop", "geoBoundaries"
CANDIDATES = "SiteScout candidates (Milestone 2)"
SCORING = "SiteScout scoring (Milestone 4)"
NETWORK = "SiteScout network optimization (Milestone 6)"
CONFIG = "SiteScout configuration"


class EvidenceError(Exception):
    """The evidence cannot be built from the project data."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Frozen):
    source: str
    metric: str
    value: str | int | float | bool | None
    unit: str | None = None


class EvidenceRecord(_Frozen):
    id: str
    claim: str
    type: EvidenceType
    evidence: Evidence
    display: str


# --- Display formats: the only way a number reaches a brief --------------------------------


def distance(metres: Any) -> str:
    if metres is None or metres != metres:
        return "none mapped"
    return f"{metres:.0f} m" if metres < 1000 else f"{metres / 1000:.1f} km"


def people(value: float) -> str:
    return f"{round(value):,}"


def one_decimal(value: float) -> str:
    return f"{value:.1f}"


def ratio(value: float) -> str:
    return f"{value:.2f}"


def percent(share: float, decimals: int = 0) -> str:
    return f"{share * 100:.{decimals}f}%"


def kilometres(metres: int) -> str:
    return f"{metres / 1000:g} km"


def text(value: Any) -> str:
    return str(value)


def record(
    id: str,
    claim: str,
    type: EvidenceType,
    source: str,
    metric: str,
    value: Any,
    display: str,
    unit: str | None = None,
) -> EvidenceRecord:
    if hasattr(value, "item"):
        value = value.item()  # numpy scalar -> Python number
    if isinstance(value, float) and value != value:
        value = None  # JSON has no NaN; a missing value is null
    return EvidenceRecord(
        id=id,
        claim=claim,
        type=type,
        evidence=Evidence(source=source, metric=metric, value=value, unit=unit),
        display=display,
    )


# --- Per-site records ----------------------------------------------------------------------

# (id, claim, type, source, column, format, unit) for every value read straight from a column.
Spec = tuple[str, str, EvidenceType, str, str, Callable[[Any], str], str | None]
SITE_SPECS: tuple[Spec, ...] = (
    ("candidate_id", "Candidate", "CALCULATED", CANDIDATES, "candidate_id", text, None),
    ("host_osm_id", "OSM object", "RETRIEVED_FACT", OSM, "host_osm_id", text, None),
    ("district", "District", "RETRIEVED_FACT", BOUNDARIES, "district", text, None),
    ("province", "Province", "RETRIEVED_FACT", BOUNDARIES, "province", text, None),
    ("profile", "Profile", "CALCULATED", SCORING, "profile", text, None),
    ("score", "Overall score (0-100)", "CALCULATED", SCORING, "score", one_decimal, None),
    ("rank", "Rank among the candidates", "CALCULATED", SCORING, "rank", text, None),
    ("marginal_coverage", "Weighted demand only this site covers in the network", "CALCULATED", NETWORK, "marginal_coverage", lambda v: percent(v, 2), None),
    ("confidence", "Confidence level", "INFERRED", SCORING, "confidence", text, None),
    ("confidence_reasons", "Confidence reasons", "INFERRED", SCORING, "confidence_reasons", text, None),
    ("pop_1km", "Modelled population within 1 km", "CALCULATED", WORLDPOP, "pop_1km", people, "people"),
    ("pop_5km", "Modelled population within 5 km", "CALCULATED", WORLDPOP, "pop_5km", people, "people"),
    ("pop_10km", "Modelled population within 10 km", "CALCULATED", WORLDPOP, "pop_10km", people, "people"),
    ("outside_share", "Share of the 10 km circle outside Rwanda", "CALCULATED", BOUNDARIES, "outside_rwanda_share_10km", percent, None),
    ("road_class", "Nearest drivable road class", "RETRIEVED_FACT", OSM, "road_class", text, None),
    ("dist_road", "Distance to the nearest drivable road", "CALCULATED", OSM, "dist_road_m", distance, "m"),
    ("dist_trunk", "Distance to the trunk corridor", "CALCULATED", OSM, "dist_trunk_m", distance, "m"),
    ("poi_1km", "Mapped points of interest within 1 km", "CALCULATED", OSM, "poi_1km", text, None),
    ("poi_3km", "Mapped points of interest within 3 km", "CALCULATED", OSM, "poi_3km", text, None),
    ("dist_charger", "Distance to the nearest known charging site", "CALCULATED", OSM, "dist_charger_m", distance, "m"),
    ("chargers_10km", "Known charging sites within 10 km", "CALCULATED", OSM, "chargers_10km", text, None),
    ("chargers_25km", "Known charging sites within 25 km", "CALCULATED", OSM, "chargers_25km", text, None),
    ("dist_substation", "Distance to the nearest mapped substation", "CALCULATED", OSM, "dist_substation_m", distance, "m"),
    ("dist_line", "Distance to the nearest mapped power line", "CALCULATED", OSM, "dist_line_m", distance, "m"),
    ("grid_completeness", "District grid-mapping completeness (proxy, 1 = national median)", "CALCULATED", OSM, "grid_completeness_ratio", ratio, None),
    ("dist_kigali", "Distance to the Kigali city centre as mapped in OSM", "CALCULATED", OSM, "dist_kigali_cbd_m", distance, "m"),
    ("dist_town", "Distance to the nearest town or city centre", "CALCULATED", OSM, "dist_town_m", distance, "m"),
    ("host_bonus", "Host-type bonus (points)", "CALCULATED", SCORING, "host_bonus", text, None),
    ("road_bonus", "Road-class bonus (points)", "CALCULATED", SCORING, "road_bonus", text, None),
)  # fmt: skip


def site_records(site: pd.Series, config: Config) -> list[EvidenceRecord]:
    """Every evidence record for one selected site, from its joined M2-M6 row."""
    if config.settings.optimization.require_host and site["host_type"] == "none":
        raise EvidenceError(f"{site['candidate_id']} has no host but is in the network")
    grid_missing = site["grid_evidence_status"] == "UNKNOWN"
    records = [
        record(id, claim, kind, source, column, site[column], fmt(site[column]), unit)
        for id, claim, kind, source, column, fmt, unit in SITE_SPECS
    ]
    records += [
        record("host", "Host", "RETRIEVED_FACT", OSM, "host_type", site["host_type"], site["host_name"]),
        record(
            "coordinates", "Coordinates (EPSG:4326)", "RETRIEVED_FACT", OSM, "lat, lon",
            f"{site['lat']}, {site['lon']}", f"{site['lat']:.5f}, {site['lon']:.5f}",
        ),
        record(
            "grid_status",
            "Grid evidence missing: no mapped substation or power line within the radius"
            if grid_missing
            else "Grid evidence: a mapped substation or power line within the radius",
            "UNKNOWN" if grid_missing else "CALCULATED",
            SCORING, "grid_evidence_status", site["grid_evidence_status"],
            "missing" if grid_missing else "mapped nearby",
        ),
    ]  # fmt: skip
    for name, label in COMPONENT_NAMES.items():
        value = float(site[f"component_{name}"])
        records.append(
            record(
                f"component_{name}", f"{label} component (0-100)", "CALCULATED", SCORING,
                f"component_{name}", value, one_decimal(value),
            )
        )  # fmt: skip
    return _unique(records)


# --- Shared context records ----------------------------------------------------------------


def context_records(
    config: Config, processed_dir: Path, network: dict[str, Any]
) -> list[EvidenceRecord]:
    """What every brief shares: radii, the network, data dates, weights, universal unknowns."""
    s = config.settings
    rules, population = s.optimization, s.sources.population
    osm_date = read_json(metadata_path(processed_dir, "osm_pois"))["stats"].get(
        "osm_data_timestamp"
    )
    if not osm_date:
        raise EvidenceError("osm_pois metadata has no osm_data_timestamp; re-run ingestion")
    manual = read_json(metadata_path(processed_dir, "chargers_manual")).get("status", "ok")
    candidates = len(read_layer("candidates", processed_dir, s))
    mclp, top30 = network["selections"]["mclp"], network["selections"]["top30"]
    osm = s.sources.osm.download
    boundaries = s.sources.boundaries.downloads.ADM2
    grid_radius = s.scoring.grid_evidence.missing_radius_m
    records = [
        record("n_sites", "Sites in the network", "RETRIEVED_FACT", CONFIG, "optimization.n_sites", rules.n_sites, str(rules.n_sites)),
        record("candidates", "Candidates scored", "CALCULATED", SCORING, "candidates", candidates, str(candidates)),
        record("service_radius", "Service radius", "RETRIEVED_FACT", CONFIG, "optimization.service_radius_m", rules.service_radius_m, kilometres(rules.service_radius_m), "m"),
        record("grid_radius", "Grid evidence radius", "RETRIEVED_FACT", CONFIG, "scoring.grid_evidence.missing_radius_m", grid_radius, kilometres(grid_radius), "m"),
        record("network_population", "Modelled population within the service radius of the network", "CALCULATED", NETWORK, "population_covered_share", mclp["population_covered_share"], percent(mclp["population_covered_share"], 1)),
        record("top30_population", "The same for the Top-30 by score", "CALCULATED", NETWORK, "population_covered_share", top30["population_covered_share"], percent(top30["population_covered_share"], 1)),
        record("known_sites", "Known charging sites", "CALCULATED", OSM, "known_charging_sites", network["known_charging_sites"], str(network["known_charging_sites"])),
        record("manual_chargers", "Manual charger list", "RETRIEVED_FACT", "data/manual/chargers.csv", "status", manual, "missing" if manual == "missing" else "included"),
        record("osm_date", "OpenStreetMap data up to", "RETRIEVED_FACT", OSM, "osm_data_timestamp", osm_date, osm_date[:10]),
        record("worldpop", "Population source", "RETRIEVED_FACT", WORLDPOP, "release", f"{population.year} {population.release}", f"WorldPop {population.year}, {population.release}"),
        record("osm_licence", "OpenStreetMap licence", "RETRIEVED_FACT", CONFIG, "sources.osm.download.licence", osm.licence, f"{osm.credit}, {osm.licence}"),
        record("worldpop_licence", "WorldPop licence", "RETRIEVED_FACT", CONFIG, "sources.population.download.licence", population.download.licence, population.download.licence),
        record("boundaries_licence", "geoBoundaries licence", "RETRIEVED_FACT", CONFIG, "sources.boundaries.downloads.ADM2.licence", boundaries.licence, boundaries.licence),
    ]  # fmt: skip
    radii = {*s.features.population_radii_m, *s.features.poi_radii_m}
    radii |= set(s.features.charger_count_radii_m)
    for radius in sorted(radii):
        records.append(
            record(f"radius_{radius}", "Feature radius", "RETRIEVED_FACT", CONFIG, "features radii", radius, kilometres(radius), "m")
        )  # fmt: skip
    for profile in ("urban", "corridor"):
        weights = getattr(config.weights.profiles, profile)
        for name in COMPONENT_NAMES:
            value = getattr(weights, name)
            records.append(
                record(f"weight_{profile}_{name}", f"{profile} weight of {name}", "RETRIEVED_FACT", CONFIG, f"profiles.{profile}.{name}", value, f"{value:.2f}")
            )  # fmt: skip
    for i, unknown in enumerate(s.confidence.universal_unknowns):
        records.append(
            record(f"unknown_{i}", unknown, "UNKNOWN", "SPEC §6 universal unknowns", "universal_unknowns", None, unknown)
        )  # fmt: skip
    return _unique(records)


def _unique(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    ids = [r.id for r in records]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        raise EvidenceError(f"duplicate evidence ids: {duplicated}")
    return records


# --- The selected sites --------------------------------------------------------------------


def selected_sites(config: Config, processed_dir: Path) -> pd.DataFrame:
    """The MCLP network sites, one row each with every M2-M6 column, in rank order."""
    s = config.settings

    def table(name: str) -> pd.DataFrame:
        return pd.DataFrame(read_layer(name, processed_dir, s).drop(columns="geometry"))

    network = table("network")
    chosen = network.loc[network["selected_mclp"], ["candidate_id", "marginal_coverage"]]
    if chosen.empty:
        raise EvidenceError("The network layer selects no site; run scripts/optimize.py first")
    scores, features = table("scores_production"), table("features_production")
    shared = [c for c in features.columns if c in scores.columns and c != "candidate_id"]
    candidates = table("candidates")[["candidate_id", "host_name", "lat", "lon"]]
    joined = (
        chosen.merge(scores, on="candidate_id")
        .merge(features.drop(columns=shared), on="candidate_id")
        .merge(candidates, on="candidate_id")
    )
    return joined.sort_values(["rank", "candidate_id"], kind="mergesort", ignore_index=True)


def build_evidence(config: Config, processed_dir: Path) -> dict[str, Any]:
    """Context and per-site evidence for every network site; written to evidence.json."""
    network = read_json(processed_dir / "network.json")
    sites = selected_sites(config, processed_dir)
    evidence = {
        "context": [r.model_dump() for r in context_records(config, processed_dir, network)],
        "sites": {
            row["candidate_id"]: [r.model_dump() for r in site_records(row, config)]
            for _, row in sites.iterrows()
        },
        "order": sites["candidate_id"].tolist(),
    }
    write_json(processed_dir / EVIDENCE_FILE, evidence)
    return evidence
