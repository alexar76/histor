"""HISTOR — a public transparency log of MCP tool definitions.

HISTOR connects to public MCP servers, reads what they advertise (``initialize`` +
``tools/list``, never a tool call), and issues signed MTL/1 labels over it: the exact
definitions it saw, whether they changed since last time, and what a published pattern set
matched in them. Every label is appended to a Merkle log whose signed tree heads let anyone
check that nothing was rewritten afterwards.

It says what a server *advertised*, and when that changed. It does not say a server is safe.
"""

__version__ = "0.1.0"
