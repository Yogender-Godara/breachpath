"""LLM gateway (docs/02_HLD.md section 5.3): OpenAI-compatible providers (LLM_PROVIDERS), called directly.

The caller (agents/base.py) picks the provider and restarts a whole run on the next one if this one is unavailable.
chat() checks the Redis cache, calls once (plus one retry for a transient error) and returns an LLMResult.
If the LLM cannot serve the call it raises LLMUnavailable: the agent loop degrades to the fast brains (degraded=true).
Never log a key, a full prompt or a full response.
"""
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache

import openai
from openai.types.chat import ChatCompletionMessage
from redis.exceptions import RedisError

from core import config
from core.graph.client import get_db

log = logging.getLogger("breachpath.llm")

AGENTS = ("investigator", "planner", "reporter", "baseline")
MODEL_OF = {"baseline": "planner"}  # the JSON-only eval baseline uses the Planner's model (a fair comparison, docs/06 section 6)
DEFAULT_URLS = {"openrouter": "https://openrouter.ai/api/v1", "groq": "https://api.groq.com/openai/v1"}
# source: VERIFY_RESULTS P1.4. An unknown provider gets all False: the safe default.
CAPS = {"openrouter": {"forced_tool_choice": True, "parallel": True, "json_mode": True, "reasoning_effort": False},
        "groq": {"forced_tool_choice": True, "parallel": False, "json_mode": True, "reasoning_effort": True}}  # reasoning_effort: P9.7 probe
NO_CAPS = {"forced_tool_choice": False, "parallel": False, "json_mode": False, "reasoning_effort": False}
REASONING_AGENTS = ("investigator", "planner")  # the Reporter's prose is never sent a reasoning setting
HEADERS = {"openrouter": {"X-Title": "BreachPath"}}  # groups our calls in OpenRouter's Activity log
RETRY_BACKOFF_S = 1.5  # the pause before the one retry of a transient error


class LLMUnavailable(Exception):
    """The LLM cannot serve this call (not configured, rejected, or still failing after the retry)."""


class Transient(Exception):
    """A 200 answer with no choices (OpenRouter's way of reporting an overloaded model)."""


@dataclass
class LLMResult:
    message: object  # the assistant message (content, tool_calls)
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cached: bool


def _settings(agent, provider):
    """(base_url, api_key, model). Anything missing -> LLMUnavailable that names what to set (never a key)."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}: use one of {AGENTS}")
    name = provider.upper()
    url = os.getenv(f"{name}_BASE_URL") or DEFAULT_URLS.get(provider)
    model_var = f"MODEL_{MODEL_OF.get(agent, agent).upper()}_{name}"
    key, model = os.getenv(f"{name}_API_KEY"), os.getenv(model_var)
    missing = [what for what, value in ((f"{name}_API_KEY", key), (f"{name}_BASE_URL", url), (model_var, model)) if not value]
    if missing:
        raise LLMUnavailable(f"{provider} not configured for {agent}: set {', '.join(missing)}")
    return url, key, model


def providers_for(agent):
    """The providers of LLM_PROVIDERS, in order, that have a key, a base URL and a model for this agent. Skipped ones are logged."""
    usable = []
    for provider in config.LLM_PROVIDERS:
        try:
            _settings(agent, provider)
            usable.append(provider)
        except LLMUnavailable as e:
            log.warning("provider skipped: %s", e)
    return usable


@lru_cache(maxsize=None)
def _client(provider, base_url, api_key):
    return openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=config.LLM_TIMEOUT_S,
                         default_headers=HEADERS.get(provider))


# --- cache ---

def cache_key(provider, model, messages, tools, tool_choice, temperature, response_format, extra_body=None):
    """Stable for the same request. messages and tools must be plain JSON data (dicts, lists, strings).
    extra_body (e.g. reasoning_effort) is part of the key ONLY when it is sent, so every key made without it stays the same."""
    request = [provider, model, messages, tools, tool_choice, temperature, response_format] + ([extra_body] if extra_body else [])
    return "llmcache:" + hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def _cache_get(key):
    """The cached response as a plain dict (not re-validated: a provider may send values the SDK's literals don't know, e.g. Groq's service_tier)."""
    try:
        raw = get_db().connection.get(key)
        return json.loads(raw) if raw else None
    except RedisError as e:  # a cache problem never stops a call
        log.warning("llm cache unavailable: %s", type(e).__name__)


def _cache_set(key, data):
    try:
        get_db().connection.set(key, json.dumps(data), ex=config.LLM_CACHE_TTL_S)
    except RedisError as e:
        log.warning("llm cache unavailable: %s", type(e).__name__)


# --- the call ---

def _rejected_model_output(e):
    """A 400 where the PROVIDER rejected the model's own malformed tool call (Groq: tool_use_failed, output_parse_failed). That is the model's slip, not our bug."""
    text = f"{e} {getattr(e, 'body', '')}".lower()
    return isinstance(e, openai.BadRequestError) and (getattr(e, "code", None) in ("tool_use_failed", "output_parse_failed")
                                                       or "tool call validation failed" in text or "could not be parsed" in text)


def _is_transient(e):
    return isinstance(e, (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError, Transient)) or (
        isinstance(e, openai.APIStatusError) and e.status_code == 408) or _rejected_model_output(e)  # APIConnectionError includes timeouts


def _retry_wait(e):
    """A 429: the provider's retry-after header (seconds), capped at LLM_MAX_WAIT_S. Anything else, or no header: RETRY_BACKOFF_S."""
    if isinstance(e, openai.RateLimitError):
        try:
            return min(float(e.response.headers["retry-after"]), config.LLM_MAX_WAIT_S)
        except (KeyError, ValueError):
            pass
    return RETRY_BACKOFF_S


def _create(client, request):
    """One call plus ONE retry for a transient error. 401/403/404 -> LLMUnavailable at once. Any other 4xx is OUR bug: raised as-is."""
    for attempt in (1, 2):
        try:
            response = client.chat.completions.create(**request)
            if not response.choices:
                raise Transient("200 without choices")
            return response
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError) as e:
            raise LLMUnavailable(f"provider rejected the call: {type(e).__name__} {e.status_code} (no retry)") from e
        except Exception as e:
            if not _is_transient(e):
                raise
            if attempt == 2:
                raise LLMUnavailable(f"provider still failing after 1 retry: {type(e).__name__}") from e
            wait = _retry_wait(e)
            log.warning("llm call failed (%s), retrying in %.1fs", type(e).__name__, wait)
            time.sleep(wait)


def max_tokens(agent):
    """docs/06 section 6: Reporter 2500, the JSON baseline 4000 (it answers with paths and a cut), the others 2000."""
    return {"reporter": config.LLM_MAX_TOKENS_REPORTER, "baseline": config.LLM_MAX_TOKENS_BASELINE}.get(agent, config.LLM_MAX_TOKENS)


def chat(agent, provider, messages, tools=None, tool_choice="auto", temperature=0, response_format=None):
    url, key, model = _settings(agent, provider)
    flags = CAPS.get(provider, NO_CAPS)
    if isinstance(tool_choice, dict) and not flags["forced_tool_choice"]:
        tool_choice = "auto"  # this provider can't force a function: the prompt + validation do the work
    if response_format and not flags["json_mode"]:
        response_format = None

    effort = config.LLM_REASONING_EFFORT
    extra = {"reasoning_effort": effort} if effort and agent in REASONING_AGENTS and flags.get("reasoning_effort") else None

    started = time.perf_counter()
    ckey = cache_key(provider, model, messages, tools, tool_choice, temperature, response_format, extra) if config.LLM_CACHE else None
    data = _cache_get(ckey) if ckey else None
    cached = data is not None
    if not cached:
        request = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens(agent)}
        if tools:
            request.update(tools=tools, tool_choice=tool_choice)
        if response_format:
            request["response_format"] = response_format
        if extra:
            request["extra_body"] = extra
        data = _create(_client(provider, url, key), request).model_dump(mode="json", warnings=False)
        message = data["choices"][0]["message"]
        if ckey and (message.get("content") or message.get("tool_calls")):  # an EMPTY answer (a reasoning model out of tokens) is not cached
            _cache_set(ckey, data)
    ms = round((time.perf_counter() - started) * 1000)
    usage = data.get("usage") or {}
    result = LLMResult(ChatCompletionMessage.model_validate(data["choices"][0]["message"]), provider, model,
                       usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0, ms, cached)
    log.info("llm %s %s agent=%s %dms tokens=%d/%d cached=%s", provider, model, agent, ms, result.tokens_in, result.tokens_out, cached)
    return result
