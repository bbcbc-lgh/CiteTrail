"""stdio entry point: ``python -m src.Mcp``.

Speaks newline-delimited JSON-RPC, which is the transport MCP hosts use for
locally spawned servers.
"""

from __future__ import annotations

import asyncio
import json
import sys

from src.Infrastructure.Config import get_settings
from src.Mcp.Server import McpServer


def main() -> int:
    server = McpServer(get_settings())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = asyncio.run(server.handle(request))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
