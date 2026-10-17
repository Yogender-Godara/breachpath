"""P9.5 demo-safety settings: the tests use their own job stream, and a missing data/generated is regenerated. No database needed."""
import os
import pathlib

from data import scenarios
from orchestrator import jobs


def test_the_tests_run_on_their_own_job_stream_not_the_workers():
    assert os.environ["JOBS_STREAM"] == "jobs:test" and jobs.STREAM == "jobs:test"  # tests/conftest.py sets it before any project import
    assert jobs.GROUP == "workers"


def test_the_job_stream_default_is_jobs(monkeypatch):
    import importlib
    monkeypatch.delenv("JOBS_STREAM")
    try:
        assert importlib.reload(jobs).STREAM == "jobs"  # what the worker and the API use outside the tests
    finally:
        monkeypatch.setenv("JOBS_STREAM", "jobs:test")
        importlib.reload(jobs)


def test_generated_files_missing_looks_for_background_and_every_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(scenarios, "OUT_DIR", tmp_path)
    assert scenarios.generated_files_missing() is True
    for name in ["background.json", *(f"{sid}.json" for sid in scenarios.ALL_IDS)]:
        (tmp_path / name).write_text("{}")
    assert scenarios.generated_files_missing() is False
    (tmp_path / "s8.json").unlink()
    assert scenarios.generated_files_missing() is True


def test_generate_writes_the_scenario_the_background_and_the_answer_key(tmp_path, monkeypatch):
    monkeypatch.setattr(scenarios, "OUT_DIR", tmp_path / "generated")
    monkeypatch.setattr(scenarios, "ANSWER_DIR", tmp_path / "keys")
    scenarios.generate(["s1"])
    assert sorted(p.name for p in (tmp_path / "generated").iterdir()) == ["background.json", "s1.json"]
    assert [p.name for p in (tmp_path / "keys").iterdir()] == ["s1.json"]
    real = pathlib.Path(scenarios.__file__).parent / "answer_keys" / "s1.json"
    assert (tmp_path / "keys" / "s1.json").read_text() == real.read_text()  # deterministic: identical to the committed answer key
