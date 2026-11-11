// Same-origin counts only. Never send an endpoint, tool, referring path, cookie or browser ID.
const SOURCES = new Set(["direct", "devto", "x", "bluesky", "blog", "github", "dataset", "catalog", "other", "qa"]);
const DAY = 86_400_000;
const LAST_CHECK = "histor-last-signed-check";
const SOURCE_KEY = "histor-journey-source";
const params = new URLSearchParams(location.search);
const enabled = /^(histor\.modelmarket\.dev|localhost|127\.0\.0\.1)$/.test(location.hostname)
  && navigator.globalPrivacyControl !== true && navigator.doNotTrack !== "1"
  && (!navigator.webdriver || params.get("utm_source") === "qa");

function source() {
  const raw = params.get("utm_source")?.toLowerCase();
  if (raw) return SOURCES.has(raw) ? raw : "other";
  try {
    const referrer = new URL(document.referrer);
    const host = referrer.hostname;
    if (host !== location.hostname) {
      if (["dev.to", "www.dev.to"].includes(host)) return "devto";
      if (["t.co", "x.com", "twitter.com"].includes(host)) return "x";
      if (host === "bsky.app") return "bluesky";
      if (host === "github.com") return "github";
      if (host === "huggingface.co" || host === "zenodo.org") return "dataset";
      if (["glama.ai", "pulsemcp.com", "www.pulsemcp.com"].includes(host)) return "catalog";
      if (host === "modelmarket.dev" && referrer.pathname.startsWith("/blog/")) return "blog";
      return "other";
    }
  } catch { /* no referrer */ }
  try {
    const saved = sessionStorage.getItem(SOURCE_KEY);
    if (SOURCES.has(saved)) return saved;
  } catch { /* storage blocked */ }
  return "direct";
}

const cohort = enabled ? source() : "direct";
if (enabled) {
  try { sessionStorage.setItem(SOURCE_KEY, cohort); } catch { /* storage blocked */ }
}

export function track(event) {
  if (!enabled) return;
  fetch(`/api/v1/journey/${cohort}/${event}`, {
    method: "POST", credentials: "omit", keepalive: true, referrerPolicy: "origin",
  }).catch(() => {});
}

const viewed = new Set();
export function trackDeskVisit(route) {
  const event = route.startsWith("/s/") ? "record_viewed"
    : route === "/watch" ? "watch_viewed" : route === "/check" ? "check_viewed" : null;
  if (event && !viewed.has(event)) { viewed.add(event); track(event); }
}

let notedCheck = false;
export function trackSuccessfulCheck(sample) {
  track(sample ? "sample_succeeded" : "check_succeeded");
  if (!enabled || sample || cohort === "qa" || notedCheck) return;
  notedCheck = true;
  try {
    const now = Date.now();
    const ago = now - Number(localStorage.getItem(LAST_CHECK));
    if (ago >= DAY && ago <= 30 * DAY) track("check_repeated");
    localStorage.setItem(LAST_CHECK, String(now));
  } catch { /* a repeat is unknown if storage is blocked */ }
}

document.addEventListener("click", (ev) => {
  const event = ev.target instanceof Element ? ev.target.closest("[data-journey]")?.dataset.journey : null;
  if (event === "watch_feed_clicked" || event === "pilot_contact_clicked") track(event);
}, true);
