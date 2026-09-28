# Demo-readiness gate (Milestone 11)

**A readiness check for the local investigation demo on one date, with one provider and one model. It is not a measure of the agent's reliability, and it does not change the M10 evaluations (`reports/agent_eval.md`, `reports/agent_live_eval.md`).**

The page's investigation layer (D-062) is shown as a working demo only when this gate passes (D-063). The live part below runs three investigation kinds through the page's own server path, with the agent's configured limits and validator unchanged. It records outcomes, validation rules and recorded steps only; no answer text is kept.

## 1. Setup

| Setting | Value |
|---|---|
| Generated (UTC) | {generated_at} |
| Provider | {provider} |
| Model | {model} |
| Site (highest-ranked network site) | `{candidate_id}` |
| Runs per kind | {runs_per_kind} |
| Validated runs required per kind | {min_validated} |

## 2. Verdict: {overall}

A kind passes when every run completes (a validated answer or the agent's fallback) and at least the required number end with a validated answer. A fallback is a safe product state: the page shows the retrieved records and no generated text.

{verdicts}

## 3. Live runs

{runs}

## 4. Deterministic gate items

These hold for every run of the test suite, with no provider:

{deterministic}

## 5. Limitations

- A handful of runs on one day. A model is not deterministic between runs; a repeat may differ.
- Whether an answer reads well is not scored; only whether SiteScout's validator accepted it.
- Actual grid connection feasibility requires utility confirmation.
