"""Page sizes of a server's tools/list: python probe_pages.py URL [VERSION]."""

import sys

from mcp_probe import list_tools

negotiated, pages, tools = list_tools(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "2025-06-18")
for number, size in enumerate(pages, 1):
    print(f"page {number}: {size} tools")
print("protocol", negotiated, "total", len(tools))
