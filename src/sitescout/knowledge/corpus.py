"""Milestone 10, Phase 1 (D-056): the allow-listed documents of the project-knowledge index.

``load_documents`` reads only the documents named in ``knowledge.sources`` of
``config/settings.yaml``, normalizes their line endings, splits them into sections by
Markdown heading and keeps the sections the manifest allows. Nothing here reads any other
file, the network or the environment, and nothing is written.

**Normalization.** Text is UTF-8 with ``\\r\\n`` and ``\\r`` turned into ``\\n`` (and a leading
byte-order mark dropped) and nothing else: no Unicode normalization, no whitespace change.
The same document therefore has the same text, hashes and chunks in a Windows checkout
(CRLF) and a Linux one (LF); ``docs/SPEC.md`` is CRLF in the Windows working copy here while
the git index holds LF.

**Sections.** ATX headings ``#`` to ``###`` start a section; a heading inside a code fence
is not a heading, and ``####`` and deeper stay inside their section. A document must start
with exactly one ``#`` title, and a ``###`` must sit under a ``##``. A section runs from its
heading line to the next heading of level 3 or above. The manifest names ``##`` headings
exactly; a name that matches no heading, or more than one, stops the build, so a renamed
heading can never shrink the corpus silently.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sitescout.config import KnowledgeSource

SPLIT_LEVEL = 3  # headings up to this level start a section


class KnowledgeError(Exception):
    """Base class of every error the knowledge layer raises on purpose."""


class CorpusError(KnowledgeError):
    """A document is missing or malformed, or the manifest does not match it."""


_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def normalize_text(raw: bytes) -> str:
    """The document text the whole knowledge layer works on; see the module docstring."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorpusError(f"not valid UTF-8: {error}") from error
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    """Lowercase, runs of anything but ``a-z0-9`` become one ``-``, trimmed."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def document_slug(path: str) -> str:
    """The file-stem slug used in chunk ids: ``docs/data_sources.md`` -> ``data-sources``."""
    slug = slugify(Path(path).stem)
    if not slug:
        raise CorpusError(f"{path}: the file name has no usable slug")
    return slug


@dataclass(frozen=True, slots=True)
class Unit:
    """One section: ``text[start:end]`` is its heading line and its own body."""

    level: int
    title: str
    path: tuple[str, ...]  # titles from the "#" title down to this heading
    section: str | None  # the "##" heading it belongs to; None for the "#" preamble
    start: int
    end: int
    line: int  # 1-based line of the heading


@dataclass(frozen=True, slots=True)
class Document:
    """A normalized document and the sections the manifest keeps, in file order."""

    source: KnowledgeSource
    text: str
    sha256: str
    units: tuple[Unit, ...]


def fence_closes(line: str, marker: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(marker[0] * len(marker)) and set(stripped) == {marker[0]}


def parse_units(text: str, name: str = "document") -> list[Unit]:
    """Every section of ``text`` (levels 1 to 3), in file order; see the module docstring."""
    starts: list[tuple[int, str, int, int]] = []  # (level, title, offset, line number)
    fence: str | None = None
    offset = 0
    for number, line in enumerate(text.split("\n"), start=1):
        if fence is not None:
            if fence_closes(line, fence):
                fence = None
        elif match := FENCE_OPEN.match(line):
            fence = match.group(1)
        elif (heading := _HEADING.match(line)) and len(heading.group(1)) <= SPLIT_LEVEL:
            starts.append((len(heading.group(1)), heading.group(2), offset, number))
        offset += len(line) + 1
    if fence is not None:
        raise CorpusError(f"{name}: a code fence is never closed")
    if not starts or starts[0][0] != 1 or text[: starts[0][2]].strip():
        raise CorpusError(f"{name}: a document must start with a '#' title")
    if sum(1 for level, *_ in starts if level == 1) != 1:
        raise CorpusError(f"{name}: a document must have exactly one '#' title")

    units: list[Unit] = []
    trail: dict[int, str] = {}
    for index, (level, title, start, line) in enumerate(starts):
        trail = {key: value for key, value in trail.items() if key < level}
        trail[level] = title
        if level == SPLIT_LEVEL and 2 not in trail:
            raise CorpusError(f"{name}: line {line}: '###' heading without a '##' above it")
        end = starts[index + 1][2] if index + 1 < len(starts) else len(text)
        while end > start and text[end - 1] in " \t\n":
            end -= 1
        units.append(
            Unit(
                level=level,
                title=title,
                path=tuple(trail[key] for key in sorted(trail)),
                section=trail.get(2),
                start=start,
                end=end,
                line=line,
            )
        )
    return units


def select_units(source: KnowledgeSource, units: list[Unit]) -> tuple[Unit, ...]:
    """The sections the manifest allows; every heading it names must match exactly one."""
    counts: dict[str, int] = {}
    for unit in units:
        if unit.level == 2:
            counts[unit.title] = counts.get(unit.title, 0) + 1
    named = [*source.include, *source.exclude, *(item.heading for item in source.topics)]
    for heading in dict.fromkeys(named):
        found = counts.get(heading, 0)
        if found == 0:
            raise CorpusError(f"{source.path}: the manifest names no existing '##': {heading!r}")
        if found > 1:
            raise CorpusError(f"{source.path}: {heading!r} matches {found} '##' headings")
    chosen: list[Unit] = []
    for unit in units:
        if unit.level == 1:
            keep = source.preamble
        elif source.include:
            keep = unit.section in source.include
        else:
            keep = unit.section not in source.exclude
        if keep:
            chosen.append(unit)
    return tuple(chosen)


def load_document(root: Path, source: KnowledgeSource) -> Document:
    """Read, normalize and section one allow-listed document under ``root``."""
    path = (root / source.path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise CorpusError(f"{source.path} resolves outside the repository")
    if not path.is_file():
        raise CorpusError(f"{source.path}: the document does not exist")
    try:
        text = normalize_text(path.read_bytes())
    except CorpusError as error:
        raise CorpusError(f"{source.path}: {error}") from error
    units = select_units(source, parse_units(text, source.path))
    return Document(source=source, text=text, sha256=sha256_text(text), units=units)


def load_documents(root: Path, sources: tuple[KnowledgeSource, ...]) -> tuple[Document, ...]:
    """Every allow-listed document, in manifest order."""
    return tuple(load_document(root, source) for source in sources)
