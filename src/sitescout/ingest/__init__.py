"""Milestone 1: ingest public sources into validated layers under ``data/processed/``.

Stages: acquire (download to ``data/raw/`` with a manifest), process (validate, clean and
normalise each source offline), and validate (re-read every processed layer). See
``docs/architecture.md`` for the flow and ``docs/data_sources.md`` for the layer schemas.
"""


class IngestError(Exception):
    """Base class for ingestion failures."""


class SourceError(IngestError):
    """A public source could not be retrieved or does not match what was retrieved before."""


class SourceChangedError(SourceError):
    """A fixed-version source returned different bytes than the recorded download."""


class SourceMissingError(IngestError):
    """A source file or processed layer does not exist."""


class DataValidationError(IngestError):
    """Data broke a schema, geometry, CRS or source rule. The message lists every problem."""

    def __init__(self, what: str, problems: list[str]) -> None:
        self.what = what
        self.problems = problems
        lines = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"{what} failed validation:\n{lines}")
