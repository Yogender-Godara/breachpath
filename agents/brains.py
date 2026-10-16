"""The agent brains: the same three entry points as core/fast_brains.py, so core/pipeline.py swaps them with one line (D20).

Each LLM step opens its OWN MCP stdio session inside asyncio.run and closes it before returning: no event loop is shared across
threads, and a crashed MCP subprocess only affects that one step. The Reporter needs no session (no tools): plain sync code.
"""
import asyncio

from agents import base, investigator, planner, reporter
from core.graph.mincut import MinCutError
from orchestrator import events


def _emitter(state, agent):
    """The agents' emit(type, msg, data, latency_ms=, tokens_in=, tokens_out=) -> an event in the incident's Redis stream (no keys in data)."""
    return lambda type_, msg, data, **kw: events.emit(state["id"], agent, type_, msg, data, **kw)


def _run(step):
    """asyncio.run(step(session)) on a fresh MCP session. Everything is caught INSIDE the `async with` (anyio would wrap an exception that
    leaves it in an ExceptionGroup) and re-raised after the session closed. A crash in an MCP tool that says MinCutError is an
    invariant violation: the pipeline turns it into NEEDS_HUMAN, never a retry. Any other ToolCrashed goes up (the pipeline: FAILED)."""
    async def go():
        error = None
        async with base.open_session() as session:
            try:
                return await step(session)
            except Exception as e:
                error = e
        raise error
    try:
        return asyncio.run(go())
    except base.ToolCrashed as e:
        if "MinCutError" in str(e):
            raise MinCutError(str(e)) from e
        raise


def investigate(state):
    findings, degraded = _run(lambda session: investigator.investigate(session, state, emit=_emitter(state, "investigator")))
    state["degraded"] = state["degraded"] or degraded  # the report adds its degraded line
    return findings


def plan(state, *, lock_token):
    plan_, degraded = _run(lambda session: planner.plan(session, state, lock_token=lock_token, emit=_emitter(state, "planner")))
    state["degraded"] = state["degraded"] or degraded
    return plan_


def report(state):
    markdown, degraded = reporter.write(state, emit=_emitter(state, "reporter"))  # falls back to the template report itself
    state["degraded"] = state["degraded"] or degraded
    return {"markdown": markdown}
