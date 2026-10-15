"""Business needs (docs/03_LLD.md section 2.2): explicit ones from the scenario YAML + usage ones from recent edges."""
import logging

from core.config import NEED_USAGE_DAYS
from data.scenarios import reaches

log = logging.getLogger(__name__)
RESOURCE_LABELS = ("Server", "Database")


def build_needs(graph, usage_days=NEED_USAGE_DAYS):
    label = {n["id"]: n["label"] for n in graph["nodes"]}
    needs = []
    for n in graph.get("needs", []):
        valid = n["resource_id"] in reaches(graph, n["principal_id"])
        if not valid:
            log.warning("need %s: no path from %s to %s", n["id"], n["principal_id"], n["resource_id"])
        needs.append({"id": n["id"], "principal_id": n["principal_id"], "resource_id": n["resource_id"],
                      "reason": n["reason"], "source": "explicit", "valid": valid})

    taken = {(n["principal_id"], n["resource_id"]) for n in needs}
    recent = {"edges": [e for e in graph["edges"] if e["last_used_days"] <= usage_days]}
    usage = []
    for principal in sorted(i for i, lab in label.items() if lab == "User"):
        for resource in sorted(reaches(recent, principal)):
            if label.get(resource) in RESOURCE_LABELS and (principal, resource) not in taken:
                usage.append({"id": f"need:usage:{principal}:{resource}", "principal_id": principal,
                              "resource_id": resource, "reason": f"used in the last {usage_days} days",
                              "source": "usage", "valid": True})
    return needs + sorted(usage, key=lambda n: n["id"])
