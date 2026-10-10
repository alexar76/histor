"""Package names made to be mistaken for popular ones, and the marks of a stolen publishing token."""

from __future__ import annotations

from histor.lookalike import LookalikeIndex, default_index, skeleton
from histor.packages import version_signals

POPULAR = [
    ("npm:@modelcontextprotocol/server-github", 111_195),
    ("npm:@modelcontextprotocol/server-memory", 142_129),
    ("npm:@upstash/context7-mcp", 838_664),
    ("npm:mysql-mcp-server", 20_000),
    ("npm:mssql-mcp-server", 15_000),
    ("pypi:mcp-server-fetch", 112_394),
    # a generic name: many scopes, nobody's brand
    ("npm:@sentry/mcp-server", 99_513), ("npm:@a/mcp-server", 5), ("npm:@b/mcp-server", 3), ("npm:@c/mcp-server", 1),
    ("npm:@d/mcp-servers", 2),
]


def test_names_close_to_a_popular_one_are_flagged_with_how():
    ix = LookalikeIndex(POPULAR)
    assert ix.check("npm:@modelcontextprotocol/server-githb") == {
        "of": "npm:@modelcontextprotocol/server-github", "weekly": 111_195, "how": "one character away", "ownWeekly": 0}
    assert ix.check("npm:@mcp-official/server-memory")["how"] == "the same name under another scope"
    assert ix.check("npm:server-memory")["of"] == "npm:@modelcontextprotocol/server-memory"
    assert ix.check("npm:context7_mcp")["how"] == "the same name with other separators or look-alike characters"
    assert ix.check("pypi:mcp-server-fetch2")["of"] == "pypi:mcp-server-fetch"
    assert ix.check("pypi:mcp-server-fetch2")["how"] == "one character away"


def test_originals_siblings_and_generic_names_are_left_alone():
    ix = LookalikeIndex(POPULAR)
    assert ix.check("npm:@modelcontextprotocol/server-github") is None, "the original"
    assert ix.check("npm:mssql-mcp-server") is None, "two popular siblings one letter apart never accuse each other"
    assert ix.check("npm:@e/mcp-server") is None, "mcp-server is generic: many projects honestly use it"
    assert ix.check("npm:@f/mcp-servers") is None, "nor one letter from a generic name"
    assert ix.check("npm:@g/x-mcp-server") is None
    assert ix.check("npm:totally-different-name") is None
    assert ix.check("npm:mcp-server-fetch2") is None, "registries are compared with themselves only"
    assert skeleton("Mcp_Server.F0rm1") == "mcpserverforml"


def test_the_shipped_index_knows_the_reference_servers():
    ix = default_index()
    assert ix.check("npm:@modelcontextprotocol/server-githb")["of"] == "npm:@modelcontextprotocol/server-github"
    assert ix.check("npm:@modelcontextprotocol/server-memory") is None


def facts(version, provenance=True, publisher="trusted publisher: github", scripts=(), deps=("@modelcontextprotocol/sdk",)):
    return {"version": version, "provenance": provenance, "publisher": publisher, "installScripts": list(scripts), "dependencies": list(deps)}


def test_version_signals_name_the_marks_of_a_stolen_token():
    quiet = version_signals(facts("1.0.1"), facts("1.0.0"))
    assert quiet["flags"] == [] and quiet["previousVersion"] == "1.0.0" and quiet["provenance"] is True
    hijack = version_signals(facts("1.0.2", provenance=False, publisher="someone-else", scripts=("postinstall",),
                                   deps=("@modelcontextprotocol/sdk", "node-fetch-mail")), facts("1.0.1"))
    assert hijack["flags"] == ["provenance-lost", "publisher-changed", "install-scripts-added", "new-dependencies"]
    assert hijack["previousPublisher"] == "trusted publisher: github" and hijack["newDependencies"] == ["node-fetch-mail"]
    first = version_signals(facts("0.1.0", provenance=False, scripts=("install",)), None)
    assert first["flags"] == ["install-scripts"] and "previousVersion" not in first
    assert version_signals(None, facts("1.0.0")) is None
