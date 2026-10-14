"""Background company generator (docs/03_LLD.md section 10.2). Pure Python: builds data, writes JSON."""
import argparse
import json
import os
import random
from collections import Counter

from core.graph.schema import EDGE_TYPES, NODE_LABELS

SEED = 42
TEAMS = ["eng", "data", "finance", "sales", "ops", "security"]
ENVS = ["dev", "staging", "prod"]
ACTIONS = ["db:Read", "db:Write", "logs:Read", "deploy:Run", "ssh:Login", "storage:Get", "storage:Put", "queue:Send"]
COUNTS = {"human": 40, "service": 10, "group_per_team": 2, "role": 15, "policy": 25, "server": 10, "db": 10, "secret": 6}


def build_background(seed=SEED):
    rng = random.Random(seed)
    nodes, edges = [], []
    team_of = {}  # node id -> team (generator memory only, not stored on roles/groups)

    def node(label, nid, owner=None, **props):
        nodes.append({"id": nid, "label": label, "props": {"id": nid, "name": nid.split(":", 1)[1], **props}})
        team_of[nid] = owner

    def edge(etype, src, dst):
        last_used = rng.randint(0, 30) if rng.random() < 0.4 else rng.randint(31, 400)
        edges.append({"eid": f"e:bg:{len(edges) + 1:04d}", "type": etype, "src": src, "dst": dst, "last_used_days": last_used})

    # Nodes
    humans, services, groups, roles, policies, servers, dbs, secrets = ([] for _ in range(8))
    for i in range(COUNTS["human"]):
        team = TEAMS[i % len(TEAMS)]
        nid = f"user:{team}-{i // len(TEAMS) + 1:02d}"
        node("User", nid, team, user_type="human", team=team)
        humans.append(nid)
    for i in range(COUNTS["service"]):
        team, nid = TEAMS[i % len(TEAMS)], f"user:svc-{i + 1:02d}"
        node("User", nid, team, user_type="service", team=team)
        services.append(nid)
    for team in TEAMS:
        for n in range(1, COUNTS["group_per_team"] + 1):
            nid = f"group:{team}-{n}"
            node("Group", nid, team)
            groups.append(nid)
    for i in range(COUNTS["role"]):
        team = TEAMS[i % len(TEAMS)]
        nid = f"role:{team}-{i // len(TEAMS) + 1}"
        node("Role", nid, team, env=rng.choice(ENVS))
        roles.append(nid)
    for i in range(COUNTS["policy"]):
        nid = f"policy:{i + 1:02d}"
        node("Policy", nid, actions=rng.sample(ACTIONS, rng.randint(1, 3)))
        policies.append(nid)
    for i in range(COUNTS["server"]):
        nid = f"server:{i + 1:02d}"
        node("Server", nid, env=rng.choice(ENVS))
        servers.append(nid)
    for i in range(COUNTS["db"]):
        nid = f"db:{i + 1:02d}"
        node("Database", nid, env=rng.choice(ENVS), crown_jewel=False, sensitivity=rng.choice(["low", "medium"]))
        dbs.append(nid)
    for i in range(COUNTS["secret"]):
        nid = f"secret:{i + 1:02d}"
        node("Secret", nid, secret_type=rng.choice(["password", "api-key"]))
        secrets.append(nid)

    # Edges
    def team_roles(team):
        return [r for r in roles if team_of[r] == team]

    for h in humans:
        for g in rng.sample([g for g in groups if team_of[g] == team_of[h]], rng.randint(1, 2)):
            edge("MEMBER_OF", h, g)
    for g in groups:
        for r in rng.sample(team_roles(team_of[g]), rng.randint(1, 2)):
            edge("CAN_ASSUME", g, r)
    for s in services:  # gap fill: the LLD gives service users no outgoing edge
        edge("CAN_ASSUME", s, rng.choice(team_roles(team_of[s])))
    for r in roles:
        for p in rng.sample(policies, rng.randint(1, 3)):
            edge("HAS_POLICY", r, p)
    for p in policies:
        for d in rng.sample(dbs, rng.randint(1, 2)):
            edge("CAN_READ", p, d)
        if rng.random() < 0.2:
            edge("CAN_SSH", p, rng.choice(servers))
    free_secrets = list(secrets)
    rng.shuffle(free_secrets)
    for s in servers:
        if free_secrets and rng.random() < 0.3:
            edge("STORES_SECRET", s, free_secrets.pop())
    for sec in secrets:
        edge("AUTHENTICATES_AS", sec, rng.choice(services))

    graph = {"seed": seed, "nodes": nodes, "edges": edges}
    validate(graph)
    return graph


def validate(graph):
    labels, seen_eids, seen_triples = {}, set(), set()
    for n in graph["nodes"]:
        if n["id"] in labels:
            raise ValueError(f"duplicate node id {n['id']}")
        if n["label"] not in NODE_LABELS:
            raise ValueError(f"unknown node label {n['label']} on {n['id']}")
        labels[n["id"]] = n["label"]
    for e in graph["edges"]:
        if e["eid"] in seen_eids:
            raise ValueError(f"duplicate eid {e['eid']}")
        seen_eids.add(e["eid"])
        if e["type"] not in EDGE_TYPES:
            raise ValueError(f"unknown edge type {e['type']} on {e['eid']}")
        if e["src"] not in labels or e["dst"] not in labels:
            raise ValueError(f"missing src/dst node on {e['eid']}")
        allowed = EDGE_TYPES[e["type"]]
        if labels[e["src"]] not in allowed["from"] or labels[e["dst"]] not in allowed["to"]:
            raise ValueError(f"{e['eid']}: {e['type']} cannot go {labels[e['src']]} -> {labels[e['dst']]}")
        triple = (e["type"], e["src"], e["dst"])
        if triple in seen_triples:
            raise ValueError(f"duplicate edge {triple}")
        seen_triples.add(triple)


def summary(graph):
    nodes = Counter(n["label"] for n in graph["nodes"])
    edges = Counter(e["type"] for e in graph["edges"])
    recent = 100 * sum(e["last_used_days"] <= 30 for e in graph["edges"]) / len(graph["edges"])
    return nodes, edges, recent


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default="data/generated/background.json")
    args = ap.parse_args()
    g = build_background(args.seed)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="\n") as f:
        json.dump(g, f, sort_keys=True, indent=1)
        f.write("\n")
    nodes, edges, recent = summary(g)
    print(f"wrote {args.out}: {len(g['nodes'])} nodes, {len(g['edges'])} edges")
    print("nodes:", dict(sorted(nodes.items())))
    print("edges:", dict(sorted(edges.items())))
    print(f"recent edges (<=30 days): {recent:.1f}%")
