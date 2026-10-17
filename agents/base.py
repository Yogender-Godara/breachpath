"""The shared agent loop (docs/03_LLD.md section 6.1): allow-listed MCP tools -> OpenAI tools, a turn loop, a submit tool.

Providers (D18): the run starts on the first provider of LLM_PROVIDERS. If that provider is unavailable, the WHOLE run restarts
on the next one with a fresh conversation (never mid-conversation: tool-call ids and formats differ).

run_agent never knows about the fast brains: when the LLM cannot finish it returns degraded=True and the caller falls back.
A tool that CRASHES (an exception inside it, e.g. MinCutError) raises ToolCrashed: that is an invariant violation, never a retry.
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import jsonschema
from mcp import ClientSession, stdio_client
from pydantic import ValidationError

from agents import llm_gateway
from agents.llm_gateway import LLMUnavailable
from core import config
from mcp_server.launch import server_params

PREAMBLE = (Path(__file__).parent / "prompts" / "preamble.md").read_text(encoding="utf-8").strip()
ALWAYS_HIDDEN = ("incident_id", "lock_token")  # never in the LLM's schema; the caller must inject them (LLD section 5)
MAX_TOOL_RESULT_CHARS = 6000  # token budget


def short_reason(reason):
    """A short, readable label for why an LLM step fell back (shown in the UI and the report line): 'rate limit', 'provider timeout'..."""
    r = (reason or "").lower()
    if r.startswith("fact check failed"):
        return reason  # the Reporter's own label, already cut to length; its problem text must not be matched against the words below
    if "ratelimit" in r or "rate limit" in r or "429" in r:
        return "rate limit"
    if "no llm provider configured" in r:
        return "no provider configured"
    if "timeout" in r or "connection" in r:
        return "provider timeout"
    if any(w in r for w in ("403", "401", "permission", "authentication")):
        return "provider refused the call"
    if "badrequest" in r or "could not be parsed" in r or "tool_use_failed" in r:
        return "model output unusable"  # the provider rejected the model's own malformed output (twice)
    return (reason or "unknown")[:80]


def note_degraded(state, step, reason):
    """Record WHICH step fell back to the code (state["degraded_steps"]), e.g. 'reporter: rate limit'."""
    steps = state.setdefault("degraded_steps", [])
    label = f"{step}: {short_reason(reason)}"
    if label not in steps:
        steps.append(label)


BUDGET_REASON = "LLM budget for this incident used up"


def budget_used_up(state):
    """True when the incident already made LLM_MAX_CALLS_PER_INCIDENT calls (all agents, all provider restarts: state["llm_calls"],
    saved with the checkpoint). A runaway model can then never cost more than that."""
    return state is not None and state.get("llm_calls", 0) >= config.LLM_MAX_CALLS_PER_INCIDENT


def count_llm_call(state):
    """One request to a provider (a cached answer and a failed attempt count too: the limit must not depend on the cache)."""
    if state is not None:
        state["llm_calls"] = state.get("llm_calls", 0) + 1


class ToolCrashed(Exception):
    """An MCP tool raised an exception (not a normal {"ok": false} error). The pipeline turns a MinCutError into NEEDS_HUMAN."""


@dataclass
class AgentSpec:
    name: str  # investigator | planner | reporter
    system: str  # appended after the preamble
    allow_list: list
    submit: dict  # an entry of agents.schemas.SUBMIT_TOOLS
    output_model: type
    max_turns: int = 8
    temperature: float = 0


@dataclass
class AgentResult:
    output: object = None  # the validated output_model, or None when degraded
    degraded: bool = False
    reason: str = ""
    provider: str = ""  # the provider that produced the result
    turns: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


@asynccontextmanager
async def open_session(profile="full"):
    """One stdio MCP subprocess + session. The brains open one per agent step (agents/brains.py); tests open their own.
    Catch run_agent's exceptions (ToolCrashed...) INSIDE the `async with`: anyio wraps one that leaves it in an ExceptionGroup."""
    async with stdio_client(server_params(profile)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def load_tools(session, spec, inject=None):
    """(OpenAI tools incl. the submit tool, {tool: params the loop fills in}). A param is filled in (and removed from the LLM's
    schema) if it is in `inject` or is always hidden. Fails loudly on an unknown allow-listed name."""
    mcp_tools = {t.name: t for t in (await session.list_tools()).tools}
    unknown = [n for n in spec.allow_list if n not in mcp_tools]
    if unknown:
        raise ValueError(f"allow_list names tools the MCP server does not have: {unknown}")
    tools, hidden = [], {}
    for name in spec.allow_list:
        schema = json.loads(json.dumps(mcp_tools[name].input_schema))  # a copy: we edit it
        hidden[name] = [p for p in schema.get("properties", {}) if p in ALWAYS_HIDDEN or p in (inject or {})]
        for p in hidden[name]:
            del schema["properties"][p]
            schema["required"] = [r for r in schema.get("required", []) if r != p]
        tools.append({"type": "function", "function": {"name": name, "description": mcp_tools[name].description, "parameters": schema}})
    return tools + [spec.submit], hidden


async def tools_for(session, spec, inject=None):
    return (await load_tools(session, spec, inject))[0]


def _note(emit, type_, msg, data=None, **kw):
    if emit:
        emit(type_, msg, data, **{"latency_ms": None, "tokens_in": None, "tokens_out": None, **kw})


async def _call_tool(session, call, schemas, hidden, inject, on_result=None):
    """-> (tool message content, ok). Mistakes by the model are error messages it can fix; a crash inside a tool raises."""
    name = call.function.name
    if name not in hidden:
        return "error: tool not allowed", False
    try:
        args = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as e:
        return f"error: invalid JSON: {e}", False
    try:  # the MCP SDK reports bad arguments as is_error too, so check them here: what is left over is a real crash
        jsonschema.validate(args, schemas[name])
    except jsonschema.ValidationError as e:
        return f"error: invalid arguments: {e.message}", False
    result = await session.call_tool(name, {**args, **{p: inject[p] for p in hidden[name]}})  # injected values win over the model's
    text = result.content[0].text if result.content else ""
    if result.is_error:
        raise ToolCrashed(f"{name}: {text}")
    try:
        parsed = json.loads(text)
        ok = bool(parsed.get("ok"))
    except (ValueError, AttributeError):
        parsed, ok = None, False
    if ok and on_result:  # the caller (e.g. the Planner) collects results; injected params are not in `args`
        on_result(name, {k: v for k, v in args.items() if k not in hidden[name]}, parsed)
    if parsed is not None:  # compact and sorted: fewer tokens, and the same text (so the same cache key) every time
        text = json.dumps(parsed, separators=(",", ":"), sort_keys=True)
    return (text if len(text) <= MAX_TOOL_RESULT_CHARS else text[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"), ok


async def run_agent(session, spec, task, *, inject=None, emit=None, on_tool_result=None, state=None):
    """inject = values the loop puts into every tool call that HAS that parameter (e.g. graph, sandbox, src, incident_id, lock_token).
    They are removed from the LLM's schema, so the model never sees or writes them (and its messages do not depend on the incident).
    emit(type, msg, data, latency_ms=, tokens_in=, tokens_out=). on_tool_result(name, args, result_dict) runs after every successful tool call.
    state = the incident state: its llm_calls counter is the per-incident budget (BUDGET_REASON -> degraded=True at once, no provider restart)."""
    inject = inject or {}
    tools, hidden = await load_tools(session, spec, inject)
    needs = [n for n, h in hidden.items() if any(p in ALWAYS_HIDDEN and p not in inject for p in h)]
    if needs:
        raise ValueError(f"tools {needs} need incident_id and lock_token: pass them in inject")
    providers, total, reasons = llm_gateway.providers_for(spec.name), AgentResult(), []
    for i, provider in enumerate(providers):
        out, unavailable = await _run_once(provider, session, spec, task, tools, hidden, inject, emit, on_tool_result, state)  # a fresh conversation each time
        total.llm_calls, total.tokens_in, total.tokens_out = (total.llm_calls + out.llm_calls, total.tokens_in + out.tokens_in,
                                                              total.tokens_out + out.tokens_out)
        out.llm_calls, out.tokens_in, out.tokens_out = total.llm_calls, total.tokens_in, total.tokens_out
        if not unavailable:
            return out  # a valid submit, or any other ending (invalid output, max turns): final, no restart
        reasons.append(f"{provider}: {out.reason}")
        if i + 1 < len(providers):
            _note(emit, "step", f"provider {provider} unavailable: {out.reason}, restarting on {providers[i + 1]}", {"provider": provider})
    total.degraded = True
    total.reason = "; ".join(reasons) or f"no LLM provider configured (LLM_PROVIDERS={','.join(config.LLM_PROVIDERS)}): set a key and a model"
    return total


async def _run_once(provider, session, spec, task, tools, hidden, inject, emit, on_tool_result, state=None):
    """One attempt on one provider -> (AgentResult, True if it ended because the LLM was unavailable)."""
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    submit = spec.submit["function"]["name"]
    messages = [{"role": "system", "content": f"{PREAMBLE}\n\n{spec.system}"}, {"role": "user", "content": task}]
    out, bad_submits = AgentResult(provider=provider), 0

    def degrade(reason):
        out.degraded, out.reason = True, reason
        return out, False

    for turn in range(1, spec.max_turns + 1):
        if budget_used_up(state):
            _note(emit, "step", f"{BUDGET_REASON}: the code decides", {"llm_calls": state["llm_calls"], "limit": config.LLM_MAX_CALLS_PER_INCIDENT})
            return degrade(BUDGET_REASON)
        forced = {"type": "function", "function": {"name": submit}}  # the last turn forces the submit (if the provider can)
        count_llm_call(state)
        try:
            res = await asyncio.to_thread(llm_gateway.chat, spec.name, provider, messages, tools,
                                       forced if turn == spec.max_turns else "auto", spec.temperature)
        except LLMUnavailable as e:
            out.degraded, out.reason = True, str(e)
            return out, True
        out.turns, out.llm_calls = turn, out.llm_calls + 1
        out.tokens_in, out.tokens_out = out.tokens_in + res.tokens_in, out.tokens_out + res.tokens_out
        calls = res.message.tool_calls or []
        _note(emit, "step", f"turn {turn}: {len(calls)} tool call(s)", {"turn": turn, "provider": res.provider, "model": res.model,
              "cached": res.cached}, latency_ms=res.latency_ms, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
        messages.append({"role": "assistant", "content": res.message.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]}
            if calls else {"role": "assistant", "content": res.message.content or ""})
        if not calls:
            messages.append({"role": "user", "content": f"Call {submit} now."})  # counts as a turn
            continue

        replies = {}
        for call in (c for c in calls if c.function.name == submit):
            try:
                out.output = spec.output_model.model_validate_json(call.function.arguments)
                return out, False
            except ValidationError as e:
                bad_submits += 1
                if bad_submits == 2:
                    return degrade(f"invalid output twice: {e.errors()[0]['msg']}")
                replies[call.id] = f"error: invalid {submit}: {e}. Fix it and call {submit} again."

        async def one(call):
            began = time.perf_counter()
            replies[call.id], ok = await _call_tool(session, call, schemas, hidden, inject, on_tool_result)
            _note(emit, "tool_call", f"{call.function.name}: {'ok' if ok else 'error'}", {"tool": call.function.name, "ok": ok,
                  "provider": res.provider, "model": res.model, "cached": res.cached}, latency_ms=round((time.perf_counter() - began) * 1000))
        await asyncio.gather(*(one(c) for c in calls if c.function.name != submit))
        messages += [{"role": "tool", "tool_call_id": c.id, "content": replies[c.id]} for c in calls]
    return degrade(f"no valid {submit} within {spec.max_turns} turns")
