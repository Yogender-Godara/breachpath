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
MAX_SANDBOXES = int(os.getenv("MAX_SANDBOXES", "3"))
MAX_GRAPHS = int(os.getenv("MAX_GRAPHS", "20"))
SANDBOX_TTL_HOURS = int(os.getenv("SANDBOX_TTL_HOURS", "24"))
COLLATERAL_RECENT_DAYS = int(os.getenv("COLLATERAL_RECENT_DAYS", "90"))
# LLM (the provider key and the model ids are read by agents/llm_gateway.py)
LLM_PROVIDERS = [p.strip().lower() for p in os.getenv("LLM_PROVIDERS", "groq,openrouter").split(",") if p.strip()]  # the order is the fallback order
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "30"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
LLM_MAX_WAIT_S = float(os.getenv("LLM_MAX_WAIT_S", "20"))  # the longest we wait for a 429's retry-after
LLM_CACHE = os.getenv("LLM_CACHE", "on").strip().lower() in ("on", "true", "1")
LLM_CACHE_TTL_S = int(os.getenv("LLM_CACHE_TTL_S", str(7 * 24 * 3600)))
DEMO_TOKEN = os.getenv("DEMO_TOKEN", "")  # empty = agent mode is disabled
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "10"))
MAX_ACTIVE_AGENT = int(os.getenv("MAX_ACTIVE_AGENT", "2"))
LOCK_TTL_MS = int(os.getenv("LOCK_TTL_MS", "60000"))
LOCK_HEARTBEAT_S = float(os.getenv("LOCK_HEARTBEAT_S", "15"))
