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
  for (const control of document.querySelectorAll(".composer textarea, .composer button, #reset")) {
    control.disabled = value;
  }
  if (!value) input.focus();
}

const toolLabel = (name) => T.tools[name] ?? T.tools.default;

/** Areas from a "changed" event and the tab that shows each. */
const AREA_VIEW = { profile: "profile", memory: "profile", dishes: "dishes", plan: "week", shopping: "shopping" };

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

function slotBadge(slot) {
  if (slot.kind !== "dish") return badge(T.week.kinds[slot.kind], slot.kind);
  if (!slot.cook) return badge(T.week.leftover(T.days[slot.cooked_on]), "leftover");
  if (slot.covers.length) return badge(T.week.cook(slot.covers.map((d) => T.days[d]).join(", ")), "cook");
  return null;
}

const slotNode = (slot) =>
  el(
    "div",
    { class: `slot kind-${slot.kind}` },
    el("span", { class: "slot-name" }, slot.name),
    slot.kcal > 0 && el("span", { class: "slot-kcal" }, kcal(slot.kcal)),
    slotBadge(slot),
  );

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
      el("strong", {}, T.week.weekTotal(kcal(check.week_kcal))),
      el("span", {}, T.week.ofTarget(kcal(check.week_kcal_target))),
    ),
    el("div", { class: "bar", "aria-hidden": "true" }, el("span", { style: `width: ${width}%` })),
    el("p", { class: "summary-protein" }, T.week.proteinTotal(grams(check.week_protein_g), check.week_protein_target_g && grams(check.week_protein_target_g))),
  );
}

async function renderWeek() {
  const plan = await api("/api/plan");
  if (!plan) return empty(T.week.emptyTitle, T.week.emptyHint);
  const start = parseDay(plan.week_start);
  const meals = MEALS.filter((meal) => plan.slots.some((slot) => slot.meal === meal));
  const totals = Object.fromEntries(plan.check.days.map((day) => [day.day, day]));
  const dayRow = (day, index) => {
    const total = totals[day];
    return el(
      "section",
      { class: "day" },
      el(
        "div",
        { class: "day-head" },
        el("h3", {}, T.weekdays[day]),
        el("span", { class: "day-date" }, shortDate(addDays(start, index))),
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
      { class: "view-head" },
      el("h2", {}, T.week.title(isoWeek(start))),
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

async function renderShopping() {
  const [list, state] = await Promise.all([api("/api/shopping"), api("/api/state")]);
  if (!list) return empty(T.shopping.emptyTitle, T.shopping.emptyHint);
  const confirmation = el("span", { class: "copied", role: "status" });
  const copy = async () => {
    try {
      await copyText(list.text);
      confirmation.textContent = T.shopping.copied;
    } catch {
      confirmation.textContent = T.shopping.copyFailed;
    }
  };
  const amount = (item) =>
    item.amount == null ? T.shopping.asNeeded : `${number.format(item.amount)} ${item.unit}`.trim();
  return [
    el(
      "header",
      { class: "view-head" },
      el("h2", {}, T.shopping.title(shortDate(parseDay(list.week_start)))),
      el("div", { class: "copy" }, el("button", { type: "button", class: "primary", onclick: copy }, T.shopping.copy), confirmation),
    ),
    state.plan?.status === "draft" && el("p", { class: "hint" }, T.shopping.draftHint),
    el(
      "div",
      { class: "departments" },
      Object.entries(list.departments).map(([department, items]) =>
        el(
          "section",
          { class: "department" },
          el("h3", {}, T.departments[department] ?? department),
          el("ul", {}, items.map((item) => el("li", {}, el("span", { class: "amount" }, amount(item)), el("span", {}, item.name)))),
        ),
      ),
    ),
    list.skipped.length > 0 && el("p", { class: "hint" }, T.shopping.skipped(list.skipped.join(", "))),
  ];
}

// Gerichte

function dishNode(dish) {
  const stats = dish.stats;
  const rating = stats?.ratings
    ? el("span", { class: "rating", "aria-label": T.dishes.ratingLabel(oneDecimal.format(stats.avg_score), stats.ratings) }, T.dishes.rating(oneDecimal.format(stats.avg_score)))
    : el("span", { class: "muted" }, T.dishes.unrated);
  const remove = () => {
    if (confirm(T.dishes.deleteConfirm(dish.name))) removeThen(`/api/dishes/${dish.id}`, "dishes");
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
  dishes: view("dishes", renderDishes),
  profile: view("profile", renderProfile),
};

// ---------- Setup ----------

function setupTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    }
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      const next = tabs[(index + step + tabs.length) % tabs.length];
      select(next);
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
    button.addEventListener("click", () => send(starter.message));
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
  Object.values(views).forEach((load) => load());
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
