// HISTOR scanner sidecar — the published @aimarket/warden gates, run over tool sets HISTOR
// has already fetched and digested. It opens no socket and executes nothing it reads.
//
//   node scan.mjs describe          -> one JSON object: warden version, pattern set, record set
//   node scan.mjs scan < in.ndjson  -> one JSON line out per line in
//
// Why a sidecar and not a port: the pattern set's meaning is the gate's CODE, not its regex
// table. Ruleset v4's guards (polarity, mention, detection, identifier fragments…) decide in
// context whether a match counts, and a Python replay of the regexes would reproduce v1's
// false positives under a v4 digest. So the label names the pattern set this exact package
// computes, and this exact package produces the matches.
//
// Input line:  {"id": "...", "server": {"id","name","url"}, "tools": [{name, description, inputSchema}]}
// Output line: {"id": "...", "patternMatches": [...], "recordMatches": [...]}  or  {"id", "error"}
import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  StaticScanGate,
  ThreatFeed,
  ThreatGate,
  displaySafe,
  staticScanRuleset,
} from "@aimarket/warden";

// The package's `exports` map does not publish ./package.json, so read it off disk next to
// this script. It is the installed artifact's version, which is what the label must name.
const here = dirname(fileURLToPath(import.meta.url));
const WARDEN_VERSION = JSON.parse(
  readFileSync(join(here, "node_modules", "@aimarket", "warden", "package.json"), "utf8"),
).version;
const PACKAGE = `@aimarket/warden@${WARDEN_VERSION}`;

// The gate's own spelling of each surface in its messages, mapped to MTL field names.
const WHERE = [
  ["name ", "name"],
  ["description ", "description"],
  ["input schema ", "inputSchema"],
];

function patternSetDocument() {
  const ruleset = staticScanRuleset();
  const surfaces = ["name", "description", "inputSchema"];
  const scanned = new Set();
  for (const rule of ruleset.rules) for (const s of rule.surfaces ?? []) scanned.add(s);
  const tierCounts = {};
  for (const rule of ruleset.rules) tierCounts[rule.tier] = (tierCounts[rule.tier] ?? 0) + 1;
  return {
    id: "urn:awr:mtl:1:patternset:argus-warden-static-scan",
    package: PACKAGE,
    note:
      "Generated at runtime by calling staticScanRuleset() in the published package named above — " +
      "the same function the gate calls to stamp its verdicts. A `block` rule can refuse a " +
      "connection in WARDEN; an `advise` rule is reported and never blocks. `severity` is the " +
      "gate's own label and MTL/1 attaches no safety meaning to it. Guards are part of the " +
      "ruleset: two rules with the same regex and different guards are different scans. `fold` names " +
      "the text normalisation applied before a non-`raw` rule is matched, and each rule carries its " +
      "`raw` flag; the digest preimage is {version, fold, rules}.",
    version: ruleset.version,
    fold: ruleset.fold,
    digest: ruleset.digest,
    scannedFields: surfaces.filter((s) => scanned.has(s)),
    ruleCount: ruleset.rules.length,
    tierCounts,
    rules: ruleset.rules,
  };
}

function recordSetDocument(feed) {
  return {
    id: "urn:awr:mtl:1:recordset:argus-warden-builtin",
    package: PACKAGE,
    note:
      "The built-in deny-list floor of the published package named above, pinned by digest. " +
      "No remote feed is loaded: WARDEN's feed has no maximum age, so a remote snapshot could be " +
      "replayed to make newer records disappear (MTL/1 section 7.5). HISTOR matches these " +
      "records against a server's registry name and endpoint only — never its tool definitions — " +
      "and does not state that the set was current when a label was issued.",
    records: feed.builtins,
  };
}

const feed = new ThreatFeed({}); // never load()ed: built-in floor only
const staticScan = new StaticScanGate();
const threat = new ThreatGate(feed);
const recordKeys = feed.builtins.map((rec) => ({
  rec,
  shown: displaySafe(rec.pattern.toLowerCase()),
}));

function patternMatch(finding, tool) {
  const prefix = `Tool "${displaySafe(tool)}" `;
  let where = null;
  if (finding.message.startsWith(prefix)) {
    const rest = finding.message.slice(prefix.length);
    for (const [spelled, field] of WHERE) {
      if (rest.startsWith(spelled)) {
        where = field;
        break;
      }
    }
  }
  if (where === null) throw new Error(`unrecognised finding message shape for ${finding.code}`);
  const span = /at "([\s\S]*)"\.$/.exec(finding.message);
  return {
    code: finding.code,
    severity: finding.severity,
    tier: finding.advisory ? "advise" : "block",
    tool,
    where,
    // Not a label member (section 7.3 fixes the entry shape). HISTOR shows it on the desk,
    // because a code without the text it fired on cannot be judged.
    span: span ? span[1] : "",
  };
}

function recordMatch(finding) {
  const m = /\(matched "([\s\S]*?)" in server identity/.exec(finding.message);
  const hit = m ? recordKeys.find((k) => k.shown === m[1] && k.rec.code === finding.code) : null;
  if (!hit) throw new Error(`cannot map threat finding ${finding.code} back to a record`);
  return {
    code: finding.code,
    severity: finding.severity,
    pattern: hit.rec.pattern,
    reason: hit.rec.reason,
    field: "server identity",
  };
}

async function scanOne(line) {
  const job = JSON.parse(line);
  const tools = (job.tools ?? []).map((t) => ({
    name: String(t.name ?? ""),
    description: typeof t.description === "string" ? t.description : "",
    inputSchema: t.inputSchema && typeof t.inputSchema === "object" ? t.inputSchema : {},
  }));
  const server = {
    id: String(job.server?.id ?? ""),
    name: String(job.server?.name ?? job.server?.id ?? ""),
    transport: "http",
    url: String(job.server?.url ?? ""),
  };
  const policy = { blockAtSeverity: "high", sensitiveToolPatterns: [], allowUnknownServers: true, pinToolDefs: false };
  const scanned = await staticScan.evaluate({ server, tools, prior: [], policy });
  // tools: [] — the name-threat method's claim covers the name and coordinates only.
  const matched = await threat.evaluate({ server, tools: [], prior: [], policy });
  return {
    id: job.id,
    patternMatches: scanned.findings.map((f) => patternMatch(f, f.tool)),
    recordMatches: matched.findings.map(recordMatch),
  };
}

const mode = process.argv[2];
if (mode === "describe") {
  process.stdout.write(
    JSON.stringify({ warden: { package: PACKAGE, version: WARDEN_VERSION }, patternSet: patternSetDocument(), recordSet: recordSetDocument(feed) }) + "\n",
  );
} else if (mode === "scan") {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    let out;
    try {
      out = await scanOne(line);
    } catch (err) {
      let id = null;
      try {
        id = JSON.parse(line).id ?? null;
      } catch {
        /* unparseable line: id stays null */
      }
      out = { id, error: String(err?.message ?? err) };
    }
    process.stdout.write(JSON.stringify(out) + "\n");
  }
} else {
  process.stderr.write("usage: node scan.mjs describe | scan < jobs.ndjson\n");
  process.exit(2);
}
