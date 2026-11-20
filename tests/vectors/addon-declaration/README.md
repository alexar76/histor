# Conformance vectors of the addon-declaration format (third-party, unchanged)

Source: https://github.com/bcrypto0/addon-declaration, folder `vectors/` (formats 0.1 and 0.2), branch `main` as
served by raw.githubusercontent.com on 2026-11-19. Copyright 2026 the addon-declaration authors, Apache License 2.0
(`LICENSE` here is that repository's licence file). Files are byte for byte as downloaded; `SHA256SUMS` lists them.

Only what `tests/test_addon_declaration_vectors.py` reads is here: the two `manifest.json` files and the declaration
files they name. The tool lists (`tools*.json`) and the vendored mcp-tool-set-digest/1 vectors of that repository are
not copied: HISTOR does not run the format's tool check (step 6), so the 22 vectors that expect
`tools_hash_mismatch` are run for every other step and expected to pass them.
