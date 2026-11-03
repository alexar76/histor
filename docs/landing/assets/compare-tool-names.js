const MAX_INPUT_LENGTH = 1_000_000;
const MAX_TOOLS = 500;
const MAX_NAME_LENGTH = 256;

const form = document.getElementById("compare-tools");
const before = document.getElementById("tools-before");
const after = document.getElementById("tools-after");
const status = document.getElementById("compare-status");
const result = document.getElementById("compare-result");
const example = document.getElementById("compare-example");
const submit = form.querySelector('button[type="submit"]');

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

form.addEventListener("submit", (event) => {
  event.preventDefault();
  result.replaceChildren();
  status.textContent = "";
  before.removeAttribute("aria-invalid");
  after.removeAttribute("aria-invalid");

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
});

example.addEventListener("click", () => {
  before.value = JSON.stringify([{ name: "find" }, { name: "insert-many" }], null, 2);
  after.value = JSON.stringify([{ name: "find" }], null, 2);
  form.requestSubmit(submit);
});
example.disabled = false;
submit.disabled = false;
