# Agent grounding and trajectory evaluation (Milestone 10, Phase 4)

This report measures the **validator and the agent loop**, not a model. Every case is a scripted provider trajectory (tool calls, then answers) run through the production agent loop against a fixed SYNTHETIC world and the real project-knowledge index. No model, API, network, clock or randomness is involved, so the same inputs give the same report.

The counts are **test-suite metrics** over a small, hand-written case set. They show which rules hold and where the documented limits (D-058) are, not how any model or the system performs in general. Nothing is combined into one score: the four decision outcomes, the rules, the terminations and the known limits are separate measurements. Retrieval quality is a different measurement (`reports/knowledge_eval.md`); this report only records the corpus it ran against.

## 1. Setup

{setup}

## 2. Decisions by category

The decision under test is the validator's verdict on a run's **first answer attempt**. A *true accept* is a legitimate answer accepted, a *true reject* an unsupported one rejected, a *false accept* an unsupported answer accepted, a *false reject* a legitimate answer rejected. The ground truth is each case's `decision`; cases without an answer attempt count as not applicable. "Pass" means the run matched everything the case expects, including pinned known limits.

{categories}

## 3. Decisions by focus

A case can carry several tags, so these rows overlap.

{tags}

## 4. Grounding rules that rejected a first attempt

{rules}

## 5. Terminations

{terminations}

## 6. Known limits (false accepts and false rejects)

These cases pin behaviour the validator is documented not to get right. The suite passes only while it stays exactly as pinned; each is counted above as the false accept or false reject it is.

{limits}

## 7. Cases

{cases}

## 8. Limitations

- The case set is small and hand-written. It checks that each rule fires and does not fire where it should not; it is not a sample of real questions.
- The provider is scripted: the cases show what the validator and the loop do with an answer, never what a model would write.
- The world is SYNTHETIC. Its candidate ids are not in the 12-hex-digit form of real ids, so the unknown-site rule is exercised with an id of the real form and the short synthetic ids are outside it.
- A quotation proves where a claim comes from, not that the paraphrase is faithful to it. Faithfulness is not verified (D-058).
- The number rules match exact number tokens. They cannot tell a methodology constant from a current value that happens to be the same token, so some legitimate statements are rejected and a number only documentation holds is accepted (D-058).
- Fixture values come from the tool outputs the scripted provider was shown, except literal quotations, which must stay verbatim in the current documents; a stale quotation shows up as a failing case.
- Actual grid connection feasibility requires utility confirmation.
