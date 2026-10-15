"""Graph model constants (docs/03_LLD.md sections 2.1-2.5). Edge types and labels come only from here."""
import re

SHARED_LABEL = "Asset"
NEED_LABEL = "BusinessNeed"
NODE_LABELS = ["User", "Group", "Role", "Policy", "Server", "Database", "Secret"]

# Every edge is stored as :ATTACK with its real type in r.kind, because algo.maxFlow accepts exactly one
# relationship type (P3.1). The graph JSON keeps the field "type"; the loader writes it as kind.
EDGE_TYPE = "ATTACK"

# kind -> which labels it goes from / to, and the real-world fix when the edge is cut
EDGE_TYPES = {
    "MEMBER_OF": {"from": ["User"], "to": ["Group"], "remediation": "Remove user from group"},
    "CAN_ASSUME": {"from": ["User", "Group"], "to": ["Role"], "remediation": "Edit the role's trust policy"},
    "HAS_POLICY": {"from": ["User", "Group", "Role"], "to": ["Policy"], "remediation": "Detach the policy"},
    "CAN_READ": {"from": ["Policy"], "to": ["Database"], "remediation": "Remove the statement from the policy"},
    "CAN_SSH": {"from": ["User", "Policy"], "to": ["Server"], "remediation": "Revoke the SSH key / access grant"},
    "CAN_PASS_ROLE": {"from": ["Policy"], "to": ["Role"], "remediation": "Remove PassRole for that role from the policy"},
    "STORES_SECRET": {"from": ["Server"], "to": ["Secret"], "remediation": "Delete the file and rotate the secret"},
    "AUTHENTICATES_AS": {"from": ["Secret"], "to": ["User", "Role", "Database"], "remediation": "Rotate the credential"},
}
ATTACK_PATTERN = EDGE_TYPE  # used as -[:{ATTACK_PATTERN}*1..N]->

INDEX_QUERIES = [
    f"CREATE INDEX FOR (n:{SHARED_LABEL}) ON (n.id)",
    "CREATE INDEX FOR (n:Database) ON (n.crown_jewel)",
    f"CREATE INDEX FOR (n:{NEED_LABEL}) ON (n.id)",
    f"CREATE INDEX FOR ()-[r:{EDGE_TYPE}]-() ON (r.eid)",
]

_ID = re.compile(r"[a-z0-9-]+")
_SANDBOX = re.compile(r"t:[a-z0-9-]+:sbx:[a-z0-9-]+")


def _check(value):
    if not _ID.fullmatch(value):
        raise ValueError(f"invalid id {value!r}: use only a-z, 0-9 and '-'")
    return value


def baseline_name(scenario):
    return f"t:{_check(scenario)}:baseline"


def sandbox_name(scenario, incident_id, suffix=""):
    # suffix (e.g. "-split", "-sim-1") is validated with the id, so every sandbox name passes is_sandbox()
    _check(incident_id)
    return f"t:{_check(scenario)}:sbx:{_check(incident_id + suffix)}"


def is_sandbox(name):
    return _SANDBOX.fullmatch(name) is not None
