# Site Evidence Brief: Fuel station, Nyagatare

Candidate `cand-f0dffb6f5f10` · OpenStreetMap node/11651402133 · Low confidence

## Site

| | |
|---|---|
| Host | Fuel station, Nyagatare |
| Coordinates | -1.12142, 30.41941 |
| District, province | Nyagatare, Eastern Province |
| Profile | corridor |

## Why this site was selected

The exact network optimization selected it as one of 30 sites. Without it the network would lose **0.68%** of its weighted demand coverage. It ranks 132 of 300 candidates by score (55.3, corridor profile); its strongest components are host / commercial (79.2) and charging gap (73.6). Together the 30 network sites cover 49.8% of Rwanda's modelled population within 10 km, against 24.0% for the Top-30 by score.

## Demand

Modelled population (WorldPop 2025, R2025A): **4,704** within 1 km, **41,471** within 5 km and **89,020** within 10 km. Mapped points of interest nearby: 7 within 1 km, 13 within 3 km. 29% of the 10 km circle lies outside Rwanda, where population is not counted.

## Access

The nearest drivable road (trunk) is 20 m away; the trunk corridor is 20 m away.

## Charging gap

The nearest known charging site is 85.7 km away, with 0 known sites within 10 km and 0 within 25 km. Only 5 charging sites are known (OpenStreetMap; manual charger list: missing), so this measures distance from them, not the whole market.

## Grid evidence

Nearest mapped substation: 47.6 km. Nearest mapped power line: 5.5 km. Grid evidence: **missing**. District grid-mapping completeness: 0.39 times the national median, a proxy for how much of the grid is mapped. These are distances to mapped infrastructure, not a statement about capacity or a connection.

Actual grid connection feasibility requires utility confirmation.

## Score breakdown

Overall score **55.3** (rank 132 of 300), corridor profile.

| Component | Score | Weight (corridor) |
|---|---|---|
| Demand | 29.5 | 0.15 |
| Access | 68.5 | 0.30 |
| Host / commercial | 79.2 | 0.15 |
| Charging gap | 73.6 | 0.25 |
| Grid evidence | 0.0 | 0.15 |

Bonuses already included: host type 10 points, road class 10 points.

## Confidence

**Low.** Grid evidence missing: no mapped substation or power line within 5 km; Sparse public-map coverage in the district (grid completeness ratio below 0.5).

## Risks

- No mapped substation or power line within 5 km: grid evidence is missing, and its component scores 0.
- Grid mapping in Nyagatare is sparse (0.39 times the national median), so missing lines may simply be unmapped.
- 29% of the 10 km circle lies outside Rwanda; demand there is not counted.
- The charging gap is measured against only 5 known charging sites.
- Weakest component: grid evidence (0.0).

## Unknowns

- Grid connection capacity
- Transformer capacity
- Land availability
- Landowner willingness
- Permit requirements
- Whether any grid infrastructure lies nearby (none is mapped within 5 km)

## Recommended next actions

- Ask the utility about grid connection capacity, transformer capacity and cost here.
- Contact the owner of the fuel station about land availability and willingness to host chargers.
- Check permit requirements with the Nyagatare district authorities.
- Request the utility's network map for this area: no line or substation is mapped within 5 km.
- Verify grid infrastructure on the ground; public mapping here is sparse.
- Estimate cross-border demand, which the population figures leave out.

## Important notice

This analysis uses public data. It does not establish grid approval, land availability, permitting approval, or commercial viability.

Data: OpenStreetMap (© OpenStreetMap contributors, ODbL-1.0) up to 2026-09-23; WorldPop 2025, R2025A (CC-BY-4.0); geoBoundaries (CC-BY-4.0). Generated from structured project data by `scripts/briefs.py`; every number above is checked against `data/processed/evidence.json`.
