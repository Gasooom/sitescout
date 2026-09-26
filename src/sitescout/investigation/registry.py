"""Milestone 10, Phase 2 (D-057): how a later registry can list the two investigation tools.

``InvestigationTool`` has the same fields as the M9 ``ToolSpec`` (name, description, argument
model, call). It is defined here rather than imported because ``ToolSpec`` lives in the M9
loop module, which pulls in the provider interface; these tools depend on no provider code.
``call`` takes an ``InvestigationData`` and the validated arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from sitescout.investigation.data import InvestigationData
from sitescout.investigation.nearby import NearbyArgs, nearby_sites
from sitescout.investigation.network import network_summary


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class InvestigationTool:
    name: str
    description: str
    args_model: type[BaseModel]
    call: Callable[[InvestigationData, BaseModel], BaseModel]


INVESTIGATION_TOOLS: tuple[InvestigationTool, ...] = (
    InvestigationTool(
        "network_summary",
        "The network-level decision results from the optimization: for the optimized, greedy "
        "and Top-30 selections, their modelled population coverage, covered demand, sites, "
        "provinces, districts and mean score; the exact-versus-greedy gap on the objective; "
        "overlaps; solver status; eligible sites; parameters; and sensitivity rows.",
        NoArguments,
        lambda data, args: network_summary(data),
    ),
    InvestigationTool(
        "nearby_sites",
        "The candidates within radius_m metres (1 up to the service radius) of one candidate, "
        "nearest first, with their stored facts and distances. Lists at most a fixed number "
        "and always reports the full count. It does not rank or compare sites.",
        NearbyArgs,
        lambda data, args: nearby_sites(data.analyst, args.candidate_id, args.radius_m),  # type: ignore[attr-defined]
    ),
)
