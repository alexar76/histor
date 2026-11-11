#!/usr/bin/env python3
"""Write Sign-manifest-revision workflows for the other alexar76 registry servers.

HISTOR's own workflow lives in histor/.github/workflows/ and installs from the checkout.
Every other satellite installs the pinned PyPI release ``aimarket-histor[sigstore]==0.8.0``
(the first with ``histor revision``), then lists tools from its released package (stdio) or
public HTTP endpoint.

Run from the monorepo root::

    python3 histor/scripts/gen_sign_manifest_workflows.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (monorepo path for .github/workflows, registry name, version input hint, how tools are listed)
SERVERS: list[tuple[str, str, str, dict[str, str]]] = [
    ("warden", "io.github.alexar76/warden", "0.25.0",
     {"kind": "stdio", "source": "npm:@aimarket/warden@{VERSION}",
      "npm_global": "@aimarket/warden@{VERSION}", "bin": "warden-mcp",
      "stdio": "warden-mcp", "node": "20"}),
    ("argus", "io.github.alexar76/argus3", "0.3.6",
     {"kind": "stdio", "source": "npm:@alexar76/argus3@{VERSION}",
      "npm_global": "@alexar76/argus3@{VERSION}", "bin": "argus",
      "stdio": "argus", "node": "20"}),
    ("aimarket-hub", "io.github.alexar76/aimarket-hub", "3.19.0",
     {"kind": "url", "source": "https://modelmarket.dev/mcp",
      "url": "https://modelmarket.dev/mcp"}),
    ("aimarket-mcp", "io.github.alexar76/aimarket-mcp", "0.6.4",
     {"kind": "stdio", "source": "pypi:aimarket-mcp=={VERSION}",
      "stdio": "uvx --from aimarket-mcp=={VERSION} aimarket-mcp"}),
    ("plugins/aimarket-oracle-gateway", "io.github.alexar76/aimarket-oracle-gateway", "0.3.3",
     {"kind": "stdio", "source": "pypi:aimarket-oracle-gateway=={VERSION}",
      "stdio": "uvx --from aimarket-oracle-gateway=={VERSION} aimarket-oracle-gateway"}),
    ("plugins", "io.github.alexar76/aimarket-plugins", "2.1.3",
     {"kind": "stdio", "source": "pypi:aimarket-mcp-packager=={VERSION}",
      "stdio": "uvx --from aimarket-mcp-packager=={VERSION} aimarket-mcp-packager-mcp"}),
    ("attested/attested-memory-hub", "io.github.alexar76/attested-memory", "3.15.16",
     {"kind": "url", "source": "https://hub.attestedmemory.net/mcp",
      "url": "https://hub.attestedmemory.net/mcp"}),
]


def workflow(server: str, version_hint: str, how: dict[str, str]) -> str:
    node = how.get("node")
    if how["kind"] == "stdio":
        list_step = f'''          .rv/bin/histor revision build --server "$SERVER" \\
            --identity "https://github.com/${{GITHUB_WORKFLOW_REF}}" \\
            --server-version "$VERSION" --source "{how["source"]}" \\
            --stdio "{how["stdio"]}" --timeout 180 \\
            "${{index[@]}}" --if-changed -o statement.json'''
        verify = f'''          .rv/bin/histor revision verify "rev/revisions/${{n}}.json" \\
            --stdio "{how["stdio"]}" --timeout 180 | tee verify.json'''
    else:
        list_step = f'''          .rv/bin/histor revision build --server "$SERVER" \\
            --identity "https://github.com/${{GITHUB_WORKFLOW_REF}}" \\
            --server-version "$VERSION" --source "{how["source"]}" \\
            --url "{how["url"]}" --timeout 180 \\
            "${{index[@]}}" --if-changed -o statement.json'''
        verify = f'''          .rv/bin/histor revision verify "rev/revisions/${{n}}.json" \\
            --url "{how["url"]}" --timeout 180 | tee verify.json'''

    node_step = ""
    if node:
        node_step = f"""
      - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020  # v4.4.0
        with:
          node-version: "{node}"
"""

    npm_global = how.get("npm_global")
    npm_install_step = ""
    if npm_global:
        npm_install_step = f"""
      - name: Install MCP server (npm -g)
        run: |
          npm install -g "{npm_global}"
          command -v "{how.get("bin", "mcp")}" >/dev/null
"""

    # Substitute {VERSION} → ${VERSION} for shell; keep the literal for the YAML description.
    list_step = list_step.replace("{VERSION}", "${VERSION}")
    verify = verify.replace("{VERSION}", "${VERSION}")
    npm_install_step = npm_install_step.replace("{VERSION}", "${VERSION}")

    return f'''# Signed, logged tool-manifest revision (OWASP MCPSTG-SUPP-06) for {server}.
#
# Uses aimarket-histor from PyPI for the CLI (histor revision build|envelope|record|verify).
# Sigstore keyless + Rekor; envelope pushed to the manifest-revisions branch.
name: Sign manifest revision

on:
  workflow_dispatch:
    inputs:
      version:
        description: "Released version whose tools/list is signed, e.g. {version_hint}"
        required: true

permissions:
  contents: read

concurrency:
  group: manifest-revisions
  cancel-in-progress: false

jobs:
  sign:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions:
      contents: write
      id-token: write
    env:
      VERSION: ${{{{ inputs.version }}}}
      SERVER: {server}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
      - uses: astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884  # v10.3.0
{node_step}
      - name: Check the version input
        run: |
          [[ "$VERSION" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+([.a-z0-9]*)$ ]] || {{ echo "not a version: $VERSION"; exit 2; }}

      - name: histor revision CLI (pinned PyPI release)
        run: |
          uv venv --python 3.12 .rv
          # A pinned release, not a repository tip: what verifies and signs here is a published, fixed version.
          uv pip install --python .rv "aimarket-histor[sigstore]==0.8.0"
{npm_install_step}
      - name: The manifest-revisions branch
        run: |
          if git ls-remote --exit-code --heads origin manifest-revisions >/dev/null; then
            git fetch --depth 1 origin manifest-revisions:manifest-revisions
            git worktree add rev manifest-revisions
          else
            git worktree add --detach rev
            git -C rev switch --orphan manifest-revisions
          fi

      - name: Build the statement
        id: build
        run: |
          index=()
          [[ -f rev/index.json ]] && index=(--index rev/index.json)
{list_step}

      - name: Sign (Sigstore keyless; the entry goes to Rekor)
        if: steps.build.outputs.changed == 'true'
        run: .rv/bin/python -m sigstore sign --bundle statement.sigstore.json statement.json

      - name: Envelope, index, and a check of what was just written
        if: steps.build.outputs.changed == 'true'
        run: |
          .rv/bin/histor revision envelope statement.json statement.sigstore.json -o envelope.json
          .rv/bin/histor revision record envelope.json --dir rev
          n="${{{{ steps.build.outputs.revision }}}}"
{verify}
          {{
            echo "### ${{SERVER}}: revision ${{n}} (${{VERSION}})"
            echo
            echo '```json'
            cat verify.json
            echo '```'
          }} >> "$GITHUB_STEP_SUMMARY"

      - name: Push the revision
        if: steps.build.outputs.changed == 'true'
        run: |
          n="${{{{ steps.build.outputs.revision }}}}"
          log_index="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["transparencyLog"]["logIndex"])' "rev/revisions/${{n}}.json")"
          cd rev
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add index.json revisions
          git commit -m "Revision ${{n}} of ${{SERVER}}: ${{VERSION}}, Rekor log index ${{log_index}}"
          git push origin HEAD:manifest-revisions
'''


def main() -> None:
    for rel, server, hint, how in SERVERS:
        out = ROOT / rel / ".github" / "workflows" / "sign-manifest-revision.yml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(workflow(server, hint, how), encoding="utf-8")
        print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
