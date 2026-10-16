"""B-LLM-JSON (docs/06 section 5): the same model as the Planner, the WHOLE graph as JSON, no tools. "Just ask an LLM."

Its answer is validated with Pydantic. Invalid JSON or a wrong schema is a FAILED answer, scored as cut [] and NOT retried (fair comparison).
A provider that cannot answer (unavailable, request too large for its free tier) is skipped for the next one; when none can, the row is
"not run" with the reason, never a made-up value. The cut is scored by the networkx reference, not by the product.
"""
import json
import re

from pydantic import BaseModel, ValidationError

from agents import llm_gateway
from agents.llm_gateway import LLMUnavailable

SYSTEM = """You are a security analyst. Below is a company's full access graph as JSON.
Edges mean "if you control FROM, you gain TO".
Answer using only this data."""

TASK = """1. List every crown jewel the compromised identity can reach.
2. List up to 20 attack paths (<= 6 hops) as lists of eids.
3. Give the SMALLEST set of eids to remove so no crown jewel is reachable,
   without breaking any business need.
Reply with JSON only: {"reachable_jewels": [string], "paths": [[eid, ...], ...], "cut_eids": [eid, ...]}"""


class Answer(BaseModel):
    reachable_jewels: list[str]
    paths: list[list[str]]
    cut_eids: list[str]


def _dump(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def messages_for(graph):
    """The system + user messages for one scenario. The same information as docs/06 section 5 in a COMPACT form: the full JSON is about 8.5k
    tokens and Groq's free tier refuses a request over 8000 tokens (413). Edges are rows [eid, type, src, dst]; a node's label is its id prefix
    (user:, group:, role:, policy:, server:, db:, secret:) and a node with no edge cannot matter, so only the crown jewels are listed apart.
    The same text every time, so the cache hits."""
    jewels = sorted(n["id"] for n in graph["nodes"] if n["props"].get("crown_jewel"))
    edges = [[e["eid"], e["type"], e["src"], e["dst"]] for e in graph["edges"]]
    needs = [[n["principal_id"], n["resource_id"]] for n in graph["needs"] if n["valid"]]
    user = (f'GRAPH (edges are rows [eid, type, src, dst]): {_dump({"crown_jewels": jewels, "edges": edges})}\n'
            f'BUSINESS_NEEDS (rows [principal, resource]): {_dump(needs)}\nCOMPROMISED: "{graph["src"]}"\n\n{TASK}')
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse(text):
    """-> (Answer, "") or (None, why it failed). Only a markdown fence around the JSON is tolerated."""
    body = re.sub(r"\A```(?:json)?[ \t]*\n(.*?)\n?```\Z", r"\1", (text or "").strip(), flags=re.DOTALL)
    try:
        return Answer.model_validate(json.loads(body)), ""
    except json.JSONDecodeError:
        return None, "invalid JSON"
    except ValidationError as e:
        return None, f"wrong schema ({e.error_count()} error(s))"


def ask(graph):
    """One answer. -> {"answer": Answer | None, "failed": str, "provider", "model", "tokens_in", "tokens_out", "latency_ms", "not_run": str}.
    not_run is set (and the rest empty) when no provider could answer."""
    messages, reasons = messages_for(graph), []
    for provider in llm_gateway.providers_for("baseline"):
        try:
            result = llm_gateway.chat("baseline", provider, messages, temperature=0, response_format={"type": "json_object"})
        except LLMUnavailable as e:
            reasons.append(f"{provider}: {e}")
            continue
        except Exception as e:  # e.g. 413 request too large for the free tier: another provider may take it
            reasons.append(f"{provider}: {type(e).__name__} {str(e)[:120]}")
            continue
        answer, failed = parse(result.message.content)
        return {"answer": answer, "failed": failed, "provider": result.provider, "model": result.model, "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out, "latency_ms": result.latency_ms, "cached": result.cached, "not_run": ""}
    return {"answer": None, "failed": "", "provider": None, "model": None, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "cached": False,
            "not_run": "; ".join(reasons) or "no provider configured"}
