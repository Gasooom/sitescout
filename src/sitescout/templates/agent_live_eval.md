# Agent live evaluation (Milestone 10, Phase 6)

**{caveat}**

This report measures how one real provider behaved on one run of a small, cost-bounded, unscripted case set: each case is a free-text question, and the model chose every tool call and its order for itself through the unmodified M10 agent loop and provider (Phases 3 and 5). Grounding is decided entirely by the unchanged validator (`sitescout.agent.validate`); nothing here adds or relaxes a rule. It is an observational report, not a pass/fail gate: the counts below are what happened, not a score.

## 1. Setup

{setup}

## 2. Cases run, by category

{categories}

## 3. Failure taxonomy (of the cases that ran)

Model failure (an ungrounded or policy-violating answer) is `validator_rejected`; model failure (inefficient or repeated mistakes) is `budget_exhausted`; provider/API failure is `provider_error`; a badly-shaped reply is `malformed_response`; a tool that raised unexpectedly is `tool_failure`. `answered_grounded`/`answered_after_retry` are the two ways a run ends validated.

{failures}

## 4. Soft expectations

Each case may declare a few properties it expects (which tools were used, at some point, in any order; which evidence-id prefixes were cited; which phrases appear). These are reported, not enforced: a real model choosing a different, equally valid path is data, not a defect.

{expectations}

## 5. Usage and latency

{usage}

## 6. Cases

{cases}

## 7. Limitations

- One run, one provider, one model, one date. Model behaviour is not deterministic between runs; a repeat run may differ.
- "Correct final answer" is not scored: no ground truth exists for free text. Grounding validity (whether the validator accepted the answer) is measured; whether the prose reads well is not.
- Token usage and latency come only from what the provider's own reply reports; a model or SDK that omits `usage` is recorded as "not available", never guessed.
- Estimated cost, where shown, is a best-effort figure from a price table the operator configured; it is not authoritative and is absent when no price is configured for the model tested.
- A skipped case (its `site_selector` matched no real candidate) spent no call and is not counted as a failure.
- Actual grid connection feasibility requires utility confirmation.
{validation_detail}
