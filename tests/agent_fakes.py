"""Shared fakes for the agent tests: scripted assistant messages and a scripted gateway (no LLM calls)."""
import copy
import json

from openai.types.chat import ChatCompletionMessage

from agents import llm_gateway
from agents.llm_gateway import LLMResult


def message(*calls, content=None):
    """An assistant message. calls: (name, args dict or raw string)."""
    return ChatCompletionMessage.model_validate({"role": "assistant", "content": content, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
        for i, (n, a) in enumerate(calls)] or None})


class Gateway:
    """Replaces llm_gateway.chat and providers_for. Plays a script of messages (or exceptions) and records what the loop sent.
    Gateway(monkeypatch, m1, m2) = one provider "fake". Gateway(monkeypatch, {"p1": [..], "p2": [..]}) = those providers, in order."""

    def __init__(self, monkeypatch, *script):
        self.scripts = {k: list(v) for k, v in script[0].items()} if script and isinstance(script[0], dict) else {"fake": list(script)}
        self.calls = []
        monkeypatch.setattr(llm_gateway, "chat", self.chat)
        monkeypatch.setattr(llm_gateway, "providers_for", lambda agent: list(self.scripts))

    def chat(self, agent, provider, messages, tools=None, tool_choice="auto", temperature=0, response_format=None):
        self.calls.append({"agent": agent, "provider": provider, "messages": copy.deepcopy(messages), "tools": tools, "tool_choice": tool_choice})
        json.dumps(messages)  # every message must be plain JSON, or the gateway cache key breaks
        step = self.scripts[provider].pop(0)
        if isinstance(step, Exception):
            raise step
        return LLMResult(step, provider, f"model-{provider}", 10, 5, 7, False)
