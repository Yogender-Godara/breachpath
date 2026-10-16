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
