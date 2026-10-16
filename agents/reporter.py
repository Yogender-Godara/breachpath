"""The Reporter agent (docs/06 section 4, docs/03_LLD.md 6.2): the LLM WRITES the report, the CODE checks every fact in it.

No tools and no MCP session: one chat call over a small JSON slice of the incident state (`_slice`).
The text must pass `check` (7 headings in order, no invented eid or node id, "verified" only when the suggestion is proven,
NEEDS_HUMAN says there is no safe automatic fix). Problems -> ONE retry with the problems as a user message; still bad, or no
provider left -> the template report (core/fast_brains.report) and degraded=True. Fast mode never gets here.
"""
import json
import re
from pathlib import Path

from agents import llm_gateway
from agents.base import PREAMBLE
from agents.llm_gateway import LLMUnavailable
from core import fast_brains
from core.graph import queries
from core.graph.client import ro_query
from core.graph.schema import EDGE_TYPES

SYSTEM = f"{PREAMBLE}\n\n{(Path(__file__).parent / 'prompts' / 'reporter.md').read_text(encoding='utf-8').strip()}"
TEMPERATURE = 0.2  # docs/06 section 6
HEADINGS = ["Summary", "What was at risk", "Attack paths", "Fix", "Proof", "Business impact", "Next steps"]
DEGRADED_LINE = "Generated in degraded mode (LLM step unavailable)."
MAX_PATHS, MAX_PAIRS, MAX_DETAIL = 5, 10, 400

_TYPOGRAPHY = {**{c: "-" for c in [*range(0x2010, 0x2016), 0x2212]}, 0x00A0: " ", 0x202F: " "}  # U+2010-2015, minus sign; NBSP, narrow NBSP
# An id is not part of a longer word or of a need id ("need:usage:user:ravi:db:08" has user:/db: after a colon).
_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)*"
EID = re.compile(rf"(?<![\w:])e:{_NAME}:{_NAME}")
NODE = re.compile(rf"(?<![\w:])(?:user|group|role|policy|server|db|secret):{_NAME}")
PRONOUN = re.compile(r"\b(?:he|she|him|her|his|hers)\b", re.IGNORECASE)
MODE_LINE = re.compile(r"^.*\bGenerated in\b.*\bmode\b.*$", re.MULTILINE)
NO_ONE = re.compile(r"\b(?:nobody|no one|none)\b", re.IGNORECASE)
IMPACT = re.compile(r"^## +Business impact.*?$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
HEADING = re.compile(r"^## +(.+?) *$", re.MULTILINE)


# --- the slice: only what the report may use ---

def _cut_kinds(state):
    """{eid: edge kind} of the cut edges, from the BASELINE (the sandbox no longer has them after the fix)."""
    cut = set((state.get("plan") or {}).get("cut_eids") or [])
    if not cut:
        return {}
    return {r[0]: r[4] for r in ro_query(state["baseline_graph"], queries.ALL_EDGES) if r[0] in cut}


def _collateral(c):
    return {"count": c["count"], "weighted": c["weighted"], "pairs": c["pairs"][:MAX_PAIRS]}


def _slice(state, kinds):
    """The trimmed JSON dict the Reporter sees. kinds: {eid: edge kind} of the cut edges. No raw tool results, no lists of protected edges."""
    # The report is written while the status is still REPORTING; the pipeline makes it DONE right after.
    out = {k: state[k] for k in ("id", "tenant", "src", "degraded", "attempt")}
    out["status"] = "DONE" if state["status"] == "REPORTING" else state["status"]
    if state.get("failure_detail"):
        out["failure_detail"] = state["failure_detail"][:MAX_DETAIL]
    if f := state.get("findings"):
        shortest = sorted(f["paths"], key=lambda p: len(p["eids"]))[:MAX_PATHS]
        out["findings"] = {"reachable_jewels": f["reachable_jewels"], "path_count": len(f["paths"]), "truncated": f["truncated"],
                           "paths": [{"target": p["target"], "node_ids": p["node_ids"], "eids": p["eids"]} for p in shortest]}
    if p := state.get("plan"):
        sim = p["simulation"]
        out["plan"] = {
            "cut_eids": p["cut_eids"], "chosen": p["chosen"], "safe": p["safe"], "notes": p["notes"][:MAX_DETAIL],
            "reasons": p["reasons"], "witness": p["witness"], "suggestion": p["suggestion"],
            "simulation": sim and {"collateral": _collateral(sim["collateral"]), "size": sim["size"], "min_size": sim["min_size"]},
            "cut_actions": [{"eid": e, "action": EDGE_TYPES[kinds[e]]["remediation"], "host_action": bool(EDGE_TYPES[kinds[e]].get("host_action"))}
                            for e in p["cut_eids"] if e in kinds]}
        if p["safe"]:  # not safe: max_flow is the protect cap there, not a meaningful flow
            out["plan"]["attack_max_flow_before_fix"] = p["max_flow"]
    if a := state.get("action"):
        out["action"] = {"applied_eids": a["applied_eids"], "protected_count": len(a["protected_eids"])}
    if v := state.get("verdict"):
        out["verdict"] = {"pass": v["pass"], "attack_max_flow": v["attack_max_flow"], "bfs_reached_jewels": v["bfs_reached_jewels"],
                          "needs_checked": len(v["business"]), "needs_ok": sum(bool(n["ok"]) for n in v["business"]),
                          "collateral": _collateral(v["collateral"]), "already_broken": v["already_broken"],
                          "failure_detail": v["failure_detail"] and v["failure_detail"][:MAX_DETAIL]}
    return out


def _dump(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _user_message(slice_):
    """The incident id is left out: it would change the messages (and the LLM cache key) for every incident of a scenario."""
    return "Incident state JSON:\n" + _dump({k: v for k, v in slice_.items() if k != "id"})


# --- the fact guard (code only) ---

def _heading_name(text):
    """'Summary – one paragraph' (the prompt's own layout) counts as 'Summary'."""
    return re.split(r"\s+[–—-]\s+|:", text, maxsplit=1)[0].strip()


def check(markdown, slice_):
    """Pure. -> a list of problems; empty means the report may be used."""
    problems = []
    found = [_heading_name(h) for h in HEADING.findall(markdown)]
    if [h for h in found if h in HEADINGS] != HEADINGS:
        missing = [h for h in HEADINGS if found.count(h) != 1]
        problems.append("the report needs exactly these '## ' headings, once each and in this order: "
                        + ", ".join(HEADINGS) + (f" (wrong or missing: {', '.join(missing)})" if missing else " (the order is wrong)"))
    known = _dump(slice_)
    for pattern, what in ((EID, "eid"), (NODE, "node id")):
        allowed = set(pattern.findall(known))
        problems += [f"{what} {token} is not in the incident JSON: remove it" for token in dict.fromkeys(pattern.findall(markdown)) if token not in allowed]
    proven = bool(((slice_.get("plan") or {}).get("suggestion") or {}).get("proven"))
    said_verified = bool(re.search(r"\bverified\b", markdown, re.IGNORECASE))
    if said_verified and not proven:
        problems.append('the word "verified" may only be used when plan.suggestion.proven is true: use another word')
    if proven and not said_verified:
        problems.append('plan.suggestion.proven is true: say that the suggestion is "verified" (in Next steps) and give its proof (suggestion.proof)')
    if slice_["status"] == "NEEDS_HUMAN" and not re.search(r"no (?:automatic )?safe (?:automatic )?(?:fix|cut)", markdown, re.IGNORECASE):
        problems.append("the status is NEEDS_HUMAN: say clearly that no safe automatic fix was found, and why")
    impact = (IMPACT.search(markdown) or [None, ""])[1]
    no_collateral_data = not (slice_.get("plan") or {}).get("simulation") and not (slice_.get("verdict") or {}).get("collateral")
    if no_collateral_data and impact.strip() and (NO_ONE.search(impact) or "not assessed" not in impact.lower()):
        problems.append('there is no collateral data: in Business impact do not say "nobody", "no one" or "none"; write "not assessed"')
    if pronouns := dict.fromkeys(w.lower() for w in PRONOUN.findall(markdown)):
        problems.append(f"do not use pronouns for identities ({', '.join(pronouns)}): refer to them by id")
    for line in MODE_LINE.findall(markdown):
        if not (slice_["degraded"] and line.strip("*_ ") == DEGRADED_LINE):  # the ONLY allowed one: the degraded line, when degraded
            problems.append(f'remove the line "{line.strip()}": only the exact line "{DEGRADED_LINE}" is allowed, and only when degraded is true')
    return problems


# --- write ---

def _emit(emit, type_, msg, data, **kw):
    if emit:
        emit(type_, msg, data, **{"latency_ms": None, "tokens_in": None, "tokens_out": None, **kw})


def _normalise(text):
    """Models write ids with Unicode hyphens (secret:s2-billing + U+2011 + pw), which would read as another id. The guard and the returned
    report use the normalised text."""
    return text.translate(_TYPOGRAPHY)


def _unfence(text):
    """Some models wrap the whole answer in a ```markdown fence."""
    return re.sub(r"\A```(?:markdown|md)?[ \t]*\n(.*?)\n?```\Z", r"\1", text.strip(), flags=re.DOTALL)


def _ask(provider, slice_, emit, tally):
    """One call, and ONE retry when the guard finds problems (they are sent back as a user message).
    -> (markdown, []) or (None, the problems of the 2nd answer). LLMUnavailable goes up: the caller tries the next provider."""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _user_message(slice_)}]
    for attempt in (1, 2):
        result = llm_gateway.chat("reporter", provider, messages, temperature=TEMPERATURE)
        tally["llm_calls"] += 1
        tally["provider"], tally["model"] = result.provider, result.model
        text = _normalise(_unfence(result.message.content or ""))
        problems = check(text, slice_)
        _emit(emit, "step", f"Reporter ({result.provider}, attempt {attempt}): " + (f"{len(problems)} problem(s)" if problems else "report passed the fact guard"),
              {"provider": result.provider, "model": result.model, "attempt": attempt, "cached": result.cached, "problems": problems},
              latency_ms=result.latency_ms, tokens_in=result.tokens_in, tokens_out=result.tokens_out)
        if not problems:
            return text, []
        tally["problems"] = problems
        messages = messages + [{"role": "assistant", "content": text}, {"role": "user", "content":
                               "Your report has problems:\n" + "\n".join(f"- {p}" for p in problems) + "\nWrite the whole report again and fix them."}]
    return None, problems


def write(state, emit=None):
    """-> (markdown, degraded). degraded is True when the template report was used instead of the LLM's."""
    slice_ = _slice(state, _cut_kinds(state))
    tally = {"llm_calls": 0, "provider": None, "model": None, "problems": []}
    markdown = None
    for provider in llm_gateway.providers_for("reporter"):
        try:
            markdown, _ = _ask(provider, slice_, emit, tally)
        except LLMUnavailable as e:
            _emit(emit, "step", f"Reporter provider {provider} unavailable: {e}", {"provider": provider, "reason": str(e)})
            continue
        break  # a good report, or still bad after the retry: the template decides, not another provider
    degraded = markdown is None
    if degraded:
        markdown = fast_brains.report({**state, "degraded": True})["markdown"]  # the same sections; adds the degraded line
    elif state["degraded"] and DEGRADED_LINE not in markdown:
        markdown = markdown.rstrip() + "\n\n" + DEGRADED_LINE
    markdown = markdown.rstrip() + "\n"
    _emit(emit, "report", f"report from the {'template (fallback)' if degraded else 'LLM'}: {len(markdown)} chars",
          {"source": "template" if degraded else "llm", "provider": tally["provider"], "model": tally["model"],
           "llm_calls": tally["llm_calls"], "retry_problems": tally["problems"], "chars": len(markdown)})
    return markdown, degraded
