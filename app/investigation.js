/*
  SiteScout investigation client (Milestone 11, D-062).

  The decision page is complete without this file's features. Opened from disk, it makes no
  request and every investigation block says the investigation is unavailable. Served by
  scripts/serve.py, it asks the local server whether investigation is available, and shows
  contextual actions that send a fixed investigation kind plus identifiers (never free text).
  It renders only what the server returns: the validated answer with its cited records, or the
  fallback's retrieved records, plus the recorded trace. Text is inserted with textContent only,
  and nothing is stored in the browser.
*/
"use strict";

window.SiteScoutInvestigation = (function () {
  const KIND_TITLES = {
    site_investigation: "Site investigation",
    network_comparison: "Network difference",
    unknowns: "What would need to be verified",
    evidence_explanation: "Evidence explanation",
  };
  const GROUP_TITLES = {
    demand: "Demand",
    access: "Access",
    host: "Host and nearby activity",
    charging_gap: "Charging gap",
    grid_evidence: "Grid evidence",
  };
  const TYPE_TITLES = {
    RETRIEVED_FACT: "Retrieved facts",
    CALCULATED: "Calculated",
    INFERRED: "Inferred",
    UNKNOWN: "Unknown",
  };
  const LIMIT = "The investigation reached its step limit before producing a validated answer.";
  const PROVIDER = "The model provider did not return a usable response.";
  const ENDINGS = {
    validation_failed:
      "The investigation gathered evidence, but no generated answer passed SiteScout's " +
      "validation, so none is shown.",
    iteration_limit: LIMIT,
    tool_call_limit: LIMIT,
    retrieval_call_limit: LIMIT,
    recoverable_error_limit: "The investigation stopped after repeated refused tool calls.",
    provider_error: PROVIDER,
    malformed_provider_response: PROVIDER,
    tool_failure: "A SiteScout tool failed during the investigation.",
  };
  const UNAVAILABLE =
    "Investigation unavailable in this view. The SiteScout decision above is complete without it.";
  const UNAFFECTED = "The decision above is unaffected.";

  let availability = null;
  let gridDisclaimer = "";
  let running = false;
  let runToken = 0;
  const sections = new WeakMap();

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "class") node.className = value;
      else node.setAttribute(key, value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  // One status request per page load, and none at all when the page is opened from disk.
  function status() {
    if (availability) return availability;
    if (location.protocol === "file:") {
      availability = Promise.resolve(false);
      return availability;
    }
    availability = fetch("/api/status", { cache: "no-store", credentials: "same-origin" })
      .then((response) => (response.ok ? response.json() : {}))
      .then((body) => body.investigation_available === true)
      .catch(() => false);
    return availability;
  }

  function init(options) {
    gridDisclaimer = options.gridDisclaimer || "";
    status();
  }

  function setRunning(value) {
    running = value;
    for (const button of document.querySelectorAll("button.inv-action")) button.disabled = value;
  }

  // A labelled investigation block; every result of its actions renders inside it.
  function section(options) {
    const note = el("p", { class: "inv-intro" }, options.intro);
    const actions = el("div", { class: "inv-actions" });
    const line = el("p", { class: "inv-line", "aria-live": "polite" });
    const result = el("div", { class: "inv-result" });
    const node = el(
      "section",
      { class: "investigation", "aria-label": "Investigation" },
      el("div", { class: "inv-head" }, el("h3", {}, "Investigation"), el("span", { class: "inv-tag" }, "AI Analyst")),
      note, actions, line, result,
    );
    sections.set(node, { note, actions, line, result });
    if (options.primary) actions.append(action(options.primary.label, options.primary.request, node));
    status().then((ok) => {
      if (!ok) note.textContent = UNAVAILABLE;
    });
    return node;
  }

  // A contextual action, hidden until the server says investigation is available.
  function action(label, request, target, extraClass) {
    const button = el("button", { type: "button", class: `inv-action${extraClass ? " " + extraClass : ""}` }, label);
    button.hidden = true;
    button.addEventListener("click", () => run(request, target));
    status().then((ok) => {
      button.hidden = !ok;
      button.disabled = running;
    });
    return button;
  }

  function run(request, target) {
    const parts = sections.get(target);
    if (!parts || running) return;
    const token = ++runToken;
    setRunning(true);
    parts.result.replaceChildren();
    parts.line.textContent = "Investigating…";
    fetch("/api/investigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    })
      .then((response) => response.json().catch(() => ({ status: "invalid_request" })))
      .catch(() => ({ status: "network_error" }))
      .then((data) => {
        if (token !== runToken) return;
        setRunning(false);
        if (!target.isConnected) return;
        parts.line.textContent = "";
        parts.result.replaceChildren(render(request, data));
        const heading = parts.result.querySelector(".inv-title");
        if (heading) heading.focus({ preventScroll: false });
      });
  }

  // --- Rendering -------------------------------------------------------------------------------

  function title(request) {
    const kind = KIND_TITLES[request.kind] || "Investigation";
    return request.group ? `${kind}: ${GROUP_TITLES[request.group]}` : kind;
  }

  function render(request, data) {
    const head = el("h4", { class: "inv-title", tabindex: "-1" }, title(request));
    if (data.status === "answered" && data.answer) return el("div", {}, head, answered(data), trace(data));
    if (data.status === "fallback") return el("div", {}, head, fallback(data), trace(data));
    if (data.status === "unavailable") return el("div", {}, head, el("p", { class: "inv-message" }, UNAVAILABLE));
    const text = data.status === "busy"
      ? "Another investigation is running. Try again when it finishes."
      : "The investigation request could not be completed.";
    return el("div", {}, head, el("p", { class: "inv-message" }, text, " ", UNAFFECTED));
  }

  function answered(data) {
    const a = data.answer;
    const blocks = [
      ["Conclusion", a.direct_answer],
      ["Evidence", [...a.evidence, ...a.interpretation]],
      ["Unknown", a.unknowns],
      ["Next investigation", a.next_investigation],
    ];
    return el(
      "div",
      { class: "inv-answer" },
      el("p", { class: "inv-verdict" }, "Validated against the records this investigation retrieved."),
      blocks
        .filter(([, statements]) => statements.length)
        .map(([name, statements]) =>
          el("div", { class: "inv-block" }, el("h5", {}, name),
            el("ul", { class: "stmts" }, statements.map((s) => statement(s, data))))),
    );
  }

  function statement(s, data) {
    const cites = s.evidence_ids.map((id) => citation(id, data)).filter(Boolean);
    return el(
      "li",
      { class: "stmt" },
      el("span", { class: `type t-${s.kind}` }, s.kind),
      el(
        "div",
        { class: "stmt-body" },
        el("p", {}, s.text),
        s.quotes.map((q) => el("blockquote", {}, q)),
        cites.length ? el("div", { class: "cites" }, cites) : null,
      ),
    );
  }

  function citation(id, data) {
    const r = data.records[id];
    if (r && id.startsWith("kb/")) {
      return el(
        "details",
        { class: "cite" },
        el("summary", {}, "Project knowledge · ", r.evidence.metric),
        el("dl", {}, el("dt", {}, "Document"), el("dd", {}, r.evidence.metric),
          el("dt", {}, "Section"), el("dd", {}, r.claim), el("dt", {}, "Record"), el("dd", { class: "mono" }, r.id)),
      );
    }
    if (r) {
      return el(
        "details",
        { class: "cite" },
        el("summary", {}, `${r.claim}: ${r.display}`),
        el("dl", {}, el("dt", {}, "Value"), el("dd", {}, r.display), el("dt", {}, "Type"), el("dd", {}, r.type),
          el("dt", {}, "Source"), el("dd", {}, r.evidence.source), el("dt", {}, "Record"), el("dd", { class: "mono" }, r.id)),
      );
    }
    const c = data.comparisons[id];
    if (c) {
      return el(
        "details",
        { class: "cite" },
        el("summary", {}, `${c.label}: ${c.display}`),
        el("dl", {}, el("dt", {}, "Record"), el("dd", { class: "mono" }, c.id)),
      );
    }
    return null;
  }

  function fallback(data) {
    const byType = {};
    for (const r of Object.values(data.records)) (byType[r.type] = byType[r.type] || []).push(r);
    const groups = Object.keys(TYPE_TITLES)
      .filter((type) => byType[type])
      .map((type) =>
        el("details", { class: "inv-group", ...(type === "UNKNOWN" ? { open: "" } : {}) },
          el("summary", {}, TYPE_TITLES[type]),
          el("ul", { class: "recs" }, byType[type].map((r) =>
            el("li", {}, el("span", {}, r.id.startsWith("kb/") ? r.evidence.metric : r.claim),
              el("span", { class: "v" }, r.id.startsWith("kb/") ? r.claim : r.display))))));
    return el(
      "div",
      { class: "inv-fallback" },
      el("p", { class: "inv-verdict warn" }, UNAVAILABLE),
      el("p", { class: "note" }, ENDINGS[data.termination] || "The investigation ended without a validated answer."),
      groups.length ? el("h5", {}, "Evidence retrieved during this investigation") : null,
      groups,
      gridDisclaimer ? el("p", { class: "disclaimer" }, gridDisclaimer) : null,
    );
  }

  function argumentsText(args) {
    const parts = [];
    for (const [key, value] of Object.entries(args || {})) {
      if (key === "query") parts.push(`“${value}”`);
      else if (Array.isArray(value)) parts.push(value.join(", "));
      else if (typeof value === "string") parts.push(value);
      else parts.push(`${key} ${value}`);
    }
    return parts.join(" · ");
  }

  function trace(data) {
    const steps = data.trace.map((t) =>
      el("li", {}, el("span", { class: "step" }, t.label),
        argumentsText(t.arguments) ? el("span", { class: "args" }, argumentsText(t.arguments)) : null));
    const checks = data.validation.map((v) =>
      el("li", { class: v.passed ? "pass" : "fail" },
        el("span", { class: "step" }, `Answer attempt ${v.attempt}: ${v.passed ? "passed validation" : "rejected by validation"}`),
        v.rules.length ? el("span", { class: "args mono" }, v.rules.join(", ")) : null));
    const verdict = data.status === "answered" ? "validated" : "not validated";
    return el(
      "details",
      { class: "trace" },
      el("summary", {}, `How this was investigated · ${verdict} · ${data.elapsed_display}`),
      steps.length ? el("ol", { class: "steps" }, steps) : el("p", { class: "note" }, "No tool was called."),
      checks.length ? el("ul", { class: "checks" }, checks) : null,
    );
  }

  return { init, status, section, action };
})();
