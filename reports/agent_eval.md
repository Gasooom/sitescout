# Agent grounding and trajectory evaluation (Milestone 10, Phase 4)

This report measures the **validator and the agent loop**, not a model. Every case is a scripted provider trajectory (tool calls, then answers) run through the production agent loop against a fixed SYNTHETIC world and the real project-knowledge index. No model, API, network, clock or randomness is involved, so the same inputs give the same report.

The counts are **test-suite metrics** over a small, hand-written case set. They show which rules hold and where the documented limits (D-058) are, not how any model or the system performs in general. Nothing is combined into one score: the four decision outcomes, the rules, the terminations and the known limits are separate measurements. Retrieval quality is a different measurement (`reports/knowledge_eval.md`); this report only records the corpus it ran against.

## 1. Setup

| Setting | Value |
|---|---|
| Evaluation version | `agent-eval-v1` |
| World | SYNTHETIC test world (3 candidates, 1 network site) with the real knowledge index |
| Cases | 73 |
| Corpus chunks (context only) | 180 |
| Corpus fingerprint (context only) | `3e24fbc3b0c31ef897b4a1696805d6b0e7791727fbc8a1698757d1ac8999d74b` |
| `knowledge_top_k` | 5 |
| `max_iterations` | 12 |
| `max_tool_calls` | 10 |
| `max_retrieval_calls` | 3 |
| `max_recoverable_errors` | 2 |
| `min_quote_words` | 3 |

## 2. Decisions by category

The decision under test is the validator's verdict on a run's **first answer attempt**. A *true accept* is a legitimate answer accepted, a *true reject* an unsupported one rejected, a *false accept* an unsupported answer accepted, a *false reject* a legitimate answer rejected. The ground truth is each case's `decision`; cases without an answer attempt count as not applicable. "Pass" means the run matched everything the case expects, including pinned known limits.

| Category | Cases | True accept | True reject | False accept | False reject | N/A | Pass |
|---|---|---|---|---|---|---|---|
| A: knowledge only | 9 | 3 | 5 | 1 | 0 | 0 | 9 of 9 |
| B: structured only | 9 | 6 | 3 | 0 | 0 | 0 | 9 of 9 |
| C: mixed sources | 8 | 5 | 2 | 1 | 0 | 0 | 8 of 8 |
| D: unknown | 10 | 4 | 4 | 2 | 0 | 0 | 10 of 10 |
| E: candidate identity | 6 | 3 | 2 | 1 | 0 | 0 | 6 of 6 |
| F: number provenance | 8 | 4 | 4 | 0 | 0 | 0 | 8 of 8 |
| G: structured-first edge cases | 7 | 3 | 2 | 0 | 2 | 0 | 7 of 7 |
| H: trajectory and termination | 16 | 9 | 2 | 0 | 0 | 5 | 16 of 16 |
| **All cases** | 73 | 37 | 24 | 5 | 2 | 5 | 73 of 73 |

## 3. Decisions by focus

A case can carry several tags, so these rows overlap.

| Tag | Cases | True accept | True reject | False accept | False reject | N/A | Pass |
|---|---|---|---|---|---|---|---|
| `candidate_id` | 6 | 3 | 2 | 1 | 0 | 0 | 6 of 6 |
| `data_dependency` | 1 | 1 | 0 | 0 | 0 | 0 | 1 of 1 |
| `duplicate` | 1 | 1 | 0 | 0 | 0 | 0 | 1 of 1 |
| `knowledge_quote` | 9 | 3 | 5 | 1 | 0 | 0 | 9 of 9 |
| `limit` | 4 | 0 | 0 | 0 | 0 | 4 | 4 of 4 |
| `methodology_constant` | 5 | 3 | 0 | 0 | 2 | 0 | 5 of 5 |
| `mixed_source` | 8 | 5 | 2 | 1 | 0 | 0 | 8 of 8 |
| `number_provenance` | 15 | 5 | 9 | 1 | 0 | 0 | 15 of 15 |
| `paraphrase_faithfulness` | 1 | 0 | 0 | 1 | 0 | 0 | 1 of 1 |
| `recoverable_error` | 3 | 2 | 0 | 0 | 0 | 1 | 3 of 3 |
| `retry` | 2 | 0 | 2 | 0 | 0 | 0 | 2 of 2 |
| `small_common_number` | 2 | 1 | 0 | 0 | 1 | 0 | 2 of 2 |
| `structured_first` | 18 | 12 | 3 | 1 | 2 | 0 | 18 of 18 |
| `synthetic_id_form` | 1 | 0 | 0 | 1 | 0 | 0 | 1 of 1 |
| `trajectory` | 17 | 10 | 2 | 0 | 0 | 5 | 17 of 17 |
| `unknown` | 10 | 4 | 4 | 2 | 0 | 0 | 10 of 10 |

## 4. Grounding rules that rejected a first attempt

| Rule | Cases |
|---|---|
| `calculated_not_grounded` | 2 |
| `knowledge_quote_missing` | 1 |
| `missing_citation` | 2 |
| `mixed_number_not_structured` | 1 |
| `number_must_cite_structured_record` | 4 |
| `number_not_grounded` | 11 |
| `quote_not_in_cited_chunk` | 2 |
| `quote_too_short` | 1 |
| `quote_without_knowledge_citation` | 1 |
| `unknown_evidence_id` | 2 |
| `unknown_must_state_absence` | 3 |
| `unknown_site_reference` | 1 |
| `unknown_stated_as_fact` | 1 |

## 5. Terminations

| Termination | Cases |
|---|---|
| `answered` | 43 |
| `iteration_limit` | 1 |
| `provider_error` | 1 |
| `recoverable_error_limit` | 1 |
| `retrieval_call_limit` | 1 |
| `tool_call_limit` | 1 |
| `validation_failed` | 25 |

| Status | Cases |
|---|---|
| `answered` | 43 |
| `fallback` | 30 |

Runs that answered after one rejected answer (retries): 1. Fallbacks: 30.

## 6. Known limits (false accepts and false rejects)

These cases pin behaviour the validator is documented not to get right. The suite passes only while it stays exactly as pinned; each is counted above as the false accept or false reject it is.

| Case | Counted as | Why |
|---|---|---|
| A09 | false accept | Faithfulness of a paraphrase is not verified: the quotation only shows where the claim comes from (D-058). |
| C06 | false accept | A number that only documentation holds is accepted from the chunk it cites, even when a tool shows a different current value (D-058). |
| D07 | false accept | A statement passes if any cited record is not UNKNOWN; whether that record supports the claim is not checked (D-058). |
| D10 | false accept | A statement's kind is checked against its records for UNKNOWN and for CALCULATED, not for RETRIEVED_FACT or INFERRED, so a calculated value can be labelled a retrieved fact (M9 rule set, unchanged). |
| E06 | false accept | Only ids of the real form (cand- and 12 hex digits) are checked against the run's records; the SYNTHETIC world's short ids are outside that pattern (E02 covers the real form). |
| G03 | false reject | The number rule matches exact tokens: 30 in the Top-30 name equals the 5 km population of cand-a, so a knowledge-only statement is rejected as if it copied that record (D-058). |
| G05 | false reject | The rule cannot tell a methodology constant from a current value with the same token: 50 in the documented rule equals the current parameter (D-058). |

## 7. Cases

| Id | Kind | Title | Ground truth | Validator | Outcome | Ended | Pass |
|---|---|---|---|---|---|---|---|
| A01 | legitimate | A knowledge claim with a verbatim quotation | accept | accept | true accept | answered | yes |
| A02 | legitimate | A quotation of exactly the minimum length passes | accept | accept | true accept | answered | yes |
| A03 | adversarial | A quotation shorter than the minimum | reject | reject | true reject | validation_failed | yes |
| A04 | adversarial | A paraphrase of a chunk with no quotation | reject | reject | true reject | validation_failed | yes |
| A05 | adversarial | A quotation that exists in another retrieved chunk, not the cited one | reject | reject | true reject | validation_failed | yes |
| A06 | adversarial | A quotation that is in no chunk at all | reject | reject | true reject | validation_failed | yes |
| A07 | adversarial | A quotation with no cited knowledge chunk | reject | reject | true reject | validation_failed | yes |
| A08 | legitimate | Two knowledge chunks cited, each quoted | accept | accept | true accept | answered | yes |
| A09 | adversarial | A verbatim quotation attached to a paraphrase that misstates it | reject | accept | false accept | answered | yes |
| B01 | legitimate | A site's score copied from get_site | accept | accept | true accept | answered | yes |
| B02 | legitimate | A network share copied from network_summary | accept | accept | true accept | answered | yes |
| B03 | legitimate | Two sites' ranks from compare_sites, stated without a verdict | accept | accept | true accept | answered | yes |
| B04 | legitimate | A count of nearby sites from nearby_sites | accept | accept | true accept | answered | yes |
| B05 | legitimate | A component score from explain_score | accept | accept | true accept | answered | yes |
| B06 | legitimate | A network-contribution figure | accept | accept | true accept | answered | yes |
| B07 | adversarial | A score that no record shows | reject | reject | true reject | validation_failed | yes |
| B08 | adversarial | A factual statement with no citation | reject | reject | true reject | validation_failed | yes |
| B09 | adversarial | A citation of a record that was never returned | reject | reject | true reject | validation_failed | yes |
| C01 | legitimate | A methodology claim with a network number | accept | accept | true accept | answered | yes |
| C02 | legitimate | A methodology claim with a site id | accept | accept | true accept | answered | yes |
| C03 | legitimate | A methodology claim with a network metric | accept | accept | true accept | answered | yes |
| C04 | legitimate | A structured score with the documented method | accept | accept | true accept | answered | yes |
| C05 | adversarial | A number taken from documentation while the tool shows a different value | reject | reject | true reject | validation_failed | yes |
| C06 | adversarial | A documentation-only number stated as current while the tool shows a different value | reject | accept | false accept | answered | yes |
| C07 | adversarial | An invented number in a mixed statement | reject | reject | true reject | validation_failed | yes |
| C08 | legitimate | The same number in a tool and in the documentation, both cited | accept | accept | true accept | answered | yes |
| D01 | legitimate | UNKNOWN on unknown-only evidence | accept | accept | true accept | answered | yes |
| D02 | adversarial | An UNKNOWN record stated as a retrieved fact | reject | reject | true reject | validation_failed | yes |
| D03 | adversarial | UNKNOWN turned into "no issue" | reject | reject | true reject | validation_failed | yes |
| D04 | adversarial | UNKNOWN turned into "confirmed" | reject | reject | true reject | validation_failed | yes |
| D05 | adversarial | UNKNOWN turned into "not applicable" | reject | reject | true reject | validation_failed | yes |
| D06 | legitimate | UNKNOWN stated next to supporting positive evidence | accept | accept | true accept | answered | yes |
| D07 | adversarial | A fact asserted on positive evidence that does not speak to the unknown | reject | accept | false accept | answered | yes |
| D08 | legitimate | UNKNOWN with no evidence when the tools hold nothing on the question | accept | accept | true accept | answered | yes |
| D09 | legitimate | An INFERRED record stated as INFERRED keeps its type | accept | accept | true accept | answered | yes |
| D10 | adversarial | A CALCULATED record presented as a retrieved fact | reject | accept | false accept | answered | yes |
| E01 | legitimate | A fetched site named and cited | accept | accept | true accept | answered | yes |
| E02 | adversarial | A site id of the real form that no record holds | reject | reject | true reject | validation_failed | yes |
| E03 | adversarial | A record of a site that was never fetched | reject | reject | true reject | validation_failed | yes |
| E04 | legitimate | get_site uses the id find_sites returned | accept | accept | true accept | answered | yes |
| E05 | legitimate | Two fetched sites named together | accept | accept | true accept | answered | yes |
| E06 | adversarial | A site never fetched, named in the text with a synthetic id | reject | accept | false accept | answered | yes |
| F01 | legitimate | A number copied exactly from a cited record | accept | accept | true accept | answered | yes |
| F02 | adversarial | A number that is close to the record's but not equal | reject | reject | true reject | validation_failed | yes |
| F03 | adversarial | A number copied from an unrelated knowledge chunk | reject | reject | true reject | validation_failed | yes |
| F04 | legitimate | A derived number that deterministic code computed and a record holds | accept | accept | true accept | answered | yes |
| F05 | adversarial | A difference the model computed itself | reject | reject | true reject | validation_failed | yes |
| F06 | legitimate | A parameter shown by a tool and by the documentation, both cited | accept | accept | true accept | answered | yes |
| F07 | adversarial | A number that appears only in the question | reject | reject | true reject | validation_failed | yes |
| F08 | legitimate | A number in the question that the record also holds | accept | accept | true accept | answered | yes |
| G01 | legitimate | A methodology constant that only the documentation holds | accept | accept | true accept | answered | yes |
| G02 | legitimate | A number only a tool holds, no documentation searched | accept | accept | true accept | answered | yes |
| G03 | legitimate | A documentation name containing 30, while an unrelated record shows 30 | accept | reject | false reject | validation_failed | yes |
| G04 | legitimate | The same statement citing the record too | accept | accept | true accept | answered | yes |
| G05 | legitimate | A methodology constant that a tool also shows as the current parameter | accept | reject | false reject | validation_failed | yes |
| G06 | adversarial | A current parameter copied from documentation although the tool shows it | reject | reject | true reject | validation_failed | yes |
| G07 | adversarial | A network figure attributed to documentation that no source shows | reject | reject | true reject | validation_failed | yes |
| H01 | legitimate | search, then answer | accept | accept | true accept | answered | yes |
| H02 | legitimate | structured tool, then answer | accept | accept | true accept | answered | yes |
| H03 | legitimate | search, then structured tool, then answer | accept | accept | true accept | answered | yes |
| H04 | legitimate | structured tool, then search, then answer | accept | accept | true accept | answered | yes |
| H05 | legitimate | several structured tools, then answer | accept | accept | true accept | answered | yes |
| H06 | legitimate | several searches, then answer | accept | accept | true accept | answered | yes |
| H07 | legitimate | a refused tool call, then the corrected call, then answer | accept | accept | true accept | answered | yes |
| H08 | legitimate | an identical call is reused, not run again | accept | accept | true accept | answered | yes |
| H09 | adversarial | the tool budget is reached | none | none | not applicable | tool_call_limit | yes |
| H10 | adversarial | the retrieval budget is reached | none | none | not applicable | retrieval_call_limit | yes |
| H11 | adversarial | the iteration budget is reached | none | none | not applicable | iteration_limit | yes |
| H12 | adversarial | too many refused calls | none | none | not applicable | recoverable_error_limit | yes |
| H13 | legitimate | an unknown tool is refused, then the run continues | accept | accept | true accept | answered | yes |
| H14 | adversarial | the provider fails after one tool call | none | none | not applicable | provider_error | yes |
| H15 | adversarial | an invalid answer, one retry, a valid answer | reject | reject | true reject | answered | yes |
| H16 | adversarial | an invalid answer, one retry, a second invalid answer | reject | reject | true reject | validation_failed | yes |

## 8. Limitations

- The case set is small and hand-written. It checks that each rule fires and does not fire where it should not; it is not a sample of real questions.
- The provider is scripted: the cases show what the validator and the loop do with an answer, never what a model would write.
- The world is SYNTHETIC. Its candidate ids are not in the 12-hex-digit form of real ids, so the unknown-site rule is exercised with an id of the real form and the short synthetic ids are outside it.
- A quotation proves where a claim comes from, not that the paraphrase is faithful to it. Faithfulness is not verified (D-058).
- The number rules match exact number tokens. They cannot tell a methodology constant from a current value that happens to be the same token, so some legitimate statements are rejected and a number only documentation holds is accepted (D-058).
- Fixture values come from the tool outputs the scripted provider was shown, except literal quotations, which must stay verbatim in the current documents; a stale quotation shows up as a failing case.
- Actual grid connection feasibility requires utility confirmation.
