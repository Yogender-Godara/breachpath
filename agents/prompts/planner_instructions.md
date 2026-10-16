OPERATOR INSTRUCTIONS (only when the task contains them): a person in charge gave a free-text instruction.
- Map it to the edges it rules out. Use get_edge / tools to find them. A cut must not contain such an edge.
- Call compute_min_cut with avoid_eids = those edges (only the candidates it returns respect the instruction), then simulate_cut on each candidate.
- Choose by collateral, then size, then staleness, as usual, among cuts that block the attack and keep every need.
- In submit_fix_plan, "avoid" lists every edge you avoided as {eid, quote}: quote = the EXACT words of the instruction that rule that edge out
  (copy them, do not paraphrase). In "notes" say in one sentence how the instruction changed the fix.
- If the instruction rules out no edge, submit avoid = [] and say so in notes. Never ignore an instruction silently.
