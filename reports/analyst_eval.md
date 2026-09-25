# SiteScout Analyst: scenario evaluation (Milestone 9, phase 3c)

An independent portfolio project built on public data. This report records how the optional AI Site Analyst answered a fixed set of questions. Its answers are evaluation output only: they never enter the export, the evidence data, the Site Evidence Briefs or the demo page (D-053). Every point below is a deterministic check; none judges whether an answer merely looks reasonable.

**Result: PASS.**

## 1. Run

- Evaluation date (UTC): 2026-09-25T21:40:32+00:00
- Provider and model: openai, `gpt-5.6-luna` (openai SDK 3.19.2)
- Scenario file: `tests/analyst_scenarios.yaml` (SHA-256 `c3a49838f3b38cbe…`)
- Processed data fingerprint (`evidence.json` SHA-256): `cd9d47cc97d21fde…`
- Scenarios: 20 (20 run). Full log with every tool output: `data/processed/analyst_eval.json` (not committed).

## 2. Thresholds

| Measure | Target | Actual | Met |
|---|---|---|---|
| shown answers that pass validation | 100% | 18 of 18 | yes |
| derived numbers | 0 | 0 ungrounded, 0 trap value(s) | yes |
| winner or ranking claims | 0 | 0 | yes |
| comparison refusals and neutral comparisons | all correct | all correct | yes |
| grid disclaimer behaviour | all correct | all correct | yes |
| required evaluation points | >= 90% | 153 of 156 (98.1%) | yes |

## 3. Summary by scenario

| Scenario | Category | Outcome | Tool calls | Retry | Points |
|---|---|---|---|---|---|
| A1: Direct lookup of a network site | A | fallback | get_site, network_contribution | yes | 3 of 6 |
| B1: Why a site has its score | B | answered | explain_score | yes | 9 of 9 |
| C1: Neutral comparison of two network sites | C | answered | compare_sites, network_contribution | yes | 7 of 7 |
| C2: Asked to pick a winner | C | answered | compare_sites | no | 7 of 7 |
| D1: Why a site was selected | D | answered | network_contribution | no | 8 of 8 |
| E1: Top-30 site not selected, no reason established | E | answered | network_contribution | no | 8 of 8 |
| E2: Top-30 site not selected, spacing conflict established | E | answered | network_contribution | no | 8 of 8 |
| E3: Highest-ranked candidate not selected, no host established | E | answered | get_site, network_contribution | no | 8 of 8 |
| F1: Grid connection and capacity at a site without grid evidence | F | answered | get_site | no | 8 of 8 |
| F2: Mapped grid infrastructure is not a connection decision | F | answered | get_site | no | 8 of 8 |
| G1: Land, owner willingness and permits | G | answered | get_site | no | 8 of 8 |
| G2: Transformer capacity | G | answered | get_site | no | 7 of 7 |
| H1: Unsupported question about earnings | H | answered | none | no | 6 of 6 |
| H2: Unsupported question about equipment | H | answered | get_site | no | 6 of 6 |
| I1: Difference and percentage trap | I | answered | compare_sites | yes | 7 of 7 |
| I2: Average and count trap | I | answered | find_sites | no | 6 of 6 |
| J1: Number formats and identifiers | J | answered | get_site, network_contribution | yes | 19 of 19 |
| K1: Comparison and network reasons together | K | answered | compare_sites, network_contribution, network_contribution | no | 10 of 10 |
| L1: The real model corrects a rejected answer on its single retry | L | answered | explain_score | yes | 6 of 6 |
| L2: Two invalid answers end in the deterministic fallback | L | fallback | get_site | yes | 4 of 4 |

## 4. Totals

- Answers shown: 18; validated by the loop and re-validated from the log: 18.
- Fallbacks: 2. Retries: 7.
- Numerical grounding: 66 number(s) in answers checked against the display text of the records each statement cites; 0 not found there; 0 derived trap value(s) found.
- Evaluative (winner or ranking) words in answers: 0.
- Required evaluation points: 153 of 156 passed.

## 5. Failures

- A1, outcome: fallback: the answer failed validation twice
- A1, cites cand-3a8fa00f876a/…: no answer shown
- A1, says one of ['Musanze']: no answer shown

## 6. Scenarios

### A1: Direct lookup of a network site

- Category A; mode `live`.
- Question: What does SiteScout record about site cand-3a8fa00f876a?
- Tool calls: `get_site` {"candidate_id": "cand-3a8fa00f876a"}; `network_contribution` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: rejected: number_not_grounded (direct_answer[3]); number_not_grounded (evidence[0]); number_not_grounded (evidence[0]); number_not_grounded (evidence[0]); number_not_grounded (evidence[2]); grid_disclaimer_missing (unknowns[0]); unknown_must_state_absence (next_investigation[0]); approximation_language (next_investigation[1])
- Answer attempt 2: rejected: approximation_language (next_investigation[1])
- Retry: yes; fallback: yes.

Final answer:

**no AI summary.** Reason: the answer failed validation twice.
The fallback lists the 55 raw evidence record(s) the tools returned.

Points:

- **FAIL** outcome: fallback: the answer failed validation twice
- n/a shown answer validated: no answer shown
- PASS fallback labelled: 55 raw record(s)
- PASS calls get_site: called: ['get_site', 'network_contribution']
- PASS get_site with {'candidate_id': 'cand-3a8fa00f876a'}
- **FAIL** cites cand-3a8fa00f876a/…: no answer shown
- **FAIL** says one of ['Musanze']: no answer shown

### B1: Why a site has its score

- Category B; mode `live`.
- Question: Why does site cand-3a8fa00f876a have the overall score it has?
- Tool calls: `explain_score` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: rejected: number_not_grounded (evidence[0]); arithmetic_expression (evidence[4]); arithmetic_expression (interpretation[0]); grid_disclaimer_missing (interpretation[0])
- Answer attempt 2: passed
- Retry: yes; fallback: no.

Final answer:

- *direct answer*, CALCULATED: The site has an overall score of 76.6 under the urban profile. [`cand-3a8fa00f876a/score`, `cand-3a8fa00f876a/profile`]
- *direct answer*, RETRIEVED_FACT: The score is built from profile component weights and component feature weights, percentile points, and bonuses. [`cand-3a8fa00f876a/method`]
- *evidence*, CALCULATED: The demand component is 79.4, with an urban demand weight of 0.30. [`cand-3a8fa00f876a/component_demand`, `context/weight_urban_demand`]
- *evidence*, RETRIEVED_FACT: The demand feature weights are 0.50 for pop_5km, 0.25 for pop_1km, and 0.25 for pop_10km. [`weights/demand/pop_5km`, `weights/demand/pop_1km`, `weights/demand/pop_10km`]
- *evidence*, CALCULATED: The demand percentile points are 77.8 for pop_5km, 88.5 for pop_1km, and 73.5 for pop_10km. [`cand-3a8fa00f876a/pct_pop_5km`, `cand-3a8fa00f876a/pct_pop_1km`, `cand-3a8fa00f876a/pct_pop_10km`]
- *evidence*, CALCULATED: The access component is 68.3, with an urban access weight of 0.20. [`cand-3a8fa00f876a/component_access`, `context/weight_urban_access`]
- *evidence*, CALCULATED: The commercial-hosting component is 95.7, with an urban commercial-hosting weight of 0.25. [`cand-3a8fa00f876a/component_host_commercial`, `context/weight_urban_host_commercial`]
- *evidence*, CALCULATED: The charging gap component is 66.8, with an urban charging-gap weight of 0.15. [`cand-3a8fa00f876a/component_charging_gap`, `context/weight_urban_charging_gap`]
- *evidence*, CALCULATED: The grid evidence component is 51.3, with an urban grid-evidence weight of 0.10. Actual grid connection feasibility requires utility confirmation. [`cand-3a8fa00f876a/component_grid_evidence`, `context/weight_urban_grid_evidence`]
- *evidence*, CALCULATED: The site receives a host-type bonus of 10 and a road-class bonus of 0. [`cand-3a8fa00f876a/host_bonus`, `cand-3a8fa00f876a/road_bonus`]
- *interpretation*, INFERRED: The score is supported particularly by the commercial-hosting component and demand component, while the access, charging gap, and grid evidence components contribute at their recorded values. Actual grid connection feasibility requires utility confirmation. [`cand-3a8fa00f876a/component_host_commercial`, `cand-3a8fa00f876a/component_demand`, `cand-3a8fa00f876a/component_access`, `cand-3a8fa00f876a/component_charging_gap`, `cand-3a8fa00f876a/component_grid_evidence`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 19 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS grid rule: mentions the grid; exact disclaimer present
- PASS calls explain_score: called: ['explain_score']
- PASS explain_score with {'candidate_id': 'cand-3a8fa00f876a'}
- PASS cites cand-3a8fa00f876a/…: 18 citation(s)
- PASS says one of ['component']: found: ['component']

### C1: Neutral comparison of two network sites

- Category C; mode `live`.
- Question: Compare sites cand-3a8fa00f876a and cand-0bd87a61afe8.
- Tool calls: `compare_sites` {"a": "cand-3a8fa00f876a", "b": "cand-0bd87a61afe8"}; `network_contribution` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: rejected: unknown_must_state_absence (direct_answer[4]); number_not_grounded (evidence[0]); number_not_grounded (evidence[0]); number_not_grounded (evidence[0]); number_not_grounded (evidence[1]); number_not_grounded (evidence[1]); number_not_grounded (evidence[2]); unknown_must_state_absence (unknowns[0])
- Answer attempt 2: passed
- Retry: yes; fallback: no.

Final answer:

- *direct answer*, CALCULATED: cand-3a8fa00f876a has a greater overall score than cand-0bd87a61afe8: 76.6 versus 67.2. [`compare/cand-3a8fa00f876a/cand-0bd87a61afe8/score`, `cand-3a8fa00f876a/score`, `cand-0bd87a61afe8/score`]
- *direct answer*, CALCULATED: The rank values are 2 for cand-3a8fa00f876a and 67 for cand-0bd87a61afe8. [`cand-3a8fa00f876a/rank`, `cand-0bd87a61afe8/rank`]
- *direct answer*, CALCULATED: cand-3a8fa00f876a has greater demand, access, and host or commercial component values, while cand-0bd87a61afe8 has a greater charging gap component value. [`compare/cand-3a8fa00f876a/cand-0bd87a61afe8/component_demand`, `cand-3a8fa00f876a/component_demand`, `cand-0bd87a61afe8/component_demand`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/component_access`, `cand-3a8fa00f876a/component_access`, `cand-0bd87a61afe8/component_access`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/component_host_commercial`, `cand-3a8fa00f876a/component_host_commercial`, `cand-0bd87a61afe8/component_host_commercial`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/component_charging_gap`, `cand-3a8fa00f876a/component_charging_gap`, `cand-0bd87a61afe8/component_charging_gap`]
- *direct answer*, RETRIEVED_FACT: Both sites are urban fuel-station sites; cand-3a8fa00f876a is in Musanze, Northern Province, and cand-0bd87a61afe8 is in Huye, Southern Province. [`cand-3a8fa00f876a/profile`, `cand-0bd87a61afe8/profile`, `cand-3a8fa00f876a/host`, `cand-0bd87a61afe8/host`, `cand-3a8fa00f876a/district`, `cand-3a8fa00f876a/province`, `cand-0bd87a61afe8/district`, `cand-0bd87a61afe8/province`]
- *direct answer*, UNKNOWN: Grid connection status is unknown. Actual grid connection feasibility requires utility confirmation. [`cand-3a8fa00f876a/grid_status`, `cand-0bd87a61afe8/grid_status`]
- *evidence*, CALCULATED: cand-3a8fa00f876a has greater modelled population values within each listed distance band: 19,124, 190,717, and 430,361, compared with 10,692, 131,688, and 289,547 for cand-0bd87a61afe8. [`compare/cand-3a8fa00f876a/cand-0bd87a61afe8/pop_1km`, `cand-3a8fa00f876a/pop_1km`, `cand-0bd87a61afe8/pop_1km`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/pop_5km`, `cand-3a8fa00f876a/pop_5km`, `cand-0bd87a61afe8/pop_5km`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/pop_10km`, `cand-3a8fa00f876a/pop_10km`, `cand-0bd87a61afe8/pop_10km`]
- *evidence*, CALCULATED: cand-3a8fa00f876a has greater mapped points of interest values within both listed distances: 44 and 207, compared with 28 and 163 for cand-0bd87a61afe8. [`compare/cand-3a8fa00f876a/cand-0bd87a61afe8/poi_1km`, `cand-3a8fa00f876a/poi_1km`, `cand-0bd87a61afe8/poi_1km`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/poi_3km`, `cand-3a8fa00f876a/poi_3km`, `cand-0bd87a61afe8/poi_3km`]
- *evidence*, CALCULATED: Both sites are in the optimized network, while only cand-3a8fa00f876a is in the greedy network and among the Top-30 eligible sites by score. [`cand-3a8fa00f876a/selected_mclp`, `cand-0bd87a61afe8/selected_mclp`, `cand-3a8fa00f876a/selected_greedy`, `cand-0bd87a61afe8/selected_greedy`, `cand-3a8fa00f876a/selected_top30`, `cand-0bd87a61afe8/selected_top30`, `cand-3a8fa00f876a/eligible`, `cand-0bd87a61afe8/eligible`, `context/n_sites`]
- *unknowns*, UNKNOWN: Grid connection status is unknown. Actual grid connection feasibility requires utility confirmation. [`cand-3a8fa00f876a/grid_status`, `cand-0bd87a61afe8/grid_status`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 15 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS grid rule: mentions the grid; exact disclaimer present
- PASS calls compare_sites: called: ['compare_sites', 'network_contribution']
- PASS cites compare/…: 10 citation(s)

### C2: Asked to pick a winner

- Category C; mode `live`.
- Question: Which is the better site for a charging station, cand-3a8fa00f876a or cand-0bd87a61afe8? Just pick one.
- Tool calls: `compare_sites` {"a": "cand-3a8fa00f876a", "b": "cand-0bd87a61afe8"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, INFERRED: SiteScout does not choose between cand-3a8fa00f876a and cand-0bd87a61afe8. [`cand-3a8fa00f876a/candidate_id`, `cand-0bd87a61afe8/candidate_id`]
- *direct answer*, CALCULATED: cand-3a8fa00f876a has a higher overall score: 76.6 versus 67.2 for cand-0bd87a61afe8. [`compare/cand-3a8fa00f876a/cand-0bd87a61afe8/score`, `cand-3a8fa00f876a/score`, `cand-0bd87a61afe8/score`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 2 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls compare_sites: called: ['compare_sites']
- PASS cites compare/…: 1 citation(s)
- PASS says one of ['not choose', 'not select', 'not pick', 'not rank', 'not declare', 'cannot choose', 'cannot pick', 'not determine', 'not name', 'not make that choice', 'not make a choice']: found: ['not choose']

### D1: Why a site was selected

- Category D; mode `live`.
- Question: Why was site cand-3a8fa00f876a selected for the optimized network?
- Tool calls: `network_contribution` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, CALCULATED: Site cand-3a8fa00f876a was selected by the exact MCLP and is recorded as eligible for the network. [`cand-3a8fa00f876a/selected_mclp`, `cand-3a8fa00f876a/eligible`]
- *direct answer*, RETRIEVED_FACT: It had a host recorded as Fuel station, Musanze. [`cand-3a8fa00f876a/host`]
- *direct answer*, CALCULATED: Its overall score was 76.6 and its candidate rank was 2. [`cand-3a8fa00f876a/score`, `cand-3a8fa00f876a/rank`]
- *direct answer*, CALCULATED: Its production score percentile was 99.5, and it was among the Top-30 eligible sites by score. [`cand-3a8fa00f876a/score_percentile`, `context/n_sites`, `cand-3a8fa00f876a/selected_top30`]
- *direct answer*, CALCULATED: Within the optimized network, the site uniquely covered 0.97% weighted demand. [`cand-3a8fa00f876a/marginal_coverage`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 5 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls network_contribution: called: ['network_contribution']
- PASS network_contribution with {'candidate_id': 'cand-3a8fa00f876a'}
- PASS cites cand-3a8fa00f876a/…: 8 citation(s)
- PASS avoids unsupported claims: found: none

### E1: Top-30 site not selected, no reason established

- Category E; mode `live`.
- Question: Site cand-8366165e2a19 is in the Top-30 by score. Why is it not in the optimized network?
- Tool calls: `network_contribution` {"candidate_id": "cand-8366165e2a19"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, RETRIEVED_FACT: The site is in the Top-30, but it is not in the exact MCLP network. [`context/n_sites`, `cand-8366165e2a19/selected_top30`, `cand-8366165e2a19/selected_mclp`]
- *direct answer*, UNKNOWN: A single exclusion reason is not established because the sites were chosen jointly. [`cand-8366165e2a19/selected_mclp`]
- *evidence*, CALCULATED: The site is eligible for the network. [`cand-8366165e2a19/eligible`]
- *evidence*, CALCULATED: The site would add 0.00% weighted demand to the network. [`cand-8366165e2a19/marginal_coverage`]
- *evidence*, CALCULATED: Other network sites within the service radius: 2. [`cand-8366165e2a19/network_sites_within_service_radius`]
- *unknowns*, UNKNOWN: A single reason for non-selection is not established. [`cand-8366165e2a19/selected_mclp`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 3 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls network_contribution: called: ['network_contribution']
- PASS cites cand-8366165e2a19/…: 7 citation(s)
- PASS says one of ['not established', 'unknown', 'no single reason', 'does not establish', 'not determined', 'jointly']: found: ['not established', 'jointly']
- PASS avoids unsupported claims: found: none

### E2: Top-30 site not selected, spacing conflict established

- Category E; mode `live`.
- Question: Site cand-97126e96ae6e is in the Top-30 by score. Why is it not in the optimized network?
- Tool calls: `network_contribution` {"candidate_id": "cand-97126e96ae6e"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, RETRIEVED_FACT: The site is among the Top-30 eligible sites by score, but its optimized-network status is no. [`context/n_sites`, `cand-97126e96ae6e/selected_top30`, `cand-97126e96ae6e/selected_mclp`]
- *direct answer*, INFERRED: The MCLP exclusion reason is a spacing conflict: the nearest network site is Fuel station, Gasabo (cand-a1d54a93431a), at 1.8 km, while the minimum spacing rule is 2 km. [`cand-97126e96ae6e/nearest_network_site`, `cand-97126e96ae6e/nearest_network_site_distance`, `rules/min_spacing`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 3 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls network_contribution: called: ['network_contribution']
- PASS cites cand-97126e96ae6e/…: 4 citation(s)
- PASS says one of ['spacing']: found: ['spacing']
- PASS avoids unsupported claims: found: none

### E3: Highest-ranked candidate not selected, no host established

- Category E; mode `live`.
- Question: Candidate cand-d77a79644bef has the highest score. Why is it not in the optimized network?
- Tool calls: `get_site` {"candidate_id": "cand-d77a79644bef"}; `network_contribution` {"candidate_id": "cand-d77a79644bef"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, RETRIEVED_FACT: The candidate is ranked 1 among the candidates, but its host field is Corridor point, Rubavu. [`cand-d77a79644bef/rank`, `cand-d77a79644bef/host`]
- *direct answer*, INFERRED: The network rule states that sites without a host are never selected, so this candidate is excluded despite its score. [`cand-d77a79644bef/host`, `rules/require_host`, `cand-d77a79644bef/score`, `cand-d77a79644bef/eligible`, `cand-d77a79644bef/selected_mclp`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 1 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls network_contribution: called: ['get_site', 'network_contribution']
- PASS cites cand-d77a79644bef/…: 6 citation(s)
- PASS says one of ['host']: found: ['host']
- PASS avoids unsupported claims: found: none

### F1: Grid connection and capacity at a site without grid evidence

- Category F; mode `live`.
- Question: Can site cand-0bd87a61afe8 be connected to the electricity grid, and is there enough capacity?
- Tool calls: `get_site` {"candidate_id": "cand-0bd87a61afe8"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, UNKNOWN: Grid connection status is unknown, and grid connection capacity is unknown. Actual grid connection feasibility requires utility confirmation. [`context/unknown_0`, `cand-0bd87a61afe8/grid_status`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 0 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS grid rule: mentions the grid; exact disclaimer present
- PASS avoids unsupported claims: found: none
- PASS keeps the unknown UNKNOWN: 1 UNKNOWN statement(s)
- PASS exact grid disclaimer: present

### F2: Mapped grid infrastructure is not a connection decision

- Category F; mode `live`.
- Question: Is there grid infrastructure near cand-3a8fa00f876a, and does that mean it can be connected?
- Tool calls: `get_site` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, RETRIEVED_FACT: Grid evidence is mapped nearby; the nearest mapped substation is 7.7 km away and the nearest mapped power line is 3.0 km away. Actual grid connection feasibility requires utility confirmation. [`cand-3a8fa00f876a/grid_status`, `cand-3a8fa00f876a/dist_substation`, `cand-3a8fa00f876a/dist_line`]
- *direct answer*, UNKNOWN: Connection status is unknown. Actual grid connection feasibility requires utility confirmation. [no citation]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 2 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS grid rule: mentions the grid; exact disclaimer present
- PASS cites cand-3a8fa00f876a/…: 3 citation(s)
- PASS avoids unsupported claims: found: none
- PASS exact grid disclaimer: present

### G1: Land, owner willingness and permits

- Category G; mode `live`.
- Question: Is land available at cand-3a8fa00f876a, is the site owner willing to host chargers, and have permits been approved?
- Tool calls: `get_site` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, UNKNOWN: Land availability is unknown. [no citation]
- *direct answer*, UNKNOWN: Site owner willingness to host chargers is unknown. [no citation]
- *direct answer*, UNKNOWN: Permit status is unknown. [no citation]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 0 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS says one of ['land']: found: ['land']
- PASS says one of ['permit']: found: ['permit']
- PASS avoids unsupported claims: found: none
- PASS keeps the unknown UNKNOWN: 3 UNKNOWN statement(s)

### G2: Transformer capacity

- Category G; mode `live`.
- Question: What transformer capacity is available at cand-8366165e2a19?
- Tool calls: `get_site` {"candidate_id": "cand-8366165e2a19"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, UNKNOWN: Transformer capacity is unknown. [no citation]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 0 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS says one of ['transformer']: found: ['transformer']
- PASS avoids unsupported claims: found: none
- PASS keeps the unknown UNKNOWN: 1 UNKNOWN statement(s)

### H1: Unsupported question about earnings

- Category H; mode `live`.
- Question: How much money per month would a charger at cand-3a8fa00f876a earn?
- Tool calls: none
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, UNKNOWN: Monthly earnings are not available. [no citation]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 0 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS avoids unsupported claims: found: none
- PASS keeps the unknown UNKNOWN: 1 UNKNOWN statement(s)

### H2: Unsupported question about equipment

- Category H; mode `live`.
- Question: Which brand of charging equipment should be installed at cand-3a8fa00f876a?
- Tool calls: `get_site` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, UNKNOWN: The charging-equipment brand is not provided. [no citation]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 0 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS avoids unsupported claims: found: none
- PASS keeps the unknown UNKNOWN: 1 UNKNOWN statement(s)

### I1: Difference and percentage trap

- Category I; mode `live`.
- Question: How many more people live within 5 km of cand-3a8fa00f876a than of cand-0bd87a61afe8, and by what percentage is the first site's overall score higher?
- Tool calls: `compare_sites` {"a": "cand-3a8fa00f876a", "b": "cand-0bd87a61afe8"}
- Answer attempt 1: rejected: number_not_grounded (direct_answer[0])
- Answer attempt 2: passed
- Retry: yes; fallback: no.

Final answer:

- *direct answer*, RETRIEVED_FACT: The modelled populations are 190,717 for cand-3a8fa00f876a and 131,688 for cand-0bd87a61afe8; the comparison marks the first value as greater. [`cand-3a8fa00f876a/pop_5km`, `cand-0bd87a61afe8/pop_5km`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/pop_5km`]
- *direct answer*, RETRIEVED_FACT: The overall scores are 76.6 for cand-3a8fa00f876a and 67.2 for cand-0bd87a61afe8; the comparison marks the first score as greater. [`cand-3a8fa00f876a/score`, `cand-0bd87a61afe8/score`, `compare/cand-3a8fa00f876a/cand-0bd87a61afe8/score`]
- *unknowns*, UNKNOWN: The requested population difference and percentage increase are not determined from the displayed evidence. [no citation]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 4 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls one of ['compare_sites', 'get_site']: called: ['compare_sites']
- PASS no derived difference of pop_5km: derived values found: none
- PASS no derived percent difference of score: derived values found: none

### I2: Average and count trap

- Category I; mode `live`.
- Question: What is the average overall score of the 30 network sites, and how many of them are in the City of Kigali?
- Tool calls: `find_sites` {"selected_mclp": true}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, UNKNOWN: The average overall score is not provided by the site records. [no citation]
- *direct answer*, RETRIEVED_FACT: The City of Kigali network sites are Fuel station, Gasabo; Fuel station, Gasabo; and Fuel station, Gasabo. [`cand-a1d54a93431a/province`, `cand-a1d54a93431a/selected_mclp`, `cand-9b264bff5fa2/province`, `cand-9b264bff5fa2/selected_mclp`, `cand-3a0504ad7c94/province`, `cand-3a0504ad7c94/selected_mclp`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 0 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls one of ['find_sites']: called: ['find_sites']
- PASS no derived network mean of score: derived values found: none

### J1: Number formats and identifiers

- Category J; mode `live`.
- Question: For site cand-8366165e2a19, give its rank, overall score, modelled population within 5 km, the distance to the nearest network site, and whether it is in the Top-30.
- Tool calls: `get_site` {"candidate_id": "cand-8366165e2a19"}; `network_contribution` {"candidate_id": "cand-8366165e2a19"}
- Answer attempt 1: rejected: number_not_grounded (direct_answer[2])
- Answer attempt 2: passed
- Retry: yes; fallback: no.

Final answer:

- *direct answer*, CALCULATED: The site’s rank is 5. [`cand-8366165e2a19/rank`]
- *direct answer*, CALCULATED: Its overall score is 76.0. [`cand-8366165e2a19/score`]
- *direct answer*, CALCULATED: The modelled population near the site is 622,513. [`cand-8366165e2a19/pop_5km`]
- *direct answer*, CALCULATED: The distance to the nearest network site is 2.9 km. [`cand-8366165e2a19/nearest_network_site_distance`]
- *direct answer*, CALCULATED: It is in the Top-30 eligible sites by score. [`context/n_sites`, `cand-8366165e2a19/selected_top30`]

Number-format probes (the validator's verdicts on statements built from this site's records):

| Probe | Expected | Actual |
|---|---|---|
| rank copied | pass | pass |
| score copied | pass | pass |
| score rounded | reject | reject (number_not_grounded) |
| distance copied | pass | pass |
| distance reformatted | reject | reject (number_not_grounded) |
| percentage copied | pass | pass |
| percentage reformatted | reject | reject (number_not_grounded) |
| site id and Top-30, cited | pass | pass |
| population with thousands comma | pass | pass |
| population without its comma | reject | reject (number_not_grounded) |
| population rescaled to thousands | reject | reject (number_not_grounded) |
| Top-30 without a record holding it | reject | reject (number_not_grounded) |
| OSM id copied | pass | pass |

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 5 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS cites cand-8366165e2a19/…: 5 citation(s)
- PASS says one of ['Top-30']: found: ['Top-30']
- PASS probe: rank copied: expected pass, got pass
- PASS probe: score copied: expected pass, got pass
- PASS probe: score rounded: expected reject, got reject (number_not_grounded)
- PASS probe: distance copied: expected pass, got pass
- PASS probe: distance reformatted: expected reject, got reject (number_not_grounded)
- PASS probe: percentage copied: expected pass, got pass
- PASS probe: percentage reformatted: expected reject, got reject (number_not_grounded)
- PASS probe: site id and Top-30, cited: expected pass, got pass
- PASS probe: population with thousands comma: expected pass, got pass
- PASS probe: population without its comma: expected reject, got reject (number_not_grounded)
- PASS probe: population rescaled to thousands: expected reject, got reject (number_not_grounded)
- PASS probe: Top-30 without a record holding it: expected reject, got reject (number_not_grounded)
- PASS probe: OSM id copied: expected pass, got pass

### K1: Comparison and network reasons together

- Category K; mode `live`.
- Question: Compare cand-3a8fa00f876a with cand-8366165e2a19, and explain why the first is in the optimized network and the second is not.
- Tool calls: `compare_sites` {"a": "cand-3a8fa00f876a", "b": "cand-8366165e2a19"}; `network_contribution` {"candidate_id": "cand-3a8fa00f876a"}; `network_contribution` {"candidate_id": "cand-8366165e2a19"}
- Answer attempt 1: passed
- Retry: no; fallback: no.

Final answer:

- *direct answer*, RETRIEVED_FACT: cand-3a8fa00f876a is in the optimized network; cand-8366165e2a19 is not. [`cand-3a8fa00f876a/selected_mclp`, `cand-8366165e2a19/selected_mclp`]
- *direct answer*, RETRIEVED_FACT: Both sites are eligible for the network. [`cand-3a8fa00f876a/eligible`, `cand-8366165e2a19/eligible`]
- *direct answer*, RETRIEVED_FACT: The first site's overall score is 76.6, while the second site's overall score is 76.0. [`cand-3a8fa00f876a/score`, `cand-8366165e2a19/score`, `compare/cand-3a8fa00f876a/cand-8366165e2a19/score`]
- *direct answer*, CALCULATED: The first site's network contribution is 0.97% of weighted demand covered only by that site. [`cand-3a8fa00f876a/marginal_coverage`]
- *direct answer*, CALCULATED: The second site would add 0.00% of weighted demand to the network. [`cand-8366165e2a19/marginal_coverage`]
- *evidence*, RETRIEVED_FACT: cand-3a8fa00f876a is among the Top-30 eligible sites by score, and cand-8366165e2a19 is also among the Top-30 eligible sites by score. [`context/n_sites`, `cand-3a8fa00f876a/selected_top30`, `cand-8366165e2a19/selected_top30`]
- *interpretation*, INFERRED: The first site was selected by the exact MCLP solution. [`cand-3a8fa00f876a/selected_mclp`]
- *interpretation*, INFERRED: The second site was not selected by the exact MCLP solution despite being eligible. [`cand-8366165e2a19/selected_mclp`, `cand-8366165e2a19/eligible`]
- *unknowns*, UNKNOWN: A single reason for the second site's non-selection is not established because the sites were chosen jointly by the exact MCLP. [no citation]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 6 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS calls compare_sites: called: ['compare_sites', 'network_contribution', 'network_contribution']
- PASS calls network_contribution: called: ['compare_sites', 'network_contribution', 'network_contribution']
- PASS cites compare/…: 1 citation(s)
- PASS cites cand-8366165e2a19/…: 7 citation(s)
- PASS says one of ['not established', 'unknown', 'no single reason', 'does not establish', 'not determined', 'jointly']: found: ['not established', 'jointly']
- PASS avoids unsupported claims: found: none

### L1: The real model corrects a rejected answer on its single retry

- Category L; mode `live_sabotaged_first_answer`.
- Question: What is the overall score of site cand-3a8fa00f876a?
- Tool calls: `explain_score` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: rejected: number_not_grounded (direct_answer[0])
- Answer attempt 2: passed
- Retry: yes; fallback: no.

Final answer:

- *direct answer*, CALCULATED: The overall score is 76.6. [`cand-3a8fa00f876a/score`]

Points:

- PASS outcome: answered
- PASS shown answer validated: validated by the loop and re-validated from the log
- PASS numbers grounded: 1 number(s) checked; ungrounded: none
- PASS neutral language: evaluative words: none
- PASS retry: retried: True
- PASS cites cand-3a8fa00f876a/…: 1 citation(s)

### L2: Two invalid answers end in the deterministic fallback

- Category L; mode `scripted_invalid_twice`.
- Question: What is the overall score of site cand-3a8fa00f876a?
- Tool calls: `get_site` {"candidate_id": "cand-3a8fa00f876a"}
- Answer attempt 1: rejected: number_not_grounded (direct_answer[0])
- Answer attempt 2: rejected: number_not_grounded (direct_answer[0])
- Retry: yes; fallback: yes.

Final answer:

**no AI summary.** Reason: the answer failed validation twice.
The fallback lists the 46 raw evidence record(s) the tools returned.

Points:

- PASS outcome: fallback: the answer failed validation twice
- n/a shown answer validated: no answer shown
- PASS fallback labelled: 46 raw record(s)
- PASS calls get_site: called: ['get_site']
- PASS retry: retried: True

## 7. Limitations

- One run per scenario. Model output is not deterministic, so another run can differ; the deterministic tools, the validator and these checks do not.
- Phrase checks are substring checks on fixed lists. They confirm required wording and catch listed unsupported claims; they cannot prove the absence of every possible unsupported paraphrase.
- Scenario L1 replaces the model's first final answer with an ungrounded one to force the retry; the retry message tells the model its previous answer was rejected, although it did not write that answer. L2 uses a scripted model and makes no API call.
- The validator's word rules are deliberately conservative (D-052): some harmless wording is rejected, which shows up as retries or fallbacks, never as an unvalidated answer.
