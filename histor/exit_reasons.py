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
    r"|isn't set up yet|is not set up|run [`'\"]?\S+ init[`'\"]? first|not logged in|log ?in first|run [`'\"]?\S+ (?:auth )?login)",
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
SANDBOX_NODE = 22  # the default npm image, histor-node:22 (node:22-bookworm-slim)
# The package itself says how to start it: "Run 'xcodebuildmcp mcp' to start the MCP server".
RUN_HINT = re.compile(r"""(?:run|use|try|start (?:it )?with)\s+[`'"]?(?:npx\s+(?:-y\s+)?)?[@\w./-]+\s+(stdio|mcp|serve|server|start)\b[`'"]?""", re.I)
# "You must specify one of --stdio, --sse, or --streamableHttp", "--transport stdio".
FLAG_HINT = re.compile(r"(?:specify|pass|use|choose|requires?|needs?|one of)[^.\n]{0,60}?(--stdio)\b|(--transport)[ =]stdio\b", re.I)
USAGE = re.compile(r"(^|\s)usage:|the following arguments are required|missing argument|missing command|no such command"
                   r"|command[- ]line argument|required option '--|Show this message and exit|^\s*Commands:\s*$|as the first argument"
                   r"|arguments? must be provided|provide (?:the |a |an )?[\w -]{0,40}(?:argument|command|path) (?:after|as|with)"
                   r"|requires a subcommand|specify a (sub)?command|子命令", re.I)
OTHER_OS = re.compile(r"\b(comtypes|pywin32|win32api|win32com|winreg|pythoncom|AppKit|pyobjc|Quartz)\b|only (?:supported|works|runs) on (?:Windows|macOS)"
                      r"|requires (?:Windows|macOS)|not supported on (?:Linux|this platform)|is (?:Windows|macOS)[- ]only", re.I)
# Native code built for glibc, run on Alpine's musl: a prebuilt binary or N-API addon that will
# not load. It runs again on a Debian (glibc) image, the one most users' Linux has.
GLIBC = re.compile(r"\bmusl\b|Alpine|\bglibc\b|GLIBC_\d|ld-linux-x86-64|linux-x64-gnu|Error loading shared library"
                   r"|Error relocating|Cannot find native binding", re.I)
# An interactive program: it wants a keyboard, and an MCP client gives it a pipe.
TERMINAL = re.compile(r"needs a terminal|not a TTY|\bisatty\b|interactive terminal|Raw mode is not supported|/dev/tty"
                      r"|stdin is not a (?:tty|terminal)|requires an interactive", re.I)
# Another program it starts or talks to, which is not part of the package: git, Java, Docker, a
# desktop app, a CLI it wraps. Its users install it themselves; the sandbox has only the runtime.
PROGRAM = re.compile(r"spawn(?:Sync)? \S+ ENOENT|executable (?:was )?not found|command not found|not found (?:on|in) (?:your )?PATH"
                     r"|on PATH\b|is not installed|isn't installed|not installed on this|Install (?:a JDK|Java|Docker|git|bubblewrap|Node\.js)"
                     r"|needs Java|requires? (?:Java|a JDK|Docker|Node\.js)|Bun runtime required|Install Bun|No such file or directory: '(?:git|docker|java|uvx?|npx|node|hg|ssh|ffmpeg|kubectl|gh|code)'"
                     r"|is not running\. Start it|not running on this machine|no supported [\w -]{0,30}(?:CLI|app) (?:detected|found)", re.I)
# A database or service on this machine it connects to at start (no internet involved).
SERVICE = re.compile(r"ECONNREFUSED (?:127\.0\.0\.1|::1|localhost)|connect(?:ion)? (?:to|refused)[^\n]{0,40}(?:127\.0\.0\.1|localhost):\d+"
                     r"|Can't connect to (?:local )?MySQL|could not connect to server|psycopg\w*\.OperationalError|Is the server running"
                     r"|redis\.exceptions\.ConnectionError|ServerSelectionTimeoutError", re.I)
# The package cannot run as published: its own file or a dependency it did not declare is
# missing, it ships TypeScript sources, a syntax error, an entry point that is not callable.
BROKEN = re.compile(r"ERR_MODULE_NOT_FOUND|Cannot find (?:module|package) '|ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING"
                    r"|ERR_UNKNOWN_FILE_EXTENSION|ERR_UNSUPPORTED_DIR_IMPORT|ERR_PACKAGE_PATH_NOT_EXPORTED|^\s*SyntaxError\b"
                    r"|ReferenceError: \w+ is not defined|ModuleNotFoundError|No module named|^\s*ImportError\b"
                    r"|coroutine '?\w+'? was never awaited|__require is not a function|Dynamic require of|has no attribute 'main'", re.M)
# A server that started an HTTP listener instead of answering on stdin: the run times out. Asked
# again with --transport=stdio, it starts on stdio or prints a usage text that names the option.
HTTP_START = re.compile(r"Uvicorn running|Started server process|Application startup complete|\blistening (?:on|at)\b"
                        r"|running (?:on|at) https?://|https?://(?:0\.0\.0\.0|127\.0\.0\.1|localhost|\[::\]):\d+"
                        r"|StreamableHTTP session manager started|transport '(?:streamable-http|sse|http)'", re.I)
EXIT_CODE = re.compile(r"^\[exit code (\d+)\]\s*")
NOT_WORDS = re.compile(r"\S*[/\\]\S*|(?:Invalid or )?[Uu]nexpected token|token .{0,20} is not valid JSON")
# Written for MCP SDK 1.x: FastMCP moved, and the low-level Server lost its decorators in 2.x —
# "'Server' object has no attribute 'list_tools'", or the decorator line the import died on.
SDK = re.compile(r"mcp\.server\.fastmcp|This is mcp 2\.x|cannot import name 'FastMCP'|from mcp\.server\.fastmcp"
                 r"|'Server' object has no attribute '(?:list_tools|call_tool|list_resources|read_resource|list_prompts|get_prompt)'"
                 r"|^\s*@\w+\.(?:list_tools|call_tool|list_resources|read_resource|list_prompts|get_prompt)\(\)?\s*(?:#.*)?$", re.I | re.M)
# The sandbox's traps answer every address with a placeholder: a server that downloads a binary
# or validates its key online at start gets that answer back and stops ("histor-synthetic").
NETWORK = re.compile(r"name resolution|ENOTFOUND|EAI_AGAIN|getaddrinfo|ECONNREFUSED|fetch failed|Network is unreachable"
                     r"|ConnectError|connection refused|Failed to establish a new connection|histor-synthetic|histor-trap\.invalid"
                     r"|\"ErrorCode\",\s*\"Message\",\s*\"MessageID\"|ErrorCode, Message, MessageID"
                     r"|checksum mismatch|SHA\d*SUMS|\bdownloading\b", re.I)
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
    """{kind, env?, args?, code?} for a run that exited before answering, or one that timed out
    serving HTTP instead of stdio; None for any other status.

    kind: needs-config (a key or setting it names, or does not), needs-arguments (a usage text),
    sdk-incompatible (built against the MCP SDK 1.x and run with 2.x), needs-other-os (Windows- or
    macOS-only libraries; the sandbox is Linux), needs-newer-node (with the major it named),
    needs-glibc (native code built for glibc), needs-terminal (an interactive program),
    needs-program (git, Java, Docker, an app it drives), needs-service (a database on this machine),
    needs-network (it reached for the internet while starting; the sandbox has none),
    broken-package (it cannot run as published), starts-http (timed out serving HTTP), not-a-server
    (exit code 0 and nothing recognisable), silent, crashed. ``code`` is the exit code the observer saw (observer 4 and later).
    """
    text = detail or ""
    if status == "timeout":
        return {"kind": "starts-http", "args": ["--transport=stdio"]} if HTTP_START.search(text) else None
    if status != "exited":
        return None
    found = EXIT_CODE.match(text)
    code = {"code": int(found.group(1))} if found else {}
    text = text[found.end():] if found else text
    if not text.strip():
        return {"kind": "silent", **code}
    kind = _not_a_server(_kind(text), code)
    return {**kind, **code}


def _kind(text: str) -> dict[str, Any]:
    if SDK.search(text):
        return {"kind": "sdk-incompatible"}
    if OTHER_OS.search(text):
        return {"kind": "needs-other-os"}
    node = NODE_NEEDED.search(text)
    major = int(next(g for g in node.groups() if g)) if node else 0
    if major > SANDBOX_NODE:
        # Below that it is not "newer": a Python package that shells out to npx wants Node at all.
        return {"kind": "needs-newer-node", "node": major}
    if GLIBC.search(text):
        return {"kind": "needs-glibc"}
    if TERMINAL.search(text):
        return {"kind": "needs-terminal"}
    # Key words are read without paths and parser errors: "Unexpected token" and
    # ".../auth/token.js" in a stack are not a request for a token.
    words = KEY_WORDS.search(NOT_WORDS.sub(" ", text))
    names = env_names(text) if words else []
    arg = start_argument(text)
    extra = {**({"env": names} if names else {}), **({"args": [arg]} if arg else {})}
    if USAGE.search(text) or (arg and not names):
        return {"kind": "needs-arguments", **extra}
    if names:
        return {"kind": "needs-config", **extra}
    if PROGRAM.search(text):
        return {"kind": "needs-program"}
    if SERVICE.search(text):
        return {"kind": "needs-service"}
    if words or AUTH_OR_SETUP.search(text):
        return {"kind": "needs-config", **extra}
    if NETWORK.search(text):
        return {"kind": "needs-network"}
    if BROKEN.search(text):
        return {"kind": "broken-package"}
    return {"kind": "crashed"}


def _not_a_server(kind: dict[str, Any], code: dict[str, Any]) -> dict[str, Any]:
    # Exit code 0 and nothing recognisable: the command did what it is for and stopped. It is not
    # a stdio MCP server as started — a CLI, a setup helper, or a server behind a subcommand it
    # did not name.
    return {"kind": "not-a-server"} if kind.get("kind") == "crashed" and code.get("code") == 0 else kind


# The line that says what failed: Python's last "SomeError: …", Node's "Error: …" above its stack.
ERROR_LINE = re.compile(r"^\s*((?:[\w.]*(?:Error|Exception|Exit)(?: \[\w+\])?|Error|error|fatal|FATAL|Fatal(?: error)?)\b:? .{3,})$", re.M)


def excerpt(detail: str | None, limit: int) -> str:
    """What fits in ``limit`` characters of a failure, the cause first. A Python traceback names
    its error on its last line; cut from the front, 300 characters were "Traceback (most recent
    call last): File …" and never the error itself."""
    text = (detail or "").strip()
    if len(text) <= limit:
        return text
    code = EXIT_CODE.match(text)
    prefix = code.group(0) if code else ""
    body = text[len(prefix):]
    lines = ERROR_LINE.findall(body)
    key = re.sub(r"\s+", " ", lines[-1]).strip()[: limit // 2] if lines else ""
    if not key or body.startswith(key):
        return text[:limit]
    head = f"{prefix}{key} … "
    return head + body[: max(0, limit - len(head))]


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
