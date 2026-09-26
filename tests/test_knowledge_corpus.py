"""Milestone 10, Phase 1: the allow-listed documents, their normalization and their sections."""

import pytest
from pydantic import ValidationError

from knowledge_support import (
    EXPECTED_SOURCES,
    GUIDE,
    GUIDE_SOURCE,
    settings,
    synthetic_root,
    write_docs,
)
from sitescout.config import KnowledgeSource, load_config
from sitescout.knowledge import CorpusError, build_corpus
from sitescout.knowledge.chunking import verify_corpus
from sitescout.knowledge.corpus import (
    document_slug,
    load_document,
    normalize_text,
    parse_units,
    select_units,
    sha256_text,
    slugify,
)


def _source(**changes) -> KnowledgeSource:
    return KnowledgeSource.model_validate({**GUIDE_SOURCE, **changes})


# --- Normalization -----------------------------------------------------------------------------


def test_line_endings_and_a_byte_order_mark_are_normalized_and_nothing_else():
    raw = "﻿# T\r\n\r\nλ × 2  \r\nold mac\rline".encode()
    assert normalize_text(raw) == "# T\n\nλ × 2  \nold mac\nline"


def test_text_that_is_not_utf8_is_refused():
    with pytest.raises(CorpusError, match="UTF-8"):
        normalize_text(b"\xff\xfe\x00")


def test_crlf_and_lf_give_the_same_hash():
    assert sha256_text(normalize_text(b"a\r\nb\r\n")) == sha256_text(normalize_text(b"a\nb\n"))


@pytest.mark.parametrize(
    ("path", "slug"),
    [
        ("docs/data_sources.md", "data-sources"),
        ("docs/SPEC.md", "spec"),
        ("README.md", "readme"),
        ("docs/decisions.md", "decisions"),
    ],
)
def test_document_slugs(path, slug):
    assert document_slug(path) == slug


def test_a_file_name_without_a_slug_is_refused():
    with pytest.raises(CorpusError, match="no usable slug"):
        document_slug("docs/___.md")


def test_slugify_keeps_ascii_words_and_drops_the_rest():
    assert slugify("1. Percentile points (D-040)") == "1-percentile-points-d-040"
    assert slugify("`pop_1km`, `pop_5km`") == "pop-1km-pop-5km"
    assert slugify("λ Σ") == ""


# --- Sections ----------------------------------------------------------------------------------


def test_sections_are_exact_slices_that_start_at_their_heading():
    units = parse_units(GUIDE)
    assert [(u.level, u.title) for u in units] == [
        (1, "Guide"),
        (2, "Alpha section"),
        (3, "Detail one"),
        (3, "Detail two"),
        (2, "Beta section"),
        (2, "Gamma (Milestone 4)"),
        (2, "Empty section"),
        (2, "Hidden section"),
    ]
    for unit in units:
        text = GUIDE[unit.start : unit.end]
        assert text.startswith("#") and text == text.rstrip()
        assert GUIDE.split("\n")[unit.line - 1] == text.split("\n")[0]
    detail = next(u for u in units if u.title == "Detail one")
    assert detail.path == ("Guide", "Alpha section", "Detail one")
    assert detail.section == "Alpha section"
    assert units[0].section is None


def test_a_section_runs_to_the_next_heading_of_level_three_or_above():
    units = {u.title: u for u in parse_units(GUIDE)}
    alpha = GUIDE[units["Alpha section"].start : units["Alpha section"].end]
    assert alpha == "## Alpha section\n\nAlpha body text with the word alpha and beta."


def test_a_heading_inside_a_code_fence_is_not_a_heading():
    titles = [u.title for u in parse_units(GUIDE)]
    assert "not a heading" not in titles
    tilde = "# T\n\n~~~\n## no\n~~~\n\n## Yes\n\ntext\n"
    assert [u.title for u in parse_units(tilde)] == ["T", "Yes"]


def test_a_fence_needs_a_closing_fence_of_the_same_kind():
    text = "# T\n\n````\n```\n## still inside\n````\n\n## Out\n\nx\n"
    assert [u.title for u in parse_units(text)] == ["T", "Out"]


def test_deeper_headings_stay_inside_their_section():
    text = "# T\n\n## A\n\n#### deep\n\nbody\n"
    units = parse_units(text)
    assert [u.title for u in units] == ["T", "A"]
    assert "#### deep" in text[units[1].start : units[1].end]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no title\n\n## A\n", "must start with a '#' title"),
        ("text\n\n# T\n", "must start with a '#' title"),
        ("## A\n\nx\n", "must start with a '#' title"),
        ("# T\n\n# Again\n", "exactly one '#' title"),
        ("# T\n\n### Orphan\n\nx\n", "without a '##' above it"),
        ("# T\n\n```\nnever closed\n", "never closed"),
        ("", "must start with a '#' title"),
    ],
)
def test_malformed_documents_are_refused(text, message):
    with pytest.raises(CorpusError, match=message):
        parse_units(text, "docs/x.md")


# --- Choosing sections -------------------------------------------------------------------------


def _chosen(source: KnowledgeSource, text: str = GUIDE) -> list[str]:
    return [u.title for u in select_units(source, parse_units(text))]


def test_exclude_keeps_everything_but_the_named_headings():
    chosen = _chosen(_source())
    assert "Hidden section" not in chosen
    assert {"Guide", "Alpha section", "Detail one", "Detail two", "Beta section"} <= set(chosen)


def test_include_keeps_only_the_named_headings_with_their_subsections():
    chosen = _chosen(_source(include=["Alpha section"], exclude=[], preamble=False))
    assert chosen == ["Alpha section", "Detail one", "Detail two"]


def test_the_preamble_is_taken_only_when_asked_for():
    assert "Guide" in _chosen(_source(preamble=True))
    assert "Guide" not in _chosen(_source(preamble=False))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"exclude": ["No such heading"]}, "names no existing"),
        ({"include": ["Detail one"], "exclude": []}, "names no existing"),  # a ###, not a ##
        ({"exclude": [], "topics": [{"heading": "Missing", "topic": "scoring"}]}, "names no"),
    ],
)
def test_a_manifest_heading_that_matches_nothing_stops_the_build(changes, message):
    with pytest.raises(CorpusError, match=message):
        select_units(_source(**changes), parse_units(GUIDE))


def test_a_manifest_heading_that_matches_two_sections_is_ambiguous():
    text = "# T\n\n## Same\n\na\n\n## Same\n\nb\n"
    with pytest.raises(CorpusError, match="matches 2"):
        select_units(_source(exclude=["Same"]), parse_units(text))


# --- Loading -----------------------------------------------------------------------------------


def test_a_missing_document_is_an_error_naming_it(temp_dir):
    with pytest.raises(CorpusError, match=r"docs/guide.md: the document does not exist"):
        load_document(temp_dir, _source())


def test_the_same_document_gives_the_same_hash_and_sections_with_crlf(temp_dir):
    lf, crlf = temp_dir / "lf", temp_dir / "crlf"
    write_docs(lf, {"docs/guide.md": GUIDE})
    write_docs(crlf, {"docs/guide.md": GUIDE}, newline="\r\n")
    a, b = load_document(lf, _source()), load_document(crlf, _source())
    assert a.text == b.text and a.sha256 == b.sha256
    assert [(u.title, u.start, u.end) for u in a.units] == [
        (u.title, u.start, u.end) for u in b.units
    ]


def test_a_document_that_breaks_the_rules_is_reported_with_its_path(temp_dir):
    write_docs(temp_dir, {"docs/guide.md": "no title here\n"})
    with pytest.raises(CorpusError, match=r"docs/guide.md: a document must start"):
        load_document(temp_dir, _source())


def test_two_documents_with_the_same_slug_are_refused(temp_dir):
    first = {**GUIDE_SOURCE, "path": "docs/my_guide.md"}
    second = {**GUIDE_SOURCE, "path": "docs/my-guide.md"}
    write_docs(temp_dir, {"docs/my_guide.md": GUIDE, "docs/my-guide.md": GUIDE})
    with pytest.raises(CorpusError, match="share a slug"):
        build_corpus(settings([first, second]), temp_dir)


# --- The manifest in config ---------------------------------------------------------------------


def test_the_real_manifest_is_exactly_the_approved_allow_list():
    sources = [s.model_dump(mode="json") for s in load_config().settings.knowledge.sources]
    assert sources == EXPECTED_SOURCES


@pytest.mark.parametrize(
    "path",
    [
        "CLAUDE.md",
        "config/settings.md",
        "tests/notes.md",
        "data/export/readme.md",
        "app/notes.md",
        "scripts/notes.md",
        "src/sitescout/notes.md",
        "reports/evaluation.md",
        "reports/analyst_eval.md",
        "reports/briefs/README.md",
        "reports/briefs/cand-3a8fa00f876a.md",
    ],
)
def test_configuration_code_data_reports_and_claude_md_can_never_be_indexed(path):
    with pytest.raises(ValidationError, match="never be indexed"):
        _source(path=path)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"path": "docs/notes.txt"}, "only Markdown"),
        ({"path": "/docs/x.md"}, "relative"),
        ({"path": "../x.md"}, "inside the repository"),
        ({"include": ["A"], "exclude": ["B"]}, "not both"),
        (
            {"include": [], "exclude": ["A"], "topics": [{"heading": "A", "topic": "scoring"}]},
            "excluded",
        ),
        (
            {"include": ["A"], "exclude": [], "topics": [{"heading": "B", "topic": "scoring"}]},
            "included",
        ),
        (
            {
                "topics": [
                    {"heading": "A", "topic": "scoring"},
                    {"heading": "A", "topic": "features"},
                ]
            },
            "more than one topic",
        ),
        ({"doc_type": "report"}, "doc_type"),
        ({"topic": "gossip"}, "topic"),
        ({"unknown": 1}, "Extra inputs"),
    ],
)
def test_a_bad_source_is_refused(changes, message):
    with pytest.raises(ValidationError, match=message):
        _source(**changes)


# --- The real corpus ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_corpus():
    config = load_config()
    return config, verify_corpus(config.settings.knowledge, config.root)


def test_the_real_corpus_builds_and_passes_its_own_checks(real_corpus):
    _, corpus = real_corpus
    assert len(corpus.chunks) > 100
    assert len(corpus.fingerprint) == 64


def test_only_allow_listed_documents_reach_the_corpus(real_corpus):
    _, corpus = real_corpus
    assert {chunk.source_path for chunk in corpus.chunks} == {s["path"] for s in EXPECTED_SOURCES}
    assert all(chunk.chunk_id.startswith("kb/") for chunk in corpus.chunks)


def test_excluded_sections_are_absent_from_the_real_corpus(real_corpus):
    _, corpus = real_corpus
    excluded = {
        "docs/scoring.md": ["Results on 2026-09-25"],
        "docs/features.md": ["Data status on 2026-09-25"],
        "docs/architecture.md": ["Repository layout"],
        "docs/data_sources.md": ["Rebuilding `data/`", "Processed layers"],
        "docs/SPEC.md": ["1. Product", "2. Data sources", "10. Export and front end"],
        "README.md": ["Setup", "Milestones", "Licence"],
    }
    for path, headings in excluded.items():
        titles = {
            chunk.section_path[1]
            for chunk in corpus.chunks
            if chunk.source_path == path and len(chunk.section_path) > 1
        }
        assert not titles & set(headings), path


def test_the_real_corpus_holds_no_claude_md_no_report_and_no_brief(real_corpus):
    _, corpus = real_corpus
    denied = ("CLAUDE", "reports/", "config/", "tests/", "data/", "app/", "scripts/", "src/")
    assert not any(chunk.source_path.startswith(denied) for chunk in corpus.chunks)
    assert not any(chunk.text.startswith("# Site Evidence Brief") for chunk in corpus.chunks)


def test_the_real_fingerprint_is_the_same_for_crlf_copies(real_corpus, temp_dir):
    config, corpus = real_corpus
    settings_ = config.settings.knowledge
    for source in settings_.sources:
        raw = (config.root / source.path).read_bytes().decode("utf-8")
        write_docs(temp_dir, {source.path: raw.replace("\r\n", "\n")}, newline="\r\n")
    assert build_corpus(settings_, temp_dir).fingerprint == corpus.fingerprint


def test_the_synthetic_corpus_builds_from_a_directory(temp_dir):
    synthetic_root(temp_dir)
    corpus = build_corpus(settings(), temp_dir)
    assert corpus.fingerprint == build_corpus(settings(), temp_dir).fingerprint
    assert not any("Hidden" in chunk.text for chunk in corpus.chunks)
