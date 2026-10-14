"""P1.5: start the hello server as a stdio subprocess, list its tools, call one."""
import asyncio
import os
from mcp import ClientSession, StdioServerParameters, stdio_client

# The subprocess only gets a small default environment, so pass the FalkorDB vars explicitly.
ENV = {k: os.environ[k] for k in ("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_PASSWORD")}
SERVER = StdioServerParameters(command="python", args=["spikes/mcp_hello_server.py"], env=ENV)


async def main():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])
            result = await session.call_tool("ping_graph", {})
            print("is_error:", result.is_error)
            print("structured:", result.structured_content)
            print("text:", [c.text for c in result.content])


asyncio.run(main())
