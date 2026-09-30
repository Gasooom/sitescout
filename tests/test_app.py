"""app/index.html is a read-only view over the export (SPEC §10; D-048), and
app/investigation.js is its optional investigation layer, served locally (D-062) or, for the
GitHub Pages copy, by the public deployment (D-066)."""

import json
import re

import pytest

from sitescout.config import PROJECT_ROOT

PAGE = (PROJECT_ROOT / "app" / "index.html").read_text(encoding="utf-8")
CLIENT = (PROJECT_ROOT / "app" / "investigation.js").read_text(encoding="utf-8")
EXPORT = PROJECT_ROOT / "data" / "export" / "sitescout.json"


def test_the_page_loads_the_export_by_script_so_it_works_from_disk():
    assert '<script src="../data/export/sitescout.js"></script>' in PAGE
    assert "window.SITESCOUT" in PAGE
    assert "fetch(" not in PAGE and "XMLHttpRequest" not in PAGE


def test_the_page_uses_no_external_resource():
    urls = set(re.findall(r"https?://[^\s\"'<>]+", PAGE))
    assert urls == {"http://www.w3.org/2000/svg"}  # the SVG namespace, not a request


def test_the_page_inserts_text_safely():
    assert "innerHTML" not in PAGE and "outerHTML" not in PAGE


@pytest.mark.skipif(not EXPORT.is_file(), reason="run scripts/export.py first")
@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_no_result_from_the_export_is_written_into_the_page(text):
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    headline = data["headline"]
    values = {headline["exact_vs_greedy_gap"]}
    for selection in headline["selections"]:
        values |= {selection["population_display"], selection["mean_score_display"]}
    values |= {site["score"] for site in data["sites"] if site["selected_mclp"]}
    values |= {site["unique_coverage"] for site in data["sites"] if site["unique_coverage"]}
    written = sorted(value for value in values if value in text)
    assert written == []
    for disclaimer in data["meta"]["disclaimers"]:
        assert disclaimer not in text  # disclaimers come from the export too


# --- The optional investigation layer (D-062) ------------------------------------------------


def test_the_page_loads_the_investigation_client_locally_after_the_export():
    export_tag = PAGE.index('<script src="../data/export/sitescout.js"></script>')
    client_tag = PAGE.index('<script src="investigation.js"></script>')
    assert export_tag < client_tag


def test_the_client_requests_only_its_two_endpoints():
    targets = re.findall(r"fetch\(\s*api\(\"([^\"]*)\"\)", CLIENT)
    assert sorted(set(targets)) == ["/api/investigate", "/api/status"]
    assert CLIENT.count("fetch(") == len(targets)  # every request names a literal endpoint
    for other in ("XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import("):
        assert other not in CLIENT


def test_the_client_makes_no_request_when_the_page_is_opened_from_disk():
    assert CLIENT.index('location.protocol === "file:"') < CLIENT.index("fetch(")


BACKENDS = re.search(r"const BACKENDS = \{ \"([^\"]*)\": \"([^\"]*)\" \};", CLIENT)


def test_only_the_github_pages_copy_asks_another_server():
    # D-066: one entry, keyed by the GitHub Pages origin; every other view (served by
    # scripts/serve.py, or by the deployment itself) keeps asking its own origin.
    assert BACKENDS is not None
    page, backend = BACKENDS.groups()
    assert page == "https://gasooom.github.io"
    assert backend == "" or re.fullmatch(r"https://[a-z0-9-]+(\.[a-z0-9-]+)+", backend)
    assert 'const API = BACKENDS[location.origin] || "";' in CLIENT
    assert "const api = (path) => API + path;" in CLIENT


def test_the_client_uses_no_other_external_resource():
    assert re.findall(r"https?://[^\s\"']*", CLIENT) == [u for u in BACKENDS.groups() if u]


@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_text_is_inserted_safely_and_nothing_is_stored(text):
    unsafe = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")
    storage = ("new Function", "localStorage", "sessionStorage", "indexedDB", "document.cookie")
    for banned in unsafe + storage:
        assert banned not in text, banned


@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_no_credential_or_provider_detail_reaches_the_browser(text):
    for banned in ("OPENAI", "ANTHROPIC", "api_key", "apiKey", "Authorization", "Bearer", "sk-"):
        assert banned not in text, banned


@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_ai_is_named_only_where_it_adds_context(text):
    uses = re.findall(r"\bAI\b.{0,40}", text)
    allowed = ("AI Analyst", "AI investigation unavailable", "AI-assisted investigation")
    assert all(u.startswith(allowed) for u in uses), uses
    lowered = text.lower()
    for banned in (
        "ai-powered",
        "powered by ai",
        "ai-driven",
        "generative",
        "chatbot",
        "thinking",
        "reasoning…",
        "sparkle",
        "@keyframes",
        "gradient(",
    ):
        assert banned not in lowered, banned  # fmt: skip


def test_the_client_shows_only_what_the_server_returns():
    # The loading line is the one honest state while a request runs; results are rendered only
    # from the response, and a fallback shows retrieved records, never generated text.
    assert CLIENT.count("Investigating…") == 1
    assert (
        '"Investigation unavailable in this view. The SiteScout decision above is complete '
        'without it."' in CLIENT
    )
    for field in ("data.answer", "data.records", "data.trace", "data.validation"):
        assert field in CLIENT
    assert "reason" not in CLIENT  # the server never sends a fallback's reason


# --- Typefaces (D-065) -------------------------------------------------------------------------


def test_the_page_typefaces_are_shipped_licensed_and_served():
    from sitescout.server import STATIC

    fonts = re.findall(r'url\("(fonts/[^"]+\.woff2)"\)', PAGE)
    assert len(fonts) == 4
    for font in fonts:
        path = PROJECT_ROOT / "app" / font
        assert path.is_file() and path.read_bytes()[:4] == b"wOF2", font
        assert STATIC[f"/app/{font}"] == (f"app/{font}", "font/woff2")  # served, not only from disk
    licence = (PROJECT_ROOT / "app" / "fonts" / "LICENSE.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License, Version 1.1" in licence
    # A system stack follows each face, so the page still reads if a file cannot load.
    assert '--sans: "IBM Plex Sans", ui-sans-serif' in PAGE
    assert '--mono: "IBM Plex Mono", ui-monospace' in PAGE


# --- Network outcome and navigation (visual redesign, Phase 2) ----------------------------------


def test_the_outcome_strip_shows_coverage_and_reach_but_not_the_mean_score():
    body = PAGE[PAGE.index("function renderStatus()") : PAGE.index("enter(figure);")]
    assert "s.population_display" in body  # the one large figure
    for reach in ("H.n_sites", "s.districts_display", "s.provinces_display"):
        assert reach in body
    assert "mean_score" not in body  # the mean score belongs to the comparison


def test_the_top_bar_links_the_workspace_and_every_reference_section():
    tabs = re.findall(r'data-nav="([a-z]+)"', PAGE)
    assert tabs == ["network", "evaluation", "sources", "method", "limitations"]
    for section in ("workspace", "network", "evaluation", "sources", "method", "limitations"):
        assert f'id="{section}"' in PAGE


# --- Network workspace (visual redesign, Phase 3) -------------------------------------------------


def test_the_workspace_reads_outcome_list_map_then_dossier():
    order = [PAGE.index(f'id="{i}"') for i in ("overview", "panel", "mapwrap", "dossier")]
    assert order == sorted(order)


def test_the_network_site_dossier_keeps_the_approved_order():
    start = PAGE.index("  if (inNetwork) {")
    branch = PAGE[start : PAGE.index("  } else {", start)]
    # D-067: why it matters, the evidence, what is known and what remains uncertain, the
    # investigation, then what to do next.
    sections = (
        '"Why this site matters"',
        '"Evidence"',
        "confidence,",
        '"What remains uncertain"',
        '"site-investigation"',
        '"Next actions"',
    )
    positions = [branch.index(s) for s in sections]
    assert positions == sorted(positions)
    assert (
        'block("What is known"' in PAGE
    )  # the confidence block, named for what it tells the reader


# --- Map (visual redesign, Phase 4) --------------------------------------------------------------


def test_the_map_legend_says_public_data_and_modelled_radius():
    # The chargers are the publicly known sites, not a market inventory; the ring is an assumption.
    assert '"Known charging site (public data)"' in PAGE
    assert "service radius (modelled)" in PAGE


# --- Site dossier (visual redesign, Phase 5) -----------------------------------------------------


def test_the_network_role_keeps_the_optimized_contribution_with_the_optimized_network():
    # unique_coverage exists for the optimized network only: it is shown only for its sites,
    # labelled as measured there, and the role is redrawn whenever the active network changes.
    role = PAGE[PAGE.index("function roleBlock(site)") : PAGE.index("function queueNav(site)")]
    assert "site.selected_mclp && site.unique_coverage" in role
    assert "Measured in the ${optimized} only" in role
    start = PAGE.index("function setMode(key)")
    set_mode = PAGE[start : PAGE.index("for (const s of H.selections)", start)]
    assert "roleBlock(site)" in set_mode


# --- Network position vs global score rank, verification presentation, network contribution -----
# (targeted clarity fix; presentation only, no change to scoring, ranking or selection)


def test_the_opportunity_list_shows_network_position_not_global_score_rank():
    render_list = PAGE[PAGE.index("function renderList()") : PAGE.index("function markRow(id)")]
    # The leading rank badge counts the site's position in the *currently shown* list (1..N)...
    assert "pad2(i + 1)" in render_list
    assert "`/${items.length}`" in render_list
    # ...never the candidate's rank among all 300, and the two are named differently in the row.
    assert "pad2(site.rank)" not in render_list
    assert "Score rank #${site.rank} / ${H.candidates}" in render_list
    # The help note spells out the distinction in words, not only in the row's own labels.
    assert "position in this" in render_list and "Score rank" in render_list


def test_the_dossier_labels_the_global_rank_score_rank_not_rank_by_score():
    start = PAGE.index("function showSite(id, fromUser)")
    show_site = PAGE[start : PAGE.index("function setMode(key)")]
    assert '"Rank by score"' not in show_site
    assert '"Score rank"' in show_site
    # The value itself is untouched: still the candidate's rank of all candidates, unchanged.
    assert "`#${site.rank}`" in show_site and "` / ${H.candidates}`" in show_site


def test_the_limitations_section_reads_as_open_items_not_a_list_of_verify_tags():
    assert "What still needs verification" in PAGE
    assert "What this does not establish" not in PAGE
    limits = PAGE[PAGE.index('$("limits").append') : PAGE.index('$("limits-disclaimer")')]
    assert "limitRow(title, text)" in limits
    # No per-row "Verify" tag repeated once for each of the 7 items.
    assert "verifyRow" not in limits
    # One clear framing replaces it, still amber, still not a functional control.
    cue = '<p class="verify-cue">Verify with operator, utility, site owner or authorities</p>'
    assert cue in PAGE
    near = PAGE[PAGE.index(cue) - 200 : PAGE.index(cue) + 200]
    assert "<button" not in near
    # The items and their meaning are unchanged: still open questions, not a claim of "no".
    for item in (
        "Grid connection capacity",
        "Transformer capacity",
        "Land availability",
        "Landowner / host willingness",
        "Permit approval",
        "Commercial viability",
        "Actual charger utilization",
    ):
        assert item in PAGE


def test_network_contribution_is_never_presented_as_a_greedy_or_top30_contribution():
    role = PAGE[PAGE.index("function roleBlock(site)") : PAGE.index("function queueNav(site)")]
    # The "Network contribution" label is gated by the same optimized-only condition as
    # unique_coverage, appears exactly once, and comes before the plain sentence used when a
    # candidate (Greedy- or Top-30-only) has no exported contribution at all.
    assert "site.selected_mclp && site.unique_coverage\n    ? el(" in role
    assert role.count('"Network contribution"') == 1
    label_at = role.index('"Network contribution"')
    no_contribution_at = role.index("exports no network contribution for this candidate")
    assert label_at < no_contribution_at


# --- The redesign (D-067): access, motion, and what must not come back --------------------------


def test_the_page_has_a_skip_link_landmarks_and_a_named_map_control_set():
    assert '<a class="skip" href="#workspace">' in PAGE
    assert '<header class="topbar"' in PAGE and '<main class="content" id="content">' in PAGE
    for name in ("Zoom in", "Zoom out", "Zoom to the selected site", "Show the whole country"):
        assert f'aria-label="{name}"' in PAGE  # icon-only controls carry a name


def test_the_map_can_be_operated_without_a_pointer():
    assert 'id="mapwrap" tabindex="0" role="group"' in PAGE
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
        assert key in PAGE  # panning has a keyboard alternative to dragging
    assert "e.ctrlKey || e.metaKey" in PAGE  # an ordinary wheel keeps scrolling the page


def test_motion_is_off_when_the_reader_asks_for_less():
    assert (
        "@media (prefers-reduced-motion: reduce)" in PAGE and "transition: none !important" in PAGE
    )
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in PAGE
    smooth = [line for line in PAGE.splitlines() if '"smooth"' in line]
    assert smooth and all(
        "reducedMotion()" in line for line in smooth
    )  # no scripted scroll ignores it


def test_uncertainty_is_amber_and_never_an_error_colour():
    # --warn (rust) is only for a failed validation step in the trace; unknowns, verification and
    # the investigation's fallback line use the verification amber.
    assert (
        PAGE.count("var(--warn)") == 1 and ".checks li.fail .step { color: var(--warn); }" in PAGE
    )
    assert ".inv-verdict.warn { color: var(--verify);" in PAGE
    assert ".p-UNKNOWN { color: var(--verify); }" in PAGE


def test_the_page_wrapper_does_not_reuse_the_investigation_result_class():
    # investigation.js draws each result as <article class="report">; a page rule of that name once
    # gave every result stray padding and a maximum width.
    assert 'class: "report"' in CLIENT
    assert 'class="report"' not in PAGE and not re.search(r"^\s*\.report\s*\{", PAGE, re.M)


def test_the_page_prints_as_a_plain_document():
    assert "@media print" in PAGE


def test_the_queue_says_why_a_location_matters_without_inventing_a_contribution():
    render_list = PAGE[PAGE.index("function renderList()") : PAGE.index("function markRow(id)")]
    assert "contributes && site.unique_coverage" in render_list  # the optimized network only
    assert "of demand only this site reaches" in render_list and "Strongest: " in render_list


def test_the_dossier_and_masthead_use_analyst_wording():
    phrases = (
        "Why this site matters",
        "What is known",
        "What remains uncertain",
        "Next actions",
        "Choose a place",
        "Compare the three networks",
    )
    for phrase in phrases:
        assert phrase in PAGE
    for retired in (
        "What we don't know yet",
        "Next checks",
        "Confidence score",
        "Unlock",
        "Next-generation",
    ):
        assert retired not in PAGE
