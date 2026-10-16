# BreachPath: Task Tracker (live)

> **How we use this file**
> - Every subtask is small (about 15–60 min). Tick `- [x]` the moment it's done.
> - Each task's **Done when** line must be true before the task is marked ✅.
> - **👤 You** tasks are yours: reviews, commits, keys, video, blog, AI disclosure. **🤖 Claude** builds and shows you the working output.
> - After every task: Claude shows what it does, the main functions, the imports and why, plus real output. **You review, then you commit** (Rule 13).
> - Blockers go in [Blockers log](#blockers-log). Any change to a design decision goes in [Decision changes](#decision-changes-during-build), and the docs get updated.
> - Status: ⬜ todo · 🟨 in progress · ✅ done · ⛔ blocked · ✂️ cut (by the cut list, [05 §6](docs/05_Problems_Blockers_Risks.md))

**Deadline:** submit by **Oct 18, 6:00 PM IST** (hard close 11:59 PM IST).
**Design docs:** [docs/README.md](docs/README.md) · **Problem:** [BreachPath_Problem_Statement.md](BreachPath_Problem_Statement.md)

---

## Live status

| Phase | Day | Status | Done / Total |
|---|---|---|---|
| P0 Pre-kickoff prep | Oct 14 | 🟨 | 4 / 12 |
| P1 Kickoff checks (blockers B1–B9) | Day 1 | 🟨 | 30 / 32 |
| P2 Data and graph | Day 1 | 🟨 | 25 / 28 |
| P3 Queries and min-cut | Day 1 | 🟨 | 19 / 22 |
| P4 Core tools and guards | Day 2 | ✅ | 26 / 26 |
| P5 Pipeline, state and API (fast mode) | Day 2 | 🟨 | 15 / 17 |
| P6 MCP server and agents (agent mode) | Day 2 | 🟨 | 17 / 20 |
| P7 Reporter and UI | Day 3 | 🟨 | 10 / 12 |
| P8 Evaluation | Day 3 | 🟨 | 7 / 9 |
| P9 Hardening and deploy | Day 3 | ⬜ | 0 / 11 |
| P10 Submission | Day 4 | ⬜ | 0 / 14 |
| **Total** | | | **153 / 203** |

**Now working on:** P8.3 Review ⑧ (yours): read the eval table
**Day gates:** Day 1 ⬜ · Day 2 ⬜ · Day 3 ⬜ · Day 4 ⬜ (see [05 §5](docs/05_Problems_Blockers_Risks.md))

---

## P0: Pre-kickoff prep (no coding, Rule 8)

### P0.1 Design docs ✅ 🤖
- [x] Problem statement
- [x] Requirements, HLD, LLD, flow diagrams, risks, prompts (`docs/`)
- [x] Two review rounds applied (05 decisions Q12, Q13)

### P0.2 Your machine and accounts 🟨 👤
- [ ] Groq, OpenRouter and Mistral API keys ready (password manager, never in the repo)
- [ ] Docker Desktop running (WSL2 backend), about 8 GB RAM free for Docker
- [ ] Python 3.12, git, VS Code + a Mermaid preview extension installed
- [x] `docker pull falkordb/falkordb:latest` done
- [ ] GitHub repo name `breachpath` is free (create the repo **after** kickoff)
- [ ] Joined the WeMakeDevs / FalkorDB community channel for rule updates
- [ ] 9 review slots (30–45 min each) blocked in your calendar
- [ ] Screen recorder (OBS or similar) installed and mic tested
- [ ] Blog account (Hashnode / dev.to) for the side prize

**Done when:** every box is ticked before you start P1.

---

## P1: Kickoff checks (Day 1, first 2 hours)

### P1.1 Repo skeleton ✅ 🤖 + 👤
- [x] 👤 Create the public GitHub repo `breachpath` and clone it locally
- [x] Folder skeleton per [LLD §1](docs/03_LLD.md) (`core/`, `mcp_server/`, `agents/`, `orchestrator/`, `api/`, `worker/`, `ui/`, `data/`, `eval/`, `tests/`)
- [x] `.gitignore` (`.env`, `__pycache__`, `.venv`, data outputs) + `.gitattributes` (LF endings)
- [x] `env.example` with every variable from [LLD §12](docs/03_LLD.md) (no secrets; named without the dot because `.env*` files are blocked for Claude, so your keys stay private)
- [x] `pyproject.toml` (falkordb, fastapi, uvicorn, openai, mcp, pydantic, redis, networkx, pyyaml, streamlit, pytest)
- [x] README skeleton: title, one-line pitch, a placeholder "AI usage" section, Track 1
- [x] Copy `docs/` + the problem statement in, labelled "planning notes prepared before kickoff (Rule 8)"
- [x] 👤 First commit

**Done when:** the repo is public and the skeleton is pushed.

### P1.2 FalkorDB up ✅ 🤖
- [x] `docker-compose.yml` with the `falkordb` service only (volume, `requirepass`, `expose`)
- [x] `docker-compose.dev.yml`: `127.0.0.1:6379` + browser `127.0.0.1:3300` (host 3300 → container 3000; 3000 was taken on the dev machine) (local only)
- [x] `docker compose up` → `PING` works and the FalkorDB browser opens
- [x] Record the FalkorDB version in `docs/VERIFY_RESULTS.md`

**Done when:** FalkorDB answers from Python on localhost.

### P1.3 FalkorDB feature checks (B1–B7) ✅ 🤖
Script: `spikes/verify_falkordb.py`. One toy graph per check, results written to `docs/VERIFY_RESULTS.md`.
- [x] **B1** `algo.maxFlow` exists and returns the known max flow on a 4-node graph
- [x] **B2a** `edges` from maxFlow carry properties (`e.eid` works)
- [x] **B2b** what happens when the flow is 0: a row with `maxFlow = 0`, or no row
- [x] **B2c** multiple `targetNodes` work
- [x] **B3** `algo.SPpaths`: parameter names, `relDirection` value, `maxLen`, `pathCount`, behaviour when there's no path
- [x] **B4** `algo.bfs(node, depth, null)` signature and output
- [x] **B5** `GRAPH.COPY` works (and Python `graph.copy()`)
- [x] **B6** relationship property index syntax
- [x] **B7** `CALL algo.*` after `MATCH … WITH collect(...)`
- [x] Apply the fallback for any check that fails ([05 §1](docs/05_Problems_Blockers_Risks.md)), and update the LLD queries
- [x] Remove the **[VERIFY]** tag from the docs for each check that passed

**Done when:** every B1–B7 result is recorded and the design is adjusted where needed.

### P1.4 LLM provider smoke test (B8) ✅ 🤖 + 👤
- [x] 👤 Put the keys in the local `.env`
- [x] Script `spikes/llm_smoke.py`: for each provider, 1 tool call, 2 parallel tool calls, 1 `submit_fix_plan`-shaped call
- [x] Record per provider: passes, latency, free-tier limits seen
- [x] Pick the models for Investigator, Planner and Reporter → `.env.example` + `VERIFY_RESULTS.md`
- [x] Record provider quirks (tool_choice support, JSON mode, parallel calls) as capability flags

**Done when:** at least one provider passes all 3 calls, and the models are chosen.

### P1.5 MCP stdio and Streamlit checks (B9) ✅ 🤖
- [x] Hello-world MCP server (`MCPServer`) over stdio: the client starts the subprocess, lists tools, calls one (inside the worker container)
- [x] Streamlit version supports `st.fragment(run_every=…)`

**Done when:** both are confirmed, or their fallbacks are recorded.

### P1.6 Review ① 👤
- [ ] Review the toy-graph results, model choice and MCP check
- [ ] Commit

---

## P2: Data and graph (Day 1)

### P2.1 Schema and client ✅ 🤖
- [x] `core/graph/schema.py`: labels, edge types, `ATTACK_TYPES`, remediation verb per type ([LLD §2.3](docs/03_LLD.md))
- [x] `core/graph/client.py`: connection from env, `ro_query` / `query`, per-query timeout, graph-name helpers
- [x] Index creation function ([LLD §2.5](docs/03_LLD.md))

**Done when:** you can connect and create indexes on an empty graph.

### P2.2 Background company generator ✅ 🤖
- [x] `data/generator.py` skeleton, `SEED=42`
- [x] Nodes per [LLD §10.2](docs/03_LLD.md): 6 teams, 50 users (40 humans + 10 service accounts), 12 groups, 15 roles, 25 policies, 10 servers, 10 DBs, 6 secrets
- [x] Noise-edge rules (membership, assume, policy, read, SSH, secrets), each with an `eid`
- [x] `last_used_days` distribution (about 40% recent, i.e. ≤ 30 days)
- [x] Output `data/generated/background.json` (nodes + edges)

**Done when:** the same seed always gives the same file.

### P2.3 Scenario files S1–S3 ✅ 🤖
- [x] YAML schema per [LLD §10.3](docs/03_LLD.md) + parser with validation
- [x] `s1.yaml` simple chain
- [x] `s2.yaml` problem-statement example (escalation + saved password; needs: staging, build)
- [x] `s3.yaml` shared choke point (4 paths, cut = 1)
- [x] Planting: add scenario nodes and edges + `background_links` to the background company

**Done when:** each scenario merges into a valid graph.

### P2.4 Business needs ✅ 🤖
- [x] Explicit needs from YAML
- [x] Usage needs for **every** principal (all edges on the path ≤ 30 days) ([LLD §2.2](docs/03_LLD.md))
- [x] Mark needs with no path as `valid=false` + log a warning

**Done when:** each scenario has company-wide `:BusinessNeed` records.

### P2.5 Ground truth (networkx) ✅ 🤖
- [x] Build a `MultiDiGraph` from the final graph
- [x] Reachable jewels, true paths ≤ 6 hops, long-path jewels
- [x] Protected edges (lowest-eid tie-break), `protect_cap`, min-cut on the split graph with a super-sink
- [x] `expect` assertions: generation stops with a clear error on mismatch
- [x] Write `answer_keys/sN.json`

**Done when:** S1–S3 answer keys are generated and match their `expect` blocks.

### P2.6 Loader ✅ 🤖
- [x] Q0 batched `UNWIND` per label and per edge type
- [x] Load `:BusinessNeed` nodes
- [x] Graph name `t:sN:baseline`, delete then load, create indexes
- [x] CLI: `python -m core.graph.loader s1 s2 s3` (the loader lives in `core/graph/` so the API reuses it, LLD §1)

**Done when:** S1–S3 are loaded, and node and edge counts match `graph.json`.

### P2.7 Review ② 👤
- [ ] Look at S2 in the FalkorDB browser (the attack paths should be visible)
- [ ] Review the generator, scenarios and ground truth
- [ ] Commit

---

## P3: Queries and min-cut (Day 1)

### P3.1 Cypher queries ✅ 🤖
- [x] `core/graph/queries.py`: Q1 blast radius, Q2 paths, Q3 SPpaths, Q4 edge frequency
- [x] Q5 betweenness, Q6 maxFlow, Q7 all attack edges
- [x] Q8 protect (protect_cap = edge count + 1), Q9 bfs, Q10 apply fix, Q11 needs check, Q12 reach sets
- [x] Every query parameterized. Edge-type list and hop bound come from constants only.

**Done when:** every query runs on S2 without errors.

### P3.2 Query tests against answer keys ✅ 🤖
- [x] Q1 / Q2 vs answer keys on S1–S3 (recall and precision = 100%)
- [x] Q6 max flow = answer key `min_cut_size`
- [x] Q9 bfs reach = answer key `reachable_jewels`
- [x] Q11 all needs ok on the untouched baseline

**Done when:** `pytest tests/test_queries.py` is green.

### P3.3 Min-cut engine ✅ 🤖
- [x] `core/graph/mincut.py`: build the residual graph (forward and backward arcs)
- [x] Source-side cut + **sanity assert** (sum of cut caps == max flow)
- [x] Sink-side cut (reverse BFS from the targets)
- [x] Witness path per cut edge (walk the flow back to src and forward to a jewel)
- [x] `safe = max_flow < protect_cap`

**Done when:** it returns candidates + witnesses on S2.

### P3.4 Min-cut unit tests ✅ 🤖
- [x] Textbook graph with a known min-cut
- [x] Parallel edges between the same nodes
- [x] **R23 test:** the BFS crosses protected edges, and blocking them makes the assert fail
- [x] A "no safe fix" graph (`max_flow ≥ protect_cap`)
- [x] Many-paths graph: no false "no safe fix"

**Done when:** `pytest tests/test_mincut.py` is green.

### P3.5 Cross-check with networkx ✅ 🤖
- [x] FalkorDB min-cut size == networkx min-cut size on S1–S3

**Done when:** the two independent implementations agree.

### P3.6 Review ③ + Day 1 gate 👤
- [ ] Review the queries and the min-cut (you should be able to explain the residual BFS)
- [ ] Day 1 gate: S1–S3 load, queries match, min-cut matches networkx
- [ ] Commit

---

## P4: Core tools and guards (Day 2)

### P4.1 Read tools ✅ 🤖
- [x] `core/tools/read.py`: `get_blast_radius`, `find_attack_paths` (k ≤ 50, truncated flag), `shortest_paths`
- [x] `edge_path_frequency`, `choke_points` (samplingSize ≥ node count)
- [x] `get_node`, `get_edge`, `run_readonly_cypher` (RO, timeout, 200-row cap)
- [x] `compute_min_cut` (candidates, witnesses, max flow, safe, protect_cap, protected_in_way)
- [x] Uniform error model `{ok:false, error:{code, message}}`

**Done when:** every read tool works on S2 from a Python shell.

### P4.2 Guards ✅ 🤖
- [x] `core/tools/guards.py`: sandbox-name regex
- [x] Lock-owner check (`lock:inc:{id}` == token)
- [x] Scope (graph name contains the incident id) + quota (3 sandboxes + 6 simulations per incident, 20 graphs total)
- [x] Tests: a baseline write is refused, a wrong token is refused, the quota is enforced (AC-5)

**Done when:** `pytest tests/test_guards.py` is green.

### P4.3 Write tools ✅ 🤖
- [x] `create_sandbox` (GRAPH.COPY, reused if it already exists, tracked in `sbx:expiry`)
- [x] `protect_business_paths` (company-wide needs, protect_cap)
- [x] `apply_fix` (idempotent, refuses protected edges)

**Done when:** sandbox → protect → apply works on S2 and the baseline is unchanged.

### P4.4 Simulation and collateral ✅ 🤖
- [x] Q12 before snapshot, cached per sandbox
- [x] `simulate_cut`: copy to `-sim-n`, apply, verify, Q12 after, collateral (weighted), delete the copy
- [x] Returns `blocks_attack`, `needs_ok`, `broken_needs`, `collateral`, `size`, `min_size`, `stale_days_total`

**Done when:** both S2 candidates are simulated and collateral numbers are produced.

### P4.5 Verify tool ✅ 🤖
- [x] `verify_fix`: A maxFlow = 0, B bfs reaches no jewel, C every need ok, D collateral report
- [x] A/B mismatch → `pass=false`, "proof mismatch"

**Done when:** S2 after the fix: pass ✅.

### P4.6 Split simulation (S) ✅ 🤖
- [x] CASE 0: src legitimately needs a jewel → `rotate_credentials`
- [x] Find the divergence node (Role/Policy/Group only)
- [x] Split in a `-split` sandbox → re-run min-cut → verify → `proven`

**Done when:** S5 gives a **proven** policy-split suggestion.

### P4.7 Scenarios S4–S8 ✅ 🤖
- [x] `s4.yaml` decoys (precision)
- [x] `s5.yaml` no safe fix (divergence node = Policy)
- [x] `s6.yaml` betweenness trap (hub group)
- [x] `s7.yaml` 8-hop path (completeness)
- [x] `s8.yaml` collateral choice (two equal cuts)
- [x] Ground truth + `expect` pass for all 8; all loaded

**Done when:** `python -m data.scenarios --all` succeeds.

---

## P5: Pipeline, state and API: fast mode (Day 2)

### P5.1 State, events and lock ✅ 🤖
- [x] `orchestrator/state.py`: incident JSON load/save (checkpoint)
- [x] `orchestrator/events.py`: `XADD inc:{id}:events` with the event schema ([LLD §8](docs/03_LLD.md))
- [x] `orchestrator/lock.py`: `SET NX PX` + heartbeat thread every 15 s (Lua check-and-extend) + safe release

**Done when:** a test shows the heartbeat keeps a 90 s task's lock alive.

### P5.2 State machine and retry policy ✅ 🤖
- [x] `orchestrator/state_machine.py`: statuses + transitions ([LLD §7](docs/03_LLD.md))
- [x] `retry_change()`: retry only when an input changes; invariant violations → `NEEDS_HUMAN`

**Done when:** unit tests cover pass, retry-with-change, no-change → NEEDS_HUMAN, and max 3 attempts.

### P5.3 One pipeline: fast brains ✅ 🤖
- [x] `core/pipeline.py: run_incident(incident, mode)` with shared Prepare / Execute / Verify
- [x] Fast brains: deterministic findings, code ranking (collateral → size → staleness), template report
- [x] Executor validation (eids exist, not protected)

**Done when:** `run_incident(mode="fast")` finishes S2 with a correct plan and pass.

### P5.4 FastAPI ✅ 🤖
- [x] `/healthz`, `/readyz` (pings FalkorDB)
- [x] `GET /scenarios`, `POST /scenarios/{sid}/load` (409 if an incident is active)
- [x] `POST /analyze` (fast mode, sync, sandbox deleted in `finally`)
- [x] `POST /incidents` (Idempotency-Key, demo token for agent mode, rate limit, max 2 active)
- [x] `GET /incidents/{id}`, `GET /incidents/{id}/events?after=`, `GET /incidents/{id}/graph`
- [x] Error format + codes ([LLD §9](docs/03_LLD.md))

**Done when:** `/docs` works locally and `/analyze` on S2 returns the right answer.

### P5.5 Fast mode on all scenarios + review ④⑤ 👤
- [x] 🤖 Fast mode correct on **S1–S8** vs answer keys (script)
- [ ] 👤 Review the tools, guards, pipeline and API. Run `/analyze` yourself.
- [ ] 👤 Commit

---

## P6: MCP server and agents: agent mode (Day 2)

### P6.1 MCP server (stdio) ✅ 🤖
- [x] `mcp_server/server.py`: `MCPServer` (mcp 2.x), registers `core.tools`, stdio only. The worker passes FalkorDB env vars via `StdioServerParameters(env=…)`
- [x] Profiles `full` / `readonly`
- [x] Tool descriptions written for LLMs (short, clear, with limits)

**Done when:** a test client lists and calls tools over stdio.

### P6.2 LLM gateway ✅ 🤖
- [x] `agents/llm_gateway.py`: providers from env (base URL, key, model per agent)
- [x] Single provider (`LLM_PROVIDER`), called directly; LLM failure → `LLMUnavailable` (degrade)
- [x] Timeout 30 s, 1 retry, capability flags (forced tool choice, JSON mode, parallel)
- [x] Response cache in Redis (`llmcache:{hash}`)

**Done when:** a cached 2nd call returns instantly, and a bad key gives `LLMUnavailable`.

### P6.3 Agent loop and schemas ✅ 🤖
- [x] `agents/schemas.py`: minimal LLM outputs + code enrichment ([LLD §6.3](docs/03_LLD.md)). *Enrichment lands with each agent's post-check (P6.4/P6.5).*
- [x] `agents/base.py`: MCP stdio client, allow-list → OpenAI tools, turn loop, `json.loads` guard, submit validation, reminder, max turns, degrade

**Done when:** a dummy agent completes a 2-tool task.

### P6.4 Investigator ✅ 🤖
- [x] Prompt from [06 §2](docs/06_Agent_Prompts_and_Schemas.md) + allow-list
- [x] Post-check (paths are real chains) + enrichment

**Done when:** on S2 its findings match the answer key.

### P6.5 Planner ✅ 🤖
- [x] Prompt from [06 §3](docs/06_Agent_Prompts_and_Schemas.md) + allow-list incl. `simulate_cut`
- [x] Acceptance rule: only simulation-verified cuts. Otherwise the code candidate + `planner_choice_rejected`.
- [x] Enrichment (safe, max flow, witnesses, simulation numbers, suggestion)

**Done when:** on S8 it picks the zero-collateral cut.

### P6.6 Agent-mode pipeline and worker ✅ 🤖
- [x] `run_incident(mode="agent")` uses the LLM brains, checkpoints and events after each step
- [x] `worker/main.py`: consume `jobs`, take the lock + heartbeat, one MCP session per agent step (a crash only affects that step), janitor (sandbox TTL)
- [x] Add `worker` + `api` services to `docker-compose.yml`

**Done when:** `POST /incidents` on S2 → status `DONE` with live events.

### P6.7 Review ⑥ + Day 2 gate 👤
- [ ] Watch one agent run event by event. Can you explain each tool choice?
- [ ] Day 2 gate: fast mode correct on S1–S8, agent mode `DONE` on S2
- [ ] Commit

---

## P7: Reporter and UI (Day 3)

### P7.1 Reporter ✅ 🤖
- [x] Prompt from [06 §4](docs/06_Agent_Prompts_and_Schemas.md) (remediation verbs, collateral, proof)
- [x] Template fallback report (the same sections)

**Done when:** the S2 and S5 reports read well and contain only facts from the state.

### P7.2 Streamlit layout ✅ 🤖
- [x] Sidebar: scenario, load, src, mode, demo token, run, status
- [x] Tabs: Attack graph · Agent timeline · Fix & proof · Report · Eval

**Done when:** you can start fast and agent runs from the UI.

### P7.3 Graph tab ✅ 🤖
- [x] Graph component (or the static-image fallback, R17)
- [x] Sub-graph rules + colours ([LLD §15](docs/03_LLD.md))
- [x] Redraw only on phase change, cached in `session_state`

**Done when:** S2 shows its paths, cut edges and protected edges clearly.

### P7.4 Timeline, Fix & proof, Report tabs ✅ 🤖
- [x] Timeline in `st.fragment(run_every=1s)` with short polls
- [x] Fix & proof: cut edges + reasons + witnesses, ✅/❌ checks, collateral, suggestion (verified or not)
- [x] Report tab (Markdown)

**Done when:** a live agent run updates smoothly without page flicker.
*Built and tested headless (Streamlit AppTest + the real API and worker: fast run, agent run QUEUED → DONE with 23 live events). **Still to check by eye in a browser (P7.5):** no flicker during an agent run, and the graph look.*

### P7.5 Review ⑦ 👤
- [ ] Walk through the UI on S2, S5 and S8
- [ ] Commit

---

## P8: Evaluation (Day 3)

### P8.1 Metrics and baselines ✅ 🤖
- [x] `eval/metrics.py`: blast-radius recall, path recall@20, precision, fix gap, residual, broken needs, collateral, verdict, latency, tokens
- [x] Baselines: B-disable, B-betweenness, B-frequency (scored by networkx)
- [x] `eval/baseline_json_llm.py` (same model as the Planner, JSON mode, invalid JSON = fail)
- [x] `eval/bruteforce.py` (optimum, ≤ 25 candidates)

**Done when:** each baseline runs on S2.

### P8.2 Eval run ✅ 🤖
- [x] `eval/run_eval.py`: S1–S8 × (fast, agent, 4 baselines) → table; provider and model recorded per row
- [x] Save `eval/results/latest.json` + Markdown table; `/eval/latest`; Eval tab
- [x] Throttle for free-tier limits; use the response cache

**Done when:** the full table exists, honest whatever the result.

### P8.3 Review ⑧ 👤
- [ ] Read the eval table. Does it support the story? Note the weak spots honestly.
- [ ] Commit

---

## P9: Hardening and deploy (Day 3)

### P9.1 Security controls ⬜ 🤖
- [ ] Only `ui` publishes a port; `api` and `falkordb` use `expose` ([HLD §11.1](docs/02_HLD.md))
- [ ] FalkorDB password, RO queries, timeouts
- [ ] Demo token + rate limit + max 2 concurrent agent incidents
- [ ] Secrets only in `.env`; nothing secret in logs

**Done when:** a port scan of the compose host shows only the UI port.

### P9.2 Compose and reliability ⬜ 🤖
- [ ] Final `docker-compose.yml`: 4 services, healthchecks, `restart: unless-stopped`, volume
- [ ] Fresh-clone test: `docker compose up` works on a clean checkout (AC-8)
- [ ] Light chaos test: kill the worker mid-run; remove the API keys → degraded run still finishes (AC-6)

**Done when:** all three pass.

### P9.3 Latency measurement ⬜ 🤖
- [ ] Measure NFR-L1 … L5 and record the real numbers for the README

**Done when:** the numbers are recorded (whatever they are).

### P9.4 Free deployment (optional) ⬜ 🤖 + 👤
- [ ] Shortlist free platforms. Check **private networking** (only the UI public) and the current free-tier limits.
- [ ] Deploy, then smoke test S2 in fast mode
- [ ] If nothing fits: local setup steps + video (Rule 10 allows this)

**Done when:** there's a live URL, or a documented decision not to deploy.

---

## P10: Submission (Day 4)

### P10.1 README ⬜ 🤖 draft + 👤 review
- [ ] What it does + the S2 story + screenshots
- [ ] Architecture diagram + "3 LLM agents + 2 code workers"
- [ ] **Graph data model** (labels, edges, properties)
- [ ] **Cypher queries and algorithms used**: `algo.maxFlow`, `algo.bfs`, `algo.SPpaths`, `algo.betweenness`, `GRAPH.COPY`, variable-length paths
- [ ] Why FalkorDB isn't decoration + "why not networkx"
- [ ] Eval table + honest limitations + security scope
- [ ] Setup steps (local) + live URL if any; Track 1 named

**Done when:** a stranger could run it from the README alone.

### P10.2 Your parts 👤
- [ ] Write the **AI usage** section in your own words (Rules 11–13)
- [ ] External MCP demo: Claude Desktop/Code over stdio with the `readonly` profile; record a short clip
- [ ] Record the **demo video** (3–5 min): problem → S2 live → proof → S8 collateral choice → eval table
- [ ] Write the **blog post** (AirPods side prize)

### P10.3 Submit ⬜ 👤
- [ ] Final check against the submission checklist ([Requirements §7](docs/01_Requirements.md))
- [ ] Submit by **6:00 PM IST, Oct 18**, naming Track 1
- [ ] Optional fixes before 11:59 PM IST

---

## Blockers log

| Date / time | Task | Blocker | Fallback taken | Status |
|---|---|---|---|---|
| Oct 15 | P3.1 | `algo.maxFlow` accepts exactly ONE `relationshipTypes` entry, so Q6 can't span the 8 attack types (LLD Q6 assumed a list; P1.3 only tested one type). Details: VERIFY_RESULTS P3.1 | Q6 kept as in the LLD; its test is `xfail(strict)`. P3.1 left unticked | Closed. Fallback: option A (one `:ATTACK` type, real type in `r.kind`) |
| Oct 15 | P1.4 | Groq: every chat call returned 403 `model_permission_blocked_project` (models list fine) | OpenRouter (`nemotron-3-super`) passed all checks meanwhile | Closed. Fixed: the Default Project's model allow-list only allowed `llama-3.1-8b-instant`; the models are now allowed at org + project level (gpt-oss-120b/20b pass checks 1, 3, 4, 5) |
| Oct 15 | P6.5 | LLM cache never hit across incidents for the Planner: its task and tool arguments contained the sandbox name (with the random incident id), and one run used 7-9k tokens against Groq's 8000/minute | Fixed: `graph`, `sandbox`, `src` (and the auth params) are injected by the loop, not in the LLM's messages; tool results are compact and sorted. Proven live: a NEW incident (S8 Planner 5/5 steps, S2 Investigator 4/4 steps) is served fully from the cache with the same result | Closed |
| Oct 15 | P5.5 | `algo.bfs` returns NO row when the source has no outgoing edge; `verify.attack_status` and `fast_brains.investigate` read `[0][0]` and raised IndexError. S6's source-side cut removes both of `user:kai`'s only edges, so `/analyze` ended FAILED | Fixed: `verify.reached_jewels()` returns `[]` for no row; both callers use it. Regression test `test_source_with_no_outgoing_edge_reaches_nothing` | Closed |

## Decision changes during build

| Date / time | Decision | Old → New | Why | Docs updated? |
|---|---|---|---|---|
| Oct 16 | Plan `alternatives` (P7) | A plan recorded only the chosen cut → also `alternatives`: every simulated cut NOT chosen with its collateral, size, staleness and blocks_attack/needs_ok, filled in code from `simulate_cut` (planner and fast mode); shown in Fix & proof, the Reporter slice and the template. The Reporter guard requires the Fix section to name each alternative and its collateral number | The trade-off (why this cut and not the other) is the demo's key sentence; the prompt alone was ignored by the model in a live S8 run | Yes (LLD §6.2, §9, §15, docs/06 §4) |
| Oct 16 | JSON baseline prompt is compact (P8.1) | docs/06 §5: the whole graph as JSON (nodes with label + crown_jewel, edges as objects) → the same information as rows: `crown_jewels` + edges `[eid, type, src, dst]` + valid needs `[principal, resource]` | The full JSON is about 8.5k tokens and Groq's free tier refuses a request over 8000 tokens (413); no other provider has a key. A node's label is its id prefix, and a node with no edge cannot matter | Yes (docs/06 §5, LLD §11) |
| Oct 16 | Eval table layout (P8.2) | "rows = scenarios, columns = metric × method" → rows = scenarios, columns = methods, each cell packs that method's metrics (cut and gap, residual, broken, collateral, recall/precision, verdict); the full numbers are in `latest.json` | 8 metrics × 6 methods is about 35 columns: unreadable in Markdown and in the UI | Yes (LLD §11) |
| Oct 16 | Graph component (P7.3) | LLD §15: `streamlit-agraph` (vis.js) [VERIFY], graphviz as fallback → `st.graphviz_chart` with a DOT string (the R17 fallback) | No new dependency, no graphviz binary (the browser draws it with viz.js), and the DOT is easy to unit-test; the layout is automatic. Not interactive (no drag) | Yes (LLD §15) |
| Oct 16 | `/analyze` returns the graph (P7.3) | `/analyze` → `{findings, plan, verdict, timings_ms}` → also `graph` (the same view as `GET /incidents/{id}/graph`) | A fast state is never stored, so the UI cannot ask `/graph` afterwards, and the UI has no database access | Yes (LLD §9) |
| Oct 16 | Reporter repair + template (P7.1) | A bad Business impact (collateral claim without data) after the retry → template → the code replaces that section with "Not assessed…" (`llm+repaired`); the template says "not assessed" too and passes the same guard | One unsupported sentence should not throw away a good report | Yes (LLD §6.2, docs/05 R26) |
| Oct 16 | Reporter model (P7.1) | `MODEL_REPORTER_GROQ` `openai/gpt-oss-20b` → `openai/gpt-oss-120b` (same as the Investigator and Planner) | 20B ignored rules the guard can't check; 1-2 calls per incident, so 120B is affordable | Yes (env.example, VERIFY_RESULTS) |
| Oct 16 | MCP session per agent step (P6.6) | HLD 5.4 / LLD §5: one MCP stdio subprocess per worker process, restarted on a crash → each LLM step (Investigator, Planner) opens its OWN session inside `asyncio.run` and closes it before returning; no restart logic | The worker and pipeline are plain sync code: one long-lived session would need an event loop shared across threads; a crashed subprocess now only affects the one step (`ToolCrashed` → the pipeline decides) | Yes (HLD §5.4, LLD §5, §6.1) |
| Oct 16 | Groq 400 `tool_use_failed` (P6.6) | LLD 6.1: any other 4xx is raised as-is (our bug) → a 400 where the provider rejected the model's own malformed tool call (`tool_use_failed` / "tool call validation failed") is TRANSIENT: 1 retry, then `LLMUnavailable` → the next provider → degrade to the fast brains | It is the model's slip, not our request, so it must not fail the incident while another provider may answer correctly. Any other 400/422 is still raised | Yes (LLD §6.1) |
| Oct 15 | Injected tool params (cache follow-up) | LLD 6.1: only `incident_id` and `lock_token` are hidden from the LLM → any param the code already knows (`graph`, `sandbox`, `src`) is injected too (`run_agent(inject=...)`); tool results are compact and key-sorted; the code sets the plan label (`chosen`) from the min-cut candidate names | The incident id in the messages defeated the cache across incidents; fewer arguments for the model to get wrong; fewer tokens | Yes (LLD §6.1, §6.2, docs/06) |
| Oct 15 | Planner rules (P6.5) | LLD 6.2: the LLM plans every time → no LLM call when the cut is not safe or after a failed Planner cut; a cut is accepted only if simulated ok AND without protected/missing edges; no set is ever simulated twice (a repeated `simulate_cut` is answered from the results already seen) | The split suggestion is proven in code (the LLM adds nothing, and free calls are scarce); `simulate_cut` is limited to 6 per incident and a provider restart re-runs the agent | Yes (LLD §6.2, §16, docs/06 §3) |
| Oct 15 | LLM 429 wait (P6.5) | One retry after 1.5 s → a 429 waits for its `retry-after` header (capped at `LLM_MAX_WAIT_S` = 20 s), then retries once | Groq's 8000 tokens/minute: back-to-back agents hit 429, and 1.5 s is far too short | Yes (LLD §6.1, §12) |
| Oct 15 | Who decides the jewels (P6.4) | LLD: the Investigator's findings come from the LLM → `reachable_jewels` is the code BFS (`code_facts`, shared with fast mode); the LLM's list is compared and the difference noted; the LLM's paths are post-checked and enriched in code | The pipeline picks `DONE_NO_RISK` from `reachable_jewels`: a decision must never rest on the LLM's word (graph guarantees, LLM chooses) | Yes (LLD §6.2, docs/06 §2) |
| Oct 15 | Agent loop tool errors (P6.3) | LLD 6.1: bad arguments = tool error message → bad arguments are checked with `jsonschema` first (error message to the model); an `is_error` MCP result = a crash → `ToolCrashed` (invariant, no retry) | The MCP SDK reports bad arguments as `is_error` too, so without the check a wrong `k` would look like a crash | Yes (LLD §6.1) |
| Oct 15 | LLM providers (P6.2 follow-up) | One provider → Groq then OpenRouter (`LLM_PROVIDERS`), called directly; an unavailable provider restarts the agent run on the next one (D18); one retry per call + Redis cache kept; all unavailable → degrade to fast brains | Free quotas are small (OpenRouter 50/day); Groq is faster (about 0.7 s) with 1000/day. Earlier: chain → one provider (Portkey tried, dropped: no account) | Yes (CLAUDE.md, HLD 5.3/D14/D18, LLD 6.1/12) |
| Oct 15 | LLM retry policy (P6.2) | SDK retries (2) then our retry (1) → SDK retries off, the gateway retries once; 401/403/404 fail at once (no retry); a 200 without `choices` is transient; other 4xx are raised as-is | Retries stack up against the 50 free requests/day and the 30 s timeout; blocked or bad-auth providers can't recover by retrying; a 400 is our bug and must not trigger a failover | Yes (LLD §6.1, §12, §13) |
| Oct 15 | Background usage needs (P2.4) | Random `last_used_days` only → one "daily work" chain per team's first human is forced recent (0–30) | Seed 42 gave ZERO usage needs: a 4-hop path is all-recent only ~2.5% of the time | Yes (LLD §10.2) |
| Oct 15 | FalkorDB browser host port (P1.2) | `127.0.0.1:3000` → `127.0.0.1:3300` | Port 3000 is used by another project on the dev machine | Yes (LLD §1) |
| Oct 15 | Split simulation rule (P4.6) | D = "a node with an outgoing flow edge NOT on any protected path" → D = a node on the witness path where the needs of the edge in and the edge out diverge (business non-empty, conflict empty); D must be a Role/Policy/Group. Edges record `r.needs`. `split_suggestion` is its own write tool, not part of `compute_min_cut` | `maxFlow ≥ PROTECT_CAP` is equivalent to an all-protected attack path, so the old rule can never hold (proved with a networkx prototype) | Yes (LLD §4.1, §5) |
| Oct 15 | "Recently used" for collateral (P4.4) | "recent" = 30 days (undefined) → the lost pair was reachable before the fix via edges all used within `COLLATERAL_RECENT_DAYS` = 90; weight 3 recent, 1 otherwise | Pairs within 30 days are usage needs and are protected, so a 30-day rule would never fire | Yes (LLD §4.2, §8, §12) |
| Oct 15 | Parallel edges (P3.4) | Duplicate `(src, dst)` pairs allowed → loader rejects them | `algo.maxFlow` fails on multi-edges ("requires a simple adjacency matrix"); generated data already can't have them | Yes (LLD §2.3, §4.1; VERIFY_RESULTS) |
| Oct 15 | FalkorDB image pin (P4.7 step 0) | `falkordb/falkordb:latest` → `falkordb/falkordb:6.0.2` | pin 6.0.2: `:latest` drifted after the image cache was lost (graph module ver 42201 instead of 60002) | Yes (VERIFY_RESULTS) |
| Oct 15 | Lock heartbeat (P5.1) | background `asyncio` task → background `threading.Thread` (`hold_lock` context manager yielding a handle with `.token` and `.lost`) | The same lock must work in the sync fast-mode API and in the async worker; a thread needs no event loop | Yes (LLD §1, §8) |
| Oct 15 | Edge storage (P3.1) | 8 relationship types → one `:ATTACK` type with `r.kind` = the real type | `algo.maxFlow` accepts exactly one relationship type; the 8-type list in Q6 failed live | Yes (LLD §2.3, §2.5, Q0-Q12; VERIFY_RESULTS) |
| Oct 15 | Loader CLI (P2.6) | `python -m data.load` → `python -m core.graph.loader` | The loader is product code (the API reuses it), and LLD §1 already puts it in `core/graph/` | Task only (LLD already right) |

## Daily log

| Day | Planned gate | Result | Notes |
|---|---|---|---|
| Day 1 (Oct 15) | S1–S3 load, queries match, min-cut = networkx | | |
| Day 2 (Oct 16) | Fast mode S1–S8 correct, agent mode DONE on S2 | | fast mode S1–S8 correct (scripts/check_fast_mode.py) |
| Day 3 (Oct 17) | UI works, eval table produced, deploy tried | | |
| Day 4 (Oct 18) | Submitted by 6 PM IST | | |
