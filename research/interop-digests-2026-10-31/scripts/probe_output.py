"""How many tools carry outputSchema under one requested version: python probe_output.py URL VERSION."""

import sys

from mcp_probe import list_tools

url, version = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "2025-06-18"
negotiated, _, tools = list_tools(url, version, all_pages=False)
with_output = sum(isinstance(t.get("outputSchema"), dict) for t in tools)
print(url, negotiated, "tools", len(tools), "with outputSchema", with_output)
