"""Calls every read tool on S2 and prints one line each. Run in the toolbox: python -m spikes.read_tools_demo"""
from core.graph import client, loader, queries
from core.graph.schema import baseline_name, sandbox_name
from core.tools import read

SRC = "user:ravi"


def show(name, result):
    data = result["data"] if result["ok"] else result["error"]
    print(f"{name:24} ok={result['ok']}  {str(data)[:110]}")


loader.load_scenario("s2")
base = baseline_name("s2")
sbx = sandbox_name("s2", "demo")
client.delete_graph(sbx)
client.get_graph(base).copy(sbx)  # protected copy for the min-cut and the business check
try:
    client.query(sbx, queries.PROTECT_NEEDS, {"protect_cap": client.ro_query(sbx, queries.PROTECT_CAP)[0][0]})
    show("get_blast_radius", read.get_blast_radius(base, SRC))
    show("find_attack_paths", read.find_attack_paths(base, SRC, k=1))
    show("shortest_paths", read.shortest_paths(base, SRC, "db:s2-payments"))
    show("edge_path_frequency", read.edge_path_frequency(base, SRC, top=3))
    show("choke_points", read.choke_points(base, SRC, top=2))
    show("get_node", read.get_node(base, "role:s2-deploy-role"))
    show("get_edge", read.get_edge(base, "e:s2:06"))
    show("run_readonly_cypher", read.run_readonly_cypher(base, "MATCH (n:Database) RETURN n.id AS id LIMIT 3"))
    show("check_business_access", read.check_business_access(sbx))
    show("compute_min_cut", read.compute_min_cut(sbx, SRC))
    show("error: unknown graph", read.get_blast_radius("t:s9:baseline", SRC))
finally:
    client.delete_graph(sbx)
