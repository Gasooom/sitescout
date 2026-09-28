# Agent live evaluation (Milestone 10, Phase 6)

**This is one run against one provider and one model, on the date above; it is not evidence that the agent is reliable in general. See `reports/agent_eval.md` for the deterministic, reproducible evaluation (73 scripted cases, no live model).**

This report measures how one real provider behaved on one run of a small, cost-bounded, unscripted case set: each case is a free-text question, and the model chose every tool call and its order for itself through the unmodified M10 agent loop and provider (Phases 3 and 5). Grounding is decided entirely by the unchanged validator (`sitescout.agent.validate`); nothing here adds or relaxes a rule. It is an observational report, not a pass/fail gate: the counts below are what happened, not a score.

## 1. Setup

| Setting | Value |
|---|---|
| Evaluation version | `agent-live-eval-v1` |
| Generated (UTC) | 2026-09-28T09:05:30.341854+00:00 |
| Provider | openai |
| Model | gpt-5.6-luna |
| Case set version | 1 |
| Cases in file | 11 |
| Skipped (site not found) | 0 |
| Infrastructure errors | 0 |
| `max_iterations` | 6 |
| `max_tool_calls` | 5 |
| `max_retrieval_calls` | 2 |
| `max_recoverable_errors` | 2 |
| `min_quote_words` | 3 |

## 2. Cases run, by category

| Category | Cases run |
|---|---|
| A: knowledge only | 2 |
| B: structured only | 2 |
| C: mixed sources | 1 |
| D: unknown | 2 |
| E: candidate identity | 1 |
| F: number provenance | 1 |
| G: structured-first | 1 |
| H: multi-step trajectory | 1 |

## 3. Failure taxonomy (of the cases that ran)

Model failure (an ungrounded or policy-violating answer) is `validator_rejected`; model failure (inefficient or repeated mistakes) is `budget_exhausted`; provider/API failure is `provider_error`; a badly-shaped reply is `malformed_response`; a tool that raised unexpectedly is `tool_failure`. `answered_grounded`/`answered_after_retry` are the two ways a run ends validated.

| Failure category | Cases |
|---|---|
| `answered_after_retry` | 4 |
| `answered_grounded` | 6 |
| `validator_rejected` | 1 |

## 4. Soft expectations

Each case may declare a few properties it expects (which tools were used, at some point, in any order; which evidence-id prefixes were cited; which phrases appear). These are reported, not enforced: a real model choosing a different, equally valid path is data, not a defect.

| Case | Expectation | Result |
|---|---|---|
| A01 | tools_any | met |
| A01 | contains_any:Actual grid connection feasibility requires utility confirmation. | met |
| A02 | tools_any | met |
| B01 | tool_include:get_site | met |
| B02 | tool_include:network_summary | met |
| C01 | tool_include:network_summary | met |
| C01 | tools_any | met |
| D01 | expect_unknown | **unmet** |
| D02 | expect_unknown | met |
| E01 | tools_any | met |
| F01 | tool_include:network_contribution | **unmet** |
| G01 | tools_any | met |
| H01 | tools_any | met |

## 5. Usage and latency

Total latency across every provider call: 128.1s.
Total input tokens: 230046.
Total output tokens: 10239.
Estimated cost: not available (no price configured for this model, or no usage was reported by the provider).

## 6. Cases

| Id | Category | Title | Status | Outcome | Tools used | Attempts | Latency |
|---|---|---|---|---|---|---|---|
| A01 | knowledge only | Grid evidence and the exact disclaimer | answered | answered_after_retry | search_knowledge, search_knowledge | 2 | 16.5s |
| A02 | knowledge only | What confidence does not represent | fallback | validator_rejected | search_knowledge | 2 | 13.9s |
| B01 | structured only | A real site's stored score and rank | answered | answered_grounded | get_site | 1 | 3.7s |
| B02 | structured only | The exact network's coverage share | answered | answered_grounded | network_summary | 1 | 3.2s |
| C01 | mixed sources | The exact network against the Top-30 baseline, and how it is chosen | answered | answered_after_retry | network_summary, search_knowledge | 2 | 20.4s |
| D01 | unknown | What SiteScout can never determine | answered | answered_after_retry | search_knowledge, search_knowledge | 2 | 10.0s |
| D02 | unknown | Land, permits and ownership at a real site | answered | answered_grounded | get_site | 1 | 5.8s |
| E01 | candidate identity | A neutral comparison of two real sites | answered | answered_after_retry | compare_sites | 2 | 27.6s |
| F01 | number provenance | A derived percentile, grounded in a tool's own record | answered | answered_grounded | explain_score, network_summary, get_site | 1 | 11.1s |
| G01 | structured-first | A site's marginal contribution to network coverage | answered | answered_grounded | network_contribution | 1 | 3.7s |
| H01 | multi-step trajectory | Investigate a real site end to end | answered | answered_grounded | get_site, network_contribution | 1 | 12.3s |

## 7. Limitations

- One run, one provider, one model, one date. Model behaviour is not deterministic between runs; a repeat run may differ.
- "Correct final answer" is not scored: no ground truth exists for free text. Grounding validity (whether the validator accepted the answer) is measured; whether the prose reads well is not.
- Token usage and latency come only from what the provider's own reply reports; a model or SDK that omits `usage` is recorded as "not available", never guessed.
- Estimated cost, where shown, is a best-effort figure from a price table the operator configured; it is not authoritative and is absent when no price is configured for the model tested.
- A skipped case (its `site_selector` matched no real candidate) spent no call and is not counted as a failure.
- Actual grid connection feasibility requires utility confirmation.
