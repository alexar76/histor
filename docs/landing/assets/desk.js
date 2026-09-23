/** HISTOR desk — the landing and the public log browser in one page.
 *
 * Served by the HISTOR service at histor.modelmarket.dev (path routes: /servers, /s/<id>, …) and
 * mirrored to GitHub Pages (hash routes: #/servers, …), where it reads the same API cross-origin.
 * Every piece of third-party text — server names, descriptions, tool definitions — is inserted
 * with textContent, never as HTML: the page renders exactly what servers advertise, and some of
 * what they advertise is written to be read by a model.
 *
 * Verification runs here, in the reader's browser, with no help from the server: signed tree
 * heads and /check answers against the log's did:key (WebCrypto Ed25519), inclusion and
 * consistency proofs against RFC 9162, label leaf hashes over RFC 8785 JCS.
 */
import { DICT, JS_EN } from "./i18n.js";

const HOST_RE = /(^|\.)histor\.modelmarket\.dev$|^localhost$|^127\.0\.0\.1$/;
const SERVED = HOST_RE.test(location.hostname);
const API = SERVED ? "" : "https://histor.modelmarket.dev";
const DESK = SERVED ? "" : "https://histor.modelmarket.dev";
const LANGS = ["en", "ru", "es", "fr", "zh"];
const STATES = ["pinned", "changed", "flagged", "unobserved", "all"];

// ---------------------------------------------------------------------------------------
// i18n — English is snapshotted from the markup, never written in a dictionary.
// ---------------------------------------------------------------------------------------
const EN = {};
document.querySelectorAll("[data-i]").forEach((n) => { EN[n.dataset.i] ??= n.innerHTML; });
document.querySelectorAll("[data-i-ph]").forEach((n) => { EN[n.dataset.iPh] ??= n.getAttribute("placeholder"); });
let lang = "en";

function t(key, vars = {}) {
  let s = (DICT[lang] && DICT[lang][key]) ?? JS_EN[key] ?? EN[key] ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

function applyLang(next, persist) {
  lang = LANGS.includes(next) ? next : "en";
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i]").forEach((n) => {
    const v = lang === "en" ? EN[n.dataset.i] : DICT[lang]?.[n.dataset.i];
    if (v != null) n.innerHTML = v;
  });
  document.querySelectorAll("[data-i-ph]").forEach((n) => {
    const v = lang === "en" ? EN[n.dataset.iPh] : DICT[lang]?.[n.dataset.iPh];
    if (v != null) n.setAttribute("placeholder", v);
  });
  document.querySelectorAll("[data-lang]").forEach((b) => b.classList.toggle("on", b.dataset.lang === lang));
  if (persist) {
    try { localStorage.setItem("histor-lang", lang); } catch { /* storage may be blocked */ }
  }
  render();
}
window.__i18n = { apply: applyLang, t, get lang() { return lang; } };

// ---------------------------------------------------------------------------------------
// small DOM + format helpers
// ---------------------------------------------------------------------------------------
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return n;
}
/** A link from third-party data only if it is http(s): a registry entry can say "javascript:". */
function safeHref(url) {
  try { const u = new URL(String(url)); return u.protocol === "https:" || u.protocol === "http:" ? u.href : null; } catch { return null; }
}
// Characters a model reads and a reader cannot see: controls, zero-width and joiners, bidi
// overrides and isolates, invisible separators, variation selectors, the BOM, and the Unicode tag
// block (which can spell out whole hidden sentences).
const INVISIBLE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u00AD\u034F\u061C\u115F\u1160\u17B4\u17B5\u180B-\u180F\u200B-\u200F\u202A-\u202E\u2060-\u206F\u3164\uFE00-\uFE0F\uFEFF\uFFA0\uFFF0-\uFFFB]|\uDB40[\uDC00-\uDC7F]/g;
const codeOf = (ch) => `U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}`;
/** Text as nodes, every invisible character replaced by a visible ⟨U+XXXX⟩ mark. */
function visible(text) {
  const str = String(text ?? "");
  const nodes = [];
  let last = 0, count = 0;
  for (const m of str.matchAll(INVISIBLE)) {
    nodes.push(str.slice(last, m.index), el("mark", { class: "inv", title: "invisible character", text: `⟨${codeOf(m[0])}⟩` }));
    last = m.index + m[0].length;
    count += 1;
  }
  nodes.push(str.slice(last));
  return { nodes, count };
}
const escapeInvisible = (str) => String(str).replace(INVISIBLE, (ch) => `\\u{${ch.codePointAt(0).toString(16)}}`);
const fmt = (ts) => (ts ? ts.replace("T", " ").replace("Z", "").slice(0, 16) : "—");
const num = (n) => (n == null ? "—" : Number(n).toLocaleString(lang === "zh" ? "zh-CN" : lang));
const short = (d, n = 14) => (d ? `${d.slice(0, n)}…` : "—");

async function api(path, opts = {}) {
  const res = await fetch(`${API}${path}`, { headers: { accept: "application/json" }, ...opts });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    try { err.body = await res.json(); } catch { /* not json */ }
    throw err;
  }
  return res.json();
}

// ---------------------------------------------------------------------------------------
// verification in the browser
// ---------------------------------------------------------------------------------------
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function b58decode(s) {
  let bytes = [0];
  for (const c of s) {
    const v = B58.indexOf(c);
    if (v < 0) throw new Error("bad base58");
    let carry = v;
    for (let i = 0; i < bytes.length; i++) { carry += bytes[i] * 58; bytes[i] = carry & 0xff; carry >>= 8; }
    while (carry) { bytes.push(carry & 0xff); carry >>= 8; }
  }
  for (const c of s) { if (c === "1") bytes.push(0); else break; }
  return new Uint8Array(bytes.reverse());
}
function didKeyRaw(did) {
  const mb = did.replace(/^did:key:/, "");
  if (!mb.startsWith("z")) throw new Error("not base58btc");
  const b = b58decode(mb.slice(1));
  if (b[0] !== 0xed || b[1] !== 0x01 || b.length !== 34) throw new Error("not an Ed25519 did:key");
  return b.slice(2);
}
const b64u = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
const hex = (s) => Uint8Array.from(s.match(/../g) || [], (h) => parseInt(h, 16));
const eq = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
const enc = new TextEncoder();

/** RFC 8785: JS already serialises strings and numbers the way JCS requires; sort keys by UTF-16. */
function jcs(v) {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(jcs).join(",")}]`;
  return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${jcs(v[k])}`).join(",")}}`;
}
async function sha256(bytes) { return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)); }
async function leafHash(doc) { const b = enc.encode(jcs(doc)); const x = new Uint8Array(b.length + 1); x.set(b, 1); return sha256(x); }
async function nodeHash(l, r) { const x = new Uint8Array(65); x[0] = 1; x.set(l, 1); x.set(r, 33); return sha256(x); }

async function verifyInclusion(leaf, index, size, proof, root) {
  if (index >= size) return false;
  let fn = index, sn = size - 1, r = leaf;
  for (const p of proof) {
    if (sn === 0) return false;
    if (fn & 1 || fn === sn) {
      r = await nodeHash(p, r);
      if (!(fn & 1)) while (fn && !(fn & 1)) { fn >>= 1; sn >>= 1; }
    } else r = await nodeHash(r, p);
    fn >>= 1; sn >>= 1;
  }
  return sn === 0 && eq(r, root);
}
async function verifyConsistency(first, second, proof, firstRoot, secondRoot) {
  if (first === second) return proof.length === 0 && eq(firstRoot, secondRoot);
  if (!(0 < first && first < second) || !proof.length) return false;
  const path = (first & (first - 1)) === 0 ? [firstRoot, ...proof] : [...proof];
  let fn = first - 1, sn = second - 1;
  while (fn & 1) { fn >>= 1; sn >>= 1; }
  let fr = path[0], sr = path[0];
  for (const c of path.slice(1)) {
    if (sn === 0) return false;
    if (fn & 1 || fn === sn) {
      fr = await nodeHash(c, fr); sr = await nodeHash(c, sr);
      if (!(fn & 1)) while (fn && !(fn & 1)) { fn >>= 1; sn >>= 1; }
    } else sr = await nodeHash(sr, c);
    fn >>= 1; sn >>= 1;
  }
  return sn === 0 && eq(fr, firstRoot) && eq(sr, secondRoot);
}
/** "ok" | "bad" | "unsupported" */
async function verifySigned(doc, did) {
  try {
    const { signature, ...body } = doc;
    const key = await crypto.subtle.importKey("raw", didKeyRaw(did), { name: "Ed25519" }, false, ["verify"]);
    return (await crypto.subtle.verify({ name: "Ed25519" }, key, b64u(signature.value), enc.encode(jcs(body)))) ? "ok" : "bad";
  } catch (e) {
    return e && /Ed25519|algorithm|NotSupported/i.test(String(e.name || e.message)) ? "unsupported" : "bad";
  }
}
function verdictLine(state, okKey, badKey) {
  if (state === "ok") return el("span", { class: "ok-t", text: `✓ ${t(okKey)}` });
  if (state === "unsupported") return el("span", { class: "warn-t", text: t("js_ed_unsupported") });
  return el("span", { class: "bad-t", text: `✗ ${t(badKey)}` });
}

// ---------------------------------------------------------------------------------------
// routing
// ---------------------------------------------------------------------------------------
function currentRoute() {
  const raw = SERVED ? location.pathname : (location.hash.replace(/^#/, "") || "/");
  return raw.replace(/\/+$/, "") || "/";
}
function href(route) { return SERVED ? route : `#${route}`; }
function go(route, push = true) {
  if (push) {
    if (SERVED) history.pushState({}, "", route + location.search);
    else location.hash = route;
  }
  render();
  if (route !== "/") document.getElementById("desk").scrollIntoView({ behavior: "smooth", block: "start", inline: "nearest" });
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-route]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
  e.preventDefault();
  document.getElementById("drawer").classList.remove("open");
  document.body.classList.remove("menu-open");
  go(a.dataset.route);
});
window.addEventListener("popstate", () => render());
window.addEventListener("hashchange", () => { if (!SERVED) render(); });

const view = document.getElementById("view");
let renderToken = 0;

function render() {
  const route = currentRoute();
  document.querySelectorAll("[data-route]").forEach((a) => {
    const r = a.dataset.route;
    a.setAttribute("href", href(r));
    a.classList.toggle("on", r !== "/" && (route === r || (r === "/servers" && route.startsWith("/s/"))));
  });
  const token = ++renderToken;
  const done = (node) => { if (token === renderToken) view.replaceChildren(node); };
  view.replaceChildren(el("p", { class: "muted", text: t("js_loading") }));
  if (route.startsWith("/s/")) return serverView(route.slice(3), done);
  if (route === "/changes") return changesView(done);
  if (route === "/log") return logView(done);
  if (route === "/check") return checkView(done);
  if (route === "/stats") return statsView(done);
  return serversView(done);
}

function unreachable(done, err) {
  done(el("div", { class: "empty" }, err && err.status === 404 ? t("js_not_found") : t("js_unreachable")));
}

// ---------------------------------------------------------------------------------------
// views
// ---------------------------------------------------------------------------------------
const serverQuery = { q: new URLSearchParams(location.search).get("q") || "", state: "pinned" };

function stateChip(s) { return el("span", { class: `state ${s.state}`, text: s.badgeText }); }

/** Why an endpoint has no tool set on record, in words rather than status codes. */
function reasonText(s) {
  const r = s.skipReason || s.lastStatus || "";
  if (r === "transport-not-observed") return t("js_why_sse");
  if (r === "templated-url") return t("js_why_template");
  if (r === "cleartext-url") return t("js_why_cleartext");
  if (r === "operator-opt-out") return t("js_why_optout");
  if (r === "http-401" || r === "http-403") return t("js_why_auth");
  if (r === "timeout" || r === "connect" || r === "network" || /^http-5/.test(r)) return t("js_why_down", { status: r });
  if (r === "undigestible" || (r === "ok" && !s.toolSetDigest)) return t("js_why_undigestible");
  if (!r) return t("js_not_yet");
  return r;
}

function serverRow(s) {
  const pills = [];
  if (s.toolCount != null) pills.push(el("span", { class: "pill", text: t("js_tools", { n: s.toolCount }) }));
  if (s.blockMatches) pills.push(el("span", { class: "pill block", text: t("js_block_n", { n: s.blockMatches }) }));
  if (s.adviseMatches) pills.push(el("span", { class: "pill advise", text: t("js_advise_n", { n: s.adviseMatches }) }));
  if (s.recordMatches) pills.push(el("span", { class: "pill block", text: t("js_name_n", { n: s.recordMatches }) }));
  if (!s.subjectDigest) pills.push(el("span", { class: "pill", text: reasonText(s) }));
  return el("a", { class: "row-card", href: href(`/s/${s.id}`), "data-route": `/s/${s.id}` },
    el("div", {}, el("div", { class: "nm" }, s.title && s.title !== s.name ? `${s.title} · ` : "", s.name, ...pills),
      el("div", { class: "ep", text: s.endpoint })),
    s.subjectDigest ? stateChip(s) : el("span"));
}

function serversView(done) {
  const list = el("div", { class: "list" });
  const more = el("button", { class: "btn btn-ghost more", type: "button", hidden: true, text: t("js_more") });
  const count = el("span", { class: "faint" });
  const input = el("input", { type: "search", value: serverQuery.q, placeholder: t("lookup_ph"), "aria-label": t("lookup_ph") });
  const chips = STATES.map((st) => el("button", {
    type: "button", class: `chip${serverQuery.state === st ? " on" : ""}`, text: t(`js_state_${st}`), "data-state": st,
    onclick: () => { serverQuery.state = st; chips.forEach((c, i) => c.classList.toggle("on", STATES[i] === st)); load(true); },
  }));
  let offset = 0;
  let timer;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { serverQuery.q = input.value.trim(); load(true); }, 250); });
  async function load(reset) {
    if (reset) { offset = 0; list.replaceChildren(); }
    try {
      const data = await api(`/api/v1/servers?q=${encodeURIComponent(serverQuery.q)}&state=${serverQuery.state}&limit=50&offset=${offset}`);
      if (reset && !data.servers.length) list.append(el("div", { class: "empty", text: t("js_no_servers") }));
      data.servers.forEach((s) => list.append(serverRow(s)));
      offset += data.servers.length;
      more.hidden = offset >= data.total;
      count.textContent = t("js_count", { n: num(data.total) });
    } catch (err) {
      list.replaceChildren(el("div", { class: "empty", text: t("js_unreachable") }));
    }
  }
  more.addEventListener("click", () => load(false));
  done(el("div", {}, el("p", { class: "list-intro", text: t("js_servers_intro") }),
    el("div", { class: "toolbar" }, input, ...chips, count), list, more));
  load(true);
  api("/api/v1/stats").then((st) => {
    const c = st.targets || {};
    const counts = { pinned: c.pinned, changed: c.ever_changed, flagged: (c.block_tier || 0) + (c.advise_only || 0) + (c.name_records || 0),
      unobserved: c.listed != null && c.pinned != null ? c.listed - c.pinned : null, all: c.listed };
    chips.forEach((chip) => {
      const n = counts[chip.dataset.state];
      if (n != null) chip.append(el("span", { class: "cnt", text: num(n) }));
    });
  }).catch(() => {});
}

function renderDiff(summary) {
  const box = el("div", {});
  if (summary.newAddresses?.length) {
    box.append(el("p", {}, el("b", { class: "bad-t", text: `⚠ ${t("js_new_addresses")}: ` }),
      ...visible(summary.newAddresses.join(", ")).nodes,
      (summary.newAddressesTotal || 0) > summary.newAddresses.length ? ` … +${summary.newAddressesTotal - summary.newAddresses.length}` : ""));
    box.append(el("p", { class: "faint", text: t("js_new_addresses_hint") }));
  }
  if (summary.added?.length) box.append(el("p", {}, el("b", { class: "ok-t", text: `+ ${t("js_added")}: ` }), ...visible(summary.added.join(", ")).nodes));
  if (summary.removed?.length) box.append(el("p", {}, el("b", { class: "bad-t", text: `− ${t("js_removed")}: ` }), ...visible(summary.removed.join(", ")).nodes));
  for (const m of summary.modified || []) {
    box.append(el("p", { class: "mono" }, el("b", {}, ...visible(m.tool).nodes)));
    if (m.fields.description) {
      const d = el("div", { class: "diff" });
      for (const [op, text] of m.fields.description) {
        const parts = visible(text).nodes;
        d.append(op === "+" ? el("ins", {}, ...parts) : op === "-" ? el("del", {}, ...parts) : el("span", {}, ...parts));
      }
      box.append(el("div", { class: "faint", text: "description" }), d);
    }
    for (const member of ["inputSchema", "outputSchema"]) {
      if (!m.fields[member]) continue;
      const d = el("div", { class: "diff" });
      for (const p of m.fields[member]) {
        d.append(el("div", {}, ...visible(p.path).nodes, " ", p.old != null ? el("del", {}, ...visible(p.old).nodes) : "", " ",
          p.new != null ? el("ins", {}, ...visible(p.new).nodes) : ""));
      }
      box.append(el("div", { class: "faint", text: member }), d);
    }
  }
  if (summary.detail === "names-only") box.append(el("p", { class: "faint", text: t("js_names_only") }));
  return box;
}

async function verifyLabelButton(label, did) {
  const out = el("span", { class: "faint" });
  const btn = el("button", { class: "chip", type: "button", text: t("js_verify"), onclick: async () => {
    out.replaceChildren(t("js_checking"));
    try {
      const [doc, proof] = await Promise.all([api(`/api/v1/labels/${encodeURIComponent(label.id)}`),
        api(`/api/v1/labels/${encodeURIComponent(label.id)}/proof`)]);
      // The proof must be about THIS row: the served document is the label shown, at the leaf
      // shown, with the method and verdict shown — otherwise a ✓ would vouch for another label.
      const cs = doc.credentialSubject || {};
      if (doc.id !== label.id || proof.leafIndex !== label.leaf_index || cs.verdict !== label.verdict
          || (cs.method || {}).id !== label.method) {
        out.replaceChildren(el("span", { class: "bad-t", text: `✗ ${t("js_row_mismatch")}` }));
        return;
      }
      const leaf = await leafHash(doc);
      const inc = await verifyInclusion(leaf, proof.leafIndex, proof.sth.treeSize, proof.inclusionProof.map(hex), hex(proof.sth.rootHash));
      const sig = proof.sth.log === did && proof.sth.type === "histor.sth/v1" ? await verifySigned(proof.sth, did) : "bad";
      out.replaceChildren(inc ? el("span", { class: "ok-t", text: `✓ ${t("js_included", { n: proof.sth.treeSize })}` })
        : el("span", { class: "bad-t", text: `✗ ${t("js_not_included")}` }), " · ", verdictLine(sig, "js_sth_ok", "js_sth_bad"));
    } catch (err) {
      out.replaceChildren(err && err.status === 409 ? el("span", { class: "faint", text: t("js_not_yet_signed") })
        : el("span", { class: "bad-t", text: t("js_unreachable") }));
    }
  } });
  return el("span", {}, btn, " ", out);
}

/** One plain sentence about an endpoint, and what a reader might do about it. */
function summaryLine(s) {
  const days = (ts) => (ts ? Math.max(0, Math.floor((Date.now() - Date.parse(ts)) / 86400000)) : 0);
  let cls = "", text, advice = "";
  if (!s.toolSetDigest) {
    text = t("js_sum_unobserved", { why: reasonText(s) });
  } else if (s.changes > 0 && days(s.unchangedSince) < 14) {
    cls = "warn";
    text = t("js_sum_changed", { date: fmt(s.unchangedSince).slice(0, 10), n: s.changes, obs: num(s.observations) });
    advice = t("js_adv_changed", { date: fmt(s.unchangedSince).slice(0, 10) });
  } else {
    cls = "good";
    text = t("js_sum_stable", { first: fmt(s.firstPinned).slice(0, 10), obs: num(s.observations),
      since: fmt(s.unchangedSince).slice(0, 10), d: days(s.unchangedSince), n: s.changes || 0 });
    advice = t("js_adv_stable");
  }
  return el("div", { class: `verdict-line ${cls}` }, el("div", { text }), advice ? el("div", { class: "advice", text: advice }) : "");
}

async function serverView(id, done) {
  let data, issuer;
  try { [data, issuer] = await Promise.all([api(`/api/v1/servers/${encodeURIComponent(id)}`), api("/api/v1/issuer")]); }
  catch (err) { return unreachable(done, err); }
  const s = data.server;
  const matchesFor = (name) => (data.patternMatches || []).filter((m) => m.tool === name);
  const badgeUrl = `${DESK || location.origin}/badge/${s.id}.svg`;
  const pageUrl = `${DESK || location.origin}/s/${s.id}`;
  const md = `[![MCP tool defs](${badgeUrl})](${pageUrl})`;
  const root = el("div", { class: "detail" },
    el("p", {}, el("a", { href: href("/servers"), "data-route": "/servers", class: "faint", text: `← ${t("nav_servers")}` })),
    el("h2", {}, ...visible(s.title && s.title !== s.name ? s.title : s.name).nodes),
    el("p", { class: "mono faint" }, ...visible(s.name).nodes, " · ", s.endpoint),
    el("p", {}, stateChip(s), safeHref(s.repository) ? el("a", { class: "pill", href: safeHref(s.repository), rel: "noopener noreferrer", text: "repository" }) : "",
      s.delisted ? el("span", { class: "pill", text: t("js_delisted") }) : ""),
    data.description ? el("p", { class: "muted", text: data.description }) : "",
    el("div", { class: "facts" },
      ...[["js_f_first", fmt(s.firstPinned)], ["js_f_unchanged", fmt(s.unchangedSince)], ["js_f_last", fmt(s.lastObserved)],
        ["js_f_obs", num(s.observations)], ["js_f_changes", num(s.changes)], ["js_f_tools", s.toolCount ?? "—"],
        ["js_f_status", s.lastStatus || "—"], ["js_f_digest", short(s.toolSetDigest, 18)]]
        .map(([k, v]) => el("div", { class: "fact" }, el("div", { class: "k", text: t(k) }), el("div", { class: "v", text: String(v) })))),
    el("div", { class: "snippet" }, el("img", { src: badgeUrl, alt: "badge" }), el("code", { text: md }),
      el("button", { class: "chip", type: "button", text: t("js_copy"), onclick: (e) => { navigator.clipboard?.writeText(md); e.target.textContent = t("js_copied"); } })),
  );
  root.insertBefore(summaryLine(s), root.querySelector(".facts"));
  if (!s.toolSetDigest) {
    root.append(el("div", { class: "empty", text: t("js_unobserved_why", { status: reasonText(s) }) }));
  }
  if (data.changes?.length) {
    root.append(el("h3", { class: "block-h", text: t("js_h_changes") }));
    for (const c of data.changes) root.append(el("div", { class: "result" }, el("p", { class: "warn-t mono", text: `${fmt(c.observed_at)} · ${t("js_changed")}` }), renderDiff(c.summary)));
  }
  const clsByIndex = new Map();
  if (data.classifier?.findings) for (const f of data.classifier.findings) {
    if (!clsByIndex.has(f.i)) clsByIndex.set(f.i, []);
    clsByIndex.get(f.i).push(f);
  }
  const clsChecked = data.classifier ? (data.classifier.checkedTools ?? 0) : 0;
  if (data.classifier) {
    const c = data.classifier;
    const partial = (c.checkedTools ?? 0) < (c.toolCount ?? 0);
    root.append(el("h3", { class: "block-h", text: t("js_h_classifier") }));
    root.append(el("p", { class: "faint", text: t("js_classifier_note", { model: c.model }) }));
    if (partial) root.append(el("p", { class: "warn-t", text: t("js_classifier_partial", { checked: c.checkedTools ?? 0, total: c.toolCount ?? 0 }) }));
    // "flagged nothing" can only be said about what was actually examined.
    if (!c.findings?.length && !partial) root.append(el("p", { class: "ok-t", text: t("js_classifier_clean") }));
  }
  if (data.tools?.length) {
    root.append(el("h3", { class: "block-h", text: t("js_h_tools", { n: data.tools.length }) }));
    for (let ti = 0; ti < data.tools.length; ti++) {
      const tool = data.tools[ti];
      const ms = matchesFor(tool.name);
      const cf = clsByIndex.get(ti) || [];
      const name = visible(tool.name), desc = visible(tool.description || "");
      const schemaText = JSON.stringify(tool.inputSchema, null, 2);
      const hidden = name.count + desc.count + ((schemaText || "").match(INVISIBLE) || []).length;
      const notChecked = data.classifier && ti >= clsChecked;
      const box = el("div", { class: "tool" }, el("div", { class: "tn" }, ...name.nodes,
        ...ms.map((m) => el("span", { class: `pill ${m.tier}`, text: `${m.code} · ${m.tier}` })),
        ...cf.map((f) => el("span", { class: "pill block", text: `AI · ${f.severity}` })),
        notChecked ? el("span", { class: "pill", text: t("js_classifier_notchecked") }) : ""),
      tool.description ? el("p", { class: "td" }, ...desc.nodes) : "",
      hidden ? el("p", { class: "warn-t", text: t("js_invisible", { n: hidden }) }) : "");
      for (const m of ms) box.append(el("div", { class: "match" }, el("b", { text: m.where }), " · ", m.severity, " · ", el("mark", { text: m.span || "" })));
      for (const f of cf) box.append(el("div", { class: "match" }, el("b", { class: "bad-t", text: (Array.isArray(f.categories) ? f.categories : []).join(", ") }), " · ", f.severity || "", " · ",
        ...visible(f.reason || "").nodes, f.quote ? el("mark", {}, ...visible(f.quote).nodes) : ""));
      box.append(el("details", {}, el("summary", { text: "inputSchema" }), el("pre", { text: escapeInvisible(schemaText) })));
      if (tool.outputSchema) box.append(el("details", {}, el("summary", { text: "outputSchema" }), el("pre", { text: escapeInvisible(JSON.stringify(tool.outputSchema, null, 2)) })));
      root.append(box);
    }
    root.append(el("p", { class: "faint", text: t("js_match_note") }));
  }
  if (data.clientReports?.length) {
    root.append(el("h3", { class: "block-h", text: t("js_h_clients") }), el("p", { class: "faint", text: t("js_clients_note") }));
    for (const r of data.clientReports) {
      const same = r.toolset === s.toolSetDigest;
      root.append(el("p", { class: "mono" }, el("span", { class: same ? "ok-t" : "warn-t", text: same ? t("js_same") : t("js_different") }), ` · ${short(r.toolset, 20)} · ${t("js_reports", { n: r.reports })} · ${r.first_day}…${r.last_day}`));
    }
  }
  root.append(el("h3", { class: "block-h", text: t("js_h_timeline") }));
  const tl = el("div", { class: "timeline" });
  for (const r of data.timeline) {
    const cls = r.status === "ok" ? "ok" : r.status === "undigestible" ? "chg" : "";
    tl.append(el("div", { class: `tl ${cls}` }, el("span", { class: "mono", text: `${fmt(r.from)}${r.count > 1 ? ` → ${fmt(r.until)} (×${r.count})` : ""}` }), " · ",
      r.status, r.toolSetDigest ? ` · ${short(r.toolSetDigest, 18)}` : "", r.detail ? ` · ${r.detail}` : ""));
  }
  root.append(tl);
  root.append(el("h3", { class: "block-h", text: t("js_h_labels") }));
  const table = el("table", { class: "labels-table" }, el("tr", {}, ...["js_c_method", "js_c_verdict", "js_c_observed", "js_c_leaf", ""].map((k) => el("th", { text: k ? t(k) : "" }))));
  for (const lbl of data.labels) {
    table.append(el("tr", {}, el("td", { class: "mono", text: lbl.methodShort }),
      el("td", { class: `verdict-${lbl.verdict}`, text: lbl.verdict }), el("td", { text: fmt(lbl.observed_at) }),
      el("td", { class: "mono" }, el("a", { href: `${API}/api/v1/labels/${encodeURIComponent(lbl.id)}`, text: `#${lbl.leaf_index}` })),
      el("td", {}, await verifyLabelButton(lbl, issuer.did))));
  }
  root.append(table, el("p", { class: "faint", text: t("js_label_note") }));
  done(root);
}

async function changesView(done) {
  let data;
  try { data = await api("/api/v1/changes?limit=60"); } catch (err) { return unreachable(done, err); }
  const root = el("div", {}, el("p", { class: "muted" }, t("js_changes_intro"), " ", el("a", { href: `${API || ""}/feed.xml`, text: "Atom" })));
  if (!data.changes.length) root.append(el("div", { class: "empty", text: t("js_no_changes") }));
  for (const c of data.changes) {
    const s = c.summary;
    const head = el("div", { class: "row-card" },
      el("div", {}, el("a", { class: "nm", href: href(`/s/${c.target_id}`), "data-route": `/s/${c.target_id}`, text: c.title || c.name }),
        el("div", { class: "ep", text: c.endpoint })),
      el("span", { class: "state changed", text: `${fmt(c.observed_at)}` }));
    const counts = el("p", { class: "mono faint", text: `+${s.added.length} −${s.removed.length} ~${s.modified.length}` });
    if (s.newAddresses?.length) counts.append(" ", el("b", { class: "bad-t", text: `⚠ ${t("js_new_addresses")}: ${s.newAddressesTotal || s.newAddresses.length}` }));
    root.append(el("details", { class: "result" }, el("summary", {}, head, counts), renderDiff(s)));
  }
  done(root);
}

function savedHead() { try { return JSON.parse(localStorage.getItem("histor-sth") || "null"); } catch { return null; } }
function saveHead(sth) { try { localStorage.setItem("histor-sth", JSON.stringify(sth)); } catch { /* blocked */ } }

async function logView(done) {
  let sth, issuer;
  try { [sth, issuer] = await Promise.all([api("/api/v1/log/sth"), api("/api/v1/issuer")]); }
  catch (err) {
    if (err && err.status === 404) return done(el("div", { class: "empty", text: t("js_no_sth") }));
    return unreachable(done, err);
  }
  const sigLine = el("span", { class: "faint", text: t("js_checking") });
  const consLine = el("p", {});
  // Keeping a head is offered only once its signature checked out and it is consistent with the
  // head already kept: a browser must never pin a head it could not verify.
  const keep = el("button", { class: "chip", type: "button", disabled: true, text: t("js_keep_head"),
    onclick: (e) => { saveHead(sth); e.target.textContent = t("js_kept"); e.target.disabled = true; } });
  const root = el("div", {},
    el("div", { class: "result" },
      el("h3", { class: "block-h", text: t("js_h_sth") }),
      el("div", { class: "facts" },
        ...[["js_f_size", num(sth.treeSize)], ["js_f_time", fmt(sth.timestamp)], ["js_f_root", short(sth.rootHash, 24)], ["js_f_issuer", short(issuer.did, 24)]]
          .map(([k, v]) => el("div", { class: "fact" }, el("div", { class: "k", text: t(k) }), el("div", { class: "v", text: v })))),
      el("p", {}, sigLine), consLine, keep,
      el("details", {}, el("summary", { text: "JSON" }), el("pre", { text: JSON.stringify(sth, null, 2) }))),
    el("p", { class: "muted", text: t("js_log_intro") }));
  done(root);
  const bad = (key, vars) => el("span", { class: "bad-t", text: `✗ ${t(key, vars)}` });
  // The same rules as `python -m histor audit`: the head is this log's, signed by its key...
  const sig = sth.log === issuer.did && sth.type === "histor.sth/v1" ? await verifySigned(sth, issuer.did) : "bad";
  sigLine.replaceChildren(verdictLine(sig, "js_sth_ok", "js_sth_bad"));
  // ...and it extends the head this browser kept: never smaller, never a different root at the
  // same size, never under another key.
  const kept = savedHead();
  let consistent = !kept;
  if (!kept) consLine.replaceChildren(el("span", { class: "faint", text: t("js_no_kept") }));
  else if (kept.log !== issuer.did) consLine.replaceChildren(bad("js_key_changed", { a: num(kept.treeSize) }));
  else if (kept.treeSize > sth.treeSize) consLine.replaceChildren(bad("js_shrank", { a: num(kept.treeSize), b: num(sth.treeSize) }));
  else if (kept.treeSize === sth.treeSize) {
    consistent = kept.rootHash === sth.rootHash;
    consLine.replaceChildren(consistent ? el("span", { class: "ok-t", text: `✓ ${t("js_same_head", { a: num(kept.treeSize), d: fmt(kept.timestamp) })}` })
      : bad("js_rewritten", { a: num(kept.treeSize) }));
  } else {
    try {
      const proof = await api(`/api/v1/log/proof/consistency?first=${kept.treeSize}&second=${sth.treeSize}`);
      consistent = await verifyConsistency(kept.treeSize, sth.treeSize, proof.proof.map(hex), hex(kept.rootHash), hex(sth.rootHash));
      consLine.replaceChildren(consistent ? el("span", { class: "ok-t", text: `✓ ${t("js_consistent", { a: num(kept.treeSize), d: fmt(kept.timestamp) })}` })
        : bad("js_inconsistent", { a: num(kept.treeSize) }));
    } catch { consLine.replaceChildren(el("span", { class: "bad-t", text: t("js_unreachable") })); }
  }
  keep.disabled = !(sig === "ok" && consistent);
  const start = Math.max(0, sth.treeSize - 60);
  try {
    const entries = (await api(`/api/v1/log/entries?start=${start}&end=${sth.treeSize}`)).entries.reverse();
    const table = el("table", { class: "labels-table" }, el("tr", {}, ...["js_c_leaf", "js_c_method", "js_c_verdict", "js_c_issued", "js_c_server"].map((k) => el("th", { text: t(k) }))));
    for (const e of entries) {
      table.append(el("tr", {}, el("td", { class: "mono" }, el("a", { href: `${API}/api/v1/labels/${encodeURIComponent(e.id)}`, text: `#${e.leaf_index}` })),
        el("td", { class: "mono", text: e.methodShort }), el("td", { class: `verdict-${e.verdict}`, text: e.verdict }),
        el("td", { text: fmt(e.issued_at) }),
        el("td", {}, e.target_id ? el("a", { href: href(`/s/${e.target_id}`), "data-route": `/s/${e.target_id}`, text: e.target_id }) : "—")));
    }
    root.append(el("h3", { class: "block-h", text: t("js_h_entries") }), table);
  } catch { /* the head is still worth showing */ }
}

function parseTools(text) {
  const v = JSON.parse(text);
  if (Array.isArray(v)) return v;
  if (Array.isArray(v?.tools)) return v.tools;
  if (Array.isArray(v?.result?.tools)) return v.result.tools;
  throw new Error("no tools array");
}

async function checkView(done) {
  const endpoint = el("input", { type: "text", placeholder: "https://example.com/mcp", "aria-label": "endpoint" });
  const area = el("textarea", { placeholder: t("js_paste"), spellcheck: "false" });
  const contribute = el("input", { type: "checkbox", id: "contrib" });
  const out = el("div", {});
  const submit = el("button", { class: "btn btn-primary", type: "button", text: t("js_check_go"), onclick: async () => {
    out.replaceChildren(el("p", { class: "muted", text: t("js_checking") }));
    const body = {};
    const ep = endpoint.value.trim();
    if (/^https?:\/\//.test(ep)) body.endpoint = ep; else if (ep) body.name = ep;
    if (area.value.trim()) {
      try { body.tools = parseTools(area.value); } catch { return out.replaceChildren(el("p", { class: "bad-t", text: t("js_bad_json") })); }
    }
    if (contribute.checked) body.contribute = true;
    try {
      const [res, issuer] = await Promise.all([api("/api/v1/check", { method: "POST", headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify(body) }), api("/api/v1/issuer")]);
      const sig = await verifySigned(res, issuer.did);
      const box = el("div", { class: "result" },
        el("h3", { class: "block-h", text: t(`js_match_${res.match}`) }),
        el("p", { class: "muted", text: res.note }),
        res.target ? el("p", {}, el("a", { href: href(`/s/${res.target.id}`), "data-route": `/s/${res.target.id}`, text: res.target.name }), " · ", el("span", { class: "mono faint", text: res.target.endpoint })) : "",
        res.observed ? el("p", { class: "mono faint", text: `${t("js_f_unchanged")}: ${fmt(res.observed.unchangedSince)} · ${t("js_f_changes")}: ${res.observed.changes ?? 0}` }) : "",
        res.query?.toolSetDigest ? el("p", { class: "mono faint", text: `${t("js_your_digest")}: ${res.query.toolSetDigest}` }) : "",
        res.query?.digestError ? el("p", { class: "warn-t", text: `${res.query.digestError.code}: ${res.query.digestError.detail}` }) : "",
      );
      if (res.patternScan?.status === "scanned") {
        box.append(el("p", {}, el("b", { text: t("js_scan") }), ` · ${t("js_block_n", { n: res.patternScan.block })} · ${t("js_advise_n", { n: res.patternScan.advise })}`));
        for (const m of res.patternScan.matches) box.append(el("div", { class: "match" }, el("b", { text: m.tool }), ` · ${m.code} · ${m.tier} · ${m.where} · `, el("mark", { text: m.span || "" })));
        box.append(el("p", { class: "faint", text: res.patternScan.note }));
      }
      if (res.classifier) {
        const c = res.classifier;
        if (c.status === "classified") {
          box.append(el("p", {}, el("b", { text: t("js_h_classifier") }), ` · ${c.model || ""} · ${t("js_classifier_flagged_n", { n: c.flagged ?? 0 })}`));
          for (const f of (Array.isArray(c.findings) ? c.findings : [])) {
            box.append(el("div", { class: "match" }, el("b", {}, ...visible(f.tool ?? `#${f.i}`).nodes),
              ` · ${(Array.isArray(f.categories) ? f.categories : []).join(", ")} · ${f.severity || ""} · `, ...visible(f.reason || "").nodes,
              f.quote ? el("mark", {}, ...visible(f.quote).nodes) : ""));
          }
          const shownN = Array.isArray(c.findings) ? c.findings.length : 0;
          if ((c.flagged ?? 0) > shownN) box.append(el("p", { class: "faint", text: t("js_classifier_more", { n: (c.flagged ?? 0) - shownN }) }));
          if ((c.checkedTools ?? 0) < (c.toolCount ?? 0)) {
            box.append(el("p", { class: "warn-t", text: t("js_classifier_partial", { checked: c.checkedTools ?? 0, total: c.toolCount ?? 0 }) }));
          }
        } else {
          box.append(el("p", {}, el("b", { text: t("js_h_classifier") }), ` · ${t("js_classifier_none")}`));
        }
        box.append(el("p", { class: "faint", text: c.note || "" }));
      }
      box.append(el("p", {}, verdictLine(sig, "js_check_sig_ok", "js_check_sig_bad")), el("details", {}, el("summary", { text: "JSON" }), el("pre", { text: JSON.stringify(res, null, 2) })));
      out.replaceChildren(box);
    } catch (err) {
      out.replaceChildren(el("p", { class: "bad-t", text: err.status === 429 ? t("js_slow_down") : (err.body?.detail || t("js_unreachable")) }));
    }
  } });
  const example = el("button", { class: "btn btn-ghost", type: "button", text: t("js_try_example"), onclick: async () => {
    try {
      const list = await api("/api/v1/servers?state=pinned&limit=1");
      const one = list.servers[0];
      if (!one) return out.replaceChildren(el("p", { class: "muted", text: t("js_no_example") }));
      const detail = await api(`/api/v1/servers/${encodeURIComponent(one.id)}`);
      endpoint.value = one.endpoint;
      area.value = JSON.stringify({ tools: detail.tools }, null, 2);
      submit.click();
    } catch { out.replaceChildren(el("p", { class: "bad-t", text: t("js_unreachable") })); }
  } });
  const help = el("details", { class: "help" }, el("summary", { text: t("js_help_where") }),
    el("p", { class: "muted", text: t("js_help_text") }),
    el("pre", { text: [
      "# 1. open a session",
      "curl -sS -D - https://example.com/mcp -H 'Content-Type: application/json' \\",
      "  -H 'Accept: application/json, text/event-stream' \\",
      "  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"me\",\"version\":\"1\"}}}'",
      "# 2. list the tools (send the Mcp-Session-Id header the first answer returned)",
      "curl -sS https://example.com/mcp -H 'Content-Type: application/json' \\",
      "  -H 'Accept: application/json, text/event-stream' -H 'Mcp-Session-Id: <id>' \\",
      "  -d '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}'",
      "",
      "# or, interactively:",
      "npx @modelcontextprotocol/inspector",
    ].join("\n") }));
  done(el("div", {}, el("p", { class: "muted", text: t("js_check_intro") }),
    el("div", { class: "check-grid" },
      el("div", {}, el("p", {}, el("b", { text: t("js_endpoint") })), endpoint, el("p", {}, el("b", { text: t("js_tools_json") })), area,
        el("p", {}, contribute, " ", el("label", { for: "contrib", text: t("js_contribute") })),
        el("div", { class: "btn-row" }, submit, example), help),
      out)));
}

async function statsView(done) {
  let st, hosts;
  try { [st, hosts] = await Promise.all([api("/api/v1/stats"), api("/api/v1/stats/hosts").catch(() => null)]); }
  catch (err) { return unreachable(done, err); }
  const run = st.lastRun || {};
  const statuses = Object.entries(run.statuses || {}).sort((a, b) => b[1] - a[1]);
  const total = statuses.reduce((a, [, n]) => a + n, 0) || 1;
  const colors = { ok: "var(--pass)", "http-401": "var(--scan)", "http-403": "var(--scan)", timeout: "var(--fail)", connect: "var(--fail)" };
  const bar = el("div", { class: "bar" }, ...statuses.map(([k, n]) => el("span", { title: `${k}: ${n}`, style: `width:${(n / total) * 100}%;background:${colors[k] || "var(--inc)"}` })));
  const pr = st.crawl?.running ? st.crawl.progress : null;
  const root = el("div", {},
    st.crawl?.running ? el("p", { class: "crawl-note", text: pr ? t("js_crawling", { done: num(pr.done), of: num(pr.of) }) : t("js_crawling_start") }) : "",
    el("p", { class: "muted", text: t("js_stats_intro", { at: fmt(run.finishedAt) }) }),
    el("div", { class: "facts" },
      ...[["js_s_servers", num(run.registryServers)], ["js_s_endpoints", num(run.registryEndpoints)], ["js_s_attempted", num(run.attempted)],
        ["js_s_pinned", num(st.targets.pinned)], ["js_s_block", num(st.targets.block_tier)], ["js_s_advise", num(st.targets.advise_only)],
        ["js_s_changes7", num(st.changes.last7d)], ["js_s_changes30", num(st.changes.last30d)], ["js_s_log", num(st.log.treeSize)],
        ["js_s_clients", num(st.clientReports.last7d)]]
        .map(([k, v]) => el("div", { class: "fact" }, el("div", { class: "k", text: t(k) }), el("div", { class: "v", text: v })))),
    el("h3", { class: "block-h", text: t("js_h_statuses") }), bar,
    el("div", { class: "hosts" }, ...statuses.map(([k, n]) => el("div", {}, el("span", { text: k }), el("span", { text: `${num(n)} · ${((n / total) * 100).toFixed(1)}%` })))),
    el("h3", { class: "block-h", text: t("js_h_skipped") }),
    el("div", { class: "hosts" }, ...Object.entries(run.notAttempted || {}).map(([k, n]) => el("div", {}, el("span", { text: k }), el("span", { text: num(n) })))),
  );
  if (hosts?.hosts?.length) {
    root.append(el("h3", { class: "block-h", text: t("js_h_hosts") }), el("p", { class: "faint", text: t("js_hosts_note") }),
      el("div", { class: "hosts" }, ...hosts.hosts.map((h) => el("div", {}, el("span", { text: h.host }), el("span", { text: num(h.endpoints) })))));
  }
  done(root);
}

// ---------------------------------------------------------------------------------------
// hero: metrics, lookup, 3D
// ---------------------------------------------------------------------------------------
async function loadMetrics() {
  try {
    const st = await api("/api/v1/stats");
    document.getElementById("m-endpoints").textContent = num(st.lastRun?.registryEndpoints);
    document.getElementById("m-pinned").textContent = num(st.targets.pinned);
    document.getElementById("m-changes").textContent = num(st.changes.last7d);
    document.getElementById("m-tree").textContent = num(st.log.treeSize);
    document.getElementById("m-sth").textContent = st.log.sthTimestamp ? fmt(st.log.sthTimestamp).slice(5) : "—";
    const note = document.getElementById("crawl-note");
    note.hidden = !st.crawl?.running;
    if (st.crawl?.running) {
      const pr = st.crawl.progress;
      note.textContent = pr ? t("js_crawling", { done: num(pr.done), of: num(pr.of) }) : t("js_crawling_start");
      // Figures move as each batch is saved; follow them while the crawl runs.
      clearTimeout(loadMetrics.timer);
      loadMetrics.timer = setTimeout(loadMetrics, 60000);
    }
  } catch { /* metrics stay at — */ }
  try { document.getElementById("foot-did").textContent = short((await api("/api/v1/issuer")).did, 30); } catch { /* offline */ }
}

document.getElementById("lookup").addEventListener("submit", (e) => {
  e.preventDefault();
  serverQuery.q = document.getElementById("lookup-q").value.trim();
  serverQuery.state = "all";
  go("/servers");
});
document.getElementById("burger").addEventListener("click", () => {
  const d = document.getElementById("drawer");
  const open = !d.classList.contains("open");
  d.classList.toggle("open", open);
  document.body.classList.toggle("menu-open", open);
  document.getElementById("burger").setAttribute("aria-expanded", String(open));
});
document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => applyLang(b.dataset.lang, true)));

// Reveal on scroll. Sections are hidden only once this line has run (html.rv-on), so a script
// that fails to load leaves the page readable instead of blank; what is already on screen is
// marked visible first so it does not blink.
const io = new IntersectionObserver((items) => items.forEach((it) => { if (it.isIntersecting) it.target.classList.add("in"); }), { threshold: 0.12 });
document.querySelectorAll(".rv").forEach((n) => { if (n.getBoundingClientRect().top < innerHeight) n.classList.add("in"); else io.observe(n); });
document.documentElement.classList.add("rv-on");

function hud(mode, phase, msg) {
  const m = document.getElementById("hud-mode");
  m.className = `mode ${mode}`;
  document.getElementById("hud-mode-l").textContent = mode;
  if (phase != null) document.getElementById("hud-phase").textContent = phase;
  if (msg != null) document.getElementById("hud-msg").textContent = msg;
}

async function startLoom() {
  const stage = document.getElementById("stage");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let mod;
  try { mod = await import("./loom.js"); } catch { stage.classList.add("nogl"); return; }
  let liveMode = "SIM";
  const loom = mod.mountLoom(document.getElementById("loom"), {
    reducedMotion: reduced,
    onPhase: (phase, info) => {
      if (phase === "SIGNED" && info.sth) hud(liveMode, t("js_ph_signed"), `n=${num(info.sth.treeSize)} · root ${info.sth.rootHash.slice(0, 16)}… · ${fmt(info.sth.timestamp)}`);
      else if (phase === "SIGNED") hud(liveMode, t("js_ph_signed"), t("js_ph_model"));
      else if (phase === "CHANGED") hud(liveMode, t("js_ph_changed"), null);
      else if (phase === "FREEZE") hud(liveMode, t("js_ph_freeze"), null);
      else if (phase === "APPEND") hud(liveMode, t("js_ph_append"), null);
    },
  });
  if (!loom) { stage.classList.add("nogl"); return; }
  async function poll() {
    const data = await mod.probeLog(API);
    liveMode = data.mode === "LIVE" ? "LIVE" : data.mode === "UNREACHABLE" ? "UNREACHABLE" : "SIM";
    hud(liveMode, null, data.mode === "LIVE" ? null : t("js_ph_model"));
    loom.setData(data);
  }
  await poll();
  setInterval(poll, 60000);
}

let initial = "en";
try { initial = new URLSearchParams(location.search).get("lang") || localStorage.getItem("histor-lang") || (navigator.language || "en").slice(0, 2); } catch { /* storage blocked */ }
applyLang(initial, false);
if (currentRoute() !== "/") {
  // A deep link (/s/<id>, /log …) lands on the desk, not on the hero above it.
  setTimeout(() => { document.getElementById("desk").scrollIntoView({ block: "start", inline: "nearest" }); window.scrollTo({ left: 0 }); }, 0);
}
loadMetrics();
startLoom();
