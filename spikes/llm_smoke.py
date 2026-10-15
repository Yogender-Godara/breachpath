"""P1.4 spike: does each LLM provider/model do tool calling the way the agents need it (B8)? Raw `openai` SDK, no gateway.

Run in the toolbox (it gets .env through env_file; keys are never printed):
  python spikes/llm_smoke.py --list
  python spikes/llm_smoke.py groq:llama-3.3-70b-versatile openrouter:some/model:free
"""
import json
import os
import sys
import time
from typing import Literal

import httpx
import openai
from pydantic import BaseModel

DEFAULT_URLS = {"groq": "https://api.groq.com/openai/v1", "openrouter": "https://openrouter.ai/api/v1",
                "mistral": "https://api.mistral.ai/v1"}
NOT_CHAT = ("whisper", "tts", "orpheus", "guard", "embed", "playai")  # Groq also lists audio and safety models


class Reason(BaseModel):
    eid: str
    why: str


class Plan(BaseModel):  # the submit_fix_plan schema (docs/06 section 3), as Pydantic
    cut_eids: list[str]
    chosen: Literal["source_side", "sink_side", "custom"]
    reasons: list[Reason]
    notes: str


def tool(name, props, required):
    return {"type": "function", "function": {"name": name, "description": f"Call {name}.", "parameters": {
        "type": "object", "properties": {p: {"type": "string"} for p in props}, "required": required}}}


BLAST = tool("get_blast_radius", ["graph", "src"], ["graph", "src"])
PATHS = tool("find_attack_paths", ["graph", "src", "target"], ["graph", "src", "target"])
SUBMIT = {"type": "function", "function": {"name": "submit_fix_plan", "description": "Submit the chosen fix plan.", "parameters": {
    "type": "object", "additionalProperties": False, "required": ["cut_eids", "chosen", "reasons", "notes"], "properties": {
        "cut_eids": {"type": "array", "items": {"type": "string"}},
        "chosen": {"type": "string", "enum": ["source_side", "sink_side", "custom"]},
        "reasons": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["eid", "why"],
                                               "properties": {"eid": {"type": "string"}, "why": {"type": "string"}}}},
        "notes": {"type": "string"}}}}}
PLAN_TASK = ("You are the Planner. Candidates: source_side = [e:s2:05, e:s2:10], sink_side = [e:s2:08, e:s2:13]. "
             "simulate_cut says source_side blocks the attack, keeps all business needs, collateral 0; sink_side has collateral 4. "
             "Submit the plan for source_side with one short reason per edge.")


def providers():
    names = ["groq", "openrouter", "mistral"]
    out = {}
    for name in (n.strip() for n in names):
        key = os.getenv(f"{name.upper()}_API_KEY")
        if key:
            url = os.getenv(f"{name.upper()}_BASE_URL", DEFAULT_URLS.get(name, ""))
            out[name] = (openai.OpenAI(api_key=key, base_url=url, max_retries=0), url, key)
        else:
            print(f"[{name}] no key: skipped")
    return out


def call(client, model, limits, **kw):
    """One attempt. Returns (response or None, error text or None, ms, tokens). Rate-limit headers go into `limits`."""
    start = time.perf_counter()
    try:
        raw = client.chat.completions.with_raw_response.create(model=model, temperature=0, timeout=30, **kw)
    except openai.RateLimitError as e:
        limits["429 message"] = str(e.body)[:300]  # often says which limit was hit
        return None, "rate limited", round((time.perf_counter() - start) * 1000), 0
    except Exception as e:
        limits["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        return None, "error", round((time.perf_counter() - start) * 1000), 0
    ms = round((time.perf_counter() - start) * 1000)
    limits.update({k: v for k, v in raw.headers.items() if k.lower().startswith("x-ratelimit")})
    resp = raw.parse()
    if not resp.choices:  # OpenRouter can answer 200 with an error body and no choices
        limits["error"] = f"200 without choices: {str(getattr(resp, 'model_extra', None))[:300]}"
        return None, "error", ms, 0
    return resp, None, ms, resp.usage.total_tokens if resp.usage else 0


def args_of(resp):
    return [(c.function.name, json.loads(c.function.arguments)) for c in (resp.choices[0].message.tool_calls or [])]


def run_checks(client, model, limits):
    """Returns {check: (result text, ms)}; result is PASS/FAIL/n or the error. One attempt per call, 3 s between calls."""
    res, forced = {}, True

    def step(key, fn, **kw):
        time.sleep(3)
        resp, err, ms, tokens = call(client, model, limits, **kw)
        res[key] = (err if err else fn(resp), ms, tokens)
        return resp, err

    def one_tool(r):
        try:
            return "PASS" if args_of(r) == [("get_blast_radius", {"graph": "t:s2:baseline", "src": "user:ravi"})] else f"FAIL {args_of(r)}"
        except Exception as e:
            return f"FAIL bad json: {e}"

    step("1", one_tool, tools=[BLAST], messages=[{"role": "user", "content":
         "Call get_blast_radius for graph 't:s2:baseline' and src 'user:ravi'."}])

    def parallel(r):
        try:
            return str(len(args_of(r)))
        except Exception as e:
            return f"FAIL bad json: {e}"
    step("2", parallel, tools=[PATHS], messages=[{"role": "user", "content":
         "From src 'user:ravi' in graph 't:s2:baseline', find attack paths to target 'db:s2-customers' AND to target "
         "'db:s2-payments'. Make both find_attack_paths calls now, in the same turn."}])

    def submit(r):
        try:
            calls = args_of(r)
            if [c[0] for c in calls] != ["submit_fix_plan"]:
                return f"FAIL called {[c[0] for c in calls]}"
            Plan(**calls[0][1])
            return "PASS" + ("" if forced else " (auto)")
        except Exception as e:
            return f"FAIL {type(e).__name__}: {str(e)[:80]}"
    msgs = [{"role": "user", "content": PLAN_TASK}]
    forced_choice = {"type": "function", "function": {"name": "submit_fix_plan"}}
    resp, err = step("3", submit, tools=[SUBMIT], tool_choice=forced_choice, messages=msgs)
    if err and err != "rate limited":  # forced tool_choice not supported: try once with auto
        forced = False
        step("3", submit, tools=[SUBMIT], tool_choice="auto", messages=msgs)
    res["forced"] = forced

    def json_mode(r):
        try:
            return "true" if isinstance(json.loads(r.choices[0].message.content), dict) else "false"
        except Exception:
            return "false"
    step("4", json_mode, response_format={"type": "json_object"}, messages=[{"role": "user", "content":
         'Reply with JSON only: an object with the key "answer" and the value 4.'}])

    def markdown(r):
        text = r.choices[0].message.content or ""
        return "PASS" if all(f"## {h}" in text for h in "ABC") else "FAIL"
    step("5", markdown, messages=[{"role": "user", "content": "Write 3 Markdown sections ## A ## B ## C, one line each."}])
    return res


def list_models(name, client, url, key):
    print(f"\n[{name}] candidate models")
    if name == "openrouter":  # the SDK object may not expose supported_parameters: read /models directly
        data = httpx.get(f"{url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30).json()["data"]
        for m in sorted(data, key=lambda m: m["id"]):
            if m["id"].endswith(":free") and "tools" in (m.get("supported_parameters") or []):
                print(f"  {m['id']}  (context {m.get('context_length')})")
    else:
        for m in sorted(client.models.list().data, key=lambda m: m.id):
            if not any(w in m.id for w in NOT_CHAT):
                print(f"  {m.id}  (context {getattr(m, 'context_window', None)})")


def main(args):
    provs = providers()
    if args == ["--list"]:
        for name, (client, url, key) in provs.items():
            list_models(name, client, url, key)
        return
    rows, header_info = [], {}
    for arg in args:
        name, model = arg.split(":", 1)  # the model id may contain ':' (OpenRouter ':free')
        if name not in provs:
            print(f"{arg}: provider {name} has no key, skipped")
            continue
        limits = {}
        print(f"running {arg} ...", flush=True)
        res = run_checks(provs[name][0], model, limits)
        ms = [v[1] for k, v in res.items() if k != "forced"]
        rows.append((name, model, res, round(sum(ms) / len(ms))))
        header_info[arg] = limits
    cell = lambda res, k: str(res[k][0])[:40]
    print("\nprovider | model | 1 tool | 2 parallel(n) | 3 submit | 4 json | 5 md | forced | avg ms | tokens")
    for name, model, res, avg in rows:
        tokens = sum(v[2] for k, v in res.items() if k != "forced")
        print(f"{name} | {model} | {cell(res, '1')} | {cell(res, '2')} | {cell(res, '3')} | {cell(res, '4')} | "
              f"{cell(res, '5')} | {res['forced']} | {avg} | {tokens}")
    print("\nrate-limit headers (last successful call per model)")
    for arg, limits in header_info.items():
        print(f"  {arg}: {limits or 'none seen'}")


if __name__ == "__main__":
    main(sys.argv[1:])
