"use strict";

// Tasks, inside the chat window.
//
// The rail's foot turns the session list into the task list, and the window's
// main area into the task view: an overview first, then a task as a session
// would read — its steps drawn on the transcript's work rail, a step opened in
// place, and Run now where the composer is (D99). Nothing here is a second
// page: the rail, the palette, `api`, `el`, the tool summaries and the markdown
// renderer are the chat window's own, loaded before this file.
//
// A task's output lives here and nowhere else. A run step's own words are on
// its record; an agent step's transcript is read through its run, and chat
// never lists it. A running task is followed by polling, not a socket: the
// run executes on the server whether or not this window is open, so the view
// is only ever a reader of the run's row and its log.
//
// A task is a recipe of steps, not a graph (D95): the editor is the same rail
// with each step opened as a card of fields.

const TASKS_IDLE_MS = 15000;
const TASKS_LIVE_MS = 2000;
const TASKS_HISTORY_DAYS = 14;
const TASK_RUN_DIR = "$RUN_DIR";
// Long traces fold in the middle: the first steps say how it began, the last
// say where it is. A reader who wants the rest opens the fold.
const TRACE_HEAD = 3;
const TRACE_TAIL = 6;

const TASK_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// The words a person reads, not the contract's. `budget` is the harness's
// name for a turn that hit its cap; what happened is that it stopped early.
const TASK_WORD = {
  due: "starting",
  waiting: "waiting for your computer",
  running: "running",
  succeeded: "done",
  failed: "failed",
  budget: "stopped early",
  missed: "missed",
  cancelled: "stopped",
  pending: "waiting",
  skipped: "skipped",
};

const TASK_RUN_IF = [
  ["ok", "only if every step before it finished"],
  ["ok_or_budget", "even if the step before it stopped early"],
  ["always", "always, whatever happened before"],
];

const TASK_INTERVALS = [
  [15, "15 minutes"],
  [30, "30 minutes"],
  [60, "hour"],
  [120, "2 hours"],
  [180, "3 hours"],
  [360, "6 hours"],
  [720, "12 hours"],
  [1440, "24 hours"],
];

const TASK_LIMITS = [60, 120, 300, 600, 900, 1200, 1800, 2700, 3600];

const tasksState = {
  started: false,
  open: false,
  tasks: [],
  latest: {},
  recent: [],
  connected: false,
  view: "overview",
  taskId: null,
  runs: [],
  runIndex: 0,
  openStep: null,
  settingsStep: null,
  // Agent-step transcripts, keyed `${run}:${index}`.
  transcripts: new Map(),
  // Tool rows opened inside a trace, and folds opened, keyed the same way plus
  // the call — so a poll that redraws the step keeps what the reader opened.
  opened: new Set(),
  unfolded: new Set(),
  showAllRuns: false,
  menu: false,
  confirmDelete: false,
  draft: null,
  adding: false,
  filter: "",
  sessionsFilter: "",
  message: "",
  busy: false,
  catalog: null,
  timer: null,
  signature: "",
};

// -- small helpers ------------------------------------------------------------

function taskNode(tag, className, text, attrs) {
  const node = el(tag, className, text);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value);
  }
  return node;
}

function taskButton(className, text, onClick, attrs) {
  const button = taskNode("button", className, text, { type: "button", ...attrs });
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick(event);
  });
  return button;
}

// A row that is a control: focusable, and Enter or Space presses it.
function taskPressable(node, onPress) {
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.addEventListener("click", onPress);
  node.addEventListener("keydown", (event) => {
    if (event.target !== node) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onPress(event);
    }
  });
  return node;
}

function taskIcon(path, viewBox = "0 0 16 16") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", viewBox);
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = path;
  return svg;
}

const TASK_PLAY = '<path d="M4 2.6v10.8L13 8z" fill="currentColor"/>';
const TASK_GRID =
  '<path d="M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z" ' +
  'fill="none" stroke="currentColor" stroke-width="1.4"/>';
const TASK_DOTS =
  '<circle cx="3.5" cy="8" r="1.3" fill="currentColor"/>' +
  '<circle cx="8" cy="8" r="1.3" fill="currentColor"/>' +
  '<circle cx="12.5" cy="8" r="1.3" fill="currentColor"/>';

function taskTime(date) {
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function taskDayKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function taskDaysFromToday(date) {
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((start(date) - start(new Date())) / 86400000);
}

// "Today 6:15 AM", "Yesterday 6:15 AM", "Sun 8 Nov, 6:15 AM".
function taskWhen(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const days = taskDaysFromToday(date);
  if (days === 0) return `Today ${taskTime(date)}`;
  if (days === -1) return `Yesterday ${taskTime(date)}`;
  if (days === 1) return `Tomorrow ${taskTime(date)}`;
  const day = date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  return `${day}, ${taskTime(date)}`;
}

// How far off a future moment is, in the unit a person would say it in.
function taskUntil(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const minutes = Math.round((date.getTime() - Date.now()) / 60000);
  if (minutes <= 0) return "due now";
  if (minutes < 60) return `in ${minutes}m`;
  const days = taskDaysFromToday(date);
  if (days === 0) return `in ${Math.round(minutes / 60)}h`;
  if (days === 1) return "tomorrow";
  return date.toLocaleDateString([], { weekday: "short" });
}

function taskDuration(run) {
  const from = run && Date.parse(run.started_at);
  if (!from) return "";
  const to = run.finished_at ? Date.parse(run.finished_at) : Date.now();
  let seconds = Math.max(0, Math.round((to - from) / 1000));
  const hours = Math.floor(seconds / 3600);
  seconds -= hours * 3600;
  const minutes = Math.floor(seconds / 60);
  seconds -= minutes * 60;
  const text = hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
  return run.finished_at ? text : `${text} so far`;
}

function taskMoney(value) {
  if (value === null || value === undefined) return "";
  return `$${value.toFixed(value > 0 && value < 0.01 ? 4 : 2)}`;
}

function taskClock(time) {
  const [h, m] = String(time || "00:00").split(":").map(Number);
  return taskTime(new Date(2000, 0, 1, h, m));
}

function taskScheduleText(schedule) {
  if (!schedule) return "";
  if (schedule.kind === "daily") return `Every day at ${taskClock(schedule.time)}`;
  if (schedule.kind === "weekly") {
    return `Every ${TASK_DAYS[schedule.weekday] || "week"} at ${taskClock(schedule.time)}`;
  }
  const minutes = schedule.minutes || 60;
  if (minutes === 60) return "Every hour";
  if (minutes % 60 === 0) return `Every ${minutes / 60} hours`;
  return `Every ${minutes} minutes`;
}

function taskBrowserZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

// The zone is named only when it is not the reader's own: a schedule in your
// own time needs no label, one in another's must say so.
function taskZoneText(schedule) {
  if (!schedule || !schedule.timezone || schedule.timezone === taskBrowserZone()) return "";
  return `${schedule.timezone.split("/").pop().replace(/_/g, " ")} time`;
}

function taskById(id) {
  return tasksState.tasks.find((task) => task.id === id) || null;
}

function taskLatest(task) {
  return tasksState.latest[task.id] || null;
}

function taskActive(run) {
  return Boolean(run) && ["due", "waiting", "running"].includes(run.status);
}

function taskNeedsLook(task) {
  const run = taskLatest(task);
  return task.enabled && Boolean(run) && ["failed", "budget", "missed"].includes(run.status);
}

function taskStatus(task) {
  const run = taskLatest(task);
  if (taskActive(run)) return run.status === "waiting" ? "waiting" : "running";
  if (!task.enabled) return "paused";
  return run ? run.status : "never";
}

// The colour a status dot carries; the word beside it always says the same.
function taskTone(status) {
  if (status === "succeeded") return "ok";
  if (status === "failed" || status === "missed") return "bad";
  if (status === "budget" || status === "waiting") return "warn";
  if (status === "running" || status === "due") return "live";
  return "";
}

function taskCommandLine(argv) {
  return (argv || [])
    .map((word) =>
      /^[A-Za-z0-9_@%+=:,./$-]+$/.test(word) ? word : `'${word.replace(/'/g, "'\\''")}'`
    )
    .join(" ");
}

// A command is edited as one line and stored as argv. The split is the shell's
// word rules for quotes and backslashes and nothing else: no variables, no
// globs, no pipes — the server runs the words as given (D96).
function taskSplitCommand(text) {
  const words = [];
  let word = "";
  let started = false;
  let quote = null;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quote) {
      if (char === quote) quote = null;
      else if (char === "\\" && quote === '"' && i + 1 < text.length) word += text[(i += 1)];
      else word += char;
    } else if (char === "'" || char === '"') {
      quote = char;
      started = true;
    } else if (char === "\\" && i + 1 < text.length) {
      word += text[(i += 1)];
      started = true;
    } else if (/\s/.test(char)) {
      if (started) words.push(word);
      word = "";
      started = false;
    } else {
      word += char;
      started = true;
    }
  }
  if (quote) throw new Error("A command has an unclosed quote.");
  if (started) words.push(word);
  return words;
}

function taskList(value) {
  return String(value || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

// Why a run needs a look, as a sentence rather than a status.
function taskReason(task, run) {
  if (!run) return "";
  if (run.status === "missed") {
    return "It was due while your computer was off, and it waited six hours before giving up.";
  }
  const steps = run.steps || [];
  const index = steps.findIndex((step) => step.status === "failed" || step.status === "budget");
  if (index < 0) return run.error || "";
  const step = steps[index];
  if (step.status === "budget") {
    const authored = (task.steps || [])[index];
    const cap = authored && authored.envelope ? authored.envelope.max_iterations : null;
    const after = steps.slice(index + 1).some((later) => later.status === "succeeded");
    return (
      `The ${step.name} step hit its ${cap ? `${cap}-step ` : ""}limit.` +
      (after ? " The steps after it still ran." : "")
    );
  }
  const said = step.output ? step.output.trim().split("\n").pop() : "";
  return `The ${step.name} step failed (${step.error || "no reason given"})${said ? `: ${said}` : "."}`;
}

// -- the API --------------------------------------------------------------------

async function tasksLoad() {
  const data = await api("GET", "/api/tasks");
  tasksState.tasks = (data && data.tasks) || [];
  tasksState.latest = (data && data.latest) || {};
  tasksState.recent = (data && data.recent) || [];
  tasksState.connected = Boolean(data && data.connected);
}

async function tasksLoadOpen() {
  const id = tasksState.taskId;
  if (!id || id === "new") return;
  const data = await api("GET", `/api/tasks/${id}`);
  if (tasksState.taskId !== id) return;
  tasksState.runs = (data && data.runs) || [];
  const index = tasksState.tasks.findIndex((task) => task.id === id);
  if (index >= 0 && data && data.task) tasksState.tasks[index] = data.task;
  if (tasksState.runIndex >= tasksState.runs.length) tasksState.runIndex = 0;
}

async function tasksLoadTranscript() {
  const task = taskById(tasksState.taskId);
  const run = tasksState.runs[tasksState.runIndex];
  const index = tasksState.openStep;
  if (!task || !run || index === null || tasksState.draft) return;
  const record = (run.steps || [])[index];
  if (!record || record.kind !== "agent" || !record.session_id) return;
  const key = `${run.id}:${index}`;
  const cached = tasksState.transcripts.get(key);
  // A finished step's transcript does not change; read it once.
  if (cached && cached.status !== "running") return;
  try {
    const data = await api("GET", `/api/tasks/${task.id}/runs/${run.id}/steps/${index}`);
    tasksState.transcripts.set(key, data);
  } catch {
    tasksState.transcripts.set(key, { status: record.status, messages: null });
  }
}

// What the editor offers: the models granted, and the client tools a task may
// hold — never a critical one, which a task can never be pre-approved for (D93).
async function tasksLoadCatalog() {
  if (tasksState.catalog) return tasksState.catalog;
  const [policy, toolData] = await Promise.all([
    api("GET", "/api/policy"),
    api("GET", "/api/tools"),
  ]);
  tasksState.catalog = {
    models: ((policy && policy.model_info) || []).map((entry) => entry.name),
    maxIterations: (policy && policy.policy && policy.policy.max_iterations) || 500,
    tools: ((toolData && toolData.tools) || [])
      .filter((tool) => tool.allowed && tool.kind === "client" && !tool.critical)
      .map((tool) => tool.name),
  };
  return tasksState.catalog;
}

// One loop for every read. Fast while something is running and someone is
// looking, slow otherwise — the slow pace still keeps the rail's count honest.
async function tasksTick() {
  clearTimeout(tasksState.timer);
  if (!tasksState.started) return;
  try {
    await tasksLoad();
    if (tasksState.open && tasksState.view === "task" && !tasksState.draft) {
      await tasksLoadOpen();
      await tasksLoadTranscript();
    }
    tasksPaint();
  } catch {
    // A failed poll leaves the last answer on screen; the next one retries.
  }
  // Signed out while this read was in flight: the loop ends here.
  if (!tasksState.started) return;
  const live = tasksState.open && Object.values(tasksState.latest).some(taskActive);
  tasksState.timer = setTimeout(tasksTick, live ? TASKS_LIVE_MS : TASKS_IDLE_MS);
}

// -- entering and leaving ---------------------------------------------------------

function tasksSignedIn() {
  if (tasksState.started) return;
  tasksState.started = true;
  tasksTick();
}

function tasksSignedOut() {
  tasksClose();
  clearTimeout(tasksState.timer);
  Object.assign(tasksState, {
    started: false,
    tasks: [],
    latest: {},
    recent: [],
    taskId: null,
    runs: [],
    draft: null,
    catalog: null,
    signature: "",
  });
  tasksState.transcripts.clear();
  $("tasks-badge").classList.add("hidden");
}

function tasksIsOpen() {
  return tasksState.open;
}

function tasksShow() {
  if (tasksState.open) return;
  tasksState.open = true;
  tasksState.sessionsFilter = $("sessions-filter").value;
  $("sessions-filter").value = tasksState.filter;
  $("sessions-filter").placeholder = "Search tasks…";
  $("sessions-filter").setAttribute("aria-label", "Search tasks");
  $("new-label").textContent = "New task";
  document.body.classList.add("tasks-mode");
  for (const id of ["sessions-list", "tasks-link"]) $(id).classList.add("hidden");
  for (const id of ["tasks-list", "sessions-link", "rail-mode", "tasks-view"]) {
    $(id).classList.remove("hidden");
  }
  tasksState.signature = "";
  tasksPaint();
  tasksTick();
}

function tasksClose() {
  if (!tasksState.open) return;
  tasksState.open = false;
  tasksState.filter = $("sessions-filter").value;
  $("sessions-filter").value = tasksState.sessionsFilter;
  $("sessions-filter").placeholder = "Search sessions…";
  $("sessions-filter").setAttribute("aria-label", "Search sessions");
  $("new-label").textContent = "New session";
  document.body.classList.remove("tasks-mode");
  for (const id of ["tasks-list", "sessions-link", "rail-mode", "tasks-view", "tasks-dock"]) {
    $(id).classList.add("hidden");
  }
  for (const id of ["sessions-list", "tasks-link"]) $(id).classList.remove("hidden");
}

function tasksFilter(text) {
  tasksState.filter = text;
  tasksPaintRail();
}

function tasksOverview() {
  Object.assign(tasksState, { view: "overview", taskId: null, draft: null, menu: false });
  tasksState.signature = "";
  tasksPaint();
}

async function tasksOpen(id) {
  Object.assign(tasksState, {
    view: "task",
    taskId: id,
    runs: [],
    runIndex: 0,
    openStep: null,
    settingsStep: null,
    draft: null,
    adding: false,
    menu: false,
    confirmDelete: false,
    showAllRuns: false,
    message: "",
  });
  tasksState.signature = "";
  tasksPaint();
  $("tasks-view").scrollTop = 0;
  try {
    await tasksLoadOpen();
  } catch (error) {
    tasksState.message = error.message;
  }
  // Opened on the step that is the news: the one running, or the one that
  // failed or stopped early.
  const run = tasksState.runs[0];
  const at = run
    ? (run.steps || []).findIndex((step) =>
        ["running", "failed", "budget"].includes(step.status)
      )
    : -1;
  tasksState.openStep = at >= 0 ? at : null;
  await tasksLoadTranscript();
  tasksState.signature = "";
  tasksPaint();
  tasksTick();
}

// -- painting -----------------------------------------------------------------------

function tasksPaint() {
  tasksPaintBadge();
  if (!tasksState.open) return;
  tasksPaintRail();
  // A poll repaints only when what it read changed: a repaint rebuilds the
  // view, and doing that every two seconds for nothing would fight a reader
  // selecting text. Never while editing, which is the reader's own state.
  const signature = JSON.stringify([
    tasksState.view,
    tasksState.taskId,
    tasksState.tasks,
    tasksState.latest,
    tasksState.recent,
    tasksState.connected,
    tasksState.runs,
    tasksState.runIndex,
    tasksState.openStep,
    tasksState.settingsStep,
    // A transcript by its size, not its text: a long one stringified every
    // two seconds would cost more than the repaint it decides.
    [...tasksState.transcripts.entries()].map(([key, data]) => [
      key,
      data.status,
      data.messages ? data.messages.length : -1,
      data.messages ? JSON.stringify(data.messages[data.messages.length - 1] || null).length : 0,
    ]),
    [...tasksState.opened],
    [...tasksState.unfolded],
    tasksState.showAllRuns,
    tasksState.menu,
    tasksState.confirmDelete,
    tasksState.message,
    tasksState.busy,
  ]);
  if (tasksState.draft) {
    if (tasksState.signature !== "draft") tasksPaintEditor();
    tasksState.signature = "draft";
    return;
  }
  if (signature === tasksState.signature) return;
  tasksState.signature = signature;
  const view = $("tasks-view");
  const top = view.scrollTop;
  const content =
    tasksState.view === "task" && taskById(tasksState.taskId)
      ? tasksTaskView(taskById(tasksState.taskId))
      : tasksOverviewView();
  $("tasks-content").replaceChildren(...content);
  view.scrollTop = top;
  tasksPaintDock();
}

// The Tasks link's count, drawn in either mode: it is how a person in a
// conversation learns that a scheduled run needs them.
function tasksPaintBadge() {
  const count = tasksState.tasks.filter(taskNeedsLook).length;
  const badge = $("tasks-badge");
  badge.textContent = count ? `${count} need${count === 1 ? "s" : ""} a look` : "";
  badge.classList.toggle("hidden", !count);
}

function tasksPaintRail() {
  const list = $("tasks-list");
  const rows = [];
  const overview = taskPressable(el("div", "row overview"), () => tasksOverview());
  overview.classList.toggle("current", tasksState.view === "overview");
  overview.append(taskIcon(TASK_GRID), el("span", "title", "Overview"));
  rows.push(overview);

  if (tasksState.draft && !tasksState.draft.id) {
    rows.push(el("div", "group active", "Draft"));
    rows.push(tasksRailRow(tasksState.draft, true));
  }

  const query = tasksState.filter.trim().toLowerCase();
  const matches = (task) =>
    !query ||
    task.name.toLowerCase().includes(query) ||
    (task.steps || []).some((step) =>
      (step.instruction || taskCommandLine(step.argv)).toLowerCase().includes(query)
    );
  const groups = [
    ["Running", "active", (task) => ["running", "waiting"].includes(taskStatus(task))],
    ["Needs a look", "alarm", (task) => taskNeedsLook(task) && !taskActive(taskLatest(task))],
    [
      "Scheduled",
      "",
      (task) => task.enabled && !taskNeedsLook(task) && !taskActive(taskLatest(task)),
    ],
    ["Paused", "", (task) => !task.enabled && !taskActive(taskLatest(task))],
  ];
  let shown = 0;
  for (const [label, tone, test] of groups) {
    const members = tasksState.tasks.filter((task) => test(task) && matches(task));
    if (!members.length) continue;
    shown += members.length;
    rows.push(el("div", `group ${tone}`, label));
    for (const task of members) rows.push(tasksRailRow(task, false));
  }
  if (!shown && !tasksState.tasks.length) {
    rows.push(el("p", "tasks-rail-empty", "No tasks yet. New task builds one."));
  } else if (!shown) {
    rows.push(el("p", "tasks-rail-empty", "No task matches."));
  }
  list.replaceChildren(...rows);
}

function tasksRailRow(task, draft) {
  const status = draft ? "never" : taskStatus(task);
  const row = taskPressable(el("div", `row task-row tone-${taskTone(status) || "none"}`), () =>
    draft ? null : tasksOpen(task.id)
  );
  const current = draft
    ? tasksState.draft && !tasksState.draft.id
    : tasksState.view === "task" && tasksState.taskId === task.id;
  row.classList.toggle("current", Boolean(current));
  const run = draft ? null : taskLatest(task);

  let when = "";
  let snippet = "";
  if (draft) {
    snippet = "Not saved yet";
  } else if (status === "running" || status === "waiting") {
    when = status === "running" ? "running" : "waiting";
    const live = (run.steps || []).findIndex((step) => step.status === "running");
    snippet =
      status === "waiting"
        ? "Waiting for your computer"
        : live >= 0
          ? `Step ${live + 1} of ${run.steps.length}: ${run.steps[live].name}`
          : "Starting";
  } else if (!task.enabled) {
    when = "off";
    snippet = `${taskScheduleText(task.schedule)} · paused`;
  } else {
    when = taskUntil(task.next_run_at);
    snippet = taskNeedsLook(task)
      ? `${TASK_WORD[run.status]} · ${taskWhen(run.created_at)}`
      : taskScheduleText(task.schedule);
  }
  row.append(
    el("span", "dot"),
    el("span", `title${task.name ? "" : " untitled"}`, task.name || "Untitled task"),
    el("span", "when", when),
    el("span", "snippet", snippet)
  );
  return row;
}

// -- the overview -------------------------------------------------------------------

function tasksOverviewView() {
  const nodes = [];
  const head = el("div", "tasks-head");
  head.append(el("h1", "tasks-title", "Tasks"));
  const conn = el("span", `tasks-conn ${tasksState.connected ? "on" : "off"}`);
  conn.append(
    el("i"),
    tasksState.connected
      ? "This computer is connected. Tasks run in the background, so this window can stay closed."
      : "Your computer isn't connected. Due runs wait up to six hours for it, then count as missed."
  );
  head.append(conn);
  nodes.push(head);

  if (!tasksState.tasks.length) {
    const empty = el("div", "tasks-empty");
    empty.append(
      el("p", null, "A task is a list of steps Neuman runs for you on a schedule: commands " +
        "that run exactly as you wrote them, and agent steps that work from an instruction " +
        "inside limits you set."),
    );
    empty.append(taskButton("tasks-new primary", "New task", () => tasksNew()));
    nodes.push(empty);
    return nodes;
  }

  const weekAgo = Date.now() - 7 * 86400000;
  const week = tasksState.recent.filter(
    (run) => Date.parse(run.created_at) >= weekAgo && !taskActive(run)
  );
  const fine = week.filter((run) => run.status === "succeeded").length;
  const spend = week.reduce((sum, run) => sum + (run.cost_usd || 0), 0);
  const running = tasksState.tasks.filter((task) => taskActive(taskLatest(task)));
  const needs = tasksState.tasks.filter(
    (task) => taskNeedsLook(task) && !taskActive(taskLatest(task))
  );

  const stats = el("div", "tasks-stats");
  for (const [value, label, tone] of [
    [String(running.length), "running now", running.length ? "live" : ""],
    [String(needs.length), needs.length === 1 ? "needs a look" : "need a look", needs.length ? "bad" : ""],
    [String(week.length), `runs this week${week.length ? ` · ${fine} fine` : ""}`, ""],
    [taskMoney(spend), "model spend this week", ""],
  ]) {
    const stat = el("div", "tasks-stat");
    stat.append(el("b", tone, value), el("span", null, label));
    stats.append(stat);
  }
  nodes.push(stats);

  if (needs.length) {
    nodes.push(
      tasksSection(
        "Needs a look",
        needs.map((task) =>
          tasksNotice(task, TASK_WORD[taskLatest(task).status], taskWhen(taskLatest(task).created_at),
            taskReason(task, taskLatest(task)), "Open")
        )
      )
    );
  }
  if (running.length) {
    nodes.push(
      tasksSection(
        "Running now",
        running.map((task) => {
          const run = taskLatest(task);
          const live = (run.steps || []).findIndex((step) => step.status === "running");
          const why =
            run.status === "waiting"
              ? "Waiting for your computer to connect."
              : live >= 0
                ? `Step ${live + 1} of ${run.steps.length}: ${run.steps[live].name}`
                : "Starting.";
          return tasksNotice(task, "started", taskWhen(run.started_at || run.created_at), why,
            "Follow");
        })
      )
    );
  }

  const two = el("div", "tasks-two");
  two.append(tasksUpcoming(), tasksHistory());
  nodes.push(two);
  return nodes;
}

function tasksSection(label, children, aside) {
  const section = el("section", "tasks-section");
  const head = el("div", "tasks-label", label);
  if (aside) head.append(aside);
  section.append(head, ...children);
  return section;
}

function tasksNotice(task, word, when, why, go) {
  const item = taskPressable(
    el("div", `tasks-notice tone-${taskTone(taskLatest(task).status)}`),
    () => tasksOpen(task.id)
  );
  const title = el("span", "tasks-notice-title", task.name);
  title.append(el("span", "dim", ` ${word} · ${when}`));
  item.append(el("span", "dot"), title, el("span", "tasks-notice-go", go));
  if (why) item.append(el("span", "tasks-notice-why", why));
  return item;
}

function tasksUpcoming() {
  const due = tasksState.tasks
    .filter((task) => task.enabled && task.next_run_at)
    .sort((a, b) => Date.parse(a.next_run_at) - Date.parse(b.next_run_at))
    .slice(0, 8);
  const list = el("div", "tasks-upcoming");
  let lastGroup = null;
  for (const task of due) {
    const date = new Date(task.next_run_at);
    const days = taskDaysFromToday(date);
    const group = days <= 0 ? "Today" : days === 1 ? "Tomorrow" : "Later";
    if (group !== lastGroup) {
      list.append(el("div", "tasks-day", group));
      lastGroup = group;
    }
    const row = taskPressable(el("div", "tasks-up"), () => tasksOpen(task.id));
    row.append(
      el("span", "tm", group === "Later"
        ? `${date.toLocaleDateString([], { weekday: "short" })} ${taskTime(date)}`
        : taskTime(date)),
      el("span", "nm", task.name),
      el("span", "rel", days <= 0 ? taskUntil(task.next_run_at) : "")
    );
    list.append(row);
  }
  if (!due.length) list.append(el("p", "dim", "Nothing is scheduled."));
  return tasksSection("Coming up", [list]);
}

// Fourteen cells per task, oldest left. A day shows its worst run, because one
// failure in a day of successes is the thing the strip exists to show.
function tasksHistory() {
  const rank = { failed: 5, missed: 5, budget: 4, cancelled: 3, running: 2, succeeded: 1 };
  const days = [];
  for (let back = TASKS_HISTORY_DAYS - 1; back >= 0; back -= 1) {
    const date = new Date();
    date.setDate(date.getDate() - back);
    days.push(taskDayKey(date));
  }
  const worst = new Map();
  for (const run of tasksState.recent) {
    const status = run.status === "due" || run.status === "waiting" ? "running" : run.status;
    const key = `${run.task_id}:${taskDayKey(new Date(run.created_at))}`;
    const current = worst.get(key);
    if (!current || (rank[status] || 0) > (rank[current] || 0)) worst.set(key, status);
  }
  const rows = el("div", "tasks-hist");
  const axis = el("div", "tasks-hist-row tasks-hist-axis");
  const ends = el("span", "tasks-cells-axis");
  const first = new Date();
  first.setDate(first.getDate() - (TASKS_HISTORY_DAYS - 1));
  ends.append(
    el("span", null, first.toLocaleDateString([], { day: "numeric", month: "short" })),
    el("span", null, "today")
  );
  axis.append(el("span"), ends);
  rows.append(axis);
  for (const task of tasksState.tasks) {
    const row = taskPressable(el("div", "tasks-hist-row"), () => tasksOpen(task.id));
    row.append(el("span", `nm${task.enabled ? "" : " dim"}`, task.name));
    const cells = el("span", "tasks-cells");
    for (const day of days) {
      const status = worst.get(`${task.id}:${day}`);
      cells.append(el("i", `cell ${status ? `s-${status}` : ""}`));
    }
    cells.setAttribute("aria-label", `${task.name}: the last ${TASKS_HISTORY_DAYS} days`);
    row.append(cells);
    rows.append(row);
  }
  const legend = el("div", "tasks-legend");
  for (const [status, label] of [
    ["succeeded", "fine"],
    ["budget", "stopped early"],
    ["failed", "failed"],
    ["missed", "missed"],
    ["", "no run"],
  ]) {
    const item = el("span");
    item.append(el("i", `cell ${status ? `s-${status}` : ""}`), label);
    legend.append(item);
  }
  return tasksSection(`Last ${TASKS_HISTORY_DAYS} days`, [rows, legend]);
}

// -- a task -------------------------------------------------------------------------

function tasksTaskView(task) {
  const nodes = [];
  const run = tasksState.runs[tasksState.runIndex] || null;
  const status = taskStatus(task);

  const head = el("div", "tasks-head");
  const titleRow = el("div", "tasks-title-row");
  titleRow.append(el("h1", "tasks-title", task.name));
  const chipWord = {
    running: "running",
    waiting: "waiting",
    paused: "paused",
    never: "never run",
    succeeded: "healthy",
  }[status] || TASK_WORD[status];
  titleRow.append(el("span", `tasks-chip tone-${taskTone(status) || "none"}`, chipWord));
  const spacer = el("span", "tasks-spacer");
  titleRow.append(spacer, tasksSwitch(task.enabled, () => tasksSetEnabled(task)));
  titleRow.append(taskButton("", "Edit", () => tasksEdit(task)));
  titleRow.append(tasksMenu(task));
  head.append(titleRow);

  const sub = el("div", "tasks-sub");
  const when = el("span");
  when.append(el("b", null, taskScheduleText(task.schedule)));
  const zone = taskZoneText(task.schedule);
  if (zone) when.append(` · ${zone}`);
  sub.append(when);
  if (task.enabled && task.next_run_at) {
    const next = taskWhen(task.next_run_at).replace(/^(Today|Tomorrow) /, (w) => w.toLowerCase());
    sub.append(el("span", null, `next ${next}`));
  }
  sub.append(el("span", "mono", task.workspace));
  head.append(sub);
  nodes.push(head);

  const aside = el("span", "tasks-aside");
  if (run) {
    aside.append(
      tasksState.runIndex === 0 ? "showing the latest run · " : "showing the run from ",
      el("b", null, taskWhen(run.created_at))
    );
  } else {
    aside.append("no runs yet");
  }
  const rail = el("div", "work-run tasks-steps");
  (task.steps || []).forEach((step, index) => {
    const record = run ? (run.steps || [])[index] : null;
    rail.append(tasksStepRow(step, index, record));
    if (tasksState.openStep === index) rail.append(tasksStepDetail(task, step, index, run, record));
  });
  rail.append(tasksOutputsRow(task, run));
  const children = [rail];
  if (run && run.error && !(run.steps || []).some((step) => step.error)) {
    children.push(el("div", "tasks-error", run.error));
  }
  nodes.push(tasksSection("Steps", children, aside));

  if (tasksState.runs.length) {
    const list = el("div", "tasks-runs");
    const shown = tasksState.showAllRuns ? tasksState.runs : tasksState.runs.slice(0, 8);
    shown.forEach((item, index) => {
      const row = taskPressable(
        el("div", `tasks-run tone-${taskTone(item.status) || "none"}`),
        () => {
          tasksState.runIndex = index;
          tasksState.openStep = null;
          tasksPaint();
        }
      );
      row.classList.toggle("current", index === tasksState.runIndex);
      const what = el("span", "what", taskWhen(item.created_at));
      what.append(
        el("span", null, `${item.trigger === "manual" ? "you ran it" : "scheduled"} · ${TASK_WORD[item.status]}`)
      );
      row.append(
        el("span", "dot"),
        what,
        el("span", "num", taskDuration(item)),
        el("span", "num cost", taskMoney(item.cost_usd) || "—")
      );
      list.append(row);
    });
    if (!tasksState.showAllRuns && tasksState.runs.length > shown.length) {
      list.append(
        taskButton("tasks-more", `Show all ${tasksState.runs.length} runs`, () => {
          tasksState.showAllRuns = true;
          tasksPaint();
        })
      );
    }
    nodes.push(tasksSection("Runs", [list]));
  }
  return nodes;
}

function tasksSwitch(on, onToggle) {
  const button = taskButton("tasks-switch", null, onToggle, {
    role: "switch",
    "aria-checked": on ? "true" : "false",
    title: on ? "Turn the schedule off" : "Turn the schedule on",
  });
  button.append(el("span", "track"), on ? "On" : "Off");
  return button;
}

function tasksMenu(task) {
  const wrap = el("div", "tasks-menu-wrap");
  const toggle = taskButton("tasks-icon", null, () => {
    tasksState.menu = !tasksState.menu;
    tasksState.confirmDelete = false;
    tasksPaint();
  }, { "aria-label": "More", "aria-expanded": tasksState.menu ? "true" : "false" });
  toggle.append(taskIcon(TASK_DOTS));
  wrap.append(toggle);
  if (tasksState.menu) {
    const menu = el("div", "tasks-menu");
    menu.setAttribute("role", "menu");
    menu.append(taskButton("", "Duplicate", () => tasksDuplicate(task), { role: "menuitem" }));
    if (tasksState.confirmDelete) {
      menu.append(
        taskButton("danger", "Delete, with its run history", () => tasksDelete(task), {
          role: "menuitem",
        })
      );
    } else {
      menu.append(
        taskButton("danger", "Delete task…", () => {
          tasksState.confirmDelete = true;
          tasksPaint();
        }, { role: "menuitem" })
      );
    }
    wrap.append(menu);
  }
  return wrap;
}

function tasksStepRow(step, index, record) {
  const status = record ? record.status : "pending";
  const row = taskPressable(el("div", `tool-row task-step s-${status}`), () => {
    tasksState.openStep = tasksState.openStep === index ? null : index;
    tasksState.settingsStep = null;
    tasksLoadTranscript().then(() => {
      tasksState.signature = "";
      tasksPaint();
    });
    tasksState.signature = "";
    tasksPaint();
  });
  row.classList.toggle("focused", tasksState.openStep === index);
  row.append(
    el("span", `verb ${step.kind}`, step.kind === "run" ? "run" : "agent"),
    el("span", "name", step.name),
    el("span", `subject${step.kind === "run" ? " cmd" : ""}`,
      step.kind === "run" ? taskCommandLine(step.argv) : step.instruction)
  );
  let state = "";
  if (record && record.status !== "pending") {
    const length = taskDuration(record);
    if (record.status === "succeeded") state = length;
    else if (record.status === "running") state = record.kind === "agent" ? "working" : length;
    else if (record.status === "budget") state = `stopped early${record.iterations ? ` · ${record.iterations} steps` : ""}`;
    else if (record.status === "failed") state = `failed${record.error ? ` · ${record.error}` : ""}`;
    else state = TASK_WORD[record.status] || record.status;
  }
  row.append(el("span", `state tone-${taskTone(status) || "none"}`, state));
  return row;
}

function tasksSettings(task, step) {
  const dl = el("dl", "tasks-fields");
  const add = (term, value, className) => {
    dl.append(el("dt", null, term));
    const dd = el("dd", className);
    if (value instanceof Node) dd.append(value);
    else dd.textContent = value;
    dl.append(dd);
  };
  if (step.kind === "run") {
    add("Command", taskCommandLine(step.argv), "mono");
    add("Runs in", step.cwd || task.workspace, "mono");
    add("Time limit", tasksLimitText(step.timeout_s));
    if (step.run_if && step.run_if !== "ok") {
      add("Runs", TASK_RUN_IF.find(([key]) => key === step.run_if)[1]);
    }
  } else {
    const envelope = step.envelope || {};
    add("Instruction", step.instruction, "prose");
    add("Model", step.model);
    const tools = el("span", "tasks-chips");
    for (const name of envelope.tools || []) tools.append(el("span", "tasks-tag", name));
    add("Tools", tools);
    add("Can read", (envelope.files_read || []).join(", ") || "nothing", "mono");
    add("Can write", (envelope.files_write || []).join(", ") || "nothing", "mono");
    add("Internet", envelope.network === "any" ? "Yes" : "No");
    add("Step limit", `${envelope.max_iterations} steps`);
    if (step.run_if && step.run_if !== "ok") {
      add("Runs", TASK_RUN_IF.find(([key]) => key === step.run_if)[1]);
    }
  }
  return dl;
}

function tasksLimitText(seconds) {
  const s = Number(seconds) || 0;
  if (s % 3600 === 0) return s === 3600 ? "1 hour" : `${s / 3600} hours`;
  if (s % 60 === 0) return `${s / 60} min`;
  return `${s} s`;
}

// A step opened: what it did, with what it was told one click away. Before a
// step has run there is nothing to show but its settings.
function tasksStepDetail(task, step, index, run, record) {
  const ran = record && !["pending", "skipped"].includes(record.status);
  const card = el("div", "tasks-detail");
  if (!ran) {
    card.append(tasksSettings(task, step));
    if (record && record.status === "skipped") {
      card.append(el("p", "dim", "Skipped: it runs only if the steps before it finished."));
    }
    return card;
  }
  const head = el("div", "tasks-detail-head");
  const live = record.status === "running";
  const word = el("span", live ? "tasks-live" : "dim");
  if (live) word.append(el("i"), "Live");
  else word.append(TASK_WORD[record.status] || record.status);
  const extra = [
    taskDuration(record),
    record.iterations ? `${record.iterations} steps` : "",
    taskMoney(record.cost_usd),
  ].filter(Boolean);
  if (extra.length) word.append(` · ${extra.join(" · ")}`);
  const showing = tasksState.settingsStep === index;
  head.append(
    word,
    taskButton("tasks-link-button", showing ? "Hide settings" : "Settings", () => {
      tasksState.settingsStep = showing ? null : index;
      tasksPaint();
    })
  );
  card.append(head);
  if (showing) {
    const settings = tasksSettings(task, step);
    settings.classList.add("inset");
    card.append(settings);
  }
  if (step.kind === "run") {
    if (record.output) card.append(el("pre", `tasks-output${record.status === "failed" ? " bad" : ""}`, record.output));
    else card.append(el("p", "dim", record.error || "It printed nothing."));
  } else {
    card.append(tasksTrace(run, index, record));
  }
  return card;
}

// An agent step's transcript as the work rail draws it: a row per call, its
// result one click away, and what the model said between them in full.
function tasksTrace(run, index, record) {
  const key = `${run.id}:${index}`;
  const data = tasksState.transcripts.get(key);
  const box = el("div", "tasks-trace-box");
  if (!data) {
    box.append(el("p", "dim", "Reading…"));
    return box;
  }
  if (!data.messages) {
    box.append(el("p", "dim", "This step's transcript is no longer kept."));
    return box;
  }
  const results = new Map();
  for (const message of data.messages) {
    if (message.role === "tool") results.set(message.tool_call_id, message);
  }
  // The pieces in order, before folding: a call, a thought, or prose. The
  // opening user turn is the instruction, which Settings already shows.
  const pieces = [];
  for (const message of data.messages) {
    if (message.role !== "assistant") continue;
    if (message.reasoning) pieces.push({ kind: "thought", text: message.reasoning });
    if (message.content) pieces.push({ kind: "say", text: messageText(message.content) });
    for (const call of message.tool_calls || []) {
      pieces.push({ kind: "call", call, result: results.get(call.id) });
    }
  }
  const calls = pieces.filter((piece) => piece.kind === "call");
  const unfolded = tasksState.unfolded.has(key);
  const fold = !unfolded && calls.length > TRACE_HEAD + TRACE_TAIL + 2;
  const hidden = new Set(fold ? calls.slice(TRACE_HEAD, calls.length - TRACE_TAIL) : []);

  let rail = null;
  let folded = false;
  const flush = () => {
    if (rail) box.append(rail);
    rail = null;
  };
  for (const piece of pieces) {
    if (piece.kind === "say") {
      flush();
      const say = el("div", "turn agent md tasks-say");
      renderMarkdown(piece.text, say);
      box.append(say);
      continue;
    }
    if (!rail) rail = el("div", "work-run tasks-trace");
    if (piece.kind === "thought") {
      const row = el("div", "think-row");
      row.append(el("span", "label", "thinking"), el("span", "preview", previewLine(plainPreview(piece.text))));
      rail.append(row);
      continue;
    }
    if (hidden.has(piece)) {
      if (!folded) {
        folded = true;
        const more = taskPressable(el("div", "think-row tasks-fold"), () => {
          tasksState.unfolded.add(key);
          tasksPaint();
        });
        more.append(el("span", "label", "…"), el("span", "preview", `${hidden.size} more calls`));
        rail.append(more);
      }
      continue;
    }
    tasksCallRow(rail, key, piece.call, piece.result);
  }
  if (record.status === "running") {
    if (!rail) rail = el("div", "work-run tasks-trace");
    const row = el("div", "think-row tasks-working");
    row.append(el("span", "label", "working"), el("span", "preview", "…"));
    rail.append(row);
  }
  flush();
  if (!pieces.length && record.status !== "running") box.append(el("p", "dim", "It did nothing."));
  return box;
}

function tasksCallRow(rail, key, call, result) {
  const fn = call.function || {};
  let args = {};
  try {
    args = fn.arguments ? JSON.parse(fn.arguments) : {};
  } catch {
    args = {};
  }
  const name = fn.name || "tool";
  const id = `${key}:${call.id}`;
  const failed = result && result.exit_code !== undefined && result.exit_code !== null && result.exit_code !== 0;
  const outcome = !result ? "running" : failed ? "failed" : "done";
  const row = taskPressable(el("div", `tool-row outcome-${outcome}`), () => {
    if (tasksState.opened.has(id)) tasksState.opened.delete(id);
    else tasksState.opened.add(id);
    tasksPaint();
  });
  row.append(
    el("span", "verb", toolVerb(name, args)),
    el("span", "subject", toolSubject(name, args)),
    el("span", "state", !result ? "" : failed ? "failed" : result.content ? humanBytes(result.content) : "")
  );
  rail.append(row);
  if (tasksState.opened.has(id)) {
    row.classList.add("focused");
    const body = result ? unwrapUntrusted(messageText(result.content)) : "";
    rail.append(el("pre", `tasks-output tasks-result${failed ? " bad" : ""}`,
      body ? (body.length > 6000 ? `${body.slice(0, 6000)}\n…` : body) : "No result yet."));
  }
}

function tasksOutputsRow(task, run) {
  const row = el("div", "tool-row task-step task-outputs");
  const present = run && run.outputs_present;
  row.classList.add(`s-${run && present ? "succeeded" : "pending"}`);
  row.append(el("span", "verb leaves", "leaves"), el("span", "name"));
  const files = el("span", "subject tasks-files");
  if (!(task.outputs || []).length) files.append(el("span", "dim", "nothing declared"));
  for (const name of task.outputs || []) {
    const there = Array.isArray(present) ? present.includes(name) : null;
    files.append(el("span", there === true ? "there" : there === false ? "missing" : "", `${there ? "✓ " : ""}${name}`));
  }
  row.append(files, el("span", "state"));
  return row;
}

// -- acting on a task ---------------------------------------------------------------

async function tasksAct(work) {
  if (tasksState.busy) return;
  tasksState.busy = true;
  tasksState.message = "";
  tasksPaint();
  try {
    await work();
  } catch (error) {
    tasksState.message = error.message;
  } finally {
    tasksState.busy = false;
    tasksState.signature = "";
    tasksPaint();
    // A run started or stopped changes how fast the view should follow it.
    tasksTick();
  }
}

function tasksSetEnabled(task) {
  tasksAct(async () => {
    await api("PATCH", `/api/tasks/${task.id}`, { enabled: !task.enabled });
    await tasksLoad();
    await tasksLoadOpen();
  });
}

function tasksRun(task) {
  const inputs = {};
  for (const control of $("tasks-dock").querySelectorAll("[data-input]")) {
    const value = control.value.trim();
    if (value) inputs[control.dataset.input] = value;
  }
  tasksAct(async () => {
    await api("POST", `/api/tasks/${task.id}/run`, { inputs });
    await tasksLoad();
    await tasksLoadOpen();
    tasksState.runIndex = 0;
    tasksState.openStep = null;
  });
}

function tasksStop(task) {
  tasksAct(async () => {
    await api("POST", `/api/tasks/${task.id}/stop`, {});
    await tasksLoad();
    await tasksLoadOpen();
  });
}

function tasksDelete(task) {
  tasksAct(async () => {
    await api("DELETE", `/api/tasks/${task.id}`);
    await tasksLoad();
    Object.assign(tasksState, { view: "overview", taskId: null, menu: false, confirmDelete: false });
  });
}

// -- the dock: the composer's place ---------------------------------------------------

function tasksPaintDock() {
  const dock = $("tasks-dock");
  const task = tasksState.view === "task" ? taskById(tasksState.taskId) : null;
  if (!tasksState.open || (!task && !tasksState.draft)) {
    dock.classList.add("hidden");
    dock.replaceChildren();
    return;
  }
  dock.classList.remove("hidden");
  const bar = el("div", "tasks-bar");
  const lead = el("div", "tasks-bar-lead");
  if (tasksState.message) {
    lead.append(el("span", "tasks-bar-error", tasksState.message));
  }
  if (tasksState.draft) {
    if (!tasksState.message) {
      lead.append(tasksState.draft.id ? "Editing. Nothing changes until you save." : "New task, not saved yet.");
    }
    bar.append(
      lead,
      taskButton("", "Cancel", () => tasksCancelEdit()),
      taskButton("primary", tasksState.busy ? "Saving…" : "Save", () => tasksSave(), {
        disabled: tasksState.busy ? "" : null,
      })
    );
    dock.replaceChildren(bar);
    return;
  }
  const active = taskActive(taskLatest(task));
  if (!tasksState.message) {
    if (!(task.inputs || []).length) lead.append(el("span", "dim", "Runs on its schedule, or now."));
    for (const item of task.inputs || []) {
      const label = el("label", "tasks-input");
      label.append(item.name);
      let control;
      if (item.values && item.values.length) {
        control = document.createElement("select");
        if (!item.required) control.append(taskNode("option", null, "—", { value: "" }));
        for (const value of item.values) {
          const option = taskNode("option", null, value, { value });
          if (value === item.default) option.selected = true;
          control.append(option);
        }
      } else {
        control = taskNode("input", null, undefined, {
          value: item.default || "",
          placeholder: item.required ? "required" : "optional",
          spellcheck: "false",
        });
      }
      control.dataset.input = item.name;
      control.setAttribute("aria-label", item.name);
      label.append(control);
      lead.append(label);
    }
  }
  bar.append(lead);
  if (active) bar.append(taskButton("", "Stop run", () => tasksStop(task)));
  const run = taskButton("primary tasks-run-now", null, () => tasksRun(task), {
    disabled: active || tasksState.busy ? "" : null,
  });
  run.append(taskIcon(TASK_PLAY), active ? "Running" : "Run now");
  bar.append(run);
  dock.replaceChildren(bar);
}

// -- the editor -------------------------------------------------------------------------
//
// The page you read becomes the form: the title is a field, the schedule and
// the paths are sentences with fields in them, and each step opens as a card of
// fields on the same rail. The draft is the contract's shape throughout, so
// saving is sending it.

function tasksBlankStep(kind) {
  const catalog = tasksState.catalog || { models: [], tools: [], maxIterations: 40 };
  const count = (tasksState.draft.steps || []).length + 1;
  if (kind === "run") {
    return { kind: "run", name: `step-${count}`, argv: [], timeout_s: 600, _command: "" };
  }
  const model = catalog.models[0] || "";
  const wanted = ["fs_read", "fs_list", "fs_write", "web_fetch"];
  return {
    kind: "agent",
    name: `step-${count}`,
    instruction: "",
    model,
    reasoning: null,
    envelope: {
      tools: wanted.filter((name) => catalog.tools.includes(name)),
      files_read: [TASK_RUN_DIR],
      files_write: [TASK_RUN_DIR],
      network: "none",
      models: model ? [model] : [],
      max_iterations: Math.min(catalog.maxIterations, 40),
    },
  };
}

function tasksDraftOf(task) {
  const draft = JSON.parse(JSON.stringify(task));
  for (const step of draft.steps || []) {
    if (step.kind === "run") step._command = taskCommandLine(step.argv);
  }
  return draft;
}

async function tasksEdit(task) {
  try {
    await tasksLoadCatalog();
  } catch (error) {
    tasksState.message = error.message;
  }
  Object.assign(tasksState, {
    draft: tasksDraftOf(task),
    openStep: null,
    adding: false,
    menu: false,
    message: "",
  });
  tasksState.signature = "";
  tasksPaint();
}

async function tasksNew(from) {
  if (!tasksState.open) tasksShow();
  try {
    await tasksLoadCatalog();
  } catch (error) {
    tasksState.message = error.message;
  }
  const draft = from
    ? tasksDraftOf({ ...from, name: `${from.name} copy` })
    : {
        name: "",
        workspace: "",
        run_root: "",
        inputs: [],
        steps: [],
        schedule: { kind: "daily", time: "06:00", timezone: taskBrowserZone() },
        outputs: [],
        enabled: true,
      };
  for (const field of ["id", "account", "created_at", "updated_at", "next_run_at", "last_run_at"]) {
    delete draft[field];
  }
  Object.assign(tasksState, {
    view: "task",
    taskId: "new",
    runs: [],
    draft,
    openStep: null,
    adding: !draft.steps.length,
    menu: false,
    message: "",
  });
  tasksState.signature = "";
  tasksPaint();
  $("tasks-view").scrollTop = 0;
  const name = $("tasks-content").querySelector(".tasks-title-input");
  if (name) name.focus();
}

function tasksDuplicate(task) {
  tasksNew(task);
}

function tasksCancelEdit() {
  const wasNew = !tasksState.draft.id;
  Object.assign(tasksState, { draft: null, adding: false, openStep: null, message: "" });
  if (wasNew) tasksOverview();
  else {
    tasksState.signature = "";
    tasksPaint();
  }
}

// Fields write straight into the draft, so a repaint for a structural change
// (a step added or moved) never loses what was typed.
function tasksField(control, read, write) {
  control.value = read() ?? "";
  control.addEventListener("input", () => write(control.value));
  control.addEventListener("change", () => write(control.value));
  return control;
}

function tasksInput(attrs, read, write) {
  return tasksField(taskNode("input", attrs.className, undefined, { spellcheck: "false", ...attrs, className: null }), read, write);
}

function tasksSelect(options, read, write, attrs) {
  const control = taskNode("select", null, undefined, attrs);
  for (const [value, label] of options) control.append(taskNode("option", null, label, { value }));
  return tasksField(control, () => String(read()), write);
}

function tasksPaintEditor() {
  const draft = tasksState.draft;
  const nodes = [];

  const head = el("div", "tasks-head");
  const titleRow = el("div", "tasks-title-row");
  titleRow.append(
    tasksInput({ className: "tasks-title tasks-title-input", placeholder: "Name this task", "aria-label": "Task name" },
      () => draft.name, (value) => {
        draft.name = value;
        tasksPaintRail();
      })
  );
  titleRow.append(el("span", "tasks-spacer"), tasksSwitch(draft.enabled !== false, () => {
    draft.enabled = draft.enabled === false;
    tasksPaintEditor();
  }));
  head.append(titleRow);
  nodes.push(head);

  nodes.push(tasksSection("When and where", [tasksSentences(draft)]));

  const rail = el("div", "work-run tasks-steps editing");
  draft.steps.forEach((step, index) => {
    rail.append(tasksEditRow(step, index));
    if (tasksState.openStep === index) rail.append(tasksStepEditor(step));
  });
  const add = taskPressable(el("div", "tool-row task-step task-add"), () => {
    tasksState.adding = !tasksState.adding;
    tasksPaintEditor();
  });
  add.append(el("span", "verb", "+ Add a step"));
  rail.append(add);
  if (tasksState.adding) {
    const choose = el("div", "tasks-detail tasks-choose");
    for (const [kind, label, blurb] of [
      ["run", "Command", "Runs exactly as written, as you."],
      ["agent", "Agent", "Works from an instruction, inside limits you set."],
    ]) {
      const button = taskButton(`tasks-kind ${kind}`, null, () => {
        draft.steps.push(tasksBlankStep(kind));
        tasksState.openStep = draft.steps.length - 1;
        tasksState.adding = false;
        tasksPaintEditor();
      });
      button.append(el("b", null, label), el("small", null, blurb));
      choose.append(button);
    }
    rail.append(choose);
  }
  const outputs = el("div", "tool-row task-step task-outputs s-pending");
  outputs.append(el("span", "verb leaves", "leaves"), el("span", "name"));
  const files = el("span", "subject");
  files.append(
    tasksInput({ className: "mono tasks-wide", placeholder: "report.md, result.json", "aria-label": "Files a run leaves" },
      () => (draft.outputs || []).join(", "), (value) => {
        draft.outputs = taskList(value);
      })
  );
  outputs.append(files, el("span", "state"));
  rail.append(outputs);
  nodes.push(tasksSection("Steps", [rail]));

  $("tasks-content").replaceChildren(...nodes);
  tasksPaintDock();
}

function tasksSentences(draft) {
  const box = el("div", "tasks-sentences");
  const schedule = draft.schedule;
  const line = (label, ...controls) => {
    const row = el("div", "tasks-sentence");
    row.append(el("span", "k", label), ...controls);
    box.append(row);
    return row;
  };

  const kind = tasksSelect(
    [["daily", "every day"], ["weekly", "every week"], ["interval", "on repeat"]],
    () => schedule.kind,
    (value) => {
      if (value === schedule.kind) return;
      const inputs = schedule.inputs;
      draft.schedule =
        value === "interval"
          ? { kind: value, minutes: 60 }
          : { kind: value, time: schedule.time || "06:00", timezone: schedule.timezone || taskBrowserZone() };
      if (value === "weekly") draft.schedule.weekday = 0;
      if (inputs) draft.schedule.inputs = inputs;
      tasksPaintEditor();
    },
    { "aria-label": "How often" }
  );
  const when = [kind];
  if (schedule.kind === "weekly") {
    when.push("on", tasksSelect(TASK_DAYS.map((day, i) => [String(i), day]), () => schedule.weekday,
      (value) => { schedule.weekday = Number(value); }, { "aria-label": "Day" }));
  }
  if (schedule.kind === "interval") {
    const options = [...TASK_INTERVALS];
    if (!options.some(([m]) => m === schedule.minutes)) options.push([schedule.minutes, `${schedule.minutes} minutes`]);
    when.push("every", tasksSelect(options.map(([m, label]) => [String(m), label]), () => schedule.minutes,
      (value) => { schedule.minutes = Number(value); }, { "aria-label": "Interval" }));
  } else {
    when.push("at", tasksInput({ type: "time", "aria-label": "Time" }, () => schedule.time,
      (value) => { schedule.time = value; }));
    when.push(tasksInput({ className: "tasks-zone", "aria-label": "Time zone" }, () => schedule.timezone,
      (value) => { schedule.timezone = value.trim(); }));
  }
  line("Runs", ...when);
  line("Works in", tasksInput({ className: "mono tasks-path", placeholder: "/home/you/projects/site", "aria-label": "Workspace" },
    () => draft.workspace, (value) => { draft.workspace = value.trim(); }));
  const root = line("Keeps runs in", tasksInput({ className: "mono tasks-path", placeholder: "/home/you/site-runs", "aria-label": "Run folder" },
    () => draft.run_root, (value) => { draft.run_root = value.trim(); }));
  const hint = el("span", "tasks-hint");
  hint.append("Each run gets its own folder here, outside the workspace. Steps call it ",
    el("code", null, TASK_RUN_DIR), ".");
  root.append(hint);

  const asks = line("Asks for");
  draft.inputs.forEach((item, index) => {
    const card = el("span", "tasks-input-edit");
    card.append(
      tasksInput({ className: "mono tasks-input-name", placeholder: "name", "aria-label": "Input name" },
        () => item.name, (value) => { item.name = value.trim(); }),
      tasksInput({ placeholder: "choices, comma-separated (or any value)", "aria-label": "Choices" },
        () => (item.values || []).join(", "), (value) => {
          const values = taskList(value);
          if (values.length) item.values = values;
          else delete item.values;
        }),
      tasksInput({ placeholder: "default", className: "tasks-input-default", "aria-label": "Default" },
        () => item.default, (value) => {
          if (value.trim()) item.default = value.trim();
          else delete item.default;
        }),
      taskButton("tasks-icon small", "×", () => {
        draft.inputs.splice(index, 1);
        tasksPaintEditor();
      }, { "aria-label": `Remove ${item.name || "input"}` })
    );
    asks.append(card);
  });
  asks.append(taskButton("small", "+ Add input", () => {
    draft.inputs.push({ name: "", required: false });
    tasksPaintEditor();
  }));
  if (draft.inputs.length) {
    const fixed = line("On schedule");
    fixed.append(
      tasksInput({ className: "tasks-path", placeholder: "kind=sweep", "aria-label": "Inputs a scheduled run gets" },
        () => Object.entries(schedule.inputs || {}).map(([k, v]) => `${k}=${v}`).join(", "),
        (value) => {
          const pairs = {};
          for (const part of taskList(value)) {
            const at = part.indexOf("=");
            if (at > 0) pairs[part.slice(0, at).trim()] = part.slice(at + 1).trim();
          }
          if (Object.keys(pairs).length) draft.schedule.inputs = pairs;
          else delete draft.schedule.inputs;
        })
    );
  }
  return box;
}

function tasksEditRow(step, index) {
  const row = taskPressable(el("div", "tool-row task-step s-pending"), () => {
    tasksState.openStep = tasksState.openStep === index ? null : index;
    tasksPaintEditor();
  });
  row.classList.toggle("focused", tasksState.openStep === index);
  row.append(
    el("span", `verb ${step.kind}`, step.kind === "run" ? "run" : "agent"),
    el("span", "name", step.name || "unnamed"),
    el("span", `subject${step.kind === "run" ? " cmd" : ""}`,
      step.kind === "run" ? step._command || "" : step.instruction || "")
  );
  const grip = el("span", "tasks-grip");
  const steps = tasksState.draft.steps;
  const move = (delta) => {
    const target = index + delta;
    if (target < 0 || target >= steps.length) return;
    [steps[index], steps[target]] = [steps[target], steps[index]];
    if (tasksState.openStep === index) tasksState.openStep = target;
    else if (tasksState.openStep === target) tasksState.openStep = index;
    tasksPaintEditor();
  };
  grip.append(
    taskButton("tasks-icon small", "↑", () => move(-1), { "aria-label": "Move up", disabled: index === 0 ? "" : null }),
    taskButton("tasks-icon small", "↓", () => move(1), { "aria-label": "Move down", disabled: index === steps.length - 1 ? "" : null }),
    taskButton("tasks-icon small", "×", () => {
      steps.splice(index, 1);
      tasksState.openStep = null;
      tasksPaintEditor();
    }, { "aria-label": "Remove step" })
  );
  row.append(grip);
  return row;
}

function tasksStepEditor(step) {
  const catalog = tasksState.catalog || { models: [], tools: [], maxIterations: 500 };
  const dl = el("dl", "tasks-detail tasks-fields tasks-form");
  const add = (term, control, hint) => {
    dl.append(el("dt", null, term));
    const dd = el("dd");
    dd.append(control);
    if (hint) dd.append(el("span", "tasks-hint", hint));
    dl.append(dd);
  };
  const refresh = () => {
    // The row above the card shows the name and the command; keep it current
    // without rebuilding the card the reader is typing in.
    const rows = $("tasks-content").querySelectorAll(".task-step.focused");
    const row = rows[0];
    if (!row) return;
    row.querySelector(".name").textContent = step.name || "unnamed";
    row.querySelector(".subject").textContent =
      step.kind === "run" ? step._command || "" : step.instruction || "";
  };
  add("Name", tasksInput({ className: "mono", placeholder: "prepare", "aria-label": "Step name" },
    () => step.name, (value) => { step.name = value.trim(); refresh(); }),
    "Lower case, digits and hyphens.");
  const runIf = tasksSelect(TASK_RUN_IF, () => step.run_if || "ok", (value) => {
    if (value === "ok") delete step.run_if;
    else step.run_if = value;
  }, { "aria-label": "When it runs" });

  if (step.kind === "run") {
    add("Command", tasksInput({ className: "mono", placeholder: "nix develop -c pnpm check", "aria-label": "Command" },
      () => step._command, (value) => { step._command = value; refresh(); }),
      "Runs exactly as written, as you, without asking. Inputs arrive as NEUMAN_INPUT_<NAME>.");
    add("Runs in", tasksInput({ className: "mono", placeholder: "the workspace", "aria-label": "Working directory" },
      () => step.cwd, (value) => {
        if (value.trim()) step.cwd = value.trim();
        else delete step.cwd;
      }));
    const limits = [...TASK_LIMITS];
    if (!limits.includes(step.timeout_s)) limits.push(step.timeout_s);
    add("Time limit", tasksSelect(limits.sort((a, b) => a - b).map((s) => [String(s), tasksLimitText(s)]),
      () => step.timeout_s, (value) => { step.timeout_s = Number(value); }, { "aria-label": "Time limit" }));
    add("Run it", runIf);
    return dl;
  }

  const envelope = step.envelope;
  const instruction = taskNode("textarea", null, undefined, {
    rows: "3",
    spellcheck: "false",
    placeholder: "Read brief.md in your working directory and follow it.",
    "aria-label": "Instruction",
  });
  add("Instruction", tasksField(instruction, () => step.instruction, (value) => {
    step.instruction = value;
    refresh();
  }));
  const models = [...catalog.models];
  if (step.model && !models.includes(step.model)) models.unshift(step.model);
  add("Model", tasksSelect(models.map((m) => [m, m]), () => step.model, (value) => {
    step.model = value;
    envelope.models = value ? [value] : [];
  }, { "aria-label": "Model" }));
  const tools = el("span", "tasks-tool-pick");
  const offered = [...new Set([...catalog.tools, ...(envelope.tools || [])])];
  for (const name of offered) {
    const on = envelope.tools.includes(name);
    tools.append(taskButton("tasks-tag", name, (event) => {
      const pressed = envelope.tools.includes(name);
      envelope.tools = pressed ? envelope.tools.filter((t) => t !== name) : [...envelope.tools, name];
      event.currentTarget.setAttribute("aria-pressed", pressed ? "false" : "true");
    }, { "aria-pressed": on ? "true" : "false" }));
  }
  if (!offered.length) tools.append(el("span", "dim", "No client tools are granted to you."));
  add("Tools", tools);
  add("Can read", tasksInput({ className: "mono", placeholder: TASK_RUN_DIR, "aria-label": "Can read" },
    () => (envelope.files_read || []).join(", "), (value) => { envelope.files_read = taskList(value); }));
  add("Can write", tasksInput({ className: "mono", placeholder: TASK_RUN_DIR, "aria-label": "Can write" },
    () => (envelope.files_write || []).join(", "), (value) => { envelope.files_write = taskList(value); }),
    "Never the workspace: an agent can't change what a later command runs.");
  add("Internet", tasksSelect([["none", "No"], ["any", "Yes"]], () => envelope.network,
    (value) => { envelope.network = value; }, { "aria-label": "Internet" }));
  add("Step limit", tasksInput({ type: "number", min: "1", max: String(catalog.maxIterations), className: "tasks-short", "aria-label": "Step limit" },
    () => envelope.max_iterations, (value) => { envelope.max_iterations = Number(value) || 1; }));
  add("Run it", runIf);
  return dl;
}

// The draft in the contract's shape, or a sentence saying what to fix first.
function tasksBody(draft) {
  if (!draft.name.trim()) throw new Error("Name the task.");
  if (!draft.workspace) throw new Error("Say where it works.");
  if (!draft.run_root) throw new Error("Say where it keeps its runs.");
  if (!draft.steps.length) throw new Error("Add at least one step.");
  const steps = draft.steps.map((step, index) => {
    if (!/^[a-z][a-z0-9-]{0,39}$/.test(step.name || "")) {
      throw new Error(`Step ${index + 1}'s name must be lower case letters, digits and hyphens.`);
    }
    const copy = JSON.parse(JSON.stringify(step));
    if (copy.kind === "run") {
      copy.argv = taskSplitCommand(copy._command || "");
      if (!copy.argv.length) throw new Error(`Step ${index + 1} has no command.`);
      delete copy._command;
    } else if (!copy.instruction.trim()) {
      throw new Error(`Step ${index + 1} has no instruction.`);
    }
    return copy;
  });
  const inputs = draft.inputs.map((item) => {
    if (!item.name) throw new Error("Every input needs a name.");
    return item;
  });
  return {
    name: draft.name.trim(),
    workspace: draft.workspace,
    run_root: draft.run_root,
    // The computer the steps run on, and never inferred by the server (D112).
    // A task copied or edited keeps its own; a new one starts where the person
    // is, which is where the files it is about are.
    machine: draft.machine || localMachine,
    inputs,
    steps,
    schedule: draft.schedule,
    outputs: draft.outputs || [],
    enabled: draft.enabled !== false,
  };
}

async function tasksSave() {
  const draft = tasksState.draft;
  let body;
  try {
    body = tasksBody(draft);
  } catch (error) {
    tasksState.message = error.message;
    tasksPaintDock();
    return;
  }
  tasksState.busy = true;
  tasksState.message = "";
  tasksPaintDock();
  try {
    const saved = draft.id
      ? await api("PATCH", `/api/tasks/${draft.id}`, body)
      : await api("POST", "/api/tasks", body);
    tasksState.draft = null;
    tasksState.busy = false;
    await tasksLoad();
    await tasksOpen(saved.id);
  } catch (error) {
    tasksState.busy = false;
    tasksState.message = error.message;
    tasksPaintDock();
  }
}

// -- wiring ---------------------------------------------------------------------------

$("tasks-link").addEventListener("click", () => tasksShow());
$("sessions-link").addEventListener("click", () => tasksClose());

// The chat window may have signed in before this file ran.
if (csrf) tasksSignedIn();
