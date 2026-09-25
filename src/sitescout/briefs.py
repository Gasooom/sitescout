"""Milestone 7: Site Evidence Briefs for the network sites, and the grounding check (SPEC §9).

Each brief answers four questions for one selected site: why it was selected, what evidence
supports it, what is unknown and what to investigate next. It is filled from
``templates/brief.md``, which contains no numbers of its own: every value comes from an
evidence record's ``display`` (``sitescout.evidence``). The risks and next actions are
chosen by fixed rules from the same records. No LLM writes any text.

**Grounding check.** Every number in every brief (and in the index) must appear in the
display text of that site's evidence records or the shared context records, which are
written to ``data/processed/evidence.json``. The share of grounded numbers must reach
``evaluation.grounding_target`` (1.0), or the run stops.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sitescout.candidates import HOST_LABELS
from sitescout.config import Config
from sitescout.evidence import COMPONENT_NAMES, EvidenceRecord, build_evidence
from sitescout.ingest.metadata import write_atomically, write_json

logger = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parent / "templates" / "brief.md"
BRIEFS_DIR = "reports/briefs"
INDEX = "README.md"
GROUNDING_FILE = "grounding.json"
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
PLACEHOLDER = re.compile(r"\{[a-z0-9_]+\}")
NOTICE = (
    "This analysis uses public data. It does not establish grid approval, land availability, "
    "permitting approval, or commercial viability."
)
GRID_DISCLAIMER = "Actual grid connection feasibility requires utility confirmation."


class BriefError(Exception):
    """A brief cannot be written, or a number in it is not grounded."""


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def brief_sections(records: list[EvidenceRecord], context: list[EvidenceRecord]) -> dict[str, Any]:
    """The rule-based parts of a brief, as structured text (D-047).

    Used by the Markdown brief and by the M8 export, so both say exactly the same thing:
    the Opportunity paragraph, the border note, risks, unknowns, next actions and the
    components from strongest to weakest.
    """
    d = {r.id: r.display for r in [*context, *records]}
    by_id = {r.id: r for r in records}
    components = sorted(
        COMPONENT_NAMES,
        key=lambda name: (-by_id[f"component_{name}"].evidence.value, name),
    )
    strongest, second, weakest = components[0], components[1], components[-1]
    grid_missing = by_id["grid_status"].type == "UNKNOWN"
    sparse = "Sparse public-map coverage" in by_id["confidence_reasons"].evidence.value
    outside = by_id["outside_share"].evidence.value > 0
    charger = by_id["dist_charger"].evidence.value
    near_charger = charger is not None and charger <= next(
        r.evidence.value for r in context if r.id == "service_radius"
    )
    host_label = HOST_LABELS[by_id["host"].evidence.value].lower()

    opportunity = (
        f"The exact network optimization selected it as one of {d['n_sites']} sites. "
        f"Without it the network would lose **{d['marginal_coverage']}** of its weighted demand "
        f"coverage. It ranks {d['rank']} of {d['candidates']} candidates by score "
        f"({d['score']}, {d['profile']} profile); its strongest components are "
        f"{COMPONENT_NAMES[strongest].lower()} ({d[f'component_{strongest}']}) and "
        f"{COMPONENT_NAMES[second].lower()} ({d[f'component_{second}']}). Together the "
        f"{d['n_sites']} network sites cover {d['network_population']} of Rwanda's modelled "
        f"population within {d['service_radius']}, against {d['top30_population']} for the "
        f"Top-{d['n_sites']} by score."
    )
    border_note = (
        f" {d['outside_share']} of the {d['radius_10000']} circle lies outside Rwanda, where "
        "population is not counted."
        if outside
        else ""
    )

    risks = []
    if grid_missing:
        risks.append(
            f"No mapped substation or power line within {d['grid_radius']}: grid evidence is "
            "missing, and its component scores 0."
        )
    if sparse:
        risks.append(
            f"Grid mapping in {d['district']} is sparse ({d['grid_completeness']} times the "
            "national median), so missing lines may simply be unmapped."
        )
    if outside:
        risks.append(
            f"{d['outside_share']} of the {d['radius_10000']} circle lies outside Rwanda; "
            "demand there is not counted."
        )
    risks.append(
        f"The charging gap is measured against only {d['known_sites']} known charging sites."
    )
    risks.append(
        f"Weakest component: {COMPONENT_NAMES[weakest].lower()} ({d[f'component_{weakest}']})."
    )

    unknowns = [r.display for r in context if r.type == "UNKNOWN"]
    if grid_missing:
        unknowns.append(
            f"whether any grid infrastructure lies nearby (none is mapped within "
            f"{d['grid_radius']})"
        )

    actions = [
        "Ask the utility about grid connection capacity, transformer capacity and cost here.",
        f"Contact the owner of the {host_label} about land availability and willingness to "
        "host chargers.",
        f"Check permit requirements with the {d['district']} district authorities.",
    ]
    if grid_missing:
        actions.append(
            f"Request the utility's network map for this area: no line or substation is "
            f"mapped within {d['grid_radius']}."
        )
    if sparse:
        actions.append("Verify grid infrastructure on the ground; public mapping here is sparse.")
    if near_charger:
        actions.append(
            f"Visit the known charging site {d['dist_charger']} away to see how it is used."
        )
    if outside:
        actions.append("Estimate cross-border demand, which the population figures leave out.")

    return {
        "opportunity": opportunity,
        "border_note": border_note,
        "risks": risks,
        "unknowns": [u[0].upper() + u[1:] for u in unknowns],
        "actions": actions,
        "components_by_strength": components,
    }


def render_brief(records: list[EvidenceRecord], context: list[EvidenceRecord]) -> str:
    """One Site Evidence Brief, filled from evidence records only."""
    d = {r.id: r.display for r in [*context, *records]}
    profile = next(r for r in records if r.id == "profile").evidence.value
    sections = brief_sections(records, context)
    component_rows = "\n".join(
        f"| {label} | {d[f'component_{name}']} | {d[f'weight_{profile}_{name}']} |"
        for name, label in COMPONENT_NAMES.items()
    )
    values = {
        **d,
        "opportunity": sections["opportunity"],
        "border_note": sections["border_note"],
        "component_rows": component_rows,
        "risks": _bullets(sections["risks"]),
        "unknowns": _bullets(sections["unknowns"]),
        "actions": _bullets(sections["actions"]),
    }
    return TEMPLATE.read_text(encoding="utf-8").format(**values)


def grounding(text: str, records: list[EvidenceRecord]) -> dict[str, Any]:
    """How many numbers in ``text`` appear in the records' display text, and which do not."""
    allowed = set(NUMBER.findall(" ".join(r.display for r in records)))
    numbers = NUMBER.findall(text)
    ungrounded = sorted({n for n in numbers if n not in allowed})
    grounded = sum(n in allowed for n in numbers)
    return {"numbers": len(numbers), "grounded": grounded, "ungrounded": ungrounded}


def render_index(
    briefs: list[tuple[str, list[EvidenceRecord]]], context: list[EvidenceRecord]
) -> str:
    c = {r.id: r.display for r in context}
    rows = []
    for candidate_id, records in briefs:
        d = {r.id: r.display for r in records}
        rows.append(
            f"| {d['rank']} | [{d['host']}]({candidate_id}.md) | {d['province']} | {d['score']} "
            f"| {d['confidence']} | {d['grid_status']} | {d['marginal_coverage']} |"
        )
    return (
        "# Site Evidence Briefs\n\n"
        f"The {c['n_sites']} sites of the exact network, which together cover "
        f"{c['network_population']} of Rwanda's modelled population within "
        f"{c['service_radius']} ({c['top30_population']} for the Top-{c['n_sites']} by score). "
        "Generated by `scripts/briefs.py`; every number is checked against "
        "`data/processed/evidence.json`.\n\n"
        "| Rank | Site | Province | Score | Confidence | Grid evidence | Unique coverage |\n"
        "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n" + NOTICE + "\n"
    )


def run_briefs(config: Config, processed_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    """Build the evidence, write one brief per network site and the index, check grounding."""
    evidence = build_evidence(config, processed_dir)
    context = [EvidenceRecord(**r) for r in evidence["context"]]
    briefs = [
        (cid, [EvidenceRecord(**r) for r in evidence["sites"][cid]]) for cid in evidence["order"]
    ]
    out = out_dir or config.resolve(BRIEFS_DIR)
    texts = {f"{cid}.md": render_brief(records, context) for cid, records in briefs}
    texts[INDEX] = render_index(briefs, context)

    checks = {}
    for name, text in texts.items():
        records = context + (
            dict(briefs)[name[:-3]] if name != INDEX else [r for _, rs in briefs for r in rs]
        )
        checks[name] = grounding(text, records)
    numbers = sum(c["numbers"] for c in checks.values())
    grounded = sum(c["grounded"] for c in checks.values())
    share = grounded / numbers if numbers else 1.0
    result = {
        "briefs": len(briefs),
        "numbers": numbers,
        "grounded": grounded,
        "share": share,
        "target": config.settings.evaluation.grounding_target,
        "files": checks,
    }
    missing_text = [
        n for n, t in texts.items() if n != INDEX and (NOTICE not in t or GRID_DISCLAIMER not in t)
    ]
    if share < result["target"] or missing_text:
        bad = {n: c["ungrounded"] for n, c in checks.items() if c["ungrounded"]}
        raise BriefError(
            f"Grounding {share:.4f} is below the target {result['target']}, or a disclaimer is "
            f"missing ({missing_text}); ungrounded numbers: {bad}"
        )

    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("cand-*.md"):
        if stale.name not in texts:
            stale.unlink()
    for name, text in texts.items():
        write_atomically(out / name, lambda temp, text=text: temp.write_bytes(text.encode("utf-8")))
    write_json(processed_dir / GROUNDING_FILE, result)
    logger.info(
        "Wrote %d briefs and the index to %s; grounding %d of %d numbers (%.1f%%)",
        len(briefs),
        out,
        grounded,
        numbers,
        share * 100,
    )
    return result
