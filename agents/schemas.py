"""The MINIMAL outputs the LLM agents submit (docs/06 sections 2-3, docs/03_LLD.md section 6.3).

Each model has a twin JSON schema in SUBMIT_TOOLS (strict-compatible: additionalProperties false, every property
required, no $ref); our code validates with the Pydantic model whatever the provider does. tests/test_agent_schemas.py
checks that the two have the same field names.

NOT here: the enrichment (node_ids, hops, path_count_by_target, top_edges, witness, simulation, suggestion, source...).
It needs tool results, so each agent's post-check adds it (P6.4 Investigator, P6.5 Planner).
The rule for those brains: return EXACTLY the same dict keys as core/fast_brains.py, so the pipeline, the report
and the API never change.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathOut(Strict):
    target: str
    eids: list[str]


class FindingsOut(Strict):
    reachable_jewels: list[str]
    paths: list[PathOut]
    truncated: bool
    notes: str


class ReasonOut(Strict):
    eid: str
    why: str


class FixPlanOut(Strict):
    cut_eids: list[str]
    chosen: Literal["source_side", "sink_side", "custom"]
    reasons: list[ReasonOut]
    notes: str


def _tool(name, description, parameters):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


_STRINGS = {"type": "array", "items": {"type": "string"}}

SUBMIT_TOOLS = {
    "submit_findings": _tool("submit_findings", "Submit your findings. Call exactly once, when you are done.", {
        "type": "object", "additionalProperties": False,
        "required": ["reachable_jewels", "paths", "truncated", "notes"],
        "properties": {
            "reachable_jewels": _STRINGS,
            "paths": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["target", "eids"],
                "properties": {"target": {"type": "string"}, "eids": _STRINGS}}},
            "truncated": {"type": "boolean"},
            "notes": {"type": "string"}}}),
    "submit_fix_plan": _tool("submit_fix_plan", "Submit the chosen fix plan. Call exactly once, when you are done.", {
        "type": "object", "additionalProperties": False,
        "required": ["cut_eids", "chosen", "reasons", "notes"],
        "properties": {
            "cut_eids": _STRINGS,
            "chosen": {"type": "string", "enum": ["source_side", "sink_side", "custom"]},
            "reasons": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["eid", "why"],
                "properties": {"eid": {"type": "string"}, "why": {"type": "string"}}}},
            "notes": {"type": "string"}}}),
}
