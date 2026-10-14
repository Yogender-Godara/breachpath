# 01: Requirements

> **Status:** pre-hackathon planning notes (Rule 8). Source: [Problem Statement](../BreachPath_Problem_Statement.md).
> Priority uses **MoSCoW**: **M** = Must (needed to submit), **S** = Should (needed to win), **C** = Could (only if time allows), **W** = Won't (this hackathon).

---

## 1. Goal

Build an **AI multi-agent system on FalkorDB**. Given one compromised identity, it should:

1. find every attack path to the crown jewels,
2. compute the **smallest safe fix** (fewest permissions to remove, with no business access broken),
3. apply the fix to a **sandbox**,
4. **prove** the fix worked, and
5. explain everything in plain language.

**Track:** Track 1, *Agents That Act on Connected Data*.

---

## 2. Users and use cases

| Actor | Use case |
|---|---|
| **Security responder** | "Key for `ravi` leaked. What can the attacker reach, and what should I cut right now?" |
| **Cloud engineer** | "Will this fix break deploys?" They read the verification result before approving. |
| **Manager / auditor** | They read the incident report: paths found, changes made, the reason for each, and the proof. |
| **Judge (demo)** | Runs a scenario, watches agents work live, and compares results with and without the graph. |
| **External AI assistant** (C) | Uses BreachPath tools from Claude Desktop or Claude Code through MCP. |

---

## 3. Functional requirements

### 3.1 Data and graph

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Generate a **synthetic company** of about 150 nodes (User, Group, Role, Policy, Server, Database, Secret) with a fixed random **seed**, so the data is the same on every run. | M |
| FR-02 | Plant **8 attack scenarios (S1–S8)** with an **answer key**: the true paths, the true minimum fix, and the expected verdict. | M |
| FR-03 | Load the data into FalkorDB as one **baseline graph per tenant** (`t:{tenant}:baseline`), with indexes. | M |
| FR-04 | Store **business needs** for the **whole company** (not just the compromised user) as isolated `:BusinessNeed` nodes: `explicit` ones from the scenario + `usage` ones generated for every principal from access used in the last 30 days. | M |
| FR-05 | Mark **crown jewels** on resources (`crown_jewel: true`). | M |
| FR-06 | Load or reset any scenario through one API call. | S |

### 3.2 Investigation (problem statement R1, R2)

| ID | Requirement | Priority |
|---|---|---|
| FR-10 | Compute the **blast radius**: every crown jewel reachable from the compromised identity. | M |
| FR-11 | List **attack paths** (node ids, edge ids, hop count), shortest first, up to a top-K. | M |
| FR-12 | Count paths per crown jewel, and per-edge "path frequency" (how many paths use each edge). | S |
| FR-13 | Return shortest paths per target using `algo.SPpaths`. | S |

### 3.3 Planning the fix (R3, R4, R5)

| ID | Requirement | Priority |
|---|---|---|
| FR-20 | Rank **choke points** using `algo.betweenness` (global hint) and path frequency (incident-specific). | S |
| FR-21 | Compute the **minimum cut** with `algo.maxFlow` plus a residual-graph search, which gives the exact edges to remove. | M |
| FR-22 | **Protect business access**: edges on business-need paths get capacity = (edge count + 1), so the min-cut never picks them and many ordinary paths can never look like "no safe fix". | M |
| FR-23 | If the max flow is ≥ the protection capacity, return **"no safe fix"** (M). Compute the alternative **in code**: rotate credentials if the identity legitimately needs the jewel, or split the divergence node. **Prove** it in a second sandbox, and show it as "verified" only if proven (S). See LLD §4.1. | M/S |
| FR-24 | Give every edge in the fix a **reason** and a **witness path** (an attack path it cuts, taken from the max-flow itself, so it works even for unlisted or long paths). | M |
| FR-25 | Compute **collateral** for any candidate cut: which *other* principals lose access to which resources (before/after reachability). | M |
| FR-26 | Offer **candidate cuts** (source-side, sink-side, plus any the Planner proposes). The Planner chooses by collateral → size → staleness. Only cuts verified by simulation can be applied. | M |

### 3.4 Acting and verifying (R6, R7)

| ID | Requirement | Priority |
|---|---|---|
| FR-30 | Create a **sandbox copy** of the baseline for each incident (`GRAPH.COPY`). | M |
| FR-31 | Apply the fix **only to the sandbox**. Write tools must refuse any graph that isn't a sandbox. | M |
| FR-32 | **Verify the attack is blocked**: `maxFlow(attacker → crown jewels) == 0` on the sandbox. | M |
| FR-33 | **Verify business access**: every business need is still reachable on the sandbox. | M |
| FR-34 | If verification fails, retry **only if an input changes** (e.g. protect a broken need's alternative path, or drop a failed custom cut), up to 3 attempts. Otherwise escalate as "needs human" right away. | M |
| FR-35 | Export the final fix as a **change plan** (JSON plus a readable list). | S |
| FR-36 | Promote the sandbox to the baseline after human approval (demo only). | C |

### 3.5 Agents and tools (R8, R9)

| ID | Requirement | Priority |
|---|---|---|
| FR-40 | Expose graph capabilities as **MCP tools** from a BreachPath MCP server. | M |
| FR-41 | LLM agents **choose their own tools** from an allow-list for their role. | M |
| FR-42 | Use **3 LLM agents** (Investigator, Planner, Reporter) + **2 deterministic workers** (Executor, Verifier), coordinated by an Orchestrator. Described exactly this way, never as "5 agents". | M |
| FR-43 | Write a **plain-language report** covering paths, fix, reasons, proof, collateral and confidence. Each fix uses its **real remediation verb** (detach policy, remove membership, revoke SSH key, delete and rotate a stored secret). | M |
| FR-44 | Stream every agent step live to the UI as events. | S |
| FR-45 | Provide a **fast mode**: deterministic analysis with no LLM, the same paths, cut and proof, returned in about 1 second. | S |

### 3.6 UI

| ID | Requirement | Priority |
|---|---|---|
| FR-50 | Pick a scenario and a compromised identity, then start an incident. | M |
| FR-51 | Show the attack paths as a **graph picture**, with cut edges highlighted. | M |
| FR-52 | Show a live agent timeline: agent, tool, latency, result. | S |
| FR-53 | Show the verification result: attack blocked ✅/❌ and business OK ✅/❌. | M |
| FR-54 | Show the **BreachPath vs baselines** comparison table. | S |

### 3.7 Evaluation

| ID | Requirement | Priority |
|---|---|---|
| FR-60 | Run all scenarios and score them against the answer key. Path recall is measured on paths **≤ 6 hops** (recall@20). Longer paths are covered by blast-radius recall and the max-flow proof. Ground truth comes from an **independent networkx reference** on the final graph (LLD §10.4). | M |
| FR-61 | Run **realistic baselines**: disable-user, greedy-betweenness, greedy-path-frequency, and the same LLM with a JSON dump. Compare fix size, residual attack, broken needs and collateral. | S |
| FR-62 | Compute the true optimum by **brute force** on small scenarios, to check the min-cut. | S |

### 3.8 Won't do (this hackathon)

| ID | Item | Priority |
|---|---|---|
| FR-90 | Connecting to real AWS, Azure or GCP, or changing real permissions | W |
| FR-91 | Detecting the leak itself | W |
| FR-92 | Network or CVE-based attack paths | W |
| FR-93 | Multi-cloud | W |
| FR-94 | Login, auth and user management for the UI | W |

---

## 4. Non-functional requirements

All numbers are **targets to measure**, not claims. Measure them on Day 3 and put the real numbers in the README.

### 4.1 Latency

| ID | Requirement | Target |
|---|---|---|
| NFR-L1 | One graph tool call (150-node graph), p95 | < 200 ms |
| NFR-L2 | Fast mode `/analyze` end to end (paths, min-cut, verify), p95 | < 1 s |
| NFR-L3 | Agent mode: time to first event in the UI | < 1 s |
| NFR-L4 | Agent mode: full incident end to end, p50 | < 45 s |
| NFR-L5 | API accept (`POST /incidents` → `202`) | < 100 ms |

How we reach these: indexed lookups, parameterized queries, bounded path queries, the deterministic fast path, a fast LLM provider first (Groq), turn caps, parallel tool calls and an LLM response cache. Details are in [02_HLD.md §8](02_HLD.md#8-low-latency-design).

### 4.2 Scalability

| ID | Requirement |
|---|---|
| NFR-S1 | API and workers are **stateless**. Add replicas to handle more incidents. |
| NFR-S2 | Work goes through a **queue**, so slow LLM calls never block the API. |
| NFR-S3 | **One graph per tenant**, so tenants can be spread across FalkorDB instances. |
| NFR-S4 | Read-heavy graph queries can go to **read replicas** (`GRAPH.RO_QUERY`). |
| NFR-S5 | Larger graphs (10k+ nodes) are **untested**. Path listing would need tighter bounds there, while maxFlow/bfs-based decisions don't enumerate paths. Documented, not claimed. |

### 4.3 Availability and reliability

| ID | Requirement |
|---|---|
| NFR-A1 | Every service has a `/healthz` check and restarts automatically (`restart: unless-stopped`). |
| NFR-A2 | **Graceful degradation**: if the LLM is down, fast mode still returns paths, fix and proof, with a template report. |
| NFR-A3 | Jobs are **at-least-once** with a checkpoint after every agent step, so a crashed worker resumes rather than restarting. |
| NFR-A4 | Write tools are **idempotent**: applying the same fix twice gives the same result. |
| NFR-A5 | **Zero writes to the baseline** by agents. Enforced in code, not just in prompts. |
| NFR-A6 | Same seed in, same graph and same graph-algorithm results out. |
| NFR-A7 | Demo target: up through the judging window. The recorded video is the backup. |

### 4.4 Security

| ID | Requirement |
|---|---|
| NFR-SEC1 | All Cypher is **parameterized**. The LLM never builds a write query. |
| NFR-SEC2 | Each agent gets only the **tools its role needs** (least privilege for agents). |
| NFR-SEC3 | The optional raw Cypher tool is **read-only** (`GRAPH.RO_QUERY`), with a timeout and an enforced `LIMIT`. |
| NFR-SEC4 | Secrets (API keys, DB password) come from environment variables and are never committed. |
| NFR-SEC5 | Synthetic data only (Rule 6). |
| NFR-SEC6 | The MCP server is **not publicly reachable** (no published port; stdio for local external clients). Write tools also check the incident lock owner and a sandbox quota. |
| NFR-SEC7 | Agent mode on a public deployment requires a demo token, a rate limit and at most 2 concurrent incidents, to cap LLM spend. |

### 4.5 Simplicity and approachability

| ID | Requirement |
|---|---|
| NFR-U1 | **One language** (Python) for every service. |
| NFR-U2 | **One command** to run everything: `docker compose up`. |
| NFR-U3 | **4 containers**: falkordb, api, worker, ui. The MCP server is a stdio subprocess of the worker (no container, no port). API, worker and UI share one image. **One pipeline** (`run_incident`) serves both modes. |
| NFR-U4 | A new developer can run the demo in under 10 minutes from the README. |

### 4.6 Explainability and observability

| ID | Requirement |
|---|---|
| NFR-E1 | Every decision links to graph evidence: a path, an edge id or an algorithm output. |
| NFR-E2 | Every agent step is logged as an event: agent, tool, input, output summary, latency, tokens. |
| NFR-E3 | The incident event log **is** the audit trail. |

---

## 5. Constraints (from the hackathon rules)

| Rule | Constraint | How we meet it |
|---|---|---|
| 2, 3 | FalkorDB is the primary database and does real work | Paths, min-cut, verification and sandboxing are all FalkorDB. Without it, nothing works. |
| 6 | Public or synthetic data only | Our own generator |
| 7 | Oct 15, 12:01 AM IST to Oct 18, 11:59 PM IST | Internal deadline: **Oct 18, 6:00 PM IST** |
| 8 | Main coding and design after kickoff | These docs are notes and sketches. Code starts after kickoff. |
| 10 | Repo, README, graph model, queries, video, deploy or setup steps | Section 7 checklist |
| 11–13 | Disclose AI use, and understand every part | README "AI usage" section. Everyone can explain the graph model and queries. |

---

## 6. Acceptance criteria (definition of done)

| # | Criterion | How tested |
|---|---|---|
| AC-1 | Path recall = 100% and precision = 100% on all scenarios | `eval/run_eval.py` against the answer keys |
| AC-2 | Fix size equals the true minimum on every scenario that has a safe fix | Brute-force checker |
| AC-3 | After the fix, `maxFlow == 0` and every business need is reachable | Verifier output |
| AC-4 | The "no safe fix" scenario returns "no safe fix" plus a suggestion | Scenario S5 |
| AC-5 | The baseline write guard blocks a write to `t:*:baseline` | Unit test |
| AC-6 | When the LLM is unavailable, fast mode still works | Kill the API key and re-run |
| AC-7 | BreachPath is minimal, breaks no need, and has collateral ≤ every baseline on each scenario. Any scenario where it doesn't is reported honestly. | Eval table in the README |
| AC-9 | On S8, the Planner picks the zero-collateral cut | Eval + event log |
| AC-8 | `docker compose up` works on a clean machine | Fresh clone test |

---

## 7. Submission checklist (Rule 10)

- [ ] Public GitHub repo
- [ ] README: setup, **graph data model**, architecture diagram
- [ ] README: **Cypher queries and algorithms** used (`algo.maxFlow`, `algo.betweenness`, `algo.SPpaths`, `algo.bfs`, variable-length paths)
- [ ] Demo video (3–5 min)
- [ ] Live deployment URL, or full local setup steps
- [ ] Track named: **Track 1, Agents That Act on Connected Data**
- [ ] AI-assistance disclosure
- [ ] Eval results table (with graph vs without graph)
- [ ] Blog post (optional, for the AirPods side prize)

---

## 8. Traceability (problem statement → requirements → design)

| Problem statement | Functional reqs | Component (HLD) | Test |
|---|---|---|---|
| R1 Find every path | FR-10, FR-11 | Investigator + `find_attack_paths` | AC-1 |
| R2 Rank paths | FR-11, FR-12, FR-13 | Investigator + `shortest_paths` | AC-1 |
| R3 Choke points | FR-20 | Planner + `choke_points` | Eval |
| R4 Smallest fix | FR-21, FR-25, FR-26 | Planner + `compute_min_cut` + `simulate_cut` | AC-2, AC-9 |
| R5 Respect business | FR-22, FR-23 | `protect_business_paths` | AC-3, AC-4 |
| R6 Act safely | FR-30, FR-31 | Executor + sandbox guard | AC-5 |
| R7 Verify | FR-32, FR-33, FR-34 | Verifier | AC-3 |
| R8 Explain | FR-24, FR-43 | Reporter | Manual review |
| R9 Choose tools via MCP | FR-40, FR-41 | MCP server + agents | Event log |
