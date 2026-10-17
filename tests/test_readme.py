"""README.md (P10.1): the links and images exist, Track 1 is named, the AI-usage placeholder is untouched, no key-like strings,
and every command in it points at a module or file that exists. No database, no Docker."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "README.md").read_text(encoding="utf-8")
HEADINGS = ["Screenshots", "Architecture", "Graph data model", "Cypher queries and graph algorithms used", "Why FalkorDB is not decoration",
            "MCP and agents", "Features", "Evaluation", "Latency", "Security scope", "Limitations", "Setup (local)", "Repo map", "AI usage"]


def test_every_relative_link_and_image_exists():
    targets = re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", TEXT)
    assert len(targets) >= 15  # the screenshots, the design docs, the eval files
    missing = [t for t in targets if not t.startswith(("http://", "https://", "mailto:", "#")) and not (ROOT / t.split("#")[0]).exists()]
    assert missing == []


def test_the_screenshots_are_embedded():
    images = re.findall(r"!\[[^\]]*\]\(([^)\s]+\.png)\)", TEXT)
    assert images == ["docs/screenshots/s2_attack_graph.png", "docs/screenshots/s2_fix_and_proof.png", "docs/screenshots/s8_fix_and_proof.png"]


def test_the_hackathon_and_track_1_are_named_at_the_top():
    top = TEXT[:600]
    assert "Graph Hacks" in top and "WeMakeDevs" in top and "FalkorDB" in top
    assert "Track 1" in top and "Agents That Act on Connected Data" in top


def test_every_required_section_exists_in_order():
    found = [h for h in re.findall(r"^## (.+)$", TEXT, re.MULTILINE)]
    positions = [next(i for i, h in enumerate(found) if h.startswith(want)) for want in HEADINGS]
    assert positions == sorted(positions), found


def test_the_ai_usage_section_is_only_the_placeholder_for_the_author():
    section = TEXT.split("## AI usage", 1)[1]
    assert section.strip() == "_To be written by the author before submission._"
    assert "## AI usage" not in section  # the last section


def test_it_says_3_llm_agents_and_2_code_workers_never_5_agents():
    assert "3 LLM agents (Investigator, Planner, Reporter) + 2 code workers (Executor, Verifier) + an Orchestrator" in TEXT
    assert not re.search(r"\b5 agents\b", TEXT, re.IGNORECASE)


def test_no_key_like_strings():
    assert not re.search(r"sk-or-[A-Za-z0-9_-]{10,}|gsk_[A-Za-z0-9]{10,}|Bearer [A-Za-z0-9._-]{20,}", TEXT)


def _repo_module_exists(module):
    base = ROOT.joinpath(*module.split("."))
    if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file() or (base / "__main__.py").is_file():
        return True
    return importlib.util.find_spec(module.split(".")[0]) is not None and module.split(".")[0] not in {p.name for p in ROOT.iterdir()}  # pytest, pip


def test_every_command_names_a_module_or_file_that_exists():
    modules = re.findall(r"python -m ([\w.]+)", TEXT)
    scripts = re.findall(r"python (scripts/[\w./]+\.py)", TEXT)
    compose_files = set(re.findall(r"-f (docker-compose[\w.]*\.yml)", TEXT))
    assert {"mcp_server.server", "eval.run_eval", "scripts.check_fast_mode", "scripts.latency", "pytest"} <= set(modules)
    assert [m for m in modules if not _repo_module_exists(m)] == []
    assert scripts and [s for s in scripts if not (ROOT / s).is_file()] == []
    assert compose_files == {"docker-compose.yml", "docker-compose.dev.yml"} and all((ROOT / f).is_file() for f in compose_files)
    for needed in ("env.example", "requirements.txt"):
        assert needed in TEXT and (ROOT / needed).is_file()


def test_the_services_and_ports_it_names_are_the_real_ones():
    import yaml
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    assert set(services) == {"falkordb", "api", "worker", "ui"}
    assert "${UI_PORT:-8501}" in services["ui"]["ports"][0] and "UI_PORT" in TEXT and "8501" in TEXT
    env = (ROOT / "env.example").read_text(encoding="utf-8")
    for name in ("FALKORDB_PASSWORD", "DEMO_TOKEN", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        assert name in TEXT and f"{name}=" in env


def test_the_17_mcp_tools_are_the_real_ones():
    from mcp_server import server
    names = [f.__name__ for f in server.READ_TOOLS + server.FULL_ONLY_TOOLS]
    assert len(names) == 17 and len(server.READ_TOOLS) == 10 and len(server.FULL_ONLY_TOOLS) == 7
    assert all(f"`{n}`" in TEXT for n in names)


def test_the_eval_numbers_in_the_readme_come_from_the_saved_results():
    import json
    rows = json.loads((ROOT / "eval/results/latest.json").read_text(encoding="utf-8"))
    assert rows["meta"]["commit"] in TEXT and rows["meta"]["date"][:10] in TEXT
    summary = rows["summary"]
    for needle in ("B-disable: a safe fix in 4 of 7 scenarios, 6 business need(s) broken", "B-betweenness: a safe fix in 6 of 7 scenarios, 1 business need(s) broken in total, weighted collateral 12",
                   "B-frequency: a safe fix in 4 of 7 scenarios, 4 business need(s) broken", "B-LLM-JSON: a safe fix in 7 of 7 scenarios, 0 business need(s) broken in total, weighted collateral 12"):
        assert needle in summary
    assert "| B-disable | 4 of 7 | 6 | 0 | 1.00 |" in TEXT and "| B-betweenness | 6 of 7 | 1 | 12 | 0.00 |" in TEXT
    assert "| B-frequency | 4 of 7 | 4 | 0 | 0.00 |" in TEXT and "| B-LLM-JSON | 7 of 7 | 0 | 12 | 0.00 |" in TEXT
