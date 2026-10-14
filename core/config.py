"""Settings read from the environment (defaults: docs/03_LLD.md section 12)."""
import os

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "falkordb")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD")

MAX_HOPS = int(os.getenv("MAX_HOPS", "6"))
TOP_K_PATHS = int(os.getenv("TOP_K_PATHS", "20"))
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "2000"))
NEED_USAGE_DAYS = int(os.getenv("NEED_USAGE_DAYS", "30"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
MAX_SIMULATIONS = int(os.getenv("MAX_SIMULATIONS", "6"))
