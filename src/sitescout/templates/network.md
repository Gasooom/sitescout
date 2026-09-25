Production mode. {eligible} candidates are eligible: production score in the top half (percentile at least {min_percentile}){host_rule}. Demand: modelled population (WorldPop) summed into {nodes} H3 cells at resolution {resolution}, normalised to 1, with demand within {radius_km} km of the {known_sites} known charging sites weighted by {factor}. A site covers demand within {radius_km} km; selected sites are at least {spacing_km} km apart (except in Top-{n}, which takes scores only); λ = {lam}.

{table}

- Exact MCLP solver status: **{status}** ({solution}).
- Exact-versus-greedy gap: **{gap}**, measured on the optimization objective Σ wᵢ·yᵢ + λ·Σ sⱼ·xⱼ ({exact_objective} against {greedy_objective}), which includes the λ score term. It is not the difference between the population-coverage percentages above.
- Overlap: MCLP and greedy share {mclp_greedy} sites, MCLP and Top-{n} {mclp_top30}, greedy and Top-{n} {greedy_top30}.

Selecting sites together covers more of the country than taking the best-scoring sites one by one, which cluster where scores are highest. Coverage is modelled population within a radius: it says nothing about grid capacity, land or charging demand.

Sensitivity (one parameter changed at a time, the others at their defaults):

| Change | Population covered | Provinces | Sites shared with the base network |
|---|---|---|---|
{sensitivity}

{factor_note}