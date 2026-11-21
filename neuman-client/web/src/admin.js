// The administration page. Same origin as the client, same session as the
// chat window: the client proxy holds the core cookie and this page only ever
// talks to `/api`. Nothing here is trusted by the server; every route re-checks
// the administrator group, and every mutation is audited.
"use strict";

const $ = (id) => document.getElementById(id);
let csrf = "";
const loaded = new Set();

// Says what went wrong, or what the page cannot show. A change that is visible where
// it was made needs no sentence about it.
function note(text, bad = false) {
  const box = $("msg");
  box.textContent = text || "";
  box.classList.toggle("bad", Boolean(bad));
}

function cell(value) {
  const td = document.createElement("td");
  if (value instanceof Node) td.append(value);
  else td.textContent = value == null ? "" : String(value);
  return td;
}

function table(headers, rows) {
  const element = document.createElement("table");
  element.className = "admin-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const name of headers) {
    const th = document.createElement("th");
    th.textContent = name;
    headRow.append(th);
  }
  head.append(headRow);
  element.append(head);
  const body = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of row) tr.append(cell(value));
    body.append(tr);
  }
  element.append(body);
  return element;
}

function button(label, action, primary = false) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = label;
  if (primary) element.className = "primary";
  element.onclick = async () => {
    element.disabled = true;
    try {
      await action();
    } catch (error) {
      note(error.message, true);
    } finally {
      element.disabled = false;
    }
  };
  return element;
}

/* A detail opens as a row of the table, directly under the row that asked for
   it, and the same press closes it. It used to land in one `<pre>` at the foot
   of the card, so on a list longer than the window a press looked like it had
   done nothing: the answer was below the scroll and away from the row it
   described. That is the transcript's rule for its own history rows — a detail
   opens in place, where the reader is looking (D64) — and it took this page a
   second round to follow it. `build` runs only when opening, so a detail that
   costs a fetch pays it on the press rather than once per row drawn. */
async function toggleRow(row, build) {
  const below = row.nextElementSibling;
  if (below && below.classList.contains("detail")) {
    below.remove();
    row.classList.remove("open");
    return false;
  }
  const cell = document.createElement("td");
  cell.colSpan = row.cells.length;
  cell.append(await build());
  const detail = document.createElement("tr");
  detail.className = "detail";
  detail.append(cell);
  row.after(detail);
  row.classList.add("open");
  return true;
}

// The whole row is the control, and its toggle is the target a keyboard can land
// on: a press there bubbles to the row, so there is one handler.
function rowToggle(label) {
  const toggle = element("button", "admin-row-toggle", label);
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  return toggle;
}

function expandable(row, toggle, build) {
  // A detail can be a fetch, and a second press inside it would find no detail
  // yet and open a second one. A flag rather than `disabled`, which `button()`
  // uses: disabling the focused toggle would throw a keyboard reader out of the
  // table.
  let opening = false;
  row.onclick = async () => {
    if (opening) return;
    opening = true;
    try {
      toggle.setAttribute("aria-expanded", String(await toggleRow(row, build)));
    } catch (error) {
      note(error.message, true);
    } finally {
      opening = false;
    }
  };
}

// `rows` are `{ label, cells, build }`: the first column's text, the rest of the
// row, and what opens under it.
function expandingTable(headers, rows) {
  const toggles = rows.map((row) => rowToggle(row.label));
  const view = table(headers, rows.map((row, index) => [toggles[index], ...row.cells]));
  view.classList.add("admin-expanding");
  rows.forEach((row, index) => {
    expandable(view.tBodies[0].rows[index], toggles[index], row.build);
  });
  return view;
}

/* The tail, not the head. A failed turn records why in its last two events, and a
   session can have hundreds of thousands — the first fifty are the opening
   message and the build line, and the reason for the failure is unreachable from
   here. `window` already reads the last N in SQL, so this asks for the same size
   page from the other end. */
async function sessionLog(session) {
  const log = await api(
    "GET",
    `/api/admin/sessions/${encodeURIComponent(session.id)}?view=events&last=50`
  );
  const pre = document.createElement("pre");
  pre.className = "admin-pre";
  pre.textContent = JSON.stringify(log, null, 2);
  return pre;
}

function replace(id, node) {
  const host = $(id);
  host.replaceChildren(node);
}

async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  if (csrf) headers["x-csrf-token"] = csrf;
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (response.status === 401) {
    showLogin("Sign in to continue.");
    throw new Error("signed out");
  }
  if (!response.ok) {
    const detail = data && data.detail ? `: ${data.detail}` : "";
    throw new Error(`${(data && data.error) || `HTTP ${response.status}`}${detail}`);
  }
  return data;
}

// What the appliance calls itself (D82), in this page's own chrome. The admin
// console is named after the thing it administers, so a renamed agent that the
// console still calls "Neuman" is the inconsistency the setting exists to
// remove — including on the sign-in screen, which is why `/api/brand` is read
// before there is a session. The mark takes the name's first letter: a square
// saying "N" beside a name reading "Acme" is the same inconsistency in smaller
// type.
function applyAgentName(name) {
  const label = (name || "").trim() || "Neuman";
  $("admin-brand-mark").textContent = label.slice(0, 1).toUpperCase();
  $("admin-brand-name").textContent = label;
  $("admin-login-title").textContent = `Manage ${label}`;
  document.title = `Manage ${label}`;
}

// Renaming in place on the wordmark. Enter commits, Escape reverts, blur commits
// — the edit the chat window's session rows open, because two text edits in one
// product should not behave differently.
//
// An empty name is not an error: the server stores the default and answers with
// it, so clearing the wordmark is how "Neuman" comes back (N175). `nameBefore`
// is the name the edit started from, and `null` means no edit is in progress.
let nameBefore = null;

function beginNameEdit() {
  const node = $("admin-brand-name");
  if (nameBefore !== null) return;
  nameBefore = node.textContent.trim();
  node.contentEditable = "plaintext-only";
  node.focus();
  const range = document.createRange();
  range.selectNodeContents(node);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

async function finishNameEdit(commit) {
  const node = $("admin-brand-name");
  if (nameBefore === null) return;
  const before = nameBefore;
  // Cleared before the save, so the blur that follows Enter cannot save twice.
  nameBefore = null;
  node.contentEditable = "false";
  const next = commit ? node.textContent.trim().replace(/\s+/g, " ") : before;
  if (next === before) {
    applyAgentName(before);
    return;
  }
  try {
    applyAgentName((await api("PUT", "/api/admin/name", { name: next })).name);
  } catch (error) {
    // What the server refused is not what the console should keep showing.
    applyAgentName(before);
    note(error.message, true);
  }
}

$("admin-brand-name").addEventListener("click", beginNameEdit);
$("admin-brand-name").addEventListener("blur", () => finishNameEdit(true));
$("admin-brand-name").addEventListener("keydown", (event) => {
  if (nameBefore !== null) {
    // Consumed by `preventDefault` alone: the page's Escape handler checks it
    // before dismissing anything, so one press does one thing.
    if (event.key === "Enter") {
      event.preventDefault();
      finishNameEdit(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      finishNameEdit(false);
    }
    return;
  }
  // Not editing: the wordmark behaves like the button it is.
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    beginNameEdit();
  }
});

function showLogin(message = "") {
  $("panel").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("login-msg").textContent = message;
}

function start(me) {
  csrf = me.csrf_token;
  // The account's theme, set in the chat window and carried by the identity
  // (D53). No `PALETTES` list here: the column's CHECK bounds the value, and
  // this page has no picker to validate for.
  document.documentElement.dataset.palette = me.interface.palette;
  $("login").classList.add("hidden");
  $("panel").classList.remove("hidden");
  $("who").textContent = me.account;
  $("who-initials").textContent = me.account.slice(0, 2);
  if (me.must_change_password) {
    showLogin("Change your temporary password in the chat window first.");
    return;
  }
  if (!me.is_admin) {
    showLogin("This account is not an administrator.");
    return;
  }
  // The hash names the section, so a reload lands where the operator was. The
  // value is checked against `loaders` rather than trusted: a stale link to a
  // section that no longer exists is Overview, not a blank page.
  const wanted = location.hash.slice(1);
  showTab(loaders[wanted] ? wanted : "overview", false);
}

async function boot() {
  try {
    try {
      applyAgentName((await api("GET", "/api/brand")).name);
    } catch {
      // Cosmetic: a name that cannot be read must not stop an operator signing in.
    }
    start(await api("GET", "/api/me"));
  } catch {
    showLogin();
  }
}

async function signIn() {
  $("login-msg").textContent = "";
  try {
    const body = await api("POST", "/api/login", {
      username: $("username").value,
      password: $("password").value,
    });
    $("password").value = "";
    start(body);
  } catch (error) {
    $("login-msg").textContent =
      error.message === "unauthenticated" ? "Incorrect account or password." : error.message;
  }
}

// -- sections ---------------------------------------------------------------

const loaders = {
  overview: loadOverview,
  accounts: loadAccounts,
  groups: loadGroups,
  roles: loadRoles,
  models: loadModels,
  providers: loadProviders,
  tools: loadTools,
  files: loadFiles,
  prompt: loadPrompt,
  sessions: loadSessions,
  usage: loadUsage,
  audit: loadAudit,
  events: loadEvents,
};

function showTab(name, remember = true) {
  for (const button of $("tabs").querySelectorAll("button")) {
    button.setAttribute("aria-current", button.dataset.tab === name ? "page" : "false");
  }
  for (const section of document.querySelectorAll(".admin-section")) {
    section.classList.toggle("hidden", section.id !== `tab-${name}`);
  }
  note("");
  // `replaceState`, not `location.hash = name`: assigning the hash fires
  // `hashchange`, which would run this a second time — fetching the section
  // twice and leaving a second overview watcher behind. The section is written
  // into the URL so a reload and a link keep it, without filling the history.
  if (remember && location.hash.slice(1) !== name) {
    history.replaceState(null, "", `#${name}`);
  }
  // A section is a page, and the last one may have been scrolled: the new one
  // starts at its own head rather than at whatever pixel the old one was at.
  window.scrollTo(0, 0);
  // Usage is the one figure here that moves while the panel is open, so every
  // visit re-reads it: a remembered total would be from the first visit and
  // would look current (D69: a number with no timestamp is a rumour).
  if (name === "usage") loaded.delete("usage");
  // The overview watches while it is showing and stops when it is not; a
  // return visit starts it again rather than showing what it last saw.
  stopOverview();
  if (name === "overview") loaded.delete("overview");
  // Membership also changes on an account's page and in its creation dialog, and
  // a roster of groups that lags them would offer a stale dialog to save over.
  if (name === "groups") loaded.delete("groups");
  load(name);
}

function load(name) {
  if (loaded.has(name)) return;
  loaded.add(name);
  loaders[name]().catch((error) => {
    loaded.delete(name);
    note(error.message, true);
  });
}

// -- overview ---------------------------------------------------------------

// A label over a value. The card is a strip of these rather than a two-column
// table, because a table's widest cell sets its floor and every one of these is
// short (N159's family: the page must not scroll sideways for a status line).
function statusItem(label, value, hint) {
  const item = document.createElement("div");
  item.className = "admin-status-item";
  if (hint) item.title = hint;

  const name = document.createElement("span");
  name.className = "admin-status-label";
  name.textContent = label;

  const content = document.createElement("span");
  content.className = "admin-status-value";
  if (value instanceof Node) content.append(value);
  else content.textContent = value == null || value === "" ? "—" : String(value);

  item.append(name, content);
  return item;
}

function chip(text, level) {
  const element = document.createElement("span");
  element.className = `admin-chip ${level}`;
  element.textContent = text;
  return element;
}

// `/nix/store/<hash>-neuman-server`. The hash is the identity and the rest is
// the same word on every appliance, so the card shows the hash and the whole
// path stays in the title.
function shortBuild(build) {
  const match = /^\/nix\/store\/([a-z0-9]+)-/.exec(build);
  return match ? match[1].slice(0, 8) : build;
}

function duration(seconds) {
  if (seconds == null) return "unknown";
  const parts = [];
  const units = [
    [86400, "d"],
    [3600, "h"],
    [60, "m"],
  ];
  let left = Math.floor(seconds);
  for (const [size, suffix] of units) {
    const count = Math.floor(left / size);
    if (count) parts.push(`${count}${suffix}`);
    left -= count * size;
    // Two units at most: "3d 4h" is a duration, "3d 4h 12m 8s" is a stopwatch.
    if (parts.length === 2) return parts.join(" ");
  }
  return parts.length ? parts.join(" ") : `${left}s`;
}

// The overview answers what an operator opens the page for: does anything need
// me, and what is going on. One request draws it all, and while the section is open
// the request is held by the server until the GPU changes (or half a minute
// passes), so the "now" card follows a load without polling.
const overviewUI = { watch: 0 };

async function loadOverview() {
  overviewUI.watch += 1;
  const watch = overviewUI.watch;
  await loadCatalogs();
  let sampled = paintOverview(await api("GET", "/api/admin/overview"));
  while (watch === overviewUI.watch) {
    try {
      const path =
        sampled === null
          ? "/api/admin/overview"
          : `/api/admin/overview?after=${encodeURIComponent(sampled)}`;
      // Without a GPU sample there is nothing to wait on, so wait here instead.
      if (sampled === null) await new Promise((resolve) => setTimeout(resolve, 15000));
      if (watch !== overviewUI.watch) return;
      const body = await api("GET", path);
      if (watch !== overviewUI.watch) return;
      sampled = paintOverview(body);
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 15000));
    }
  }
}

function stopOverview() {
  overviewUI.watch += 1;
}

function paintOverview(body) {
  paintAttention(body.attention);
  paintNow(body.now);
  paintToday(body.today);
  paintRecent(body.recent);
  const about = body.about;
  const schema =
    about.schema.applied === about.schema.expected
      ? ""
      : ` · schema ${about.schema.applied} of ${about.schema.expected}`;
  const footer = $("overview-about");
  footer.textContent =
    `build ${about.build ? shortBuild(about.build) : "not stamped"} · up ${duration(about.uptime_s)}` +
    ` · ${about.bind}:${about.port}${about.tls ? " · TLS" : ""}${schema}`;
  // The whole store path is a hover away, for a bug report.
  footer.title = about.build || "";
  return body.now.gpu && typeof body.now.gpu.sampled_at === "number" ? body.now.gpu.sampled_at : null;
}

const tabLabels = {
  accounts: "Accounts",
  roles: "Roles",
  models: "Models",
  providers: "Providers",
  events: "Events",
};

// The rail's badges are the same items the attention card lists. The page
// already knows an error is waiting on Events; the destination says so rather
// than making the operator read Overview to find out (D80). A badge host exists
// only for the sections `tabLabels` lets an item name.
function paintBadges(items) {
  const counts = new Map();
  for (const item of items) {
    if (!item.tab) continue;
    const held = counts.get(item.tab) || { count: 0, error: false };
    held.count += 1;
    held.error = held.error || item.level === "error";
    counts.set(item.tab, held);
  }
  for (const badge of $("tabs").querySelectorAll("[data-badge]")) {
    const held = counts.get(badge.dataset.badge);
    badge.classList.toggle("hidden", !held);
    badge.classList.toggle("bad", Boolean(held && held.error));
    badge.classList.toggle("warn", Boolean(held && !held.error));
    badge.textContent = held ? String(held.count) : "";
    badge.title = held
      ? `${held.count} ${held.count === 1 ? "item needs" : "items need"} attention`
      : "";
  }
}

function paintAttention(items) {
  paintBadges(items);
  const card = $("overview-attention");
  card.replaceChildren(element("h2", null, "Needs attention"));
  if (!items.length) {
    card.append(element("p", "admin-calm", "Nothing needs attention."));
    return;
  }
  const list = element("ul", "admin-attention");
  for (const item of items) {
    const row = element("li", item.level);
    // `like this` in the text is a command to type, drawn as one.
    const text = element("span", "admin-attention-text");
    item.text.split("`").forEach((part, index) => {
      text.append(index % 2 ? element("code", null, part) : document.createTextNode(part));
    });
    row.append(text);
    if (item.tab && tabLabels[item.tab]) {
      const go = button(`${tabLabels[item.tab]} →`, async () => showTab(item.tab));
      go.classList.add("admin-attention-go");
      row.append(go);
    }
    list.append(row);
  }
  card.append(list);
}

function paintNow(now) {
  const card = $("overview-now");
  card.replaceChildren(element("h2", null, "Now"));
  const lines = element("dl", "admin-facts");
  const fact = (label, value, warn = false) => {
    const term = element("dt", null, label);
    const detail = element("dd", warn ? "warn" : null, value);
    lines.append(term, detail);
  };
  fact(
    "People",
    now.active.length ? `${now.active.length} active · ${now.active.join(", ")}` : "nobody active"
  );
  fact("Clients", now.clients.length ? now.clients.join(", ") : "none connected");
  fact(
    "Turns",
    now.turns.length
      ? `${now.turns.length} running · ${[...new Set(now.turns.map((t) => t.account))].join(", ")}`
      : "none running"
  );
  if (now.awaiting.length) {
    // Someone is blocked on a person: the one line here that may be about you.
    fact(
      "Waiting",
      now.awaiting
        .map((a) => `${a.account || "a session"} (${a.calls} approval${a.calls === 1 ? "" : "s"})`)
        .join(", "),
      true
    );
  }
  card.append(lines);

  if (now.gpu) {
    const used = now.gpu.used_mb;
    const total = now.gpu.total_mb;
    const meter = element("div", "admin-meter");
    const fill = element("span");
    fill.style.width = `${Math.min(100, (used / total) * 100)}%`;
    meter.append(fill);
    meter.title = `${now.gpu.name}: ${(used / 1024).toFixed(1)} of ${(total / 1024).toFixed(1)} GiB`;
    const caption = element(
      "p",
      "dim admin-meter-caption",
      `GPU ${(used / 1024).toFixed(1)} / ${(total / 1024).toFixed(0)} GiB` +
        (now.loaded.length ? ` · ${now.loaded.join(", ")}` : " · nothing loaded")
    );
    card.append(meter, caption);
  }
}

// 1,234,567 → 1.2M: a glance, with the exact figure in the title.
function compact(count) {
  if (count >= 1e6) return `${(count / 1e6).toFixed(1)}M`;
  if (count >= 1e3) return `${Math.round(count / 1e3)}k`;
  return String(count);
}

function dollars(usd) {
  return usd < 0.01 && usd > 0 ? "<$0.01" : `$${usd.toFixed(2)}`;
}

function paintToday(today) {
  const card = $("overview-today");
  card.replaceChildren(element("h2", null, "Today"));

  // Tokens and dollars side by side: a model on this machine costs nothing per
  // token, so dollars alone would hide the local share and tokens alone would
  // hide which ones were billed.
  const headline = element("p", "admin-headline");
  const tokens = element("strong", null, `${compact(today.tokens)} tokens`);
  tokens.title = today.tokens.toLocaleString();
  headline.append(tokens);
  if (today.usd !== null) {
    headline.append(
      element("span", "dim", " · "),
      element("strong", null, `${today.usd_estimated ? "~" : ""}${dollars(today.usd)}`)
    );
  }
  headline.append(spark(
    today.days.map((day) => ({ day: day.day, settled: day.tokens })),
    Math.max(1, ...today.days.map((day) => day.tokens))
  ));
  card.append(headline);
  if (today.unpriced_tokens) {
    card.append(
      element("p", "dim", `${compact(today.unpriced_tokens)} of them on models with no price (on this machine)`)
    );
  }

  const lists = element("div", "admin-today-lists");
  if (today.accounts.length) {
    const people = element("ul", "admin-plain");
    for (const account of today.accounts) {
      people.append(element("li", null, `${account.name} · ${compact(account.tokens)}`));
    }
    lists.append(people);
  }
  if (today.models.length) {
    const models = element("ul", "admin-plain");
    for (const model of today.models) {
      const price =
        model.usd !== null ? dollars(model.usd) : model.local ? "local" : "unpriced";
      const row = document.createElement("li");
      row.append(
        model.name ? modelTag(model.name) : "earlier replies",
        ` · ${compact(model.tokens)} · ${price}`
      );
      models.append(row);
    }
    lists.append(models);
  }
  if (!today.accounts.length && !today.models.length) {
    lists.append(element("p", "dim", "Nothing used yet today."));
  }
  card.append(lists);
}

function paintRecent(recent) {
  const card = $("overview-recent");
  card.replaceChildren(element("h2", null, "Recently changed"));
  if (!recent.length) {
    card.append(element("p", "dim", "No configuration changes yet."));
    return;
  }
  const list = element("ul", "admin-plain admin-recent");
  for (const change of recent) {
    const row = element("li");
    row.append(
      element("span", null, `${change.actor || "the system"} ${change.summary}`),
      element("span", "dim", ` · ${since(change.at)}`)
    );
    row.title = new Date(change.at).toLocaleString();
    list.append(row);
  }
  card.append(list);
}

// -- accounts ---------------------------------------------------------------

// The roster is paged and searched on the server (D84). One page is 50 accounts, and
// the Today figure is a settled token count from `usage_daily` — not a share of
// a cap, because a cap is composed policy and composing it for every row would
// put fifty policy reads behind one table. The account's own page shows the cap.
const ACCOUNTS_PAGE = 50;
const USAGE_DAYS = 7;
const LIST_FIELDS = ["tools", "files_read", "files_write", "models"];
const POLICY_SWITCHES = {
  "policy-network": "network",
  "policy-ceiling": "auto_accept_max",
  "policy-mode": "auto_accept",
};
const SCALAR_FIELDS = [
  "network",
  "auto_accept",
  "auto_accept_max",
  "max_iterations",
  "tokens_daily",
];

const accountsUI = {
  query: "",
  offset: 0,
  total: 0,
  asked: 0,
  current: null,
  policy: null,
  groups: [],
  tools: [],
  models: [],
  usage: null,
  sessions: [],
  form: null,
  initial: null,
  editing: { policy: false, tools: false, limits: false },
  timer: null,
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function brandOf(name) {
  return (catalogUI.models || []).find((model) => model.name === name)?.logo;
}

function modelTag(name) {
  const tag = element("span", "admin-model", name);
  const brand = brandOf(name);
  if (brand) tag.dataset.brand = brand;
  return tag;
}

function tagToggle(name, chosen, brand) {
  const tag = element("button", "admin-model", name);
  tag.type = "button";
  if (brand) tag.dataset.brand = brand;
  tag.setAttribute("aria-pressed", String(chosen.has(name)));
  tag.onclick = () => {
    if (chosen.has(name)) chosen.delete(name);
    else chosen.add(name);
    tag.setAttribute("aria-pressed", String(chosen.has(name)));
  };
  return tag;
}

function modelToggle(name, chosen) {
  return tagToggle(name, chosen, brandOf(name));
}

// A row of buttons that holds one answer. `options` are `[value, label, on]`, where
// `on` marks the answer that wears the colour of something switched on. Pressing the
// chosen answer again clears it, and `onChange` is then told `""`.
function clearableSwitch(options, value, onChange) {
  const host = element("div", "admin-segmented");
  host.setAttribute("role", "group");
  let chosen = value;
  for (const [option, label, on] of options) {
    const choice = element("button", on ? "admin-on" : null, label);
    choice.type = "button";
    choice.setAttribute("aria-pressed", String(option === value));
    choice.onclick = () => {
      chosen = chosen === option ? "" : option;
      for (const each of host.children) {
        each.setAttribute("aria-pressed", String(each === choice && chosen !== ""));
      }
      onChange(chosen);
    };
    host.append(choice);
  }
  return host;
}

function counted(total, noun) {
  return `${total} ${noun}${total === 1 ? "" : "s"}`;
}

// A list row's first cell. A row that only answers a mouse click is unreachable
// from the keyboard, so the name is the target the keyboard can land on; a press
// there bubbles to the row. The same arrangement as `rowToggle`, without the
// expanded state.
function rowName(text) {
  const open = element("button", "admin-row-toggle admin-account-name", text);
  open.type = "button";
  const name = document.createElement("td");
  name.append(open);
  return name;
}

function emptyRow(body, columns, text) {
  const cell = element("td", "admin-empty", text);
  cell.colSpan = columns;
  const row = document.createElement("tr");
  row.append(cell);
  body.append(row);
}

// The filter for a list held whole in the page, in the roster's fuzzy manner; the
// roster's own has to reach other pages and runs on the server (D84).
function fuzzyMatch(query, texts) {
  const needle = query.toLowerCase().replace(/\s+/g, "");
  return texts.some((text) => {
    let found = 0;
    for (const letter of text.toLowerCase()) if (letter === needle[found]) found += 1;
    return found === needle.length;
  });
}

function armTip(node, text) {
  node.dataset.tip = text;
  node.tabIndex = 0;
  node.setAttribute("role", "img");
  node.setAttribute("aria-label", text);
}

function tip(text) {
  const node = element("span", "admin-tip");
  armTip(node, text);
  return node;
}

function showTip(node) {
  const box = $("tip");
  box.textContent = node.dataset.tip;
  box.classList.remove("hidden");
  const anchor = node.getBoundingClientRect();
  const size = box.getBoundingClientRect();
  const centred = anchor.left + anchor.width / 2 - size.width / 2;
  const below = anchor.bottom + 6;
  const fits = below + size.height <= window.innerHeight - 8;
  box.style.left = `${Math.max(8, Math.min(centred, window.innerWidth - size.width - 8))}px`;
  box.style.top = `${fits ? below : anchor.top - size.height - 6}px`;
}

function hideTip() {
  $("tip").classList.add("hidden");
}

function checkField(labelText, checked) {
  const label = document.createElement("label");
  label.className = "admin-check";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  label.append(input, document.createTextNode(labelText));
  return { label, input };
}

async function loadAccounts() {
  await refreshAccounts();
}

async function refreshAccounts() {
  const params = new URLSearchParams({
    limit: String(ACCOUNTS_PAGE),
    offset: String(accountsUI.offset),
  });
  if (accountsUI.query) params.set("q", accountsUI.query);
  // The list follows the keyboard, so answers overlap, and a slower answer to an
  // earlier query must not paint over the current one.
  const asked = ++accountsUI.asked;
  const data = await api("GET", `/api/admin/accounts?${params.toString()}`);
  if (asked !== accountsUI.asked) return;
  accountsUI.total = data.total;
  renderAccountRows(data.accounts);
  renderAccountPager();
}

function renderAccountPager() {
  const shown = Math.min(accountsUI.total, accountsUI.offset + ACCOUNTS_PAGE);
  const paged = accountsUI.total > ACCOUNTS_PAGE;
  $("accounts-page").textContent = paged
    ? `${accountsUI.offset + 1}–${shown} of ${accountsUI.total}`
    : counted(accountsUI.total, "account");
  $("accounts-prev").classList.toggle("hidden", !paged);
  $("accounts-next").classList.toggle("hidden", !paged);
  $("accounts-prev").disabled = accountsUI.offset === 0;
  $("accounts-next").disabled = accountsUI.offset + ACCOUNTS_PAGE >= accountsUI.total;
}

function renderAccountRows(accounts) {
  const body = $("accounts");
  body.replaceChildren();
  if (!accounts.length) {
    const cell = element("td", "admin-empty", "No account matches that search.");
    cell.colSpan = 6;
    const row = document.createElement("tr");
    row.append(cell);
    body.append(row);
    return;
  }

  for (const account of accounts) {
    const row = document.createElement("tr");
    row.onclick = () => openAccount(account).catch((error) => note(error.message, true));

    const name = rowName(account.name);
    if (account.admin) name.append(document.createTextNode(" "), chip("admin", "ok"));

    const groups = element(
      "td",
      null,
      account.groups.length ? account.groups.join(", ") : "—"
    );

    const status = document.createElement("td");
    status.append(
      element(
        "span",
        `admin-state ${account.disabled ? "off" : "on"}`,
        account.disabled ? "disabled" : "active"
      )
    );

    const today = element("td", "opt", tokens(account.settled));
    today.title = `${account.settled.toLocaleString()} tokens today`;

    const sessions = element("td", "opt", String(account.sessions));
    const seen = element("td", null, since(account.last_seen));
    if (account.last_seen) seen.title = new Date(account.last_seen).toLocaleString();

    row.append(name, groups, status, today, sessions, seen);
    body.append(row);
  }
}

async function openAccount(account) {
  accountsUI.current = account;
  $("account-list").classList.add("hidden");
  $("account-detail").classList.remove("hidden");
  note("");
  showAccountTab("overview");
  renderAccountHeader();
  window.scrollTo(0, 0);
  await refreshAccount();
}

function closeAccount() {
  accountsUI.current = null;
  $("account-detail").classList.add("hidden");
  $("account-list").classList.remove("hidden");
  note("");
  refreshAccounts().catch((error) => note(error.message, true));
}

function showAccountTab(name) {
  for (const button of $("account-tabs").querySelectorAll("button")) {
    button.setAttribute(
      "aria-current",
      button.dataset.accountTab === name ? "page" : "false"
    );
  }
  for (const section of document.querySelectorAll(".admin-account-section")) {
    section.classList.toggle("hidden", section.id !== `account-${name}`);
  }
  // Leaving an editor abandons it. The three share one form, so edits kept across
  // a switch would read as in force and go out with another tab's save. The tab
  // being edited is spared: pressing it again is not leaving.
  for (const section of ["policy", "tools", "limits"]) {
    if (section !== name && accountsUI.editing[section]) cancelEditing(section);
  }
}

function renderAccountHeader() {
  const account = accountsUI.current;
  if (!account) return;
  $("account-name").textContent = account.name;
  $("account-sub").textContent = [
    account.admin ? "administrator" : "member",
    account.groups.length ? `groups ${account.groups.join(", ")}` : "no groups",
    account.disabled ? "disabled" : "active",
    `signed in ${since(account.last_seen)}`,
  ].join(" · ");
  $("account-disable").textContent = account.disabled ? "Enable account" : "Disable account";
  // Red for the act that shuts someone out, not for undoing it.
  $("account-disable").classList.toggle("bad", !account.disabled);
  $("account-admin").textContent = account.admin
    ? "Remove administrator"
    : "Make administrator";
}

async function refreshAccount() {
  const account = accountsUI.current;
  if (!account) return;
  const name = encodeURIComponent(account.name);
  const [policy, tools, models, usage, sessions, groups] = await Promise.all([
    api("GET", `/api/admin/policy/${name}`),
    api("GET", "/api/admin/tools"),
    api("GET", "/api/admin/models"),
    api("GET", `/api/admin/usage?account=${name}&days=${USAGE_DAYS}`),
    api("GET", `/api/admin/sessions?account=${name}`),
    api("GET", "/api/admin/groups"),
  ]);
  // Membership changes from here, the Manage menu and the Groups tab, so it is
  // read back after each rather than patched into the row three ways.
  accountsUI.groups = groups.groups;
  account.groups = groups.groups
    .filter((group) => group.members.includes(account.name))
    .map((group) => group.name);
  account.admin = account.groups.includes(ADMIN_GROUP);
  accountsUI.policy = policy;
  accountsUI.tools = tools.tools;
  accountsUI.models = models.models;
  accountsUI.usage = usage.accounts[0] || null;
  accountsUI.sessions = sessions.sessions;
  // The fragment forms read the catalogs from here rather than fetching them
  // again for every dialog.
  catalogUI.tools = tools.tools;
  catalogUI.models = models.models;
  resetAccountForm();
  renderAccountHeader();
  renderAccountOverview();
  renderPolicyForm();
  renderToolMatrix();
  renderLimits();
  renderAccountUsage();
  renderAccountSessions();
  for (const section of ["policy", "tools", "limits"]) setEditing(section, false);
}

// The form starts at the effective policy. `initial` is the same snapshot: a
// field is an override when the operator moves it away from this, and a field
// left alone is carried forward from the overlay as it was written. That is
// what lets one save change one field without clearing the others.
function resetAccountForm() {
  const policy = accountsUI.policy.policy;
  accountsUI.form = {
    files_read: [...policy.files_read],
    files_write: [...policy.files_write],
    tools: [...policy.tools],
    models: [...policy.models],
    network: policy.network,
    auto_accept: policy.auto_accept,
    auto_accept_max: policy.auto_accept_max,
    max_iterations: policy.max_iterations,
    tokens_daily: policy.tokens_daily,
  };
  accountsUI.initial = clone(accountsUI.form);
}

function sourceLabel(field) {
  if (field === "auto_accept" && accountsUI.policy.provenance[field] === "overlay") {
    return "user's choice";
  }
  const source = accountsUI.policy.provenance[field] || "defaults";
  return source === "overlay" ? "override" : sourceWords(source);
}

// `role:x` and `defaults` are composition's labels; this is what they mean to the
// person reading the field.
function sourceWords(source) {
  if (source === "defaults") return "from defaults";
  return source.startsWith("role:") ? `from ${source.slice("role:".length)} role` : source;
}

function inheritedSource(field) {
  return sourceWords((accountsUI.policy.inherited_provenance || {})[field] || "defaults");
}

function itemSource(field, item) {
  const inherited = (accountsUI.policy.inherited || {})[field] || [];
  const current = accountsUI.form[field] || [];
  if (!inherited.includes(item) && current.includes(item)) return "added here";
  if (inherited.includes(item) && !current.includes(item)) return "removed here";
  if (inherited.includes(item)) return inheritedSource(field);
  return "not granted";
}

function sectionFor(field) {
  if (field === "tools") return "tools";
  if (LIST_FIELDS.includes(field)) return "policy";
  if (["network", "auto_accept", "auto_accept_max"].includes(field)) return "policy";
  return "limits";
}

function toolSource(tool, granted) {
  const inherited = new Set(accountsUI.policy.inherited.tools || []);
  const withheld = tool.needs.includes("network") && accountsUI.form.network !== "any";
  if (granted) {
    if (!inherited.has(tool.name)) return "added here";
    if (withheld) return "granted, but Web tools is off";
    return inheritedSource("tools");
  }
  if (inherited.has(tool.name)) return "removed here";
  return `not granted${tool.needs.length ? ` (${tool.needs.join(", ")})` : ""}`;
}

function renderAccountOverview() {
  const account = accountsUI.current;
  const policy = accountsUI.policy.policy;
  const usage = accountsUI.usage;
  $("account-identity").replaceChildren(
    statusItem("Account", account.name),
    statusItem("Created", account.created ? new Date(account.created).toLocaleDateString() : "—"),
    statusItem("Last seen", since(account.last_seen)),
    statusItem("Administrator", account.admin ? "yes" : "no"),
    statusItem("Today", usage ? usage.settled.toLocaleString() : "0"),
    statusItem("Daily cap", policy.tokens_daily ? tokens(policy.tokens_daily) : "none"),
    statusItem("Sessions", String(account.sessions))
  );

  renderAccountGroups();
  renderRolesInForce();
}

function conflictMessage(conflicts) {
  return conflicts
    .map(
      (conflict) =>
        `${conflict.field}: ${conflict.values
          .map((entry) => `${entry.role}=${entry.value}`)
          .join(", ")}`
    )
    .join("; ");
}

// The grant is a role. This card is the account's primary control: every role
// in force, with the path it took, because a role that arrives through a group
// is removed on the group and not by writing an override against the person.
function token(name, { level, via, open, remove, removeLabel }) {
  const pill = chip("", level);
  const label = element("button", "admin-token-name", name);
  label.type = "button";
  label.onclick = () => open().catch((error) => note(error.message, true));
  pill.append(label);
  if (via) pill.append(element("span", "admin-token-via", via));
  if (remove) {
    const cross = element("button", "admin-chip-remove", "×");
    cross.type = "button";
    cross.title = removeLabel;
    cross.setAttribute("aria-label", removeLabel);
    cross.onclick = () => remove().catch((error) => note(error.message, true));
    pill.append(cross);
  }
  return pill;
}

// A search in the page, not a dialog: adding one thing should not cover the thing
// it is being added to. While it is open the pill that opened it reads Cancel, and
// pressing elsewhere or Escape closes it too. Each opening has its own host because
// `focusout` has no `on` property (N178) and this is drawn again on every refresh.
function addToken(label, panel, picker) {
  const add = element("button", "admin-add-token", `+ ${label}`);
  add.type = "button";
  add.setAttribute("aria-expanded", "false");
  panel.classList.add("hidden");
  panel.replaceChildren();

  let close = null;
  const open = () => {
    const host = document.createElement("div");
    // Removing a focused element fires `focusout`, which would close it twice.
    close = () => {
      close = null;
      add.textContent = `+ ${label}`;
      add.setAttribute("aria-expanded", "false");
      panel.classList.add("hidden");
      panel.replaceChildren();
    };
    const { search, list } = listPicker({
      ...picker,
      choose: async (name) => {
        close();
        await picker.choose(name);
      },
      failed: (message) => note(message, true),
    });
    // A press on a row or the scrollbar keeps focus in the search; otherwise some
    // browsers report it leaving for nowhere and the row is gone before the click.
    host.addEventListener("mousedown", (event) => {
      if (event.target !== search) event.preventDefault();
    });
    host.addEventListener("focusout", (event) => {
      if (close && !host.contains(event.relatedTarget)) close();
    });
    host.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
      add.focus();
    });
    host.append(search, list);
    panel.replaceChildren(host);
    panel.classList.remove("hidden");
    add.textContent = "Cancel";
    add.setAttribute("aria-expanded", "true");
    search.focus();
  };
  // Without this a press takes focus from the search, the panel closes on the way
  // down, and the click that follows opens it again.
  add.onmousedown = (event) => event.preventDefault();
  add.onclick = () => (close ? close() : open());
  return add;
}

function renderAccountGroups() {
  const account = accountsUI.current;
  const host = $("account-groups");
  host.replaceChildren();
  if (!account.groups.length) host.append(element("span", "dim", "No groups."));
  for (const group of account.groups) {
    host.append(
      token(group, {
        open: () => groupInfoDialog(group),
        remove: () => leaveGroup(group),
        removeLabel: `Remove ${account.name} from ${group}`,
      })
    );
  }
  host.append(
    addToken("Add group", $("account-groups-add"), {
      placeholder: "Search groups…",
      empty: "No other group matches.",
      find: async (query) =>
        accountsUI.groups
          .map((group) => group.name)
          .filter((name) => !account.groups.includes(name) && fuzzyMatch(query, [name])),
      choose: joinGroup,
    })
  );
}

function roleHolding(role) {
  const entries = (accountsUI.policy.roles || []).filter((entry) => entry.name === role);
  return {
    known: entries.every((entry) => entry.known),
    direct: entries.some((entry) => entry.source === "declared"),
    groups: entries
      .filter((entry) => entry.source !== "declared")
      .map((entry) => entry.source.slice("group:".length)),
  };
}

function renderRolesInForce() {
  const account = accountsUI.current;
  const host = $("account-roles");
  host.replaceChildren();
  $("account-role-conflicts").replaceChildren();
  const roles = [...new Set((accountsUI.policy.roles || []).map((entry) => entry.name))].sort();
  if (!roles.length) {
    host.append(element("span", "dim", "No roles. This account has only the defaults."));
  }
  for (const role of roles) {
    const held = roleHolding(role);
    host.append(
      token(role, {
        level: held.known ? "ok" : "warn",
        via: held.groups.length ? `via ${held.groups.join(", ")}` : "",
        open: () => roleInfoDialog(role),
        remove: held.direct ? () => toggleDeclaredRole(role, false) : null,
        removeLabel: `Remove ${role} from ${account.name}`,
      })
    );
  }
  host.append(
    addToken("Add role", $("account-roles-add"), {
      placeholder: "Search roles…",
      empty: "No other role matches.",
      find: async (query) => {
        const baseline = await loadBaseline(true);
        return Object.keys(baseline.roles || {})
          .sort()
          .filter((name) => !roleHolding(name).direct && fuzzyMatch(query, [name]));
      },
      choose: addDeclaredRole,
    })
  );

  const conflicts = accountsUI.policy.conflicts || [];
  if (conflicts.length) {
    const warning = element("div", "admin-role-conflict");
    warning.append(chip("roles disagree", "warn"));
    warning.append(
      document.createTextNode(
        ` ${conflictMessage(conflicts)}. The alphabetically last role wins unless an override sets it; add an override or remove one role.`
      )
    );
    $("account-role-conflicts").append(warning);
  }
}

function renderSource(id, field) {
  $(id).textContent = sourceLabel(field);
}

// The sections are readings until the operator says otherwise. Edit enables the
// controls; a save or a cancel puts them back to read-only. This is the difference
// between the effective policy and an editor: the panel shows what is in force, and
// an override is a deliberate second step.
function setEditing(section, editing) {
  accountsUI.editing[section] = editing;
  const host = $(`account-${section}`);
  if (!host) return;
  for (const control of host.querySelectorAll(
    "input, select, textarea, .admin-segmented button"
  )) {
    control.disabled = !editing;
  }
  if (section === "policy") renderPolicyForm();
  const edit = $(`${section}-edit`);
  if (edit) edit.classList.toggle("hidden", editing);
  const save = $(`${section}-save`);
  if (save) save.classList.toggle("hidden", !editing);
  const clear = $(`${section}-clear`);
  if (clear) clear.classList.toggle("hidden", !editing);
  const cancel = $(`${section}-cancel`);
  if (cancel) cancel.classList.toggle("hidden", !editing);
}

// Back to the policy as saved. All three forms redraw because they share one
// form object; nothing is sent, so there is nothing to confirm.
function cancelEditing(section) {
  resetAccountForm();
  renderPolicyForm();
  renderToolMatrix();
  renderLimits();
  setEditing(section, false);
  note("");
}

// `describe` says where a path comes from, on hover, or nothing. The path adder and
// each `×` are drawn only while `editing`.
function renderPathList(host, values, { editing, describe, onChange }) {
  host.replaceChildren();
  if (!values.length && !editing) host.append(element("span", "dim", "—"));
  for (const value of values) {
    const row = element("div", "admin-path");
    row.append(element("span", "admin-path-text", value));
    const source = describe ? describe(value) : "";
    row.title = source;
    if (source === "added here") row.append(element("span", "admin-item-source", source));
    const remove = element("button", "admin-chip-remove", "×");
    remove.type = "button";
    remove.classList.toggle("hidden", !editing);
    remove.setAttribute("aria-label", `Remove ${value}`);
    remove.onclick = () => onChange(values.filter((item) => item !== value));
    row.append(remove);
    host.append(row);
  }
  host.append(pathAdder(host, editing, (value) => {
    const path = value.trim();
    onChange(path && !values.includes(path) ? [...values, path] : values);
  }));
}

// The list being added to, so the adder drawn for it next opens again after Enter.
let pathAdding = null;

// A small icon button. The label is the name a screen reader and a hover give it.
function iconButton(className, label, path) {
  const button = element("button", `admin-icon ${className}`);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
  line.setAttribute("d", path);
  svg.append(line);
  button.append(svg);
  // Keeps focus in the field, so the press is not read as pressing away first.
  button.onmousedown = (event) => event.preventDefault();
  return button;
}

// A pill that becomes a field with a tick and a cross. A path is added only on purpose:
// by the tick, or by Enter, which stays open for the next one. Escape, the cross and
// pressing away all leave without adding.
function pathAdder(list, editing, add) {
  const host = element("span", "admin-add-inline");
  const pill = element("button", "admin-add-token", "+ Add path");
  pill.type = "button";
  pill.classList.toggle("hidden", !editing);
  host.append(pill);

  let input = null;
  const save = iconButton("save", "Save path", "M5 13l4 4 10-10");
  const cancel = iconButton("cancel", "Cancel", "M6 6l12 12M18 6L6 18");
  const close = () => {
    const field = input;
    input = null;
    field.remove();
    save.remove();
    cancel.remove();
    pill.classList.remove("hidden");
  };
  const open = () => {
    input = document.createElement("input");
    input.placeholder = "/path/to/grant";
    input.setAttribute("aria-label", "Path to grant");
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        const { value } = input;
        close();
        pathAdding = list;
        add(value);
      } else if (event.key === "Escape") {
        // A list in a dialog: Escape leaves this field, not the dialog around it.
        event.preventDefault();
        close();
        pill.focus();
      }
    });
    input.addEventListener("blur", () => {
      if (input) close();
    });
    pill.classList.add("hidden");
    host.append(input, save, cancel);
    input.focus();
  };
  save.onclick = () => {
    const { value } = input;
    close();
    add(value);
  };
  cancel.onclick = close;
  pill.onclick = open;
  if (pathAdding === list) {
    pathAdding = null;
    // Focus cannot land on a field that is not in the page yet.
    setTimeout(open);
  }
  return host;
}

function renderPolicyForm() {
  renderPathList($("policy-read"), accountsUI.form.files_read, {
    editing: accountsUI.editing.policy,
    describe: (value) => itemSource("files_read", value),
    onChange: (values) => {
      accountsUI.form.files_read = values;
      renderPolicyForm();
    },
  });
  renderPathList($("policy-write"), accountsUI.form.files_write, {
    editing: accountsUI.editing.policy,
    describe: (value) => itemSource("files_write", value),
    onChange: (values) => {
      accountsUI.form.files_write = values;
      renderPolicyForm();
    },
  });
  renderSource("policy-read-source", "files_read");
  renderSource("policy-write-source", "files_write");

  for (const [id, field] of Object.entries(POLICY_SWITCHES)) {
    for (const option of $(id).querySelectorAll("button")) {
      option.setAttribute("aria-pressed", String(option.dataset.value === accountsUI.form[field]));
      option.disabled = !accountsUI.editing.policy;
    }
    renderSource(`${id}-source`, field);
  }
  renderPolicyModels();
  renderSource("policy-models-source", "models");
}

// While reading, what the account has. While editing, everything it could: a tag
// that is on wears its maker's colour, and a press turns it on or off.
function renderPolicyModels() {
  const host = $("policy-models");
  host.replaceChildren();
  const chosen = new Set(accountsUI.form.models);
  if (!accountsUI.editing.policy) {
    if (!chosen.size) host.append(element("span", "dim", "—"));
    for (const name of [...chosen].sort()) host.append(modelTag(name));
    return;
  }
  const offered = new Set([...accountsUI.models.map((model) => model.name), ...chosen]);
  for (const name of [...offered].sort()) {
    const tag = modelToggle(name, chosen);
    tag.title = itemSource("models", name);
    tag.addEventListener("click", () => {
      accountsUI.form.models = [...chosen];
      tag.title = itemSource("models", name);
    });
    host.append(tag);
  }
}

function renderToolMatrix() {
  const host = $("tool-matrix");
  host.replaceChildren();
  const granted = new Set(accountsUI.form.tools);
  const offered = accountsUI.tools.filter((tool) => !tool.diagnostic);
  for (const tool of offered) {
    const has = granted.has(tool.name);
    const source = element("div", "admin-field-source", toolSource(tool, has));
    const name = document.createElement("div");
    name.append(element("div", "admin-tool-name", tool.name), source);
    const answer = element("div", "admin-segmented");
    answer.setAttribute("role", "group");
    answer.setAttribute("aria-label", tool.name);
    for (const [label, on] of [["On", true], ["Off", false]]) {
      const option = element("button", on ? "admin-on" : null, label);
      option.type = "button";
      option.setAttribute("aria-pressed", String(on === has));
      option.disabled = !accountsUI.editing.tools;
      // In place: drawing the matrix again would lose the focus just used.
      option.onclick = () => {
        if (on) granted.add(tool.name);
        else granted.delete(tool.name);
        accountsUI.form.tools = [...granted];
        source.textContent = toolSource(tool, on);
        for (const each of answer.children) {
          each.setAttribute("aria-pressed", String(each === option));
        }
      };
      answer.append(option);
    }
    const row = element("div", "admin-tool");
    row.append(name, element("div", "admin-tool-summary", tool.summary), answer);
    host.append(row);
  }
}

function readLimitsForm() {
  const tokensRaw = $("limit-tokens").value.trim();
  accountsUI.form.tokens_daily = tokensRaw === "" ? null : Number(tokensRaw);
  accountsUI.form.max_iterations = Number($("limit-steps").value);
}

function renderLimits() {
  const form = accountsUI.form;
  $("limit-tokens").value = form.tokens_daily == null ? "" : String(form.tokens_daily);
  $("limit-steps").value = String(form.max_iterations);
  renderSource("limit-tokens-source", "tokens_daily");
  renderSource("limit-steps-source", "max_iterations");

  const usage = accountsUI.usage;
  const settled = usage ? usage.settled : 0;
  const cap = form.tokens_daily;
  const share = cap ? Math.min(1, settled / cap) : 0;
  const meter = $("limit-meter");
  meter.style.width = `${(share * 100).toFixed(1)}%`;
  meter.parentElement.classList.toggle("high", share >= 0.9);
  // No cap, no bar: an empty track against infinity implies a limit the
  // account does not have.
  meter.parentElement.classList.toggle("hidden", !cap);
  $("limit-usage").textContent = cap
    ? `${settled.toLocaleString()} of ${cap.toLocaleString()} tokens today · ` +
      `${Math.round(share * 100)}% · resets 00:00 UTC`
    : `${settled.toLocaleString()} tokens today · no daily cap`;
}

function renderAccountUsage() {
  const usage = accountsUI.usage;
  $("account-usage-strip").replaceChildren(
    statusItem("Today", usage ? usage.settled.toLocaleString() : "0"),
    statusItem("In flight", usage && usage.reserved ? usage.reserved.toLocaleString() : "—"),
    statusItem("Daily cap", usage && usage.tokens_daily ? tokens(usage.tokens_daily) : "none"),
    statusItem("Last call", usage ? since(usage.last_call) : "never")
  );
  drawUsageWeek($("account-usage-week"), usage);
}

// The top of a scale: a round number that holds `peak` without leaving the chart half
// empty, and whose half is round too, because that is the rule drawn between.
function roundedUp(peak) {
  const power = 10 ** Math.floor(Math.log10(peak));
  return [1, 2, 4, 6, 8, 10].find((step) => step * power >= peak) * power;
}

function usageDayLabel(day, options) {
  // The server's calendar is UTC, so the label has to be, or a browser west of it
  // names every bar a day early.
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, {
    ...options,
    timeZone: "UTC",
  });
}

// One scale for every bar, from zero, set by the busiest day. The cap is drawn only
// when it falls on that scale: stretching the scale to reach a distant cap would
// flatten the week into nothing, and the figure above already states it.
function drawUsageWeek(host, usage) {
  host.replaceChildren();
  const history = usage ? usage.history : [];
  const peak = Math.max(0, ...history.map((day) => day.settled));
  if (!peak) {
    host.append(element("p", "dim admin-week-empty", "No usage in the last 7 days."));
    return;
  }
  const top = roundedUp(peak);
  const cap = usage.tokens_daily;

  const plot = element("div", "admin-week-plot");
  for (const share of [1, 0.5, 0]) {
    const rule = element("div", "admin-week-rule");
    rule.style.bottom = `${share * 100}%`;
    rule.append(element("span", null, tokens(top * share)));
    plot.append(rule);
  }
  if (cap && cap <= top) {
    const rule = element("div", "admin-week-rule admin-week-cap");
    rule.style.bottom = `${(cap / top) * 100}%`;
    rule.append(element("span", null, "daily cap"));
    plot.append(rule);
  }

  const labels = element("div", "admin-week-labels");
  history.forEach((day, index) => {
    const today = index === history.length - 1;
    const bar = element("div", `admin-week-day${today ? " today" : ""}`);
    const used = day.settled.toLocaleString();
    const share = cap ? ` · ${Math.round((day.settled / cap) * 100)}% of the daily cap` : "";
    const name = usageDayLabel(day.day, { weekday: "short", month: "short", day: "numeric" });
    armTip(bar, `${name} · ${used} tokens${share}`);
    if (day.settled) {
      const fill = document.createElement("i");
      fill.style.height = `${(day.settled / top) * 100}%`;
      bar.append(fill);
    }
    plot.append(bar);
    labels.append(
      element(
        "span",
        today ? "today" : null,
        today ? "Today" : usageDayLabel(day.day, { weekday: "short" })
      )
    );
  });
  host.append(plot, labels);
}

function renderAccountSessions() {
  const body = $("account-sessions-body");
  body.replaceChildren();
  for (const session of accountsUI.sessions) {
    const toggle = rowToggle(session.title || "(untitled)");
    const name = document.createElement("td");
    name.append(toggle);
    const row = document.createElement("tr");
    row.append(
      name,
      element("td", null, (session.created_at || "").slice(0, 16).replace("T", " ")),
      element("td", null, tokens(session.tokens || 0))
    );
    expandable(row, toggle, () => sessionLog(session));
    body.append(row);
  }
  const shown = accountsUI.sessions.length;
  const total = accountsUI.current.sessions;
  $("account-sessions-note").textContent = total > shown
    ? `Most recent ${shown} of ${total} sessions.`
    : `${shown} session${shown === 1 ? "" : "s"}.`;
}

// -- saving one account's policy --------------------------------------------

function sameList(left, right) {
  if (left.length !== right.length) return false;
  const a = [...left].sort();
  const b = [...right].sort();
  return a.every((value, index) => value === b[index]);
}

// An override is the final say on a field, so a list is written whole (D87).
// Starting from the overlay as written carries forward every field the operator
// did not touch, including the user's own `auto_accept`; a field moved back to what
// the layers below give stops being an override.
function overlayFragment() {
  const loaded = accountsUI.policy;
  const form = accountsUI.form;
  const inherited = loaded.inherited;
  const fragment = clone(loaded.overlay || {});

  for (const field of LIST_FIELDS) {
    if (sameList(form[field], accountsUI.initial[field])) continue;
    if (sameList(form[field], inherited[field] || [])) delete fragment[field];
    else fragment[field] = [...form[field]];
  }

  for (const field of SCALAR_FIELDS) {
    if (form[field] === accountsUI.initial[field]) continue;
    if (form[field] === inherited[field]) delete fragment[field];
    else fragment[field] = form[field];
  }

  return fragment;
}

async function saveAccountPolicy() {
  readLimitsForm();
  if (!Number.isInteger(accountsUI.form.max_iterations) || accountsUI.form.max_iterations < 1) {
    throw new Error("Max steps per turn must be a whole number of at least 1.");
  }
  if (
    accountsUI.form.tokens_daily !== null &&
    (!Number.isInteger(accountsUI.form.tokens_daily) || accountsUI.form.tokens_daily < 1)
  ) {
    throw new Error("A daily token limit must be a whole number of at least 1, or blank.");
  }
  const name = encodeURIComponent(accountsUI.current.name);
  const fragment = overlayFragment();
  if (Object.keys(fragment).length) {
    await api("PUT", `/api/admin/policy/${name}/overlay`, fragment);
  } else if (Object.keys(accountsUI.policy.overlay).length) {
    await api("DELETE", `/api/admin/policy/${name}/overlay`);
  }
  await refreshAccount();
  note("");
}

// -- dialogs ----------------------------------------------------------------

let dialogAction = null;

function openDialog(title, body, actionLabel, action) {
  $("dialog-title").textContent = title;
  $("dialog-body").replaceChildren(...body);
  $("dialog-error").textContent = "";
  $("dialog-ok").textContent = actionLabel;
  dialogAction = action;
  $("dialog-cancel").textContent = "Cancel";
  $("dialog-delete").classList.add("hidden");
  $("dialog").classList.remove("hidden");
  const first = $("dialog-body").querySelector("input, select");
  if (first) first.focus();
}

function dialogDelete(label, action) {
  const remove = $("dialog-delete");
  remove.textContent = label;
  remove.classList.remove("hidden");
  remove.onclick = () =>
    action().catch((error) => {
      $("dialog-error").textContent = error.message;
    });
}

function closeDialog() {
  $("dialog").classList.add("hidden");
  dialogAction = null;
}

function dialogField(labelText, input) {
  const label = document.createElement("label");
  label.className = "admin-dialog-field";
  label.append(element("span", null, labelText), input);
  return label;
}

function dialogHint(text) {
  return element("p", "dim admin-hint", text);
}

function dialogLabel(text, explanation) {
  const label = element("p", "admin-dialog-label", `${text} `);
  label.append(tip(explanation));
  return label;
}

// Beside the title, for an explanation of the dialog as a whole. `openDialog`
// rewrites the title, so this follows it and the next dialog starts without one.
function dialogTip(text) {
  $("dialog-title").append(" ", tip(text));
}

async function fetchGroups() {
  const data = await api("GET", "/api/admin/groups");
  return data.groups;
}

function rolePresetField(name, fragment) {
  const label = document.createElement("label");
  label.className = "admin-preset";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.value = name;
  const body = element("span", "admin-preset-body");
  body.append(
    element("span", "admin-preset-name", name),
    element("span", "admin-preset-summary", fragmentSummary(fragment))
  );
  label.append(input, body);
  return { label, input };
}

async function newAccountDialog() {
  let groups;
  let baseline;
  let granted;
  try {
    [groups, baseline, granted] = await Promise.all([
      fetchGroups(),
      loadBaseline(true),
      api("GET", "/api/admin/grants").catch(() => ({ grants: [] })),
    ]);
  } catch (error) {
    note(error.message, true);
    return;
  }
  const groupRoles = new Map();
  for (const grant of granted.grants || []) {
    if (!groupRoles.has(grant.group)) groupRoles.set(grant.group, []);
    groupRoles.get(grant.group).push(grant.role);
  }
  const knownRoles = new Set(Object.keys(baseline.roles || {}));

  const name = document.createElement("input");
  name.placeholder = "lower-case, no spaces";
  const password = document.createElement("input");
  password.type = "password";
  password.placeholder = "at least 12 characters";
  const admin = checkField("Administrator", false);
  const mustChange = checkField("Require a password change at first sign-in", true);

  const roleChecks = Object.entries(baseline.roles || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([role, fragment]) => rolePresetField(role, fragment));
  const groupChecks = groups
    .filter((group) => group.name !== "neuman-admin")
    .map((group) => {
      const check = checkField(group.name, false);
      check.input.value = group.name;
      return check;
    });

  const chosenGroups = () =>
    groupChecks
      .filter((check) => check.input.checked)
      .map((check) => check.input.value);
  const preview = element("div", "admin-preset-preview");
  const refreshPreview = async () => {
    const direct = roleChecks
      .filter((check) => check.input.checked)
      .map((check) => check.input.value);
    const inherited = chosenGroups().flatMap((group) => groupRoles.get(group) || []);
    const roles = [...new Set([...direct, ...inherited])].filter((role) =>
      knownRoles.has(role)
    );
    preview.replaceChildren(element("p", "dim", "Composing the starting policy…"));
    try {
      const draft = await api("POST", "/api/admin/policy/preview", { roles });
      preview.replaceChildren(
        element("p", "admin-preset-effective", `Effective: ${fragmentSummary(draft.policy)}`)
      );
      if (draft.conflicts?.length) {
        const warning = element("p", "admin-role-conflict", conflictMessage(draft.conflicts));
        warning.append(
          document.createTextNode(" Roles disagree; choose one.")
        );
        preview.append(warning);
      }
    } catch (error) {
      preview.replaceChildren(element("p", "admin-role-conflict", error.message));
    }
  };

  const body = [
    dialogField("Account name", name),
    dialogField("Temporary password", password),
    dialogHint("Shown once; the person chooses their own at first sign-in."),
  ];
  if (roleChecks.length) {
    body.push(
      dialogLabel(
        "Starting roles",
        "A later change to a role reaches this account. Add overrides once it exists."
      )
    );
    const roleList = element("div", "admin-preset-grid");
    for (const check of roleChecks) {
      check.input.onchange = () => refreshPreview();
      roleList.append(check.label);
    }
    body.push(roleList);
  } else {
    body.push(dialogHint("No roles exist yet; create one on the Roles tab first."));
  }
  if (groupChecks.length) {
    body.push(element("p", "admin-dialog-label", "Groups"));
    const list = element("div", "admin-checklist");
    for (const check of groupChecks) {
      check.input.onchange = () => refreshPreview();
      list.append(check.label);
    }
    body.push(list);
  }
  body.push(preview);
  admin.label.append(tip("Administrators can open this console. This is separate from roles."));
  body.push(admin.label);
  body.push(mustChange.label);

  openDialog("New account", body, "Create account", async () => {
    const accountName = name.value.trim();
    const temporary = password.value;
    const wantsAdmin = admin.input.checked;
    const wantsChange = mustChange.input.checked;
    const chosenGroupNames = chosenGroups();
    const chosenRoles = roleChecks
      .filter((check) => check.input.checked)
      .map((check) => check.input.value);
    if (!/^[a-z_][a-z0-9_-]{0,31}$/.test(accountName)) {
      throw new Error("A name is lower-case letters, digits, underscore or hyphen.");
    }
    if (temporary.length < 12) {
      throw new Error("A temporary password is at least 12 characters.");
    }
    const created = await api("POST", "/api/admin/accounts", {
      name: accountName,
      password: temporary,
      admin: wantsAdmin,
      must_change_password: wantsChange,
      roles: chosenRoles,
      groups: chosenGroupNames,
    });
    closeDialog();
    accountsUI.query = "";
    accountsUI.offset = 0;
    $("account-search").value = "";
    await refreshAccounts();
    await openAccount({
      name: accountName,
      groups: (wantsAdmin
        ? [...chosenGroupNames, "neuman-admin"]
        : chosenGroupNames
      ).sort(),
      admin: wantsAdmin,
      disabled: false,
      created: new Date().toISOString(),
      last_seen: new Date().toISOString(),
      settled: 0,
      reserved: 0,
      sessions: 0,
      must_change_password: wantsChange,
      roles: created.roles,
    });
    note("");
  });
  refreshPreview();
}

function suggestPassword() {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(36).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}

function resetPasswordDialog() {
  const account = accountsUI.current;
  const password = document.createElement("input");
  password.type = "text";
  password.value = suggestPassword();
  openDialog(
    `Reset password for ${account.name}`,
    [
      dialogField("Temporary password", password),
      dialogHint("Shown once; the person chooses their own at first sign-in."),
    ],
    "Set password",
    async () => {
      if (password.value.length < 12) {
        throw new Error("A temporary password is at least 12 characters.");
      }
      await api("POST", `/api/admin/accounts/${encodeURIComponent(account.name)}/password`, {
        password: password.value,
        must_change_password: true,
      });
      closeDialog();
      note(`Temporary password set for ${account.name}.`);
      await refreshAccount();
    }
  );
}

async function saveDeclaredRoles(roles) {
  const account = accountsUI.current;
  await saveBaseline((draft) => {
    const users = draft.users || (draft.users = []);
    let target = users.find((user) => user.name === account.name);
    if (!target) {
      target = { name: account.name };
      users.push(target);
    }
    if (roles.length) target.roles = roles;
    else delete target.roles;
    if (!target.roles && !target.policy) {
      draft.users = users.filter((user) => user !== target);
    }
  });
}

async function toggleDeclaredRole(role, present) {
  const declared = (accountsUI.policy.roles || [])
    .filter((entry) => entry.source === "declared")
    .map((entry) => entry.name);
  const next = present
    ? [...new Set([...declared, role])]
    : declared.filter((name) => name !== role);
  await saveDeclaredRoles(next);
  loaded.delete("roles");
  await refreshAccount();
  note("");
}

async function addDeclaredRole(role) {
  const roles = (accountsUI.policy.roles || []).filter((entry) => entry.known);
  const preview = await api("POST", "/api/admin/policy/preview", {
    roles: [...new Set([...roles.map((entry) => entry.name), role])],
    overlay: accountsUI.policy.overlay || {},
  });
  if (preview.conflicts?.length) {
    throw new Error(
      `${role} disagrees with this account's roles (${conflictMessage(preview.conflicts)}). ` +
        "Remove one, or give the account an override instead."
    );
  }
  await toggleDeclaredRole(role, true);
}

async function membershipChanged() {
  loaded.delete("roles");
  await refreshAccount();
  await refreshAccounts();
}

async function joinGroup(group) {
  const account = accountsUI.current.name;
  await api("POST", `/api/admin/groups/${encodeURIComponent(group)}/members`, { account });
  await membershipChanged();
  note("");
}

async function leaveGroup(group) {
  const account = accountsUI.current.name;
  await api(
    "DELETE",
    `/api/admin/groups/${encodeURIComponent(group)}/members/${encodeURIComponent(account)}`
  );
  await membershipChanged();
  note("");
}

function chipRow(names, fallback) {
  const row = element("div", "admin-chips");
  if (!names.length) row.append(element("span", "dim", fallback));
  for (const name of names) row.append(chip(name));
  return row;
}

async function groupInfoDialog(name) {
  const account = accountsUI.current.name;
  const grants = await api("GET", "/api/admin/grants");
  const roles = grants.grants.filter((grant) => grant.group === name).map((grant) => grant.role);
  const group = accountsUI.groups.find((entry) => entry.name === name);
  openDialog(
    `Group ${name}`,
    [
      element("p", "admin-dialog-label", "Roles it gives"),
      chipRow(roles, "No roles."),
      element("p", "admin-dialog-label", counted(group.members.length, "member")),
      chipRow(group.members, "No members."),
    ],
    "Edit group",
    () => groupDialog({ ...group, roles })
  );
  $("dialog-cancel").textContent = "Close";
  dialogDelete(`Remove ${account}`, async () => {
    closeDialog();
    await leaveGroup(name);
  });
}

async function roleInfoDialog(role) {
  const account = accountsUI.current.name;
  const baseline = await loadBaseline(true);
  const held = roleHolding(role);
  const how = [];
  if (held.direct) how.push("Directly");
  for (const group of held.groups) how.push(`Through group ${group}`);
  const list = document.createElement("ul");
  list.className = "admin-held";
  for (const line of how) list.append(element("li", null, line));

  const body = [
    element("p", "admin-dialog-label", "Grants"),
    element(
      "p",
      "dim",
      held.known ? fragmentSummary(baseline.roles[role]) : "This role is not defined."
    ),
    element("p", "admin-dialog-label", `How ${account} holds it`),
    list,
  ];
  if (held.direct && held.groups.length) {
    body.push(dialogHint(`Removing the direct role leaves ${held.groups.join(", ")} giving it.`));
  } else if (held.groups.length > 1) {
    body.push(dialogHint(`${account} keeps this role until it leaves every one of these groups.`));
  }
  openDialog(
    `Role ${role}`,
    body,
    held.known ? "Edit role" : "Close",
    held.known ? () => roleDialog(role) : async () => closeDialog()
  );
  $("dialog-cancel").textContent = "Close";
  if (held.direct) {
    dialogDelete("Remove role", async () => {
      closeDialog();
      await toggleDeclaredRole(role, false);
    });
  } else if (held.groups.length === 1) {
    dialogDelete(`Remove from ${held.groups[0]}`, async () => {
      closeDialog();
      await leaveGroup(held.groups[0]);
    });
  }
}

// -- wiring -----------------------------------------------------------------

$("account-search").addEventListener("input", (event) => {
  clearTimeout(accountsUI.timer);
  accountsUI.timer = setTimeout(() => {
    accountsUI.query = event.target.value.trim();
    accountsUI.offset = 0;
    refreshAccounts().catch((error) => note(error.message, true));
  }, 80);
});
$("accounts-prev").onclick = () => {
  accountsUI.offset = Math.max(0, accountsUI.offset - ACCOUNTS_PAGE);
  refreshAccounts().catch((error) => note(error.message, true));
};
$("accounts-next").onclick = () => {
  accountsUI.offset += ACCOUNTS_PAGE;
  refreshAccounts().catch((error) => note(error.message, true));
};
$("account-back").onclick = () => closeAccount();
$("account-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-account-tab]");
  if (button) showAccountTab(button.dataset.accountTab);
});
$("new-account").onclick = () => {
  newAccountDialog().catch((error) => note(error.message, true));
};
// A disclosure rather than `role="menu"`: its items are ordinary buttons, so Tab
// walks them and leaving closes the list, without a menu's arrow-key contract.
function closeAccountMenu() {
  const menu = $("account-menu");
  if (menu.classList.contains("hidden")) return;
  // Only when focus is still inside: an item that opened a dialog has already
  // moved it, and hiding the item under focus would drop a keyboard reader.
  const holdsFocus = menu.contains(document.activeElement);
  menu.classList.add("hidden");
  $("account-manage").setAttribute("aria-expanded", "false");
  if (holdsFocus) $("account-manage").focus();
}
$("account-manage").onclick = () => {
  if (!$("account-menu").classList.contains("hidden")) return closeAccountMenu();
  $("account-menu").classList.remove("hidden");
  $("account-manage").setAttribute("aria-expanded", "true");
};
$("account-menu").onclick = closeAccountMenu;
$("account-menu-host").addEventListener("focusout", (event) => {
  if (!$("account-menu-host").contains(event.relatedTarget)) closeAccountMenu();
});
document.addEventListener("click", (event) => {
  if (!$("account-menu-host").contains(event.target)) closeAccountMenu();
});
$("account-reset").onclick = resetPasswordDialog;
$("account-disable").onclick = async () => {
  const account = accountsUI.current;
  try {
    const response = await api(
      "PATCH",
      `/api/admin/accounts/${encodeURIComponent(account.name)}`,
      { disabled: !account.disabled }
    );
    account.disabled = response.disabled;
    renderAccountHeader();
    await refreshAccounts();
    note("");
  } catch (error) {
    note(error.message, true);
  }
};
$("account-admin").onclick = async () => {
  const account = accountsUI.current;
  try {
    const response = await api(
      "PATCH",
      `/api/admin/accounts/${encodeURIComponent(account.name)}`,
      { admin: !account.admin }
    );
    await refreshAccount();
    await refreshAccounts();
    note("");
  } catch (error) {
    note(error.message, true);
  }
};
$("policy-edit").onclick = () => setEditing("policy", true);
$("tools-edit").onclick = () => setEditing("tools", true);
$("limits-edit").onclick = () => setEditing("limits", true);
$("policy-cancel").onclick = () => cancelEditing("policy");
$("tools-cancel").onclick = () => cancelEditing("tools");
$("limits-cancel").onclick = () => cancelEditing("limits");
for (const [id, field] of Object.entries(POLICY_SWITCHES)) {
  $(id).addEventListener("click", (event) => {
    const option = event.target.closest("button[data-value]");
    if (!option) return;
    accountsUI.form[field] = option.dataset.value;
    renderPolicyForm();
    renderToolMatrix();
  });
}
$("policy-save").onclick = () => saveAccountPolicy().catch((error) => note(error.message, true));
$("tools-save").onclick = () => saveAccountPolicy().catch((error) => note(error.message, true));
$("limits-save").onclick = () => saveAccountPolicy().catch((error) => note(error.message, true));
$("policy-clear").onclick = async () => {
  const account = accountsUI.current;
  if (!Object.keys(accountsUI.policy.overlay).length) {
    note("This account has no overrides to clear.");
    return;
  }
  if (!confirm(`Clear every override for ${account.name}?`)) return;
  try {
    await api("DELETE", `/api/admin/policy/${encodeURIComponent(account.name)}/overlay`);
    await refreshAccount();
    note("");
  } catch (error) {
    note(error.message, true);
  }
};
$("dialog-cancel").onclick = closeDialog;
$("dialog-ok").onclick = async () => {
  if (!dialogAction) return;
  const button = $("dialog-ok");
  button.disabled = true;
  try {
    await dialogAction();
  } catch (error) {
    $("dialog-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
$("dialog").addEventListener("click", (event) => {
  if (event.target === $("dialog")) closeDialog();
});
// Bound once, with the page, rather than when the section first loads: a click on
// the toggle before that load finished used to do nothing.
for (const option of document.querySelectorAll("#models-where button")) {
  option.addEventListener("click", () => setWhere(option.dataset.where));
}

document.addEventListener("keydown", (event) => {
  // A key a field has already claimed wins, so Escape in the name's edit reverts
  // the name rather than also dismissing a menu behind it.
  if (event.defaultPrevented || event.key !== "Escape") return;
  closeAccountMenu();
  if (!$("dialog").classList.contains("hidden")) closeDialog();
});


// -- groups -----------------------------------------------------------------

const ADMIN_GROUP = "neuman-admin";
const MEMBERS_SHOWN = 3;
const MEMBER_RESULTS = 50;
const groupsUI = { groups: [], query: "" };

async function loadGroups() {
  const [data, grants] = await Promise.all([
    api("GET", "/api/admin/groups"),
    api("GET", "/api/admin/grants"),
  ]);
  groupsUI.groups = data.groups.map((group) => ({
    ...group,
    roles: grants.grants.filter((grant) => grant.group === group.name).map((grant) => grant.role),
  }));
  renderGroups();
}

function memberSummary(members) {
  if (!members.length) return "—";
  const shown = members.slice(0, MEMBERS_SHOWN).join(", ");
  const rest = members.length - MEMBERS_SHOWN;
  return rest > 0 ? `${shown} +${rest} more` : shown;
}

function renderGroups() {
  const groups = groupsUI.groups.filter((group) =>
    fuzzyMatch(groupsUI.query, [group.name, ...group.roles, ...group.members])
  );
  $("groups-count").textContent = counted(groups.length, "group");
  const body = $("groups");
  body.replaceChildren();
  if (!groups.length) emptyRow(body, 3, "No group matches that search.");
  for (const group of groups) {
    const row = document.createElement("tr");
    row.onclick = () => groupDialog(group).catch((error) => note(error.message, true));

    const name = rowName(group.name);
    if (group.name === ADMIN_GROUP) name.append(document.createTextNode(" "), chip("built in"));

    const roles = document.createElement("td");
    if (group.roles.length) {
      for (const role of group.roles) roles.append(chip(role), document.createTextNode(" "));
    } else {
      roles.textContent = "—";
    }

    const members = element("td", "dim", memberSummary(group.members));
    members.title = group.members.join(", ");
    row.append(name, roles, members);
    body.append(row);
  }
}

// A short list under a search, there from the start so what can be chosen is in
// view before a letter is typed. `find` answers for the search box.
function listPicker({ placeholder, empty, find, choose, failed }) {
  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = placeholder;
  search.setAttribute("aria-label", placeholder);
  search.autocomplete = "off";
  const list = element("div", "admin-pick-list");

  let asked = 0;
  let timer = null;
  const refresh = async () => {
    const mine = ++asked;
    try {
      const found = await find(search.value.trim());
      if (mine !== asked) return;
      list.replaceChildren();
      if (!found.length) list.append(dialogHint(empty));
      for (const name of found) {
        const row = element("button", "admin-pick-row", name);
        row.type = "button";
        row.onclick = () => Promise.resolve(choose(name)).catch((error) => failed(error.message));
        list.append(row);
      }
    } catch (error) {
      failed(error.message);
    }
  };
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 80);
  });
  // Enter takes the closest match, and must not reach anything else in the dialog.
  search.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const first = list.querySelector("button");
    if (first) first.click();
  });
  refresh();
  return { search, list, refresh };
}

// Who is in the group, each removable where it is read, and the roster's own search
// over everyone else.
function memberPicker(members) {
  const chosen = element("div", "admin-members");
  const { search, list, refresh } = listPicker({
    placeholder: "Search accounts to add…",
    empty: "No other account matches.",
    // Members are left out here rather than by the server, so the page is asked
    // for enough to still fill the list once they are gone.
    find: async (query) => {
      const params = new URLSearchParams({ limit: String(MEMBER_RESULTS + members.size) });
      if (query) params.set("q", query);
      const data = await api("GET", `/api/admin/accounts?${params.toString()}`);
      return data.accounts.map((account) => account.name).filter((name) => !members.has(name));
    },
    choose: (name) => {
      members.add(name);
      search.value = "";
      paint();
      refresh();
      search.focus();
    },
    failed: (message) => {
      $("dialog-error").textContent = message;
    },
  });
  const paint = () => {
    chosen.replaceChildren();
    if (!members.size) chosen.append(element("span", "dim", "No members yet."));
    for (const name of [...members].sort()) {
      const remove = element("button", null, "×");
      remove.type = "button";
      remove.title = `Remove ${name}`;
      remove.setAttribute("aria-label", `Remove ${name}`);
      remove.onclick = () => {
        members.delete(name);
        paint();
        refresh();
      };
      const member = element("span", "admin-member", name);
      member.append(remove);
      chosen.append(member);
    }
  };
  paint();
  return [chosen, search, list];
}

// Revokes first, then people, then grants: a save that takes away the role a new
// one conflicts with, or the member it conflicts for, is not refused for either.
// People are added before they are removed so the last administrator can be
// replaced in one save. `held` follows each write that lands: the server refuses a
// step on its own (a role conflict names the account), and pressing Save again
// has to apply what is left rather than repeat what is done.
async function saveGroup(name, held, roles, members) {
  const path = `/api/admin/groups/${encodeURIComponent(name)}/members`;
  if (!held.exists) {
    await api("POST", "/api/admin/groups", { name });
    held.exists = true;
  }
  for (const role of [...held.roles].filter((role) => !roles.has(role))) {
    await api("DELETE", "/api/admin/grants", { group: name, role });
    held.roles.delete(role);
  }
  for (const account of [...members].filter((member) => !held.members.has(member))) {
    await api("POST", path, { account });
    held.members.add(account);
  }
  for (const account of [...held.members].filter((member) => !members.has(member))) {
    await api("DELETE", `${path}/${encodeURIComponent(account)}`);
    held.members.delete(account);
  }
  for (const role of [...roles].filter((role) => !held.roles.has(role))) {
    await api("POST", "/api/admin/grants", { group: name, role });
    held.roles.add(role);
  }
}

async function refreshGroups() {
  loaded.delete("roles");
  loaded.delete("accounts");
  await loadGroups();
  await refreshAccount();
}

async function deleteGroup(group) {
  const loses =
    group.members.length && group.roles.length
      ? ` ${group.members.length} account${group.members.length === 1 ? "" : "s"} lose the roles it gives.`
      : "";
  if (!confirm(`Delete the group ${group.name}?${loses}`)) return;
  await api("DELETE", `/api/admin/groups/${encodeURIComponent(group.name)}`);
  closeDialog();
  await refreshGroups();
  note("");
}

async function groupDialog(group = null) {
  const [baseline] = await Promise.all([loadBaseline(true), loadCatalogs()]);
  const held = {
    exists: group !== null,
    roles: new Set(group ? group.roles : []),
    members: new Set(group ? group.members : []),
  };
  const members = new Set(held.members);
  const builtIn = group !== null && group.name === ADMIN_GROUP;

  const name = document.createElement("input");
  name.autocomplete = "off";
  const roleChecks = Object.entries(baseline.roles || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([role, fragment]) => {
      const check = rolePresetField(role, fragment);
      check.input.checked = held.roles.has(role);
      return check;
    });

  const body = group ? [] : [dialogField("Group name", name)];
  if (roleChecks.length) {
    body.push(dialogLabel("Roles", "Every member of this group holds these roles."));
    const list = element("div", "admin-preset-grid");
    for (const check of roleChecks) list.append(check.label);
    body.push(list);
  } else {
    body.push(element("p", "admin-dialog-label", "Roles"));
    body.push(dialogHint("No roles exist yet; create one on the Roles tab first."));
  }
  body.push(
    builtIn
      ? dialogLabel("Members", "Accounts in this group are administrators of this appliance.")
      : element("p", "admin-dialog-label", "Members"),
    ...memberPicker(members)
  );

  openDialog(
    group ? `Group ${group.name}` : "New group",
    body,
    group ? "Save group" : "Create group",
    async () => {
      const groupName = group ? group.name : name.value.trim();
      if (!groupName) throw new Error("A group needs a name.");
      const roles = new Set(
        roleChecks.filter((check) => check.input.checked).map((check) => check.input.value)
      );
      try {
        await saveGroup(groupName, held, roles, members);
      } finally {
        // Created and then refused: the name is taken by this very group now.
        name.disabled = held.exists;
        await refreshGroups();
      }
      closeDialog();
      note("");
    }
  );

  if (group && !builtIn) dialogDelete("Delete group", () => deleteGroup(group));
}

// -- the baseline document --------------------------------------------------

// Roles, defaults, models, providers and account role assignments are sections
// of one document. It is read once per visit and written whole, with the document the
// edit was based on sent alongside it: the server refuses the write if another
// section got there first, so a stale editor cannot erase a role it never saw.
const baselineUI = { document: null, builtIn: {}, loaded: false };
const catalogUI = { tools: null, models: null };

async function loadCatalogs() {
  if (!catalogUI.tools) catalogUI.tools = (await api("GET", "/api/admin/tools")).tools;
  if (!catalogUI.models) catalogUI.models = (await api("GET", "/api/admin/models")).models;
  return catalogUI;
}

async function loadBaseline(force = false) {
  if (force || !baselineUI.loaded) {
    const data = await api("GET", "/api/admin/baseline");
    baselineUI.document = data.baseline;
    baselineUI.builtIn = data.built_in;
    baselineUI.loaded = true;
  }
  return baselineUI.document;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

async function saveBaseline(mutate) {
  const base = await loadBaseline();
  const next = clone(base);
  mutate(next);
  try {
    const data = await api("PUT", "/api/admin/baseline", { baseline: next, base });
    baselineUI.document = data.baseline;
    baselineUI.loaded = true;
    return data.baseline;
  } catch (error) {
    if (String(error.message).startsWith("baseline_changed")) {
      baselineUI.loaded = false;
      await loadBaseline(true);
    }
    throw error;
  }
}

// A form for one policy fragment. A baseline list only adds (subtracting is the
// overlay's job), so an empty list and an absent one compose alike, and a list is
// written only when it grants something: an empty one would claim provenance for
// a field it adds nothing to. A scalar overrides, so for a scalar absent is not
// empty, and every scalar has an inherit option.
function fragmentField(label, control, source, modifier = "") {
  const row = element("div", `admin-field ${modifier}`);
  row.append(element("div", "admin-field-label", label), control);
  if (source) row.append(source);
  return row;
}

const SCALAR_WORDS = {
  any: "On",
  none: "Off",
  never: "Never",
  safe: "Safe",
  yolo: "Yolo",
};

// A role or the defaults, in the shapes the Policy tab uses. A scalar is set or takes
// no stance: pressing its answer again clears it and the row greys out as not in play.
// `gives` says what happens then, and `base` is the value that says it with.
function fragmentEditor(value, { gives, base }) {
  const initial = value || {};
  const body = element("div", "admin-form");
  const fallsTo = (field) => {
    const given = base[field];
    const word = given == null ? "no cap" : SCALAR_WORDS[given] || given.toLocaleString();
    return `Not set · ${gives} ${word}`;
  };

  const out = {
    network: initial.network || "",
    auto_accept: initial.auto_accept || "",
    auto_accept_max: initial.auto_accept_max || "",
  };
  const switchRow = (label, field, options) => {
    const source = element("div", "admin-field-source");
    const control = clearableSwitch(options, out[field], (choice) => {
      out[field] = choice;
      paint();
    });
    control.setAttribute("aria-label", label);
    const row = fragmentField(label, control, source, "admin-field-switch");
    const paint = () => {
      row.classList.toggle("admin-field-unset", !out[field]);
      source.textContent = out[field] ? "" : fallsTo(field);
    };
    paint();
    return row;
  };
  const numberRow = (label, field) => {
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.placeholder = "Not set";
    input.setAttribute("aria-label", label);
    input.value = initial[field] ?? "";
    const source = element("div", "admin-field-source");
    const row = fragmentField(label, input, source, "admin-field-switch");
    const paint = () => {
      const set = input.value.trim();
      row.classList.toggle("admin-field-unset", !set);
      source.textContent = set ? "" : fallsTo(field);
    };
    input.addEventListener("input", paint);
    paint();
    return { input, row };
  };
  const pathsRow = (label, field) => {
    const list = element("div", "admin-paths");
    out[field] = [...(initial[field] || [])];
    const paint = () =>
      renderPathList(list, out[field], {
        editing: true,
        onChange: (values) => {
          out[field] = values;
          paint();
        },
      });
    paint();
    return fragmentField(label, list);
  };

  const chosenModels = new Set(initial.models || []);
  const models = element("div", "admin-chips");
  for (const model of catalogUI.models) models.append(modelToggle(model.name, chosenModels));

  // Diagnostics are dispatched by the appliance and are not capabilities, so
  // they are not offered as grants anywhere a policy is authored — the same
  // reason the chat window's chooser hides them (D33).
  const chosenTools = new Set(initial.tools || []);
  const tools = element("div", "admin-chips");
  for (const tool of catalogUI.tools.filter((item) => !item.diagnostic)) {
    const tag = tagToggle(tool.name, chosenTools);
    tag.title = tool.summary;
    tools.append(tag);
  }

  const approval = [
    ["never", "Never"],
    ["safe", "Safe"],
    ["yolo", "Yolo"],
  ];
  const steps = numberRow("Max steps per turn", "max_iterations");
  const daily = numberRow("Daily tokens", "tokens_daily");
  body.append(
    pathsRow("Files read", "files_read"),
    pathsRow("Files write", "files_write"),
    switchRow("Web tools", "network", [
      ["any", "On", true],
      ["none", "Off"],
    ]),
    fragmentField("Models", models),
    fragmentField("Tools", tools),
    switchRow("Approval ceiling", "auto_accept_max", approval),
    switchRow("Approval mode", "auto_accept", approval),
    steps.row,
    daily.row
  );

  function read() {
    const fragment = {};
    const lists = {
      tools: [...chosenTools],
      models: [...chosenModels],
      files_read: out.files_read,
      files_write: out.files_write,
    };
    for (const [field, values] of Object.entries(lists)) {
      if (values.length) fragment[field] = values;
    }
    for (const field of ["network", "auto_accept", "auto_accept_max"]) {
      if (out[field]) fragment[field] = out[field];
    }
    if (steps.input.value.trim()) fragment.max_iterations = Number(steps.input.value);
    if (daily.input.value.trim()) fragment.tokens_daily = Number(daily.input.value);
    return fragment;
  }

  return { node: body, read };
}

// A label/control row in the fragment form, with no provenance column: a role
// or defaults fragment is its own source.
function fragmentSummary(fragment) {
  if (!fragment || !Object.keys(fragment).length) return "nothing set";
  const parts = [];
  if (Array.isArray(fragment.tools)) parts.push(`${fragment.tools.length} tools`);
  if (Array.isArray(fragment.files_read)) parts.push(`${fragment.files_read.length} read`);
  if (Array.isArray(fragment.files_write)) parts.push(`${fragment.files_write.length} write`);
  if (Array.isArray(fragment.models)) parts.push(`${fragment.models.length} models`);
  if (fragment.network) parts.push(`web tools ${fragment.network === "any" ? "on" : "off"}`);
  if (fragment.auto_accept) parts.push(fragment.auto_accept);
  if (fragment.auto_accept_max) parts.push(`ceiling ${fragment.auto_accept_max}`);
  if (fragment.max_iterations) parts.push(`${fragment.max_iterations} steps`);
  if (fragment.tokens_daily) parts.push(`${fragment.tokens_daily} tokens/day`);
  return parts.join(" · ") || "nothing set";
}

// -- roles ------------------------------------------------------------------

let grantsUI = [];
const rolesUI = { baseline: null, query: "" };

async function loadRoles() {
  const [baseline, grants] = await Promise.all([
    loadBaseline(true),
    api("GET", "/api/admin/grants"),
    loadCatalogs(),
  ]);
  grantsUI = grants.grants;
  rolesUI.baseline = baseline;
  renderRoles();
}

function roleMembers(name, baseline) {
  const members = [];
  for (const user of baseline.users || []) {
    if ((user.roles || []).includes(name)) members.push(user.name);
  }
  for (const grant of grantsUI) {
    if (grant.role === name) members.push(`group ${grant.group}`);
  }
  return members;
}

// The defaults sit in the table as the layer every role stacks on: first, because
// composition starts there, and not counted, because it is not a role.
function renderRoles() {
  const baseline = rolesUI.baseline;
  const roles = Object.entries(baseline.roles || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, fragment]) => ({ name, fragment, holders: roleMembers(name, baseline) }))
    .filter((role) => fuzzyMatch(rolesUI.query, [role.name, ...role.holders]));
  $("roles-count").textContent = counted(roles.length, "role");
  const body = $("roles");
  body.replaceChildren();

  const row = (name, fragment, holders, open) => {
    const tr = document.createElement("tr");
    tr.onclick = () => open().catch((error) => note(error.message, true));
    const cells = [rowName(name), element("td", "dim", fragmentSummary(fragment))];
    cells.push(element("td", "dim", holders));
    tr.append(...cells);
    return tr;
  };

  if (fuzzyMatch(rolesUI.query, ["defaults", "everyone"])) {
    const defaults = row("Defaults", baseline.defaults, "Everyone", defaultsDialog);
    defaults.cells[0].append(document.createTextNode(" "), chip("built in"));
    body.append(defaults);
  }
  for (const role of roles) {
    const holders = role.holders.join(", ") || "—";
    body.append(row(role.name, role.fragment, holders, () => roleDialog(role.name)));
  }
  if (!body.children.length) emptyRow(body, 3, "No role matches that search.");
}

async function roleDialog(existing) {
  await loadCatalogs();
  const baseline = await loadBaseline();
  const name = document.createElement("input");
  name.placeholder = "lower-case, underscore or hyphen";
  name.value = existing || "";
  const editor = fragmentEditor(existing ? baseline.roles[existing] : {}, {
    gives: "defaults give",
    base: { ...baselineUI.builtIn, ...(baseline.defaults || {}) },
  });
  openDialog(
    existing ? `Role ${existing}` : "New role",
    [dialogField("Role name", name), editor.node],
    existing ? "Save" : "Create role",
    async () => {
      const roleName = name.value.trim();
      if (!/^[a-z_][a-z0-9_-]*$/.test(roleName)) {
        throw new Error("A role name is lower-case letters, digits, underscore or hyphen.");
      }
      if (roleName !== (existing || "") && (baseline.roles || {})[roleName]) {
        throw new Error("That role already exists.");
      }
      const fragment = editor.read();

      if (existing && roleName !== existing) {
        const saved = await api(
          "POST",
          `/api/admin/roles/${encodeURIComponent(existing)}/rename`,
          { to: roleName, base: baseline }
        );
        baselineUI.document = saved.baseline;
        baselineUI.loaded = true;
        // The grant form may have been edited in the same dialog. Write it
        // against the renamed document so the name and the grant change do not
        // race each other.
        await saveBaseline((draft) => {
          draft.roles = draft.roles || {};
          draft.roles[roleName] = fragment;
        });
        closeDialog();
        loaded.delete("roles");
        await loadRoles();
        note("");
        return;
      }

      await saveBaseline((draft) => {
        draft.roles = draft.roles || {};
        draft.roles[roleName] = fragment;
      });
      closeDialog();
      loaded.delete("roles");
      await loadRoles();
      await refreshAccount();
      note("");
    }
  );
  dialogTip(
    "A named set of permissions. A change here reaches every account that holds it. " +
      "A field that is not set takes no stance: the account gets it from Defaults, unless " +
      "another of its roles sets it. Press a chosen answer again to clear it."
  );
  if (existing) dialogDelete("Delete role", () => deleteRole(existing));
}

async function deleteRole(name) {
  const baseline = await loadBaseline();
  // Read here because this can be reached from an account's page, where the Roles
  // tab may never have loaded the grants that hold this role in use.
  grantsUI = (await api("GET", "/api/admin/grants")).grants;
  const inUse = roleMembers(name, baseline);
  if (inUse.length) {
    throw new Error(`Role ${name} is in use by ${inUse.join(", ")}; remove those first.`);
  }
  if (!confirm(`Delete the role ${name}?`)) return;
  await saveBaseline((draft) => {
    delete draft.roles[name];
  });
  closeDialog();
  loaded.delete("roles");
  await loadRoles();
  note("");
}

async function defaultsDialog() {
  await loadCatalogs();
  const baseline = await loadBaseline();
  const editor = fragmentEditor(baseline.defaults, {
    gives: "Neuman uses",
    base: baselineUI.builtIn,
  });
  openDialog("Defaults for everyone", [editor.node], "Save", async () => {
    const fragment = editor.read();
    await saveBaseline((draft) => {
      draft.defaults = fragment;
    });
    closeDialog();
    loaded.delete("roles");
    await loadRoles();
    note("");
  });
  dialogTip(
    "The starting point for every account. Roles and overrides apply on top. " +
      "A field that is not set uses Neuman's own value. Press a chosen answer again to clear it."
  );
}

// -- models and providers ---------------------------------------------------

// What Neuman ships, and which of it this appliance has (D76). Read fresh on
// each visit to either section, because the other section's tiles change what this one
// can offer: a model is only addable on a provider that is already here.
const presetsUI = { catalog: null };

async function loadPresets(force = false) {
  if (force || !presetsUI.catalog) presetsUI.catalog = await api("GET", "/api/admin/catalog");
  return presetsUI.catalog;
}

// After any write to the catalog. The response carries the document the server
// stored, so the next save is based on it rather than on a re-read; the role
// editor's model list is cached separately and has to forget too.
async function catalogChanged(data, tab) {
  if (data && data.baseline) {
    baselineUI.document = data.baseline;
    baselineUI.loaded = true;
  }
  presetsUI.catalog = null;
  catalogUI.models = null;
  loaded.delete("models");
  loaded.delete("providers");
  await (tab === "models" ? loadModels() : loadProviders());
}

// A logo from `logos/`, or the label's first letter when there is none. The
// files are the client's own, so the content security policy's `img-src
// 'self'` admits them and nothing else; a missing file is a monogram rather
// than a broken image.
// Stems with no file, remembered so a section that repaints every second and a
// half during a download asks for each missing logo once, not every time.
const missingLogos = new Set();

// Logos that are a single colour. Drawn as a mask filled with the palette's text
// colour rather than as an image, so a black mark is not lost on the dark
// palettes and needs no second file for them.
const monoLogos = new Set(["kimi", "openrouter"]);

function logo(stem, label) {
  const monogram = element("span", "admin-tile-mono", (label || "?").slice(0, 1).toUpperCase());
  if (!stem || missingLogos.has(stem)) return monogram;
  if (monoLogos.has(stem)) {
    const mark = element("span", "admin-tile-mark");
    // Set here rather than in the stylesheet, which holds only the mask's
    // geometry: the image is per tile, and the colour is the palette's.
    const image = `url("${new URL(`logos/${stem}.svg`, document.baseURI).href}")`;
    mark.style.setProperty("mask-image", image);
    mark.style.setProperty("-webkit-mask-image", image);
    return mark;
  }
  const image = document.createElement("img");
  image.src = `logos/${stem}.svg`;
  image.alt = "";
  image.onerror = () => {
    missingLogos.add(stem);
    image.replaceWith(monogram);
  };
  return image;
}

function tile({
  stem,
  mark,
  label,
  caption,
  added = false,
  disabled = false,
  badges = [],
  progress = null,
  warn = false,
  title = "",
  action,
}) {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "admin-tile";
  node.classList.toggle("added", added);
  node.classList.toggle("warn", warn);
  node.disabled = disabled;
  node.append(
    logo(stem, mark || label),
    element("span", "admin-tile-label", label),
    element("span", "admin-tile-caption", caption || "")
  );
  // What the model can do, on the tile, so the filter above it is never the
  // only way to find out.
  if (badges.length) {
    const row = element("span", "admin-tile-badges");
    for (const badge of badges) row.append(element("span", "admin-chip", badge));
    node.append(row);
  }
  // A download's progress, as a bar across the foot of the tile.
  if (progress !== null) {
    const bar = element("span", "admin-tile-progress");
    const fill = element("span");
    fill.style.width = `${Math.round(progress * 100)}%`;
    bar.append(fill);
    node.append(bar);
  }
  // The caption is one line on a square tile; the whole of it is a hover away.
  node.title = title || (caption ? `${label}: ${caption}` : label);
  node.onclick = () => action().catch((error) => note(error.message, true));
  return node;
}

// One tile stands for one preset, and a preset may be added under more than one
// name. One entry names itself, because that name is the string people pick in
// the composer; several say how many there are, since the tile can only open
// one of them and the panel is what chooses between them.
function addedCaption(names) {
  if (!names.length) return "";
  return names.length === 1 ? names[0] : `${names.length} entries`;
}

// Every catalog entry made from `model`'s preset, of the same kind: a preset may
// be added on a provider and on this machine both, and the tile under one half
// must not offer the entries of the other.
function presetEntries(baseline, model) {
  return Object.entries(baseline.models || {})
    .filter(
      ([, entry]) =>
        entry.preset === model.preset && ("local" in entry) === ("local" in model)
    )
    .map(([name]) => name)
    .sort();
}

// A panel showing one entry of a preset that has several. Moving the choice
// reopens the panel on the entry chosen rather than swapping the fields under
// the operator's hands: the fields are a different model's.
function entryChooser(names, current, open) {
  const choice = choiceField(
    names.map((name) => [name, name]),
    current
  );
  choice.onchange = () => {
    closeDialog();
    open(choice.value).catch((error) => note(error.message, true));
  };
  return dialogField("Entry", choice);
}

// The way to a second entry of one preset — the same weights or the same route
// under another name, with settings of its own. A sentence rather than a button
// in the dialog's foot, which already holds the save, the cancel and the delete.
function secondEntryRow(open) {
  const row = element("p", "admin-hint");
  const press = element("button", "admin-link", "Add a second entry");
  press.type = "button";
  press.onclick = () => {
    closeDialog();
    open().catch((error) => note(error.message, true));
  };
  row.append(
    press,
    document.createTextNode(" — the same model under another name, with settings of its own.")
  );
  return row;
}

function keyInput() {
  const input = document.createElement("input");
  input.type = "password";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = "paste the key";
  return input;
}

// Where to get a key, as text with a link beside it: the page is the client's,
// and a person may be reading it on a machine where the link does not open.
function keyHint(url) {
  const hint = element("p", "dim admin-hint", "Create a key at ");
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = url;
  hint.append(link, ".");
  return hint;
}

// -- providers ---------------------------------------------------------------

/* A provider is a tile (D76). Choosing one Neuman ships asks for its key; one
   already added opens the panel that says where it points, whether a key is
   filed and which models it serves. A provider declared by hand has no preset to
   draw a logo from, so its name is the tile: it exists nowhere else on this
   page, and a document an operator cannot see is one they cannot check. */
async function loadProviders() {
  const [baseline, presets] = await Promise.all([loadBaseline(true), loadPresets(true)]);

  const tiles = [];
  for (const preset of presets.providers) {
    const added = preset.added || [];
    const names = added.map((entry) => entry.name);
    const keyless = added.some((entry) => !entry.key);
    tiles.push(
      tile({
        stem: preset.logo,
        label: preset.label,
        caption: !added.length ? "add" : keyless ? "needs a key" : addedCaption(names),
        added: added.length > 0,
        warn: added.length > 0 && keyless,
        title: added.length ? `Added as ${names.join(", ")}` : "",
        action: () => (added.length ? providerPanel(names[0]) : providerPresetDialog(preset)),
      })
    );
  }
  for (const [name, provider] of Object.entries(baseline.providers || {})) {
    if (provider.preset) continue;
    tiles.push(
      tile({
        mark: name,
        label: name,
        caption: "by URL",
        added: true,
        title: provider.url || "",
        action: () => providerPanel(name),
      })
    );
  }
  tiles.push(
    tile({ mark: "+", label: "Custom", caption: "by URL", action: () => customProviderDialog() })
  );
  $("provider-tiles").replaceChildren(...tiles);
}


// -- tools --------------------------------------------------------------------
//
// A tool in no package is always present; a package is a set of tools the release
// ships and an operator turns on for the whole appliance (D101). Enabling one
// grants it to nobody — a role does that — and downloads nothing: what it needs
// on a person's machine, they install, and the dialog says what.

// One line per tool, with what decides its exposure: the reach it needs,
// whether it asks under `safe`, and whether it returns a picture.
function toolRow(tool) {
  const row = element("div", "admin-tool-row");
  const head = element("div", "admin-tool-head");
  head.append(element("code", null, tool.name));
  const chips = element("span", "admin-tile-badges");
  for (const need of tool.needs) chips.append(element("span", "admin-chip", need));
  if (tool.mutating || tool.critical) {
    chips.append(element("span", "admin-chip", tool.critical ? "always asks" : "asks"));
  }
  if (tool.returns_image) chips.append(element("span", "admin-chip", "needs vision"));
  head.append(chips);
  row.append(head, element("div", "dim", tool.summary));
  row.append(
    element(
      "div",
      "dim admin-hint",
      tool.granted_by.length ? `Granted by ${tool.granted_by.join(", ")}` : "Granted to no role"
    )
  );
  return row;
}

async function loadTools() {
  const data = await api("GET", "/api/admin/tool-packages");
  $("package-tiles").replaceChildren(
    ...data.packages.map((pkg) =>
      tile({
        mark: pkg.title,
        label: pkg.title,
        caption: pkg.enabled ? "enabled" : "enable",
        added: pkg.enabled,
        badges: pkg.publisher === "community" ? ["community"] : [],
        title: pkg.summary,
        action: async () => packageDialog(pkg),
      })
    )
  );
  $("builtin-tools").replaceChildren(...data.builtin.map(toolRow));
}

function packageDialog(pkg) {
  const body = [element("p", null, pkg.summary), dialogHint(pkg.description)];
  if (pkg.requires) {
    const needs = element("p", "admin-hint");
    const link = element("a", null, pkg.requires.url);
    link.href = pkg.requires.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    needs.append(
      `Each person using it needs ${pkg.requires.name} installed on their own machine; ` +
        "Neuman does not install it. Get it from ",
      link
    );
    body.push(needs);
  }
  body.push(element("p", "admin-dialog-label", "Tools"), ...pkg.tools.map(toolRow));
  openDialog(pkg.title, body, pkg.enabled ? "Disable" : "Enable", async () => {
    await api("PUT", `/api/admin/tool-packages/${encodeURIComponent(pkg.id)}`, {
      enabled: !pkg.enabled,
    });
    closeDialog();
    // The role editor's list of grantable tools is the installed set, which
    // this just changed.
    catalogUI.tools = null;
    baselineUI.loaded = false;
    loaded.delete("roles");
    await loadTools();
    note(
      pkg.enabled
        ? `${pkg.title} disabled: its tools are withdrawn from everyone at their next step.`
        : `${pkg.title} enabled. Add its tools to a role to let people use them.`
    );
  });
}

async function providerPresetDialog(preset) {
  const another = preset.added.length > 0;
  const name = document.createElement("input");
  name.placeholder = "a name for this account";
  name.value = another ? `${preset.id}-2` : preset.id;
  const key = keyInput();

  const body = [];
  if (preset.notes) body.push(dialogHint(preset.notes));
  // The ordinary case is one account per provider, named after it. A second
  // account at the same provider needs a name of its own, so the field is drawn
  // only then.
  if (another) body.push(dialogField("Name", name));
  body.push(dialogField("API key", key));
  if (preset.key_url) body.push(keyHint(preset.key_url));

  openDialog(
    another ? `Add another ${preset.label} account` : `Add ${preset.label}`,
    body,
    "Add provider",
    async () => {
      const data = await api("POST", "/api/admin/providers", {
        preset: preset.id,
        name: name.value.trim(),
        api_key: key.value,
      });
      closeDialog();
      await catalogChanged(data, "providers");
      note(`${preset.label} added. Its models are on the Models tab.`);
    }
  );
}

/* One provider, opened from its tile: where it points, whether a key is filed,
   and what it serves. The key is replaced here rather than in a dialog of its
   own, because this is where an operator can see which provider they are
   replacing it for; the server decides where the file lives (D77), and no
   response carries the old one back. */
async function providerPanel(name) {
  const baseline = await loadBaseline();
  const provider = (baseline.providers || {})[name];
  if (!provider) {
    note(`${name} is no longer a provider.`, true);
    return;
  }
  const siblings = Object.entries(baseline.providers || {})
    .filter(([, entry]) => provider.preset && entry.preset === provider.preset)
    .map(([other]) => other)
    .sort();
  const served = Object.keys(baseline.models || {})
    .filter((model) => baseline.models[model].provider === name)
    .sort();

  const endpoint = document.createElement("input");
  endpoint.value = provider.url || "";
  endpoint.disabled = Boolean(provider.preset);
  const key = keyInput();
  key.placeholder = provider.api_key_file ? "paste a new key to replace it" : "paste the key";

  const body = [];
  if (siblings.length > 1) body.push(entryChooser(siblings, name, providerPanel));
  body.push(
    dialogField("Endpoint", endpoint),
    dialogField("API key", key),
    dialogHint(
      provider.api_key_file
        ? "A key is filed where only the server can read it. It is never shown again; a key pasted here replaces it."
        : "No key is filed. A provider on this machine may not need one."
    ),
    dialogHint(served.length ? `In use by ${served.join(", ")}.` : "Used by nothing yet.")
  );
  if (served.length) {
    body.push(
      dialogHint(
        "A provider something still uses is not deleted: remove those models first."
      )
    );
  }

  openDialog(`Provider ${name}`, body, "Save", async () => {
    if (key.value.trim()) {
      const data = await api("PUT", `/api/admin/providers/${encodeURIComponent(name)}/key`, {
        api_key: key.value.trim(),
      });
      baselineUI.document = data.baseline;
      baselineUI.loaded = true;
    }
    if (!provider.preset && endpoint.value.trim() !== (provider.url || "")) {
      await saveBaseline((draft) => {
        draft.providers[name].url = endpoint.value.trim();
      });
    }
    closeDialog();
    await catalogChanged(null, "providers");
    note("");
  });
  if (!served.length) {
    dialogDelete("Delete provider", async () => {
      if (!confirm(`Delete the provider ${name}? Its key is deleted with it.`)) return;
      const data = {
        baseline: await saveBaseline((draft) => {
          delete draft.providers[name];
        }),
      };
      closeDialog();
      await catalogChanged(data, "providers");
      note("");
    });
  }
}

// A provider Neuman does not ship: any OpenAI-compatible endpoint, https or on
// this machine. What it serves is declared on the Models tab, and none of it is
// checked until a turn spends tokens on it.
async function customProviderDialog() {
  const name = document.createElement("input");
  name.placeholder = "provider name";
  const url = document.createElement("input");
  url.placeholder = "https://…/v1 or http://127.0.0.1:8081/v1";
  const key = keyInput();
  key.placeholder = "blank for a provider on this machine";

  openDialog(
    "Add a custom provider",
    [dialogField("Name", name), dialogField("Endpoint", url), dialogField("API key", key)],
    "Add provider",
    async () => {
      const request = { name: name.value.trim(), url: url.value.trim() };
      if (key.value.trim()) request.api_key = key.value;
      const data = await api("POST", "/api/admin/providers", request);
      closeDialog();
      await catalogChanged(data, "providers");
      note("");
    }
  );
  dialogTip(
    "Neuman can't check a custom provider's models. What you enter about them is taken as given."
  );
}

// -- models ------------------------------------------------------------------

// Which half of the catalog the section shows: models at a provider, or models on
// this machine's GPU. Per viewer, and a convenience: losing it costs one click.
// This machine is the default because it is the half that has something to offer
// before a key has been entered — a model the appliance can run on its own.
const modelsUI = { where: rememberedWhere(), filters: new Set(), poll: null };

function rememberedWhere() {
  try {
    return localStorage.getItem("neuman.admin.models.where") === "cloud" ? "cloud" : "local";
  } catch {
    return "local";
  }
}

function setWhere(where) {
  modelsUI.where = where;
  try {
    localStorage.setItem("neuman.admin.models.where", where);
  } catch {
    /* A private window; the tab still works, it just forgets. */
  }
  loadModels().catch((error) => note(error.message, true));
}

// What a preset can do, from whichever route this view shows.
function capabilities(preset, where) {
  if (where === "local") {
    const local = preset.local || {};
    return { thinks: Boolean(local.reasoning), sees: Boolean(local.vision) };
  }
  return {
    thinks: preset.routes.some((route) => route.reasoning),
    sees: preset.routes.some((route) => route.vision),
  };
}

function badgesOf(caps) {
  return [...(caps.thinks ? ["thinks"] : []), ...(caps.sees ? ["sees"] : [])];
}

// Thinking and vision are filters rather than sections: a model can be both,
// and a section each would draw it twice.
function paintFilters() {
  const chips = [
    ["thinks", "Thinking"],
    ["sees", "Vision"],
  ].map(([key, label]) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "admin-chip admin-filter";
    chip.textContent = label;
    chip.setAttribute("aria-pressed", String(modelsUI.filters.has(key)));
    chip.onclick = () => {
      if (modelsUI.filters.has(key)) modelsUI.filters.delete(key);
      else modelsUI.filters.add(key);
      loadModels().catch((error) => note(error.message, true));
    };
    return chip;
  });
  $("model-filters").replaceChildren(...chips);
}

function passesFilters(caps) {
  return [...modelsUI.filters].every((key) => caps[key]);
}

/* The catalog, as tiles. A tile is the whole object: it adds a model that is not
   here, downloads weights that are not on disk, or opens the panel of one that
   is. There is no list beside it to say which are added, because the tile that
   added one already knows — its caption is the name it was added under, and its
   press is the settings. */
async function loadModels() {
  const [baseline, presets] = await Promise.all([loadBaseline(true), loadPresets(true)]);
  const providerLabel = Object.fromEntries(presets.providers.map((p) => [p.id, p.label]));
  const where = modelsUI.where;

  for (const option of document.querySelectorAll("#models-where button")) {
    option.setAttribute("aria-pressed", String(option.dataset.where === where));
  }
  paintFilters();

  const tiles = [];
  for (const preset of presets.models) {
    const caps = capabilities(preset, where);
    if (!passesFilters(caps)) continue;
    if (where === "local") {
      if (preset.local) tiles.push(localTile(preset, caps));
      continue;
    }
    if (!preset.routes.length) continue;
    const options = preset.routes.flatMap((route) =>
      route.available.map((provider) => ({ route, provider }))
    );
    const needs = preset.routes.map((route) => providerLabel[route.provider] || route.provider);
    tiles.push(
      tile({
        stem: preset.logo,
        label: preset.label,
        caption: options.length
          ? addedCaption(preset.added) || "add"
          : `needs ${needs.join(" or ")}`,
        added: preset.added.length > 0,
        disabled: !options.length,
        badges: badgesOf(caps),
        title: preset.added.length ? `Added as ${preset.added.join(", ")}` : "",
        action: () =>
          preset.added.length
            ? modelPanel(preset.added[0])
            : modelPresetDialog(preset, options, providerLabel),
      })
    );
  }
  if (where === "cloud") {
    // A model declared by hand has no preset to draw a logo or a price from, so
    // its name is the tile — otherwise the only way back to it would be the
    // document, and an entry an operator cannot reach is one they cannot fix.
    for (const [name, model] of Object.entries(baseline.models || {})) {
      if (model.preset || "local" in model) continue;
      const caps = { thinks: Boolean(model.reasoning), sees: Boolean(model.vision) };
      if (!passesFilters(caps)) continue;
      tiles.push(
        tile({
          mark: name,
          label: name,
          caption: model.provider || "by hand",
          added: true,
          badges: badgesOf(caps),
          title: model.provider_model || "",
          action: () => modelPanel(name),
        })
      );
    }
    tiles.push(
      tile({ mark: "+", label: "Custom", caption: "by hand", action: () => customModelDialog() })
    );
  }
  $("model-tiles").replaceChildren(
    ...(tiles.length
      ? tiles
      : [element("p", "dim admin-empty", "No model matches these filters.")])
  );

  paintUngranted(baseline);

  // While anything downloads, look again: the tile is the progress bar.
  clearTimeout(modelsUI.poll);
  const busy = presets.models.some((preset) => fetchBusy(preset.local));
  if (busy) {
    modelsUI.poll = setTimeout(() => {
      if (!$("tab-models").classList.contains("hidden")) {
        loadModels().catch((error) => note(error.message, true));
      }
    }, 1500);
  }
}

// A model nobody may choose is the commonest surprise after adding one, so say
// which, and where the grant is made.
function paintUngranted(baseline) {
  const granted = new Set([
    ...((baseline.defaults || {}).models || []),
    ...Object.values(baseline.roles || {}).flatMap((role) => role.models || []),
  ]);
  const ungranted = Object.keys(baseline.models || {})
    .filter((name) => !granted.has(name))
    .sort();
  $("models-ungranted").textContent = ungranted.length
    ? `Not granted by any role yet: ${ungranted.join(", ")}. Grant them on the Roles tab.`
    : "";
}

// -- models on this machine ---------------------------------------------------

function fetchBusy(local) {
  const job = local && local.fetch;
  return Boolean(job && (job.state === "fetching" || job.state === "verifying"));
}

function gigabytes(bytes) {
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

function gibibytes(mib) {
  return `${(mib / 1024).toFixed(mib >= 10240 ? 0 : 1)} GiB`;
}

function fitText(local) {
  if (local.fits === true) return `fits ${gibibytes(local.gpu_mib)}`;
  if (local.fits === false) return `needs ${gibibytes(local.vram_mib)}`;
  return "";
}

// One tile for a model on this machine, in whichever state its weights and its
// use are: the caption says what is true and the click does the next thing.
function localTile(preset, caps) {
  const local = preset.local;
  const job = local.fetch;
  const weights = local.weights.state;
  const base = { stem: preset.logo, label: preset.label, badges: badgesOf(caps) };

  if (fetchBusy(local)) {
    const fraction = job.total_bytes ? job.done_bytes / job.total_bytes : 0;
    return tile({
      ...base,
      caption: `${job.state === "verifying" ? "checking" : "downloading"} ${Math.floor(fraction * 100)}%`,
      progress: fraction,
      title: `${preset.label}: ${gigabytes(job.done_bytes)} of ${gigabytes(job.total_bytes)}. Click to stop.`,
      action: async () => {
        if (!confirm(`Stop downloading ${preset.label}? What has arrived is kept for a resume.`)) return;
        await api("POST", `/api/admin/weights/${encodeURIComponent(preset.id)}/cancel`, {});
        await loadModels();
      },
    });
  }

  if (weights === "verified") {
    const added = local.added || [];
    return tile({
      ...base,
      caption: addedCaption(added) || "add",
      added: added.length > 0,
      title: added.length ? `${preset.label}: added as ${added.join(", ")}` : "",
      action: () => (added.length ? modelPanel(added[0]) : addLocalDialog(preset)),
    });
  }

  if (weights === "present") {
    return tile({
      ...base,
      caption: "on disk · check it",
      title: `${preset.label}: the files are here and the right size. Click to check them against the hash Neuman ships, which reads them once.`,
      action: () => startFetch(preset, `Checking ${preset.label}'s files.`),
    });
  }

  if (weights === "wrong" || weights === "unreadable") {
    return tile({
      ...base,
      caption: weights === "wrong" ? "another file is in the way" : "models directory unreadable",
      warn: true,
      action: () => blockedDialog(preset),
    });
  }

  // Absent, partly downloaded, cancelled or failed: the next thing is a download.
  const failed = job && job.state === "failed";
  return tile({
    ...base,
    caption: failed
      ? "failed · retry"
      : [gigabytes(local.weights.bytes), fitText(local)].filter(Boolean).join(" · "),
    warn: failed || local.fits === false,
    title: failed ? `${preset.label}: ${job.error}` : "",
    action: () => downloadDialog(preset),
  });
}

async function startFetch(preset, message) {
  await api("POST", `/api/admin/weights/${encodeURIComponent(preset.id)}/fetch`, {});
  note(message);
  await loadModels();
}

function localSummary(preset) {
  const local = preset.local;
  const lines = [
    `${local.context_tokens.toLocaleString()} token window`,
    local.vision ? "accepts images" : "text only",
  ];
  if (local.reasoning) lines.push(`reasoning: ${local.reasoning.levels.join(", ")}`);
  lines.push(`${gigabytes(local.weights.bytes)} download`);
  if (local.vram_mib) {
    lines.push(
      local.gpu_mib
        ? `needs ${gibibytes(local.vram_mib)} of this GPU's ${gibibytes(local.gpu_mib)}`
        : `needs ${gibibytes(local.vram_mib)} of GPU memory`
    );
  }
  const summary = element("div", "admin-preset-preview");
  summary.append(element("p", null, lines.join(" · ")));
  if (local.notes) summary.append(element("p", "dim", local.notes));
  summary.append(
    element(
      "p",
      "dim admin-preset-effective",
      `${local.repo} at ${local.revision.slice(0, 10)}. Verified ${local.verified}.`
    )
  );
  return summary;
}

async function downloadDialog(preset) {
  const local = preset.local;
  const resume = local.weights.state === "partial";
  const body = [];
  if (preset.notes) body.push(dialogHint(preset.notes));
  body.push(localSummary(preset));
  if (local.fits === false) {
    // A proposal, not a refusal: the measurement is at the preset's starting
    // window, and a smaller one fits a smaller card.
    body.push(
      dialogHint(
        `Measured at ${gibibytes(local.vram_mib)} with its starting settings, and ` +
          `this GPU has ${gibibytes(local.gpu_mib)}. It will download, and it may not ` +
          "load until its window is lowered."
      )
    );
  }
  if (local.fetch && local.fetch.state === "failed") {
    body.push(dialogHint(`The last attempt failed: ${local.fetch.error}`));
  }
  openDialog(
    `${resume ? "Resume" : "Download"} ${preset.label}`,
    body,
    resume ? "Resume download" : "Download",
    async () => {
      closeDialog();
      await startFetch(preset, `Downloading ${preset.label}.`);
    }
  );
}

async function blockedDialog(preset) {
  const files = preset.local.weights.files
    .filter((file) => file.state === "wrong" || file.state === "unreadable")
    .map((file) => file.name);
  const body = [
    dialogHint(
      preset.local.weights.state === "wrong"
        ? `A file is in the models directory under a name ${preset.label} uses, and it ` +
            `is not the file Neuman ships: ${files.join(", ")}. Neuman does not overwrite ` +
            "it, because it is somebody's. Move it aside, then download."
        : "The server cannot read the models directory. Check that it exists and " +
            "belongs to neuman."
    ),
  ];
  openDialog(preset.label, body, "Close", async () => closeDialog());
}

async function addLocalDialog(preset) {
  const local = preset.local;
  const name = document.createElement("input");
  const baseline = baselineUI.document || {};
  const taken = (candidate) => Boolean((baseline.models || {})[candidate]);
  name.value = taken(local.name) ? `${local.name}-2` : local.name;
  const body = [];
  if (local.added.length) {
    body.push(
      dialogHint(
        `Already added as ${local.added.join(", ")}. A second entry is the same ` +
          "weights under another name, with settings of its own."
      )
    );
  }
  body.push(localSummary(preset), dialogField("Name in the composer", name));
  openDialog(`Add ${preset.label}`, body, "Add model", async () => {
    const data = await api("POST", "/api/admin/models/local", {
      preset: preset.id,
      name: name.value.trim(),
    });
    closeDialog();
    await catalogChanged(data, "models");
    note(`${name.value.trim()} added. Grant it to a role so someone can choose it.`);
  });
}

// After the last use of a preset goes, its weights are gigabytes nothing needs.
// Offered rather than done: downloading them again is the expensive direction.
async function offerToDeleteWeights(preset, baseline) {
  if (!preset || !preset.local) return;
  const stillUsed = Object.values(baseline.models || {}).some(
    (model) => "local" in model && model.preset === preset.id
  );
  if (stillUsed) return;
  if (!confirm(`Also delete ${preset.label}'s weights? That frees ${gigabytes(preset.local.weights.bytes)}.`)) return;
  await api("POST", `/api/admin/weights/${encodeURIComponent(preset.id)}/delete`, {});
  note(`${preset.label}'s weights deleted.`);
  await loadModels();
}

// -- a model's settings -------------------------------------------------------

/* The two controls every settings panel is built from. Their bounds are the
   runtime's own (`runtime/plan.py`): a value it would refuse is not one the form
   invites, so a save cannot fail on a number the page offered. */
function numberField(value, { min, max, step = "1", placeholder = "" } = {}) {
  const input = document.createElement("input");
  input.type = "number";
  input.step = step;
  if (min !== undefined) input.min = String(min);
  if (max !== undefined) input.max = String(max);
  if (placeholder) input.placeholder = placeholder;
  input.value = value ?? "";
  return input;
}

function textField(value, placeholder = "") {
  const input = document.createElement("input");
  input.placeholder = placeholder;
  input.value = value || "";
  return input;
}

function choiceField(choices, value) {
  const input = document.createElement("select");
  for (const [key, label] of choices) input.append(new Option(label, key));
  input.value = value;
  return input;
}

const RESIDENCY = [
  ["on-demand", "Load when chosen, unload when idle"],
  ["always", "Keep loaded"],
];

const SPECULATION = [
  ["none", "Off"],
  ["draft-mtp", "The model's own draft head (faster, costs VRAM)"],
];

// Two answers, not llama.cpp's nine: any other type, or K and V that differ,
// has no flash-attention kernel on this build and puts attention on the CPU
// without a word (N100).
const KV_CACHE = [
  ["f16", "Full precision (fastest)"],
  ["q8_0", "Compact: about half the memory, a little slower when the window is full"],
];

// The reasoning default is the entry's, not the preset's: the dialect and the
// levels a model can be asked for are facts, and which of them a turn starts at
// is a choice.
function reasoningField(model) {
  const levels = (model.reasoning || {}).levels;
  if (!levels || !levels.length) return null;
  return choiceField(
    levels.map((level) => [level, level]),
    model.reasoning.default || levels[0]
  );
}

// What a preset fixes about a model on this machine, drawn rather than offered:
// the window is in the fields below, because the entry's window is the
// operator's and the preset's is only where it starts.
function localFacts(preset, model) {
  const box = element("div", "admin-preset-preview");
  const local = preset && preset.local;
  if (!local) {
    box.append(element("p", "dim", "Its preset is one Neuman no longer ships."));
    return box;
  }
  const facts = [model.vision ? "accepts images" : "text only"];
  if (model.reasoning) facts.push(`reasoning: ${model.reasoning.levels.join(", ")}`);
  box.append(element("p", null, facts.join(" · ")));
  const size = [gigabytes(local.weights.bytes)];
  if (local.vram_mib) {
    size.push(
      local.gpu_mib
        ? `needs ${gibibytes(local.vram_mib)} of this GPU's ${gibibytes(local.gpu_mib)}`
        : `needs ${gibibytes(local.vram_mib)} of GPU memory`
    );
  }
  box.append(element("p", "dim", size.join(" · ")));
  if (local.notes) box.append(element("p", "dim", local.notes));
  box.append(
    element(
      "p",
      "dim admin-preset-effective",
      `${local.repo} at ${local.revision.slice(0, 10)}. Verified ${local.verified}.`
    )
  );
  return box;
}

// What a route fixes about a hosted model: the window, what it accepts and the
// levels it forwards. The prices are fields, so they are not repeated here.
function routeFacts(preset, route, model) {
  const box = element("div", "admin-preset-preview");
  const window = Number(model.context_tokens || 0);
  const facts = [
    window ? `${window.toLocaleString()} token window` : "window unknown",
    model.vision ? "accepts images" : "text only",
  ];
  if (model.reasoning) facts.push(`reasoning: ${model.reasoning.levels.join(", ")}`);
  box.append(element("p", null, facts.join(" · ")));
  if (preset && preset.notes) box.append(element("p", "dim", preset.notes));
  if (route && route.notes) box.append(element("p", "dim", route.notes));
  box.append(
    element(
      "p",
      "dim admin-preset-effective",
      route
        ? `Facts from ${preset ? preset.label : "the preset"}, verified ${route.verified}.`
        : "Declared by hand: nothing checks these until a turn spends tokens."
    )
  );
  return box;
}

/* One catalog entry, opened from its tile: what is fixed about it, then every
   setting the operator may change. The fixed half is prose and not inputs,
   because the server refuses a write that contradicts a preset (D78) and a form
   that let the operator try would turn a rule into an error message.

   Which settings are drawn is the preset's answer rather than this page's: the
   window and the reasoning default are the entry's, a local chat model also has
   an offload and a draft head, and a hand-declared model is all fields because
   nothing about it was ever stated for us. */
async function modelPanel(name) {
  const [baseline, presets] = await Promise.all([loadBaseline(), loadPresets()]);
  const model = (baseline.models || {})[name];
  if (!model) {
    note(`${name} is no longer in the catalog.`, true);
    return;
  }
  if ("local" in model) await localModelPanel(name, model, baseline, presets);
  else await hostedModelPanel(name, model, baseline, presets);
}

// Deleting an entry is removing a name from the catalog. For a model on this
// machine it is also the last use of gigabytes, so the weights are offered
// rather than kept: downloading them again is the expensive direction.
async function deleteModel(name, preset, local) {
  if (!confirm(`Delete the model ${name}?`)) return;
  const data = {
    baseline: await saveBaseline((draft) => {
      delete draft.models[name];
    }),
  };
  closeDialog();
  await catalogChanged(data, "models");
  note("");
  // The offer is made after the dialog is gone, so a refusal here has nowhere
  // to land but the page's own note.
  if (local) {
    await offerToDeleteWeights(preset, data.baseline).catch((error) =>
      note(error.message, true)
    );
  }
}

async function localModelPanel(name, model, baseline, presets) {
  const preset = presets.models.find((entry) => entry.id === model.preset);
  const starting = (preset && preset.local && preset.local.policy) || {};
  const local = model.local || {};

  const context = numberField(model.context_tokens, { min: 512, max: 1 << 21 });
  const reply = numberField(model.max_tokens, { min: 0 });
  const layers = numberField(local.gpu_layers ?? starting.gpu_layers ?? 99, {
    min: 0,
    max: 999,
  });
  const speculation =
    "speculation" in starting ? choiceField(SPECULATION, local.speculation || "none") : null;
  // Offered for every model, unlike the draft head: every model has a cache.
  const kvCache = choiceField(KV_CACHE, local.kv_cache || starting.kv_cache || "f16");
  const residency = choiceField(RESIDENCY, local.residency || "on-demand");
  const idle = numberField(local.idle_minutes ?? starting.idle_minutes ?? 15, {
    min: 1,
    max: 1440,
  });
  const idleRow = dialogField("Idle minutes before unloading", idle);
  // The idle period is read only while the model unloads when idle, so the field
  // is drawn unavailable while "keep loaded" is the answer rather than removed:
  // the number is still there when the answer is pressed back.
  const paintIdle = () => {
    idle.disabled = residency.value === "always";
  };
  residency.onchange = paintIdle;
  paintIdle();
  const reasoning = reasoningField(model);

  const entries = presetEntries(baseline, model);
  const body = [];
  if (entries.length > 1) body.push(entryChooser(entries, name, modelPanel));
  body.push(
    localFacts(preset, model),
    dialogField("Context tokens", context),
    dialogField("Max output tokens", reply),
    dialogField("Layers on the GPU", layers)
  );
  if (speculation) body.push(dialogField("Speculative decoding", speculation));
  body.push(dialogField("Context memory", kvCache));
  body.push(dialogField("Residency", residency), idleRow);
  if (reasoning) body.push(dialogField("Default reasoning", reasoning));
  if (preset) body.push(secondEntryRow(() => addLocalDialog(preset)));

  openDialog(`Settings for ${name}`, body, "Save", async () => {
    const data = {
      baseline: await saveBaseline((draft) => {
        const entry = draft.models[name];
        entry.context_tokens = Number(context.value);
        entry.max_tokens = Number(reply.value);
        entry.local = {
          ...entry.local,
          gpu_layers: Number(layers.value),
          kv_cache: kvCache.value,
          residency: residency.value,
          ...(residency.value === "on-demand" ? { idle_minutes: Number(idle.value) } : {}),
          ...(speculation ? { speculation: speculation.value } : {}),
        };
        if (reasoning) entry.reasoning = { ...entry.reasoning, default: reasoning.value };
      }),
    };
    closeDialog();
    await catalogChanged(data, "models");
    note(`${name} saved. The runtime restarts its models to apply it.`);
  });
  dialogTip(
    "The window is GPU memory: lower it, or make its memory compact, if the model " +
      "does not load. Its projector, vision and reasoning levels come with the model."
  );
  dialogDelete("Delete model", () => deleteModel(name, preset, true));
}

async function hostedModelPanel(name, model, baseline, presets) {
  const preset = presets.models.find((entry) => entry.id === model.preset);
  const provider = (baseline.providers || {})[model.provider] || {};
  const route = preset
    ? (preset.routes || []).find((item) => item.provider === provider.preset)
    : null;
  const fromPreset = Boolean(model.preset);

  const providers = Object.keys(baseline.providers || {}).sort();
  const providerSelect = choiceField(
    providers.map((item) => [item, item]),
    model.provider || providers[0]
  );
  providerSelect.disabled = fromPreset;
  const providerModel = textField(model.provider_model, "id the provider serves");
  providerModel.disabled = fromPreset;
  const context = numberField(model.context_tokens ?? 65536, { min: 0 });
  context.disabled = fromPreset;
  const reply = numberField(model.max_tokens ?? 16384, { min: 0 });
  const inputCost = numberField(model.input_cost_per_m ?? 0, { min: 0, step: "any" });
  const cachedCost = numberField(model.cached_input_cost_per_m ?? "", {
    min: 0,
    step: "any",
    placeholder: "none",
  });
  const outputCost = numberField(model.output_cost_per_m ?? 0, { min: 0, step: "any" });
  const vision = checkField("Accepts images", Boolean(model.vision));
  vision.input.disabled = fromPreset;
  const reasoning = reasoningField(model);

  const entries = presetEntries(baseline, model);
  const body = [];
  if (entries.length > 1) body.push(entryChooser(entries, name, modelPanel));
  if (fromPreset) body.push(routeFacts(preset, route, model));
  body.push(
    dialogField("Provider", providerSelect),
    dialogField("Provider model", providerModel),
    dialogField("Context tokens", context),
    dialogField("Max output tokens", reply),
    dialogField("Input $/M", inputCost),
    dialogField("Cached input $/M (blank if none)", cachedCost),
    dialogField("Output $/M", outputCost),
    vision.label
  );
  if (reasoning) body.push(dialogField("Default reasoning", reasoning));
  if (preset) {
    const options = (preset.routes || []).flatMap((item) =>
      item.available.map((providerName) => ({ route: item, provider: providerName }))
    );
    const labels = Object.fromEntries(presets.providers.map((item) => [item.id, item.label]));
    body.push(secondEntryRow(() => modelPresetDialog(preset, options, labels)));
  }

  openDialog(`Settings for ${name}`, body, "Save", async () => {
    const data = {
      baseline: await saveBaseline((draft) => {
        const entry = { ...draft.models[name] };
        entry.provider = providerSelect.value;
        entry.provider_model = providerModel.value.trim();
        entry.context_tokens = Number(context.value);
        entry.max_tokens = Number(reply.value);
        entry.input_cost_per_m = Number(inputCost.value);
        entry.output_cost_per_m = Number(outputCost.value);
        entry.vision = vision.input.checked;
        if (cachedCost.value.trim()) {
          entry.cached_input_cost_per_m = Number(cachedCost.value);
        } else {
          delete entry.cached_input_cost_per_m;
        }
        if (reasoning) entry.reasoning = { ...entry.reasoning, default: reasoning.value };
        draft.models[name] = entry;
      }),
    };
    closeDialog();
    await catalogChanged(data, "models");
    note("");
  });
  if (fromPreset) {
    dialogTip(
      "The provider's id, the window and vision come from this model's preset. " +
        "Reply length, prices and the default reasoning level are yours to set."
    );
  } else {
    dialogTip("Nothing checks a hand-declared model until a turn spends tokens on it.");
  }
  dialogDelete("Delete model", () => deleteModel(name, preset, false));
}

// A model by hand, against any provider: every field is the operator's and
// every claim is unchecked, which is why it is not the way a shipped model is
// added.
async function customModelDialog() {
  const baseline = await loadBaseline();
  const providers = Object.keys(baseline.providers || {}).sort();
  if (!providers.length) {
    note("A model needs a provider; add one on the Providers tab first.", true);
    return;
  }
  const name = document.createElement("input");
  name.placeholder = "catalog name";
  const provider = choiceField(
    providers.map((item) => [item, item]),
    providers[0]
  );
  const providerModel = textField("", "id the provider serves");
  const context = numberField(65536, { min: 0 });
  const reply = numberField(16384, { min: 0 });
  const inputCost = numberField(0, { min: 0, step: "any" });
  const cachedCost = numberField("", { min: 0, step: "any", placeholder: "none" });
  const outputCost = numberField(0, { min: 0, step: "any" });
  const vision = checkField("Accepts images", false);

  openDialog(
    "Add a model by hand",
    [
      dialogField("Catalog name", name),
      dialogField("Provider", provider),
      dialogField("Provider model", providerModel),
      dialogField("Context tokens", context),
      dialogField("Max output tokens", reply),
      dialogField("Input $/M", inputCost),
      dialogField("Cached input $/M (blank if none)", cachedCost),
      dialogField("Output $/M", outputCost),
      vision.label,
    ],
    "Add model",
    async () => {
      const modelName = name.value.trim();
      if (!/^[A-Za-z0-9_.-]+$/.test(modelName)) {
        throw new Error("A model name is letters, digits, dot, underscore or hyphen.");
      }
      if ((baseline.models || {})[modelName]) {
        throw new Error("That model already exists.");
      }
      const entry = {
        provider: provider.value,
        provider_model: providerModel.value.trim(),
        context_tokens: Number(context.value),
        max_tokens: Number(reply.value),
        input_cost_per_m: Number(inputCost.value),
        output_cost_per_m: Number(outputCost.value),
      };
      if (vision.input.checked) entry.vision = true;
      if (cachedCost.value.trim()) {
        entry.cached_input_cost_per_m = Number(cachedCost.value);
      }
      const data = {
        baseline: await saveBaseline((draft) => {
          draft.models = draft.models || {};
          draft.models[modelName] = entry;
        }),
      };
      closeDialog();
      await catalogChanged(data, "models");
      note("");
    }
  );
  dialogTip("Nothing checks a hand-declared model until a turn spends tokens on it.");
}

function routeSummary(route) {
  const lines = [
    `${route.context_tokens ? route.context_tokens.toLocaleString() : "unknown"} token window`,
    route.vision ? "accepts images" : "text only",
  ];
  const policy = route.policy || {};
  if (policy.input_cost_per_m !== undefined) {
    lines.push(`$${policy.input_cost_per_m}/M in · $${policy.output_cost_per_m}/M out`);
  }
  if (route.reasoning) lines.push(`reasoning: ${route.reasoning.levels.join(", ")}`);
  const summary = element("div", "admin-preset-preview");
  summary.append(element("p", null, lines.join(" · ")));
  if (route.notes) summary.append(element("p", "dim", route.notes));
  summary.append(element("p", "dim admin-preset-effective", `Verified ${route.verified}.`));
  return summary;
}

async function modelPresetDialog(preset, options, providerLabel) {
  const baseline = await loadBaseline();
  const provider = document.createElement("select");
  options.forEach((option, index) => {
    const label = providerLabel[option.route.provider] || option.route.provider;
    const text = option.provider === option.route.provider ? label : `${label} (${option.provider})`;
    provider.append(new Option(text, String(index)));
  });
  const name = document.createElement("input");
  const taken = (candidate) => Boolean((baseline.models || {})[candidate]);
  const suggest = (option) =>
    taken(preset.name) ? `${preset.name}-${option.provider}` : preset.name;
  name.value = suggest(options[0]);
  const summary = element("div");
  const show = () => {
    const option = options[Number(provider.value)];
    summary.replaceChildren(routeSummary(option.route));
    if (!name.dataset.edited) name.value = suggest(option);
  };
  name.oninput = () => {
    name.dataset.edited = "1";
  };
  provider.onchange = show;
  show();

  const body = [];
  if (preset.notes) body.push(dialogHint(preset.notes));
  // One provider is the common case and needs no choice drawn; the name is the
  // string users pick in the composer, so it is always offered.
  if (options.length > 1) body.push(dialogField("Served by", provider));
  body.push(summary, dialogField("Name in the composer", name));

  openDialog(`Add ${preset.label}`, body, "Add model", async () => {
    const option = options[Number(provider.value)];
    const data = await api("POST", "/api/admin/models", {
      preset: preset.id,
      provider: option.provider,
      name: name.value.trim(),
    });
    closeDialog();
    await catalogChanged(data, "models");
    note(`${name.value.trim()} added. Grant it to a role so someone can choose it.`);
  });
}
// -- files ------------------------------------------------------------------

function renderHomeDirs(data) {
  $("home-dirs").value = (data.dirs || []).join("\n");
  // Nothing to restore when the default list is already in force, and the
  // button says which folders that is so "defaults" is not a guess.
  $("reset-home-dirs").disabled = !data.set;
  $("reset-home-dirs").title = `Defaults: ${(data.default || []).join(", ")}`;
}

async function loadFiles() {
  renderHomeDirs(await api("GET", "/api/admin/home-dirs"));
}

// -- prompt -----------------------------------------------------------------

async function loadPrompt() {
  const data = await api("GET", "/api/admin/prompt");
  $("prompt").value = data.text || "";
}

/* Where each section comes from, keyed by the ids `prompt_sections` returns.
   The prose is here rather than in the response because it is a *label for* a
   fact the server computed, never a second composition of it: the page shows
   the text the model would receive and names its source, and an id this table
   does not know falls back to the id so a new section appears rather than
   vanishing. */
const PROMPT_SOURCES = {
  identity: "Setting and code — the name, then who it acts for",
  untrusted: "Code — the untrusted-output rule",
  operator: "Setting — the text above",
  account: "Account — the person's own instructions",
  project: "Project — AGENTS.md in the working directory",
  reach: "Composed — what this account can reach",
  tools: "Composed — the tools offered and the approval mode",
};

async function loadComposed() {
  const account = $("compose-account").value.trim();
  const message = $("compose-note");
  if (!account) {
    message.textContent = "Enter an account to compose its prompt.";
    $("composed-sections").replaceChildren();
    return;
  }
  const params = new URLSearchParams({ account });
  const workingDir = $("compose-working-dir").value.trim();
  if (workingDir) params.set("working_dir", workingDir);

  const data = await api("GET", `/api/admin/prompt/compose?${params}`);
  message.textContent = data.note || "";

  const body = document.createDocumentFragment();
  for (const section of data.sections) {
    body.append(
      element(
        "h3",
        "admin-compose-source",
        PROMPT_SOURCES[section.source] || section.source
      ),
      element("pre", "admin-pre", section.text)
    );
  }
  // The sections a preview cannot reach are named rather than left as a gap,
  // because a blank space reads as "this prompt says nothing here" — which is
  // the opposite of what a dispatch-only section means.
  for (const omitted of data.omitted || []) {
    body.append(
      element("h3", "admin-compose-source", PROMPT_SOURCES[omitted] || omitted),
      element(
        "p",
        "dim admin-hint",
        "Read on the acting machine, so it is not part of a preview."
      )
    );
  }
  $("composed-sections").replaceChildren(body);
}

// -- sessions ---------------------------------------------------------------

async function loadSessions() {
  const account = $("session-account").value.trim();
  const query = account ? `?account=${encodeURIComponent(account)}` : "";
  const data = await api("GET", `/api/admin/sessions${query}`);
  const rows = data.sessions.map((session) => ({
    label: session.title || "(untitled)",
    cells: [
      session.account,
      (session.created_at || "").slice(0, 19).replace("T", " "),
      // The provider's own total over the session's model calls, from its log.
      // A conversation that has not called a model yet reads zero rather than
      // blank, so the column is a number on every row.
      tokens(session.tokens || 0),
    ],
    build: () => sessionLog(session),
  }));
  replace("sessions", expandingTable(["Title", "Account", "Created", "Tokens"], rows));
}

// -- usage ------------------------------------------------------------------

// 173232041 → "173.2M". The table has six columns, and a seven-figure count in
// each one sets its floor (N159); the exact number stays in the title, where a
// hover reads it and the reader who needs it is one gesture away.
function tokens(count) {
  if (!count) return "0";
  for (const [size, suffix] of [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "k"],
  ]) {
    if (count >= size) {
      return `${(count / size).toFixed(1).replace(/\.0$/, "")}${suffix}`;
    }
  }
  return String(count);
}

// "2m ago", from an ISO timestamp. One unit, not `duration()`'s two: this is
// the age of a reading, and "1h 20m ago" reads as a countdown.
function since(iso) {
  if (!iso) return "never";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(seconds) || seconds < 60) return "just now";
  for (const [size, suffix] of [
    [86400, "d"],
    [3600, "h"],
    [60, "m"],
  ]) {
    if (seconds >= size) return `${Math.floor(seconds / size)}${suffix} ago`;
  }
  return "just now";
}

// Seven days, scaled against the heaviest window on the page rather than the
// row's own peak: on a roster the question is "who is spending", so a tall bar
// must mean more tokens and not "this account's busiest day". The detail
// redraws the same days against the row's own maximum, where the shape of one
// week is the question.
function spark(history, scale) {
  const box = document.createElement("span");
  box.className = "admin-spark";
  for (const day of history) {
    const bar = document.createElement("span");
    bar.title = `${day.day} · ${day.settled.toLocaleString()} tokens`;
    const fill = document.createElement("i");
    fill.style.height = scale ? `${(day.settled / scale) * 100}%` : "0%";
    // A shared scale can round a real day of spend down to nothing — a small
    // account beside a large one is a fraction of a percent — and an empty
    // track then reads as "no activity" rather than "little activity". The
    // height still carries the magnitude and the title the exact count; the
    // floor only keeps a day that spent something from looking like one that
    // did not.
    if (day.settled) fill.style.minHeight = "2px";
    bar.append(fill);
    box.append(bar);
  }
  return box;
}

function usageDetail(account) {
  const box = document.createElement("div");
  box.className = "admin-usage-detail";

  const total = account.history.reduce((sum, day) => sum + day.settled, 0);
  const best = account.history.reduce(
    (top, day) => (day.settled > top.settled ? day : top),
    { day: "—", settled: 0 }
  );
  const cap = account.tokens_daily
    ? `${account.tokens_daily.toLocaleString()} a day`
    : "no daily cap";

  const line = document.createElement("p");
  line.className = "dim";
  line.textContent =
    `${total.toLocaleString()} tokens over ${account.history.length} days · ` +
    `busiest ${best.day} (${best.settled.toLocaleString()}) · ${cap} · ` +
    `last call ${since(account.last_call)}`;
  box.append(line);

  // The row's own maximum here, not the page's: this chart answers "what did
  // this account's fortnight look like", and a shared scale would flatten a
  // small account into a straight line of nothing.
  const scale = Math.max(1, ...account.history.map((day) => day.settled));
  const bars = document.createElement("div");
  bars.className = "admin-days";
  for (const day of account.history) {
    const bar = document.createElement("span");
    bar.title = `${day.day} · ${day.settled.toLocaleString()} tokens`;
    const fill = document.createElement("i");
    fill.style.height = `${(day.settled / scale) * 100}%`;
    bar.append(fill);
    bars.append(bar);
  }
  box.append(bars);

  const axis = document.createElement("p");
  axis.className = "dim admin-axis";
  axis.textContent = `${account.history[0].day} → ${account.history[account.history.length - 1].day}`;
  box.append(axis);

  return box;
}

async function loadUsage() {
  const data = await api("GET", "/api/admin/usage");

  const today = data.accounts.reduce((sum, account) => sum + account.settled, 0);
  const inflight = data.accounts.reduce((sum, account) => sum + account.reserved, 0);
  const capped = data.accounts.filter((account) => account.tokens_daily).length;

  // The day comes from the server: the core's calendar decided which rows these
  // are, and a browser in another zone would name a different one.
  $("usage-note").textContent =
    `Every account, since midnight UTC on ${data.day}. Open one for its last ` +
    `${data.days} days.`;

  const strip = document.createElement("div");
  strip.className = "admin-status";
  strip.append(
    statusItem(
      "Tokens today",
      tokens(today),
      `${today.toLocaleString()} tokens across ${data.accounts.length} account(s)`
    ),
    statusItem(
      "In flight",
      inflight ? tokens(inflight) : "—",
      "Held for calls still running and released if they fail, so it is a " +
        "ceiling rather than a charge."
    ),
    statusItem(
      "With a cap",
      `${capped} of ${data.accounts.length}`,
      "A daily token cap comes from composed policy; an account without one " +
        "has no line to cross."
    ),
    statusItem("Resets", "00:00 UTC", "Token budgets are per calendar day in UTC.")
  );
  const stamp = document.createElement("p");
  stamp.className = "admin-stamp dim";
  stamp.textContent = `as of ${new Date().toLocaleTimeString()}`;
  const summary = document.createDocumentFragment();
  summary.append(strip, stamp);
  replace("usage-summary", summary);

  $("usage-caveat").textContent =
    "Settled counts completed model calls, from the total the provider reported. " +
    "In flight is held for calls still running and is released if they fail, so it " +
    "is a ceiling, not a charge. Summarisation calls are not metered yet.";

  // One scale across the roster, so the mini bars compare accounts to each
  // other rather than each to its own best day.
  const scale = Math.max(
    0,
    ...data.accounts.flatMap((account) => account.history.slice(-7).map((day) => day.settled))
  );

  const rows = data.accounts.map((account) => {
    // A capped account gets its denominator and its share; an uncapped one gets
    // neither, because "of ∞" would imply a limit that does not exist — the
    // same rule the chat window's usage pane follows.
    const day = document.createElement("span");
    if (account.tokens_daily) {
      const share = Math.min(1, account.settled / account.tokens_daily);
      day.textContent =
        `${tokens(account.settled)} · ${Math.round(share * 100)}% of ` +
        tokens(account.tokens_daily);
      day.title =
        `${account.settled.toLocaleString()} of ` +
        `${account.tokens_daily.toLocaleString()} tokens today`;
      if (share >= 0.9) day.className = "bad";
    } else {
      day.textContent = tokens(account.settled);
      day.title = `${account.settled.toLocaleString()} tokens today; no daily cap`;
    }

    // Zero is the ordinary state and is left blank rather than drawn as "0".
    const inflight = document.createElement("span");
    if (account.reserved) {
      inflight.textContent = tokens(account.reserved);
      inflight.title = `${account.reserved.toLocaleString()} tokens reserved for running calls`;
    }

    const last = document.createElement("span");
    last.textContent = since(account.last_call);
    if (account.last_call) last.title = new Date(account.last_call).toLocaleString();

    return {
      label: account.name,
      cells: [day, spark(account.history.slice(-7), scale), inflight, last],
      build: () => usageDetail(account),
    };
  });

  replace(
    "usage",
    expandingTable(["Account", "Today", "7 days", "In flight", "Last call"], rows)
  );
}

// -- audit ------------------------------------------------------------------

async function loadAudit() {
  const query = $("audit-query").value.trim();
  const data = await api(
    "GET",
    `/api/admin/audit?limit=50${query ? `&q=${encodeURIComponent(query)}` : ""}`
  );
  const rows = data.hits.map((hit) => [
    hit.seq,
    (hit.ts || "").slice(0, 19).replace("T", " "),
    hit.actor || "",
    hit.action,
  ]);
  replace("audit", table(["Seq", "Time", "Actor", "Action"], rows));
}

async function verifyAudit() {
  const result = await api("POST", "/api/admin/audit/verify");
  const pre = $("audit-verify");
  pre.textContent = JSON.stringify(result, null, 2);
  pre.classList.remove("hidden");
}

// -- events -----------------------------------------------------------------

async function loadEvents() {
  const level = $("event-level").value;
  const data = await api(
    "GET",
    `/api/admin/events?limit=200${level ? `&level=${encodeURIComponent(level)}` : ""}`
  );
  const rows = data.events.map((event) => [
    (event.at || "").slice(0, 19).replace("T", " "),
    event.level,
    event.logger,
    event.message,
  ]);
  replace("events", table(["Time", "Level", "Source", "Message"], rows));
}

// -- wiring -----------------------------------------------------------------

$("login-go").onclick = signIn;
for (const id of ["username", "password"]) {
  $(id).onkeydown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      signIn();
    }
  };
}
$("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-tab]");
  if (!button) return;
  // The rail names the roster, so it leads there from inside an account as well.
  if (button.dataset.tab === "accounts" && accountsUI.current) closeAccount();
  showTab(button.dataset.tab);
});

// The collapse is one attribute on the root element that the stylesheet reads,
// so there is no second idea of "is it narrow" in JavaScript to disagree with
// the CSS.
$("rail-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const compact = root.dataset.rail === "compact";
  root.dataset.rail = compact ? "expanded" : "compact";
  const label = compact ? "Collapse navigation" : "Expand navigation";
  $("rail-toggle").setAttribute("aria-expanded", String(compact));
  $("rail-toggle").title = label;
  $("rail-toggle").setAttribute("aria-label", label);
});

// A hash edited by hand, or a link into the page, is the one case `showTab` did
// not write itself; it answers to it rather than ignoring it.
window.addEventListener("hashchange", () => {
  const name = location.hash.slice(1);
  if (loaders[name] && !$("panel").classList.contains("hidden")) showTab(name, false);
});

for (const node of document.querySelectorAll("[data-tip]")) armTip(node, node.dataset.tip);
document.addEventListener("mouseover", (event) => {
  if (event.target.closest("[data-tip]")) showTip(event.target.closest("[data-tip]"));
});
document.addEventListener("mouseout", (event) => {
  if (event.target.closest("[data-tip]")) hideTip();
});
document.addEventListener("focusin", (event) => {
  if (event.target.closest("[data-tip]")) showTip(event.target.closest("[data-tip]"));
});
document.addEventListener("focusout", hideTip);
document.addEventListener("scroll", hideTip, true);
// A tip can sit inside a label, where a press would otherwise toggle the checkbox.
document.addEventListener("click", (event) => {
  if (event.target.closest(".admin-tip")) event.preventDefault();
});

$("group-search").addEventListener("input", (event) => {
  groupsUI.query = event.target.value;
  renderGroups();
});
$("new-group").onclick = () => groupDialog().catch((error) => note(error.message, true));

$("new-role").onclick = () => roleDialog().catch((error) => note(error.message, true));
$("role-search").addEventListener("input", (event) => {
  rolesUI.query = event.target.value;
  renderRoles();
});

$("save-prompt").onclick = async () => {
  try {
    await api("PUT", "/api/admin/prompt", { text: $("prompt").value });
    note("Prompt saved.");
  } catch (error) {
    note(error.message, true);
  }
};

$("save-home-dirs").onclick = async () => {
  // Blank lines are dropped rather than sent: a trailing newline is how a
  // textarea ends, not an empty folder name. Everything else is the server's
  // to refuse, with a reason the operator can act on.
  const dirs = $("home-dirs")
    .value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  try {
    const data = await api("PUT", "/api/admin/home-dirs", { dirs });
    renderHomeDirs(data);
    note("Home folders saved. The next turn uses them.");
  } catch (error) {
    note(error.message, true);
  }
};

$("reset-home-dirs").onclick = async () => {
  try {
    renderHomeDirs(await api("DELETE", "/api/admin/home-dirs"));
    note("Using the built-in home folders.");
  } catch (error) {
    note(error.message, true);
  }
};

$("compose-prompt").onclick = () => {
  loadComposed().catch((error) => note(error.message, true));
};
$("load-sessions").onclick = () => {
  loaded.delete("sessions");
  loadSessions().catch((error) => note(error.message, true));
};
$("load-audit").onclick = () => {
  loadAudit().catch((error) => note(error.message, true));
};
$("verify-audit").onclick = () => {
  verifyAudit().catch((error) => note(error.message, true));
};
$("load-events").onclick = () => {
  loaded.delete("events");
  loadEvents().catch((error) => note(error.message, true));
};

boot();
