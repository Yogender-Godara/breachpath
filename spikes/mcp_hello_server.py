"""P1.5: smallest MCP server over stdio, one tool that talks to FalkorDB."""
import os
from falkordb import FalkorDB
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("hello")


@mcp.tool()
def ping_graph() -> dict[str, int]:
    """Run RETURN 1 on a throwaway FalkorDB graph and delete it."""
    db = FalkorDB(host=os.environ["FALKORDB_HOST"], port=int(os.environ["FALKORDB_PORT"]),
                  password=os.environ["FALKORDB_PASSWORD"])
    g = db.select_graph("mcp_ping")
    ok = g.query("RETURN 1 AS ok").result_set[0][0]
    g.delete()
    return {"ok": ok}


if __name__ == "__main__":
    mcp.run(transport="stdio")
