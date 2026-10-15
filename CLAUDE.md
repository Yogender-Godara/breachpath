# CLAUDE.md: BreachPath project rules

Read this first. It's the source of truth for **how** we work. The design lives in `docs/`, and progress lives in `task.md`.

## Project (one paragraph)
BreachPath is an attack-path agent on **FalkorDB**. Given a compromised identity, it finds every path to the crown jewels, computes the **smallest safe fix** (`algo.maxFlow` min-cut, with company-wide business needs protected), applies it to a **sandbox** (`GRAPH.COPY`), **proves** it (maxFlow = 0, bfs reaches no jewel, every need ok), and explains it.
Hackathon: Graph Hacks (WeMakeDevs × FalkorDB), **Track 1 only**. Solo developer. **Submit by Oct 18, 6:00 PM IST** (hard close 11:59 PM IST).

## Hard rules (never break)
1. **Commits:** the user commits. If Claude ever commits, **no `Co-Authored-By`, no "Generated with Claude", no AI attribution lines** in commit messages or PR text. AI use is disclosed only in the README "AI usage" section, which the user writes.
2. **Never read or write `.env`** (it's blocked). The template is `env.example`.
3. **FalkorDB must do the real work** (hackathon Rule 3): paths, min-cut, sandbox and proof all run in FalkorDB. `networkx` is allowed **only** in `data/` (generator) and `eval/` (test oracle), never in the product path.
4. **Synthetic data only** (Rule 6). No real cloud accounts or private data.
5. **The user must understand everything** (Rules 12–13). After each task, show: what it does, the main functions, the imports and why, the design choice, and real run output. Keep code simple enough to explain.
6. **Stay on track:** work in `task.md` order. Don't add features that aren't in `task.md` without asking. If behind schedule, use the cut list in `docs/05_Problems_Blockers_Risks.md` §6. Never cut what's on its "Never cut" list.

## Architecture invariants (from the docs; changing one needs the user's OK + a docs update)
- **One pipeline** `core/pipeline.py: run_incident(mode)` for fast and agent mode. Never duplicate orchestration.
- **3 LLM agents** (Investigator, Planner, Reporter) + **2 code workers** (Executor, Verifier) + Orchestrator. Never call it "5 agents".
- **Graph guarantees, LLM chooses:** code produces and verifies the options. The Planner picks by collateral → size → staleness and may only submit a cut that `simulate_cut` verified.
- **Agents never write the baseline.** Write tools pass the guards: sandbox-name regex, lock owner, scope, quota.
- **MCP = stdio subprocess** of the worker over the `core` library. No MCP container, no port. Fast mode calls `core` directly.
- **4 containers:** falkordb, api, worker, ui. Only `ui` publishes a port. `docker-compose.dev.yml` is local-only.
- **Min-cut:** `PROTECT_CAP = edge count + 1`. The residual BFS **crosses** protected edges. Never block them (it breaks max-flow = min-cut). Keep the sanity assert.
- **Retry only when an input changes.** An invariant violation → `NEEDS_HUMAN`, never a blind retry.
- **LLM:** provider-agnostic, OpenAI-compatible (Groq → OpenRouter → Mistral), **pinned per agent run**, restart the run on the next provider if one fails, response cache, temperature 0, Pydantic validation, minimal LLM output (code enriches it).
- **Cypher:** always parameterized. Edge types and hop bounds come only from constants in `core/graph/schema.py`.

## Environment
- Docker Engine runs **inside WSL** (no Docker Desktop). Run docker/compose via WSL: `wsl -e sh -c "cd /mnt/d/HACKTHON/breachpath && …"`.
- Repo path: Windows `D:\HACKTHON\breachpath`, WSL `/mnt/d/HACKTHON/breachpath`.
- App containers use **Python 3.12**. WSL has 3.14, so use `uv` for a local 3.12 venv.
- Use `.gitattributes` LF endings. Write files with LF.

## Workflow per task
1. Read the task in `task.md` + only the doc sections it links (use grep/offset, not whole files).
2. Build the smallest version that meets its **Done when** line. Add tests where the task says so.
3. Run it for real and show the output. Never claim something works without running it.
4. Tick the boxes in `task.md`, update **Now working on**, then run `python scripts/task_status.py`.
5. Blockers → the `task.md` Blockers log. Design changes → the Decision log + the matching `docs/` file.
6. Items marked **[VERIFY]** in the docs: test them on the real system and record the result in `docs/VERIFY_RESULTS.md`, then remove the tag.

## Key references (don't restate, point to)
| Need | Where |
|---|---|
| What to build next | `task.md` |
| Graph model, queries Q0–Q12, min-cut, tools, agents, API, config | `docs/03_LLD.md` |
| Architecture, decisions D1–D20, security | `docs/02_HLD.md` |
| Prompts and LLM schemas | `docs/06_Agent_Prompts_and_Schemas.md` |
| Blockers B1–B9, risks, cut list, day gates | `docs/05_Problems_Blockers_Risks.md` |
| Requirements and acceptance criteria | `docs/01_Requirements.md` |

## Communication with the user
- Plain, short English. The user often asks "tell me only" → answer briefly, no edits.
- Be accurate. Say what was verified vs assumed. Recommend one option, not a survey.
- Keep token use low: no long recaps, no re-reading files already in context, no restating the docs.
