"""MCP server (docs/03_LLD.md section 5): a thin, typed wrapper over core.tools. stdio only, no port.

Run: python -m mcp_server.server   (MCP_PROFILE=full|readonly, default full)
Every wrapper just calls the core function and returns its {"ok", "data"} / {"ok": false, "error"} dict unchanged.
The type hints give the LLM a real JSON schema and the docstring is the description it sees.
stdout is the protocol: this module must never print.
"""
import os
import sys

from mcp.server.mcpserver import MCPServer

from core.tools import read, split, verify, write

PROFILES = ("full", "readonly")


# --- read tools: registered in every profile ---

def get_blast_radius(graph: str, src: str) -> dict:
    """List the crown-jewel databases that `src` can reach within 6 hops (id and name). `graph` is a baseline or sandbox name."""
    return read.get_blast_radius(graph, src)


def find_attack_paths(graph: str, src: str, target: str | None = None, k: int = 20) -> dict:
    """Attack paths from `src` (to `target` if given), shortest first, up to 6 hops. k must be 1-50. Returns `truncated` if more exist. Every edge is cited by eid."""
    return read.find_attack_paths(graph, src, target, k)


def shortest_paths(graph: str, src: str, target: str, k: int = 3) -> dict:
    """The k shortest paths from `src` to `target`, as edge eids with hop counts. k must be 1-10."""
    return read.shortest_paths(graph, src, target, k)


def edge_path_frequency(graph: str, src: str, top: int = 10) -> dict:
    """Edges that appear on the most attack paths from `src` (looks at up to 500 paths). top must be 1-20."""
    return read.edge_path_frequency(graph, src, top)


def choke_points(graph: str, src: str, top: int = 10) -> dict:
    """Choke point hints: graph-wide betweenness plus the edges on the most attack paths from `src`. top must be 1-20."""
    return read.choke_points(graph, src, top)


def compute_min_cut(graph: str, src: str) -> dict:
    """Min-cut candidates that block every attack path from `src` while keeping business-critical edges (max-flow = min-cut). Returns candidates with witness paths, max_flow and `safe`. safe=false means every cut would break a business need."""
    return read.compute_min_cut(graph, src)


def check_business_access(graph: str) -> dict:
    """For every valid business need, whether its principal can still reach its resource in `graph`."""
    return read.check_business_access(graph)


def get_node(graph: str, id: str) -> dict:
    """Properties of one node plus its outgoing and incoming edges (each cited by eid)."""
    return read.get_node(graph, id)


def get_edge(graph: str, eid: str) -> dict:
    """Properties of one edge by its eid: kind, endpoints and properties (e.g. last_used_days, protected)."""
    return read.get_edge(graph, eid)


def verify_fix(sandbox: str, src: str) -> dict:
    """Prove a fix on a sandbox graph: max flow from `src` is 0, a full BFS reaches no crown jewel, and every business need still works. Also lists collateral."""
    return verify.verify_fix(sandbox, src)


# --- full profile only ---
# The write tools (and simulate_cut, split_suggestion) take incident_id and lock_token as normal parameters:
# one MCP subprocess serves many incidents, so the caller passes them, never the environment.
# In P6.3 the agent loop hides these two from the LLM's schema and injects them.

def run_readonly_cypher(graph: str, query: str, params: dict | None = None) -> dict:
    """Run one read-only Cypher query on `graph` (GRAPH.RO_QUERY: writes are rejected). Max 200 rows, 2 s timeout. Always pass values in `params`."""
    return read.run_readonly_cypher(graph, query, params)


def simulate_cut(sandbox: str, src: str, eids: list[str], incident_id: str, lock_token: str) -> dict:
    """Try removing edges `eids` on a throw-away copy of the sandbox and report: blocks_attack, needs_ok, collateral, size. The sandbox is unchanged. Max 6 simulations per incident. Sandbox graphs only."""
    return write.simulate_cut(sandbox, src, eids, incident_id=incident_id, lock_token=lock_token)


def create_sandbox(tenant: str, incident_id: str, lock_token: str) -> dict:
    """Copy the baseline of `tenant` into this incident's sandbox graph. Max 3 sandboxes per incident. Sandbox graphs only."""
    return write.create_sandbox(tenant, incident_id=incident_id, lock_token=lock_token)


def protect_business_paths(sandbox: str, incident_id: str, lock_token: str, second_path_needs: list[str] | None = None) -> dict:
    """Mark the edges of every valid business need's shortest path as protected so a cut can't break them. `second_path_needs` also protects those needs' second path. Sandbox graphs only."""
    return write.protect_business_paths(sandbox, second_path_needs, incident_id=incident_id, lock_token=lock_token)


def delete_sandbox(sandbox: str, incident_id: str, lock_token: str) -> dict:
    """Delete a sandbox graph. Idempotent. Sandbox graphs only: a baseline is always refused."""
    return write.delete_sandbox(sandbox, incident_id=incident_id, lock_token=lock_token)


def apply_fix(sandbox: str, eids: list[str], reason: str, incident_id: str, lock_token: str) -> dict:
    """Remove edges `eids` (cited by eid) from a sandbox. Protected edges are refused. `reason` is required. Sandbox graphs only."""
    return write.apply_fix(sandbox, eids, reason, incident_id=incident_id, lock_token=lock_token)


def split_suggestion(sandbox: str, src: str, incident_id: str, lock_token: str) -> dict:
    """Only when compute_min_cut says safe=false: find and prove a split of one node that makes a safe cut possible. Creates a -split sandbox. Sandbox graphs only."""
    return split.split_suggestion(sandbox, src, incident_id=incident_id, lock_token=lock_token)


READ_TOOLS = [get_blast_radius, find_attack_paths, shortest_paths, edge_path_frequency, choke_points, compute_min_cut,
              check_business_access, get_node, get_edge, verify_fix]
FULL_ONLY_TOOLS = [run_readonly_cypher, simulate_cut, create_sandbox, protect_business_paths, delete_sandbox, apply_fix,
                   split_suggestion]


def build_server(profile="full"):
    """readonly = the read tools only, WITHOUT run_readonly_cypher. full = everything."""
    if profile not in PROFILES:
        raise ValueError(f"MCP_PROFILE must be one of {', '.join(PROFILES)}, got {profile!r}")
    mcp = MCPServer("breachpath")
    for fn in READ_TOOLS + (FULL_ONLY_TOOLS if profile == "full" else []):
        mcp.tool()(fn)
    return mcp


if __name__ == "__main__":
    try:
        server = build_server(os.getenv("MCP_PROFILE", "full"))
    except ValueError as e:
        sys.exit(f"mcp_server: {e}")  # to stderr, never stdout
    server.run(transport="stdio")
