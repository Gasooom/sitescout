# Demo-readiness gate (Milestone 11)

**A readiness check for the local investigation demo on one date, with one provider and one model. It is not a measure of the agent's reliability, and it does not change the M10 evaluations (`reports/agent_eval.md`, `reports/agent_live_eval.md`).**

The page's investigation layer (D-062) is shown as a working demo only when this gate passes (D-063). The live part below runs three investigation kinds through the page's own server path, with the agent's configured limits and validator unchanged. It records outcomes, validation rules and recorded steps only; no answer text is kept.

## 1. Setup

| Setting | Value |
|---|---|
| Generated (UTC) | 2026-09-28T12:28:42.425197+00:00 |
| Provider | openai |
| Model | gpt-5.6-luna |
| Site (highest-ranked network site) | `cand-3a8fa00f876a` |
| Runs per kind | 2 |
| Validated runs required per kind | 1 |

## 2. Verdict: PASS

A kind passes when every run completes (a validated answer or the agent's fallback) and at least the required number end with a validated answer. A fallback is a safe product state: the page shows the retrieved records and no generated text.

| Kind | Runs | Validated | Completed | Verdict |
|---|---|---|---|---|
| site_investigation | 2 | 2 | 2 | pass |
| evidence_explanation | 2 | 2 | 2 | pass |
| unknowns | 2 | 2 | 2 | pass |

## 3. Live runs

| Kind | Status | Ending | Attempts | Validation rules | Steps | Time |
|---|---|---|---|---|---|---|
| site_investigation | answered | answered | 1 | none | Retrieved site record → Checked network contribution | 11.7 s |
| site_investigation | answered | answered | 1 | none | Retrieved site record → Checked network contribution | 10.3 s |
| evidence_explanation (grid_evidence) | answered | answered | 2 | `arithmetic_expression`, `number_not_grounded` | Retrieved site record | 14.1 s |
| evidence_explanation (grid_evidence) | answered | answered | 2 | `grid_disclaimer_missing`, `quote_not_in_cited_chunk` | Retrieved site record → Tool call refused → Tool call refused → Searched project knowledge | 19.7 s |
| unknowns | answered | answered | 2 | `grid_disclaimer_missing`, `unknown_stated_as_fact` | Retrieved site record | 14.0 s |
| unknowns | answered | answered | 2 | `approximation_language`, `grid_disclaimer_missing`, `unknown_must_state_absence`, `unknown_stated_as_fact` | Retrieved site record | 22.6 s |

## 4. Deterministic gate items

These hold for every run of the test suite, with no provider:

| Gate item | Enforced by |
|---|---|
| An invalid or nonexistent site is refused before any provider call | tests/test_server.py |
| A rejection returns the fallback's records, never answer text | tests/test_server.py |
| The export is unchanged by investigations | tests/test_server.py |
| No credential reaches a response or the page | tests/test_server.py, tests/test_app.py |
| No investigation output is written anywhere | tests/test_server.py |
| Runs stay within the agent's configured limits | tests/test_server.py |
| The page opened from disk is complete and makes no request | tests/test_app.py |

## 5. Limitations

- A handful of runs on one day. A model is not deterministic between runs; a repeat may differ.
- Whether an answer reads well is not scored; only whether SiteScout's validator accepted it.
- Actual grid connection feasibility requires utility confirmation.
