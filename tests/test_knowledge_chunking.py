"""Milestone 10, Phase 1: deterministic chunks, ids, metadata and the corpus fingerprint."""

import pytest

from knowledge_support import (
    DECISIONS,
    DECISIONS_SOURCE,
    GUIDE,
    GUIDE_SOURCE,
    MILESTONE_TOPICS,
    settings,
    synthetic_root,
    write_docs,
)
from sitescout.knowledge import CorpusError, build_corpus
from sitescout.knowledge import chunking as chunking_module
from sitescout.knowledge.chunking import pack_blocks, split_blocks, verify_corpus

# --- Blocks ------------------------------------------------------------------------------------


def _blocks(text: str) -> list[str]:
    return [text[start:end] for start, end in split_blocks(text, 0, len(text))]


def test_paragraphs_are_separated_by_blank_lines():
    assert _blocks("## H\n\nfirst line\nsecond line\n\nnext") == [
        "## H",
        "first line\nsecond line",
        "next",
    ]


def test_every_top_level_list_item_is_one_block_with_its_nested_and_continued_lines():
    text = "- a\n- b\n  nested\n\n  continued after a blank\n- c\n\n1. one\n2. two"
    assert _blocks(text) == [
        "- a",
        "- b\n  nested\n\n  continued after a blank",
        "- c",
        "1. one",
        "2. two",
    ]


def test_an_unindented_line_after_a_list_item_continues_it_lazily():
    assert _blocks("- a\nlazy") == ["- a\nlazy"]


def test_a_table_is_one_block_and_ends_at_its_last_row():
    assert _blocks("| a |\n|---|\n| 1 |\ntext after") == ["| a |\n|---|\n| 1 |", "text after"]


def test_a_code_fence_is_one_block_whatever_it_contains():
    text = "intro\n\n```text\n# not a heading\n\n- not a list\n```\nafter"
    assert _blocks(text) == ["intro", "```text\n# not a heading\n\n- not a list\n```", "after"]


def test_a_deeper_heading_starts_its_own_block():
    assert _blocks("para\n#### deep\nmore") == ["para", "#### deep\nmore"]


# --- Packing -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blocks", "max_chars", "min_chars", "expected"),
    [
        ([], 100, 10, []),
        ([(0, 500)], 100, 10, [(0, 500)]),  # a lone block stays whole
        ([(0, 10), (12, 20), (22, 90)], 100, 10, [(0, 90)]),  # everything fits: one chunk
        ([(0, 10), (12, 20), (22, 90)], 60, 5, [(0, 20), (22, 90)]),  # cut between blocks
        ([(0, 10), (12, 200), (202, 210)], 50, 5, [(0, 200), (202, 210)]),  # heading + first block
        ([(0, 5), (7, 10), (12, 50)], 30, 25, [(0, 50)]),  # a short part joins the next
        ([(0, 10), (12, 40), (42, 50)], 45, 15, [(0, 50)]),  # a short last part joins the previous
        ([(0, 10), (12, 30), (32, 60), (62, 90)], 45, 5, [(0, 30), (32, 60), (62, 90)]),
    ],
)
def test_packing(blocks, max_chars, min_chars, expected):
    assert pack_blocks(blocks, max_chars, min_chars) == expected


def test_packed_chunks_never_overlap_and_keep_document_order():
    blocks = [(i * 12, i * 12 + 10) for i in range(30)]
    parts = pack_blocks(blocks, 50, 15)
    assert parts[0][0] == 0 and parts[-1][1] == blocks[-1][1]
    assert all(a[1] < b[0] for a, b in zip(parts, parts[1:], strict=False))


# --- Ids and metadata of the synthetic corpus ----------------------------------------------------

WHOLE_GUIDE = {**GUIDE_SOURCE, "exclude": []}
GOLDEN_IDS = [
    "kb/guide/intro",
    "kb/guide/alpha-section",
    "kb/guide/alpha-section/detail-one",
    "kb/guide/alpha-section/detail-two",
    "kb/guide/beta-section",
    "kb/guide/gamma-milestone-4",
    "kb/decisions/d-001",
    "kb/decisions/d-002",
    "kb/decisions/d-003",
]
# Pinned on purpose: if the chunking algorithm changes, these change and CHUNKER_VERSION must
# be raised, so an old fingerprint can never describe new chunks.
GOLDEN_FINGERPRINT = "fad6740d84a2e884fa1b63ed9a9dd2273fcdfef878df69d535ea02d6c5afc774"
GOLDEN_FINGERPRINT_SMALL = "aa351b4fdc7f14d6bf3a113ba4131305e536e48eb2a901f7c7441adb3beab0df"


@pytest.fixture
def root(temp_dir):
    return synthetic_root(temp_dir)


def _by_id(corpus):
    return corpus.by_id


def test_golden_ids_and_fingerprint(root):
    corpus = build_corpus(settings(), root)
    assert [chunk.chunk_id for chunk in corpus.chunks] == GOLDEN_IDS
    assert corpus.fingerprint == GOLDEN_FINGERPRINT
    small = build_corpus(settings(max_chars=80, min_chars=20), root)
    assert small.fingerprint == GOLDEN_FINGERPRINT_SMALL


def test_every_chunk_is_an_exact_slice_and_the_corpus_verifies(root):
    for max_chars in (30, 80, 200, 1500):
        verify_corpus(settings(max_chars=max_chars, min_chars=10), root)


def test_a_section_is_split_only_when_it_exceeds_the_limit(root):
    small = build_corpus(settings(max_chars=80, min_chars=20), root)
    ids = [chunk.chunk_id for chunk in small.chunks]
    assert "kb/guide/beta-section#1" in ids and "kb/guide/beta-section#2" in ids
    assert "kb/guide/beta-section" not in ids  # a suffix only when a section has several parts
    first, second = small.by_id["kb/guide/beta-section#1"], small.by_id["kb/guide/beta-section#2"]
    assert (first.part, first.parts, second.part, second.parts) == (1, 2, 2, 2)
    assert first.text.startswith("## Beta section") and "| 1 | 2 |" in first.text
    assert second.text.startswith("```text")  # the fence is one block, kept whole


def test_oversize_marks_a_chunk_longer_than_the_limit(root):
    small = build_corpus(settings(max_chars=80, min_chars=20), root)
    flagged = {chunk.chunk_id for chunk in small.chunks if chunk.oversize}
    assert flagged == {"kb/guide/alpha-section/detail-two"}
    assert all(chunk.char_count == len(chunk.text) for chunk in small.chunks)
    assert not any(chunk.oversize for chunk in build_corpus(settings(), root).chunks)


def test_sections_without_body_and_excluded_sections_make_no_chunk(root):
    corpus = build_corpus(settings(), root)
    assert "kb/guide/empty-section" not in corpus.by_id
    assert not any("Hidden" in chunk.text for chunk in corpus.chunks)


def test_the_preamble_and_nested_sections_get_their_own_ids(root):
    corpus = build_corpus(settings(), root)
    intro = corpus.by_id["kb/guide/intro"]
    assert (intro.heading_level, intro.section_path) == (1, ("Guide",))
    detail = corpus.by_id["kb/guide/alpha-section/detail-one"]
    assert (detail.heading_level, detail.section_path) == (
        3,
        ("Guide", "Alpha section", "Detail one"),
    )


def test_a_decision_id_is_taken_only_from_a_heading_naming_exactly_one(root):
    corpus = build_corpus(settings(), root)
    assert corpus.by_id["kb/decisions/d-001"].decision_id == "D-001"
    assert corpus.by_id["kb/decisions/d-002"].decision_id is None  # its heading names three
    assert corpus.by_id["kb/guide/alpha-section"].decision_id is None


def test_a_milestone_is_read_only_where_the_text_states_it(root):
    corpus = build_corpus(settings(), root)
    assert corpus.by_id["kb/decisions/d-001"].milestone == 2  # from the Date line
    assert corpus.by_id["kb/decisions/d-003"].milestone == 6  # from the heading
    assert corpus.by_id["kb/guide/gamma-milestone-4"].milestone == 4
    assert corpus.by_id["kb/decisions/d-002"].milestone is None  # never inferred
    assert corpus.by_id["kb/guide/alpha-section"].milestone is None


def test_topics_follow_manifest_then_milestone_then_source(root):
    corpus = build_corpus(settings(), root)
    assert corpus.by_id["kb/decisions/d-001"].topic == "candidates"  # milestone 2
    assert corpus.by_id["kb/decisions/d-003"].topic == "optimization"  # milestone 6
    assert corpus.by_id["kb/decisions/d-002"].topic == "project"  # the source default
    assert corpus.by_id["kb/guide/gamma-milestone-4"].topic == "scoring"  # milestone 4
    overridden = {
        **GUIDE_SOURCE,
        "topics": [
            {"heading": "Alpha section", "topic": "features"},
            {"heading": "Gamma (Milestone 4)", "topic": "confidence"},
        ],
    }
    corpus = build_corpus(settings([overridden, DECISIONS_SOURCE]), root)
    assert corpus.by_id["kb/guide/alpha-section"].topic == "features"
    assert corpus.by_id["kb/guide/alpha-section/detail-one"].topic == "features"  # inherited
    assert corpus.by_id["kb/guide/gamma-milestone-4"].topic == "confidence"  # beats milestone 4
    assert corpus.by_id["kb/guide/beta-section"].topic == "scoring"


def test_a_milestone_without_a_topic_falls_back_to_the_source(temp_dir):
    text = DECISIONS.replace("(Milestone 6)", "(Milestone 9)")
    corpus = build_corpus(settings(), synthetic_root(temp_dir, **{"docs/decisions.md": text}))
    chunk = corpus.by_id["kb/decisions/d-003"]
    assert (chunk.milestone, chunk.topic) == (9, "project")


def test_doc_type_and_source_hash_come_from_the_manifest_and_the_document(root):
    corpus = build_corpus(settings(), root)
    assert {c.doc_type for c in corpus.chunks if c.source_path == "docs/guide.md"} == {"method"}
    assert {c.doc_type for c in corpus.chunks if c.source_path == "docs/decisions.md"} == {
        "decision"
    }
    assert len({c.source_sha256 for c in corpus.chunks}) == 2


def test_the_vocabulary_lists_only_values_the_corpus_takes(root):
    vocabulary = build_corpus(settings(), root).vocabulary()
    assert vocabulary["doc_type"] == ("decision", "method")
    assert vocabulary["milestone"] == (2, 4, 6)
    assert vocabulary["decision_id"] == ("D-001", "D-003")
    assert vocabulary["topic"] == ("candidates", "optimization", "project", "scoring")


def test_two_sections_with_the_same_id_stop_the_build(temp_dir):
    text = "# T\n\n## A\n\nx\n\n### Same\n\ny\n\n### Same\n\nz\n"
    write_docs(temp_dir, {"docs/guide.md": text})
    with pytest.raises(CorpusError, match="duplicate chunk id 'kb/guide/a/same'"):
        build_corpus(settings([WHOLE_GUIDE]), temp_dir)


def test_the_same_subheading_under_different_sections_is_fine(temp_dir):
    text = "# T\n\n## A\n\nx\n\n### Same\n\ny\n\n## B\n\nx\n\n### Same\n\nz\n"
    write_docs(temp_dir, {"docs/guide.md": text})
    ids = build_corpus(settings([WHOLE_GUIDE]), temp_dir).by_id
    assert "kb/guide/a/same" in ids and "kb/guide/b/same" in ids


def test_a_heading_with_no_usable_slug_stops_the_build(temp_dir):
    write_docs(temp_dir, {"docs/guide.md": "# T\n\n## λ Σ\n\nbody\n"})
    with pytest.raises(CorpusError, match="no usable slug"):
        build_corpus(settings([WHOLE_GUIDE]), temp_dir)


def test_an_empty_corpus_is_an_error(temp_dir):
    write_docs(temp_dir, {"docs/guide.md": "# T\n\n## Hidden section\n\n## Other\n\nx\n"})
    only = {**GUIDE_SOURCE, "preamble": False, "exclude": [], "include": ["Hidden section"]}
    with pytest.raises(CorpusError, match="empty"):
        build_corpus(settings([only]), temp_dir)


# --- Stability and the fingerprint ---------------------------------------------------------------


def _hashes(corpus):
    return {chunk.chunk_id: chunk.content_sha256 for chunk in corpus.chunks}


def test_the_same_files_give_the_same_chunks_and_fingerprint(root):
    first, second = build_corpus(settings(), root), build_corpus(settings(), root)
    assert first.chunks == second.chunks and first.fingerprint == second.fingerprint


def test_crlf_files_give_the_same_chunks_and_fingerprint(temp_dir):
    lf, crlf = temp_dir / "lf", temp_dir / "crlf"
    synthetic_root(lf)
    write_docs(crlf, {"docs/guide.md": GUIDE, "docs/decisions.md": DECISIONS}, newline="\r\n")
    a, b = build_corpus(settings(), lf), build_corpus(settings(), crlf)
    assert a.chunks == b.chunks and a.fingerprint == b.fingerprint


def test_editing_one_section_changes_only_its_hash_and_keeps_every_id(temp_dir):
    before = build_corpus(settings(), synthetic_root(temp_dir / "a"))
    after = build_corpus(
        settings(),
        synthetic_root(
            temp_dir / "b", **{"docs/guide.md": GUIDE.replace("Gamma text", "Gamma prose")}
        ),
    )
    assert list(_hashes(before)) == list(_hashes(after))
    changed = {i for i in _hashes(before) if _hashes(before)[i] != _hashes(after)[i]}
    assert changed == {"kb/guide/gamma-milestone-4"}
    assert before.fingerprint != after.fingerprint


def test_renaming_a_heading_changes_that_id_only(temp_dir):
    renamed = GUIDE.replace("### Detail one", "### Detail first")
    corpus = build_corpus(settings(), synthetic_root(temp_dir, **{"docs/guide.md": renamed}))
    ids = set(corpus.by_id)
    assert "kb/guide/alpha-section/detail-first" in ids
    assert "kb/guide/alpha-section/detail-one" not in ids
    assert set(GOLDEN_IDS) - ids == {"kb/guide/alpha-section/detail-one"}


def test_the_fingerprint_ignores_text_that_is_not_indexed(temp_dir):
    base = build_corpus(settings(), synthetic_root(temp_dir / "a")).fingerprint
    hidden = GUIDE.replace("Hidden text.", "Other hidden words.")
    assert (
        build_corpus(
            settings(), synthetic_root(temp_dir / "b", **{"docs/guide.md": hidden})
        ).fingerprint
        == base
    )
    # Text before an indexed section shifts its line numbers, which are not fingerprinted.
    longer = DECISIONS.replace(
        "Each decision has an id.", "Each decision has an id.\n\nMore.\n\nMore."
    )
    shifted = build_corpus(
        settings(), synthetic_root(temp_dir / "c", **{"docs/decisions.md": longer})
    )
    assert shifted.fingerprint == base
    assert shifted.by_id["kb/decisions/d-001"].line_start > 5


def test_the_fingerprint_changes_with_the_manifest_the_sizes_and_the_chunker_version(
    root, monkeypatch
):
    base = build_corpus(settings(), root).fingerprint
    other_topic = {**GUIDE_SOURCE, "topic": "features"}
    assert build_corpus(settings([other_topic, DECISIONS_SOURCE]), root).fingerprint != base
    assert build_corpus(settings(max_chars=1000), root).fingerprint != base
    assert build_corpus(settings(milestone_topics=[MILESTONE_TOPICS[0]]), root).fingerprint != base
    monkeypatch.setattr(chunking_module, "CHUNKER_VERSION", "chunker-test")
    assert build_corpus(settings(), root).fingerprint != base


# --- verify_corpus catches a corrupted corpus ---------------------------------------------------


def _tampered(monkeypatch, root, index, **changes):
    real = build_corpus(settings(), root)
    chunks = list(real.chunks)
    chunks[index] = chunks[index].model_copy(update=changes)
    fake = type(real)(chunks=tuple(chunks), fingerprint=real.fingerprint, by_id=real.by_id)
    monkeypatch.setattr(chunking_module, "build_corpus", lambda *_: fake)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"text": "## Alpha section\n\nAltered."}, "not the document's text"),
        ({"char_count": 5}, "size or hash"),
        ({"content_sha256": "0" * 64}, "size or hash"),
        ({"source_sha256": "0" * 64}, "source hash"),
        ({"oversize": True}, "oversize flag"),
        ({"source_path": "docs/other.md"}, "not allow-listed"),
    ],
)
def test_verify_corpus_rejects_a_chunk_that_is_not_what_it_claims(
    monkeypatch, root, changes, message
):
    _tampered(monkeypatch, root, 1, **changes)
    with pytest.raises(CorpusError, match=message):
        verify_corpus(settings(), root)
