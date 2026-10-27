"""Small, reviewed launch defaults for packages whose bare bin is not a stdio server.

These are HISTOR's test conditions, not publisher-supplied credentials. Explicit registry or
previously supplied arguments take precedence. Network access is never enabled by a profile.
"""

from __future__ import annotations

# The standalone observer expands this exact argument into a per-run placeholder. Never put a
# real credential in this table. Keep the token in sandbox/histor_observe.py in sync.
PLACEHOLDER_TOKEN = "histor-placeholder-token"
SANDBOX_DIRECTORY = "/work/app"

# Sources: modelcontextprotocol/servers src/filesystem README; microsoft/azure-devops-mcp
# src/auth.ts and src/index.ts; launchdarkly/mcp-server src/mcp-server/cli/start/command.ts;
# GLips/Figma-Context-MCP README. Checked 2026-10-27; versions may change their requirements.
_PROFILES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "npm:@modelcontextprotocol/server-filesystem": ((), (SANDBOX_DIRECTORY,)),
    "npm:@azure-devops/mcp": (("ADO_MCP_AUTH_TOKEN",),
                              ("histor-sandbox", "--authentication", "envvar")),
    "npm:@launchdarkly/mcp-server": ((), ("start", "--transport=stdio", "--api-key", PLACEHOLDER_TOKEN)),
    "npm:figma-developer-mcp": (("FIGMA_API_KEY",), ("--stdio",)),
}


def package_launch_defaults(endpoint: str) -> dict[str, list[str]]:
    """Fresh configuration lists, only for exact package identities in the reviewed table."""
    env, args = _PROFILES.get(endpoint, ((), ()))
    return {key: list(values) for key, values in (("env", env), ("args", args)) if values}
