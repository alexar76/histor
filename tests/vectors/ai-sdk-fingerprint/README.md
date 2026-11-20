# Reference fingerprints from the AI SDK itself

`vectors.json` is the output of `fingerprintTools` (`ai` 7.0.177) over the tool set that `@ai-sdk/mcp` 2.0.101 builds
from `tools.json`, 16 definitions chosen for the edges: no description, an empty one, `title` against
`annotations.title`, an absent `properties`, an open schema with numbers in several spellings, non-ASCII text and
member names, members the fingerprint does not cover, and tools named like object properties.

Made on 2026-11-19 with Node v22.23.4 in a gVisor container: `npm install --ignore-scripts && node gen.mjs`
(`server.mjs` sends `tools.json` as the literal text it is written in, so number spellings reach the client
unchanged). `tests/test_fingerprint_profiles.py` checks `histor.fingerprint_profiles` against it. To refresh for a
newer library, change the versions in `package.json`, run the same two commands and compare.
