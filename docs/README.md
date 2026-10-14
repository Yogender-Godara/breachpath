# BreachPath: Design Docs

> **Status:** pre-hackathon planning notes and sketches, written 2026-10-14 (allowed by Rule 8). No code yet.
> Items marked **[VERIFY]** must be tested on the real FalkorDB / API at kickoff (Oct 15, 12:01 AM IST).

## Reading order

| # | Doc | What it answers |
|---|---|---|
| 0 | [../BreachPath_Problem_Statement.md](../BreachPath_Problem_Statement.md) | **Why**: the problem we solve |
| 1 | [01_Requirements.md](01_Requirements.md) | **What** the system must do: functional and non-functional requirements, acceptance criteria |
| 2 | [02_HLD.md](02_HLD.md) | **High-level design**: architecture, components, multi-agent design, scaling, availability, latency |
| 3 | [03_LLD.md](03_LLD.md) | **Low-level design**: graph schema, Cypher queries, min-cut algorithm, MCP tools, agents, API, data stores |
| 4 | [04_Flow_Diagrams.md](04_Flow_Diagrams.md) | **Diagrams**: context, deployment, sequence, agent state machine, algorithm and failure flows |
| 5 | [05_Problems_Blockers_Risks.md](05_Problems_Blockers_Risks.md) | **What can go wrong**: blockers, risks, known limits, open questions, kickoff checklist, solo plan |
| 6 | [06_Agent_Prompts_and_Schemas.md](06_Agent_Prompts_and_Schemas.md) | **Agent prompts**, strict tool schemas, baseline-eval prompt, per-agent settings |

## The design in 5 lines

1. **FalkorDB is the brain.** Attack paths, choke points, the min-cut fix and the proof all come from graph queries and algorithms.
2. **3 LLM agents + 2 code workers: the graph guarantees, the LLM chooses.** Code produces and verifies fix options that block the attack and keep **everyone's** business access. The LLM Planner picks the option with the least collateral (or proposes its own, which must pass simulation).
3. **Agents never touch the real graph.** Every change happens in a per-incident **sandbox copy** (`GRAPH.COPY`).
4. **Proof by math.** After the fix, `algo.maxFlow` from the attacker to the crown jewels must be **0**, and every business need must still be reachable.
5. **Simple to run, easy to scale.** One language (Python), 4 containers, `docker compose up`, one shared pipeline for fast and agent mode. MCP runs over stdio (no port). API and workers are stateless, so they scale out.

Diagrams use **Mermaid**. They render on GitHub, and in VS Code with a Mermaid preview extension.
