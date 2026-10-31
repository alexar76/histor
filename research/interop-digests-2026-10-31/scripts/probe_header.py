"""tools/list with and without the MCP-Protocol-Version header after one initialize:
python probe_header.py URL REQUESTED_VERSION."""

import sys

from mcp_probe import list_tools

url, asked = sys.argv[1], sys.argv[2]
out = []
for header in (True, False):
    negotiated, _, tools = list_tools(url, asked, header=header, all_pages=False)
    with_output = sum(isinstance(t.get("outputSchema"), dict) for t in tools)
    titled = sum(bool(t.get("title")) for t in tools)
    out.append(f"{'header' if header else 'no header'}: outputSchema {with_output}/{len(tools)}, title {titled}")
print(url, "asked", asked, "negotiated", negotiated, "|", " | ".join(out))
