# 05: Problems, Blockers, Risks and Open Questions

> **Status:** pre-hackathon planning notes (Rule 8).
> **Blocker** = must be resolved before we can build that part. **Risk** = might go wrong; we have a plan. **Limitation** = a known weakness we accept and document.

---

## 1. Blockers: verify in the first 2 hours after kickoff

| ID | Blocker | Why it blocks | How to test (toy graph) | Fallback if it fails |
|---|---|---|---|---|
| B1 | **`algo.maxFlow` exists** in the FalkorDB Docker image we pull | The min-cut and the proof depend on it | Create a 4-node graph with `cap` on edges, run Q6, expect a known max flow | Pin a newer image tag. Otherwise: path-frequency greedy + verify loop (lose the "minimal" guarantee, and say so in the README). |
| B2 | **maxFlow output details**: does `edges` carry properties (`e.eid`)? Is a flow of 0 a row with `maxFlow = 0` or no row? Do multiple `targetNodes` work? | `mincut.py` relies on all three | Toy graph with 2 sinks, plus a graph with no path | Map edges by `id(e)` instead of `eid`. Treat no row as 0. Add a super-sink node if multi-target fails. |
| B3 | **`algo.SPpaths` parameters**: `relDirection` value, `maxLen`, `pathCount`, behavior when there's no path | Q3, Q8, Q11 | Toy graph, with and without a path | Plain Cypher bounded `MATCH p=… LIMIT 1` |
| B4 | **`algo.bfs` signature** `(node, depth, relType or null)` | Q9 second proof | Toy graph | Drop proof B and keep proof A (maxFlow) |
| B5 | **`GRAPH.COPY`** in our version | Sandbox per incident | `GRAPH.COPY a b` | Re-run the loader into the sandbox name (slower but fine at 150 nodes) |
| B6 | **Relationship property index** syntax | Fast `eid` lookups | `CREATE INDEX FOR ()-[r:MEMBER_OF]-() ON (r.eid)` | Skip it. A full edge scan is fine at our size. |
| B7 | **`CALL algo.*` after `MATCH … WITH`** with collected node lists | Q6 builds the target list | Run Q6 as written | Pass the target nodes in a separate `MATCH` inside the same query, or call per target |
| B8 | **LLM providers work for tool calling**: Groq, OpenRouter, Mistral keys, current free-tier limits, OpenAI-compatible URLs | Agent mode and eval | 3-call smoke test per provider: single tool call, parallel calls, submit schema (06 §6) | Use whichever providers pass. If none pass reliably: deterministic Planner, with LLM only for the Investigator narrative + Reporter. Fast mode always works. |
| B9 | **MCP Python SDK stdio**: the worker can start the MCP server as a subprocess and list/call tools | Agents' tool calls | Start the subprocess from the worker container, list tools, call one | Agents call `core.tools` in-process (lose the "via MCP" claim for agents; keep the stdio server for the external-client demo) |
| B10 | ~~Team size and skills~~ **Resolved: solo, Python.** | — | — | Solo plan (§5) |

---

## 2. Risk register

Likelihood and impact: **H**igh, **M**edium, **L**ow.

| ID | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | **Path explosion**: variable-length queries return too many paths, or are slow | M | H | Hop bound (6) + `LIMIT k` for display. The **decision and proof use maxFlow/bfs**, which need no path enumeration. Per-query timeout. |
| R2 | **Business-protection heuristic** (protect one shortest path per need) gives a non-minimal fix, or a false "no safe fix" | M | M | Brute-force checker in the eval finds the true optimum on small scenarios. Document the gap. A retry protects a broken need's 2nd path (a changed input). |
| R21 | **Usage needs over-protect**: with company-wide needs, so many edges are protected that "no safe fix" becomes common | M | M | Tune `NEED_USAGE_DAYS` (30). About 40% of background edges are recent by design. The generator `expect` block catches surprises. The report shows how many needs were protected. |
| R22 | **Simulation latency**: up to 6 `simulate_cut` calls, each a `GRAPH.COPY` + checks | L | L | Tiny graphs (about 150 nodes). Run in parallel. Cap of 6. Fast mode simulates only the 2 candidates. |
| R24 | **Streamlit page reruns** redraw the graph on every event (flicker, lost zoom) | M | M | Timeline in `st.fragment(run_every=1s)`. Graph redrawn only on phase change, cached in `session_state` (LLD §15). Fallback: a manual "Refresh" button. |
| R25 | **MCP stdio subprocess dies** mid-incident | L | M | Worker restarts it and retries the tool call once, then degrades to fast-mode results for that step |
| R23 | **The critique's "block protected edges in the residual BFS" idea** gets added by mistake | L | H | It would **break** the min-cut (sum of cut caps ≠ max flow). Code comment + the sanity assert in `mincut.py` + a unit test. |
| R3 | **Betweenness misleads** (global and sampled) | H | L | It's only a hint. Set `samplingSize` ≥ node count. Scenario S6 *shows* why min-cut beats it, turning the weakness into a demo point. |
| R4 | **LLM invents edge ids or paths** | M | H | Strict tool schemas. Executor validates every `eid`. Verifier is code. A fake plan can never pass verification. |
| R5 | **LLM latency** makes the demo slow | M | M | Fast mode first (about 1 s), agent narrative after. Groq first in the chain. Turn caps. Response cache for repeat runs. |
| R6 | **LLM refusal**: the security topic trips a model's safety filter | L–M | M | Defensive framing in the system prompt. Next provider in the chain. Degrade to fast mode + template report. |
| R7 | **Synthetic data looks like a toy** | M | M | Model the edges on real cloud privilege-escalation patterns (PassRole + create compute, saved credentials on servers). Realistic names, about 150 nodes plus noise. |
| R8 | **Judges question Rule 3** ("is the graph really needed?") | L | H | README section "Why FalkorDB is not decoration" + the **with-graph vs JSON-only eval table**. |
| R9 | **Rules 8 and 13**: looks pre-built or fully AI-generated | L | H | Code starts after kickoff. Small commits through the 4 days. AI-use disclosure. Every member can explain the graph model, queries and min-cut. |
| R10 | **Scope creep**: too many features, nothing finished | H | H | MoSCoW in the requirements. Daily gates (§5). Cut list (§6). |
| R11 | **Live demo breaks** during judging | M | H | Seeded data, scenario reset endpoint, health checks, **recorded video** as the main proof |
| R12 | **Sandbox memory grows** with many incidents | L | L | 24 h TTL janitor. One sandbox per incident. Overlay design for scale. |
| R13 | **Two workers on one incident** | L | M | `SET NX` lock per incident + idempotent tools |
| R14 | **Deadline / time-zone slip** | M | H | Internal deadline **Oct 18, 6:00 PM IST** (6 h buffer). Submit early, then update. |
| R15 | **Free-tier rate limits** (tokens or requests per minute/day) run out during eval or the demo | H | M | Provider chain. **Response cache** (record/replay). Small scenario set. Fast-mode eval for most runs, agent eval once per day. Throttle eval calls. |
| R18 | **Weaker tool calling** in open models (wrong args, skipped submit, made-up eids) | M | M | Pydantic validation + one retry. Forced submit on the last turn where supported. Executor and Verifier are code. Deterministic fallback. The eval shows real numbers per model. |
| R19 | **Provider API differences** (tool-call format, `tool_choice`, JSON mode, parallel calls), e.g. Mistral vs OpenAI format | M | M | Smoke test per provider at kickoff (B8). Per-provider capability flags in the gateway. Reach Mistral models via OpenRouter if the direct API has quirks. LiteLLM as a backup. |
| R20 | **Eval fairness** questioned when the model differs per run | L | L | The baseline uses the **same model** as the Planner. Record provider + model in every eval row. |
| R16 | **Deployment host problems** (ports, memory, FalkorDB Cloud limits) | M | M | Same Docker Compose locally and on one VM. Test the deploy on **Day 3**, not Day 4. |
| R17 | **UI graph component** doesn't render well | M | L | Fallback: a static image of the sub-graph (graphviz) + a table of paths |
| R26 | **LLM report wording: guarded facts, unguarded tone.** The Reporter's text is checked in code for facts (7 headings, every eid/node id exists, "verified" only when proven, NEEDS_HUMAN says no safe fix, no pronouns, no collateral claim without data) but not for tone. Seen live (gpt-oss-120b/20b): host actions worded as done ("deleted/rotated"), internal field names leaking (`already_broken`, `needs_ok = true`), "confirmed as safe and complete", stray closing lines ("Report generated by BreachPath", "Submit"), loose "Verify the fix" steps | M | L | Accepted (P7.1). Facts, paths, counts and the proof are always right (guard); any unguarded wording problem is cosmetic. The template report is always available (`degraded`). A wrong fact falls back to the template, or the Business impact section is repaired in code (`llm+repaired`) |

---

## 3. Known design limitations (accepted and documented)

| # | Limitation | Why we accept it | Where it shows |
|---|---|---|---|
| L1 | **Business-safe minimum cut is approximate.** Cutting the attacker off while *keeping* many other pairs connected is a harder problem than plain min-cut (multicut-style, with no simple exact algorithm like max-flow). We protect one shortest path per need, company-wide. | Simple, fast, always **safe** (every protected need keeps a path). Optimality and collateral are measured against brute force and the baselines. | README "Limitations" |
| L2 | **Edge cuts only.** Removing a node (e.g. deleting a secret) is modeled as removing its edge. | Matches how permissions are actually removed (detach, revoke). | Graph model |
| L3 | **Path listing is bounded** (6 hops) | Keeps the display fast. The **fix and proof are unbounded** (maxFlow/bfs), and scenario S7 demonstrates this. | UI note "paths shown ≤ 6 hops" |
| L4 | **Candidate cuts are source-side, sink-side, plus whatever the Planner proposes** (max 6 simulations). We don't enumerate *all* equal-size cuts. | Usually enough. S8 tests the collateral choice. | Planner |
| L9 | **Node splitting only for Role, Policy and Group** | Servers, users and secrets can't be "split" in real life. Those get "rotate credentials / isolate host" instead. | Split simulation |
| L10 | **Collateral counts lost reachability, not lost business value** | Weighted by recent use. That's a fair proxy for the demo. | Planner, report |
| L5 | **Simplified permission model**: no deny rules, conditions, permission boundaries or resource policies | Real cloud permission logic is a huge project. Our model keeps the graph idea clear. | Out of scope |
| L6 | **"Control is transitive"**: holding A gives *all* of B | Standard attack-graph assumption | Graph model |
| L7 | **One compromised identity per incident** | `sourceNodes` is a list, so several is an easy extension | Future work |
| L8 | **Synthetic data only** | Required by Rule 6, and gives exact ground truth | Data |

---

## 4. Decisions log

| # | Question | Decision | Status |
|---|---|---|---|
| Q1 | Team size | **Solo** | ✅ Decided |
| Q2 | Language | Python everywhere | ✅ Decided |
| Q3 | Deployment | **Local Docker Compose first.** Find a free platform on Day 3. | ✅ Decided |
| Q4 | FalkorDB Cloud or Docker for the demo | Docker (same as local) | ✅ Default |
| Q5 | Promote sandbox to baseline | **Cut** (solo) | ✅ Decided |
| Q6 | Public MCP server | **No.** Internal only. External-client demo via local stdio, in the video (HLD §11.1). | ✅ Decided |
| Q7 | Blog post | Day 4 morning, from the README | ✅ Default |
| Q8 | Scenario graph layout | **Option C:** shared background + one scenario per graph, networkx ground truth (LLD §10.1) | ✅ Decided |
| Q11 | LLM provider | **Provider-agnostic gateway:** Groq → OpenRouter → Mistral (OpenAI-compatible). Models picked at kickoff by smoke test. Provider pinned per agent run. | ✅ Decided |
| Q13 | Architecture review #2 (Oct 15) | Adopted: one shared `run_incident(mode)` pipeline; MCP as a stdio subprocess over a `core` library (4 containers, no MCP port); Streamlit fragments + graph redraw only on phase change; lock heartbeat; API docs local-only. **Rejected as wrong or overstated:** "`GRAPH.COPY` doesn't exist" (it's documented; still checked as B5), and the Redis-stall, Q7 and RAM concerns at our size (moved to the HLD §9 scale path). | ✅ Decided |
| Q12 | Devil's-advocate review (Oct 15) | Adopted 14 fixes: company-wide needs + collateral, input-change retry, LLM Planner chooses between simulated cuts, witness paths, edge count + 1 capacity, pinned providers, collateral-first tie-break, split only for config nodes, realistic baselines, honest agent count, local-only FalkorDB port for the stdio demo, "why not networkx", minimal LLM schemas, scale claim softened. **Rejected:** blocking protected edges in the residual BFS (breaks the math) and "sum the per-jewel cuts" (overcounts). | ✅ Decided |
| Q9 | "No safe fix" suggestion | Deterministic split simulation, proven in a 2nd sandbox (LLD §4.1) | ✅ Decided |
| Q10 | Path recall definition | recall@20 on paths ≤ 6 hops + unbounded blast-radius recall | ✅ Decided |

## 4.1 Working model (Rule 13: you must understand everything)

For **every task**:
1. Claude implements the task and runs it.
2. Claude shows you: what it does, the main functions, the imports and why, the design choice, and real output from a run.
3. **You** review it, ask questions, and **you make the git commit** in your own words. The repo history shows your work across Oct 15–18.
4. At the end, you write the README "AI usage" section yourself, e.g. "Implementation pair-programmed with Claude Code. Design decisions, review, testing and commits by me."

---

## 5. Solo plan with day gates (review checkpoints included)

| Day | Build (Claude) | Your review checkpoints (~30–45 min each) | Gate: must be true by end of day |
|---|---|---|---|
| **Day 1 (Oct 15)** | Kickoff checks B1–B9 · repo + compose · generator + networkx ground truth · loader · Q0–Q11 · `mincut.py` + tests | ① toy-graph results ② graph model + generator ③ queries + min-cut | S1–S3 load. Queries return the expected rows. Min-cut matches networkx. |
| **Day 2 (Oct 16)** | MCP server + guards · fast mode `/analyze` · orchestrator + Executor/Verifier · Investigator + Planner agents | ④ MCP tools + guards ⑤ fast mode demo ⑥ agent loop | Fast mode correct on S1–S8. Agent mode reaches `DONE` on S2. |
| **Day 3 (Oct 17)** | Split simulation · Reporter · UI · eval + JSON baseline · try a free deploy | ⑦ UI walkthrough ⑧ eval table | UI works locally. Eval table produced. |
| **Day 4 (Oct 18)** | README draft (graph model, queries, algorithms, eval) · polish | ⑨ README review · **you record the video** · you write the AI-usage section + blog | **Submit by 6 PM IST** |

**Solo cuts applied from Day 1:** promote-to-baseline (FR-36), public MCP, worker crash recovery (`XAUTOCLAIM`, single worker only). The other cut-list items are cut only if a gate fails.

---

## 6. Cut list (remove from the top if behind schedule)

1. ~~Promote sandbox to baseline (FR-36)~~ (already cut, solo)
2. ~~Public MCP~~ (already cut). Local stdio demo stays; it's cheap.
3. ~~Worker crash recovery (`XAUTOCLAIM`)~~ (already cut, solo)
4. Split simulation (§4.1). Keep CASE 0 (rotate credentials), drop the split search, and show "manual review".
5. Sink-side alternative cut
5. Scenario S7 (long path)
6. Betweenness tool. Path frequency is enough.
7. LLM Planner. Fall back to the **code ranking** (same collateral → size → staleness order) and keep LLM agents for investigation and the report. Loses the custom-cut ability only.

**Never cut:** min-cut, company-wide needs, `simulate_cut` + collateral, sandbox, verification proof, the eval table with baselines, the README graph model and queries, the demo video.

---

## 7. Kickoff checklist (first 2 hours, Oct 15)

- [ ] `docker pull falkordb/falkordb:latest` and record the version
- [ ] Toy graph test: B1, B2, B3, B4, B5, B6, B7
- [ ] LLM smoke test on Groq, OpenRouter and Mistral (B8). Record the chosen model ids and their rate limits.
- [ ] MCP hello-world tool listed from another container (B9)
- [ ] Create the repo, first commit, README skeleton with **AI usage disclosure**
- [x] Update these docs with the test results (B1–B7 done in P1.3; remaining tags are for the LLM, MCP, Streamlit and deploy checks)
