"""Milestone 9, phase 1: the deterministic, read-only SiteScout Analyst tools (SPEC §11).

``find_sites``, ``get_site``, ``compare_sites``, ``explain_score``, ``network_contribution``
and ``generate_brief`` answer questions about the candidates from the pipeline's own
outputs in ``data/processed/``. They read, never write, and use no model or network.

Every value a tool returns is an evidence record (``sitescout.evidence.EvidenceRecord``)
with a stable id, its display text, its type (RETRIEVED_FACT, CALCULATED, INFERRED or
UNKNOWN) and its source. Ids are ``<candidate_id>/<field>`` for a site,
``context/<field>`` for values every site shares, ``weights/<component>/<feature>`` for
feature weights and ``compare/<a>/<b>/<field>`` for comparison rows, so a later validator
can check any statement against exactly the text it cites.

The tools compute only what the pipeline's own rules define: display formats (M7), the
M6 eligibility rule and spacing distances, and value-neutral comparison relations. They
never produce a winner, a preference, a ranking of their own or an invented reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from sitescout.briefs import BRIEFS_DIR, brief_sections, render_brief
from sitescout.config import Config
from sitescout.crs import to_metric
from sitescout.evidence import (
    COMPONENT_NAMES,
    EvidenceRecord,
    all_sites,
    context_records,
    distance,
    kilometres,
    one_decimal,
    percent,
    record,
    site_records,
)
from sitescout.ingest.metadata import read_json
from sitescout.scoring import percentile_rank

M6, M4, CONFIG = (
    "SiteScout network optimization (Milestone 6)",
    "SiteScout scoring (Milestone 4)",
    "SiteScout configuration",
)
TOOLS = (
    "find_sites",
    "get_site",
    "compare_sites",
    "explain_score",
    "network_contribution",
    "generate_brief",
)


class AnalystError(Exception):
    """A tool was called with an invalid argument, or its inputs are inconsistent."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Loaded data ----------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalystData:
    """The processed outputs the tools read, loaded once and never written."""

    config: Config
    sites: pd.DataFrame  # every candidate with its M2-M6 columns, indexed by candidate_id
    context: list[EvidenceRecord]  # shared records, ids prefixed "context/"
    points: gpd.GeoSeries  # candidate points in the metric CRS, indexed by candidate_id
    percentile: pd.Series  # production score percentile (the M6 eligibility input)
    pct: pd.DataFrame  # the M4 percentile points of every weighted feature

    @classmethod
    def load(cls, config: Config, processed_dir: Path) -> AnalystData:
        sites = all_sites(config, processed_dir)
        network = read_json(processed_dir / "network.json")
        context = [_prefixed(r, "context") for r in context_records(config, processed_dir, network)]
        from sitescout.ingest.layers import read_layer

        scores = read_layer("scores_production", processed_dir, config.settings)
        candidates = read_layer("candidates", processed_dir, config.settings)
        points = to_metric(candidates.set_index("candidate_id").geometry, config.settings.crs)
        percentile = pd.Series(
            percentile_rank(scores["score"].to_numpy(dtype=np.float64)),
            index=scores["candidate_id"].to_numpy(),
        )
        pct_columns = [c for c in scores.columns if c.startswith("pct_")]
        pct = pd.DataFrame(
            scores[pct_columns].to_numpy(), index=scores["candidate_id"], columns=pct_columns
        )
        return cls(
            config=config,
            sites=sites.set_index("candidate_id", drop=False),
            context=context,
            points=points,
            percentile=percentile,
            pct=pct,
        )

    def site(self, candidate_id: str) -> pd.Series:
        if candidate_id not in self.sites.index:
            raise AnalystError(f"Unknown site id {candidate_id!r}; use find_sites to look one up")
        return self.sites.loc[candidate_id]

    def context_record(self, field: str) -> EvidenceRecord:
        return next(r for r in self.context if r.id == f"context/{field}")


def _prefixed(item: EvidenceRecord, prefix: str) -> EvidenceRecord:
    return item.model_copy(update={"id": f"{prefix}/{item.id}"})


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _site_records(data: AnalystData, candidate_id: str) -> list[EvidenceRecord]:
    """The M7 evidence records of any candidate, with its M6 selection flags.

    For a site outside the network the marginal-coverage record is left out: its M7 claim
    ("weighted demand only this site covers in the network") describes network sites only;
    network_contribution states what a non-network site would add.
    """
    row = data.site(candidate_id)
    records = site_records(row, data.config, network_site=False)
    if not row["selected_mclp"]:
        records = [r for r in records if r.id != "marginal_coverage"]
    records += [
        record("selected_mclp", "In the optimized network (exact MCLP)", "CALCULATED", M6, "selected_mclp", bool(row["selected_mclp"]), _yes_no(row["selected_mclp"])),
        record("selected_greedy", "In the greedy network", "CALCULATED", M6, "selected_greedy", bool(row["selected_greedy"]), _yes_no(row["selected_greedy"])),
        record("selected_top30", "Among the Top-30 eligible sites by score", "CALCULATED", M6, "selected_top30", bool(row["selected_top30"]), _yes_no(row["selected_top30"])),
        record("eligible", "Eligible for the network (M6 rule)", "CALCULATED", M6, "eligible", bool(row["eligible"]), _yes_no(row["eligible"])),
    ]  # fmt: skip
    return [_prefixed(r, candidate_id) for r in records]


def _by_field(records: list[EvidenceRecord], candidate_id: str) -> dict[str, EvidenceRecord]:
    return {r.id.removeprefix(f"{candidate_id}/"): r for r in records}


# --- 1. find_sites --------------------------------------------------------------------------


class FindQuery(_Model):
    """Exact filters, combined with AND. Text filters match the stored value exactly."""

    candidate_id: str | None = None
    label: str | None = None
    district: str | None = None
    province: str | None = None
    rank: int | None = Field(default=None, ge=1)
    profile: Literal["urban", "corridor"] | None = None
    confidence: Literal["High", "Medium", "Low"] | None = None
    selected_mclp: bool | None = None
    selected_top30: bool | None = None
    grid_evidence_status: Literal["CALCULATED", "UNKNOWN"] | None = None


class SiteSummary(_Model):
    candidate_id: str
    label: str
    district: str
    province: str
    rank: int
    score: str
    profile: str
    confidence: str
    selected_mclp: bool
    selected_top30: bool
    grid_evidence_status: str
    record_ids: list[str]


class FindResult(_Model):
    tool: Literal["find_sites"] = "find_sites"
    query: FindQuery
    count: int
    sites: list[SiteSummary]
    records: list[EvidenceRecord]


_QUERY_COLUMNS = {
    "candidate_id": "candidate_id",
    "label": "host_name",
    "district": "district",
    "province": "province",
    "rank": "rank",
    "profile": "profile",
    "confidence": "confidence",
    "selected_mclp": "selected_mclp",
    "selected_top30": "selected_top30",
    "grid_evidence_status": "grid_evidence_status",
}


def find_sites(data: AnalystData, query: FindQuery) -> FindResult:
    """The candidates matching every given filter, in rank order."""
    mask = np.ones(len(data.sites), dtype=bool)
    for field, column in _QUERY_COLUMNS.items():
        value = getattr(query, field)
        if value is not None:
            mask &= (data.sites[column] == value).to_numpy(dtype=bool)
    matched = data.sites[mask]
    summaries, records = [], []
    for candidate_id, row in matched.iterrows():
        by_field = _by_field(_site_records(data, candidate_id), candidate_id)
        keep = ["host", "district", "province", "rank", "score", "profile", "confidence", "selected_mclp", "selected_top30", "grid_status"]  # fmt: skip
        chosen = [by_field[f] for f in keep]
        records += chosen
        summaries.append(
            SiteSummary(
                candidate_id=candidate_id,
                label=row["host_name"],
                district=row["district"],
                province=row["province"],
                rank=int(row["rank"]),
                score=by_field["score"].display,
                profile=row["profile"],
                confidence=row["confidence"],
                selected_mclp=bool(row["selected_mclp"]),
                selected_top30=bool(row["selected_top30"]),
                grid_evidence_status=row["grid_evidence_status"],
                record_ids=[r.id for r in chosen],
            )
        )
    return FindResult(query=query, count=len(summaries), sites=summaries, records=records)


# --- 2. get_site ----------------------------------------------------------------------------


class SiteResult(_Model):
    tool: Literal["get_site"] = "get_site"
    candidate_id: str
    label: str
    in_network: bool
    records: list[EvidenceRecord]
    context: list[EvidenceRecord]
    component_record_ids: list[str]
    unknowns: list[str]
    actions: list[str]
    note: str | None


def get_site(data: AnalystData, candidate_id: str) -> SiteResult:
    """Identity, score, rank, profile, confidence, components, evidence, selection flags,
    unknowns and (for network sites) the M7 next actions of one candidate."""
    row = data.site(candidate_id)
    records = _site_records(data, candidate_id)
    in_network = bool(row["selected_mclp"])
    universal = [r for r in data.context if r.type == "UNKNOWN"]
    if in_network:
        sections = _sections(data, candidate_id)
        unknowns, actions, note = sections["unknowns"], sections["actions"], None
    else:
        unknowns = [r.display[0].upper() + r.display[1:] for r in universal]
        actions = []
        note = (
            "Not selected for the optimized network: next actions are generated only for "
            "the network sites (Milestone 7)."
        )
    return SiteResult(
        candidate_id=candidate_id,
        label=row["host_name"],
        in_network=in_network,
        records=records,
        context=universal,
        component_record_ids=[f"{candidate_id}/component_{name}" for name in COMPONENT_NAMES],
        unknowns=unknowns,
        actions=actions,
        note=note,
    )


# --- 3. compare_sites -----------------------------------------------------------------------

NUMERIC_FIELDS = (
    "score", "rank", *(f"component_{n}" for n in COMPONENT_NAMES), "pop_1km", "pop_5km",
    "pop_10km", "outside_share", "dist_road", "dist_trunk", "poi_1km", "poi_3km",
    "dist_charger", "chargers_10km", "chargers_25km", "dist_substation", "dist_line",
    "grid_completeness", "dist_kigali", "dist_town", "host_bonus", "road_bonus",
)  # fmt: skip
CATEGORICAL_FIELDS = (
    "host", "profile", "confidence", "district", "province", "road_class", "grid_status",
    "selected_mclp", "selected_greedy", "selected_top30", "eligible",
)  # fmt: skip
Relation = Literal["equal", "a_greater", "b_greater", "same", "different"]


class ComparisonField(_Model):
    """One field side by side. ``relation`` is value-neutral: it states which stored value is
    numerically greater (or whether categories match), never which site is better."""

    id: str
    field: str
    label: str
    kind: Literal["numeric", "categorical"]
    a_record_id: str
    b_record_id: str
    a_display: str
    b_display: str
    relation: Relation | None  # None when either value is missing
    display: str


class CompareResult(_Model):
    tool: Literal["compare_sites"] = "compare_sites"
    a: str
    b: str
    fields: list[ComparisonField]
    records: list[EvidenceRecord]


def _relation(kind: str, a: Any, b: Any) -> Relation | None:
    if a is None or b is None:
        return None
    if kind == "categorical":
        return "same" if a == b else "different"
    if a == b:
        return "equal"
    return "a_greater" if a > b else "b_greater"


def compare_sites(data: AnalystData, a: str, b: str) -> CompareResult:
    """Two candidates side by side, field by field, with value-neutral relations only."""
    if a == b:
        raise AnalystError("compare_sites needs two different site ids")
    records_a, records_b = _site_records(data, a), _site_records(data, b)
    fa, fb = _by_field(records_a, a), _by_field(records_b, b)
    fields = []
    for kind, names in (("numeric", NUMERIC_FIELDS), ("categorical", CATEGORICAL_FIELDS)):
        for name in names:
            ra, rb = fa[name], fb[name]
            fields.append(
                ComparisonField(
                    id=f"compare/{a}/{b}/{name}",
                    field=name,
                    label=ra.claim if name != "grid_status" else "Grid evidence status",
                    kind=kind,  # type: ignore[arg-type]
                    a_record_id=ra.id,
                    b_record_id=rb.id,
                    a_display=ra.display,
                    b_display=rb.display,
                    relation=_relation(kind, ra.evidence.value, rb.evidence.value),
                    display=f"{ra.display} | {rb.display}",
                )
            )
    return CompareResult(a=a, b=b, fields=fields, records=records_a + records_b)


# --- 4. explain_score -----------------------------------------------------------------------


class FeatureWeight(_Model):
    feature: str
    weight_record_id: str
    points_record_id: str


class ComponentExplanation(_Model):
    component: str
    label: str
    value_record_id: str
    profile_weight_record_id: str
    features: list[FeatureWeight]
    bonus_record_id: str | None


class ScoreExplanation(_Model):
    tool: Literal["explain_score"] = "explain_score"
    candidate_id: str
    method_record_id: str
    components: list[ComponentExplanation]
    records: list[EvidenceRecord]


def explain_score(data: AnalystData, candidate_id: str) -> ScoreExplanation:
    """The existing M4 score of one candidate, broken into the stored parts: components,
    the profile's weights, feature weights, percentile points, bonuses and confidence."""
    row = data.site(candidate_id)
    site = _by_field(_site_records(data, candidate_id), candidate_id)
    s, w = data.config.settings, data.config.weights
    profile = row["profile"]
    method = record(
        "method",
        "How the score is built (SPEC §5)",
        "RETRIEVED_FACT",
        CONFIG,
        "scoring",
        None,
        "score = sum of the profile's component weights x component scores; component = sum "
        "of feature weights x percentile points, plus any bonus, capped at "
        f"{s.scoring.scale_max}; missing grid evidence scores "
        f"{s.scoring.grid_evidence.missing_component_score}",
    )
    records = [_prefixed(method, candidate_id)]
    records += [site[f] for f in ("score", "rank", "profile", "confidence", "confidence_reasons", "grid_status", "host_bonus", "road_bonus")]  # fmt: skip
    components = []
    for name, label in COMPONENT_NAMES.items():
        records.append(site[f"component_{name}"])
        weight = data.context_record(f"weight_{profile}_{name}")
        records.append(weight)
        features = []
        for feature, value in getattr(w.components, name).model_dump().items():
            weight_record = record(f"weights/{name}/{feature}", f"Weight of {feature} within {name}", "RETRIEVED_FACT", CONFIG, f"components.{name}.{feature}", value, f"{value:.2f}")  # fmt: skip
            points = float(data.pct.loc[candidate_id, f"pct_{feature}"])
            points_record = _prefixed(record(f"pct_{feature}", f"Percentile points of {feature} (0-100, higher is better)", "CALCULATED", M4, f"pct_{feature}", points, one_decimal(points)), candidate_id)  # fmt: skip
            records += [weight_record, points_record]
            features.append(
                FeatureWeight(
                    feature=feature,
                    weight_record_id=weight_record.id,
                    points_record_id=points_record.id,
                )
            )
        bonus = {"host_commercial": "host_bonus", "access": "road_bonus"}.get(name)
        components.append(
            ComponentExplanation(
                component=name,
                label=label,
                value_record_id=f"{candidate_id}/component_{name}",
                profile_weight_record_id=weight.id,
                features=features,
                bonus_record_id=f"{candidate_id}/{bonus}" if bonus else None,
            )
        )
    return ScoreExplanation(
        candidate_id=candidate_id,
        method_record_id=f"{candidate_id}/method",
        components=components,
        records=records,
    )


# --- 5. network_contribution ----------------------------------------------------------------

Reason = Literal["no_host", "score_below_eligibility_percentile", "spacing_conflict"]


class NetworkContribution(_Model):
    tool: Literal["network_contribution"] = "network_contribution"
    candidate_id: str
    in_network: bool
    status: Literal["selected", "not_selected"]
    reasons: list[Reason]
    reason_status: Literal["NOT_APPLICABLE", "ESTABLISHED", "UNKNOWN"]
    explanation: str
    nearest_network_site: str | None
    spacing_conflicts: list[str]
    records: list[EvidenceRecord]


def established_reasons(
    *, hostless: bool, require_host: bool, percentile: float, threshold: float, conflicts: bool
) -> list[Reason]:
    """The M6 rules that exclude a site from the network. Nothing else counts as a reason."""
    reasons: list[Reason] = []
    if require_host and hostless:
        reasons.append("no_host")
    if percentile < threshold:
        reasons.append("score_below_eligibility_percentile")
    if conflicts:
        reasons.append("spacing_conflict")
    return reasons


def network_contribution(data: AnalystData, candidate_id: str) -> NetworkContribution:
    """What M6 establishes about one candidate's place in the network, and why a
    non-selected site is out when an M6 rule excludes it. Otherwise the reason is UNKNOWN."""
    row = data.site(candidate_id)
    rules = data.config.settings.optimization
    site = _by_field(_site_records(data, candidate_id), candidate_id)
    in_network = bool(row["selected_mclp"])
    percentile = float(data.percentile[candidate_id])
    hostless = row["host_type"] == "none"
    eligible = percentile >= rules.min_score_percentile and not (rules.require_host and hostless)
    if eligible != bool(row["eligible"]):
        raise AnalystError(
            f"{candidate_id}: the M6 eligibility rule disagrees with the network layer"
        )

    network_ids = [
        cid for cid in data.sites.index[data.sites["selected_mclp"]] if cid != candidate_id
    ]
    here = data.points[candidate_id]
    gaps = pd.Series(
        {cid: float(here.distance(data.points[cid])) for cid in network_ids}
    ).sort_index()
    nearest = str(gaps.idxmin()) if len(gaps) else None
    conflicts = sorted(cid for cid, d in gaps.items() if d < rules.min_spacing_m)
    within = int((gaps <= rules.service_radius_m).sum())

    coverage = float(row["marginal_coverage"])
    records = [site["host"], site["score"], site["rank"], site["selected_mclp"], site["selected_greedy"], site["selected_top30"], site["eligible"]]  # fmt: skip
    records += [
        _prefixed(record("score_percentile", "Production score percentile (the M6 eligibility input)", "CALCULATED", M6, "percentile", percentile, one_decimal(percentile)), candidate_id),
        data.context_record("n_sites"),
        record("rules/min_score_percentile", "Minimum score percentile for eligibility", "RETRIEVED_FACT", CONFIG, "optimization.min_score_percentile", rules.min_score_percentile, str(rules.min_score_percentile)),
        record("rules/require_host", "Sites without a host are never selected", "RETRIEVED_FACT", CONFIG, "optimization.require_host", rules.require_host, _yes_no(rules.require_host)),
        record("rules/min_spacing", "Minimum spacing between network sites", "RETRIEVED_FACT", CONFIG, "optimization.min_spacing_m", rules.min_spacing_m, kilometres(rules.min_spacing_m), "m"),
        data.context_record("service_radius"),
        _prefixed(record(
            "marginal_coverage",
            "Weighted demand only this site covers in the network" if in_network else "Weighted demand this site would add to the network",
            "CALCULATED", M6, "marginal_coverage", coverage, percent(coverage, 2),
        ), candidate_id),
        _prefixed(record("network_sites_within_service_radius", "Other network sites within the service radius", "CALCULATED", M6, "count", within, str(within)), candidate_id),
    ]  # fmt: skip
    if nearest is not None:
        label = data.site(nearest)["host_name"]
        records.append(_prefixed(record("nearest_network_site", "Nearest other network site", "CALCULATED", M6, "candidate_id", nearest, f"{label} ({nearest})"), candidate_id))  # fmt: skip
        records.append(_prefixed(record("nearest_network_site_distance", "Distance to the nearest other network site", "CALCULATED", M6, "distance_m", float(gaps[nearest]), distance(gaps[nearest]), "m"), candidate_id))  # fmt: skip

    if in_network:
        reasons, status, explanation = [], "NOT_APPLICABLE", "Selected by the exact MCLP."
    else:
        reasons = established_reasons(
            hostless=hostless,
            require_host=rules.require_host,
            percentile=percentile,
            threshold=rules.min_score_percentile,
            conflicts=bool(conflicts),
        )
        if reasons:
            status = "ESTABLISHED"
            explanation = "Excluded by an M6 rule: " + ", ".join(reasons) + "."
        else:
            status = "UNKNOWN"
            explanation = (
                "Eligible, with no spacing conflict, but not in the exact MCLP solution. The "
                "solver chooses all sites jointly, so the outputs establish no single reason; "
                "the demand it would add is shown."
            )
    return NetworkContribution(
        candidate_id=candidate_id,
        in_network=in_network,
        status="selected" if in_network else "not_selected",
        reasons=reasons,
        reason_status=status,
        explanation=explanation,
        nearest_network_site=nearest,
        spacing_conflicts=conflicts,
        records=records,
    )


# --- 6. generate_brief ----------------------------------------------------------------------


class BriefResult(_Model):
    tool: Literal["generate_brief"] = "generate_brief"
    candidate_id: str
    in_network: bool
    sections: dict[str, Any] | None
    markdown: str | None
    brief_path: str | None
    records: list[EvidenceRecord]
    note: str | None


def _sections(data: AnalystData, candidate_id: str) -> dict[str, Any]:
    raw = site_records(data.site(candidate_id), data.config)
    context = [r.model_copy(update={"id": r.id.removeprefix("context/")}) for r in data.context]
    return brief_sections(raw, context)


def generate_brief(data: AnalystData, candidate_id: str) -> BriefResult:
    """The M7 brief sections (and Markdown) of a network site, from the M7 code itself."""
    row = data.site(candidate_id)
    records = _site_records(data, candidate_id)
    if not row["selected_mclp"]:
        return BriefResult(
            candidate_id=candidate_id,
            in_network=False,
            sections=None,
            markdown=None,
            brief_path=None,
            records=records,
            note="No Site Evidence Brief: not selected for the optimized network.",
        )
    raw = site_records(row, data.config)
    context = [r.model_copy(update={"id": r.id.removeprefix("context/")}) for r in data.context]
    return BriefResult(
        candidate_id=candidate_id,
        in_network=True,
        sections=brief_sections(raw, context),
        markdown=render_brief(raw, context),
        brief_path=f"{BRIEFS_DIR}/{candidate_id}.md",
        records=records,
        note=None,
    )
