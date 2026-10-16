"""docker-compose.yml (P9.1 / P9.2): only `ui` publishes a port, every service restarts and has a healthcheck, the UI waits for a healthy API.
No Docker needed: the file is parsed."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


def test_only_the_ui_publishes_a_port():
    published = {name: s["ports"] for name, s in SERVICES.items() if "ports" in s}
    assert list(published) == ["ui"], published  # api and falkordb use `expose`; the dev ports live in docker-compose.dev.yml
    assert published["ui"] == ["${UI_PORT:-8501}:8501"]
    assert SERVICES["api"]["expose"] == ["8000"] and SERVICES["falkordb"]["expose"] == ["6379"]


def test_the_dev_file_is_the_only_place_with_the_other_ports():
    dev = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))["services"]
    assert dev["falkordb"]["ports"] and dev["api"]["ports"]
    assert all(p.startswith("127.0.0.1:") for s in ("falkordb", "api") for p in dev[s]["ports"])  # loopback only


def test_every_service_restarts_has_a_healthcheck_and_the_data_volume_exists():
    assert set(SERVICES) == {"falkordb", "api", "worker", "ui"}  # 4 containers (CLAUDE.md)
    for name, s in SERVICES.items():
        assert s["restart"] == "unless-stopped", name
        assert s["healthcheck"]["test"], name
    assert SERVICES["falkordb"]["volumes"] == ["falkordb-data:/data"] and "falkordb-data" in COMPOSE["volumes"]


def test_health_commands_use_python_not_curl():
    api, ui, worker = (" ".join(SERVICES[n]["healthcheck"]["test"]) for n in ("api", "ui", "worker"))
    assert "/readyz" in api and "/_stcore/health" in ui and "worker:alive:" in worker
    assert all("curl" not in c and "python" in c for c in (api, ui, worker))  # the slim image has no curl


def test_the_ui_waits_for_a_healthy_api_and_the_worker_for_the_database():
    assert SERVICES["ui"]["depends_on"]["api"] == {"condition": "service_healthy"}
    assert SERVICES["api"]["depends_on"]["falkordb"] == {"condition": "service_healthy"}
    assert SERVICES["worker"]["depends_on"]["falkordb"] == {"condition": "service_healthy"}
