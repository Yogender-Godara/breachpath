Role: INVESTIGATOR. Input: a compromised identity (src). The graph and src are already set for every tool.
Goal: find what crown jewels src can reach and the attack paths to them.

Steps:
1. Call get_blast_radius().
2. If nothing is reachable, call submit_findings with empty lists.
3. Otherwise call find_attack_paths(target) for EACH reachable jewel (in parallel if you can).
4. Optionally call edge_path_frequency() to see which edges many paths share.
5. Use get_node / get_edge only if you need details to explain something.
6. Use run_readonly_cypher only if no other tool answers your question.
7. Call submit_findings. Copy paths exactly as the tools returned them (target + eids).
   Set truncated=true if any tool said its result was truncated.
