const MAX_INPUT_LENGTH = 1_000_000;
const MAX_TOOLS = 500;
const MAX_NAME_LENGTH = 256;
const NOTE_NAMES_PER_GROUP = 8;
const NOTE_NAME_LENGTH = 60;
const NOTE_LINK = "https://histor.modelmarket.dev/mongodb-mcp-read-only-tools.html?from=shared-review-note";

const form = document.getElementById("compare-tools");
const before = document.getElementById("tools-before");
const after = document.getElementById("tools-after");
const status = document.getElementById("compare-status");
const result = document.getElementById("compare-result");
const example = document.getElementById("compare-example");
const submit = form.querySelector('button[type="submit"]');
const review = document.getElementById("review-note");
const copyReview = document.getElementById("copy-review-note");
const reviewStatus = document.getElementById("review-note-status");
const reviewFallback = document.getElementById("review-note-fallback");
const reviewText = document.getElementById("review-note-text");
let comparingExample = false;

function readToolNames(input) {
  if (input.length > MAX_INPUT_LENGTH) {
    throw new Error("Use at most 1,000,000 characters.");
  }
  if (!input.trim()) {
    throw new Error("Paste a tool list or enter [] for an empty list.");
  }

  let data;
  try {
    data = JSON.parse(input);
  } catch {
    throw new Error("Enter valid JSON.");
  }

  const tools = Array.isArray(data) ? data : data?.result?.tools ?? data?.tools;
  if (!Array.isArray(tools)) {
    throw new Error("Use a tools/list response, an object with a tools array, or an array of tool objects.");
  }
  if (tools.length > MAX_TOOLS) {
    throw new Error("Use at most 500 tools.");
  }

  const names = new Set();
  for (const [index, tool] of tools.entries()) {
    const name = tool?.name;
    if (typeof name !== "string" || !name.trim() || name.length > MAX_NAME_LENGTH) {
      throw new Error(`Tool ${index + 1} needs a nonblank name of at most 256 characters.`);
    }
    if (names.has(name)) {
      throw new Error(`Tool ${index + 1} repeats a name already in this list.`);
    }
    names.add(name);
  }
  return names;
}

function appendNames(heading, names) {
  const title = document.createElement("h3");
  title.textContent = `${heading} (${names.length})`;
  result.append(title);

  if (!names.length) {
    const empty = document.createElement("p");
    empty.textContent = "None.";
    result.append(empty);
    return;
  }

  const list = document.createElement("ul");
  for (const name of names) {
    const item = document.createElement("li");
    const label = document.createElement("bdi");
    label.textContent = name;
    item.append(label);
    list.append(item);
  }
  result.append(list);
}

function noteToolName(name) {
  const characters = Array.from(name);
  const shortName = characters.length > NOTE_NAME_LENGTH
    ? characters.slice(0, NOTE_NAME_LENGTH).join("") + "…"
    : name;
  const visibleName = shortName.replace(/[\p{Cc}\p{Cf}\u2028\u2029]/gu,
    (ch) => `⟨U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}⟩`);
  // Longer delimiters keep even pasted backticks inside the Markdown code span.
  const ticks = "`".repeat(1 + Math.max(0, ...(visibleName.match(/`+/g) || []).map((run) => run.length)));
  return `${ticks} ${visibleName} ${ticks}`;
}

function noteLine(label, names) {
  if (!names.length) return `- ${label} (0)`;
  const sample = names.slice(0, NOTE_NAMES_PER_GROUP).map(noteToolName).join(", ");
  const more = names.length > NOTE_NAMES_PER_GROUP ? ` (+${names.length - NOTE_NAMES_PER_GROUP} more)` : "";
  return `- ${label} (${names.length}) — ${sample}${more}`;
}

function clearComparison() {
  result.replaceChildren();
  status.textContent = "";
  before.removeAttribute("aria-invalid");
  after.removeAttribute("aria-invalid");
  if (!review) return;
  review.hidden = true;
  reviewFallback.hidden = true;
  reviewStatus.textContent = "";
  reviewText.value = "";
}

form.addEventListener("input", clearComparison);
form.addEventListener("submit", (event) => {
  event.preventDefault();
  clearComparison();

  const lists = [];
  for (const [field, label] of [[before, "Before"], [after, "After"]]) {
    try {
      lists.push(readToolNames(field.value));
    } catch (error) {
      status.textContent = `${label} — ${error.message}`;
      field.setAttribute("aria-invalid", "true");
      field.focus();
      return;
    }
  }

  const [beforeNames, afterNames] = lists;
  const added = [...afterNames].filter((name) => !beforeNames.has(name)).sort();
  const removed = [...beforeNames].filter((name) => !afterNames.has(name)).sort();
  appendNames("Added", added);
  appendNames("Removed", removed);
  status.textContent = added.length || removed.length
    ? `Compared tool names. Added ${added.length}, removed ${removed.length}.`
    : "The two lists contain the same tool names.";
  if (!review) return;
  reviewText.value = [
    comparingExample ? "**MongoDB MCP read-only tool comparison (example)**" : "**MongoDB MCP read-only tool comparison**",
    "",
    noteLine("Added", added),
    noteLine("Removed", removed),
    "",
    "Compared advertised tool names only. Verify database permissions separately.",
    "",
    `[Compare your tool lists](${NOTE_LINK})`,
  ].join("\n");
  review.hidden = false;
});

copyReview?.addEventListener("click", async () => {
  const note = reviewText.value;
  if (!note) return;
  try {
    await navigator.clipboard.writeText(note);
    if (reviewText.value !== note) return;
    reviewFallback.hidden = true;
    reviewStatus.textContent = "Review note copied.";
  } catch {
    if (reviewText.value !== note) return;
    reviewFallback.hidden = false;
    reviewStatus.textContent = "Copy the selected review note manually.";
    reviewText.focus();
    reviewText.select();
  }
});

example.addEventListener("click", () => {
  before.value = JSON.stringify([{ name: "find" }, { name: "insert-many" }], null, 2);
  after.value = JSON.stringify([{ name: "find" }], null, 2);
  comparingExample = true;
  form.requestSubmit(submit);
  comparingExample = false;
});
example.disabled = false;
submit.disabled = false;
