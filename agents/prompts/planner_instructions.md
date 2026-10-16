OPERATOR INSTRUCTIONS (only when the task contains them): a person in charge gave a free-text instruction.
- Map it to the edges it rules out. Use get_edge / tools to find them. A cut must not contain such an edge.
- The first compute_min_cut() already shows the candidates and the eids of the witness paths: use them, and call get_edge only when an eid is still unclear.
- Call compute_min_cut ONCE with avoid_eids = all the edges you mapped (only the candidates it returns respect the instruction), then simulate_cut on each candidate.
- If that compute_min_cut answers safe=false, the tool proved that no safe cut can respect the instruction: do not explore any further. Call submit_fix_plan at once
  with cut_eids = [] and the same avoid list (with quotes); say in "notes" that the instruction rules out every safe cut.
- Choose by collateral, then size, then staleness, as usual, among cuts that block the attack and keep every need.
- In submit_fix_plan, "avoid" lists every edge you avoided as {eid, quote}: quote = the EXACT words of the instruction that rule that edge out
  (copy them, do not paraphrase). In "notes" say in one sentence how the instruction changed the fix.
- If the instruction rules out no edge, submit avoid = [] and say so in notes. Never ignore an instruction silently.
