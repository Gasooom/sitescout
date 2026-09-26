"""Milestone 10, Phase 2 (D-057): ``nearby_sites``, which candidates lie near a candidate.

``nearby_sites(data, candidate_id, radius_m)`` lists the candidates within ``radius_m`` metres
of one candidate. It answers a spatial question and nothing else: it does not rank, score,
compare or recommend, and it adds no filter. Comparing a neighbour with the target stays with
``compare_sites``.

**Geometry.** The candidate points in EPSG:32735 that ``AnalystData`` already holds, and the
same ``distance`` call ``network_contribution`` uses for its nearest-network-site distance, so
the two always agree. Nothing is measured in degrees.

**Rules.** ``radius_m`` is a required integer from 1 up to ``optimization.service_radius_m``
(there is no separate radius setting). A candidate exactly ``radius_m`` away is within the
radius (``<=``, as everywhere in SiteScout). The target itself is not listed. Every one of the
300 candidates can be found, whatever its host, eligibility or selection: those are shown as
the flags the pipeline already stores. Neighbours are ordered by distance, then by
``candidate_id``; that is an ordering, not a ranking method.

**Size.** At most ``investigation.nearby_sites.max_results`` neighbours are listed, only to
keep a result small. ``count_within_radius`` is always the full count, ``returned_count`` the
number listed and ``truncated`` says whether they differ.

Site facts are the M9 tools' own records (``find_sites`` and ``get_site``); the only new
values are the radius, the counts and each distance. An unknown target is
``InvestigationError("unknown_site")`` and a bad radius ``invalid_arguments``; a result with
no neighbour is a normal result with a count of 0.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict

from sitescout.analyst.tools import (
    AnalystData,
    AnalystError,
    FindQuery,
    SiteSummary,
    find_sites,
    get_site,
)
from sitescout.evidence import CANDIDATES, EvidenceRecord, distance, kilometres, record
from sitescout.investigation.errors import InvestigationError

# The site fields a neighbour carries: find_sites' own summary fields plus the M6 flags that
# say whether the site was eligible and whether the greedy network chose it.
SITE_FIELDS = (
    "host",
    "district",
    "province",
    "rank",
    "score",
    "profile",
    "confidence",
    "selected_mclp",
    "selected_greedy",
    "selected_top30",
    "eligible",
    "grid_status",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NearbyArgs(_Strict):
    """The arguments a tool registry validates before calling ``nearby_sites``."""

    candidate_id: str = Field(min_length=1)
    radius_m: Annotated[int, Strict(), Field(ge=1)]


class Neighbour(_Strict):
    site: SiteSummary
    distance_record_id: str
    record_ids: list[str]


class NearbySites(_Strict):
    tool: Literal["nearby_sites"] = "nearby_sites"
    candidate_id: str
    radius_m: int
    max_results: int
    count_within_radius: int
    returned_count: int
    truncated: bool
    target: SiteSummary
    target_record_ids: list[str]
    neighbours: list[Neighbour]
    records: list[EvidenceRecord]


def _refuse(message: str) -> InvestigationError:
    return InvestigationError("invalid_arguments", message)


def _check_arguments(data: AnalystData, candidate_id: Any, radius_m: Any) -> None:
    if not isinstance(candidate_id, str) or not candidate_id:
        raise _refuse("candidate_id must be a non-empty string")
    limit = data.config.settings.optimization.service_radius_m
    if isinstance(radius_m, bool) or not isinstance(radius_m, int):
        raise _refuse("radius_m must be an integer number of metres")
    if not 1 <= radius_m <= limit:
        raise _refuse(
            f"radius_m must be between 1 and {limit} (the service radius); got {radius_m}"
        )
    try:
        data.site(candidate_id)
    except AnalystError:
        raise InvestigationError(
            "unknown_site", f"unknown site id {candidate_id!r}; use find_sites to look one up"
        ) from None


def _site(data: AnalystData, candidate_id: str) -> tuple[SiteSummary, list[EvidenceRecord]]:
    """The M9 summary of one candidate and its records for ``SITE_FIELDS``."""
    summary = find_sites(data, FindQuery(candidate_id=candidate_id)).sites[0]
    prefix = f"{candidate_id}/"
    by_field = {r.id.removeprefix(prefix): r for r in get_site(data, candidate_id).records}
    return summary, [by_field[name] for name in SITE_FIELDS]


def nearby_sites(data: AnalystData, candidate_id: str, radius_m: int) -> NearbySites:
    """The candidates within ``radius_m`` metres of ``candidate_id``; see the module docstring."""
    analyst = data
    _check_arguments(analyst, candidate_id, radius_m)
    max_results = analyst.config.settings.investigation.nearby_sites.max_results

    here = analyst.points[candidate_id]
    gaps = {
        other: float(here.distance(point))  # the call network_contribution makes
        for other, point in analyst.points.items()
        if other != candidate_id
    }
    within = sorted(
        ((gap, other) for other, gap in gaps.items() if gap <= radius_m),
        key=lambda item: (item[0], item[1]),
    )
    shown = within[:max_results]

    target, target_records = _site(analyst, candidate_id)
    label = target.label
    records: list[EvidenceRecord] = list(target_records)
    records.append(
        record(
            f"nearby/{candidate_id}/radius",
            "Search radius",
            "RETRIEVED_FACT",
            "nearby_sites argument",
            "radius_m",
            radius_m,
            kilometres(radius_m),
            "m",
        )
    )
    records.append(
        record(
            f"nearby/{candidate_id}/count_within_radius",
            f"Other candidates within the search radius of {label}",
            "CALCULATED",
            CANDIDATES,
            "count_within_radius",
            len(within),
            str(len(within)),
        )
    )
    records.append(
        record(
            f"nearby/{candidate_id}/returned_count",
            "Neighbours listed in this result",
            "CALCULATED",
            CANDIDATES,
            "returned_count",
            len(shown),
            str(len(shown)),
        )
    )
    neighbours = []
    for gap, other in shown:
        summary, site_records = _site(analyst, other)
        distance_record = record(
            f"nearby/{candidate_id}/{other}/distance",
            f"Distance from {label} ({candidate_id}) to {summary.label} ({other})",
            "CALCULATED",
            CANDIDATES,
            "distance_m",
            gap,
            distance(gap),
            "m",
        )
        records.extend([*site_records, distance_record])
        neighbours.append(
            Neighbour(
                site=summary,
                distance_record_id=distance_record.id,
                record_ids=[r.id for r in site_records],
            )
        )
    return NearbySites(
        candidate_id=candidate_id,
        radius_m=radius_m,
        max_results=max_results,
        count_within_radius=len(within),
        returned_count=len(shown),
        truncated=len(within) > len(shown),
        target=target,
        target_record_ids=[r.id for r in target_records],
        neighbours=neighbours,
        records=records,
    )
