"""Milestone 10, Phase 1 (D-056): deterministic Markdown chunks, their ids and metadata.

``build_corpus`` turns the allow-listed documents (``sitescout.knowledge.corpus``) into
chunks. No randomness, clock, model or network is involved, so the same files and settings
always give the same chunks, ids, metadata and fingerprint.

**Chunks are exact slices.** A chunk's ``text`` is a contiguous slice of the normalized
document (a test checks it), so a quotation from a chunk is a quotation from the document.

**Blocks are atomic.** A section is cut into blocks: a paragraph, a code fence, a table (its
consecutive ``|`` lines) or a top-level list item with everything nested or continued under
it. A block is never split. A section's heading line and its first block stay together.
Blocks are packed in order into chunks of at most ``chunk.max_chars`` characters; a block
that alone is longer stays whole. A part shorter than ``chunk.min_chars`` joins the part
after it (a short last part joins the one before it). Any chunk that ends up longer than
``chunk.max_chars`` this way is flagged ``oversize``. There is no overlap. A section that
fits is one chunk; a section with no body text is skipped.

**Ids.** ``kb/<document>/<section>`` with ``#<part>`` added only when a section yields more
than one chunk. The document is the file-stem slug (``data-sources``, ``spec``); the section
is the slugs of the ``##`` and ``###`` titles joined by ``/``, ``d-046`` for a decision, and
``intro`` for the preamble. A duplicate id stops the build; nothing is suffixed silently.
An id changes only if one of its own headings is renamed or its section changes its number
of parts.

**Metadata** is derived from the text and the manifest only. ``milestone`` is set only where
the section says so ("Milestone N" in its heading, or in a decision's Date line) and is
otherwise null, never inferred. ``decision_id`` is the ``D-nnn`` in the section's own heading
when it names exactly one.

**Fingerprint.** SHA-256 of a canonical JSON document holding the chunker version, the
manifest, the chunk sizes, the milestone topics and every chunk's id, metadata and content
hash. Line numbers and whole-file hashes are left out, so editing a part of a document that
is not indexed never changes it.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from sitescout.config import KnowledgeSettings
from sitescout.knowledge.corpus import (
    FENCE_OPEN,
    CorpusError,
    Document,
    Unit,
    document_slug,
    fence_closes,
    load_documents,
    sha256_text,
    slugify,
)

CHUNKER_VERSION = "chunker-v1"

_LIST_ITEM = re.compile(r"^(?:[-*+]|\d{1,9}[.)])[ \t]")
_INDENTED = re.compile(r"^[ \t]+\S")
_DEEP_HEADING = re.compile(r"^#{4,6}[ \t]")
_DECISION_TITLE = re.compile(r"^D-(\d{3}):")
_DECISION_ID = re.compile(r"\bD-\d{3}\b")
_MILESTONE = re.compile(r"\bMilestone (\d+)\b")


class Chunk(BaseModel):
    """One chunk of a document, with its metadata (see the module docstring)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    source_path: str
    doc_type: str
    topic: str
    section_path: tuple[str, ...]
    heading_level: int
    decision_id: str | None
    milestone: int | None
    part: int
    parts: int
    oversize: bool
    char_count: int
    line_start: int
    content_sha256: str
    source_sha256: str
    text: str

    def search_text(self) -> str:
        """What the lexical index reads: the section titles, then the chunk text."""
        return " ".join(self.section_path) + "\n" + self.text


def split_blocks(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The atomic blocks of ``text[start:end]`` as (start, end) offsets, in order."""
    lines: list[tuple[int, str]] = []
    offset = start
    for line in text[start:end].split("\n"):
        lines.append((offset, line))
        offset += len(line) + 1

    blocks: list[tuple[int, int]] = []
    kind: str | None = None
    block_start = block_end = 0

    def close() -> None:
        nonlocal kind
        if kind is not None:
            blocks.append((block_start, block_end))
            kind = None

    index = 0
    while index < len(lines):
        offset, line = lines[index]
        line_end = offset + len(line)
        if fence := FENCE_OPEN.match(line):
            close()
            last = index + 1
            while last < len(lines) and not fence_closes(lines[last][1], fence.group(1)):
                last += 1
            last = min(last, len(lines) - 1)
            blocks.append((offset, lines[last][0] + len(lines[last][1])))
            index = last + 1
            continue
        if not line.strip():
            if kind == "list":
                following = next((rest for _, rest in lines[index + 1 :] if rest.strip()), None)
                if following is not None and _INDENTED.match(following):
                    index += 1  # a blank line inside a list item, continued by an indented line
                    continue
            close()
            index += 1
            continue
        starts_block = (
            kind is None
            or line.startswith("|") != (kind == "table")
            or _LIST_ITEM.match(line) is not None
            or _DEEP_HEADING.match(line) is not None
        )
        if starts_block:
            close()
            if line.startswith("|"):
                kind = "table"
            elif _LIST_ITEM.match(line):
                kind = "list"
            else:
                kind = "paragraph"
            block_start = offset
        block_end = line_end
        index += 1
    close()
    return blocks


def pack_blocks(
    blocks: Sequence[tuple[int, int]], max_chars: int, min_chars: int
) -> list[tuple[int, int]]:
    """Pack blocks into (start, end) chunk spans; see the module docstring."""
    items = list(blocks)
    if not items:
        return []
    if len(items) > 1:
        items[:2] = [(items[0][0], items[1][1])]  # the heading line and the first block
    parts: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for start, end in items:
        if current is not None and end - current[0] > max_chars:
            parts.append(current)
            current = (start, end)
        else:
            current = (current[0] if current else start, end)
    assert current is not None
    parts.append(current)

    merged: list[tuple[int, int]] = []
    carry: int | None = None
    for index, (start, end) in enumerate(parts):
        if carry is not None:
            start, carry = carry, None
        if end - start < min_chars and index < len(parts) - 1:
            carry = start
            continue
        merged.append((start, end))
    if len(merged) > 1 and merged[-1][1] - merged[-1][0] < min_chars:
        merged[-2:] = [(merged[-2][0], merged[-1][1])]
    return merged


def _decision_id(title: str) -> str | None:
    found = set(_DECISION_ID.findall(title))
    return found.pop() if len(found) == 1 else None


def _milestone(title: str, unit_text: str) -> int | None:
    match = _MILESTONE.search(title)
    if match is None and _DECISION_TITLE.match(title):
        date = next((ln for ln in unit_text.split("\n") if ln.startswith("- **Date:**")), "")
        match = _MILESTONE.search(date)
    return int(match.group(1)) if match else None


def _section_id(unit: Unit) -> str:
    if unit.level == 1:
        return "intro"
    if unit.level == 2 and (decision := _DECISION_TITLE.match(unit.title)):
        return f"d-{decision.group(1)}"
    parts = [slugify(title) for title in unit.path[1:]]
    if not all(parts):
        raise CorpusError(f"the heading {unit.title!r} has no usable slug")
    return "/".join(parts)


def chunk_document(document: Document, settings: KnowledgeSettings) -> list[Chunk]:
    """Every chunk of one document's allowed sections, in file order."""
    text, source = document.text, document.source
    topics = {item.heading: item.topic for item in source.topics}
    milestone_topics = {item.milestone: item.topic for item in settings.milestone_topics}
    line_starts = [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]
    slug = document_slug(source.path)
    chunks: list[Chunk] = []
    for unit in document.units:
        unit_text = text[unit.start : unit.end]
        if not unit_text.partition("\n")[2].strip():
            continue  # a heading with no body text of its own
        parts = pack_blocks(
            split_blocks(text, unit.start, unit.end),
            settings.chunk.max_chars,
            settings.chunk.min_chars,
        )
        milestone = _milestone(unit.title, unit_text)
        topic = topics.get(unit.section) if unit.section else None
        if topic is None and milestone in milestone_topics:
            topic = milestone_topics[milestone]
        base = f"kb/{slug}/{_section_id(unit)}"
        for number, (start, end) in enumerate(parts, start=1):
            piece = text[start:end]
            chunks.append(
                Chunk(
                    chunk_id=base if len(parts) == 1 else f"{base}#{number}",
                    source_path=source.path,
                    doc_type=source.doc_type,
                    topic=topic or source.topic,
                    section_path=unit.path,
                    heading_level=unit.level,
                    decision_id=_decision_id(unit.title),
                    milestone=milestone,
                    part=number,
                    parts=len(parts),
                    oversize=len(piece) > settings.chunk.max_chars,
                    char_count=len(piece),
                    line_start=bisect.bisect_right(line_starts, start),
                    content_sha256=sha256_text(piece),
                    source_sha256=document.sha256,
                    text=piece,
                )
            )
    return chunks


@dataclass(frozen=True)
class Corpus:
    """The chunks of every allow-listed document, in manifest and file order."""

    chunks: tuple[Chunk, ...]
    fingerprint: str
    by_id: dict[str, Chunk]

    def vocabulary(self) -> dict[str, tuple[str | int, ...]]:
        """The values each filterable metadata field takes in this corpus, sorted."""
        fields = ("doc_type", "topic", "milestone", "decision_id")
        return {
            name: tuple(
                sorted(
                    {getattr(chunk, name) for chunk in self.chunks} - {None},
                    key=lambda value: (isinstance(value, str), value),
                )
            )
            for name in fields
        }


def corpus_fingerprint(settings: KnowledgeSettings, chunks: Sequence[Chunk]) -> str:
    """The SHA-256 fingerprint described in the module docstring."""
    document = {
        "chunker": CHUNKER_VERSION,
        "sources": [source.model_dump(mode="json") for source in settings.sources],
        "chunk": settings.chunk.model_dump(mode="json"),
        "milestone_topics": [item.model_dump(mode="json") for item in settings.milestone_topics],
        "chunks": [
            {
                "id": chunk.chunk_id,
                "doc_type": chunk.doc_type,
                "topic": chunk.topic,
                "section_path": list(chunk.section_path),
                "heading_level": chunk.heading_level,
                "decision_id": chunk.decision_id,
                "milestone": chunk.milestone,
                "part": chunk.part,
                "parts": chunk.parts,
                "oversize": chunk.oversize,
                "char_count": chunk.char_count,
                "content_sha256": chunk.content_sha256,
            }
            for chunk in chunks
        ],
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_corpus(settings: KnowledgeSettings, root: Path) -> Corpus:
    """Read the allow-listed documents under ``root`` and chunk them."""
    documents = load_documents(root, settings.sources)
    slugs = [document_slug(document.source.path) for document in documents]
    if len(set(slugs)) != len(slugs):
        raise CorpusError(f"two documents share a slug: {sorted(slugs)}")
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, settings))
    by_id: dict[str, Chunk] = {}
    for chunk in chunks:
        if chunk.chunk_id in by_id:
            raise CorpusError(f"duplicate chunk id {chunk.chunk_id!r}: rename a heading")
        by_id[chunk.chunk_id] = chunk
    if not chunks:
        raise CorpusError("the knowledge corpus is empty")
    return Corpus(chunks=chunks, fingerprint=corpus_fingerprint(settings, chunks), by_id=by_id)


def verify_corpus(settings: KnowledgeSettings, root: Path) -> Corpus:
    """Build the corpus and check its invariants against the documents themselves.

    Every chunk must be an exact slice of its document that starts at its ``line_start``,
    carry the right size, hash and flag, come from an allow-listed document, and, within a
    section, follow its neighbour without overlap. Raises ``CorpusError`` on the first
    violation; returns the verified corpus.
    """
    corpus = build_corpus(settings, root)
    documents = {doc.source.path: doc for doc in load_documents(root, settings.sources)}
    previous: Chunk | None = None
    for chunk in corpus.chunks:
        document = documents.get(chunk.source_path)
        if document is None:
            raise CorpusError(f"{chunk.chunk_id}: {chunk.source_path} is not allow-listed")
        lines = document.text.split("\n")
        if not "\n".join(lines[chunk.line_start - 1 :]).startswith(chunk.text):
            raise CorpusError(f"{chunk.chunk_id}: its text is not the document's text at its line")
        if chunk.char_count != len(chunk.text) or chunk.content_sha256 != sha256_text(chunk.text):
            raise CorpusError(f"{chunk.chunk_id}: its size or hash does not match its text")
        if chunk.source_sha256 != document.sha256:
            raise CorpusError(f"{chunk.chunk_id}: its source hash does not match the document")
        if chunk.oversize != (chunk.char_count > settings.chunk.max_chars):
            raise CorpusError(f"{chunk.chunk_id}: its oversize flag is wrong")
        if previous is not None and previous.section_path == chunk.section_path:
            same_unit = (
                previous.source_path == chunk.source_path and chunk.part == previous.part + 1
            )
            if not same_unit or chunk.line_start <= previous.line_start + previous.text.count("\n"):
                raise CorpusError(f"{chunk.chunk_id}: it overlaps or skips its neighbour")
        previous = chunk
    return corpus
