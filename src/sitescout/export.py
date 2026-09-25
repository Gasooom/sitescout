"""Milestone 8: the export the one-page demo reads (SPEC §10; D-048).

``data/export/sitescout.json`` holds everything the page shows, already computed and already
formatted: the network comparison, every candidate (with the evidence and brief sections of
the 30 network sites), the known charging sites, a summary of the evaluation, and simplified
district and province outlines. ``data/export/sitescout.js`` holds the same object as
``window.SITESCOUT = …;`` so ``app/index.html`` also works when opened from disk.

Nothing new is analysed here: every value comes from the M2-M7 outputs, and every number the
page prints arrives as a display string formatted in Python, so the page only draws. The
export is validated by the pydantic models below before either file is written. It is
deterministic except ``meta.generated_at``, which records when it was written.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import pandas as pd
from pydantic import BaseModel, ConfigDict

from sitescout.briefs import BRIEFS_DIR, GRID_DISCLAIMER, NOTICE, brief_sections
from sitescout.config import Config
from sitescout.crs import to_metric, to_storage
from sitescout.evaluation import known_charging_sites
from sitescout.evidence import COMPONENT_NAMES, EvidenceRecord, one_decimal, percent
from sitescout.ingest.layers import metadata_path, read_layer
from sitescout.ingest.metadata import read_json, write_atomically

logger = logging.getLogger(__name__)

INDEPENDENT = "An independent portfolio project built on public data."
MAX_BYTES = 1_000_000  # each export file; far below the 5 MB repository limit
BOUNDARY_TOLERANCE_M = 250  # outline simplification for drawing only (EPSG:32735)
COORDINATE_DECIMALS = 5  # about 1 m


class ExportError(Exception):
    """The export cannot be built or fails its checks."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(_Model):
    name: str
    url: str
    licence: str
    credit: str


class Meta(_Model):
    generated_at: str  # intentionally variable: when this export was written
    data_status: Literal["pipeline", "synthetic"]
    data_date: str
    sources: list[Source]
    licence_notice: str
    disclaimers: list[str]
    config: dict[str, Any]


class Selection(_Model):
    key: Literal["mclp", "greedy", "top30"]
    label: str
    population_share: float  # for bar lengths only
    population_display: str
    districts_display: str
    provinces_display: str
    mean_score_display: str
    by_province: list[tuple[str, str]]


class Headline(_Model):
    question: str
    n_sites: str
    candidates: str
    service_radius: str
    selections: list[Selection]
    exact_vs_greedy_gap: str
    solver_status: str


class Brief(_Model):
    opportunity: str
    border_note: str
    risks: list[str]
    unknowns: list[str]
    actions: list[str]
    components_by_strength: list[str]
    brief_path: str


class Component(_Model):
    key: str
    label: str
    value: float  # 0-100, for bar lengths only
    display: str


class Site(_Model):
    candidate_id: str
    label: str
    host_type: str
    lat: float
    lon: float
    district: str
    province: str
    profile: str
    score: str
    rank: int
    confidence: str
    confidence_reasons: str
    grid_evidence_status: Literal["CALCULATED", "UNKNOWN"]
    components: list[Component]
    selected_mclp: bool
    selected_greedy: bool
    selected_top30: bool
    unique_coverage: str | None
    evidence: list[EvidenceRecord] | None
    brief: Brief | None


class Charger(_Model):
    lat: float
    lon: float


class MapFrame(_Model):
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    lon_scale: float  # cos(mean latitude): x units per y unit, for a true-shape drawing
    ring_radius_deg: float  # the service radius in degrees of latitude, for drawing only


class Evaluation(_Model):
    label: str
    lines: list[str]
    report_path: str


class Export(_Model):
    meta: Meta
    headline: Headline
    weights: dict[str, Any]
    context: list[EvidenceRecord]
    sites: list[Site]
    existing_chargers: list[Charger]
    evaluation: Evaluation
    map: MapFrame
    districts: dict[str, Any]
    provinces: dict[str, Any]


# --- Building blocks ---------------------------------------------------------------------


def _round(value: float) -> float:
    return round(float(value), COORDINATE_DECIMALS)


def _outlines(frame: gpd.GeoDataFrame, config: Config, props: dict[str, str]) -> dict[str, Any]:
    """Simplified outlines as a GeoJSON FeatureCollection, for drawing only."""
    metric = to_metric(frame, config.settings.crs)
    metric["geometry"] = metric.geometry.simplify(BOUNDARY_TOLERANCE_M, preserve_topology=True)
    points = metric.geometry.representative_point()
    storage = to_storage(metric, config.settings.crs)
    storage["geometry"] = storage.geometry.set_precision(10**-COORDINATE_DECIMALS)
    labels = to_storage(gpd.GeoSeries(points, crs=config.settings.crs.metric), config.settings.crs)
    features = []
    for (_, row), label in zip(storage.iterrows(), labels, strict=True):
        properties = {out: str(row[column]) for out, column in props.items()}
        properties["label_lon"] = _round(label.x)
        properties["label_lat"] = _round(label.y)
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": json.loads(gpd.GeoSeries([row.geometry]).to_json())["features"][0][
                    "geometry"
                ],
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _selection(key: str, label: str, summary: dict[str, Any]) -> Selection:
    return Selection(
        key=key,
        label=label,
        population_share=summary["population_covered_share"],
        population_display=percent(summary["population_covered_share"], 1),
        districts_display=str(summary["districts"]),
        provinces_display=str(summary["provinces"]),
        mean_score_display=one_decimal(summary["mean_score"]),
        by_province=[(name, str(count)) for name, count in summary["by_province"].items()],
    )


def _evaluation(processed_dir: Path, config: Config) -> Evaluation:
    results = read_json(processed_dir / "evaluation.json")
    grounding = read_json(processed_dir / "grounding.json")
    a, b = results["backtest"], results["stability"]
    top = max(config.settings.evaluation.backtest.precision_at)
    key = f"precision_at_{top}"
    ours, pop = a["metrics"]["sitescout"][key], a["metrics"]["population_only"][key]
    interval = a["bootstrap"]["sitescout_minus_population"][key]
    lines = [
        f"Backtest against {a['known_charging_sites']} known charging sites: Precision@{top} "
        f"{ours:.3f} for SiteScout, {pop:.3f} for population only; the {top}-candidate "
        f"difference interval [{interval[0]:.3f}, {interval[1]:.3f}] "
        + (
            "includes 0, so the two cannot be told apart."
            if interval[0] <= 0 <= interval[1]
            else "excludes 0."
        ),
        f"Weight stability: Top-{b['top_k']} overlap {b['mean_overlap']:.2f} on average and at "
        f"least {b['min_overlap']:.2f} when any weight changes by {b['perturbation'] * 100:.0f}%.",
        f"Grounding: {grounding['grounded']} of {grounding['numbers']} numbers in the "
        f"{grounding['briefs']} briefs trace to structured evidence.",
    ]
    return Evaluation(label=results["label"], lines=lines, report_path="reports/evaluation.md")


def build_export(config: Config, processed_dir: Path, generated_at: str) -> Export:
    """The whole export, from the M2-M7 outputs, validated by the models above."""
    s = config.settings
    network = read_json(processed_dir / "network.json")
    evidence = read_json(processed_dir / "evidence.json")
    context = [EvidenceRecord(**r) for r in evidence["context"]]
    site_evidence = {
        cid: [EvidenceRecord(**r) for r in rows] for cid, rows in evidence["sites"].items()
    }

    def table(name: str) -> pd.DataFrame:
        return pd.DataFrame(read_layer(name, processed_dir, s).drop(columns="geometry"))

    scores = table("scores_production")
    net = table("network")[
        ["candidate_id", "selected_mclp", "selected_greedy", "selected_top30", "marginal_coverage"]
    ]
    candidates = table("candidates")[["candidate_id", "host_name", "lat", "lon"]]
    rows = scores.merge(net, on="candidate_id").merge(candidates, on="candidate_id")
    rows = rows.sort_values(["rank", "candidate_id"], kind="mergesort")
    if set(site_evidence) != set(rows.loc[rows["selected_mclp"], "candidate_id"]):
        raise ExportError("evidence.json does not match the network; re-run scripts/briefs.py")

    sites = []
    for _, row in rows.iterrows():
        cid, in_network = row["candidate_id"], bool(row["selected_mclp"])
        brief = None
        if in_network:
            sections = brief_sections(site_evidence[cid], context)
            brief = Brief(
                **{**sections, "opportunity": sections["opportunity"].replace("**", "")},
                brief_path=f"{BRIEFS_DIR}/{cid}.md",
            )
        sites.append(
            Site(
                candidate_id=cid,
                label=row["host_name"],
                host_type=row["host_type"],
                lat=_round(row["lat"]),
                lon=_round(row["lon"]),
                district=row["district"],
                province=row["province"],
                profile=row["profile"],
                score=one_decimal(row["score"]),
                rank=int(row["rank"]),
                confidence=row["confidence"],
                confidence_reasons=row["confidence_reasons"],
                grid_evidence_status=row["grid_evidence_status"],
                components=[
                    Component(
                        key=key,
                        label=label,
                        value=round(float(row[f"component_{key}"]), 3),
                        display=one_decimal(row[f"component_{key}"]),
                    )
                    for key, label in COMPONENT_NAMES.items()
                ],
                selected_mclp=in_network,
                selected_greedy=bool(row["selected_greedy"]),
                selected_top30=bool(row["selected_top30"]),
                unique_coverage=percent(row["marginal_coverage"], 2) if in_network else None,
                evidence=site_evidence.get(cid),
                brief=brief,
            )
        )

    chargers, _ = known_charging_sites(processed_dir, config)
    charger_points = to_storage(chargers, s.crs)
    districts = read_layer("admin_districts", processed_dir, s)
    provinces = read_layer("admin_provinces", processed_dir, s)
    west, south, east, north = (float(v) for v in districts.total_bounds)
    mid_lat = (south + north) / 2
    osm_date = read_json(metadata_path(processed_dir, "osm_pois"))["stats"]["osm_data_timestamp"]
    download = s.sources
    sources = [
        Source(name=name, url=d.url, licence=d.licence, credit=d.credit)
        for name, d in (
            ("OpenStreetMap", download.osm.download),
            ("WorldPop", download.population.download),
            ("geoBoundaries", download.boundaries.downloads.ADM2),
        )
    ]
    params = network["parameters"]
    return Export(
        meta=Meta(
            generated_at=generated_at,
            data_status="pipeline",
            data_date=osm_date[:10],
            sources=sources,
            licence_notice=(
                f"The candidate, evidence and charger data here are derived from OpenStreetMap "
                f"({download.osm.download.credit}) and available under the Open Database "
                f"License ({download.osm.download.licence})."
            ),
            disclaimers=[INDEPENDENT, GRID_DISCLAIMER, NOTICE],
            config=config.snapshot()["settings"],
        ),
        headline=Headline(
            question=f"Where should the next {params['n_sites']} EV charging sites go?",
            n_sites=str(params["n_sites"]),
            candidates=str(len(rows)),
            service_radius=f"{params['service_radius_m'] / 1000:g} km",
            selections=[
                _selection("mclp", "Optimized network", network["selections"]["mclp"]),
                _selection("greedy", "Greedy network", network["selections"]["greedy"]),
                _selection(
                    "top30", f"Top-{params['n_sites']} by score", network["selections"]["top30"]
                ),
            ],
            exact_vs_greedy_gap=percent(network["exact_vs_greedy_gap"], 2),
            solver_status=network["solver"]["status"],
        ),
        weights=config.snapshot()["weights"],
        context=context,
        sites=sites,
        existing_chargers=[Charger(lat=_round(p.y), lon=_round(p.x)) for p in charger_points],
        evaluation=_evaluation(processed_dir, config),
        map=MapFrame(
            min_lon=_round(west),
            min_lat=_round(south),
            max_lon=_round(east),
            max_lat=_round(north),
            lon_scale=round(math.cos(math.radians(mid_lat)), 6),
            ring_radius_deg=round(params["service_radius_m"] / 110_574, 6),
        ),
        districts=_outlines(
            districts, config, {"district": "district_name", "province": "province_name"}
        ),
        provinces=_outlines(provinces, config, {"province": "province_name"}),
    )


def serialise(export: Export) -> str:
    """Deterministic JSON text (sorted keys, compact) of the whole export."""
    return json.dumps(
        export.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def run_export(config: Config, processed_dir: Path, out_path: Path | None = None) -> Export:
    """Build, validate and write sitescout.json and sitescout.js."""
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    export = build_export(config, processed_dir, generated_at)
    Export.model_validate(export.model_dump())  # the round trip must validate too
    boundary_kb = len(json.dumps(export.districts).encode("utf-8")) / 1024
    if boundary_kb > config.settings.export.boundary_geojson_max_kb:
        raise ExportError(
            f"The district outlines take {boundary_kb:.0f} KB, above the limit of "
            f"{config.settings.export.boundary_geojson_max_kb} KB"
        )
    text = serialise(export)
    json_path = out_path or config.resolve(config.settings.paths.export_json)
    js_path = json_path.with_suffix(".js")
    contents = {json_path: text + "\n", js_path: f"window.SITESCOUT = {text};\n"}
    for path, body in contents.items():
        size = len(body.encode("utf-8"))
        if size > MAX_BYTES:
            raise ExportError(f"{path.name} would be {size} bytes, above {MAX_BYTES}")
    for path, body in contents.items():
        write_atomically(path, lambda temp, body=body: temp.write_bytes(body.encode("utf-8")))
    logger.info(
        "Wrote %s and %s: %d sites (%d in the network), %d known charging sites, outlines %.0f KB",
        json_path,
        js_path.name,
        len(export.sites),
        sum(site.selected_mclp for site in export.sites),
        len(export.existing_chargers),
        boundary_kb,
    )
    return export
