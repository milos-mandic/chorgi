// Chorgi dashboard — vanilla JS, polls /api/state every 4s.

const POLL_MS = 4000;
let state = { tasks: [], bookmarks: [], watchlist: [], shopping: [], linkedin_week: {}, people: [], inbox: [] };
let lastSnapshot = "";
let bookmarkFilter = "";
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

// ---------------- Fetch helpers ----------------

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`${method} ${path} → ${r.status}: ${txt}`);
  }
  return r.json();
}

// ---------------- Poll loop ----------------

let pollCount = 0;
async function poll() {
  try {
    const data = await api("GET", "/api/state");
    const snap = JSON.stringify([data.tasks.length, data.bookmarks.length, data.linkedin_week?.week_of]);
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
  renderBookmarks();
  renderWatch();
  renderShopping();
  renderLinkedIn();
  renderInbox();
  renderContacts();
  renderTabBadges();
}

// ---- Tabs / pages ----

let activePage = (location.hash || "").slice(1)
  || localStorage.getItem("chorgi.activePage") || "tasks";

function setActivePage(name) {
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
  document.getElementById("add-bookmark-btn").classList.toggle("hidden", name !== "bookmarks");
  document.getElementById("add-watch-btn").classList.toggle("hidden", name !== "watch");
  document.getElementById("add-shopping-btn").classList.toggle("hidden", name !== "shopping");
  document.getElementById("add-contact-btn").classList.toggle("hidden", name !== "contacts");
  if (name === "wiki" && !wikiLoaded) loadWikiTopics();
  if (name === "chat" && !chatLoaded) initChat();
}

function renderTabBadges() {
  const pending = (state.tasks || []).filter(t => t.status === "pending").length;
  const bookmarks = (state.bookmarks || []).length;
  const watch = (state.watchlist || []).filter(w => !w.watched).length;
  const shopping = (state.shopping || []).filter(s => !s.bought).length;
  const inbox = (state.inbox || []).length;
  const contacts = (state.people || []).length;
  const wiki = wikiTopics.length;
  const set = (id, n) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = n;
    el.classList.toggle("zero", !n);
  };
  set("tab-badge-tasks", pending);
  set("tab-badge-bookmarks", bookmarks);
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

// ---- Tasks ----

function renderTasks() {
  const groups = { pending: [], scheduled: [], done: [] };
  for (const t of state.tasks) {
    (groups[t.status] || groups.pending).push(t);
  }
  for (const status of Object.keys(groups)) {
    const col = document.getElementById("col-" + status);
    col.innerHTML = "";
    document.getElementById("count-" + status).textContent = groups[status].length;
    for (const t of groups[status]) {
      col.appendChild(taskCard(t));
    }
  }
}

function taskCard(t) {
  const card = el("div", {
    class: `card priority-${t.priority || "medium"} status-${t.status}`,
    draggable: "true",
    dataset: { taskId: t.id },
    ondragstart: (e) => {
      e.dataTransfer.setData("text/plain", t.id);
      card.classList.add("dragging");
    },
    ondragend: () => card.classList.remove("dragging"),
    onclick: (e) => {
      // Don't open modal if clicking an action button
      if (e.target.closest("button")) return;
      openTaskModal(t);
    },
  });
  card.appendChild(el("div", { class: "title" }, t.title));
  const meta = el("div", { class: "meta" });
  if (t.deadline) meta.appendChild(el("span", {}, "📅 " + t.deadline));
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

// Drag-drop wiring (set up once)
function setupKanbanDrops() {
  document.querySelectorAll(".kanban-col").forEach((col) => {
    const target = col.querySelector(".cards");
    target.addEventListener("dragover", (e) => {
      e.preventDefault();
      target.classList.add("drag-over");
    });
    target.addEventListener("dragleave", () => target.classList.remove("drag-over"));
    target.addEventListener("drop", async (e) => {
      e.preventDefault();
      target.classList.remove("drag-over");
      const taskId = e.dataTransfer.getData("text/plain");
      const newStatus = col.dataset.status;
      try {
        await api("PATCH", "/api/tasks/" + taskId, { status: newStatus });
        poll();
      } catch (err) {
        toast("Move failed: " + err.message, "error");
      }
    });
  });
}

// ---- Bookmarks ----

function renderBookmarks() {
  const container = document.getElementById("bookmark-groups");
  container.innerHTML = "";
  const q = bookmarkFilter.toLowerCase();
  const filtered = q
    ? state.bookmarks.filter((b) => {
        const hay = [b.url, b.title, b.notes, ...(b.tags || [])].join(" ").toLowerCase();
        return hay.includes(q);
      })
    : state.bookmarks;

  const groups = {};
  for (const b of filtered) {
    const tag = (b.tags && b.tags[0]) || "untagged";
    (groups[tag] = groups[tag] || []).push(b);
  }
  const sortedGroups = Object.keys(groups).sort((a, b) => {
    if (a === "untagged") return 1;
    if (b === "untagged") return -1;
    return a.localeCompare(b);
  });
  for (const tag of sortedGroups) {
    const group = el("div", { class: "bookmark-group" });
    group.appendChild(el("h4", {}, tag + " · " + groups[tag].length));
    for (const b of groups[tag]) group.appendChild(bookmarkCard(b));
    container.appendChild(group);
  }
}

function bookmarkCard(b) {
  const card = el("div", { class: "bookmark" });
  const title = b.title || b.url;
  card.appendChild(el("a", { href: b.url, target: "_blank", rel: "noopener" }, title));
  if (b.notes) card.appendChild(el("div", { class: "notes" }, b.notes));

  const actions = el("div", { class: "actions" });
  actions.appendChild(el("button", {
    onclick: () => triggerSubagent("general", `Fetch ${b.url} and write a 1-2 sentence summary. Then update the bookmark notes via the bookmarks skill.`, "Summarizing…")
  }, "Summarize"));
  actions.appendChild(el("button", {
    onclick: () => triggerSubagent("general", `Look at this bookmark and suggest 2-3 short tags for it. URL: ${b.url}, current title: ${b.title}, current notes: ${b.notes}. Update the bookmark via bookmarks skill.`, "Re-tagging…")
  }, "Re-tag"));
  actions.appendChild(el("button", {
    class: "danger",
    onclick: async () => {
      if (!confirm("Delete bookmark?")) return;
      await api("DELETE", "/api/bookmarks", { url: b.url });
      poll();
    }
  }, "Delete"));
  card.appendChild(actions);
  return card;
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
  const container = document.getElementById("shopping-grid");
  if (!container) return;
  container.innerHTML = "";
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
    container.appendChild(filtered
      ? emptyState("No matches", "No items match the current search or price filter.")
      : emptyState("Nothing on the list yet", "Click “Add item”, paste a product link, and set a price."));
    return;
  }
  for (const s of items) container.appendChild(shoppingCard(s));
}

function shoppingCard(s) {
  const card = el("div", { class: "shopping-card" + (s.bought ? " bought" : "") });

  const thumb = el("a", { class: "shopping-thumb", href: s.url, target: "_blank", rel: "noopener" });
  if (s.image) thumb.appendChild(el("img", { src: s.image, alt: "", loading: "lazy" }));
  else thumb.appendChild(el("div", { class: "shopping-thumb-fallback" }, (s.source || "🛒").slice(0, 12)));
  card.appendChild(thumb);

  const body = el("div", { class: "shopping-body" });
  body.appendChild(el("a", { class: "shopping-title", href: s.url, target: "_blank", rel: "noopener" }, s.title || s.url));

  body.appendChild(el("div", { class: "shopping-price" }, formatPrice(s.amount, s.currency)));

  const meta = el("div", { class: "shopping-meta" });
  if (s.category) meta.appendChild(el("span", { class: "shopping-chip cat" }, s.category));
  if (s.source) meta.appendChild(el("span", { class: "shopping-chip" }, s.source));
  for (const tag of (s.tags || [])) meta.appendChild(el("span", { class: "shopping-chip tag" }, tag));
  if (meta.children.length) body.appendChild(meta);

  if (s.notes) body.appendChild(el("div", { class: "shopping-summary" }, s.notes));

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
  body.appendChild(actions);

  card.appendChild(body);
  return card;
}

// ---- LinkedIn ----

function renderLinkedIn() {
  const container = document.getElementById("linkedin-days");
  container.innerHTML = "";
  const cal = state.linkedin_week || {};
  document.getElementById("linkedin-week-of").textContent =
    cal.week_of ? "(week of " + cal.week_of + ")" : "";
  const days = cal.days || [];
  if (!days.length) {
    container.appendChild(emptyState("No content calendar yet",
      "Ask the bot to plan your LinkedIn week and it will show up here."));
    return;
  }
  for (const d of days) {
    const card = el("div", { class: "linkedin-day" });
    card.appendChild(el("div", { class: "day-header" },
      el("span", {}, `${d.weekday} ${d.date}`),
      el("span", { class: "badge" }, d.status || "?"),
    ));
    if (d.topic) card.appendChild(el("div", { class: "topic" }, d.topic));
    if (d.angle) card.appendChild(el("div", { class: "angle" }, d.angle));

    const actions = el("div", { class: "actions" });
    actions.appendChild(el("button", {
      onclick: () => triggerSubagent("linkedin", `Write a LinkedIn post for ${d.date} (${d.weekday}). Topic: ${d.topic}. Angle: ${d.angle}. Format: ${d.format}. Pillar: ${d.pillar}.`, "Drafting post…")
    }, "Draft"));
    actions.appendChild(el("button", {
      onclick: () => triggerSubagent("linkedin", `Polish the existing draft for ${d.date}. Make it punchier and tighter.`, "Polishing…")
    }, "Polish"));
    card.appendChild(actions);
    container.appendChild(card);
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
  document.getElementById("task-tags").value = (t?.tags || []).join(", ");
  document.getElementById("task-delete").classList.toggle("hidden", !t);
  document.getElementById("task-modal").classList.remove("hidden");
}

function closeTaskModal() { document.getElementById("task-modal").classList.add("hidden"); }

function openBookmarkModal() {
  document.getElementById("bookmark-url").value = "";
  document.getElementById("bookmark-title").value = "";
  document.getElementById("bookmark-tags").value = "";
  document.getElementById("bookmark-notes").value = "";
  document.getElementById("bookmark-modal").classList.remove("hidden");
}

function closeBookmarkModal() { document.getElementById("bookmark-modal").classList.add("hidden"); }

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
  setupKanbanDrops();

  document.getElementById("add-task-btn").addEventListener("click", () => openTaskModal(null));
  document.getElementById("add-bookmark-btn").addEventListener("click", openBookmarkModal);
  document.getElementById("add-watch-btn").addEventListener("click", openWatchModal);
  document.getElementById("add-shopping-btn").addEventListener("click", openShoppingModal);

  document.getElementById("task-cancel").addEventListener("click", closeTaskModal);
  document.getElementById("task-modal-x").addEventListener("click", closeTaskModal);
  document.getElementById("bookmark-cancel").addEventListener("click", closeBookmarkModal);
  document.getElementById("bookmark-modal-x").addEventListener("click", closeBookmarkModal);
  document.getElementById("watch-cancel").addEventListener("click", closeWatchModal);
  document.getElementById("watch-modal-x").addEventListener("click", closeWatchModal);
  document.getElementById("shopping-cancel").addEventListener("click", closeShoppingModal);
  document.getElementById("shopping-modal-x").addEventListener("click", closeShoppingModal);

  // Click on the backdrop closes any modal
  document.querySelectorAll(".modal").forEach((m) => {
    m.addEventListener("click", (e) => {
      if (e.target === m) m.classList.add("hidden");
    });
  });

  document.getElementById("task-save").addEventListener("click", async () => {
    const id = document.getElementById("task-id").value;
    const payload = {
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
      if (id) await api("PATCH", "/api/tasks/" + id, payload);
      else await api("POST", "/api/tasks", payload);
      closeTaskModal();
      poll();
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

  document.getElementById("bookmark-save").addEventListener("click", async () => {
    const payload = {
      url: document.getElementById("bookmark-url").value,
      title: document.getElementById("bookmark-title").value,
      tags: document.getElementById("bookmark-tags").value,
      notes: document.getElementById("bookmark-notes").value,
    };
    try {
      await api("POST", "/api/bookmarks", payload);
      closeBookmarkModal();
      poll();
    } catch (e) { toast("Save failed: " + e.message, "error"); }
  });

  document.getElementById("bookmark-search").addEventListener("input", (e) => {
    bookmarkFilter = e.target.value;
    renderBookmarks();
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

  // Esc closes modals
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeTaskModal(); closeBookmarkModal(); closeWatchModal(); closeShoppingModal(); closePersonModal(); closePersonEditModal(); }
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
