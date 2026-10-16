"""How to start the MCP server as a stdio subprocess. Shared by the worker (P6.6) and the agent loop (P6.3)."""
import os
import shlex
from pathlib import Path

from mcp import StdioServerParameters

REPO_ROOT = Path(__file__).resolve().parent.parent
# The subprocess does NOT inherit the parent's environment (verified P1.5): forward these explicitly, only if set.
FORWARDED_ENV = ["FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_PASSWORD", "MAX_HOPS", "TOP_K_PATHS", "QUERY_TIMEOUT_MS",
                 "NEED_USAGE_DAYS", "MAX_SIMULATIONS", "SIMS_RESERVED_FOR_CODE", "MAX_SANDBOXES", "MAX_GRAPHS", "COLLATERAL_RECENT_DAYS",
                 "SANDBOX_TTL_HOURS", "PATH"]


def server_params(profile="full"):
    command, *args = shlex.split(os.getenv("MCP_COMMAND", "python -m mcp_server.server"))
    env = {name: os.environ[name] for name in FORWARDED_ENV if name in os.environ}
    env["MCP_PROFILE"] = profile
    return StdioServerParameters(command=command, args=args, env=env, cwd=REPO_ROOT)
