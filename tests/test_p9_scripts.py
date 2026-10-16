"""The P9 helper scripts: the port check (plain sockets) and the latency statistics. No Docker, no database."""
import socket

from scripts import latency, port_check


def test_is_open_tells_a_listening_port_from_a_closed_one():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert port_check.is_open("127.0.0.1", port) is True
    assert port_check.is_open("127.0.0.1", port, timeout=0.3) is False  # closed again


def test_the_port_check_passes_only_when_just_the_ui_port_answers(monkeypatch, capsys):
    monkeypatch.setattr(port_check, "is_open", lambda host, port, timeout=1.5: port == 8501)
    assert port_check.main([]) == 0
    assert "PASS: only the UI port answers" in capsys.readouterr().out
    monkeypatch.setattr(port_check, "is_open", lambda host, port, timeout=1.5: port in (8501, 6379))  # the database leaks
    assert port_check.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL: 1 port(s)" in out and "6379  | falkordb (Redis)            | closed   | yes     | WRONG" in out
    monkeypatch.setattr(port_check, "is_open", lambda host, port, timeout=1.5: False)  # the UI is down too
    assert port_check.main(["--ui-port", "8601"]) == 1


def test_percentiles_are_nearest_rank_and_stats_are_rounded():
    assert latency.pct([5, 1, 4, 2, 3], 50) == 3 and latency.pct([5, 1, 4, 2, 3], 95) == 5 and latency.pct([7], 95) == 7
    assert latency.pct(list(range(1, 101)), 95) == 95
    assert latency.stats([10.04, 20.0, 30.0]) == {"n": 3, "p50": 20.0, "p95": 30.0, "max": 30.0}
