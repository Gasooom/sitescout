"""Shared helpers for the knowledge-index tests (Milestone 10, Phase 1): synthetic Markdown
corpora written at test time, and the exact allow-list the real configuration must hold."""

from pathlib import Path
from typing import Any

from sitescout.config import KnowledgeSettings

# The approved corpus manifest (D-056), exactly as `model_dump(mode="json")` shows it.
EXPECTED_SOURCES: list[dict[str, Any]] = [
    {
        "path": "docs/scoring.md",
        "doc_type": "method",
        "topic": "scoring",
        "preamble": True,
        "include": [],
        "exclude": ["Results on 2026-09-25"],
        "topics": [{"heading": "Confidence (SPEC §6, D-041)", "topic": "confidence"}],
    },
    {
        "path": "docs/features.md",
        "doc_type": "features",
        "topic": "features",
        "preamble": True,
        "include": [],
        "exclude": ["Data status on 2026-09-25"],
        "topics": [],
    },
    {
        "path": "docs/decisions.md",
        "doc_type": "decision",
        "topic": "project",
        "preamble": True,
        "include": [],
        "exclude": [],
        "topics": [],
    },
    {
        "path": "docs/architecture.md",
        "doc_type": "architecture",
        "topic": "architecture",
        "preamble": True,
        "include": [],
        "exclude": ["Repository layout"],
        "topics": [],
    },
    {
        "path": "docs/data_sources.md",
        "doc_type": "data_sources",
        "topic": "data_sources",
        "preamble": True,
        "include": [
            "Sources",
            "Manual charger list: `data/manual/chargers.csv`",
            "Derived layers",
            "The demo export",
            "Checks every processed layer passes",
            "Sources not used",
            "Where files go",
        ],
        "exclude": [],
        "topics": [],
    },
    {
        "path": "docs/SPEC.md",
        "doc_type": "specification",
        "topic": "project",
        "preamble": False,
        "include": [
            "3. Candidate generation",
            "4. Features",
            "5. Scoring",
            "6. Confidence",
            "7. Evaluation",
            "8. Network optimization (MCLP)",
            "9. Evidence and reports",
        ],
        "exclude": [],
        "topics": [
            {"heading": "3. Candidate generation", "topic": "candidates"},
            {"heading": "4. Features", "topic": "features"},
            {"heading": "5. Scoring", "topic": "scoring"},
            {"heading": "6. Confidence", "topic": "confidence"},
            {"heading": "7. Evaluation", "topic": "evaluation"},
            {"heading": "8. Network optimization (MCLP)", "topic": "optimization"},
            {"heading": "9. Evidence and reports", "topic": "evidence"},
        ],
    },
    {
        "path": "README.md",
        "doc_type": "overview",
        "topic": "project",
        "preamble": False,
        "include": ["What it answers", "What it does not claim", "Independence and data"],
        "exclude": [],
        "topics": [],
    },
]

EXPECTED_MILESTONE_TOPICS = [
    {"milestone": 1, "topic": "ingestion"},
    {"milestone": 2, "topic": "candidates"},
    {"milestone": 3, "topic": "features"},
    {"milestone": 4, "topic": "scoring"},
    {"milestone": 5, "topic": "evaluation"},
    {"milestone": 6, "topic": "optimization"},
    {"milestone": 7, "topic": "evidence"},
    {"milestone": 8, "topic": "export"},
    {"milestone": 9, "topic": "analyst"},
    {"milestone": 10, "topic": "investigation"},
]

GUIDE = """# Guide

Intro paragraph about the guide.

## Alpha section

Alpha body text with the word alpha and beta.

### Detail one

Detail one talks about gamma.

### Detail two

- item one
  nested line

  continuation after a blank line

- item two

## Beta section

| a | b |
|---|---|
| 1 | 2 |

```text
# not a heading

still inside the fence
```

## Gamma (Milestone 4)

Gamma text mentions pop_5km and λ.

## Empty section

## Hidden section

Hidden text.
"""

DECISIONS = """# Decisions

Each decision has an id.

## D-001: First choice

- **Date:** 2026-01-01 (Milestone 2, approved)
- **Decision:** the first thing.

## D-002: Second choice, replacing D-001 and D-003

- **Date:** 2026-01-02
- **Decision:** the second thing.

## D-003: Third choice (Milestone 6)

- **Date:** 2026-01-03
- **Decision:** the third thing.
"""

MILESTONE_TOPICS = [
    {"milestone": 2, "topic": "candidates"},
    {"milestone": 4, "topic": "scoring"},
    {"milestone": 6, "topic": "optimization"},
]

GUIDE_SOURCE: dict[str, Any] = {
    "path": "docs/guide.md",
    "doc_type": "method",
    "topic": "scoring",
    "preamble": True,
    "exclude": ["Hidden section"],
}
DECISIONS_SOURCE: dict[str, Any] = {
    "path": "docs/decisions.md",
    "doc_type": "decision",
    "topic": "project",
    "preamble": False,
}


def settings(
    sources: list[dict[str, Any]] | None = None,
    *,
    max_chars: int = 1500,
    min_chars: int = 20,
    milestone_topics: list[dict[str, Any]] | None = None,
) -> KnowledgeSettings:
    """A SYNTHETIC knowledge configuration; the defaults index GUIDE and DECISIONS."""
    return KnowledgeSettings.model_validate(
        {
            "sources": sources if sources is not None else [GUIDE_SOURCE, DECISIONS_SOURCE],
            "chunk": {"max_chars": max_chars, "min_chars": min_chars},
            "bm25": {"k1": 1.2, "b": 0.75},
            "retrieval": {"max_top_k": 5, "max_query_chars": 200},
            "milestone_topics": milestone_topics or MILESTONE_TOPICS,
        }
    )


def write_docs(root: Path, docs: dict[str, str], *, newline: str = "\n") -> None:
    """Write SYNTHETIC documents under ``root`` (bytes exactly as given, with ``newline``)."""
    for name, text in docs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def synthetic_root(root: Path, **overrides: str) -> Path:
    """``root`` filled with the GUIDE and DECISIONS documents (or the given replacements)."""
    write_docs(root, {"docs/guide.md": GUIDE, "docs/decisions.md": DECISIONS, **overrides})
    return root
