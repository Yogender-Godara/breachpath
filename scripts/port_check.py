"""Which ports of the stack answer from the host? (P9.1: with ONLY docker-compose.yml up, just the UI port may.)

Run: python scripts/port_check.py [--host 127.0.0.1] [--ui-port 8501]
Plain socket connects (no packages). Exit 0 when the UI port answers and none of the internal ports does.
"""
import argparse
import socket
import sys


def is_open(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ui-port", type=int, default=8501)
    args = ap.parse_args(argv)
    rows = [(args.ui_port, "ui (Streamlit)", True), (6379, "falkordb (Redis)", False), (8000, "api (FastAPI)", False),
            (3300, "falkordb browser (dev only)", False)]
    print(f"host {args.host}\nport  | service                     | expected | answers | result")
    bad = 0
    for port, name, should in rows:
        answers = is_open(args.host, port)
        ok = answers == should
        bad += not ok
        print(f"{port:<5} | {name:<27} | {'open' if should else 'closed':<8} | {'yes' if answers else 'no':<7} | {'ok' if ok else 'WRONG'}")
    print("PASS: only the UI port answers" if not bad else f"FAIL: {bad} port(s) are not as expected")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
