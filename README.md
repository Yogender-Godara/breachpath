# BreachPath

**An attack-path agent on FalkorDB that fixes the problem and proves the fix.**

When an access key leaks, BreachPath finds every path from the compromised identity to the company's most sensitive data. It then computes the **smallest set of permissions to remove** without breaking anyone's real work, applies that fix to a **sandbox copy** of the graph, and **proves** it worked: the max-flow from attacker to crown jewels is 0, and every business need is still reachable.

**Hackathon:** Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB)
**Track:** Track 1, *Agents That Act on Connected Data*

> 🚧 Work in progress during the hackathon (Oct 15–18, 2026). Live progress: [task.md](task.md)

---

## Design docs

Planning notes, graph-model sketches and diagrams **prepared before kickoff** (allowed by Rule 8), then updated during the build:

- [Problem statement](BreachPath_Problem_Statement.md)
- [Design docs index](docs/README.md): requirements, HLD, LLD, flow diagrams, risks, agent prompts

## Graph data model

_TODO (P10.1)_

## Cypher queries and graph algorithms used

_TODO (P10.1)_

## Setup

_TODO (P10.1)_

## Evaluation

_TODO (P8 / P10.1)_

## AI usage

_TODO (P10.2): written by the author._
