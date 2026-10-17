# VERIFY_RESULTS

Results of checking the real FalkorDB system (CLAUDE.md rule 6).

| Item | Result | Date |
|---|---|---|
| FalkorDB module version (`MODULE LIST`, graph `ver`) | 60002 (6.0.2) | 2026-10-15 |
| Redis server version (`INFO server`, `redis_version`) | 8.10.2 | 2026-10-15 |
| Auth enforced (`ping` without password → NOAUTH) | yes | 2026-10-15 |
| Python client connect (`spikes/ping_falkordb.py`) | ok | 2026-10-15 |

## Image pin (P4.7 step 0)

2026-10-15. After the WSL crash lost the image cache, `docker-compose.yml` (`falkordb/falkordb:latest`) re-pulled a different build: graph module ver **42201**, digest `sha256:b0bdcac2...`, not the 60002 (6.0.2) everything was verified on.

| Item | Result |
|---|---|
| Fix | `docker-compose.yml` pins `falkordb/falkordb:6.0.2` (digest `sha256:107455e8...`). Container recreated; the `falkordb-data` volume kept all data (baselines s1, s2, s3 still listed) |
| `MODULE LIST` after recreate | `graph` ver **60002**, `vectorset` ver 1; container healthy |
| Toolbox `pytest -q` on 6.0.2 | 165 passed |

## P1.3 feature checks

Script: `spikes/verify_falkordb.py` (one toy graph per check). FalkorDB 6.0.2, run 2026-10-15.
| Check | Result | Detail |
|---|---|---|
| B1 | PASS | rows=[[5.0]], expected maxFlow=5 |
| B1b | PASS | rows=[[0.0]] (row) |
| B2a | PASS | eids=['e1', 'e2', 'e5', 'e3', 'e4'] flows=[3.0, 2.0, 1.0, 2.0, 3.0] caps=[3, 2, 1, 2, 3] ends=['AB', 'AC', 'BC', 'BD', 'CD'] (flow<=cap per position: True) |
| B2b | INFO | rows=[[0.0]] (row with maxFlow) |
| B2c | PASS | rows=[[2.0]], expected 2 (also covers B7) |
| B3 | PASS | a) rows=[[2, ['r0', 'r1']], [2, ['r2', 'r3']]]; c) no-path rows=[] |
| B4 | INFO | bfs(a,5,null)=[[['B']]]; bfs(a,5,'R')=[[['B']]]; follows direction (no C): True |
| B5 | PASS | g.copy(); counts match=True; source unchanged after copy delete=True; second copy onto existing: ERROR destination key already exists |
| B6 | PASS | rows=[['R', ['eid'], OrderedDict({'eid': ['RANGE']}), OrderedDict({'eid': OrderedDict()}), 'english', [], 'RELATIONSHIP', '[Indexing] 0/1: UNDER CONSTRUCTION', OrderedDict({'fields': [OrderedDict({'name': 'range:eid'}), OrderedDict({'name': 'range:eid:numeric:arr'}), OrderedDict({'name': 'range:eid:string:arr'}), OrderedDict({'name': 'NONE_INDEXABLE_FIELDS'})]})]] |
| E1 | PASS | rows=[[3, ['T1', 'T2', 'T1']]], expected length 3 |
| E2 | PASS | r.cap values=[None, 99, None, 99], expected exactly 2 x 99 (one path) |
| E3 | PASS | scores={'A': 0.0, 'B': 1.0, 'C': 0.0} |

Observations and design impact (vs `docs/03_LLD.md`):

- **B1** `algo.maxFlow` exists, returns 5.0 on the known graph (value is a float). Design impact: none. **Note:** P1.3 tested one relationship type only; `algo.maxFlow` requires exactly one type (found in P3.1), so all edges are stored as `:ATTACK` with the real type in `r.kind`.
- **B1b** maxFlow respects direction: the only edge D->A gives a row with `maxFlow = 0.0` for A->D. Design impact: none.
- **B2a** Yielded `edges` carry properties (`e.eid`, `e.cap` work). `edgeFlows` is positionally aligned with `edges` (flow <= cap at every position, flows consistent at each node). Order is NOT the creation order (e1,e2,e5,e3,e4), so map by position, not by eid order. Design impact: none.
- **B2b** Zero flow (A and D not connected): one row with `maxFlow = 0.0`, not "no row". Design impact: none.
- **B2c** Multiple `targetNodes` via `MATCH ... WITH s, collect(t) AS ts CALL algo.maxFlow(...)` works, returns 2.0. This also covers **B7**. Design impact: none.
- **B3** `algo.SPpaths` accepts `relTypes:['R']`, `relDirection:'outgoing'`, `weightProp:'w'`, `pathCount:2`, `maxLen:8`; yields `path`, `pathWeight`; returns 2 paths of weight 2; `r.eid` readable from `relationships(path)`. No path (D->A): **no row** (empty result), no error. Design impact: none (LLD line 311 already allows for "no row").
- **B4** `algo.bfs(a, 5, null)` and `algo.bfs(a, 5, 'R')` both return `[B]` only: bfs **follows edge direction** (C->A not followed). Design impact: none.
- **B5** `graph.copy("dest")` exists in the Python client. Counts match; deleting an edge in the copy leaves the source unchanged. Copying onto an existing destination errors: `destination key already exists`. Design impact: none, but the Executor must delete or reset an existing sandbox before copying (LLD: "reuse / reset it").
- **B6** `CREATE INDEX FOR ()-[r:R]-() ON (r.eid)` works; `db.indexes()` lists it as a RELATIONSHIP index (status shown as "UNDER CONSTRUCTION" right after creation, so it builds asynchronously). Design impact: none.
- **E1** Variable-length multi-type path `[:T1|T2*1..6]` works, `length(p) = 3`, types `[T1,T2,T1]`. Design impact: none.
- **E2** `UNWIND relationships(path) AS r SET r.cap = 99` after `algo.SPpaths` works: exactly the 2 path edges changed. Design impact: none.
- **E3** `algo.betweenness` with `{relationshipTypes, samplingSize, samplingSeed}` works: B = 1.0, A = C = 0.0. Design impact: none.

Note: the first run failed only because the script's loader passed a parameter map as inline relationship properties (`CREATE ()-[:R $p]->()`), which FalkorDB rejects with `Encountered unhandled type in inlined properties`. The script now uses `SET r += $p`. Worth remembering for the real loader.

## P1.5 MCP + Streamlit checks

Run in the toolbox container (same image the worker will use), 2026-10-15. Scripts: `spikes/mcp_hello_server.py`, `spikes/mcp_hello_client.py`.

| Item | Result |
|---|---|
| MCP server class / import path | `from mcp.server.mcpserver import MCPServer` (mcp 2.3.0). `mcp.server.fastmcp` no longer exists in 2.x: it raises `ModuleNotFoundError` ("FastMCP is now MCPServer"). Tool via `@mcp.tool()`, start via `mcp.run(transport="stdio")`. |
| MCP client API | `from mcp import ClientSession, StdioServerParameters, stdio_client`. `async with stdio_client(params) as (read, write)`, `async with ClientSession(read, write) as s`, `await s.initialize()`, `await s.list_tools()`, `await s.call_tool(name, args)` |
| Tools listed | `['ping_graph']` |
| Call result | `is_error: False`, `structured_content: {'ok': 1}`, text `{"ok": 1}`. The client exited cleanly and the server subprocess was stopped. |
| Streamlit version | 1.66.0 |
| `st.fragment` `run_every` | yes: `st.fragment(func=None, *, run_every=None, parallel=False, key=None)`; `run_every` accepts int, float, timedelta or str |

- **MCP stdio:** Design impact: none for the architecture (stdio subprocess works). Two details to remember: (1) the SDK name is `MCPServer`, not `FastMCP`, so the docs' "FastMCP" wording is outdated (HLD C4 and §190, LLD line 35, task.md lines 108 and 325) (fixed in the P6.1 docs pass: only this file still names it, as history); (2) the subprocess gets only a small default environment, so the worker must pass `FALKORDB_HOST`, `FALKORDB_PORT` and `FALKORDB_PASSWORD` explicitly through `StdioServerParameters(env=...)`.
- **Streamlit:** Design impact: none. The `st.fragment(run_every=...)` live timeline in the LLD works as written.

## P3.1 query checks

Run in the toolbox on S2 (FalkorDB 6.0.2), 2026-10-15. Tests: `tests/test_queries.py`.

| Item | Result |
|---|---|
| Q1 hop bound as a `$param` (`*1..$h`) | **fails**: `Encountered unhandled type in inlined properties`. A literal `6` works. Keep the bound formatted from `MAX_HOPS`. |
| Q8 / Q11: `MATCH (n:BusinessNeed) MATCH (a:Asset {id: n.principal_id})` | **fails**: `Variable n not found`. Fix: `WITH n` between the two MATCH clauses (the same query then works). |
| Q6 `algo.maxFlow` with several `relationshipTypes` (or none, `[]`, `null`) | **fails**: `'relationshipTypes' is required and must contain exactly one type`. P1.3 only tested one type, so the LLD's 8-type list was never checked. **Decision (option A): every edge is `:ATTACK`, the real type is `r.kind`.** Verified live on S2: `algo.maxFlow` with `['ATTACK']` = 2.0, flow only on the 11 attack-path eids. |
| Q10 `APPLY_FIX` on a missing or protected edge | returns one row `[[0]]` (not an empty result) |
| Q2, Q3, Q4, Q5, Q7, Q9, Q12, Q8 step 1 | run as written; sanity values match S2 |

## P3.4 min-cut checks

FalkorDB 6.0.2, 2026-10-15. Tests: `tests/test_mincut.py`.

| Item | Result |
|---|---|
| `algo.maxFlow` on a graph with two edges between the same pair under one relationship type | **fails**: `relationship type 'ATTACK' contains multi-edges; maxFlow requires a simple adjacency matrix`. Design impact: at most one edge per `(src, dst)`; the loader rejects duplicates (LLD §2.3). Pinned by `test_falkordb_limit_maxflow_rejects_multi_edges`. |

## P5.5 finding: `algo.bfs` with no outgoing edge

Found on S6 (`scripts/check_fast_mode.py`), FalkorDB 6.0.2, 2026-10-15. Test: `tests/test_verify.py::test_source_with_no_outgoing_edge_reaches_nothing`.

| Item | Result |
|---|---|
| `algo.bfs(s, 5, null) YIELD nodes` on a node with **no outgoing edge** (even if edges point to it) | **no row** (empty result set), not a row with an empty list. A node with an outgoing edge returns one row. |

Cause: S6's source-side cut removes both of `user:kai`'s only edges, so Q9 on the sandbox returned nothing and `[0][0]` raised `IndexError` (the pipeline ended FAILED).
Impact: `verify.reached_jewels()` treats no row as `[]`. Q12 (`REACH_SETS`) then simply omits a user left with no outgoing edge, and collateral correctly counts all of that user's access as lost.

## P1.4 LLM smoke test (B8)

Run in the toolbox with `spikes/llm_smoke.py`, 2026-10-15 (raw `openai` SDK, temperature 0, 30 s timeout, one attempt per call, 3 s between calls). Mistral: no key, skipped.
Checks: 1 single tool call (exact args) · 2 two parallel calls in one response (n = tool_calls returned) · 3 `submit_fix_plan` with forced `tool_choice` (Pydantic-validated) · 4 JSON mode · 5 Markdown `## A ## B ## C`.

**Groq: untested.** `models.list()` works, but every chat call returns `403 model_permission_blocked_project` ("blocked at the project level") for `openai/gpt-oss-120b`, `openai/gpt-oss-20b` and `qwen/qwen3.8-27b`. The models must be enabled in the Groq console (project limits). Groq now lists only 4 chat models (also `allam-2-7b`, context 4096: too small).

Results (best run per model; "rate limited" = upstream 429, not a model failure):

| provider | model | 1 tool | 2 parallel (n) | 3 submit | 4 json | 5 md | forced tool_choice | avg ms |
|---|---|---|---|---|---|---|---|---|
| openrouter | nvidia/nemotron-3-super-120b-a12b:free | PASS | 2 | PASS | true | PASS | true | 3613 / 4319 / 4021 (3 runs, identical results) |
| openrouter | nvidia/nemotron-3-ultra-550b-a55b:free | 503 overloaded | 2 | PASS | true | PASS | true | 7501 |
| openrouter | poolside/laguna-s-2.1:free | PASS (run 2), FAIL no tool call (run 1) | 2 (run 3) | PASS (run 2) | rate limited | PASS (run 1) | true | 1337 to 2160 |
| openrouter | poolside/laguna-xs-2.1:free | rate limited | 1 | rate limited | rate limited | PASS | true | 2867 |
| openrouter | google/gemma-4-31b-it:free | rate limited (every run) | | | | | | |
| groq | openai/gpt-oss-120b | PASS | 1 | PASS | true | PASS | true | 716 |
| groq | openai/gpt-oss-20b | PASS | 1 | PASS | true | PASS | true | 485 |

**Chosen (OpenRouter):** Investigator, Planner and Reporter = `nvidia/nemotron-3-super-120b-a12b:free`. It is the only model that passed all five checks, in all three runs, with parallel = 2. The faster Laguna models passed single checks but were upstream-throttled in most runs, so they are not reliable enough. **Done when (P1.4) holds:** one provider (OpenRouter) passes all 3 calls.

**Capability flags** (what the P6.2 gateway reads):

| provider | forced_tool_choice | parallel | json_mode |
|---|---|---|---|
| openrouter (nemotron-3-super) | true | true (2 calls in one response) | true |
| groq (gpt-oss-120b / 20b) | true | **false** (1 call per response) | true |
| mistral | no key | | |

**Free-tier behaviour seen (OpenRouter):** no `x-ratelimit-*` headers on any response (success or 429), so the gateway cannot read the remaining quota. 429s are **upstream per-model throttles** ("temporarily rate-limited upstream", from the model's host such as Google AI Studio or Poolside), not account limits, and they clear within seconds to minutes. A 200 response can carry an error body with **no `choices`** (`503 provider_overloaded` seen on nemotron-ultra): the gateway must treat `choices` empty/None as a provider failure. Per-call cost on nemotron-super: about 300-600 tokens, 2.5-6 s. Design impact: the provider chain plus the response cache (R15) matter; `nemotron-3-super` is the single model all three agents depend on.

**OpenRouter `GET /key` (P6.2, 2026-10-15; no key in the output):** `is_free_tier: true`, `limit: null` (no credit cap), `usage: 0`, `byok_usage: 0`. **`free_model_daily_requests: {used 0, limit 50, remaining 50}`**: a free-tier account gets **50 requests per day across all `:free` models**. `rate_limit` is `{requests: -1, interval: "10s"}` and marked deprecated. No `x-ratelimit-*` or credit headers. Design impact: 50 free requests per day is the real budget for live agent runs, evals and the demo (R15). The response cache is what keeps the demo and eval repeatable, and the live tests use only 2 calls.

## P6.2 LLM transport (history)

2026-10-15. The gateway first called the provider through hosted Portkey. Its current docs show Model Catalog slugs (`@openrouter`) and no live check was possible without an account: dropped: no Portkey account; direct calls.

Direct OpenRouter check (live tests in `tests/test_llm_gateway.py`, header `X-Title: BreachPath`): 1st call 3433 ms (tool call returned), the identical 2nd call 0 ms (`cached=True`), and a bad `OPENROUTER_API_KEY` gives `LLMUnavailable` ("provider rejected the call: AuthenticationError 401") without a retry.

**Groq limits (response headers, 2026-10-15):** `x-ratelimit-limit-requests: 1000` (per day), `x-ratelimit-limit-tokens: 8000` (per minute; resets in seconds). **Chosen Groq models:** Investigator and Planner `openai/gpt-oss-120b` (about 0.7 s per call), Reporter `openai/gpt-oss-120b` since P7.1 (the 20B ignored prompt rules the guard can't check; about 0.5 s was the 20B's latency). Because Groq does not return parallel calls, an agent makes one tool call per turn (the S2 dummy agent needed 5 turns, about 4.5 s in all, against 3 turns and about 26 s on OpenRouter): watch `max_turns` for the Planner (one `simulate_cut` per candidate) and the 8000 tokens/minute limit (one dummy run used about 5400 tokens). **Provider order (P6.2 follow-up):** `LLM_PROVIDERS=groq,openrouter`; a run that finds its provider unavailable restarts on the next one (D18).

## P9.1 / P9.2 / P9.3 Hardening, reliability and latency (2026-10-16, measured, nothing estimated)

**Port check (P9.1).** `docker compose -f docker-compose.yml up -d --build` (NO dev file), then `python scripts/port_check.py` from the WSL host and from Windows (plain socket connects):

| port | service | expected | answers |
|---|---|---|---|
| 8501 | ui | open | yes |
| 6379 | falkordb | closed | no |
| 8000 | api | closed | no |
| 3300 | falkordb browser (dev only) | closed | no |

`PASS: only the UI port answers`. Only `ui` has `ports` in `docker-compose.yml` (a unit test, `tests/test_compose.py`, keeps it so). The other ports exist only through `docker-compose.dev.yml`, bound to 127.0.0.1.

**Secrets in logs (P9.1).** One fast and one agent incident (S2) through the real API, then `docker compose logs --no-color <service> | grep -c` for `gsk_`, `sk-or-` and the `DEMO_TOKEN` value (read inside the api container, never printed): falkordb 0/0/0, api 0/0/0, worker 0/0/0, ui 0/0/0 (31 / 254 / 78 / 9 log lines).
**Already enforced:** FalkorDB password = `REDIS_ARGS --requirepass` in `docker-compose.yml` + `core/graph/client.py` (`password=`); read-only queries = `ro_query` (`GRAPH.RO_QUERY`) for every read and for `run_readonly_cypher`; timeouts = `QUERY_TIMEOUT_MS` on every query (`core/graph/client.py`). Fast mode `/analyze` now has the per-IP rate limit too (`api/checks.rate_limit`, own counter, same `RATE_LIMIT_PER_MIN`).

**Fresh clone (AC-8, P9.2).** `git clone` of the repo into `/tmp/bp-fresh` (the committed HEAD `e40723e` plus the 13 uncommitted files of this task copied over, because this task is not committed yet), `cp env.example .env`, only `FALKORDB_PASSWORD` and `DEMO_TOKEN` set (both LLM keys empty), `UI_PORT=8601 docker compose -p bpfresh up -d --build`: 4/4 services `healthy` after about 25 s (build 1 min 50 s), UI `/_stcore/health` 200. In the api container: S2 loaded (200), `/analyze` 200 DONE in 0.21 s, agent incident `DONE`, **`degraded=True`** (`investigator / planner / reporter: no provider configured`, 0 LLM calls) in 6.5 s (AC-6, "remove the API keys"). Then `docker compose -p bpfresh down -v` and `rm -rf /tmp/bp-fresh`: no container, volume or directory left.

**Chaos (AC-6, P9.2).** S2 agent incident, the worker crashed while the status was `PLANNING`:
- `docker kill breachpath-worker-1` (twice): the container stays `exited (137)`. Docker treats a manual kill/stop as an explicit stop, and `restart: unless-stopped` does not bring it back. `docker start` then resumed the pending job from `PLANNING` and the incident was DONE (twice).
- A real crash (the kernel OOM-kills the worker: `docker update --memory 60m`, then the limit lifted): `t=1.6 s` PLANNING, `t=2.3 s` container back (RestartCount 9 → 10, health `starting`), `t=65.3 s` **DONE, degraded=False**, jobs pending 0. Worker log: `locked by another holder, left un-acked` every 2 s while the dead worker's 60 s lock ran out, then `running from status PLANNING`, `DONE in 4478 ms`, `resumed 5d0de0fe`. The resume takes about one lock TTL because the crashed worker's lock lives 60 s.
- Bugs found and fixed by it: the restarted worker read its pending job ONCE and gave up if the lock was still held (the incident would have stayed stuck until the next restart): now `resume_pending` retries up to `LOCK_TTL_MS + 5 s`. And the worker crashed with `NOGROUP` when the `jobs` stream was deleted: now it creates the group again.

**Found by the latency run:** kept agent sandboxes (24 h) count toward `MAX_GRAPHS = 20`: after about 8 agent incidents EVERY new incident ended `FAILED: LIMIT_EXCEEDED: 20 graphs exist`. Fix: before each job the worker keeps the newest `MAX_KEPT_SANDBOXES = 6` and deletes older ones (`worker.trim_sandboxes`).

### P9.3 latency (NFR-L1 .. L5), `python -m scripts.latency --n 5 --fresh`

Local Docker (WSL2) on a laptop, 1 worker. Raw numbers: p50 / p95 over n samples.

| ID | Requirement | Target | Measured | Verdict |
|---|---|---|---|---|
| L1 | one graph tool call, 177-node graph (`t:s6:baseline`), 8 read tools x 30 calls | p95 < 200 ms | **p95 12.2 ms** (p50 2.9 ms, max 22.8 ms; slowest tool `compute_min_cut` p95 13.9 ms) | met |
| L2 | fast `/analyze` end to end | p95 < 1 s | **p95 111.8 ms** (p50 104.6 ms) over S1-S8 x 5 in-process; real HTTP to the api container, S2 x 5: p95 111.3 ms. (An earlier run with the worker busy: p95 192.6 ms.) | met |
| L3 | agent: time to the first event | < 1 s | **p50 64.7 ms, p95 193 ms** (POST until the first event is readable; the UI adds up to its 1 s poll). Before the "worker picked up the incident" event it was p50 1.4 s: the first agent event waits for the MCP subprocess start | met |
| L4 | agent: full incident end to end, p50 | < 45 s | **cached LLM: p50 3.8 s** (5 runs, every LLM answer replayed, p95 7.0 s). **Fresh (cache OFF, real LLM calls): 1 run, 104.8 s**, status DONE but `degraded` (reporter fell back to the template), 11 LLM calls, Groq rate-limit retries (1 s + 11 s + 12 s) | **not met with real LLM calls** (one run, rate-limited; the cached number only measures the pipeline) |
| L5 | `POST /incidents` -> 202 | < 100 ms | **p50 11.7 ms, p95 17.9 ms** | met |
| UI | first paint | (none) | not measurable without a browser: the HTML shell answers in 3.6 ms (p50), and the first script run of `ui/app.py` (AppTest: everything the server computes before the first render) takes p50 334 ms (cold first run 3.0 s) | informational |

**README-ready:**

| What | Target | Measured |
|---|---|---|
| One graph tool call (177 nodes), p95 | < 200 ms | 12 ms |
| Fast mode `/analyze`, p95 | < 1 s | 112 ms |
| Agent mode, time to first event, p50 | < 1 s | 65 ms |
| Agent mode, full incident, p50 | < 45 s | 3.8 s with cached LLM answers; 105 s for one run with real, rate-limited free-tier LLM calls |
| `POST /incidents` accept, p50 | < 100 ms | 12 ms |

The agent pipeline itself (graph work, sandbox, proof) is fast; the end-to-end time of a real agent run is the free-tier LLM latency (Groq 8000 tokens/minute, OpenRouter 50 requests/day), which is why the response cache exists.

## P9.6 Quality and efficiency (2026-10-17, measured, nothing estimated)

**LLM budget:** 12 real calls were allowed. Used (counted by the scripts): 8 in the Reporter replay + 3 in the S7 run = 11. The Groq log also shows 2 rate-limit retries (2 s, 19 s) inside the Reporter replay that were not counted as calls.

**Step 1: the Reporter repair.** `reporter.write(state)` on the saved states of 4 finished incidents (Redis `inc:<id>`, the degraded step of the Reporter removed so the slice is the one the Reporter originally saw), twice: once with `_repair` switched off (the old behaviour) and once with the new code. Today's providers: Groq first. The second pass reads the first pass's answers from the LLM cache, so both passes see the same model text.

| incident | scenario | instruction | saved report source | old code, replayed today | new code, replayed today | real calls | problems of the model's attempt 1 |
|---|---|---|---|---|---|---|---|
| d002ccac | S8 | no | llm | llm | llm | 1 | missing sentence about alternative e:s8:07; attempt 2 passes |
| 1f492a18 | S8 | "Do not change anything between user:max and the reports database." | llm | llm | llm | 2 | missing sentence about e:s8:07; attempt 2 passes |
| b5d524f9 | S8 | legacy-role instruction | **template** | **template** (Groq attempt 2 hit a 429, OpenRouter answered without the Next steps heading: not repairable; 143 s) | llm (Groq attempt 2 passed; 2.6 s) | 5 (4 old pass, 1 new pass) | missing sentences about e:s8:05 and e:s8:07 |
| d941fed7 | S2 | no | **template** | llm (cached Groq answers from later S2 runs) | llm | 0 | missing sentence about e:s2:08, e:s2:11; attempt 2 passes |

Fell back to the template: **saved 2 of 4, old code replayed today 1 of 4, new code replayed today 0 of 4.** **Honest reading: the repair did not fire in any of these live replays** (`repairs=[]` every time): in all four the model's second answer was good, or the failure was a different, unrepairable one (OpenRouter's missing heading). The drop from 1 to 0 on b5d524f9 is Groq answering instead of rate-limiting, not the repair. Time per state with the answers cached: 12-17 ms; with real calls 2-6 s (143 s with the rate limit).

Because the repair did not run live, it was applied **offline to the 4 real first answers** (read from the LLM cache, no call): each had 1 or 2 missing-alternative problems, `_repair` added the sentence(s), and the guard passed afterwards in all 4. Example, S8 (final wording): "The alternative e:s8:07 was not chosen: it blocks the attack, would make 12 (user, resource) pair(s) lose access (weighted 12) against 0 for the chosen cut, and has size 1 and 150 stale days in total." Unit tests (`tests/test_reporter.py`, 8 new): missing alternative sentence repaired; missing instruction mention repaired (honoured, no change, not honoured); both together; an invented eid next to a missing sentence = template with the reason; a pronoun or a host action next to a missing sentence = no repair; guard already passing = no repair, `repairs` empty; a repair that still fails the guard = template.

**Step 2 (`reasoning_effort`): skipped.** No probe call was made: the budget was used by Steps 1 and 4, and the step also needs 2 fresh runs. Nothing about Groq's acceptance of `reasoning_effort` is known yet.

**Step 3: free evaluation numbers.** `python -m eval.run_eval --methods fast,disable,betweenness,frequency`, all 8 scenarios, 0 LLM calls, 2026-10-17 06:56 UTC, HEAD 8359e6d plus the uncommitted work of the day (the hash cannot show that). Every number is the same as in the 2026-10-16 run (fast 7/7 safe, disable 4/7 and 6 needs broken, betweenness 6/7 and weighted collateral 12, frequency 4/7 and 4 broken). The agent and B-LLM-JSON cells were kept from the 2026-10-16 09:37 UTC run at commit 55daa16; they were not refreshed (no LLM budget left). `latest.md` now says per method when and at which commit it was run.

**Step 4: S7, the Investigator, one fresh run** (`LLM_CACHE=off`, `LLM_MAX_CALLS_PER_INCIDENT=4`, `pytest -s -m live tests/test_investigator.py::test_live_s7_beyond_the_hop_bound_has_no_paths`, no prompt change): incident e9a5dfaf, provider groq (`openai/gpt-oss-120b`), 3 turns, 3 calls, none cached, tokens 2877 in / 208 out, not degraded. Finding event: **`llm_jewels` = [], `code_jewels` = ['db:s7-billing']**, `valid_paths` 0, notes "The compromised identity user:nia cannot reach any crown-jewel databases within 6 hops. LLM missed: db:s7-billing." The model missed the jewel; the code BFS found it.

**Step 5: non-root containers.** `Dockerfile`: `useradd --uid 10001 app`, `COPY --chown=app:app`, `USER app`; `docker-compose.dev.yml`: `toolbox` gets `user: root`. `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build` took 22 s (layers cached); 40 s later api, worker, ui and falkordb were all `healthy`. `id -u`: api 10001, worker 10001, ui 10001, falkordb 0 (unchanged). One S2 agent incident through the API and the worker (ed6be824): DONE, not degraded, 7.0 s, 20 of 20 LLM answers from the cache (0 real calls), report source `llm`. No volume and no FalkorDB service line was changed.
