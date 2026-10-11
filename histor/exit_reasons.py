"""Why a package that installed did not answer, read from what it printed as it exited.

Most of them are not broken: they want an API key, or a start argument such as ``serve``, that a
user's client config would give them. The reason is shown with the package, and it is also what
the next crawl acts on — the sandbox starts the package again with a placeholder for each named
variable (``histor/sandbox/histor_observe.py`` makes the values; HISTOR only sends names).

The text is the package's, so nothing here trusts it: names are matched against a strict shape
and a deny list (the observer refuses the same ones again), and at most a handful are taken.
"""

from __future__ import annotations

import re
from typing import Any

ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
ENV_DENY = re.compile(r"^(PATH|HOME|USER|SHELL|PWD|TMPDIR|TERM|LANG|LC_[A-Z_]+|NODE_[A-Z_]+|NPM_[A-Z_]+|PYTHON[A-Z_]*|PIP_[A-Z_]+"
                      r"|UV_[A-Z_]+|LD_[A-Z_]+|DYLD_[A-Z_]+|SSL_[A-Z_]+|REQUESTS_CA_BUNDLE|CURL_CA_BUNDLE|HISTOR_[A-Z_]+"
                      r"|(?:HTTPS?|ALL|NO)_PROXY|GIT_[A-Z_]+|DOCKER_[A-Z_]+)$")
# Words that look like a variable but are an error's own vocabulary.
NOT_CONFIG = re.compile(r"^(ERR_[A-Z_]+|E[A-Z]+_[A-Z_]+|HTTP_[0-9]+|MCP_[A-Z_]*ERROR|[A-Z_]*_ERROR|[A-Z_]*_EXCEPTION|SIG[A-Z]+|UTF_8|ISO_[0-9_]+)$")
CONFIG_LAST = {"KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "PAT", "ID", "URL", "URI", "ENDPOINT", "HOST", "BASE",
               "SERVER", "DOMAIN", "PATH", "DIR", "DIRECTORY", "FILE", "ROOT", "WORKSPACE", "EMAIL", "USER", "USERNAME",
               "ACCOUNT", "PROJECT", "REGION", "ORG", "ORGANIZATION", "TENANT", "SID", "DSN", "CREDENTIALS", "AUTH", "PORT"}
MAX_ENV = 8
MAX_ARGS = 6

KEY_WORDS = re.compile(
    r"(api[ _-]?key|access[ _-]?key|token|secret|credential|password|environment variable|env(ironment)? var"
    r"|is not set|not set\b|is required|are required|missing required|must be set|not configured|no .{0,20}configured"
    r"|\b(?:set|define|export|provide)\s+`?[A-Z][A-Z0-9]*_[A-Z0-9_]+|環境變數|环境变量|未設定|未设置)",
    re.I,
)
# Asking for an account or a setup it does not name: still a configuration, not a crash.
AUTH_OR_SETUP = re.compile(
    r"\b(?:session cookie|not authenticated|unauthenticated|authentication (?:is )?required|log ?in required"
    r"|please (?:log|sign) ?in|no (?:valid )?credentials|configuration error|invalid configuration"
    r"|config(?:uration)? (?:file )?(?:is )?(?:missing|not found)|expected string, received undefined"
    r"|isn't set up yet|is not set up|run [`'\"]?\S+ init[`'\"]? first)",
    re.I,
)
# "Node 24+ required, found 22.23.3", "requires Node.js >= 24", npm's EBADENGINE.
NODE_NEEDED = re.compile(
    r"node(?:\.js)?\s*(?:version\s*)?v?(\d{2})(?:\.\d+)*\s*(?:\+|or (?:newer|higher|later|above))\s*(?:is\s*)?required"
    r"|requires? node(?:\.js)?\s*(?:version\s*)?(?:>=?\s*)?v?(\d{2})"
    r"|engine \"node\" is incompatible[^\n]{0,80}?(\d{2})",
    re.I,
)
# The newest Node major the sandbox has an image for (histor_observe.RUNTIMES["npm"]["node24"]).
NODE_RUNTIMES = {24: "node24"}
SANDBOX_NODE = 22  # the default npm image, node:22-alpine
# The package itself says how to start it: "Run 'xcodebuildmcp mcp' to start the MCP server".
RUN_HINT = re.compile(r"""(?:run|use|try|start (?:it )?with)\s+[`'"]?(?:npx\s+(?:-y\s+)?)?[@\w./-]+\s+(stdio|mcp|serve|server|start)\b[`'"]?""", re.I)
# "You must specify one of --stdio, --sse, or --streamableHttp", "--transport stdio".
FLAG_HINT = re.compile(r"(?:specify|pass|use|choose|requires?|needs?|one of)[^.\n]{0,60}?(--stdio)\b|(--transport)[ =]stdio\b", re.I)
USAGE = re.compile(r"(^|\s)usage:|the following arguments are required|missing argument|missing command|no such command"
                   r"|command[- ]line argument|required option '--"
                   r"|requires a subcommand|specify a (sub)?command|子命令", re.I)
OTHER_OS = re.compile(r"\b(comtypes|pywin32|win32api|win32com|winreg|pythoncom|AppKit|pyobjc|Quartz)\b|only (?:supported|works|runs) on (?:Windows|macOS)"
                      r"|requires (?:Windows|macOS)|not supported on (?:Linux|this platform)", re.I)
SDK = re.compile(r"mcp\.server\.fastmcp|This is mcp 2\.x|cannot import name 'FastMCP'|from mcp\.server\.fastmcp", re.I)
NETWORK = re.compile(r"name resolution|ENOTFOUND|EAI_AGAIN|getaddrinfo|ECONNREFUSED|fetch failed|Network is unreachable"
                     r"|ConnectError|connection refused|Failed to establish a new connection", re.I)
# A subcommand that starts the server, in the order a usage line is searched for one.
START_WORDS = ("stdio", "mcp", "serve", "server", "start", "run")
CHOICES = re.compile(r"(?:(--?[a-z][a-z0-9-]{0,30})[ =])?\{([a-z0-9][a-z0-9_-]*(?:,[a-z0-9][a-z0-9_-]*)+)\}")
COMMAND_LINE = re.compile(r"^\s{2,}([a-z][a-z0-9_-]{1,30})\s{2,}\S", re.M)
# yargs: "  index.js stdio   Start the server using stdio." — the program name, then the command.
PROGRAM_COMMAND = re.compile(r"^\s*\S+\.(?:js|mjs|cjs)\s+(stdio|mcp|serve|server|start)\s{2,}\S", re.M)


def env_names(text: str) -> list[str]:
    names: list[str] = []
    for m in ENV_NAME.finditer(text):
        name = m.group(0)
        if len(name) > 64 or ENV_DENY.match(name) or NOT_CONFIG.match(name) or name in names:
            continue
        if name.rsplit("_", 1)[-1] in CONFIG_LAST or any(w in name for w in ("KEY", "TOKEN", "SECRET")):
            names.append(name)
        if len(names) == MAX_ENV:
            break
    return sorted(names)


def start_argument(text: str) -> str | None:
    """The subcommand (``serve``) or option (``--transport=stdio``) a usage text offers that would
    start the server over stdio, if it names one."""
    hint = RUN_HINT.search(text)
    if hint:
        return hint.group(1).lower()
    flag = FLAG_HINT.search(text)
    if flag:
        return "--stdio" if flag.group(1) else "--transport=stdio"
    offered: dict[str, str] = {}
    for word in PROGRAM_COMMAND.findall(text):
        offered.setdefault(word, word)
    for option, group in CHOICES.findall(text):
        for choice in group.split(","):
            offered.setdefault(choice, f"{option}={choice}" if option.startswith("--") else choice)
    for word in COMMAND_LINE.findall(text):
        offered.setdefault(word, word)
    for word in START_WORDS:
        if word in offered:
            return offered[word]
    return None


def classify(status: str, detail: str | None) -> dict[str, Any] | None:
    """{kind, env?, args?} for a run that exited before answering; None for any other status.

    kind: needs-config (a key or setting it names, or does not), needs-arguments (a usage text),
    sdk-incompatible (built against the MCP SDK 1.x and run with 2.x), needs-other-os (Windows- or
    macOS-only libraries; the sandbox is Linux), needs-newer-node (with the major it named),
    needs-network (it reached
    for the internet while starting; the sandbox has none), silent, crashed.
    """
    if status != "exited":
        return None
    text = detail or ""
    if not text.strip():
        return {"kind": "silent"}
    if SDK.search(text):
        return {"kind": "sdk-incompatible"}
    if OTHER_OS.search(text):
        return {"kind": "needs-other-os"}
    node = NODE_NEEDED.search(text)
    major = int(next(g for g in node.groups() if g)) if node else 0
    if major > SANDBOX_NODE:
        # Below that it is not "newer": a Python package that shells out to npx wants Node at all.
        return {"kind": "needs-newer-node", "node": major}
    names = env_names(text) if KEY_WORDS.search(text) else []
    arg = start_argument(text)
    extra = {**({"env": names} if names else {}), **({"args": [arg]} if arg else {})}
    if USAGE.search(text) or (arg and not names):
        return {"kind": "needs-arguments", **extra}
    if names or KEY_WORDS.search(text) or AUTH_OR_SETUP.search(text):
        return {"kind": "needs-config", **extra}
    if NETWORK.search(text):
        return {"kind": "needs-network"}
    return {"kind": "crashed"}


def declared_needs(package: dict[str, Any]) -> dict[str, list[str]]:
    """What a registry entry says the package must be started with: required variables, and
    positional start arguments that carry a plain value. Values are never taken from the entry —
    only names and literal subcommands the observer will also check."""
    env: list[str] = []
    for var in package.get("environmentVariables") or []:
        if not isinstance(var, dict) or not var.get("isRequired"):
            continue
        name = var.get("name")
        if isinstance(name, str) and ENV_NAME.fullmatch(name) and not ENV_DENY.match(name) and name not in env:
            env.append(name)
    args: list[str] = []
    for arg in package.get("packageArguments") or []:
        if not isinstance(arg, dict) or arg.get("type") != "positional" or not arg.get("isRequired", True):
            continue
        value = arg.get("value") or arg.get("default")
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", value):
            args.append(value)
    out: dict[str, list[str]] = {}
    if env:
        out["env"] = sorted(env)[:MAX_ENV]
    if args:
        out["args"] = args[:MAX_ARGS]
    return out
