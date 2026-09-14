// Chorgi dashboard — vanilla JS, polls /api/state every 4s.

const POLL_MS = 4000;
let state = { tasks: [], bookmarks: [], watchlist: [], shopping: [], social_posts: [], people: [], inbox: [] };
let lastSnapshot = "";
let watchFilter = "";
let watchShowWatched = false;
let shoppingFilter = "";
let shoppingShowBought = false;
let shoppingMinPrice = null;
let shoppingMaxPrice = null;
let shoppingSort = "newest";
let contactsFilter = "";
let contactsSort = "name";
let currentPerson = null;
let weekOffset = 0;         // 0 = current week; ◀/▶ shift by one week
let draggingTaskId = null;  // set while a task card is mid-drag — suppresses re-render
let socialWeekOffset = 0;     // Social board's week, independent of the task board's
let draggingSocialId = null;  // set while a social card is mid-drag
let socialImage = null;       // image in the open post editor: { url, name } or null
let socialImageDirty = false; // the editor's image was added, replaced or removed

// ---------------- Fetch helpers ----------------

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    const err = new Error(`${method} ${path} → ${r.status}: ${txt}`);
    err.status = r.status;
    try { err.payload = JSON.parse(txt); } catch {}
    throw err;
  }
  return r.json();
}

// The server's own error message when there is one, else the raw failure.
const errText = (e) => e.payload?.error || e.message;

// ---------------- Poll loop ----------------

let pollCount = 0;
async function poll() {
  try {
    const data = await api("GET", "/api/state");
    const snap = JSON.stringify([data.tasks.length, data.bookmarks.length, (data.social_posts || []).length]);
    state = data;
    // Always re-render on first load; otherwise only on shape changes
    // (full diff would be nicer, but cheap re-render is fine at this scale)
    render();
    const sync = document.getElementById("sync-status");
    sync.classList.remove("error");
    sync.title = "";
    document.getElementById("last-sync").textContent =
      "Synced " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (e) {
    const sync = document.getElementById("sync-status");
    sync.classList.add("error");
    sync.title = e.message;
    document.getElementById("last-sync").textContent = "Sync failed";
  }
  pollCount += 1;
  if (pollCount % 5 === 0) loadWikiTopics(false);  // ~every 20s
  setTimeout(poll, POLL_MS);
}

// ---------------- Render ----------------

function render() {
  renderTasks();
  renderWatch();
  renderShopping();
  renderSocial();
  renderInbox();
  renderContacts();
  renderTabBadges();
}

// ---- Tabs / pages ----

let activePage = (location.hash || "").slice(1)
  || localStorage.getItem("chorgi.activePage") || "tasks";

function setActivePage(name) {
  // A remembered or linked page may no longer exist (e.g. the removed Bookmarks tab).
  if (!document.querySelector(`.page[data-page="${name}"]`)) name = "tasks";
  activePage = name;
  localStorage.setItem("chorgi.activePage", name);
  if (("#" + name) !== location.hash) history.replaceState(null, "", "#" + name);
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.page === name);
  });
  document.querySelectorAll(".page").forEach(p => {
    p.classList.toggle("active", p.dataset.page === name);
  });
  // Contextual primary action in the top bar
  document.getElementById("add-task-btn").classList.toggle("hidden", name !== "tasks");
  document.getElementById("add-watch-btn").classList.toggle("hidden", name !== "watch");
  document.getElementById("add-shopping-btn").classList.toggle("hidden", name !== "shopping");
  document.getElementById("add-contact-btn").classList.toggle("hidden", name !== "contacts");
  document.getElementById("add-social-btn").classList.toggle("hidden", name !== "social");
  document.getElementById("import-social-btn").classList.toggle("hidden", name !== "social");
  if (name === "wiki" && !wikiLoaded) loadWikiTopics();
  if (name === "chat" && !chatLoaded) initChat();
}

function renderTabBadges() {
  // Open work needing attention: undated, or dated today or earlier.
  // Deliberately independent of weekOffset so browsing weeks doesn't churn it.
  const todayKey = localDateKey(new Date());
  const pending = (state.tasks || []).filter(t => {
    if (t.status === "done") return false;
    const k = taskDateKey(t);
    return k === null || k <= todayKey;
  }).length;
  const watch = (state.watchlist || []).filter(w => !w.watched).length;
  const shopping = (state.shopping || []).filter(s => !s.bought).length;
  const inbox = (state.inbox || []).length;
  const contacts = (state.people || []).length;
  const wiki = wikiTopics.length;
  // Social: posts from this Monday through today that still aren't posted.
  const mondayKey = localDateKey(startOfWeek(0));
  const social = (state.social_posts || []).filter((p) => {
    const k = p.scheduled_at.slice(0, 10);
    return p.status !== "posted" && k >= mondayKey && k <= todayKey;
  }).length;
  const set = (id, n) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = n;
    el.classList.toggle("zero", !n);
  };
  set("tab-badge-tasks", pending);
  set("tab-badge-social", social);
  set("tab-badge-watch", watch);
  set("tab-badge-shopping", shopping);
  set("tab-badge-inbox", inbox);
  set("tab-badge-contacts", contacts);
  set("tab-badge-wiki", wiki);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function emptyState(title, hint) {
  return el("div", { class: "empty-state" },
    el("div", { class: "empty-title" }, title),
    hint ? el("div", { class: "empty-hint" }, hint) : null,
  );
}

// ---- Tasks (week calendar) ----
//
// Mon–Sun columns for one week. There is no Pending column: every open task sits
// on a day. The heartbeat (task_cli.roll_over_tasks) carries unfinished work from
// past weeks onto the current Monday and gives undated tasks today's date.
//
// Week/day math runs in the
// browser's local timezone, assumed to match the bot's LOCAL_TZ (Europe/Berlin,
// skills/_shared.py) — the same assumption the task modal's datetime-local
// round-trip already makes.

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// YYYY-MM-DD from a Date's *local* parts. Never toISOString() — that's UTC.
function localDateKey(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function addDays(d, n) {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

// Monday 00:00 local, `offset` weeks from the current week.
function startOfWeek(offset) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7;  // Mon=0 … Sun=6
  return addDays(d, -dow + offset * 7);
}

// Which column a task belongs in, as a YYYY-MM-DD key (null → undated).
function taskDateKey(t) {
  // scheduled_at is stored Berlin-local *with* offset ("2026-07-26T11:00:00+02:00"),
  // so the date is literal — parsing it through Date would reintroduce tz drift.
  if (t.scheduled_at) return t.scheduled_at.slice(0, 10);
  if (t.deadline) return t.deadline;
  // completed_at is UTC, so this one genuinely needs converting to a local date.
  // Keeps a task finished today visible even if it never had a date.
  if (t.status === "done" && t.completed_at) {
    const d = new Date(t.completed_at);
    if (!isNaN(d)) return localDateKey(d);
  }
  return null;
}

function fmtTimeOfDay(t) {
  return t.scheduled_at ? t.scheduled_at.slice(11, 16) : "";
}

// Format a YYYY-MM-DD key without letting Date parse it as UTC midnight.
function fmtDateKeyShort(key) {
  const [y, m, d] = (key || "").split("-").map(Number);
  if (!y || !m || !d) return key || "";
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function fmtWeekRange(a, b) {
  const opts = { month: "short", day: "numeric" };
  const left = a.toLocaleDateString(undefined, opts);
  const right = b.toLocaleDateString(undefined, opts);
  if (a.getFullYear() !== b.getFullYear()) {
    return `${left}, ${a.getFullYear()} – ${right}, ${b.getFullYear()}`;
  }
  return `${left} – ${right}, ${b.getFullYear()}`;
}

const prioRank = (t) => ({ high: 0, medium: 1, low: 2 }[t.priority] ?? 1);

// Manual position within a column, set by dragging. A card that has never been
// dragged has no sort_order and ranks last, so new work lands at the bottom of a
// hand-arranged column instead of shoving itself into the middle of it.
const orderRank = (t) => (typeof t.sort_order === "number" ? t.sort_order : Infinity);

// Every column sorts the same way:
//   1. done last — completed work is always pinned to the bottom
//   2. your manual arrangement
//   3. the automatic fallback, for cards you've never dragged
// Steps 1 and 2 are shared; each column supplies its own step 3.
function byPinnedThenManual(fallback) {
  return (a, b) => {
    const ad = a.t.status === "done" ? 1 : 0, bd = b.t.status === "done" ? 1 : 0;
    if (ad !== bd) return ad - bd;
    const ao = orderRank(a.t), bo = orderRank(b.t);
    if (ao !== bo) return ao - bo;   // both Infinity (never dragged) → fall through
    return fallback(a, b);
  };
}

// Day fallback: priority band, then time of day inside it (an untimed card —
// deadline- or completion-placed — sorts after timed ones).
const sortDay = (entries) => entries.sort(byPinnedThenManual((a, b) => {
  const p = prioRank(a.t) - prioRank(b.t);
  if (p) return p;
  const at = fmtTimeOfDay(a.t), bt = fmtTimeOfDay(b.t);
  if (!!at !== !!bt) return at ? -1 : 1;
  return at.localeCompare(bt);
}));

function renderTasks() {
  const grid = document.getElementById("week-grid");
  if (!grid) return;
  // Don't yank a card out from under an in-flight drag on the 4s poll tick.
  if (draggingTaskId) return;

  const monday = startOfWeek(weekOffset);
  const days = Array.from({ length: 7 }, (_, i) => addDays(monday, i));
  const dayKeys = days.map(localDateKey);
  const todayKey = localDateKey(new Date());
  const isCurrentWeek = weekOffset === 0;

  document.getElementById("week-label").textContent = fmtWeekRange(monday, days[6]);
  document.getElementById("week-today").classList.toggle("hidden", isCurrentWeek);

  const byDay = {};
  for (const k of dayKeys) byDay[k] = [];

  for (const t of (state.tasks || [])) {
    // An open undated task (just created, heartbeat hasn't dated it yet) shows on today.
    const key = taskDateKey(t) ?? (t.status !== "done" ? todayKey : null);
    if (key && byDay[key]) byDay[key].push({ t });
    // Anything else belongs to another week.
  }

  grid.innerHTML = "";
  for (let i = 0; i < 7; i++) {
    const key = dayKeys[i];
    grid.appendChild(weekCol({
      key,
      title: WEEKDAYS[i],
      dayNum: days[i].getDate(),
      entries: sortDay(byDay[key]),
      isToday: key === todayKey,
      isWeekend: i >= 5,
    }));
  }
}

function weekCol(o) {
  const cls = ["week-col"];
  if (o.isToday) cls.push("is-today");
  if (o.isWeekend) cls.push("is-weekend");
  if (!o.entries.length) cls.push("is-empty");  // hidden on narrow screens
  const col = el("div", { class: cls.join(" "), dataset: { date: o.key } });

  const title = el("span", { class: "col-title" }, o.title);
  if (o.dayNum != null) title.appendChild(el("span", { class: "week-day-num" }, String(o.dayNum)));
  const count = el("span", { class: "count" + (o.entries.length ? "" : " zero") },
    String(o.entries.length));
  col.appendChild(el("h3", {}, title, count));

  const cards = el("div", { class: "cards" });
  for (const e of o.entries) cards.appendChild(taskCard(e.t));
  col.appendChild(cards);
  wireDayDrop(cards, o.key);
  return col;
}

function taskCard(t) {
  const card = el("div", {
    class: `card priority-${t.priority || "medium"} status-${t.status}`,
    draggable: "true",
    dataset: { taskId: t.id },
    ondragstart: (e) => {
      e.dataTransfer.setData("text/plain", t.id);
      e.dataTransfer.effectAllowed = "move";
      draggingTaskId = t.id;
      card.classList.add("dragging");
    },
    ondragend: () => {
      // Also runs when a drag is abandoned outside any column, so this is where
      // the drag chrome gets cleaned up rather than in the drop handler alone.
      draggingTaskId = null;
      card.classList.remove("dragging");
      endDragChrome();
    },
    onclick: (e) => {
      // Don't open modal if clicking an action button
      if (e.target.closest("button")) return;
      openTaskModal(t);
    },
  });
  card.appendChild(el("div", { class: "title" }, t.title));
  const meta = el("div", { class: "meta" });
  const time = fmtTimeOfDay(t);
  if (time) meta.appendChild(el("span", { class: "card-time" }, time));
  if (t.deadline) {
    const overdue = t.status !== "done" && t.deadline < localDateKey(new Date());
    meta.appendChild(el("span", { class: overdue ? "overdue" : "" },
      "⚑ " + fmtDateKeyShort(t.deadline)));
  }
  if (t.estimated_minutes) meta.appendChild(el("span", {}, t.estimated_minutes + "m"));
  if (t.carry_count > 0) meta.appendChild(el("span", {}, "↩ " + t.carry_count));
  for (const tag of (t.tags || [])) {
    meta.appendChild(el("span", { class: "tag" }, tag));
  }
  card.appendChild(meta);

  // Actions (visible on hover)
  const actions = el("div", { class: "actions" });
  if (t.status !== "done") {
    actions.appendChild(el("button", {
      onclick: async () => {
        await api("PATCH", "/api/tasks/" + t.id, { status: "done" });
        poll();
      }
    }, "✓ Done"));
  }
  card.appendChild(actions);
  return card;
}

// ---- Dragging: place a card on a day, and position it within that day ----
//
// A drop does two things at once — which column the card lands in, and where in
// that column it sits. Both go in one PATCH so a move can't half-apply.
//
// Every drop sends the destination column's full top-to-bottom id list; the
// server hands out fresh sort_order indexes from it, so a column's ordering is
// always rewritten whole and can't drift.
//
// Done cards are pinned to the bottom of every column, so the insertion point is
// clamped: an open card can't be dropped below them, nor a done card above them.

// The insertion line. Absolutely positioned, so hovering never reflows the cards
// underneath it — a flex-child indicator makes the midpoints it's measured
// against jump, and the drop target oscillates.
let dropIndicator = null;

function endDragChrome() {
  if (dropIndicator) dropIndicator.remove();
  document.querySelectorAll(".cards.drag-over")
    .forEach((c) => c.classList.remove("drag-over"));
}

// Cards already in this column, top to bottom. The card being dragged is excluded
// — it's what we're inserting, not something to insert against.
function columnCards(container) {
  return Array.from(container.querySelectorAll(".card:not(.dragging)"));
}

// Index the card would be inserted at, from the cursor's position.
function dropIndexAt(container, clientY, isDone) {
  const cards = columnCards(container);
  let index = cards.length;
  for (let i = 0; i < cards.length; i++) {
    const r = cards[i].getBoundingClientRect();
    if (clientY < r.top + r.height / 2) { index = i; break; }
  }
  // Keep the done block at the bottom intact, so the indicator never promises a
  // slot the re-render would immediately take back.
  const firstDone = cards.findIndex((c) => c.classList.contains("status-done"));
  if (firstDone < 0) return index;
  return isDone ? Math.max(index, firstDone) : Math.min(index, firstDone);
}

function showDropIndicator(container, index) {
  if (!dropIndicator) dropIndicator = el("div", { class: "drop-indicator" });
  const cards = columnCards(container);
  const box = container.getBoundingClientRect();
  let top = 0;
  if (cards.length) {
    const edge = index >= cards.length
      ? cards[cards.length - 1].getBoundingClientRect().bottom + 3
      : cards[index].getBoundingClientRect().top - 5;
    top = edge - box.top;
  }
  dropIndicator.style.top = `${top}px`;
  if (dropIndicator.parentElement !== container) container.appendChild(dropIndicator);
}

// Dropping on a day plans it for that day. The PATCH sends sync_calendar:false —
// dragging never writes to Google Calendar. Any linked event stays put; the task
// editor's "Add to calendar" toggle is the only calendar control.
// Wired per column as the grid is rebuilt.
function wireDayDrop(target, dayKey) {
  const draggedTask = () => (state.tasks || []).find((t) => t.id === draggingTaskId);

  target.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    target.classList.add("drag-over");
    showDropIndicator(target, dropIndexAt(target, e.clientY, draggedTask()?.status === "done"));
  });
  target.addEventListener("dragleave", (e) => {
    // Crossing onto a card inside this column still fires dragleave on the column.
    if (e.relatedTarget && target.contains(e.relatedTarget)) return;
    target.classList.remove("drag-over");
    if (dropIndicator) dropIndicator.remove();
  });
  target.addEventListener("drop", async (e) => {
    e.preventDefault();
    const taskId = e.dataTransfer.getData("text/plain");
    const task = (state.tasks || []).find((t) => t.id === taskId);

    // Read the drop position before tearing down the drag: both the index and the
    // id list depend on .dragging still marking the card being moved.
    const index = task ? dropIndexAt(target, e.clientY, task.status === "done") : 0;
    const ids = columnCards(target).map((c) => c.dataset.taskId);
    endDragChrome();
    draggingTaskId = null;  // dragend hasn't fired yet; unblock the re-render below
    if (!task) return;
    ids.splice(index, 0, taskId);

    const cur = taskDateKey(task);
    const body = { sync_calendar: false, order: ids };
    if (cur !== dayKey) {
      body.scheduled_at = `${dayKey} ${fmtTimeOfDay(task) || "09:00"}`;
    }
    const staleEvent = (task.calendar_event_id && task.scheduled_at)
      ? fmtDateKeyShort(task.scheduled_at.slice(0, 10)) : null;

    // Show the new arrangement now instead of waiting out a poll cycle. The
    // server's version replaces this wholesale on the next tick.
    ids.forEach((id, i) => {
      const t = (state.tasks || []).find((x) => x.id === id);
      if (t) t.sort_order = i;
    });
    if (body.scheduled_at) {
      // Offsetless, but nothing reads it as an instant — taskDateKey() and
      // fmtTimeOfDay() slice the date and time straight out of the string.
      task.scheduled_at = body.scheduled_at.replace(" ", "T") + ":00";
    }
    renderTasks();

    try {
      await api("PATCH", "/api/tasks/" + taskId, body);
      poll();
      // Be explicit rather than let the calendar silently disagree with the board.
      if (staleEvent) {
        toast(`Moved on the board — calendar event still ${staleEvent} (save it in the editor to move the event)`);
      }
    } catch (err) {
      toast("Move failed: " + err.message, "error");
      poll();  // the optimistic move didn't stick — resync to what's stored
    }
  });
}

// ---- Watch list ----

function renderWatch() {
  const container = document.getElementById("watch-grid");
  if (!container) return;
  container.innerHTML = "";
  const q = watchFilter.toLowerCase();
  let items = state.watchlist || [];
  if (!watchShowWatched) items = items.filter((w) => !w.watched);
  if (q) {
    items = items.filter((w) => {
      const hay = [w.url, w.title, w.notes, w.summary, w.source, w.where_source, ...(w.tags || [])].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }
  // Unwatched first, then watched (when shown), each newest-first as stored.
  items = [...items].sort((a, b) => (a.watched === b.watched) ? 0 : (a.watched ? 1 : -1));

  if (!items.length) {
    container.appendChild(emptyState("Nothing to watch yet",
      "Send the bot a YouTube, IMDB, or Vimeo link — or say “watch later” with any link."));
    return;
  }
  for (const w of items) container.appendChild(watchCard(w));
}

function watchCard(w) {
  const card = el("div", { class: "watch-card" + (w.watched ? " watched" : "") });

  const thumb = el("a", { class: "watch-thumb", href: w.url, target: "_blank", rel: "noopener" });
  if (w.image) thumb.appendChild(el("img", { src: w.image, alt: "", loading: "lazy" }));
  else thumb.appendChild(el("div", { class: "watch-thumb-fallback" }, (w.source || "▶").slice(0, 12)));
  if (w.duration) thumb.appendChild(el("span", { class: "watch-duration" }, w.duration));
  card.appendChild(thumb);

  const body = el("div", { class: "watch-body" });
  body.appendChild(el("a", { class: "watch-title", href: w.url, target: "_blank", rel: "noopener" }, w.title || w.url));

  const meta = el("div", { class: "watch-meta" });
  if (w.source) meta.appendChild(el("span", { class: "watch-chip" }, w.source));
  if (w.rating) meta.appendChild(el("span", { class: "watch-chip" }, "★ " + w.rating));
  for (const tag of (w.tags || [])) meta.appendChild(el("span", { class: "watch-chip tag" }, tag));
  if (meta.children.length) body.appendChild(meta);

  if (w.summary) body.appendChild(el("div", { class: "watch-summary" }, w.summary));
  else if (w.notes) body.appendChild(el("div", { class: "watch-summary" }, w.notes));

  if (w.where_url) {
    body.appendChild(el("a", {
      class: "watch-where", href: w.where_url, target: "_blank", rel: "noopener",
    }, "▶ Watch on " + (w.where_source || "link")));
  }

  const actions = el("div", { class: "actions" });
  actions.appendChild(el("button", {
    onclick: async () => {
      await api("PATCH", "/api/watchlist", { url: w.url, watched: !w.watched });
      poll();
    }
  }, w.watched ? "↺ Unwatch" : "✓ Watched"));
  actions.appendChild(el("button", {
    onclick: async () => {
      const cur = w.where_url || "";
      const next = prompt("Where to watch link (e.g. Netflix URL). Leave blank to clear:", cur);
      if (next === null) return;  // cancelled
      await api("PATCH", "/api/watchlist", { url: w.url, where_url: next.trim() });
      poll();
    }
  }, w.where_url ? "Edit link" : "+ Where"));
  actions.appendChild(el("button", {
    class: "danger",
    onclick: async () => {
      if (!confirm("Remove from watch list?")) return;
      await api("DELETE", "/api/watchlist", { url: w.url });
      poll();
    }
  }, "Delete"));
  body.appendChild(actions);

  card.appendChild(body);
  return card;
}

// ---- Shopping list ----

const CURRENCY_SYMBOLS = { USD: "$", EUR: "€", GBP: "£", JPY: "¥" };

function formatPrice(amount, currency) {
  if (amount === null || amount === undefined || amount === "") return "—";
  const sym = CURRENCY_SYMBOLS[currency];
  const n = Number(amount);
  const num = Number.isFinite(n) ? n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 }) : amount;
  return sym ? sym + num : num + " " + (currency || "");
}

function renderShopping() {
  const tbody = document.getElementById("shopping-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  const q = shoppingFilter.toLowerCase();
  let items = state.shopping || [];
  if (!shoppingShowBought) items = items.filter((s) => !s.bought);
  if (q) {
    items = items.filter((s) => {
      const hay = [s.url, s.title, s.notes, s.source, s.currency, s.category, ...(s.tags || [])].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }
  // Price range — items without a price are hidden while either bound is active.
  if (shoppingMinPrice !== null) items = items.filter((s) => typeof s.amount === "number" && s.amount >= shoppingMinPrice);
  if (shoppingMaxPrice !== null) items = items.filter((s) => typeof s.amount === "number" && s.amount <= shoppingMaxPrice);
  // Un-bought items always group above bought ones; the chosen sort orders
  // within each group. "newest" keeps stored order (newest-first) via stable sort.
  const amt = (s, fallback) => (typeof s.amount === "number" ? s.amount : fallback);
  const within = {
    newest: () => 0,
    "price-asc": (a, b) => amt(a, Infinity) - amt(b, Infinity),   // no price → last
    "price-desc": (a, b) => amt(b, -Infinity) - amt(a, -Infinity),  // no price → last
    name: (a, b) => (a.title || a.url).localeCompare(b.title || b.url),
  }[shoppingSort] || (() => 0);
  items = [...items].sort((a, b) =>
    (a.bought === b.bought) ? within(a, b) : (a.bought ? 1 : -1));

  if (!items.length) {
    const filtered = q || shoppingMinPrice !== null || shoppingMaxPrice !== null;
    const cell = el("td", { colspan: "5" }, filtered
      ? emptyState("No matches", "No items match the current search or price filter.")
      : emptyState("Nothing on the list yet", "Click “Add item”, paste a product link, and set a price."));
    tbody.appendChild(el("tr", {}, cell));
    return;
  }
  for (const s of items) tbody.appendChild(shoppingRow(s));
}

function shoppingRow(s) {
  const row = el("tr", { class: "shopping-row" + (s.bought ? " bought" : "") });

  const thumb = el("a", { class: "shopping-thumb", href: s.url, target: "_blank", rel: "noopener" });
  if (s.image) thumb.appendChild(el("img", { src: s.image, alt: "", loading: "lazy" }));
  else thumb.appendChild(el("div", { class: "shopping-thumb-fallback" }, "🛒"));
  const text = el("div", { class: "shopping-item-text" },
    el("a", { class: "shopping-title", href: s.url, target: "_blank", rel: "noopener" }, s.title || s.url));
  if (s.notes) text.appendChild(el("div", { class: "shopping-summary" }, s.notes));
  row.appendChild(el("td", {}, el("div", { class: "shopping-item-cell" }, thumb, text)));

  row.appendChild(el("td", { class: "num" }, el("span", { class: "shopping-price" }, formatPrice(s.amount, s.currency))));

  const meta = el("div", { class: "shopping-meta" });
  if (s.category) meta.appendChild(el("span", { class: "shopping-chip cat" }, s.category));
  for (const tag of (s.tags || [])) meta.appendChild(el("span", { class: "shopping-chip tag" }, tag));
  row.appendChild(el("td", {}, meta.children.length ? meta : "—"));

  row.appendChild(el("td", { class: "muted small" }, s.source || "—"));

  const actions = el("div", { class: "actions" });
  actions.appendChild(el("button", {
    onclick: async () => {
      await api("PATCH", "/api/shopping", { url: s.url, bought: !s.bought });
      poll();
    }
  }, s.bought ? "↺ Unbuy" : "✓ Bought"));
  actions.appendChild(el("button", {
    onclick: async () => {
      const cur = (s.amount === null || s.amount === undefined) ? "" : String(s.amount);
      const next = prompt("Price amount (number). Leave blank to clear:", cur);
      if (next === null) return;  // cancelled
      await api("PATCH", "/api/shopping", { url: s.url, amount: next.trim(), currency: s.currency || "EUR" });
      poll();
    }
  }, "Edit price"));
  actions.appendChild(el("button", {
    class: "danger",
    onclick: async () => {
      if (!confirm("Remove from shopping list?")) return;
      await api("DELETE", "/api/shopping", { url: s.url });
      poll();
    }
  }, "Delete"));
  row.appendChild(el("td", {}, actions));

  return row;
}

// ---- Social (LinkedIn posts + Substack Notes) ----
//
// Mon–Sun columns × one row per platform; a cell holds at most one post (the
// server enforces it). Dragging a card to another cell moves it there and keeps
// its time of day; dropping onto an occupied cell swaps the two posts. Post text
// is shown, sent and copied exactly as stored — never trimmed or reformatted.

const SOCIAL_PLATFORMS = [
  { key: "linkedin", label: "LinkedIn", single: "LinkedIn post", short: "in", limit: 3000 },
  { key: "substack_note", label: "Substack Notes", single: "Substack Note", short: "S", limit: 10000 },
];
const SOCIAL_STATUS_LABELS = { draft: "Draft", ready: "Ready", posted: "Posted" };
const SOCIAL_MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const SOCIAL_IMAGE_EXT = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif" };

const socialPlatform = (key) => SOCIAL_PLATFORMS.find((p) => p.key === key)
  || { key, label: key, single: key, short: "?", limit: 10000 };
const socialImageUrl = (file) => "/social-images/" + encodeURIComponent(file);
// Code points, not UTF-16 units: matches the server's len() and the schema's
// maxLength, so 𝗯𝗼𝗹𝗱 letters and emoji don't count double.
const charCount = (s) => [...s].length;

function platformIcon(key) {
  return el("span", { class: `platform-icon platform-${key}`, "aria-hidden": "true" }, socialPlatform(key).short);
}

function renderSocial() {
  const grid = document.getElementById("social-grid");
  if (!grid) return;
  if (draggingSocialId) return;  // don't rebuild under an in-flight drag

  const monday = startOfWeek(socialWeekOffset);
  const days = Array.from({ length: 7 }, (_, i) => addDays(monday, i));
  const dayKeys = days.map(localDateKey);
  const todayKey = localDateKey(new Date());
  document.getElementById("social-week-label").textContent = fmtWeekRange(monday, days[6]);
  document.getElementById("social-week-today").classList.toggle("hidden", socialWeekOffset === 0);

  const bySlot = new Map();
  for (const p of (state.social_posts || [])) {
    bySlot.set(`${p.platform}|${p.scheduled_at.slice(0, 10)}`, p);
  }

  grid.innerHTML = "";
  grid.appendChild(el("div", { class: "social-corner" }));
  // Phones collapse the grid to one column; --m-order then regroups it as
  // day header → one cell per platform, day by day.
  const stride = SOCIAL_PLATFORMS.length + 1;
  days.forEach((d, i) => {
    grid.appendChild(el("div", {
      class: "social-day-head" + (dayKeys[i] === todayKey ? " is-today" : ""),
      style: `--m-order: ${i * stride}`,
    },
      el("span", { class: "col-title" }, WEEKDAYS[i]),
      el("span", { class: "week-day-num" }, String(d.getDate())),
    ));
  });
  SOCIAL_PLATFORMS.forEach((plat, row) => {
    grid.appendChild(el("div", { class: "social-row-head" }, platformIcon(plat.key), el("span", {}, plat.label)));
    dayKeys.forEach((key, i) => {
      const cls = ["social-cell"];
      if (key === todayKey) cls.push("is-today");
      if (i >= 5) cls.push("is-weekend");
      const cell = el("div", {
        class: cls.join(" "),
        style: `--m-order: ${i * stride + row + 1}`,
        dataset: { platform: plat.key, date: key, platformLabel: plat.label },
      });
      const post = bySlot.get(`${plat.key}|${key}`);
      const dayName = `${WEEKDAYS[i]} ${days[i].getDate()}`;
      cell.appendChild(post ? socialCard(post) : el("button", {
        class: "social-add",
        title: `New ${plat.single} for ${dayName}`,
        "aria-label": `New ${plat.single} for ${dayName}`,
        onclick: () => openSocialModal(null, { platform: plat.key, date: key }),
      }, "+"));
      wireSocialDrop(cell);
      grid.appendChild(cell);
    });
  });
}

function socialCard(p) {
  const card = el("div", {
    class: `card social-card status-${p.status}`,
    draggable: "true",
    dataset: { postId: p.id },
    ondragstart: (e) => {
      e.dataTransfer.setData("text/plain", p.id);
      e.dataTransfer.effectAllowed = "move";
      draggingSocialId = p.id;
      card.classList.add("dragging");
    },
    ondragend: () => {
      draggingSocialId = null;
      card.classList.remove("dragging");
      document.querySelectorAll(".social-cell.drag-over").forEach((c) => c.classList.remove("drag-over"));
    },
    onclick: (e) => {
      if (e.target.closest("button")) return;
      openSocialModal(p);
    },
  });
  card.appendChild(el("div", { class: "meta" },
    el("span", { class: "card-time" }, fmtTimeOfDay(p)),
    el("span", { class: `social-status status-${p.status}` }, SOCIAL_STATUS_LABELS[p.status] || p.status),
  ));
  if (p.image) {
    card.appendChild(el("img", {
      class: "social-thumb", src: socialImageUrl(p.image.file), alt: p.image.alt || "",
      loading: "lazy", draggable: "false",
    }));
  }
  card.appendChild(el("div", { class: "social-snippet" }, p.text));
  const actions = el("div", { class: "actions" });
  actions.appendChild(el("button", {
    onclick: async () => {
      const ok = await copyToClipboard(p.text);
      if (ok) toast(`Copied the ${socialPlatform(p.platform).single} text`);
      else toast("Copy failed. Open the post and copy from the editor", "error");
    },
  }, "Copy text"));
  card.appendChild(actions);
  return card;
}

// Dropping on a cell moves the post there (same time of day). An occupied cell
// swaps: the post already there goes to where the dragged one came from.
function wireSocialDrop(cell) {
  cell.addEventListener("dragover", (e) => {
    if (!draggingSocialId) return;  // only social cards, not task cards or files
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    cell.classList.add("drag-over");
  });
  cell.addEventListener("dragleave", (e) => {
    if (e.relatedTarget && cell.contains(e.relatedTarget)) return;
    cell.classList.remove("drag-over");
  });
  cell.addEventListener("drop", async (e) => {
    if (!draggingSocialId) return;
    e.preventDefault();
    const id = draggingSocialId;
    draggingSocialId = null;  // dragend hasn't fired yet; unblock the re-render below
    cell.classList.remove("drag-over");

    const posts = state.social_posts || [];
    const post = posts.find((p) => p.id === id);
    if (!post) return;
    const { platform, date } = cell.dataset;
    const fromPlatform = post.platform;
    const fromDate = post.scheduled_at.slice(0, 10);
    if (platform === fromPlatform && date === fromDate) return;
    const other = posts.find((p) => p.id !== id && p.platform === platform
      && p.scheduled_at.slice(0, 10) === date);

    // Show the move now. Only the date part of scheduled_at is swapped in —
    // nothing reads it as an instant, and the server recomputes the offset.
    post.platform = platform;
    post.scheduled_at = date + post.scheduled_at.slice(10);
    if (other) {
      other.platform = fromPlatform;
      other.scheduled_at = fromDate + other.scheduled_at.slice(10);
    }
    renderSocial();

    try {
      await api("POST", "/api/social/move", { id, platform, date });
      if (other) toast("Swapped with the post that was already there");
    } catch (err) {
      toast("Move failed: " + errText(err), "error");
    }
    poll();  // resync to what's stored either way
  });
}

async function copyToClipboard(text) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through to the legacy path */ }
  }
  // Fallback (e.g. plain-HTTP LAN access): a hidden textarea keeps newlines and
  // Unicode exactly as-is.
  const ta = el("textarea", { readonly: "", "aria-hidden": "true", style: "position:fixed;top:0;left:-9999px;opacity:0" });
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

async function copyImageToClipboard(src) {
  if (!window.ClipboardItem || !navigator.clipboard?.write) {
    throw new Error("this browser can't copy images. Use Download instead");
  }
  // The item takes a promise, not a blob: Safari only allows a clipboard write
  // that starts synchronously inside the click, and fetching/converting is async.
  const png = (async () => {
    const blob = await (await fetch(src)).blob();
    if (blob.type === "image/png") return blob;
    // Clipboards only reliably accept PNG, so re-encode JPEG/WebP/GIF.
    const bitmap = await createImageBitmap(blob);
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext("2d").drawImage(bitmap, 0, 0);
    return new Promise((resolve, reject) => canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("image conversion failed"))), "image/png"));
  })();
  await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
}

function openSocialModal(p, preset) {
  const plat = socialPlatform(p?.platform || preset?.platform || "linkedin");
  const defaultDate = preset?.date
    || localDateKey(socialWeekOffset === 0 ? new Date() : startOfWeek(socialWeekOffset));
  document.getElementById("social-modal-title").textContent = p ? `Edit ${plat.single}` : "New post";
  document.getElementById("social-id").value = p?.id || "";
  document.getElementById("social-platform").value = plat.key;
  document.getElementById("social-scheduled-at").value = p ? p.scheduled_at.slice(0, 16) : `${defaultDate}T09:00`;
  document.getElementById("social-status").value = p?.status || "draft";
  document.getElementById("social-text").value = p?.text || "";
  document.getElementById("social-notes").value = p?.notes || "";
  document.getElementById("social-image-alt").value = p?.image?.alt || "";
  socialImage = p?.image ? { url: socialImageUrl(p.image.file), name: p.image.file } : null;
  socialImageDirty = false;
  renderSocialImage();
  updateSocialCount();
  document.getElementById("social-delete").classList.toggle("hidden", !p);
  document.getElementById("social-mark-posted").classList.toggle("hidden", !p || p.status === "posted");
  document.getElementById("social-modal").classList.remove("hidden");
  document.getElementById("social-text").focus();
}

function closeSocialModal() { document.getElementById("social-modal").classList.add("hidden"); }

function updateSocialCount() {
  const plat = socialPlatform(document.getElementById("social-platform").value);
  const n = charCount(document.getElementById("social-text").value);
  const out = document.getElementById("social-count");
  out.textContent = `${n.toLocaleString()} / ${plat.limit.toLocaleString()}`;
  out.classList.toggle("over", n > plat.limit);
}

function renderSocialImage() {
  const has = !!socialImage;
  const preview = document.getElementById("social-image-preview");
  preview.classList.toggle("hidden", !has);
  if (has) preview.src = socialImage.url;
  else preview.removeAttribute("src");
  document.getElementById("social-dropzone-empty").classList.toggle("hidden", has);
  document.getElementById("social-image-actions").classList.toggle("hidden", !has);
  const download = document.getElementById("social-download-image");
  if (has) {
    download.href = socialImage.url;
    download.setAttribute("download", socialImage.name);
  }
}

async function setSocialImageFromFile(file) {
  if (!file) return;
  const ext = SOCIAL_IMAGE_EXT[file.type];
  if (!ext) { toast("Images must be PNG, JPEG, WebP or GIF", "error"); return; }
  if (file.size > SOCIAL_MAX_IMAGE_BYTES) { toast("Images can be at most 8 MB", "error"); return; }
  try {
    const dataUri = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    socialImage = { url: dataUri, name: `post-image.${ext}` };
    socialImageDirty = true;
    renderSocialImage();
  } catch (e) {
    toast("Couldn't read the image: " + e.message, "error");
  }
}

async function saveSocialPost() {
  const idInput = document.getElementById("social-id");
  const when = document.getElementById("social-scheduled-at").value;
  const text = document.getElementById("social-text").value;
  if (!when) { toast("Pick a date and time", "error"); return; }
  if (!text.trim()) { toast("The post has no text", "error"); return; }
  const alt = document.getElementById("social-image-alt").value;
  const payload = {
    platform: document.getElementById("social-platform").value,
    scheduled_at: when.replace("T", " "),
    status: document.getElementById("social-status").value,
    text,  // exactly as typed, never trimmed
    notes: document.getElementById("social-notes").value,
  };
  try {
    let saved = idInput.value
      ? await api("PATCH", "/api/social/posts/" + idInput.value, { ...payload, image_alt: alt })
      : await api("POST", "/api/social/posts", payload);
    // Keep the id right away: if the image upload below fails, saving again
    // updates this post instead of hitting its own slot as a conflict.
    idInput.value = saved.id;
    if (socialImageDirty) {
      saved = socialImage
        ? await api("POST", `/api/social/posts/${saved.id}/image`, { data_uri: socialImage.url, alt })
        : await api("DELETE", `/api/social/posts/${saved.id}/image`);
      socialImageDirty = false;
    }
    closeSocialModal();
  } catch (e) {
    toast("Save failed: " + errText(e), "error");
  }
  poll();
}

// ---- Social import ----

let socialImportFileText = null;  // a chosen file's contents, kept out of the textarea (images make it huge)
let socialImportDoc = null;       // the document the current preview was built from
let socialImportTimer = null;
let socialImportSeq = 0;          // drops preview responses that a newer edit superseded
let socialSchemaText = null;

function openSocialImportModal() {
  socialImportFileText = null;
  socialImportDoc = null;
  document.getElementById("social-import-json").value = "";
  document.getElementById("social-import-filename").textContent = "";
  document.getElementById("social-import-overwrite").checked = false;
  document.getElementById("social-import-preview").innerHTML = "";
  document.getElementById("social-import-apply").disabled = true;
  document.getElementById("social-import-modal").classList.remove("hidden");
  // Fetched ahead of time so "Copy schema" can write to the clipboard straight
  // from the click; some browsers refuse clipboard writes after an await.
  if (!socialSchemaText) {
    api("GET", "/api/social/schema")
      .then((s) => { socialSchemaText = JSON.stringify(s, null, 2); })
      .catch(() => {});
  }
}

function closeSocialImportModal() { document.getElementById("social-import-modal").classList.add("hidden"); }

async function previewSocialImport() {
  const out = document.getElementById("social-import-preview");
  const apply = document.getElementById("social-import-apply");
  const raw = (socialImportFileText ?? document.getElementById("social-import-json").value).trim();
  const seq = ++socialImportSeq;
  apply.disabled = true;
  socialImportDoc = null;
  if (!raw) { out.innerHTML = ""; return; }
  let doc;
  try {
    doc = JSON.parse(raw);
  } catch (e) {
    out.replaceChildren(el("div", { class: "social-import-problem" }, "Not valid JSON: " + e.message));
    return;
  }
  const overwrite = document.getElementById("social-import-overwrite").checked;
  try {
    const res = await api("POST", "/api/social/import", { mode: "preview", doc, overwrite });
    if (seq !== socialImportSeq) return;
    renderSocialImportPreview(res, overwrite);
    socialImportDoc = doc;
    apply.disabled = !res.ok;
  } catch (e) {
    if (seq !== socialImportSeq) return;
    out.replaceChildren(el("div", { class: "social-import-problem" }, "Preview failed: " + errText(e)));
  }
}

function weekdayOf(dateKey) {
  const [y, m, d] = dateKey.split("-").map(Number);
  return WEEKDAYS[(new Date(y, m - 1, d).getDay() + 6) % 7];
}

// How many weeks from the current one to the week containing dateKey.
function weekOffsetFor(dateKey) {
  const [y, m, d] = dateKey.split("-").map(Number);
  const day = new Date(y, m - 1, d);
  const monday = addDays(day, -((day.getDay() + 6) % 7));
  return Math.round((monday - startOfWeek(0)) / (7 * 24 * 3600 * 1000));  // round: DST weeks
}

function renderSocialImportPreview(res, overwrite) {
  const c = res.counts;
  const parts = [];
  if (c.new) parts.push(`${c.new} new`);
  if (c.replace) parts.push(`${c.replace} replacing existing`);
  if (c.conflict) parts.push(`${c.conflict} conflicting`);
  if (c.error) parts.push(`${c.error} with errors`);
  const nodes = [el("div", { class: "social-import-summary" + (res.ok ? " ok" : "") },
    (res.ok ? "✓ Ready to import: " : "") + (parts.join(" · ") || "no posts"))];
  if (c.conflict && !overwrite) {
    nodes.push(el("div", { class: "modal-hint" },
      "Some of these days already have a post on that platform. Tick “Overwrite existing posts” to replace them."));
  }
  if (res.errors.length) {
    nodes.push(el("ul", { class: "social-import-errors" },
      ...res.errors.map((e) => el("li", {}, `${e.path}: ${e.message}`))));
  }
  if (res.rows.length) {
    const labels = { new: "New", replace: "Replace", conflict: "Conflict", error: "Error" };
    const tbody = el("tbody");
    for (const r of res.rows) {
      const postCell = el("td", {}, el("div", { class: "social-import-snippet" }, r.snippet || "—"));
      if (r.errors.length) {
        postCell.appendChild(el("ul", { class: "social-import-errors" }, ...r.errors.map((m) => el("li", {}, m))));
      }
      if (r.warnings.length) {
        postCell.appendChild(el("ul", { class: "social-import-warnings" }, ...r.warnings.map((m) => el("li", {}, m))));
      }
      tbody.appendChild(el("tr", {},
        el("td", { class: "nowrap" }, r.date ? `${weekdayOf(r.date)} ${fmtDateKeyShort(r.date)}` : (r.scheduled_at || "—")),
        el("td", { class: "nowrap" }, r.time || ""),
        el("td", { class: "nowrap" }, r.platform ? socialPlatform(r.platform).single : "—"),
        postCell,
        el("td", {}, el("span", { class: `import-badge import-${r.action}` }, labels[r.action])),
      ));
    }
    nodes.push(el("div", { class: "table-scroll" }, el("table", { class: "social-import-table" },
      el("thead", {}, el("tr", {},
        el("th", {}, "Day"), el("th", {}, "Time"), el("th", {}, "Platform"), el("th", {}, "Post"), el("th", {}, ""))),
      tbody)));
  }
  document.getElementById("social-import-preview").replaceChildren(...nodes);
}

async function applySocialImport() {
  if (!socialImportDoc) return;
  const apply = document.getElementById("social-import-apply");
  apply.disabled = true;
  try {
    const res = await api("POST", "/api/social/import", {
      mode: "apply",
      doc: socialImportDoc,
      overwrite: document.getElementById("social-import-overwrite").checked,
    });
    closeSocialImportModal();
    socialWeekOffset = weekOffsetFor(res.first_date);
    setActivePage("social");
    toast(`Imported ${res.imported} post${res.imported === 1 ? "" : "s"}`
      + (res.replaced ? ` (${res.replaced} replaced)` : ""));
    poll();
  } catch (e) {
    toast("Import failed: " + errText(e), "error");
    previewSocialImport();  // show what changed, e.g. a slot taken meanwhile
  }
}

// ---------------- Modals ----------------

function openTaskModal(t) {
  document.getElementById("task-modal-title").textContent = t ? "Edit task" : "Add task";
  document.getElementById("task-id").value = t?.id || "";
  document.getElementById("task-title").value = t?.title || "";
  document.getElementById("task-notes").value = t?.notes || "";
  document.getElementById("task-priority").value = t?.priority || "medium";
  document.getElementById("task-status").value = t?.status || "pending";
  document.getElementById("task-estimate").value = t?.estimated_minutes || "";
  document.getElementById("task-deadline").value = t?.deadline || "";
  document.getElementById("task-scheduled-at").value = t?.scheduled_at ? t.scheduled_at.slice(0, 16) : "";
  document.getElementById("task-calendar").checked = !!t?.calendar_event_id;
  document.getElementById("task-tags").value = (t?.tags || []).join(", ");
  document.getElementById("task-delete").classList.toggle("hidden", !t);
  document.getElementById("task-modal").classList.remove("hidden");
}

function closeTaskModal() { document.getElementById("task-modal").classList.add("hidden"); }

function openWatchModal() {
  document.getElementById("watch-url").value = "";
  document.getElementById("watch-title").value = "";
  document.getElementById("watch-where").value = "";
  document.getElementById("watch-tags").value = "";
  document.getElementById("watch-notes").value = "";
  document.getElementById("watch-modal").classList.remove("hidden");
}

function closeWatchModal() { document.getElementById("watch-modal").classList.add("hidden"); }

function openShoppingModal() {
  document.getElementById("shopping-url").value = "";
  document.getElementById("shopping-title").value = "";
  document.getElementById("shopping-amount").value = "";
  document.getElementById("shopping-currency").value = "EUR";
  document.getElementById("shopping-tags").value = "";
  document.getElementById("shopping-notes").value = "";
  document.getElementById("shopping-modal").classList.remove("hidden");
}

function closeShoppingModal() { document.getElementById("shopping-modal").classList.add("hidden"); }

// ---------------- Actions ----------------

async function triggerSubagent(skill, task, toastMsg) {
  try {
    await api("POST", "/api/trigger", { skill, task });
    toast(toastMsg + " (you'll see the result in Telegram)");
  } catch (e) {
    toast("Trigger failed: " + e.message, "error");
  }
}

function toast(msg, kind) {
  const t = el("div", { class: "toast" + (kind === "error" ? " error" : "") }, msg);
  document.getElementById("toasts").appendChild(t);
  setTimeout(() => t.remove(), 5000);
}

// ---------------- Wire up ----------------

document.addEventListener("DOMContentLoaded", () => {
  const shiftWeek = (n) => { weekOffset += n; renderTasks(); };
  document.getElementById("week-prev").addEventListener("click", () => shiftWeek(-1));
  document.getElementById("week-next").addEventListener("click", () => shiftWeek(1));
  document.getElementById("week-today").addEventListener("click", () => {
    weekOffset = 0;
    renderTasks();
  });

  const shiftSocialWeek = (n) => { socialWeekOffset += n; renderSocial(); };
  document.getElementById("social-week-prev").addEventListener("click", () => shiftSocialWeek(-1));
  document.getElementById("social-week-next").addEventListener("click", () => shiftSocialWeek(1));
  document.getElementById("social-week-today").addEventListener("click", () => {
    socialWeekOffset = 0;
    renderSocial();
  });

  document.getElementById("add-task-btn").addEventListener("click", () => openTaskModal(null));
  document.getElementById("add-watch-btn").addEventListener("click", openWatchModal);
  document.getElementById("add-shopping-btn").addEventListener("click", openShoppingModal);

  document.getElementById("task-cancel").addEventListener("click", closeTaskModal);
  document.getElementById("task-modal-x").addEventListener("click", closeTaskModal);
  document.getElementById("watch-cancel").addEventListener("click", closeWatchModal);
  document.getElementById("watch-modal-x").addEventListener("click", closeWatchModal);
  document.getElementById("shopping-cancel").addEventListener("click", closeShoppingModal);
  document.getElementById("shopping-modal-x").addEventListener("click", closeShoppingModal);

  // Click on the backdrop closes any modal. Only a press that starts on the
  // backdrop counts: selecting text in a field and releasing outside the card
  // must not throw away the edits.
  document.querySelectorAll(".modal").forEach((m) => {
    let pressedBackdrop = false;
    m.addEventListener("mousedown", (e) => { pressedBackdrop = e.target === m; });
    m.addEventListener("click", (e) => {
      if (e.target === m && pressedBackdrop) m.classList.add("hidden");
    });
  });

  // "Scheduled" means "on the calendar", so the status follows the toggle.
  document.getElementById("task-calendar").addEventListener("change", (e) => {
    const status = document.getElementById("task-status");
    if (status.value !== "done") status.value = e.target.checked ? "scheduled" : "pending";
  });

  document.getElementById("task-save").addEventListener("click", async () => {
    const id = document.getElementById("task-id").value;
    const calendar = document.getElementById("task-calendar").checked;
    if (calendar && !document.getElementById("task-scheduled-at").value) {
      toast("Pick a time under When to add it to your calendar", "error");
      return;
    }
    const payload = {
      calendar,
      title: document.getElementById("task-title").value,
      notes: document.getElementById("task-notes").value,
      priority: document.getElementById("task-priority").value,
      status: document.getElementById("task-status").value,
      estimated_minutes: parseInt(document.getElementById("task-estimate").value, 10) || null,
      deadline: document.getElementById("task-deadline").value || null,
      scheduled_at: (() => {
        const v = document.getElementById("task-scheduled-at").value;
        return v ? v.replace("T", " ") : null;
      })(),
      tags: document.getElementById("task-tags").value,
    };
    try {
      const saved = id
        ? await api("PATCH", "/api/tasks/" + id, payload)
        : await api("POST", "/api/tasks", payload);
      closeTaskModal();
      poll();
      if (saved?._calendar_warning) {
        toast("Saved, but the calendar wasn't updated: " + saved._calendar_warning, "error");
      }
    } catch (e) { toast("Save failed: " + e.message, "error"); }
  });

  document.getElementById("task-delete").addEventListener("click", async () => {
    const id = document.getElementById("task-id").value;
    if (!id || !confirm("Delete task?")) return;
    try {
      await api("DELETE", "/api/tasks/" + id);
      closeTaskModal();
      poll();
    } catch (e) { toast("Delete failed: " + e.message, "error"); }
  });

  document.getElementById("watch-save").addEventListener("click", async () => {
    const payload = {
      url: document.getElementById("watch-url").value,
      title: document.getElementById("watch-title").value,
      where_url: document.getElementById("watch-where").value,
      tags: document.getElementById("watch-tags").value,
      notes: document.getElementById("watch-notes").value,
    };
    try {
      await api("POST", "/api/watchlist", payload);
      closeWatchModal();
      poll();
    } catch (e) { toast("Save failed: " + e.message, "error"); }
  });

  document.getElementById("watch-search").addEventListener("input", (e) => {
    watchFilter = e.target.value;
    renderWatch();
  });

  document.getElementById("watch-show-watched").addEventListener("change", (e) => {
    watchShowWatched = e.target.checked;
    renderWatch();
  });

  document.getElementById("shopping-save").addEventListener("click", async () => {
    const payload = {
      url: document.getElementById("shopping-url").value,
      title: document.getElementById("shopping-title").value,
      amount: document.getElementById("shopping-amount").value,
      currency: document.getElementById("shopping-currency").value,
      tags: document.getElementById("shopping-tags").value,
      notes: document.getElementById("shopping-notes").value,
    };
    try {
      await api("POST", "/api/shopping", payload);
      closeShoppingModal();
      poll();
    } catch (e) { toast("Save failed: " + e.message, "error"); }
  });

  document.getElementById("shopping-search").addEventListener("input", (e) => {
    shoppingFilter = e.target.value;
    renderShopping();
  });

  document.getElementById("shopping-show-bought").addEventListener("change", (e) => {
    shoppingShowBought = e.target.checked;
    renderShopping();
  });

  const onPriceBound = (setter) => (e) => {
    const v = parseFloat(e.target.value);
    setter(Number.isFinite(v) ? v : null);
    renderShopping();
  };
  document.getElementById("shopping-min-price").addEventListener("input", onPriceBound((v) => shoppingMinPrice = v));
  document.getElementById("shopping-max-price").addEventListener("input", onPriceBound((v) => shoppingMaxPrice = v));
  document.getElementById("shopping-sort").addEventListener("change", (e) => {
    shoppingSort = e.target.value;
    renderShopping();
  });

  // Esc closes modals; ←/→ walk the task or social week
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeTaskModal(); closeWatchModal(); closeShoppingModal(); closePersonModal(); closePersonEditModal();
      closeSocialModal(); closeSocialImportModal();
    }
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    if (activePage !== "tasks" && activePage !== "social") return;
    if (document.querySelector(".modal:not(.hidden)")) return;
    if (e.target?.closest?.("input, textarea, select")) return;
    const step = e.key === "ArrowLeft" ? -1 : 1;
    if (activePage === "tasks") shiftWeek(step);
    else shiftSocialWeek(step);
  });

  // ---- Social: post editor ----
  document.getElementById("add-social-btn").addEventListener("click", () => openSocialModal(null));
  document.getElementById("social-cancel").addEventListener("click", closeSocialModal);
  document.getElementById("social-modal-x").addEventListener("click", closeSocialModal);
  document.getElementById("social-save").addEventListener("click", saveSocialPost);
  document.getElementById("social-text").addEventListener("input", updateSocialCount);
  document.getElementById("social-platform").addEventListener("change", updateSocialCount);

  document.getElementById("social-mark-posted").addEventListener("click", () => {
    document.getElementById("social-status").value = "posted";
    saveSocialPost();  // saves any edits along with the status
  });

  document.getElementById("social-delete").addEventListener("click", async () => {
    const id = document.getElementById("social-id").value;
    if (!id || !confirm("Delete this post?")) return;
    try {
      await api("DELETE", "/api/social/posts/" + id);
      closeSocialModal();
      poll();
    } catch (e) { toast("Delete failed: " + errText(e), "error"); }
  });

  document.getElementById("social-copy-text").addEventListener("click", async () => {
    const ok = await copyToClipboard(document.getElementById("social-text").value);
    if (ok) toast("Copied. Paste it as-is");
    else toast("Copy failed. Select the text and copy it manually", "error");
  });

  const socialImageFile = document.getElementById("social-image-file");
  document.getElementById("social-image-pick").addEventListener("click", () => socialImageFile.click());
  socialImageFile.addEventListener("change", () => {
    const file = socialImageFile.files[0];
    socialImageFile.value = "";
    setSocialImageFromFile(file);
  });
  const dropzone = document.getElementById("social-dropzone");
  dropzone.addEventListener("click", (e) => {
    if (!socialImage && !e.target.closest("button")) socialImageFile.click();
  });
  dropzone.addEventListener("dragover", (e) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dropzone.classList.add("drag-over");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
  dropzone.addEventListener("drop", (e) => {
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    dropzone.classList.remove("drag-over");
    setSocialImageFromFile(e.dataTransfer.files[0]);
  });
  // A file dropped anywhere else must not navigate the tab away from the editor.
  window.addEventListener("dragover", (e) => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); });
  window.addEventListener("drop", (e) => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); });
  // Paste an image anywhere in the open editor.
  document.addEventListener("paste", (e) => {
    if (document.getElementById("social-modal").classList.contains("hidden")) return;
    const file = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
    if (!file) return;
    // Rich text copied from a doc can carry a rendered image too; in a text
    // field, the text wins.
    if (e.target?.closest?.("textarea, input") && e.clipboardData.types.includes("text/plain")) return;
    e.preventDefault();
    setSocialImageFromFile(file);
  });
  document.getElementById("social-image-remove").addEventListener("click", () => {
    socialImage = null;
    socialImageDirty = true;
    renderSocialImage();
  });
  document.getElementById("social-copy-image").addEventListener("click", async () => {
    if (!socialImage) return;
    try {
      await copyImageToClipboard(socialImage.url);
      toast("Image copied");
    } catch (e) { toast("Couldn't copy the image: " + e.message, "error"); }
  });

  // ---- Social: import ----
  document.getElementById("import-social-btn").addEventListener("click", openSocialImportModal);
  document.getElementById("social-import-cancel").addEventListener("click", closeSocialImportModal);
  document.getElementById("social-import-x").addEventListener("click", closeSocialImportModal);
  const importFile = document.getElementById("social-import-file");
  document.getElementById("social-import-pick").addEventListener("click", () => importFile.click());
  importFile.addEventListener("change", async () => {
    const file = importFile.files[0];
    importFile.value = "";
    if (!file) return;
    socialImportFileText = await file.text();
    document.getElementById("social-import-filename").textContent = file.name;
    document.getElementById("social-import-json").value = "";
    previewSocialImport();
  });
  document.getElementById("social-import-json").addEventListener("input", () => {
    socialImportFileText = null;
    document.getElementById("social-import-filename").textContent = "";
    clearTimeout(socialImportTimer);
    socialImportTimer = setTimeout(previewSocialImport, 350);
  });
  document.getElementById("social-import-overwrite").addEventListener("change", previewSocialImport);
  document.getElementById("social-import-apply").addEventListener("click", applySocialImport);
  document.getElementById("social-copy-schema").addEventListener("click", async () => {
    try {
      if (!socialSchemaText) socialSchemaText = JSON.stringify(await api("GET", "/api/social/schema"), null, 2);
      const ok = await copyToClipboard(socialSchemaText);
      if (ok) toast("Schema copied. Give it to Claude as the contract");
      else toast("Copy failed", "error");
    } catch (e) { toast("Couldn't load the schema: " + errText(e), "error"); }
  });

  const cs = document.getElementById("contacts-search");
  if (cs) cs.addEventListener("input", (e) => { contactsFilter = e.target.value; renderContacts(); });

  const csort = document.getElementById("contacts-sort");
  if (csort) csort.addEventListener("change", (e) => { contactsSort = e.target.value; renderContacts(); });

  document.getElementById("add-contact-btn").addEventListener("click", () => openPersonEditModal(null));

  const pmc = document.getElementById("person-modal-close");
  if (pmc) pmc.addEventListener("click", closePersonModal);

  const pme = document.getElementById("person-modal-edit");
  if (pme) pme.addEventListener("click", () => { closePersonModal(); openPersonEditModal(currentPerson); });

  document.getElementById("person-cancel").addEventListener("click", closePersonEditModal);
  document.getElementById("person-edit-x").addEventListener("click", closePersonEditModal);

  document.getElementById("person-save").addEventListener("click", async () => {
    const id = document.getElementById("person-id").value;
    const name = document.getElementById("person-name").value.trim();
    if (!name) { toast("Name is required", "error"); return; }
    const payload = {
      name,
      role: document.getElementById("person-role").value.trim(),
      company: document.getElementById("person-company").value.trim(),
      email: document.getElementById("person-email").value.trim(),
      source: document.getElementById("person-source").value.trim(),
      linkedin_url: document.getElementById("person-linkedin").value.trim(),
      x_handle: document.getElementById("person-x").value.trim(),
      tags: document.getElementById("person-tags").value,
    };
    try {
      if (id) await api("PATCH", "/api/people/" + id, payload);
      else await api("POST", "/api/people", payload);
      closePersonEditModal();
      poll();
    } catch (e) { toast("Save failed: " + e.message, "error"); }
  });

  document.getElementById("person-delete").addEventListener("click", async () => {
    const id = document.getElementById("person-id").value;
    if (!id || !confirm("Delete this contact?")) return;
    try {
      await api("DELETE", "/api/people/" + id);
      closePersonEditModal();
      poll();
    } catch (e) { toast("Delete failed: " + e.message, "error"); }
  });

  const recluster = document.getElementById("wiki-recluster-btn");
  if (recluster) recluster.addEventListener("click", reclusterWiki);

  // Chat
  const chatNew = document.getElementById("chat-new-btn");
  if (chatNew) chatNew.addEventListener("click", newChat);
  const chatSend = document.getElementById("chat-send");
  if (chatSend) chatSend.addEventListener("click", sendChatMessage);
  const chatInput = document.getElementById("chat-input");
  if (chatInput) {
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
    });
    // Auto-grow the composer up to a few lines.
    chatInput.addEventListener("input", () => {
      chatInput.style.height = "auto";
      chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
    });
  }

  const validPages = [...document.querySelectorAll(".tab")].map(t => t.dataset.page);
  document.querySelectorAll(".tab").forEach(t => {
    t.addEventListener("click", () => setActivePage(t.dataset.page));
  });
  setActivePage(validPages.includes(activePage) ? activePage : "tasks");
  window.addEventListener("hashchange", () => {
    const page = location.hash.slice(1);
    if (validPages.includes(page) && page !== activePage) setActivePage(page);
  });

  poll();
});

// ---- Inbox ----

function renderInbox() {
  const list = document.getElementById("inbox-list");
  if (!list) return;
  const items = state.inbox || [];
  document.getElementById("inbox-count").textContent = items.length;
  list.innerHTML = "";
  if (!items.length) {
    list.appendChild(emptyState("All clear",
      "Proposals from the agent — new tasks, contact updates — will land here for review."));
    return;
  }
  // Group by source_interaction_id
  const groups = new Map();
  for (const it of items) {
    const k = it.source_interaction_id || "_";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(it);
  }
  for (const [src, group] of groups) {
    const wrap = el("div", { class: "inbox-group" });
    wrap.appendChild(el("div", { class: "inbox-group-head muted" },
      src === "_" ? "Unlinked" : `From interaction ${src.slice(0, 8)}…`));
    for (const it of group) wrap.appendChild(inboxCard(it));
    list.appendChild(wrap);
  }
}

function inboxCard(item) {
  const p = item.payload || {};
  let summary = "";
  if (item.type === "task_proposal") summary = `Task: ${p.title || "(untitled)"}${p.due_at ? " — due " + p.due_at : ""}`;
  else if (item.type === "contact_update") summary = `Contact update: ${Object.keys(p.patch || {}).join(", ") || "(empty)"}`;
  else if (item.type === "new_person") summary = `New person: ${(p.fields || {}).name || "(unnamed)"}`;
  else summary = `${item.type}: ${JSON.stringify(p).slice(0, 80)}`;

  const desc = p.description ? el("div", { class: "muted small" }, p.description) : null;

  return el("div", { class: "inbox-card", dataset: { id: item.id } },
    el("div", { class: "inbox-head" },
      el("span", { class: "inbox-type tag" }, item.type),
      el("span", { class: "inbox-summary" }, summary),
    ),
    desc,
    el("div", { class: "inbox-actions" },
      el("button", { class: "primary", onclick: () => decideInbox(item.id, "accept") }, "Accept"),
      el("button", { onclick: () => decideInbox(item.id, "reject") }, "Reject"),
    ),
  );
}

async function decideInbox(id, action) {
  try {
    await api("POST", `/api/inbox/${id}/${action}`);
    poll();
  } catch (e) { toast(`${action} failed: ${e.message}`, "error"); }
}

// ---- Contacts ----

function fmtDateShort(s) {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d)) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function renderContacts() {
  const tbody = document.getElementById("contacts-tbody");
  if (!tbody) return;
  const all = state.people || [];
  const q = contactsFilter.trim().toLowerCase();
  let rows = q
    ? all.filter(p => (p.name || "").toLowerCase().includes(q)
                    || (p.company || "").toLowerCase().includes(q)
                    || (p.role || "").toLowerCase().includes(q)
                    || (p.email || "").toLowerCase().includes(q)
                    || (p.source || "").toLowerCase().includes(q)
                    || (p.tags || "").toLowerCase().includes(q))
    : all.slice();
  // Blanks sort last for text keys; comparator map mirrors the shopping list.
  const byText = (key) => (a, b) => {
    const av = (a[key] || "").toString(), bv = (b[key] || "").toString();
    if (!av && !bv) return 0;
    if (!av) return 1;
    if (!bv) return -1;
    return av.localeCompare(bv, undefined, { sensitivity: "base" });
  };
  const cmp = {
    name: (a, b) => (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base" }),
    "name-desc": (a, b) => (b.name || "").localeCompare(a.name || "", undefined, { sensitivity: "base" }),
    newest: (a, b) => (b.created_at || "").localeCompare(a.created_at || ""),
    oldest: (a, b) => (a.created_at || "").localeCompare(b.created_at || ""),
    role: byText("role"),
    source: byText("source"),
  }[contactsSort] || cmp_name_fallback;
  rows.sort(cmp);
  document.getElementById("contacts-count").textContent = all.length;
  tbody.innerHTML = "";
  if (!rows.length) {
    const cell = el("td", { colspan: "5" }, all.length
      ? emptyState("No matches", "Try a different name, company, source, or tag.")
      : emptyState("No contacts yet", "People the agent meets in your meetings and email will appear here. Or add one manually."));
    tbody.appendChild(el("tr", {}, cell));
    return;
  }
  for (const p of rows) {
    const sub = [p.role, p.company].filter(Boolean).join(" · ");
    tbody.appendChild(el("tr", { class: "contact-row", onclick: () => openPersonModal(p.id) },
      el("td", {},
        el("div", { class: "contact-name-cell" },
          avatar(p.name),
          el("span", { class: "contact-name" }, p.name),
        ),
      ),
      el("td", { class: "muted" }, sub || "—"),
      el("td", { class: "muted small" },
        p.email
          ? el("a", { href: "mailto:" + p.email, class: "contact-email", onclick: (e) => e.stopPropagation() }, p.email)
          : "—"),
      el("td", { class: "muted small" }, p.source || "—"),
      el("td", { class: "muted small" }, fmtDateShort(p.created_at) || "—"),
    ));
  }
}

function cmp_name_fallback(a, b) {
  return (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base" });
}

function avatar(name) {
  const initials = (name || "?").trim().split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  let hash = 0;
  for (const ch of (name || "")) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  const node = el("div", { class: "avatar" }, initials);
  node.style.background = `hsl(${hash % 360} 38% 46%)`;
  return node;
}

async function openPersonModal(id) {
  try {
    const p = await api("GET", `/api/people/${id}`);
    currentPerson = p;
    document.getElementById("person-modal-name").textContent = p.name;
    const meta = [p.role, p.company, p.email, p.linkedin_url].filter(Boolean).join(" • ");
    document.getElementById("person-modal-meta").textContent = meta;
    const tagsBox = document.getElementById("person-modal-tags");
    tagsBox.innerHTML = "";
    let tags = [];
    try { tags = JSON.parse(p.tags || "[]"); } catch {}
    for (const t of tags) tagsBox.appendChild(el("span", { class: "tag" }, t));
    const ints = document.getElementById("person-modal-interactions");
    ints.innerHTML = "";
    if (!p.interactions || !p.interactions.length) {
      ints.appendChild(el("div", { class: "muted" }, "No recorded interactions yet."));
    } else {
      for (const i of p.interactions) {
        const when = i.occurred_at || i.created_at;
        ints.appendChild(el("div", { class: "interaction-row" },
          el("span", { class: "tag" }, i.type),
          el("span", {}, i.title || "(untitled)"),
          el("span", { class: "muted small" }, when || ""),
        ));
      }
    }
    document.getElementById("person-modal").classList.remove("hidden");
  } catch (e) { toast("Load failed: " + e.message, "error"); }
}

function closePersonModal() {
  document.getElementById("person-modal").classList.add("hidden");
}

function openPersonEditModal(p) {
  document.getElementById("person-edit-title").textContent = p ? "Edit contact" : "Add contact";
  document.getElementById("person-id").value = p?.id || "";
  document.getElementById("person-name").value = p?.name || "";
  document.getElementById("person-role").value = p?.role || "";
  document.getElementById("person-company").value = p?.company || "";
  document.getElementById("person-email").value = p?.email || "";
  document.getElementById("person-source").value = p?.source || "";
  document.getElementById("person-linkedin").value = p?.linkedin_url || "";
  document.getElementById("person-x").value = p?.x_handle || "";
  let tags = [];
  try { tags = JSON.parse(p?.tags || "[]"); } catch {}
  document.getElementById("person-tags").value = Array.isArray(tags) ? tags.join(", ") : "";
  document.getElementById("person-delete").classList.toggle("hidden", !p);
  document.getElementById("person-edit-modal").classList.remove("hidden");
}

function closePersonEditModal() {
  document.getElementById("person-edit-modal").classList.add("hidden");
}

// ---- Wiki ----

let wikiTopics = [];
let wikiSelectedId = null;
let wikiLoaded = false;

async function loadWikiTopics(autoSelect = true) {
  try {
    const data = await api("GET", "/api/wiki/topics");
    wikiTopics = data.topics || [];
    document.getElementById("wiki-count").textContent = wikiTopics.length;
    renderWikiSidebar();
    renderTabBadges();
    if (autoSelect && !wikiSelectedId && wikiTopics.length) {
      loadWikiTopic(wikiTopics[0].id);
    } else if (wikiSelectedId && !wikiTopics.find(t => t.id === wikiSelectedId)) {
      wikiSelectedId = null;
      clearWikiContent();
    }
    wikiLoaded = true;
  } catch (e) {
    toast("Failed to load wiki: " + e.message, "error");
  }
}

function renderWikiSidebar() {
  const list = document.getElementById("wiki-topic-list");
  list.innerHTML = "";
  if (!wikiTopics.length) {
    const empty = el("li", { class: "muted" }, "No topics yet — share a URL with the bot.");
    empty.style.cursor = "default";
    list.appendChild(empty);
    return;
  }
  for (const t of wikiTopics) {
    const cls = t.id === wikiSelectedId ? "active" : "";
    list.appendChild(el("li", {
      class: cls,
      onclick: () => loadWikiTopic(t.id),
    },
      el("span", {}, t.title),
      el("span", { class: "wiki-count-badge" }, String(t.bookmark_count || 0)),
    ));
  }
}

function clearWikiContent() {
  document.getElementById("wiki-empty").classList.remove("hidden");
  document.getElementById("wiki-article").classList.add("hidden");
  document.getElementById("wiki-sources-heading").classList.add("hidden");
  document.getElementById("wiki-sources").classList.add("hidden");
}

async function loadWikiTopic(id) {
  wikiSelectedId = id;
  renderWikiSidebar();
  try {
    const topic = await api("GET", "/api/wiki/topics/" + id);
    renderWikiTopic(topic);
  } catch (e) {
    toast("Failed to load topic: " + e.message, "error");
  }
}

function renderMarkdown(md) {
  // Tiny markdown renderer — headings, bold, italic, code, links, paragraphs.
  // Escape HTML first.
  let s = (md || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  s = s.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  s = s.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  s = s.replace(/^# (.*)$/gm, "<h1>$1</h1>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // Paragraphs: split on blank line, wrap non-block lines.
  const blocks = s.split(/\n{2,}/).map(b => {
    if (/^\s*<h[1-3]>/.test(b)) return b;
    return "<p>" + b.replace(/\n/g, "<br>") + "</p>";
  });
  return blocks.join("\n");
}

function renderWikiTopic(topic) {
  const article = document.getElementById("wiki-article");
  const sourcesHeading = document.getElementById("wiki-sources-heading");
  const sources = document.getElementById("wiki-sources");
  const empty = document.getElementById("wiki-empty");
  empty.classList.add("hidden");
  article.classList.remove("hidden");
  sourcesHeading.classList.remove("hidden");
  sources.classList.remove("hidden");

  const header = `<h2>${topic.title}</h2>` +
    (topic.summary ? `<p class="muted">${topic.summary}</p>` : "");
  let body;
  if (topic.article_md) {
    body = renderMarkdown(topic.article_md);
  } else {
    body = '<p class="muted">No article yet. ' +
      `<button onclick="resynthesizeTopic('${topic.id}')">Generate</button></p>`;
  }
  const built = topic.article_built_at
    ? `<p class="muted small">Article built ${new Date(topic.article_built_at).toLocaleString()} — ` +
      `<button onclick="resynthesizeTopic('${topic.id}')">Refresh</button></p>`
    : "";
  article.innerHTML = header + body + built;

  sources.innerHTML = "";
  for (const b of (topic.bookmarks || [])) {
    const li = el("li", {});
    li.appendChild(el("a", { href: b.url, target: "_blank", rel: "noopener" }, b.title || b.url));
    if (b.summary) li.appendChild(el("div", { class: "src-summary" }, b.summary));
    sources.appendChild(li);
  }
  if (!(topic.bookmarks || []).length) {
    sources.innerHTML = '<li class="muted">No sources.</li>';
  }
}

async function resynthesizeTopic(id) {
  try {
    await api("POST", `/api/wiki/topics/${id}/resynthesize`);
    toast("Refreshing article — check back in ~10s.");
    setTimeout(() => loadWikiTopic(id), 12000);
  } catch (e) {
    toast("Refresh failed: " + e.message, "error");
  }
}

async function reclusterWiki() {
  if (!confirm("Recluster all bookmarks? Topics may be merged or renamed.")) return;
  try {
    await api("POST", "/api/wiki/recluster");
    toast("Reclustering in background — refreshing in ~30s.");
    setTimeout(() => loadWikiTopics(false), 30000);
  } catch (e) {
    toast("Recluster failed: " + e.message, "error");
  }
}

// Expose helpers used by inline onclick handlers in rendered HTML.
window.resynthesizeTopic = resynthesizeTopic;

// ---- Local chat ----

let chatLoaded = false;
let chatConfigured = false;
let chatConversations = [];
let chatActiveId = localStorage.getItem("chorgi.chatActiveId") || null;
let chatStreaming = false;
let chatModelLabel = "";

// Each SSE delta from the local server is ~one token; rate is measured from
// the first delta so prompt processing time is excluded.
function updateChatSpeed(tokens, firstAt) {
  if (tokens < 2) return;
  const secs = (performance.now() - firstAt) / 1000;
  if (secs <= 0) return;
  const tps = (tokens - 1) / secs;
  document.getElementById("chat-model").textContent =
    chatModelLabel + " · " + tps.toFixed(1) + " tok/s";
}

async function initChat() {
  chatLoaded = true;
  try {
    const cfg = await api("GET", "/api/chat/config");
    chatConfigured = !!cfg.configured;
    const modelEl = document.getElementById("chat-model");
    chatModelLabel = chatConfigured
      ? "model: " + cfg.model
      : "Local LLM not configured — set LOCAL_LLM_BASE_URL and LOCAL_LLM_MODEL in secrets.env";
    modelEl.textContent = chatModelLabel;
    setChatComposerEnabled(chatConfigured);
  } catch (e) {
    toast("Chat config failed: " + e.message, "error");
  }
  await loadChatConversations();
  if (chatActiveId && chatConversations.find(c => c.id === chatActiveId)) {
    selectChat(chatActiveId);
  } else {
    chatActiveId = null;
    renderChatMessages(null);
  }
}

function setChatComposerEnabled(on) {
  const input = document.getElementById("chat-input");
  const send = document.getElementById("chat-send");
  if (input) input.disabled = !on;
  if (send) send.disabled = !on || chatStreaming;
}

async function loadChatConversations() {
  try {
    const data = await api("GET", "/api/chat/conversations");
    chatConversations = data.conversations || [];
    renderChatSidebar();
  } catch (e) {
    toast("Failed to load chats: " + e.message, "error");
  }
}

function renderChatSidebar() {
  const list = document.getElementById("chat-conv-list");
  if (!list) return;
  list.innerHTML = "";
  if (!chatConversations.length) {
    const empty = el("li", { class: "muted" }, "No chats yet.");
    empty.style.cursor = "default";
    list.appendChild(empty);
    return;
  }
  for (const c of chatConversations) {
    const li = el("li", {
      class: c.id === chatActiveId ? "active" : "",
      onclick: () => selectChat(c.id),
    },
      el("span", { class: "chat-conv-title" }, c.title || "New chat"),
      el("button", {
        class: "chat-conv-del",
        title: "Delete",
        onclick: (e) => { e.stopPropagation(); deleteChat(c.id); },
      }, "✕"),
    );
    list.appendChild(li);
  }
}

async function newChat() {
  if (!chatConfigured) return;
  try {
    const conv = await api("POST", "/api/chat/conversations");
    chatActiveId = conv.id;
    localStorage.setItem("chorgi.chatActiveId", chatActiveId);
    await loadChatConversations();
    renderChatMessages(conv);
    document.getElementById("chat-input")?.focus();
  } catch (e) {
    toast("New chat failed: " + e.message, "error");
  }
}

async function selectChat(id) {
  chatActiveId = id;
  localStorage.setItem("chorgi.chatActiveId", id);
  renderChatSidebar();
  try {
    const conv = await api("GET", "/api/chat/conversations/" + id);
    renderChatMessages(conv);
  } catch (e) {
    toast("Failed to load chat: " + e.message, "error");
  }
}

async function deleteChat(id) {
  if (!confirm("Delete this chat?")) return;
  try {
    await api("DELETE", "/api/chat/conversations/" + id);
    if (chatActiveId === id) {
      chatActiveId = null;
      localStorage.removeItem("chorgi.chatActiveId");
      renderChatMessages(null);
    }
    await loadChatConversations();
  } catch (e) {
    toast("Delete failed: " + e.message, "error");
  }
}

function renderChatMessages(conv) {
  const box = document.getElementById("chat-messages");
  if (!box) return;
  box.innerHTML = "";
  if (!conv || !(conv.messages || []).length) {
    box.appendChild(el("div", { class: "chat-empty muted" },
      conv ? "Send a message to start." : "Pick a chat or start a new one."));
    return;
  }
  for (const m of conv.messages) box.appendChild(chatBubble(m.role, m.content));
  box.scrollTop = box.scrollHeight;
}

function chatBubble(role, content) {
  const bubble = el("div", { class: "chat-msg " + role });
  if (role === "assistant") bubble.innerHTML = renderMarkdown(content);
  else bubble.textContent = content;
  return bubble;
}

async function sendChatMessage() {
  if (chatStreaming || !chatConfigured) return;
  const input = document.getElementById("chat-input");
  const content = (input.value || "").trim();
  if (!content) return;

  // Make sure we have a conversation to attach to.
  if (!chatActiveId) {
    try {
      const conv = await api("POST", "/api/chat/conversations");
      chatActiveId = conv.id;
      localStorage.setItem("chorgi.chatActiveId", chatActiveId);
    } catch (e) { toast("New chat failed: " + e.message, "error"); return; }
  }

  const box = document.getElementById("chat-messages");
  if (box.querySelector(".chat-empty")) box.innerHTML = "";
  input.value = "";
  input.style.height = "auto";

  box.appendChild(chatBubble("user", content));
  const assistant = chatBubble("assistant", "");
  assistant.classList.add("streaming");
  box.appendChild(assistant);
  box.scrollTop = box.scrollHeight;

  chatStreaming = true;
  setChatComposerEnabled(true);

  let acc = "";
  let tokenCount = 0;
  let firstTokenAt = 0;
  try {
    const r = await fetch("/api/chat/conversations/" + chatActiveId + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop();  // keep the trailing partial event
      for (const ev of events) {
        const line = ev.trim();
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") continue;
        let obj;
        try { obj = JSON.parse(data); } catch { continue; }
        if (obj.error) { toast("LLM error: " + obj.error, "error"); acc += "\n\n_(error: " + obj.error + ")_"; }
        else if (obj.delta) {
          acc += obj.delta;
          tokenCount++;
          if (tokenCount === 1) firstTokenAt = performance.now();
          else updateChatSpeed(tokenCount, firstTokenAt);
        }
        assistant.innerHTML = renderMarkdown(acc);
        box.scrollTop = box.scrollHeight;
      }
    }
  } catch (e) {
    toast("Send failed: " + e.message, "error");
    if (!acc) assistant.innerHTML = renderMarkdown("_(failed to get a reply)_");
  } finally {
    assistant.classList.remove("streaming");
    chatStreaming = false;
    setChatComposerEnabled(true);
    loadChatConversations();  // refresh title/order in the sidebar
  }
}
