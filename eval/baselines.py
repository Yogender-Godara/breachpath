"""Three realistic baselines (docs/03_LLD.md section 11), networkx only. They IGNORE business needs on purpose: that is what a real
"disable the account" or "cut the busiest edge" response does. Each returns a sorted list of eids; the metrics score them like any method.
Ties are broken by eid, so every run gives the same answer.
"""
import networkx as nx

from eval import metrics


def b_disable(G, src):
    """The usual first response: remove every outgoing edge of the compromised user."""
    return sorted(k for _, _, k in G.out_edges(src, keys=True))


def attack_edges(G, src):
    """Edges on some src -> jewel path: the tail is reachable from src and a jewel is reachable from the head."""
    jewels = metrics.jewels_of(G)
    from_src = nx.descendants(G, src) | {src}
    to_jewel = set(jewels)
    for j in jewels:
        to_jewel |= nx.ancestors(G, j)
    return {k for u, v, k in G.edges(keys=True) if u in from_src and v in to_jewel}


def _greedy(graph_G, src, pick):
    """Remove the edge `pick` chooses, one at a time, until no crown jewel is reachable."""
    G, cut = graph_G.copy(), []
    while metrics.reached_jewels(G, src):
        eid = pick(G, src)
        u, v, _ = next(e for e in G.edges(keys=True) if e[2] == eid)
        G.remove_edge(u, v, eid)
        cut.append(eid)
    return sorted(cut)


def _betweenness_pick(G, src):
    """The attack-path edge with the highest edge betweenness of the whole graph."""
    by_eid = {k: c for (_, _, k), c in nx.edge_betweenness_centrality(G).items()}
    return min(attack_edges(G, src), key=lambda k: (-by_eid[k], k))


def b_betweenness(G, src):
    """A standard centrality heuristic: repeatedly remove the attack-path edge with the highest betweenness."""
    return _greedy(G, src, _betweenness_pick)


def _frequency_pick(G, src):
    """The edge used by the most current simple src -> jewel paths."""
    counts = {}
    for j in metrics.reached_jewels(G, src):
        for path in nx.all_simple_edge_paths(G, src, j):
            for _, _, k in path:
                counts[k] = counts.get(k, 0) + 1
    return min(counts, key=lambda k: (-counts[k], k))


def b_frequency(G, src):
    """The 'obvious' greedy choke point: repeatedly remove the edge used by the most current attack paths."""
    return _greedy(G, src, _frequency_pick)


BASELINES = {"disable": b_disable, "betweenness": b_betweenness, "frequency": b_frequency}
