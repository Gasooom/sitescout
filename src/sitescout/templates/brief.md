# Site Evidence Brief: {host}

Candidate `{candidate_id}` · OpenStreetMap {host_osm_id} · {confidence} confidence

## Site

| | |
|---|---|
| Host | {host} |
| Coordinates | {coordinates} |
| District, province | {district}, {province} |
| Profile | {profile} |

## Why this site was selected

{opportunity}

## Demand

Modelled population ({worldpop}): **{pop_1km}** within {radius_1000}, **{pop_5km}** within {radius_5000} and **{pop_10km}** within {radius_10000}. Mapped points of interest nearby: {poi_1km} within {radius_1000}, {poi_3km} within {radius_3000}.{border_note}

## Access

The nearest drivable road ({road_class}) is {dist_road} away; the trunk corridor is {dist_trunk} away.

## Charging gap

The nearest known charging site is {dist_charger} away, with {chargers_10km} known sites within {radius_10000} and {chargers_25km} within {radius_25000}. Only {known_sites} charging sites are known (OpenStreetMap; manual charger list: {manual_chargers}), so this measures distance from them, not the whole market.

## Grid evidence

Nearest mapped substation: {dist_substation}. Nearest mapped power line: {dist_line}. Grid evidence: **{grid_status}**. District grid-mapping completeness: {grid_completeness} times the national median, a proxy for how much of the grid is mapped. These are distances to mapped infrastructure, not a statement about capacity or a connection.

Actual grid connection feasibility requires utility confirmation.

## Score breakdown

Overall score **{score}** (rank {rank} of {candidates}), {profile} profile.

| Component | Score | Weight ({profile}) |
|---|---|---|
{component_rows}

Bonuses already included: host type {host_bonus} points, road class {road_bonus} points.

## Confidence

**{confidence}.** {confidence_reasons}.

## Risks

{risks}

## Unknowns

{unknowns}

## Recommended next actions

{actions}

## Important notice

This analysis uses public data. It does not establish grid approval, land availability, permitting approval, or commercial viability.

Data: OpenStreetMap ({osm_licence}) up to {osm_date}; {worldpop} ({worldpop_licence}); geoBoundaries ({boundaries_licence}). Generated from structured project data by `scripts/briefs.py`; every number above is checked against `data/processed/evidence.json`.
