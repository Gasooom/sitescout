"""Milestone 10, Phase 2 (D-057): the deterministic site investigation tools.

``network_summary`` exposes the network-level decision results in ``network.json`` and
``nearby_sites`` the spatial relationships between candidates. Both are read-only, use no
model, embedding, knowledge index, network access or environment variable, and return
``EvidenceRecord``s that a later answer can cite. They are the structured-data counterpart of
``sitescout.knowledge.search_knowledge``; neither imports the other.

They are deliberately NOT in the M9 analyst's six-tool registry, which stays as it was. A later
phase composes M9's tools, these two and ``search_knowledge`` into one registry;
``INVESTIGATION_TOOLS`` describes them in the same shape as the M9 ``ToolSpec``.
"""

from sitescout.investigation.data import InvestigationData
from sitescout.investigation.errors import ErrorCode, InvestigationError
from sitescout.investigation.nearby import NearbyArgs, NearbySites, nearby_sites
from sitescout.investigation.network import NetworkSummary, network_summary
from sitescout.investigation.registry import INVESTIGATION_TOOLS, InvestigationTool

__all__ = [
    "INVESTIGATION_TOOLS",
    "ErrorCode",
    "InvestigationData",
    "InvestigationError",
    "InvestigationTool",
    "NearbyArgs",
    "NearbySites",
    "NetworkSummary",
    "nearby_sites",
    "network_summary",
]
