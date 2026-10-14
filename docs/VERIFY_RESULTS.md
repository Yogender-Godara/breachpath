# VERIFY_RESULTS

Results of checking the real FalkorDB system (CLAUDE.md rule 6).

| Item | Result | Date |
|---|---|---|
| FalkorDB module version (`MODULE LIST`, graph `ver`) | 60002 (6.0.2) | 2026-10-15 |
| Redis server version (`INFO server`, `redis_version`) | 8.10.2 | 2026-10-15 |
| Auth enforced (`ping` without password → NOAUTH) | yes | 2026-10-15 |
| Python client connect (`spikes/ping_falkordb.py`) | ok | 2026-10-15 |

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

- **B1** `algo.maxFlow` exists, returns 5.0 on the known graph (value is a float). Design impact: none.
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

- **MCP stdio:** Design impact: none for the architecture (stdio subprocess works). Two details to remember: (1) the SDK name is `MCPServer`, not `FastMCP`, so the docs' "FastMCP" wording is outdated (HLD C4 and §190, LLD line 35, task.md lines 108 and 325) and should be changed in a docs pass; (2) the subprocess gets only a small default environment, so the worker must pass `FALKORDB_HOST`, `FALKORDB_PORT` and `FALKORDB_PASSWORD` explicitly through `StdioServerParameters(env=...)`.
- **Streamlit:** Design impact: none. The `st.fragment(run_every=...)` live timeline in the LLD works as written.
