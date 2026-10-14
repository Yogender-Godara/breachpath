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
| P1 Kickoff checks (blockers B1–B9) | Day 1 | 🟨 | 7 / 32 |
| P2 Data and graph | Day 1 | ⬜ | 0 / 28 |
| P3 Queries and min-cut | Day 1 | ⬜ | 0 / 22 |
| P4 Core tools and guards | Day 2 | ⬜ | 0 / 26 |
| P5 Pipeline, state and API (fast mode) | Day 2 | ⬜ | 0 / 17 |
| P6 MCP server and agents (agent mode) | Day 2 | ⬜ | 0 / 20 |
| P7 Reporter and UI | Day 3 | ⬜ | 0 / 12 |
| P8 Evaluation | Day 3 | ⬜ | 0 / 9 |
| P9 Hardening and deploy | Day 3 | ⬜ | 0 / 11 |
| P10 Submission | Day 4 | ⬜ | 0 / 14 |
| **Total** | | | **11 / 203** |

**Now working on:** P1.1 (waiting for your first commit) → P1.2
**Day gates:** Day 1 ⬜ · Day 2 ⬜ · Day 3 ⬜ · Day 4 ⬜ (see [05 §5](docs/05_Problems_Blockers_Risks.md))

---

## P0: Pre-kickoff prep (no coding, Rule 8)

### P0.1 Design docs ✅ 🤖
- [x] Problem statement
- [x] Requirements, HLD, LLD, flow diagrams, risks, prompts (`docs/`)
- [x] Two review rounds applied (05 decisions Q12, Q13)

### P0.2 Your machine and accounts ⬜ 👤
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

### P1.1 Repo skeleton ⬜ 🤖 + 👤
- [x] 👤 Create the public GitHub repo `breachpath` and clone it locally
- [x] Folder skeleton per [LLD §1](docs/03_LLD.md) (`core/`, `mcp_server/`, `agents/`, `orchestrator/`, `api/`, `worker/`, `ui/`, `data/`, `eval/`, `tests/`)
- [x] `.gitignore` (`.env`, `__pycache__`, `.venv`, data outputs) + `.gitattributes` (LF endings)
- [x] `env.example` with every variable from [LLD §12](docs/03_LLD.md) (no secrets; named without the dot because `.env*` files are blocked for Claude, so your keys stay private)
- [x] `pyproject.toml` (falkordb, fastapi, uvicorn, openai, mcp, pydantic, redis, networkx, pyyaml, streamlit, pytest)
- [x] README skeleton: title, one-line pitch, a placeholder "AI usage" section, Track 1
- [x] Copy `docs/` + the problem statement in, labelled "planning notes prepared before kickoff (Rule 8)"
- [ ] 👤 First commit

**Done when:** the repo is public and the skeleton is pushed.

### P1.2 FalkorDB up ⬜ 🤖
- [ ] `docker-compose.yml` with the `falkordb` service only (volume, `requirepass`, `expose`)
- [ ] `docker-compose.dev.yml`: `127.0.0.1:6379` + browser `127.0.0.1:3000` (local only)
- [ ] `docker compose up` → `PING` works and the FalkorDB browser opens
- [ ] Record the FalkorDB version in `docs/VERIFY_RESULTS.md`

**Done when:** FalkorDB answers from Python on localhost.

### P1.3 FalkorDB feature checks (B1–B7) ⬜ 🤖
Script: `spikes/verify_falkordb.py`. One toy graph per check, results written to `docs/VERIFY_RESULTS.md`.
- [ ] **B1** `algo.maxFlow` exists and returns the known max flow on a 4-node graph
- [ ] **B2a** `edges` from maxFlow carry properties (`e.eid` works)
- [ ] **B2b** what happens when the flow is 0: a row with `maxFlow = 0`, or no row
- [ ] **B2c** multiple `targetNodes` work
- [ ] **B3** `algo.SPpaths`: parameter names, `relDirection` value, `maxLen`, `pathCount`, behaviour when there's no path
- [ ] **B4** `algo.bfs(node, depth, null)` signature and output
- [ ] **B5** `GRAPH.COPY` works (and Python `graph.copy()`)
- [ ] **B6** relationship property index syntax
- [ ] **B7** `CALL algo.*` after `MATCH … WITH collect(...)`
- [ ] Apply the fallback for any check that fails ([05 §1](docs/05_Problems_Blockers_Risks.md)), and update the LLD queries
- [ ] Remove the **[VERIFY]** tag from the docs for each check that passed

**Done when:** every B1–B7 result is recorded and the design is adjusted where needed.

### P1.4 LLM provider smoke test (B8) ⬜ 🤖 + 👤
- [ ] 👤 Put the keys in the local `.env`
- [ ] Script `spikes/llm_smoke.py`: for each provider, 1 tool call, 2 parallel tool calls, 1 `submit_fix_plan`-shaped call
- [ ] Record per provider: passes, latency, free-tier limits seen
- [ ] Pick the models for Investigator, Planner and Reporter → `.env.example` + `VERIFY_RESULTS.md`
- [ ] Record provider quirks (tool_choice support, JSON mode, parallel calls) as capability flags

**Done when:** at least one provider passes all 3 calls, and the models are chosen.

### P1.5 MCP stdio and Streamlit checks (B9) ⬜ 🤖
- [ ] Hello-world FastMCP server over stdio: the client starts the subprocess, lists tools, calls one (inside the worker container)
- [ ] Streamlit version supports `st.fragment(run_every=…)`

**Done when:** both are confirmed, or their fallbacks are recorded.

### P1.6 Review ① 👤
- [ ] Review the toy-graph results, model choice and MCP check
- [ ] Commit

---

## P2: Data and graph (Day 1)

### P2.1 Schema and client ⬜ 🤖
- [ ] `core/graph/schema.py`: labels, edge types, `ATTACK_TYPES`, remediation verb per type ([LLD §2.3](docs/03_LLD.md))
- [ ] `core/graph/client.py`: connection from env, `ro_query` / `query`, per-query timeout, graph-name helpers
- [ ] Index creation function ([LLD §2.5](docs/03_LLD.md))

**Done when:** you can connect and create indexes on an empty graph.

### P2.2 Background company generator ⬜ 🤖
- [ ] `data/generator.py` skeleton, `SEED=42`
- [ ] Nodes per [LLD §10.2](docs/03_LLD.md): 6 teams, 50 users (40 humans + 10 service accounts), 12 groups, 15 roles, 25 policies, 10 servers, 10 DBs, 6 secrets
- [ ] Noise-edge rules (membership, assume, policy, read, SSH, secrets), each with an `eid`
- [ ] `last_used_days` distribution (about 40% recent, i.e. ≤ 30 days)
- [ ] Output `graph.json` (nodes + edges)

**Done when:** the same seed always gives the same file.

### P2.3 Scenario files S1–S3 ⬜ 🤖
- [ ] YAML schema per [LLD §10.3](docs/03_LLD.md) + parser with validation
- [ ] `s1.yaml` simple chain
- [ ] `s2.yaml` problem-statement example (escalation + saved password; needs: staging, build)
- [ ] `s3.yaml` shared choke point (4 paths, cut = 1)
- [ ] Planting: add scenario nodes and edges + `background_links` to the background company

**Done when:** each scenario merges into a valid graph.

### P2.4 Business needs ⬜ 🤖
- [ ] Explicit needs from YAML
- [ ] Usage needs for **every** principal (all edges on the path ≤ 30 days) ([LLD §2.2](docs/03_LLD.md))
- [ ] Mark needs with no path as `valid=false` + log a warning

**Done when:** each scenario has company-wide `:BusinessNeed` records.

### P2.5 Ground truth (networkx) ⬜ 🤖
- [ ] Build a `MultiDiGraph` from the final graph
- [ ] Reachable jewels, true paths ≤ 6 hops, long-path jewels
- [ ] Protected edges (lowest-eid tie-break), `protect_cap`, min-cut on the split graph with a super-sink
- [ ] `expect` assertions: generation stops with a clear error on mismatch
- [ ] Write `answer_keys/sN.json`

**Done when:** S1–S3 answer keys are generated and match their `expect` blocks.

### P2.6 Loader ⬜ 🤖
- [ ] Q0 batched `UNWIND` per label and per edge type
- [ ] Load `:BusinessNeed` nodes
- [ ] Graph name `t:sN:baseline`, delete then load, create indexes
- [ ] CLI: `python -m data.load s2`

**Done when:** S1–S3 are loaded, and node and edge counts match `graph.json`.

### P2.7 Review ② 👤
- [ ] Look at S2 in the FalkorDB browser (the attack paths should be visible)
- [ ] Review the generator, scenarios and ground truth
- [ ] Commit

---

## P3: Queries and min-cut (Day 1)

### P3.1 Cypher queries ⬜ 🤖
- [ ] `core/graph/queries.py`: Q1 blast radius, Q2 paths, Q3 SPpaths, Q4 edge frequency
- [ ] Q5 betweenness, Q6 maxFlow, Q7 all attack edges
- [ ] Q8 protect (protect_cap = edge count + 1), Q9 bfs, Q10 apply fix, Q11 needs check, Q12 reach sets
- [ ] Every query parameterized. Edge-type list and hop bound come from constants only.

**Done when:** every query runs on S2 without errors.

### P3.2 Query tests against answer keys ⬜ 🤖
- [ ] Q1 / Q2 vs answer keys on S1–S3 (recall and precision = 100%)
- [ ] Q6 max flow = answer key `min_cut_size`
- [ ] Q9 bfs reach = answer key `reachable_jewels`
- [ ] Q11 all needs ok on the untouched baseline

**Done when:** `pytest tests/test_queries.py` is green.

### P3.3 Min-cut engine ⬜ 🤖
- [ ] `core/graph/mincut.py`: build the residual graph (forward and backward arcs)
- [ ] Source-side cut + **sanity assert** (sum of cut caps == max flow)
- [ ] Sink-side cut (reverse BFS from the targets)
- [ ] Witness path per cut edge (walk the flow back to src and forward to a jewel)
- [ ] `safe = max_flow < protect_cap`

**Done when:** it returns candidates + witnesses on S2.

### P3.4 Min-cut unit tests ⬜ 🤖
- [ ] Textbook graph with a known min-cut
- [ ] Parallel edges between the same nodes
- [ ] **R23 test:** the BFS crosses protected edges, and blocking them makes the assert fail
- [ ] A "no safe fix" graph (`max_flow ≥ protect_cap`)
- [ ] Many-paths graph: no false "no safe fix"

**Done when:** `pytest tests/test_mincut.py` is green.

### P3.5 Cross-check with networkx ⬜ 🤖
- [ ] FalkorDB min-cut size == networkx min-cut size on S1–S3

**Done when:** the two independent implementations agree.

### P3.6 Review ③ + Day 1 gate 👤
- [ ] Review the queries and the min-cut (you should be able to explain the residual BFS)
- [ ] Day 1 gate: S1–S3 load, queries match, min-cut matches networkx
- [ ] Commit

---

## P4: Core tools and guards (Day 2)

### P4.1 Read tools ⬜ 🤖
- [ ] `core/tools/read.py`: `get_blast_radius`, `find_attack_paths` (k ≤ 50, truncated flag), `shortest_paths`
- [ ] `edge_path_frequency`, `choke_points` (samplingSize ≥ node count)
- [ ] `get_node`, `get_edge`, `run_readonly_cypher` (RO, timeout, 200-row cap)
- [ ] `compute_min_cut` (candidates, witnesses, max flow, safe, protect_cap, protected_in_way)
- [ ] Uniform error model `{ok:false, error:{code, message}}`

**Done when:** every read tool works on S2 from a Python shell.

### P4.2 Guards ⬜ 🤖
- [ ] `core/tools/guards.py`: sandbox-name regex
- [ ] Lock-owner check (`lock:inc:{id}` == token)
- [ ] Scope (graph name contains the incident id) + quota (3 sandboxes + 6 simulations per incident, 20 graphs total)
- [ ] Tests: a baseline write is refused, a wrong token is refused, the quota is enforced (AC-5)

**Done when:** `pytest tests/test_guards.py` is green.

### P4.3 Write tools ⬜ 🤖
- [ ] `create_sandbox` (GRAPH.COPY, reused if it already exists, tracked in `sbx:expiry`)
- [ ] `protect_business_paths` (company-wide needs, protect_cap)
- [ ] `apply_fix` (idempotent, refuses protected edges)

**Done when:** sandbox → protect → apply works on S2 and the baseline is unchanged.

### P4.4 Simulation and collateral ⬜ 🤖
- [ ] Q12 before snapshot, cached per sandbox
- [ ] `simulate_cut`: copy to `-sim-n`, apply, verify, Q12 after, collateral (weighted), delete the copy
- [ ] Returns `blocks_attack`, `needs_ok`, `broken_needs`, `collateral`, `size`, `min_size`, `stale_days_total`

**Done when:** both S2 candidates are simulated and collateral numbers are produced.

### P4.5 Verify tool ⬜ 🤖
- [ ] `verify_fix`: A maxFlow = 0, B bfs reaches no jewel, C every need ok, D collateral report
- [ ] A/B mismatch → `pass=false`, "proof mismatch"

**Done when:** S2 after the fix: pass ✅.

### P4.6 Split simulation (S) ⬜ 🤖
- [ ] CASE 0: src legitimately needs a jewel → `rotate_credentials`
- [ ] Find the divergence node (Role/Policy/Group only)
- [ ] Split in a `-split` sandbox → re-run min-cut → verify → `proven`

**Done when:** S5 gives a **proven** policy-split suggestion.

### P4.7 Scenarios S4–S8 ⬜ 🤖
- [ ] `s4.yaml` decoys (precision)
- [ ] `s5.yaml` no safe fix (divergence node = Policy)
- [ ] `s6.yaml` betweenness trap (hub group)
- [ ] `s7.yaml` 8-hop path (completeness)
- [ ] `s8.yaml` collateral choice (two equal cuts)
- [ ] Ground truth + `expect` pass for all 8; all loaded

**Done when:** `python -m data.generate --all` succeeds.

---

## P5: Pipeline, state and API: fast mode (Day 2)

### P5.1 State, events and lock ⬜ 🤖
- [ ] `orchestrator/state.py`: incident JSON load/save (checkpoint)
- [ ] `orchestrator/events.py`: `XADD inc:{id}:events` with the event schema ([LLD §8](docs/03_LLD.md))
- [ ] `orchestrator/lock.py`: `SET NX PX` + heartbeat task every 15 s (Lua check-and-extend) + safe release

**Done when:** a test shows the heartbeat keeps a 90 s task's lock alive.

### P5.2 State machine and retry policy ⬜ 🤖
- [ ] `orchestrator/state_machine.py`: statuses + transitions ([LLD §7](docs/03_LLD.md))
- [ ] `retry_change()`: retry only when an input changes; invariant violations → `NEEDS_HUMAN`

**Done when:** unit tests cover pass, retry-with-change, no-change → NEEDS_HUMAN, and max 3 attempts.

### P5.3 One pipeline: fast brains ⬜ 🤖
- [ ] `core/pipeline.py: run_incident(incident, mode)` with shared Prepare / Execute / Verify
- [ ] Fast brains: deterministic findings, code ranking (collateral → size → staleness), template report
- [ ] Executor validation (eids exist, not protected)

**Done when:** `run_incident(mode="fast")` finishes S2 with a correct plan and pass.

### P5.4 FastAPI ⬜ 🤖
- [ ] `/healthz`, `/readyz` (pings FalkorDB)
- [ ] `GET /scenarios`, `POST /scenarios/{sid}/load` (409 if an incident is active)
- [ ] `POST /analyze` (fast mode, sync, sandbox deleted in `finally`)
- [ ] `POST /incidents` (Idempotency-Key, demo token for agent mode, rate limit, max 2 active)
- [ ] `GET /incidents/{id}`, `GET /incidents/{id}/events?after=`, `GET /incidents/{id}/graph`
- [ ] Error format + codes ([LLD §9](docs/03_LLD.md))

**Done when:** `/docs` works locally and `/analyze` on S2 returns the right answer.

### P5.5 Fast mode on all scenarios + review ④⑤ 👤
- [ ] 🤖 Fast mode correct on **S1–S8** vs answer keys (script)
- [ ] 👤 Review the tools, guards, pipeline and API. Run `/analyze` yourself.
- [ ] 👤 Commit

---

## P6: MCP server and agents: agent mode (Day 2)

### P6.1 MCP server (stdio) ⬜ 🤖
- [ ] `mcp_server/server.py`: FastMCP, registers `core.tools`, stdio only
- [ ] Profiles `full` / `readonly`
- [ ] Tool descriptions written for LLMs (short, clear, with limits)

**Done when:** a test client lists and calls tools over stdio.

### P6.2 LLM gateway ⬜ 🤖
- [ ] `agents/llm_gateway.py`: providers from env (base URL, key, model per agent)
- [ ] Pinned provider per agent run; `ProviderFailed` → restart the run on the next provider
- [ ] Timeout 30 s, 1 retry, capability flags (forced tool choice, JSON mode, parallel)
- [ ] Response cache in Redis (`llmcache:{hash}`)

**Done when:** a cached second call returns instantly, and failover works with a bad key.

### P6.3 Agent loop and schemas ⬜ 🤖
- [ ] `agents/schemas.py`: minimal LLM outputs + code enrichment ([LLD §6.3](docs/03_LLD.md))
- [ ] `agents/base.py`: MCP stdio client, allow-list → OpenAI tools, turn loop, `json.loads` guard, submit validation, reminder, max turns, degrade

**Done when:** a dummy agent completes a 2-tool task.

### P6.4 Investigator ⬜ 🤖
- [ ] Prompt from [06 §2](docs/06_Agent_Prompts_and_Schemas.md) + allow-list
- [ ] Post-check (paths are real chains) + enrichment

**Done when:** on S2 its findings match the answer key.

### P6.5 Planner ⬜ 🤖
- [ ] Prompt from [06 §3](docs/06_Agent_Prompts_and_Schemas.md) + allow-list incl. `simulate_cut`
- [ ] Acceptance rule: only simulation-verified cuts. Otherwise the code candidate + `planner_choice_rejected`.
- [ ] Enrichment (safe, max flow, witnesses, simulation numbers, suggestion)

**Done when:** on S8 it picks the zero-collateral cut.

### P6.6 Agent-mode pipeline and worker ⬜ 🤖
- [ ] `run_incident(mode="agent")` uses the LLM brains, checkpoints and events after each step
- [ ] `worker/main.py`: consume `jobs`, take the lock + heartbeat, start the MCP subprocess (restart on crash), janitor (sandbox TTL)
- [ ] Add `worker` + `api` services to `docker-compose.yml`

**Done when:** `POST /incidents` on S2 → status `DONE` with live events.

### P6.7 Review ⑥ + Day 2 gate 👤
- [ ] Watch one agent run event by event. Can you explain each tool choice?
- [ ] Day 2 gate: fast mode correct on S1–S8, agent mode `DONE` on S2
- [ ] Commit

---

## P7: Reporter and UI (Day 3)

### P7.1 Reporter ⬜ 🤖
- [ ] Prompt from [06 §4](docs/06_Agent_Prompts_and_Schemas.md) (remediation verbs, collateral, proof)
- [ ] Template fallback report (the same sections)

**Done when:** the S2 and S5 reports read well and contain only facts from the state.

### P7.2 Streamlit layout ⬜ 🤖
- [ ] Sidebar: scenario, load, src, mode, demo token, run, status
- [ ] Tabs: Attack graph · Agent timeline · Fix & proof · Report · Eval

**Done when:** you can start fast and agent runs from the UI.

### P7.3 Graph tab ⬜ 🤖
- [ ] Graph component (or the static-image fallback, R17)
- [ ] Sub-graph rules + colours ([LLD §15](docs/03_LLD.md))
- [ ] Redraw only on phase change, cached in `session_state`

**Done when:** S2 shows its paths, cut edges and protected edges clearly.

### P7.4 Timeline, Fix & proof, Report tabs ⬜ 🤖
- [ ] Timeline in `st.fragment(run_every=1s)` with short polls
- [ ] Fix & proof: cut edges + reasons + witnesses, ✅/❌ checks, collateral, suggestion (verified or not)
- [ ] Report tab (Markdown)

**Done when:** a live agent run updates smoothly without page flicker.

### P7.5 Review ⑦ 👤
- [ ] Walk through the UI on S2, S5 and S8
- [ ] Commit

---

## P8: Evaluation (Day 3)

### P8.1 Metrics and baselines ⬜ 🤖
- [ ] `eval/metrics.py`: blast-radius recall, path recall@20, precision, fix gap, residual, broken needs, collateral, verdict, latency, tokens
- [ ] Baselines: B-disable, B-betweenness, B-frequency (scored by networkx)
- [ ] `eval/baseline_json_llm.py` (same model as the Planner, JSON mode, invalid JSON = fail)
- [ ] `eval/bruteforce.py` (optimum, ≤ 25 candidates)

**Done when:** each baseline runs on S2.

### P8.2 Eval run ⬜ 🤖
- [ ] `eval/run_eval.py`: S1–S8 × (fast, agent, 4 baselines) → table; provider and model recorded per row
- [ ] Save `eval/results/latest.json` + Markdown table; `/eval/latest`; Eval tab
- [ ] Throttle for free-tier limits; use the response cache

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
| | | | | |

## Decision changes during build

| Date / time | Decision | Old → New | Why | Docs updated? |
|---|---|---|---|---|
| | | | | |

## Daily log

| Day | Planned gate | Result | Notes |
|---|---|---|---|
| Day 1 (Oct 15) | S1–S3 load, queries match, min-cut = networkx | | |
| Day 2 (Oct 16) | Fast mode S1–S8 correct, agent mode DONE on S2 | | |
| Day 3 (Oct 17) | UI works, eval table produced, deploy tried | | |
| Day 4 (Oct 18) | Submitted by 6 PM IST | | |
