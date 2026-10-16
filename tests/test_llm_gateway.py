"""LLM gateway: unit tests with a fake OpenAI client (no network), then live tests on OpenRouter (-m live)."""
import os

import httpx
import openai
import pytest
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from agents import llm_gateway as gw
from core import config
from core.graph import client as graph_client

MESSAGES = [{"role": "user", "content": "hello"}]
FORCED = {"type": "function", "function": {"name": "submit_fix_plan"}}
TOOLS = [{"type": "function", "function": {"name": "get_blast_radius", "description": "Blast radius.", "parameters": {
    "type": "object", "properties": {"graph": {"type": "string"}, "src": {"type": "string"}}, "required": ["graph", "src"]}}}]
REQUEST = httpx.Request("POST", "http://llm.test/chat/completions")


def completion(text="hi", choices=True):
    message = ChatCompletionMessage(role="assistant", content=text)
    return ChatCompletion(id="x", created=0, model="m", object="chat.completion", usage=CompletionUsage(
        prompt_tokens=5, completion_tokens=2, total_tokens=7), choices=[Choice(index=0, finish_reason="stop", message=message)] if choices else [])


def status_error(cls, code, body=None, headers=None):
    return cls("boom", response=httpx.Response(code, request=REQUEST, headers=headers), body=body)


class FakeClient:
    """Plays a script of outcomes (a response, or an exception to raise) and records every request."""

    def __init__(self, *script):
        self.script, self.requests = list(script), []
        self.chat = self
        self.completions = self

    def create(self, **request):
        self.requests.append(request)
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake(monkeypatch):
    """A configured gateway (provider 'p', model 'model-p') whose client is whatever you set in fake.client; no sleep, no cache."""
    monkeypatch.setattr(config, "LLM_PROVIDERS", ["p"])
    monkeypatch.setattr(config, "LLM_CACHE", False)
    monkeypatch.setattr(gw, "RETRY_BACKOFF_S", 0)
    monkeypatch.setenv("P_API_KEY", "key-p")
    monkeypatch.setenv("P_BASE_URL", "http://p.test/v1")
    monkeypatch.setenv("MODEL_PLANNER_P", "model-p")

    class Holder:
        client, built = None, []
    holder = Holder()

    def build(*args):
        holder.built.append(args)
        return holder.client
    monkeypatch.setattr(gw, "_client", build)
    return holder


# --- configuration ---

def test_the_client_calls_the_provider_directly_and_openrouter_gets_x_title(monkeypatch):
    monkeypatch.setattr(config, "LLM_TIMEOUT_S", 30.0)
    gw._client.cache_clear()
    c = gw._client("openrouter", "http://or.test/v1", "or-key")
    assert str(c.base_url) == "http://or.test/v1/" and c.api_key == "or-key" and c.max_retries == 0 and c.timeout == 30.0
    assert c.default_headers["X-Title"] == "BreachPath"
    assert "X-Title" not in gw._client("groq", "http://g.test/v1", "g-key").default_headers
    assert gw._client("openrouter", "http://or.test/v1", "or-key") is c  # cached once
    gw._client.cache_clear()


def test_chat_builds_the_client_from_the_settings(fake):
    fake.client = FakeClient(completion())
    gw.chat("planner", "p", MESSAGES)
    assert fake.built == [("p", "http://p.test/v1", "key-p")]


def test_a_known_provider_needs_no_base_url_line(fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("MODEL_PLANNER_OPENROUTER", "or-model")
    fake.client = FakeClient(completion())
    gw.chat("planner", "openrouter", MESSAGES)
    assert fake.built == [("openrouter", "https://openrouter.ai/api/v1", "or-key")]


@pytest.mark.parametrize("missing", ["P_API_KEY", "P_BASE_URL", "MODEL_PLANNER_P"])
def test_a_missing_key_url_or_model_is_llm_unavailable_and_says_what_to_set(fake, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(gw.LLMUnavailable, match=f"set {missing}") as err:
        gw.chat("planner", "p", MESSAGES)
    assert "key-p" not in str(err.value)


def test_a_missing_model_for_another_agent_is_llm_unavailable(fake):
    with pytest.raises(gw.LLMUnavailable, match="MODEL_REPORTER_P"):
        gw.chat("reporter", "p", MESSAGES)  # only MODEL_PLANNER_P is set


def test_an_unknown_agent_is_a_bug(fake):
    with pytest.raises(ValueError):
        gw.chat("nonsense", "p", MESSAGES)


# --- chat: request building ---

def test_forced_tool_choice_is_downgraded_when_the_provider_cannot_force(fake, monkeypatch):
    fake.client = FakeClient(completion(), completion())
    gw.chat("planner", "p", MESSAGES, TOOLS, tool_choice=FORCED, response_format={"type": "json_object"})  # 'p' has no caps
    monkeypatch.setitem(gw.CAPS, "p", {"forced_tool_choice": True, "parallel": True, "json_mode": True})
    gw.chat("planner", "p", MESSAGES, TOOLS, tool_choice=FORCED, response_format={"type": "json_object"})
    plain, full = fake.client.requests
    assert plain["tool_choice"] == "auto" and "response_format" not in plain
    assert full["tool_choice"] == FORCED and full["response_format"] == {"type": "json_object"}
    assert (plain["model"], plain["temperature"], plain["max_tokens"]) == ("model-p", 0, config.LLM_MAX_TOKENS)


def test_the_reporter_gets_its_own_max_tokens(fake, monkeypatch):
    monkeypatch.setenv("MODEL_REPORTER_P", "model-r")
    fake.client = FakeClient(completion(), completion())
    gw.chat("reporter", "p", MESSAGES)
    gw.chat("planner", "p", MESSAGES)
    reporter, planner = fake.client.requests
    assert (reporter["max_tokens"], planner["max_tokens"]) == (config.LLM_MAX_TOKENS_REPORTER, config.LLM_MAX_TOKENS)  # defaults 2500 / 2000 (docs/06 section 6)


def test_the_baseline_agent_uses_the_planners_model_and_4000_max_tokens(fake):
    fake.client = FakeClient(completion())
    gw.chat("baseline", "p", MESSAGES)  # MODEL_BASELINE_P is not set: the Planner's MODEL_PLANNER_P is used
    request, = fake.client.requests
    assert (request["model"], request["max_tokens"]) == ("model-p", config.LLM_MAX_TOKENS_BASELINE) and config.LLM_MAX_TOKENS_BASELINE == 4000


def test_chat_uses_the_given_providers_url_key_model_and_caps(fake, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    monkeypatch.setenv("GROQ_BASE_URL", "http://g.test/v1")
    monkeypatch.setenv("MODEL_PLANNER_GROQ", "g-model")
    fake.client = FakeClient(completion(), completion())
    gw.chat("planner", "groq", MESSAGES, TOOLS, tool_choice=FORCED)  # groq can force a function
    gw.chat("planner", "p", MESSAGES, TOOLS, tool_choice=FORCED)  # 'p' has no caps: downgraded
    assert fake.built == [("groq", "http://g.test/v1", "g-key"), ("p", "http://p.test/v1", "key-p")]
    groq, other = fake.client.requests
    assert (groq["model"], groq["tool_choice"]) == ("g-model", FORCED) and (other["model"], other["tool_choice"]) == ("model-p", "auto")
    assert gw.CAPS["groq"] == {"forced_tool_choice": True, "parallel": False, "json_mode": True}  # VERIFY_RESULTS P1.4


def test_providers_for_keeps_the_order_and_skips_the_incomplete(fake, monkeypatch, caplog):
    for name in ("t", "q", "s"):  # t: complete. q: key + URL but no model. s: unknown provider with key + model but no URL
        monkeypatch.setenv(f"{name.upper()}_API_KEY", f"key-{name}")
    monkeypatch.setenv("T_BASE_URL", "http://t.test/v1")
    monkeypatch.setenv("MODEL_PLANNER_T", "model-t")
    monkeypatch.setenv("Q_BASE_URL", "http://q.test/v1")
    monkeypatch.setenv("MODEL_PLANNER_S", "model-s")
    monkeypatch.setattr(config, "LLM_PROVIDERS", ["t", "q", "r", "s", "p"])  # r: nothing at all
    with caplog.at_level("WARNING", logger="breachpath.llm"):
        assert gw.providers_for("planner") == ["t", "p"]  # the order of LLM_PROVIDERS
    assert "q not configured for planner: set MODEL_PLANNER_Q" in caplog.text and "r not configured" in caplog.text
    assert "s not configured for planner: set S_BASE_URL" in caplog.text and "key-" not in caplog.text
    monkeypatch.setattr(config, "LLM_PROVIDERS", ["p", "t"])
    assert gw.providers_for("planner") == ["p", "t"] and gw.providers_for("reporter") == []


def test_the_result_carries_the_facts(fake):
    fake.client = FakeClient(completion("hello"))
    result = gw.chat("planner", "p", MESSAGES)
    assert (result.message.content, result.provider, result.model) == ("hello", "p", "model-p")
    assert (result.tokens_in, result.tokens_out, result.cached) == (5, 2, False) and result.latency_ms >= 0


# --- chat: errors ---

@pytest.mark.parametrize("error", [status_error(openai.RateLimitError, 429), status_error(openai.InternalServerError, 503),
                                   status_error(openai.APIStatusError, 408), openai.APITimeoutError(request=REQUEST),
                                   openai.APIConnectionError(request=REQUEST), "no choices"])
def test_a_transient_error_is_retried_once_then_llm_unavailable(fake, error):
    error = completion(choices=False) if error == "no choices" else error
    fake.client = FakeClient(error, error)
    with pytest.raises(gw.LLMUnavailable, match="after 1 retry"):
        gw.chat("planner", "p", MESSAGES)
    assert len(fake.client.requests) == 2  # the call and exactly one retry


@pytest.mark.parametrize("headers,waited", [({"retry-after": "3"}, 3.0), ({"retry-after": "2.5"}, 2.5), ({"retry-after": "99"}, 20.0),
                                            ({}, 1.5), ({"retry-after": "soon"}, 1.5)])
def test_a_429_waits_for_retry_after_capped_then_retries_once(fake, monkeypatch, headers, waited):
    monkeypatch.setattr(gw, "RETRY_BACKOFF_S", 1.5)
    monkeypatch.setattr(config, "LLM_MAX_WAIT_S", 20.0)
    slept = []
    monkeypatch.setattr(gw.time, "sleep", slept.append)
    fake.client = FakeClient(status_error(openai.RateLimitError, 429, headers=headers), completion("ok"))
    assert gw.chat("planner", "p", MESSAGES).message.content == "ok" and slept == [waited]


def test_only_a_429_honours_retry_after(fake, monkeypatch):
    monkeypatch.setattr(gw, "RETRY_BACKOFF_S", 1.5)
    slept = []
    monkeypatch.setattr(gw.time, "sleep", slept.append)
    fake.client = FakeClient(status_error(openai.InternalServerError, 503, headers={"retry-after": "9"}), completion("ok"))
    gw.chat("planner", "p", MESSAGES)
    assert slept == [1.5]


def test_a_transient_error_then_success_returns_the_answer(fake):
    fake.client = FakeClient(status_error(openai.RateLimitError, 429), completion("ok"))
    assert gw.chat("planner", "p", MESSAGES).message.content == "ok" and len(fake.client.requests) == 2


@pytest.mark.parametrize("cls,code", [(openai.AuthenticationError, 401), (openai.PermissionDeniedError, 403), (openai.NotFoundError, 404)])
def test_auth_blocked_or_unknown_model_fails_at_once_without_a_retry(fake, cls, code):
    fake.client = FakeClient(status_error(cls, code, {"message": "model blocked"}), completion())
    with pytest.raises(gw.LLMUnavailable, match="provider rejected") as err:
        gw.chat("planner", "p", MESSAGES)
    assert len(fake.client.requests) == 1 and "model blocked" not in str(err.value)


@pytest.mark.parametrize("body", [{"message": "Failed to call a function.", "type": "invalid_request_error", "code": "tool_use_failed"},
                                  {"message": "Tool call validation failed: tool call validation failed: attempted to call tool 'x'", "type": "invalid_request_error"},
                                  {"message": "Parsing failed. The model generated output that could not be parsed. Please adjust your prompt.", "type": "invalid_request_error",
                                   "code": "output_parse_failed"}])  # found live (P7.7, S2): the same slip, another code
def test_a_400_for_the_models_malformed_tool_call_is_transient(fake, body):
    """Regression (P6.6 live, Groq): the provider rejected the MODEL's tool call with a 400. One retry, then the next provider / degrade."""
    fake.client = FakeClient(status_error(openai.BadRequestError, 400, body), completion("fine"))
    assert gw.chat("planner", "p", MESSAGES).message.content == "fine" and len(fake.client.requests) == 2
    fake.client = FakeClient(status_error(openai.BadRequestError, 400, body), status_error(openai.BadRequestError, 400, body))
    with pytest.raises(gw.LLMUnavailable, match="after 1 retry"):
        gw.chat("planner", "p", MESSAGES)


def test_a_400_is_our_bug_and_is_raised_as_is(fake):
    fake.client = FakeClient(status_error(openai.BadRequestError, 400))
    with pytest.raises(openai.BadRequestError):
        gw.chat("planner", "p", MESSAGES)
    assert len(fake.client.requests) == 1


# --- cache ---

def test_the_cache_key_is_stable_and_changes_with_the_request():
    args = ("p", "m", MESSAGES, TOOLS, "auto", 0, None)
    assert gw.cache_key(*args) == gw.cache_key(*args) and gw.cache_key(*args).startswith("llmcache:")
    assert gw.cache_key(*args) != gw.cache_key("p", "m", [{"role": "user", "content": "other"}], TOOLS, "auto", 0, None)
    assert gw.cache_key(*args) != gw.cache_key("p", "m2", MESSAGES, TOOLS, "auto", 0, None)
    assert gw.cache_key(*args) != gw.cache_key("q", "m", MESSAGES, TOOLS, "auto", 0, None)  # keyed by provider
    assert gw.cache_key(*args) != gw.cache_key("p", "m", MESSAGES, TOOLS, "auto", 0.2, None)


@pytest.fixture
def clean_cache(falkordb):
    """Remove the llmcache keys a test made (the others are left alone)."""
    redis = graph_client.get_db().connection
    before = set(redis.scan_iter(match="llmcache:*"))
    yield redis
    for key in set(redis.scan_iter(match="llmcache:*")) - before:
        redis.delete(key)


def test_a_cache_hit_skips_the_client_and_failures_are_not_cached(fake, monkeypatch, clean_cache):
    monkeypatch.setattr(config, "LLM_CACHE", True)
    messages = [{"role": "user", "content": "unit-test-cache-hit"}]
    fake.client = FakeClient(status_error(openai.AuthenticationError, 401), completion("first"))
    with pytest.raises(gw.LLMUnavailable):
        gw.chat("planner", "p", messages)
    first = gw.chat("planner", "p", messages)  # the failure was not cached: this one calls the client
    second = gw.chat("planner", "p", messages)  # this one must not
    assert (first.cached, second.cached, second.message.content) == (False, True, "first")
    assert len(fake.client.requests) == 2 and second.tokens_in == 5


def test_an_empty_answer_is_returned_but_not_cached(fake, monkeypatch, clean_cache):
    monkeypatch.setattr(config, "LLM_CACHE", True)
    messages = [{"role": "user", "content": "unit-test-empty-answer"}]
    fake.client = FakeClient(completion(""), completion("real"))
    empty = gw.chat("planner", "p", messages)
    again = gw.chat("planner", "p", messages)  # not served from the cache: the client is called again
    assert (empty.message.content, again.message.content, again.cached) == ("", "real", False) and len(fake.client.requests) == 2


def test_a_response_with_a_value_the_sdk_does_not_know_still_round_trips_through_the_cache(fake, monkeypatch, clean_cache):
    """Regression (P6.2 follow-up): Groq sends service_tier 'on_demand', which the SDK's literal rejects on re-validation."""
    monkeypatch.setattr(config, "LLM_CACHE", True)
    odd = completion("odd")
    odd.service_tier = "on_demand"
    fake.client = FakeClient(odd)
    messages = [{"role": "user", "content": "unit-test-odd-service-tier"}]
    first, second = gw.chat("planner", "p", messages), gw.chat("planner", "p", messages)
    assert (first.cached, second.cached, second.message.content) == (False, True, "odd") and len(fake.client.requests) == 1


# --- live (OpenRouter, free tier: at most 3 calls) ---

live = pytest.mark.live
CHOSEN = "nvidia/nemotron-3-super-120b-a12b:free"  # VERIFY_RESULTS P1.4


@pytest.fixture
def live_gateway(monkeypatch):
    """Skip (saying why) without OPENROUTER_API_KEY. Use the model from .env; if its line is empty there, the P1.4 model."""
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    monkeypatch.setattr(config, "LLM_PROVIDERS", ["openrouter"])
    if not os.getenv("MODEL_PLANNER_OPENROUTER"):
        monkeypatch.setenv("MODEL_PLANNER_OPENROUTER", CHOSEN)


@live
def test_live_call_then_cached(falkordb, clean_cache, live_gateway):
    messages = [{"role": "user", "content": "Call get_blast_radius for graph 't:s2:baseline' and src 'user:ravi'."}]
    model = os.environ["MODEL_PLANNER_OPENROUTER"]
    clean_cache.delete(gw.cache_key("openrouter", model, messages, TOOLS, "auto", 0, None))  # make sure the 1st call is live
    first = gw.chat("planner", "openrouter", messages, TOOLS)
    second = gw.chat("planner", "openrouter", messages, TOOLS)
    print(f"\nlive 1st call: {first.latency_ms} ms (cached={first.cached}), 2nd call: {second.latency_ms} ms (cached={second.cached})")
    assert first.message.tool_calls and first.cached is False
    assert second.cached is True and second.latency_ms < 50
    assert second.message.tool_calls[0].function.name == "get_blast_radius"


@live
def test_live_a_bad_key_is_llm_unavailable(falkordb, clean_cache, live_gateway, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "bad-key")
    with pytest.raises(gw.LLMUnavailable, match="provider rejected") as err:
        gw.chat("planner", "openrouter", [{"role": "user", "content": "unit-test-bad-key"}])
    print(f"\nbad key -> {err.value}")
    assert "bad-key" not in str(err.value)
