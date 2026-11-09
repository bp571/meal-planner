import { T } from "./texts.js";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const MEALS = ["breakfast", "lunch", "dinner", "snack"];

const $ = (selector) => document.querySelector(selector);
const log = $("#log");
const input = $("#input");
const statusLine = $("#status");

// ---------- Helpers ----------

/** Build an element. Strings become text nodes, so data from the server is never parsed as HTML. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter((child) => child != null && child !== false));
  return node;
}

const number = new Intl.NumberFormat(T.locale, { maximumFractionDigits: 2 });
const oneDecimal = new Intl.NumberFormat(T.locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const kcal = (n) => T.kcal(number.format(Math.round(n / 50) * 50));
const grams = (n) => number.format(Math.round(n));
const amountText = (item) => (item.amount == null ? T.shopping.asNeeded : `${number.format(item.amount)} ${item.unit}`.trim());

/** "2026-11-09" as a UTC date, so day arithmetic ignores time zones. */
const parseDay = (iso) => new Date(`${iso.slice(0, 10)}T00:00:00Z`);
const addDays = (date, n) => new Date(date.getTime() + n * 86400000);
const shortDate = (date) =>
  `${String(date.getUTCDate()).padStart(2, "0")}.${String(date.getUTCMonth() + 1).padStart(2, "0")}.`;

function isoWeek(date) {
  const thursday = addDays(date, 3 - ((date.getUTCDay() + 6) % 7));
  const firstDay = Date.UTC(thursday.getUTCFullYear(), 0, 1);
  return Math.floor((thursday - firstDay) / 86400000 / 7) + 1;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = response.headers.get("content-type")?.includes("json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(body?.errors ? `${T.errors.prefix} ${body.errors.join(" ")}` : T.errors.generic);
    error.status = response.status;
    error.detail = body?.detail; // FastAPI validation errors (422)
    throw error;
  }
  return body;
}

const badge = (text, kind) => el("span", { class: `badge ${kind}` }, text);
const empty = (title, hint) =>
  el("div", { class: "empty" }, el("p", { class: "empty-title" }, title), hint && el("p", {}, hint));

function note(kind, text) {
  if (kind === "memory") return el("p", { class: "note memory" }, el("strong", {}, T.chat.remembered), " ", text);
  return el("p", { class: "note error", role: "alert" }, text);
}

// ---------- Markdown (escape first, then a small safe subset) ----------

const escapeHtml = (text) =>
  text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const inline = (text) =>
  text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w])_(?!\s)(.+?)_(?!\w)/g, "$1<em>$2</em>");

function renderMarkdown(source) {
  const out = [];
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (paragraph.length) out.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const start = list.tag === "ol" && list.start !== 1 ? ` start="${list.start}"` : "";
    out.push(`<${list.tag}${start}>${list.items.map((item) => `<li>${inline(item)}</li>`).join("")}</${list.tag}>`);
    list = null;
  };
  for (const line of escapeHtml(source).split("\n")) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "ul" : "ol";
      if (list?.tag !== tag) {
        flushList();
        list = { tag, start: numbered ? Number(numbered[1]) : 1, items: [] };
      }
      list.items.push(bullet ? bullet[1] : numbered[2]);
    } else if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      out.push(`<p><strong>${inline(heading[1])}</strong></p>`);
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return out.join("");
}

// ---------- Chat ----------

let busy = false;

/** Run fn and keep the log pinned to the bottom if it was there before. */
function updateLog(fn) {
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 64;
  const result = fn();
  if (atBottom) log.scrollTop = log.scrollHeight;
  return result;
}

function addMessage(role, text) {
  const node = el("div", { class: `msg ${role}` });
  if (role === "assistant") node.innerHTML = renderMarkdown(text);
  else node.textContent = text;
  return updateLog(() => log.appendChild(node));
}

const addNote = (kind, text) => updateLog(() => log.appendChild(note(kind, text)));
const renderEntry = ({ role, text }) => (role === "memory" ? addNote("memory", text) : addMessage(role, text));

function setStatus(text) {
  statusLine.hidden = !text;
  statusLine.textContent = text ?? "";
}

function setBusy(value) {
  busy = value;
  log.setAttribute("aria-busy", String(value));
  for (const control of document.querySelectorAll('.composer textarea, .composer button:not([data-starter="addDish"]), #reset')) {
    control.disabled = value;
  }
  if (!value) input.focus();
}

const toolLabel = (name) => T.tools[name] ?? T.tools.default;

/** Areas from a "changed" event and the view that shows each. */
const AREA_VIEW = { profile: "profile", memory: "profile", dishes: "library", plan: "week", shopping: "shopping" };

async function* readNdjson(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line.trim()) yield JSON.parse(line);
  }
  if (buffer.trim()) yield JSON.parse(buffer);
}

/** POST to a chat endpoint and render the streamed events of one agent turn. */
async function runTurn(path, message) {
  setBusy(true);
  setStatus(T.chat.thinking);
  let reply = null; // { node, text } of the assistant message being streamed (assembled like app.py)
  const handlers = {
    text({ text }) {
      if (!reply) reply = { node: addMessage("assistant", ""), text: "" };
      reply.text += text;
      updateLog(() => (reply.node.innerHTML = renderMarkdown(reply.text)));
      setStatus(null);
    },
    tool({ name }) {
      setStatus(toolLabel(name));
    },
    memory({ text }) {
      reply = null;
      addNote("memory", text);
    },
    changed({ areas }) {
      new Set(areas.map((area) => AREA_VIEW[area]).filter(Boolean)).forEach((id) => views[id]());
    },
    error({ message }) {
      addNote("error", `${T.errors.prefix} ${message}`);
    },
  };
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: message == null ? undefined : JSON.stringify({ message }),
    });
    if (!response.ok) throw new Error(response.status === 409 ? T.chat.busy : T.errors.generic);
    for await (const event of readNdjson(response)) handlers[event.type]?.(event);
  } catch (error) {
    addNote("error", error.message || T.errors.generic);
  } finally {
    setStatus(null);
    setBusy(false);
  }
}

function send(text) {
  text = text.trim();
  if (!text || busy) return;
  input.value = "";
  addMessage("user", text);
  runTurn("/api/chat", text);
}

async function newConversation() {
  if (!confirm(T.chat.resetConfirm)) return;
  try {
    await api("/api/chat/reset", { method: "POST" });
  } catch (error) {
    addNote("error", error.status === 409 ? T.chat.busy : error.message);
    return;
  }
  log.replaceChildren();
  await runTurn("/api/chat/start");
}

// ---------- Tabs ----------

/** A tab loader: fetches, renders into its panel, and ignores answers that a newer load overtook. */
function view(id, render) {
  const root = $(`#view-${id}`);
  let latest = 0;
  return async () => {
    const mine = ++latest;
    let content;
    try {
      content = await render();
    } catch (error) {
      content = note("error", error.message || T.errors.generic);
    }
    if (mine === latest) root.replaceChildren(...[content].flat(Infinity).filter(Boolean));
  };
}

async function removeThen(path, viewId) {
  try {
    await api(path, { method: "DELETE" });
    views[viewId]();
  } catch (error) {
    $(`#view-${viewId}`).prepend(note("error", error.message));
  }
}

// Woche

function slotNote(slot) {
  if (slot.kind !== "dish") return T.week.kinds[slot.kind];
  if (!slot.cook) return T.week.leftover(T.days[slot.cooked_on]);
  if (slot.covers.length) return T.week.cook(slot.covers.map((d) => T.days[d]).join(", "));
  return null;
}

// Like a paper pad: leftovers get a ditto mark, cooking for several days gets the highlighter.
function slotNode(slot) {
  const leftover = slot.kind === "dish" && !slot.cook;
  const cooks = slot.kind === "dish" && slot.cook && slot.covers.length > 0;
  const note = slotNote(slot);
  const text = [leftover && el("span", { class: "ditto", "aria-hidden": "true" }, "″"), el("span", { class: "ink" }, slot.name)];
  // Slots with a dish open its card; flex and unplanned slots are plain text.
  const name = slot.dish_id == null
    ? el("span", { class: "slot-name" }, text)
    : el("button", { type: "button", class: "slot-name", "aria-haspopup": "dialog", onclick: () => openDish(slot.dish_id) }, text);
  return el(
    "div",
    { class: `slot kind-${slot.kind}${leftover ? " leftover" : ""}${cooks ? " cooks" : ""}` },
    name,
    slot.kcal > 0 && el("span", { class: "slot-kcal" }, kcal(slot.kcal)),
    note && el("span", { class: "slot-note" }, note),
  );
}

const dishCard = $("#dish-card");

/** Show a dish of the week as an index card, in the same layout as an import draft. */
async function openDish(dishId) {
  let content;
  try {
    const dish = await api(`/api/dishes/${dishId}`);
    dishCard.setAttribute("aria-label", dish.name);
    content = draftSummary({ dish });
  } catch (error) {
    dishCard.removeAttribute("aria-label");
    content = note("error", error.message || T.errors.generic);
  }
  const close = el("form", { method: "dialog", class: "dish-card-close" }, el("button", { class: "quiet", "aria-label": T.week.closeDish }, "×"));
  dishCard.replaceChildren(el("div", { class: "dish-card-body" }, close, content));
  if (!dishCard.open) dishCard.showModal();
}

function weekSummary(check) {
  const ratio = check.week_kcal_target ? check.week_kcal / check.week_kcal_target : 0;
  // The bar runs to 125 % of the target; the tick marks 100 %.
  const width = Math.min(ratio / 1.25, 1) * 100;
  return el(
    "div",
    { class: "summary" },
    el(
      "p",
      { class: "summary-kcal" },
      el("strong", { class: "summary-number" }, kcal(check.week_kcal)),
      el("span", {}, T.week.thisWeek),
      el("span", { class: "summary-target" }, T.week.ofTarget(kcal(check.week_kcal_target))),
    ),
    el("div", { class: ratio > 1 ? "bar over" : "bar", "aria-hidden": "true" }, el("span", { style: `width: ${width}%` })),
    el("p", { class: "summary-protein" }, T.week.proteinTotal(grams(check.week_protein_g), check.week_protein_target_g && grams(check.week_protein_target_g))),
  );
}

async function renderWeek() {
  const plan = await api("/api/plan");
  if (!plan) return empty(T.week.emptyTitle, T.week.emptyHint);
  const start = parseDay(plan.week_start);
  const meals = MEALS.filter((meal) => plan.slots.some((slot) => slot.meal === meal));
  const totals = Object.fromEntries(plan.check.days.map((day) => [day.day, day]));
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const dayRow = (day, index) => {
    const total = totals[day];
    const date = addDays(start, index);
    const isToday = date.toISOString().slice(0, 10) === today;
    const weekend = day === "sat" || day === "sun";
    return el(
      "section",
      { class: `day${weekend ? " weekend" : ""}${isToday ? " today" : ""}` },
      el(
        "div",
        { class: "day-head" },
        el("h3", {}, el("span", { class: "day-short", "aria-hidden": "true" }, T.days[day]), el("span", { class: "sr-only" }, T.weekdays[day])),
        el("span", { class: "day-date" }, shortDate(date)),
        isToday && el("span", { class: "today-tag" }, T.week.today),
        total && el("p", { class: "day-total" }, el("span", {}, kcal(total.kcal)), el("span", {}, T.protein(grams(total.protein_g)))),
      ),
      meals.map((meal) => {
        const slots = plan.slots.filter((slot) => slot.day === day && slot.meal === meal);
        return el(
          "div",
          { class: "cell" },
          el("span", { class: "cell-meal" }, T.meals[meal]),
          el("div", { class: "cell-slots" }, slots.length ? slots.map(slotNode) : el("span", { class: "muted" }, T.week.emptyCell)),
        );
      }),
    );
  };
  return [
    el(
      "header",
      { class: "view-head sheet-head" },
      el("h2", {}, el("span", { class: "preprint" }, T.week.sheet), " ", el("span", { class: "written" }, T.week.title(isoWeek(start)))),
      el("span", { class: "muted" }, T.week.from(shortDate(start))),
      badge(plan.status === "final" ? T.week.final : T.week.draft, plan.status),
    ),
    weekSummary(plan.check),
    el(
      "div",
      { class: "week", style: `--meals: ${meals.length}` },
      el("div", { class: "week-cols", "aria-hidden": "true" }, el("span"), meals.map((meal) => el("span", {}, T.meals[meal]))),
      DAYS.map(dayRow),
    ),
  ];
}

// Einkauf

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = el("textarea", { readonly: true, style: "position: fixed; opacity: 0" });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) throw new Error("copy failed");
  }
}

/** One trip: a perforation and its list. With several trips each one gets its own head and copy button. */
function tripNodes(trip, several) {
  const confirmation = el("span", { class: "copied", role: "status" });
  const perforation = el("div", { class: "perforation", "aria-hidden": "true" });
  const departments = Object.entries(trip.departments);
  const days = trip.days.length === 1 ? T.days[trip.days[0]] : `${T.days[trip.days[0]]}–${T.days[trip.days.at(-1)]}`;
  const copyButton = el("div", { class: "copy" }, el("button", { type: "button", class: "primary", onclick: () => copy() }, T.shopping.copy), confirmation);
  const body = el(
    "div",
    { class: "strip-body" },
    several &&
      el(
        "header",
        { class: "trip-head" },
        el("h3", {}, T.shopping.trip(T.weekdays[trip.day], shortDate(parseDay(trip.shopping_date)))),
        el("p", { class: "muted" }, T.shopping.covers(days)),
        copyButton,
      ),
    departments.length === 0 && el("p", { class: "hint" }, T.shopping.nothing),
    departments.map(([department, items]) =>
      el(
        "section",
        { class: "department" },
        el("h3", {}, T.departments[department] ?? department),
        el("ul", {}, items.map((item) => el("li", {}, el("span", { class: "amount" }, amountText(item)), el("span", { class: "item" }, item.name)))),
      ),
    ),
  );
  // Copying tears the list off the pad: the tear runs along the perforation (see styles.css).
  const tear = () => {
    for (const node of [perforation, body]) {
      node.classList.remove("torn");
      void node.offsetWidth; // restart the animation on a second copy
      node.classList.add("torn");
    }
  };
  const copy = async () => {
    try {
      await copyText(trip.text);
      confirmation.textContent = T.shopping.copied;
      tear();
    } catch {
      confirmation.textContent = T.shopping.copyFailed;
    }
  };
  return { copyButton, nodes: [perforation, body] };
}

async function renderShopping() {
  const [list, state] = await Promise.all([api("/api/shopping"), api("/api/state")]);
  if (!list) return empty(T.shopping.emptyTitle, T.shopping.emptyHint);
  const several = list.trips.length > 1;
  const trips = list.trips.map((trip) => tripNodes(trip, several));
  return [
    el(
      "header",
      { class: "strip-head" },
      el("h2", {}, T.shopping.title),
      el("p", { class: "muted" }, several ? T.shopping.trips(list.trips.length) : T.shopping.from(shortDate(parseDay(list.week_start)))),
      !several && trips[0].copyButton,
    ),
    state.plan?.status === "draft" && el("p", { class: "hint" }, T.shopping.draftHint),
    trips.map((trip) => trip.nodes),
    list.skipped.length > 0 && el("p", { class: "hint" }, T.shopping.skipped(list.skipped.join(", "))),
  ];
}

// Gerichte: import, drafts to check, then the library

const importForm = $("#import");
const importSource = $("#import-source");
const importStatus = $("#import-status");
const importCount = $("#import-count");
const importLog = $("#import-log");

/** Lock the import form while an import runs and show its progress. */
function setImporting(on, text) {
  for (const control of importForm.querySelectorAll("textarea, button")) control.disabled = on;
  $("#import-progress").hidden = !on;
  importStatus.textContent = text ?? "";
  importCount.hidden = true;
}

function setProgress(done, total) {
  const bar = importCount.querySelector(".bar");
  importCount.hidden = !total;
  bar.setAttribute("aria-valuemax", total);
  bar.setAttribute("aria-valuenow", done);
  bar.firstElementChild.style.width = `${total ? (done / total) * 100 : 0}%`;
  importCount.lastElementChild.textContent = T.import.progress(done, total);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** An import this page did not start (page reload, other tab): poll until it stops. */
async function followImport(status) {
  setImporting(true, T.import.running);
  try {
    while (status.running) {
      setProgress(status.done, status.total);
      await sleep(2000);
      status = await api("/api/import/status");
    }
  } catch (error) {
    importLog.append(note("error", error.message));
  }
  setImporting(false);
  refreshDishes(false);
}

async function runImport(source) {
  importLog.replaceChildren();
  setImporting(true, T.import.starting);
  let count = null;
  const handlers = {
    status: ({ text }) => (importStatus.textContent = text),
    progress: ({ done, total }) => setProgress(done, total),
    drafts: () => loadDrafts(),
    error: ({ message }) => importLog.append(note("error", `${T.errors.prefix} ${message}`)),
    done: (event) => (count = event.count),
  };
  try {
    const response = await fetch("/api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    if (response.status === 409) return followImport(await api("/api/import/status"));
    if (!response.ok) throw new Error(T.errors.generic);
    for await (const event of readNdjson(response)) handlers[event.type]?.(event);
  } catch (error) {
    importLog.append(note("error", error.message || T.errors.generic));
  }
  setImporting(false);
  if (count != null) {
    if (count > 0) importSource.value = ""; // keep the text if nothing came out of it
    importLog.append(el("p", { class: "import-result", role: "status" }, T.import.result(count)));
  }
  refreshDishes(false);
}

/** After a save, discard or import. Saved dishes can change the week's kcal and the shopping list. */
function refreshDishes(saved) {
  loadDrafts();
  views.library();
  if (saved) {
    views.week();
    views.shopping();
  }
}

/** Drafts by id. Each keeps its edited dish and its card node, so a reload keeps edits and focus. */
const drafts = new Map();
let draftLoads = 0;
let bulkRunning = false;

async function loadDrafts() {
  const mine = ++draftLoads;
  let list;
  try {
    list = await api("/api/drafts");
  } catch (error) {
    importLog.append(note("error", error.message));
    return;
  }
  if (mine !== draftLoads) return;
  const ids = new Set(list.map((draft) => draft.id));
  for (const state of drafts.values()) if (!ids.has(state.id)) dropDraft(state);
  for (const draft of list) {
    const state = drafts.get(draft.id);
    if (!state) {
      const fresh = { ...draft, editing: false, error: null, nutritionPicked: false };
      fresh.node = draftNode(fresh);
      drafts.set(fresh.id, fresh);
      $("#draft-list").append(fresh.node);
    } else if (state.duplicate_of !== draft.duplicate_of) {
      state.duplicate_of = draft.duplicate_of;
      renderDraft(state);
    }
  }
  updateDraftsHead();
}

function updateDraftsHead() {
  const states = [...drafts.values()];
  $("#drafts").hidden = !states.length;
  $("#drafts").inert = bulkRunning;
  $("#drafts-title").textContent = T.drafts.title(states.length);
  $("#save-all").disabled = !states.some((state) => state.duplicate_of == null);
}

function renderDraft(state) {
  const node = draftNode(state);
  state.node.replaceWith(node);
  state.node = node;
}

function dropDraft(state) {
  state.node.remove();
  drafts.delete(state.id);
  updateDraftsHead();
}

/** German field names from a FastAPI 422 body, e.g. "kcal pro Portion, Zutat 2 (Name)". */
function invalidFields(detail = []) {
  const F = T.drafts.fields;
  const names = detail.map(({ loc }) => {
    const [key, index, sub] = loc.slice(loc.indexOf("dish") + 1);
    return key === "ingredients" && sub ? T.drafts.ingredientField(index + 1, F[sub] ?? sub) : (F[key] ?? key);
  });
  return [...new Set(names)].join(", ");
}

async function saveDraft(state) {
  state.node.inert = true;
  try {
    await api(`/api/drafts/${state.id}/save`, {
      method: "POST",
      body: JSON.stringify({ dish: state.dish, replace: state.duplicate_of != null }),
    });
    dropDraft(state);
    return true;
  } catch (error) {
    state.error = error.status === 422 ? T.drafts.invalid(invalidFields(error.detail)) : error.message;
    renderDraft(state);
    return false;
  }
}

async function discardDraft(state) {
  state.node.inert = true;
  try {
    await api(`/api/drafts/${state.id}`, { method: "DELETE" });
    dropDraft(state);
  } catch (error) {
    state.error = error.message;
    renderDraft(state);
  }
}

/** Run a bulk action with the drafts area locked, then reload once. */
async function bulk(action) {
  bulkRunning = true;
  updateDraftsHead();
  const saved = await action([...drafts.values()]);
  bulkRunning = false;
  updateDraftsHead();
  refreshDishes(saved);
}

// Duplicates stay: replacing a dish in the library needs its own click.
const saveAll = () =>
  bulk(async (states) => {
    let saved = false;
    for (const state of states) if (state.duplicate_of == null) saved = (await saveDraft(state)) || saved;
    return saved;
  });

function discardAll() {
  if (!confirm(T.drafts.discardAllConfirm(drafts.size))) return;
  bulk(async (states) => {
    await Promise.all(states.map(discardDraft));
    return false;
  });
}

function toggleEdit(state) {
  state.editing = !state.editing;
  renderDraft(state);
  state.node.querySelector(state.editing ? "input" : ".edit-toggle").focus();
}

const webUrl = (text) => (URL.canParse(text) && /^https?:\/\//i.test(text) ? new URL(text) : null);
const numberValue = (input) => (input.value === "" ? null : input.valueAsNumber);
const options = (labels, selected) =>
  Object.entries(labels).map(([value, label]) => el("option", { value, selected: value === selected }, label));

function draftSummary({ dish, source }) {
  const link = webUrl(dish.source) ?? webUrl(source);
  return [
    el("h3", {}, dish.name),
    el(
      "p",
      { class: "facts-row" },
      el("span", { class: "strong" }, T.kcal(number.format(dish.kcal_per_serving))),
      el("span", { class: "strong" }, T.protein(grams(dish.protein_g_per_serving))),
      el("span", {}, T.drafts.perServing(T.nutritionShort[dish.nutrition_source] ?? dish.nutrition_source)),
    ),
    el(
      "p",
      { class: "facts-row" },
      el("span", {}, T.drafts.servings(number.format(dish.servings))),
      el("span", {}, T.proteinSources[dish.protein_source] ?? dish.protein_source),
      dish.meal_types.length > 0 && el("span", {}, dish.meal_types.map((meal) => T.meals[meal] ?? meal).join(", ")),
      dish.prep_minutes != null && el("span", {}, T.drafts.minutes(dish.prep_minutes)),
      dish.meal_prep && badge(T.drafts.mealPrep, "cook"),
    ),
    dish.ingredients.length > 0 &&
      el("ul", { class: "ingredients" }, dish.ingredients.map((item) => el("li", {}, el("span", { class: "amount" }, amountText(item)), el("span", {}, item.name)))),
    dish.notes && el("p", { class: "draft-notes" }, dish.notes),
    link && el("p", { class: "draft-source" }, T.drafts.source, " ", el("a", { href: link.href, target: "_blank", rel: "noopener noreferrer" }, link.hostname)),
  ];
}

function ingredientEditor(items) {
  const F = T.drafts.fields;
  const label = (index, key) => T.drafts.ingredientField(index + 1, F[key]);
  const list = el("ul", { class: "ingredient-rows" });
  const render = () => list.replaceChildren(...items.map(row));
  const row = (item, index) =>
    el(
      "li",
      { class: "ingredient-row" },
      el("input", { type: "number", min: 0, step: "any", inputmode: "decimal", class: "ing-amount", value: item.amount, placeholder: F.amount, "aria-label": label(index, "amount"), oninput: (e) => (item.amount = numberValue(e.target)) }),
      el("input", { type: "text", class: "ing-unit", value: item.unit, placeholder: F.unit, "aria-label": label(index, "unit"), oninput: (e) => (item.unit = e.target.value) }),
      el("input", { type: "text", class: "ing-name", value: item.name, placeholder: T.drafts.ingredient, "aria-label": label(index, "name"), oninput: (e) => (item.name = e.target.value) }),
      el("select", { class: "ing-dept", "aria-label": label(index, "department"), onchange: (e) => (item.department = e.target.value) }, options(T.departments, item.department)),
      el("button", { type: "button", class: "quiet danger ing-remove", "aria-label": T.drafts.removeIngredient(item.name), onclick: () => { items.splice(index, 1); render(); add.focus(); } }, "×"),
    );
  const add = el("button", {
    type: "button",
    class: "quiet",
    onclick: () => {
      items.push({ name: "", amount: null, unit: "", department: "other" });
      render();
      list.lastElementChild.querySelector("input").focus();
    },
  }, `+ ${T.drafts.addIngredient}`);
  render();
  return el(
    "fieldset",
    { class: "field wide" },
    el("legend", {}, F.ingredients),
    el("div", { class: "ingredient-head", "aria-hidden": "true" }, el("span", {}, F.amount), el("span", {}, F.unit), el("span", {}, T.drafts.ingredient), el("span", {}, F.department)),
    list,
    el("div", {}, add),
  );
}

function draftForm(state) {
  const { dish } = state;
  const F = T.drafts.fields;
  const field = (key, control, wide) => el("label", { class: wide ? "field wide" : "field" }, el("span", {}, F[key]), control);
  const text = (key) => el("input", { type: "text", value: dish[key], oninput: (e) => (dish[key] = e.target.value) });
  const num = (key, step, onEdit) =>
    el("input", { type: "number", min: 0, step, inputmode: "decimal", value: dish[key], oninput: (e) => { dish[key] = numberValue(e.target); onEdit?.(); } });
  const choice = (key, labels, onPick) =>
    el("select", { onchange: (e) => { dish[key] = e.target.value; onPick?.(); } }, options(labels, dish[key]));
  const check = (label, checked, onChange) =>
    el("label", { class: "check" }, el("input", { type: "checkbox", checked, onchange: (e) => onChange(e.target.checked) }), label);

  // Typed-in nutrition values count as the user's own, unless the user picked a source.
  const nutrition = choice("nutrition_source", T.nutritionShort, () => (state.nutritionPicked = true));
  const ownValue = () => {
    if (!state.nutritionPicked) nutrition.value = dish.nutrition_source = "user";
  };
  return el(
    "div",
    { class: "draft-form" },
    field("name", text("name"), true),
    field("servings", num("servings", "any")),
    field("kcal_per_serving", num("kcal_per_serving", 1, ownValue)),
    field("protein_g_per_serving", num("protein_g_per_serving", "any", ownValue)),
    field("nutrition_source", nutrition),
    field("protein_source", choice("protein_source", T.proteinSources)),
    field("prep_minutes", num("prep_minutes", 1)),
    check(F.meal_prep, dish.meal_prep, (on) => (dish.meal_prep = on)),
    el(
      "fieldset",
      { class: "field wide" },
      el("legend", {}, F.meal_types),
      el("div", { class: "checks" }, MEALS.map((meal) =>
        check(T.meals[meal], dish.meal_types.includes(meal), (on) => {
          dish.meal_types = MEALS.filter((m) => (m === meal ? on : dish.meal_types.includes(m)));
        }))),
    ),
    ingredientEditor(dish.ingredients),
    field("notes", el("textarea", { rows: 2, oninput: (e) => (dish.notes = e.target.value) }, dish.notes), true),
  );
}

function draftNode(state) {
  const duplicate = state.duplicate_of != null;
  const save = async () => (await saveDraft(state)) && refreshDishes(true);
  const discard = async () => {
    await discardDraft(state);
    refreshDishes(false);
  };
  return el(
    "li",
    { class: duplicate ? "draft duplicate" : "draft" },
    duplicate && el("p", { class: "draft-duplicate" }, T.drafts.duplicate),
    state.editing ? draftForm(state) : draftSummary(state),
    state.error && note("error", state.error),
    el(
      "div",
      { class: "draft-actions" },
      el("button", { type: "button", class: "edit-toggle", onclick: () => toggleEdit(state) }, state.editing ? T.drafts.done : T.drafts.edit),
      el("button", { type: "button", class: "quiet danger", onclick: discard }, T.drafts.discard),
      el("button", { type: "button", class: "primary", onclick: save }, duplicate ? T.drafts.replace : T.drafts.save),
    ),
  );
}

function openImport() {
  showTab($("#tab-dishes"));
  importSource.focus();
}

function setupImport() {
  importSource.placeholder = T.import.placeholder;
  importCount.querySelector(".bar").setAttribute("aria-label", T.import.progressLabel);
  importForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const source = importSource.value.trim();
    if (source) runImport(source);
  });
  $("#save-all").addEventListener("click", saveAll);
  $("#discard-all").addEventListener("click", discardAll);
}

function dishNode(dish) {
  const stats = dish.stats;
  const rating = stats?.ratings
    ? el("span", { class: "rating", "aria-label": T.dishes.ratingLabel(oneDecimal.format(stats.avg_score), stats.ratings) }, T.dishes.rating(oneDecimal.format(stats.avg_score)))
    : el("span", { class: "muted" }, T.dishes.unrated);
  const remove = () => {
    if (confirm(T.dishes.deleteConfirm(dish.name))) removeThen(`/api/dishes/${dish.id}`, "library");
  };
  return el(
    "li",
    { class: "dish" },
    el(
      "div",
      {},
      el("h3", {}, dish.name),
      el(
        "p",
        { class: "facts-row" },
        el("span", { class: "strong" }, kcal(dish.kcal_per_serving)),
        el("span", { class: "strong" }, T.protein(grams(dish.protein_g_per_serving))),
        el("span", {}, T.dishes.perServing),
      ),
      el(
        "p",
        { class: "facts-row" },
        el("span", {}, T.proteinSources[dish.protein_source] ?? dish.protein_source),
        el("span", {}, dish.meal_types.map((meal) => T.meals[meal] ?? meal).join(", ")),
        el("span", {}, T.nutritionSources[dish.nutrition_source] ?? dish.nutrition_source),
      ),
    ),
    el(
      "div",
      { class: "dish-side" },
      rating,
      el("button", { type: "button", class: "quiet danger", "aria-label": T.dishes.deleteLabel(dish.name), onclick: remove }, T.dishes.delete),
    ),
  );
}

async function renderDishes() {
  const dishes = await api("/api/dishes");
  if (!dishes.length) return empty(T.dishes.emptyTitle, T.dishes.emptyHint);
  return [
    el("header", { class: "view-head" }, el("h2", {}, T.dishes.title(dishes.length))),
    el("ul", { class: "dishes" }, dishes.map(dishNode)),
  ];
}

// Profil & Gedächtnis

function profileRows(p, dishNames) {
  const P = T.profile;
  const joined = (items) => (items.length ? items.join(", ") : P.none);
  const listed = (items) => (items.length ? el("ul", {}, items.map((item) => el("li", {}, item))) : P.none);
  const days = (list) => (list.length === 7 ? P.daily : list.map((d) => T.days[d]).join(", "));
  const limits = [
    p.max_meat_meals_per_week != null && P.meatLimit(p.max_meat_meals_per_week),
    p.max_fish_meals_per_week != null && P.fishLimit(p.max_fish_meals_per_week),
  ].filter(Boolean);
  return [
    [P.goal, P.goals[p.goal]],
    [P.kcalTarget, P.perDay(kcal(p.kcal_target))],
    [P.proteinTarget, p.protein_target_g ? P.perDay(`${p.protein_target_g} g`) : P.none],
    [P.calorieMode, P.calorieModes[p.calorie_mode]],
    [P.household, P.persons(p.household_size)],
    [P.meals, p.meals.map((m) => T.meals[m]).join(", ")],
    [P.breakfast, P.breakfastVariety[p.breakfast_variety]],
    [P.cookingTime, P.cookingTimeValue(p.cooking_minutes_weekday, p.cooking_minutes_weekend)],
    [P.mealPrep, p.meal_prep ? P.mealPrepYes(p.max_same_dish_days) : P.no],
    [P.diet, P.diets[p.diet]],
    limits.length > 0 && [P.limits, limits.join(", ")],
    [P.allergies, joined(p.allergies)],
    [P.dislikes, joined(p.dislikes)],
    [P.fixedItems, listed(p.fixed_items.map((i) =>
      P.fixedItem(dishNames[i.dish_id] ?? `#${i.dish_id}`, T.meals[i.meal], days(i.days), i.servings === 1 ? null : number.format(i.servings))))],
    [P.flexMeals, listed(p.flex_meals.map((f) =>
      P.flexMeal(f.label, f.per_week, T.meals[f.meal], kcal(f.kcal), f.day && T.days[f.day])))],
    [P.unplannedMeals, listed(p.unplanned_meals.map((u) =>
      P.unplannedMeal(u.label, T.meals[u.meal], days(u.days), u.kcal == null ? null : kcal(u.kcal))))],
    [P.shoppingDays, joined(p.shopping_days.map((d) => T.days[d]))],
    [P.pantry, joined(p.pantry_staples)],
    [P.equipment, joined(p.equipment)],
    p.notes && [P.notes, p.notes],
  ].filter(Boolean);
}

function memoryNode(entry) {
  // Timestamps are UTC; SQLite returns them without a zone, which the browser would read as local time.
  const iso = /(Z|[+-]\d\d:\d\d)$/.test(entry.created_at) ? entry.created_at : `${entry.created_at}Z`;
  const date = new Date(iso).toLocaleDateString(T.locale, { day: "2-digit", month: "2-digit", year: "numeric" });
  return el(
    "li",
    { class: "memory-entry" },
    el("div", {}, el("p", {}, entry.text), el("p", { class: "facts-row" }, el("span", {}, T.memory.categories[entry.category] ?? entry.category), el("span", {}, date))),
    el("button", { type: "button", class: "quiet danger", "aria-label": T.memory.deleteLabel(entry.text), onclick: () => removeThen(`/api/memories/${entry.id}`, "profile") }, T.memory.delete),
  );
}

async function renderProfile() {
  const [{ profile, dish_names }, memories] = await Promise.all([api("/api/profile"), api("/api/memories")]);
  return [
    el("header", { class: "view-head" }, el("h2", {}, T.profile.title)),
    profile
      ? el("dl", { class: "profile" }, profileRows(profile, dish_names).map(([label, value]) => el("div", {}, el("dt", {}, label), el("dd", {}, value))))
      : empty(T.profile.empty),
    el("header", { class: "view-head" }, el("h2", {}, T.memory.title)),
    memories.length ? el("ul", { class: "memories" }, memories.map(memoryNode)) : el("p", { class: "hint" }, T.memory.empty),
  ];
}

const views = {
  week: view("week", renderWeek),
  shopping: view("shopping", renderShopping),
  library: view("library", renderDishes),
  profile: view("profile", renderProfile),
};

// ---------- Setup ----------

const tabs = [...document.querySelectorAll('[role="tab"]')];

function showTab(tab) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  }
}

function setupTabs() {
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => showTab(tab));
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      const next = tabs[(index + step + tabs.length) % tabs.length];
      showTab(next);
      next.focus();
    });
  });
}

function setupChat() {
  input.placeholder = T.chat.placeholder;
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send(input.value);
    }
  });
  for (const button of document.querySelectorAll("[data-starter]")) {
    const starter = T.starters[button.dataset.starter];
    button.textContent = starter.label;
    button.addEventListener("click", () => (button.dataset.starter === "addDish" ? openImport() : send(starter.message)));
  }
  $("#reset").addEventListener("click", newConversation);
}

async function init() {
  document.documentElement.lang = T.lang;
  document.title = T.appName;
  for (const node of document.querySelectorAll("[data-t]")) {
    node.textContent = node.dataset.t.split(".").reduce((value, key) => value?.[key], T);
  }
  setupTabs();
  setupChat();
  setupImport();
  // A click on the backdrop closes the card (the body covers the whole card itself).
  dishCard.addEventListener("click", (event) => event.target === dishCard && dishCard.close());
  Object.values(views).forEach((load) => load());
  loadDrafts();
  api("/api/import/status")
    .then((status) => status.running && followImport(status))
    .catch((error) => importLog.append(note("error", error.message)));
  try {
    const history = await api("/api/chat/history");
    history.forEach(renderEntry);
    log.scrollTop = log.scrollHeight;
    if (!history.length) await runTurn("/api/chat/start");
  } catch (error) {
    addNote("error", error.message);
  }
}

init();
