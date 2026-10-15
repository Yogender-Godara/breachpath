# 04: Design and Flow Diagrams

> **Status:** pre-hackathon diagrams (allowed by Rule 8). All diagrams are Mermaid.

| # | Diagram | Type |
|---|---|---|
| 1 | System context | flowchart |
| 2 | Container view | flowchart |
| 3 | Agent mode, end to end | sequence |
| 4 | Fast mode (no LLM) | sequence |
| 5 | Incident state machine | state |
| 6 | LLM agent tool loop (MCP) | flowchart |
| 7 | Min-cut computation | flowchart |
| 8 | Fix → verify → retry loop | flowchart |
| 9 | Example S2: before and after the fix | flowchart |
| 10 | Data generation and load | flowchart |
| 11 | Failure and degradation paths | flowchart |
| 12 | Worker crash recovery | sequence |
| 13 | Evaluation pipeline | flowchart |

---

## 1. System context

```mermaid
flowchart LR
    R(("Security responder"))
    J(("Judge / viewer"))
    X(("External AI assistant<br/>Claude Desktop / Code"))
    BP["BreachPath<br/>multi-agent attack-path<br/>investigation + fix + proof"]
    F[("FalkorDB")]
    C["LLM providers<br/>Groq / OpenRouter / Mistral"]

    R -- "compromised identity" --> BP
    J -- "runs scenarios, watches agents" --> BP
    X -. "MCP tools" .-> BP
    BP -- "Cypher + graph algorithms" --> F
    BP -- "tool-use reasoning, reports" --> C
    BP -- "paths, fix, proof, report" --> R
```

---

## 2. Container view

```mermaid
flowchart TB
    UI["ui: Streamlit :8501<br/>(only public port)"]
    API["api: FastAPI :8000 (internal)<br/>run_incident(fast) via core"]
    subgraph WKC["worker container"]
        WK["worker: run_incident(agent)<br/>Orchestrator + agents"]
        MCP["MCP server<br/>(stdio subprocess)"]
    end
    CORE["core library<br/>graph tools + guards"]
    subgraph FDB["falkordb :6379"]
        BASE[("t:acme:baseline")]
        SBX[("t:acme:sbx:inc-123")]
        JOBS[["stream: jobs"]]
        ST[["inc:123 state<br/>inc:123:events"]]
    end
    CL["LLM gateway<br/>Groq → OpenRouter → Mistral"]

    UI -->|"REST (short poll from a fragment)"| API
    API -->|"XADD job"| JOBS
    API -->|"read state/events"| ST
    API -->|"fast mode (direct import)"| CORE
    WK -->|"XREADGROUP"| JOBS
    WK -->|"checkpoint + events + lock heartbeat"| ST
    WK -->|"MCP over stdio"| MCP
    MCP --> CORE
    WK -->|"messages + tools"| CL
    CORE -->|"RO queries"| BASE
    CORE -->|"COPY / protect / fix / verify"| SBX
```

---

## 3. Agent mode, end to end

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI
    participant API
    participant Q as Redis (jobs, state, events)
    participant W as Worker (Orchestrator)
    participant IA as Investigator (LLM)
    participant PA as Planner (LLM)
    participant EX as Executor (code)
    participant VE as Verifier (code)
    participant RE as Reporter (LLM)
    participant M as MCP server (stdio subprocess → core)
    participant G as FalkorDB

    U->>UI: Start incident (src = user:ravi)
    UI->>API: POST /v1/incidents (Idempotency-Key)
    API->>Q: save state QUEUED + XADD jobs
    API-->>UI: 202 {incident_id}
    loop every ~0.5 s (long-poll)
        UI->>API: GET /events?after=last_id
        API->>Q: XREAD BLOCK
        API-->>UI: new events
    end

    W->>Q: XREADGROUP jobs
    W->>IA: investigate(src, baseline)
    IA->>M: get_blast_radius
    M->>G: Q1 (RO)
    IA->>M: find_attack_paths x2 (parallel)
    M->>G: Q2 (RO)
    IA->>M: edge_path_frequency
    M->>G: Q4 (RO)
    IA-->>W: Findings
    W->>Q: checkpoint + events

    W->>EX: prepare()
    EX->>M: create_sandbox, protect_business_paths
    M->>G: GRAPH.COPY + Q8
    W->>Q: checkpoint

    W->>PA: plan(findings) on sandbox
    PA->>M: compute_min_cut
    M->>G: Q6 algo.maxFlow + Q7 edges
    M-->>PA: candidates (source/sink side) + witness paths, max_flow, safe
    par simulate each candidate
        PA->>M: simulate_cut(source_side)
        M->>G: COPY sim, apply, Q6 + Q9 + Q11 + Q12
    and
        PA->>M: simulate_cut(sink_side)
        M->>G: COPY sim, apply, Q6 + Q9 + Q11 + Q12
    end
    M-->>PA: blocks_attack, needs_ok, collateral per candidate
    Note over PA: chooses lowest collateral<br/>(may propose and simulate its own cut)
    PA-->>W: FixPlan (chosen cut + reason per eid)
    W->>Q: checkpoint

    W->>EX: execute(plan)
    EX->>M: apply_fix(sandbox, eids)
    M->>G: Q10 DELETE edges
    W->>VE: verify()
    VE->>M: verify_fix
    M->>G: Q6 maxFlow + Q9 algo.bfs + Q11 needs
    VE-->>W: Verdict pass
    W->>RE: report(state)
    RE-->>W: Markdown report
    W->>Q: status DONE + XACK
    UI->>API: GET /incidents/{id} and /graph
    API-->>UI: report, paths, cut edges, proof
```

---

## 4. Fast mode (no LLM, about 1–2 s): the same `run_incident` pipeline, `mode=fast`

```mermaid
sequenceDiagram
    autonumber
    participant UI
    participant API as API (run_incident fast)
    participant M as core library (direct calls)
    participant G as FalkorDB
    UI->>API: POST /v1/analyze {tenant, src}
    API->>M: find_attack_paths
    M->>G: Q2
    API->>M: create_sandbox + protect_business_paths
    M->>G: GRAPH.COPY + Q8
    API->>M: compute_min_cut
    M->>G: Q6 + Q7
    API->>M: simulate_cut x2 (source/sink side)
    M->>G: COPY sim + Q6 + Q9 + Q11 + Q12
    Note over API: code ranking: collateral → size → staleness
    API->>M: apply_fix (chosen cut)
    M->>G: Q10
    API->>M: verify_fix
    M->>G: Q6 + Q9 + Q11
    API-->>UI: {findings, plan, verdict, timings_ms}
```

---

## 5. Incident state machine

```mermaid
stateDiagram-v2
    [*] --> QUEUED
    QUEUED --> INVESTIGATING
    INVESTIGATING --> DONE_NO_RISK: no crown jewel reachable
    INVESTIGATING --> PREPARING: paths found
    PREPARING --> PLANNING: sandbox ready, business edges protected
    PLANNING --> NEEDS_HUMAN: max_flow >= PROTECT_CAP (no safe fix)
    PLANNING --> EXECUTING: safe plan
    EXECUTING --> PLANNING: plan invalid (missing or protected eid)
    EXECUTING --> VERIFYING
    VERIFYING --> REPORTING: pass
    VERIFYING --> PREPARING: fail, an input changed, attempt < 3
    VERIFYING --> NEEDS_HUMAN: fail, nothing to change, or attempt = 3
    DONE_NO_RISK --> REPORTING
    NEEDS_HUMAN --> REPORTING
    REPORTING --> DONE
    DONE --> [*]
    INVESTIGATING --> FAILED: unrecoverable error
    FAILED --> [*]
```

---

## 6. LLM agent tool loop (MCP)

```mermaid
flowchart TD
    A["Start agent<br/>(role, allow-list, state slice)"] --> B["List MCP tools<br/>filter by allow-list<br/>+ submit_* tool"]
    B --> C["gateway.chat<br/>cache → Groq → OpenRouter → Mistral<br/>tool_choice auto, temperature 0"]
    C --> D{"stop_reason"}
    D -- "tool_use" --> E["Run all tool calls in parallel<br/>via MCP"]
    E --> F["Emit events (tool, latency, ok)"]
    F --> G["Append ALL tool_results<br/>in one user message"]
    G --> H{"submit_* called<br/>and schema valid?"}
    H -- "yes" --> Z["Return typed output"]
    H -- "no" --> I{"turns < MAX?"}
    I -- "yes" --> C
    I -- "no" --> Y["Degrade: use deterministic<br/>fast-mode result, degraded=true"]
    D -- "end_turn without submit" --> I
    D -- "all providers failed" --> Y
```

---

## 7. Min-cut computation

```mermaid
flowchart TD
    A["Input: sandbox graph, src<br/>PROTECT_CAP = edge count + 1"] --> B["Q6: algo.maxFlow<br/>src → all crown jewels<br/>cap property, attack types"]
    B --> C["Q7: load all attack edges<br/>(eid, u, v, cap)"]
    C --> D["Build residual graph<br/>forward if cap - flow > 0<br/>backward if flow > 0"]
    D --> E["BFS from src → set S<br/>(crossing protected edges is correct:<br/>they are never saturated)"]
    E --> F["Cut = edges u in S, v not in S"]
    F --> G{"sum of cut caps == maxFlow?"}
    G -- "no" --> X["Raise: sanity check failed<br/>(bug or wrong assumption)"]
    G -- "yes" --> H{"maxFlow >= PROTECT_CAP?"}
    H -- "yes" --> N["safe = false<br/>split simulation<br/>(Role/Policy/Group only), proven or not"]
    H -- "no" --> I["Also compute sink-side cut<br/>(reverse BFS from targets)"]
    I --> W["Witness path per cut edge<br/>(walk flow back to src, forward to a jewel)"]
    W --> J["Return candidates + witnesses,<br/>maxFlow, safe=true<br/>→ Planner simulates and chooses by collateral"]
```

---

## 8. Fix → verify → retry loop

```mermaid
flowchart TD
    P["PREPARE<br/>reset sandbox, GRAPH.COPY,<br/>protect ALL company needs"] --> PL["PLAN<br/>min-cut candidates → simulate →<br/>LLM picks lowest collateral"]
    PL --> S{"safe?"}
    S -- "no" --> NH["NEEDS_HUMAN<br/>+ suggestion"]
    S -- "yes" --> V1{"plan valid?<br/>eids exist, none protected"}
    V1 -- "no" --> PL
    V1 -- "yes" --> EX["EXECUTE<br/>apply_fix on sandbox"]
    EX --> VF["VERIFY<br/>A: maxFlow == 0<br/>B: bfs reaches no jewel<br/>C: every need ok"]
    VF --> OK{"A and B and C?"}
    OK -- "yes" --> RP["REPORT"]
    OK -- "no" --> CH{"can an INPUT change?<br/>e.g. protect broken need's 2nd path,<br/>drop failed custom cut"}
    CH -- "no (same input = same result)" --> NH
    CH -- "yes" --> AT{"attempt < 3?"}
    AT -- "yes" --> P
    AT -- "no" --> NH
    NH --> RP
```

---

## 9. Example S2: before and after the fix

**Before:** 2 attack paths reach the crown jewels. **Business needs:** Ravi must still reach `server:staging` (deploys) and `server:build` (runs builds), so those path edges are protected (cap = PROTECT_CAP).

```mermaid
flowchart LR
    K(["user:ravi<br/>(compromised)"]):::bad --> G["group:ci-deployers"]
    G --> R1["role:deploy-role"]
    R1 --> P1["policy:deploy-staging"]
    P1 --> S1[("server:staging")]
    R1 == "e:0107 HAS_POLICY" ==> P2["policy:lambda-admin"]
    P2 -- "CAN_PASS_ROLE" --> R2["role:data-admin"]
    R2 --> P3["policy:read-customers"]
    P3 --> DB1[("db:customers")]:::jewel
    K -- "CAN_SSH (protected)" --> SV["server:build"]
    SV == "e:0213 STORES_SECRET" ==> SE["secret:billing-svc-pw"]
    SE --> U2["user:billing-svc"]
    U2 --> P4["policy:read-payments"]
    P4 --> DB2[("db:payments")]:::jewel
    classDef bad fill:#fdd,stroke:#c00
    classDef jewel fill:#ffd,stroke:#b80
```

**After:** max flow was 2, so the min-cut removes 2 edges. The source-side residual search stops at the first saturated edge after the protected ones:
- `e:0107`: detach `policy:lambda-admin` from `role:deploy-role`, which removes the "create programs that run as other roles" right.
- `e:0213`: delete the saved `billing-svc` password from `server:build`.

Both business paths are untouched.

```mermaid
flowchart LR
    K(["user:ravi"]) --> G["group:ci-deployers"]
    G --> R1["role:deploy-role"]
    R1 --> P1["policy:deploy-staging"]
    P1 --> S1[("server:staging<br/>still reachable ✅")]:::ok
    R1 -. "e:0107 REMOVED" .-> P2["policy:lambda-admin"]
    P2 --> R2["role:data-admin"]
    R2 --> P3["policy:read-customers"] --> DB1[("db:customers<br/>unreachable ✅")]:::ok
    K --> SV[("server:build<br/>still reachable ✅")]:::ok
    SV -. "e:0213 REMOVED" .-> SE["secret:billing-svc-pw"]
    SE --> U2["user:billing-svc"] --> P4["policy:read-payments"] --> DB2[("db:payments<br/>unreachable ✅")]:::ok
    classDef ok fill:#dfd,stroke:#090
```

**Proof:** `maxFlow(ravi → jewels) = 0`, `algo.bfs` from ravi reaches no jewel, and every company-wide need is ok (including `ravi → staging` and `ravi → build`). **Collateral:** the report lists anyone else who lost access through `e:0107`/`e:0213` (in S2, nobody uses `policy:lambda-admin` via `deploy-role`, so collateral = 0; the generator asserts this).

> **Why business needs matter:** without the `ravi → build` need, the source-side cut would remove Ravi's SSH to `server:build` instead. That's also size 1, but it would block his builds. The capacities steer the min-cut away from edges people need.

---

## 10. Data generation and load

```mermaid
flowchart LR
    A["scenarios/*.yaml"] --> B["generator.py<br/>SEED=42"]
    B --> C["background company<br/>~120 nodes + noise edges"]
    B --> D["planted scenario sub-graphs"]
    C --> E["graph.json"]
    D --> E
    D --> F["answer_keys/*.json<br/>true paths, min cut size, safe?"]
    E --> G["loader.py<br/>UNWIND batches<br/>per label + one edge query"]
    G --> H[("t:acme:baseline")]
    G --> I["CREATE INDEX<br/>Asset.id, Database.crown_jewel, eid"]
    F --> J["eval/run_eval.py"]
```

---

## 11. Failure and degradation paths

```mermaid
flowchart TD
    S["Agent step"] --> L{"LLM call ok?"}
    L -- "yes" --> OK["Continue"]
    L -- "429 / 5xx / timeout / refusal" --> R1["retry once,<br/>then next provider<br/>Groq → OpenRouter → Mistral"]
    R1 --> L2{"any provider ok?"}
    L2 -- "yes" --> OK
    L2 -- "no" --> DG["DEGRADE<br/>use fast-mode result<br/>template report<br/>degraded=true"]
    S --> DBQ{"FalkorDB reachable?"}
    DBQ -- "no" --> W8["readyz 503<br/>job stays queued<br/>retry with backoff"]
    S --> TO{"Query timeout?"}
    TO -- "yes" --> NR["Return TIMEOUT to agent<br/>agent narrows query<br/>(smaller k / target)"]
    DG --> FIN["Incident still finishes<br/>with paths, fix, proof"]
```

---

## 12. Worker crash recovery (scale path: **cut in v1**, single worker; the lock heartbeat is built)

```mermaid
sequenceDiagram
    participant W1 as Worker 1
    participant R as Redis
    participant W2 as Worker 2
    W1->>R: XREADGROUP jobs → inc-123
    W1->>R: SET lock:inc:123 NX PX 60000
    W1->>R: checkpoint status=PLANNING
    Note over W1: crashes mid-step
    Note over R: lock expires after 60 s
    W2->>R: XAUTOCLAIM jobs (idle > 60 s) → inc-123
    W2->>R: SET lock:inc:123 NX PX 60000
    W2->>R: GET inc:123 → status=PLANNING
    Note over W2: resumes at PLANNING<br/>(sandbox reused, idempotent)
    W2->>R: ... VERIFYING → DONE
    W2->>R: XACK jobs inc-123
```

---

## 13. Evaluation pipeline

```mermaid
flowchart LR
    K["answer_keys/*.json"] --> E["run_eval.py"]
    subgraph Runs["per scenario S1–S8"]
        F["Fast mode<br/>(graph, no LLM)"]
        A["Agent mode<br/>(graph + LLM)"]
        B["Baselines<br/>disable-user · greedy betweenness ·<br/>greedy path-frequency · LLM + JSON"]
        BF["Brute-force optimum<br/>(small scenarios)"]
    end
    E --> F & A & B & BF
    F & A & B & BF --> M["metrics.py<br/>recall, precision, fix gap,<br/>residual flow, business broken,<br/>collateral, verdict, latency, tokens"]
    M --> T["Results table<br/>→ README + UI"]
```
