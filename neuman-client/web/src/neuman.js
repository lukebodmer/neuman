"use strict";

// The CSRF token from the login response. Held in memory only: putting it in
// localStorage would make it readable by anything that achieves script
// execution, which is the thing the token exists to survive.
let csrf = null;
// null until the first message of a draft creates one. See `showDraft`.
let activeBranchId = null;
// What the core reports as the account's working set, newest activity first,
// and the poll that keeps it fresh (D63). Up here with the other module state
// rather than beside the rail that draws it: `paintRailGrip` is called at module
// scope, it paints the badge, and the badge counts this list — a `let` declared
// further down is in its temporal dead zone at that point, which throws out of
// module evaluation and leaves the window empty.
let activeRows = [];
let activeSignature = null;
let activeTimer = null;
// What the composer has staged while there is no session to attach it to. Both
// are PATCHed onto the branch the moment `startSession` creates one, reusing
// the route where `within_reach` lives — the "hint, not a grant" check must not
// be duplicated client-side, and a second implementation is how it drifts.
//
// Up here with the other module state rather than beside `showDraft`, for the
// reason `reasoningByModel` below is: `showLogin` runs on the boot path and
// writes both, and a `let` declared further down is in its temporal dead zone
// at that point — which throws, into a `catch` that would show the login form
// as though the session had merely expired.
let draftWorkingDir = null;
let draftAttachments = [];
let agentTurn = null;      // the element tokens are being appended to
let reasoningTurn = null;
// The work run currently accepting steps, so a turn's calls and its reasoning
// draw as one block under one spine. Null between runs; `add` clears it for any
// event that is talk rather than work (D44).
let workRun = null;
let currentWorkingDir = null;
// The account this window is signed in as, for the shell card's prompt. The
// prompt is a drawing of the call, not a credential: the card says who the
// command ran as, which is a fact the client already has (the account chip's
// name) and one a terminal prompt without a user would not say.
let accountName = "";
// The working directory's development-environment state, from `/api/environment`:
// the resolver's own reason name, or null while nothing is known.
//
// Not persisted, and deliberately. This is a fact about the user's disk right
// now — a `nix-collect-garbage` turns a resolved environment into
// `env_collected` without the directory moving — so a value cached in the
// session would be a status light that lies. Probed on every change instead.
let currentEnvironment = null;
// Guards against a stale probe. Each `showWorkingDir` takes the next number and
// a reply that is not the newest is dropped: choosing two directories quickly
// otherwise lets the first answer land last and colour the control for a
// directory nobody is in.
let environmentProbe = 0;
let readablePaths = [];
// What each granted model declares about reasoning, from `/api/policy` — the
// one path a non-operator can read. Empty for a model that declares none, in
// which case no level is ever sent and the server uses the model's own default.
//
// Up here with the other module-level state rather than beside the code that
// reads it: `loadPolicy` assigns this near the top of the file, and a `let`
// declared further down is in its temporal dead zone at that point. The
// assignment threw, `loadPolicy`'s catch swallowed it, and the control kept
// stale options for every model.
let reasoningByModel = {};

// The level chosen per model, from the chips in the models panel. Keyed by
// model name and filled lazily from each model's declared default, so a model
// never chosen through the panel sends nothing and the server applies its own
// default — the non-operator path, since the panel is operator-only.
let reasoningChoice = {};
let toolCards = new Map();
// Where a scratch file came from, keyed by its path. The name is a random
// dispatch id, so it says nothing about what the model is reading; the result
// that wrote the file names the call, and a later `fs_read` of the path titles
// itself from this. View-scoped, like `toolCards` — a read of another
// branch's scratch keeps the digest, because this one did not produce it.
let scratchOrigins = new Map();

const $ = (id) => document.getElementById(id);
// The transcript being rendered into. `#log` for the branch on screen;
// a view's detached holder while a background branch's frames arrive. The
// view machinery below swaps it, and every renderer here keeps addressing
// "the log" rather than knowing which branch it is drawing (D63).
let log = $("log");
const jumpLive = $("jump-live");
// The composer's own invitation, restored when an inline edit ends. Captured
// rather than written twice: the placeholder is the instruction for the only
// box that sends a turn, and a second copy would drift from the markup.
const composerPlaceholder = $("input").placeholder;
// The pencil on a user turn. Drawn rather than typed, like every other icon in
// the composer row: a glyph changes weight and baseline with the font.
const PENCIL = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11 2.3l2.7 2.7L5.6 13.1l-3.6.8.8-3.6L11 2.3z"/></svg>';

// -- open branches -----------------------------------------------------------
//
// One view per branch this window has open. A view owns the socket, the
// transcript nodes and the render state that a live turn writes into, so a turn
// does not end when the reader looks at another branch or session — the socket
// stays open, the frames keep arriving, and they are drawn into that view's own
// transcript even while it is not mounted.
//
// The alternative is one transcript and a replay on every switch, which is what
// this deliberately is not: a running branch must land on the step it is on —
// the live tool card, the thinking card, the streaming answer — with the history
// above it, not on a reconstruction that looks like a reply arriving twice.
const views = new Map();
// The sessions this window knows about, keyed by session id: the rail's rows
// and the branch list a switcher needs. A view is keyed by *branch*, because a
// branch is the log and the socket; a session may have several.
const sessions = new Map();
// What a new branch starts with, from `/api/me`. The model/reasoning controls
// read this when their own branch has no choice yet.
let accountDefaults = { model: null, reasoning: null };
// The last `/api/policy` payload, the model inventory, and the approval mode of
// the branch on screen. Approval is per branch, so the chip cannot read it off
// the policy alone.
let policyState = null;
let modelInfo = new Map();
let branchApproval = null;
// The draft has no branch yet; its choices are staged here and written onto the
// root branch when the first message creates it.
let draftModel = null;
let draftReasoning = null;
let draftApproval = null;
// The view on screen. Null in the draft and on the login surface.
let currentView = null;
// The local branch switcher, when one fork node's menu is open. One instance
// at a time: a second node press moves it rather than opening a second panel.
let forkMenuOpen = null;
// The view the renderers are currently drawing into. Equal to `currentView` for
// the visible transcript, and to a hidden view for its own frames. `isLiveRender`
// is what tells a renderer whether it may touch the composer or the rail.
let renderView = null;
// True while transcript nodes are being moved between a holder and `#log`, so
// the scroll listener does not read a half-mounted height as the reader's intent.
let mounting = false;
// Guards the one asynchronous open: a slow GET must not replace a session the
// reader has since chosen.
let viewOpen = 0;

function isLiveRender() {
  return currentView !== null && renderView === currentView;
}


// -- step disclosures -------------------------------------------------------
//
// A tool call and a reasoning block are one row each in the timeline. The run
// has one **stage** at its foot, and the step the turn is on is shown there:
// the rows are the chain that grows upward out of the card, and the card is
// the one place the live work is read. A step change is a crossfade inside a
// fixed box (`--step-h`, D62), so the card turns into the next one without
// moving.
//
// The stage is only the *live* step's home. Every other row is a disclosure in
// place: a press mounts its detail under the row, so a reader scrolled up
// through a session opens the card where they are looking rather than
// replacing the card at the foot (D64). A detail is in exactly one home —
// `_mounted` is "stage", "inline" or null — and `stageDetail`/`openInline` are
// the only things that mount one.
//
// The row owns its detail (`_detail`); a replayed card builds its body through
// `_reveal` before it is mounted, so a card nobody opened never paid for it
// (D60).
//
// When prose starts, the run is over. Its stage folds up into a **reserved
// blank** at the end of the transcript, and the reply is written into that
// blank; the tail only follows once the reply has filled it. That is what makes
// folding the card safe at the end of a chain, which D62 said it was not. The
// invariant is a content *height*: the span from the first content child to the
// last, plus the reserve, is held constant. A position cannot be used: a short
// transcript is bottom-aligned by `#log > :first-child { margin-top: auto }`,
// so the reserve's top does not move when the content shrinks.

// The fade between two details, in milliseconds. The stylesheet's transition on
// the details is the same number; this copy is only how long the outgoing node
// is kept before it is removed.
const STEP_FADE_MS = 140;

function motionMs(node) {
  return (parseFloat(getComputedStyle(node).transitionDuration) || 0) * 1000;
}

// The blank held at the foot of the transcript by a folding stage. It is a
// zero-height element at the end of the log; its negative margin cancels the
// column gap, so its height is exactly the space being held. One per view,
// like the transcript it belongs to.
let reserveEl = null;
let reserveH = 0;
// The content height being held. Null when no blank is held. `settle` keeps it
// through the frame the fold starts on; clearing it at target zero is the bug
// the mockup caught.
let pinned = null;

// The height of the content above the reserve, excluding the auto margin that
// bottom-aligns a short conversation.
function contentSpan() {
  const kids = [...log.children].filter((child) => child !== reserveEl);
  if (!kids.length) return 0;
  const first = kids[0].getBoundingClientRect();
  const last = kids[kids.length - 1].getBoundingClientRect();
  return last.bottom - first.top;
}

function setReserve(px) {
  px = Math.max(0, px);
  reserveH = px;
  if (reserveEl) reserveEl.style.height = `${px.toFixed(2)}px`;
}

function pinBottom() {
  if (!isLiveRender() || !reserveEl) return;
  if (pinned === null) pinned = contentSpan() + reserveH;
}

function settle() {
  if (!isLiveRender() || pinned === null) return;
  const target = pinned - contentSpan();
  if (target > 0.5) {
    // The fold is making space: hold exactly that much.
    setReserve(target);
  } else if (target < -0.5) {
    // Content has grown past the held height: the blank is full, so the
    // ordinary glide to the tail takes over.
    setReserve(0);
    pinned = null;
    if (followTail) glideToTail();
  } else {
    // Exactly at the pin. This is the frame `pinBottom` itself runs in: keep
    // the pin, or the hold is dropped before the card has moved a pixel.
    setReserve(0);
  }
  // The fold's removal and the reserve's growth can move the scroll position
  // a pixel or two under the reader. Whatever the browser clamped to is where
  // the transcript is now, not an upward move by the reader.
  lastScrollTop = log.scrollTop;
}

// A height transition is running: settle (or glide) on every frame, so the
// reserve and the card's layout height are read from the same frame. The glide
// trails the transition a little, so a stage opening pushes the conversation
// up rather than dragging it at the transition's own speed. The reader
// may switch sessions mid-fold; the animation belongs to the transcript it
// started in, not to whatever is mounted now.
function motion(ms) {
  if (!(ms > 0) || !isLiveRender()) return;
  const view = currentView;
  const until = performance.now() + ms;
  const frame = (now) => {
    if (currentView !== view) return;
    if (pinned !== null) settle();
    else if (followTail) glideToTail();
    paintJumpLive();
    if (now < until) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// A turn can end before the reply has used the whole blank. The blank is a
// reservation, not padding: close it at the end, smoothly, with the tail
// following, so the transcript ends at the composer instead of in a void.
function releaseReserve(ms) {
  pinned = null;
  const from = reserveH;
  if (from <= 0.5) {
    setReserve(0);
    return;
  }
  if (!isLiveRender()) {
    setReserve(0);
    return;
  }
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) {
    setReserve(0);
    if (followTail) followBottom();
    paintJumpLive();
    return;
  }
  const view = currentView;
  const start = performance.now();
  const frame = (now) => {
    if (currentView !== view) return;
    const t = Math.min(1, (now - start) / Math.max(1, ms));
    const ease = 1 - Math.pow(1 - t, 3);
    setReserve(from * (1 - ease));
    if (followTail) followBottom();
    paintJumpLive();
    if (t < 1) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function runFor(row) {
  const run = row && row.parentNode;
  return run && run.classList && run.classList.contains("work-run") ? run : null;
}

// The run's stage, created on the first detail that needs one. It is the last
// child of the run: rows are inserted in front of it, so the chain and the rail
// grow upward out of it while its own box stays at the foot.
function stageFor(run) {
  if (!run) return null;
  if (!run._stage) {
    const stage = el("div", "step-stage collapsed");
    run._stage = stage;
    run.appendChild(stage);
  }
  return run._stage;
}

function revealDetail(detail) {
  const reveal = detail._reveal;
  if (reveal) {
    detail._reveal = null;
    reveal();
  }
}

// Put a row's detail in the run's stage and crossfade it over the one before.
// The detail is moved, never copied: one node, one home (the row's `_detail`).
function stageDetail(run, row) {
  const detail = row._detail;
  const stage = stageFor(run);
  if (!detail || !stage) return;
  if (stage._detail === detail) return;
  revealDetail(detail);
  // Ghosts from an earlier fade are only kept for the fade's duration; a new
  // step starts from the one detail that is actually current.
  for (const child of [...stage.children]) {
    if (child !== stage._detail) child.remove();
  }
  clearTimeout(stage._fadeTimer);
  const previous = stage._detail;
  // The entering element is inserted transparent and read once, so the browser
  // has a rendered start value before the class comes off; without the read both
  // writes land in one style recalculation and the card simply appears.
  detail.classList.remove("collapsed", "leaving");
  detail.classList.add("entering");
  stage.appendChild(detail);
  detail.getBoundingClientRect();
  detail.classList.remove("entering");
  if (previous && previous !== detail) previous.classList.add("leaving");
  if (stage._row && stage._row !== row) stage._row.classList.remove("focused");
  stage._row = row;
  stage._detail = detail;
  detail._mounted = "stage";
  row.classList.add("focused");
  stage._fadeTimer = setTimeout(() => {
    if (previous && previous.parentNode === stage) previous.remove();
    if (previous) {
      previous.classList.add("collapsed");
      previous._mounted = null;
    }
  }, STEP_FADE_MS + 80);
  paintJumpLive();
}

function revealStage(stage) {
  // A manual open can be in a run far above the tail; bring the card into view
  // rather than staging it somewhere the reader cannot see. This is motion on a
  // click, not a streamed change, so smooth is the right reading — unless the
  // reader asked for reduced motion, where an instant jump is the point.
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  stage.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
}

// A history row's detail lives under the row, not in the run's stage. The
// detail is moved there, built on first open if it was replayed.
function openInline(row) {
  const detail = row._detail;
  if (!detail) return;
  if (detail._mounted === "stage") {
    // The row just stopped being the live step and its detail is still in the
    // stage (or fading out of it). A press from history takes it out of that
    // home now rather than doing nothing until the fade timer fires.
    const stage = detail.parentNode;
    if (stage && stage._detail === detail) {
      stage._detail = null;
      if (stage._row) stage._row.classList.remove("focused");
      stage._row = null;
    }
    detail.remove();
    detail._mounted = null;
  }
  revealDetail(detail);
  detail.classList.add("collapsed");
  if (detail.parentNode !== row) row.after(detail);
  detail._mounted = "inline";
  row.classList.add("focused");
  // Insert closed, read once, then open: the height transition has a rendered
  // start value, and the content below moves as the card grows.
  void detail.offsetHeight;
  detail.classList.remove("collapsed");
  paintJumpLive();
}

function closeInline(row) {
  const detail = row._detail;
  if (!detail || detail._mounted !== "inline") return;
  detail.classList.add("collapsed");
  row.classList.remove("focused");
  setTimeout(() => {
    if (detail._mounted === "inline" && detail.classList.contains("collapsed")) {
      detail.remove();
      detail._mounted = null;
    }
  }, motionMs(detail) + 80);
}

// A press is the row's own disclosure. The live step's row toggles the stage at
// the run's foot; every other row opens its detail under itself, where the
// reader is looking. A press in history is a review gesture: it must not drag
// the live edge, and it must not touch the live stage (D64).
function pressRow(row) {
  const run = runFor(row);
  if (!run || !row._detail) return;
  const live = run._live && run._step === row && run._stage;
  if (live) {
    if (run._stage.classList.contains("collapsed")) {
      run._stage.classList.remove("collapsed");
      motion(motionMs(run._stage));
    } else {
      run._stage.classList.add("collapsed");
      motion(motionMs(run._stage));
    }
    return;
  }
  followTail = false;
  if (pinned !== null) {
    pinned = null;
    setReserve(0);
  }
  if (row._detail._mounted === "inline") closeInline(row);
  else openInline(row);
}

// The run follows its newest step: staging a row crossfades its detail over the
// one before it, so a turn's chain reads as one card advancing rather than a
// wall of cards. The new row has already been inserted in front of the stage by
// `appendWork`; a stage the reader closed opens again for the new step.
function followStep(row, { intoView = false } = {}) {
  const run = runFor(row);
  if (!run || !row._detail) return;
  const stage = stageFor(run);
  if (!stage) return;
  const wasClosed = stage.classList.contains("collapsed");
  stageDetail(run, row);
  run._step = row;
  run._live = true;
  if (wasClosed) {
    stage.classList.remove("collapsed");
    motion(motionMs(stage));
  }
  if (intoView) {
    // A stage that was closed is still at zero height when this runs; bring it
    // into view once it has opened, or scrollIntoView targets the fold's edge
    // and leaves the approve/deny row below the viewport (N89).
    const delay = wasClosed ? motionMs(stage) + 20 : 0;
    if (delay) setTimeout(() => revealStage(stage), delay);
    else revealStage(stage);
  }
}

// The live stage folding into the reserved blank when the run ends. The reply
// is appended after the run and written into the held space; the pin is what
// keeps the content above it still while the card folds.
function foldStageForOutput(run) {
  if (!run) return;
  const stage = run._stage;
  run._live = false;
  if (!stage) return;
  const open = !stage.classList.contains("collapsed");
  const live = isLiveRender();
  const view = renderView;
  // A short transcript has no scroll position to clamp; the frame-by-frame
  // reserve keeps its auto margin still. An overflowing one does: growing the
  // reserve a frame behind the stage lets the browser pull the scroll up before
  // the blank exists. There the reserve is funded *before* the card shrinks, so
  // the content height only ever grows until the stage is gone.
  const overflow = live && log.scrollHeight > log.clientHeight + 1;
  if (open && live) {
    if (overflow) {
      if (view) view.foldingStage = stage;
      setReserve(reserveH + stage.getBoundingClientRect().height);
    } else {
      pinBottom();
    }
  }
  stage.classList.add("collapsed");
  const finish = () => {
    if (view && view.foldingStage === stage) view.foldingStage = null;
    // The reader may have switched sessions while the fold ran. The blank is
    // part of the view it was folded in; settling it through the module names
    // would settle whatever transcript is on screen now. A view that is no
    // longer live simply loses the blank it was holding.
    const still = live && isLiveRender() && renderView === view;
    // Pin before the stage leaves the grid: its row gap goes with it, and the
    // reserve has to take both or the content above settles by the gap.
    if (still && overflow) pinned = contentSpan() + reserveH;
    if (stage._row) stage._row.classList.remove("focused");
    if (stage._detail) {
      stage._detail.remove();
      stage._detail.classList.add("collapsed");
      stage._detail._mounted = null;
      stage._detail = null;
    }
    stage.remove();
    if (run._stage === stage) {
      run._stage = null;
      run._step = null;
    }
    // The stage's last pixels and its grid gaps leave the layout here; the
    // reserve has to take them or the content above settles by that much.
    if (still) {
      settle();
    } else if (view) {
      view.reserveH = 0;
      view.pinned = null;
      if (view.reserveEl) view.reserveEl.style.height = "0px";
    }
    if (isLiveRender()) paintJumpLive();
  };
  if (!live || !open) {
    finish();
    return;
  }
  if (!overflow) motion(motionMs(stage));
  setTimeout(finish, motionMs(stage) + 80);
}
// Every outcome a tool row's node can wear. One class per state on the row, so
// the colour lives in the stylesheet and this file only names the state.
const TOOL_OUTCOMES = ["running", "done", "waiting", "failed", "refused"];

// The row half of a tool card, keyed like `toolCards`, so `setToolState` can
// write the one state string to the row and the detail together.
let toolRows = new Map();

// One state string, written to the detail's head and the timeline row. The two
// present the same fact differently — the row is a one-line glance and the card
// is the detail — and this is the only writer either place reads, so they cannot
// drift. `rowText` defaults to the full state because most states read the same
// in both; the terminal outcome is the exception, passed the result's size
// instead, so the timeline's right edge says what came back rather than repeating
// a status the node already colours (D44). `outcome` drives that node, and is the
// one place the row's colour is set. The write stays inside the frame handler's
// `grow()` like every other post-mount mutation, so a state change that ever
// alters the outline follows the tail (D62).
function setToolState(toolCallId, text, rowText = text, outcome = "running") {
  const card = toolCards.get(toolCallId);
  if (card) card.querySelector(".state").textContent = text;
  // A refused shell will never produce the output its caret is waiting for, so
  // the promise goes with the verdict. A failed or timed-out call arrives with
  // a result instead, and `renderResult` removes the caret there.
  if (card && outcome === "refused") {
    const waiting = card.querySelector(".shell-pending");
    if (waiting) waiting.remove();
  }
  const row = toolRows.get(toolCallId);
  if (!row) return;
  row.querySelector(".state").textContent = rowText;
  for (const kind of TOOL_OUTCOMES) {
    row.classList.toggle(`outcome-${kind}`, kind === outcome);
  }
  // A failure's row text is empty — the red node is the glance and the card has
  // the word — so the tooltip falls back to the card's state to keep `failed`
  // reachable without a mouse.
  row.title = [row._label, rowText || text].filter(Boolean).join(" · ");
}

// -- plumbing ---------------------------------------------------------------

async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  // Every unsafe request. The server re-checks this per request rather than
  // trusting the session, so a GET does not need it and a POST always does.
  if (csrf && method !== "GET") headers["x-csrf-token"] = csrf;

  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401) {
    // The session went away — a restart, an expiry, or a uid change (D17).
    // Returning to the login form is the honest response; retrying would loop.
    showLogin("Session ended. Sign in again.");
    throw new Error("unauthenticated");
  }

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    // A refusal that names several problems (a task the policy cannot
    // support) says all of them; the bare error code would say none.
    const problems = payload && Array.isArray(payload.problems) && payload.problems.join(" ");
    throw new Error(
      (payload && (payload.detail || problems || payload.error)) || `HTTP ${response.status}`
    );
  }
  return payload;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// One external link, the shape every URL in a card takes: a new tab so the
// conversation stays on screen behind it, and `noopener noreferrer` because the
// target is untrusted content fetched on the model's behalf.
function externalLink(cls, label, url) {
  const a = el("a", cls, label);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

// The composer grows with its contents, to a bound. Named rather than inline in
// the `oninput` handler because transcription writes into the box without an
// input event, and a second copy of the arithmetic would drift from this one.
function autosize(box) {
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, window.innerHeight * 0.4)}px`;
}

// Whether the transcript is following its tail. This is a *state*, not a
// measurement, and that is the fix for N141.
//
// It used to be recomputed on every mutation from the distance to the bottom
// (`scrollHeight - scrollTop - clientHeight < 120`). Distance cannot tell a
// reader who scrolled up from content that just grew, and a slow wheel tick
// moves less than the threshold — so the next streaming frame measured "at the
// bottom", snapped back, and the reader was pinned there until they flicked
// faster than the token rate. The wheel listener below flips this flag
// *synchronously*, before any queued frame can consult it, and only arriving at
// the bottom (or pressing the arrow) turns it back on.
let followTail = true;
// The last scroll position the listener saw. A programmatic follow raises this;
// content growing between the follow and the scroll event does not lower it.
// Without the distinction the listener reads "the height grew after I followed"
// as "the reader left the tail" and stops following mid-transition.
let lastScrollTop = 0;

// Distance to the bottom, for the one decision the scroll event makes: has the
// reader arrived at the tail? Kept tight — a real arrival lands at exactly zero,
// while any upward move must leave it — because a loose slack here would re-pin
// on the very tick that was trying to escape.
function atBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 2;
}

// Put the transcript's tail at the bottom and record where it landed. The
// record is what keeps the scroll listener from reading this move as the reader
// leaving the tail when the next frame has already grown the content.
function followBottom() {
  log.scrollTop = log.scrollHeight;
  lastScrollTop = log.scrollTop;
}

// The streamed follow: the transcript glides to its tail rather than jumping to
// it, on the same exponential in time as the thought card (`followThought`,
// D66). A reply growing by a line, a row inserted above the stage, the stage
// opening — each pushes the conversation up instead of snapping it there, and
// a burst of growth inside one glide only moves the target the glide is
// already closing on.
//
// `followBottom` stays the instant form for jumps the reader asked for (the
// arrow, the prompt cursor) and for the reserve's frame-exact bookkeeping.
//
// One loop per window. It belongs to the view it started in, stops the moment
// the reader leaves the tail, and stops itself once the gap is closed.
const TAIL_TAU_MS = 140;
let tailLoop = null;

function glideToTail() {
  if (!isLiveRender()) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    followBottom();
    return;
  }
  if (tailLoop) return;
  const view = currentView;
  let at = null;
  const frame = (now) => {
    if (currentView !== view || !isLiveRender() || !followTail) {
      tailLoop = null;
      return;
    }
    const dt = Math.max(1, Math.min(64, now - (at ?? now - 16)));
    at = now;
    const gap = log.scrollHeight - log.clientHeight - log.scrollTop;
    if (gap < 0.5) {
      if (gap > 0) followBottom();
      tailLoop = null;
      return;
    }
    log.scrollTop += gap * (1 - Math.exp(-dt / TAIL_TAU_MS));
    lastScrollTop = log.scrollTop;
    tailLoop = requestAnimationFrame(frame);
  };
  tailLoop = requestAnimationFrame(frame);
}

// Show the arrow only when the reader has left the tail *and* there is a tail to
// return to. The `followTail` short-circuit is not just an optimisation: while
// following there is nothing to decide, and reading `scrollHeight` here would
// force a layout on every streamed token.
function paintJumpLive() {
  if (!isLiveRender()) return;
  if (followTail) {
    jumpLive.classList.add("hidden");
    return;
  }
  jumpLive.classList.toggle("hidden", log.scrollHeight <= log.clientHeight + 1);
}

// Run something that changes the transcript's height, then follow the tail if
// the reader is still there.
//
// While a blank is held (`pinned`), growth is written into the blank instead of
// scrolling: `settle` shrinks the reserve by exactly the new height, so the
// content above it does not move until the blank is full. A card is one fixed
// stage and its body scrolls (D62), so the only heights that move over time are
// the stage folding into the reserve and the reserve being filled.
function grow(mutate) {
  const atEnd = followTail;
  const live = isLiveRender();
  const result = mutate();
  // After a layout-changing write, not before: the new height has to exist for
  // the reserve and the follow to see it. A hidden view's holder has no layout
  // to follow; its reader position is restored when it is mounted again.
  if (live) {
    if (renderView && renderView.foldingStage) {
      // The blank was funded before the stage began to shrink; leave the
      // scroll position and the reserve alone until the card is out of layout.
    } else if (pinned !== null) {
      settle();
    } else if (atEnd && reserveH <= 0.5) {
      glideToTail();
    }
    // The mutation may have made the transcript overflow for the first time, so
    // the arrow's visibility is decided here as well as on scroll.
    paintJumpLive();
  }
  return result;
}

// The same state, one scale down: a box that scrolls *inside* the transcript
// follows its own tail.
//
// `grow` follows the log, and that is all it can do — a staged card's body is a
// scrollport of its own now, so a result arriving under the arguments adds
// height *inside* a fixed box and the outer scroll has nothing to chase. The
// chain of thought is the one body that does not come through here: it arrives
// on a reading clock and eases its own follow (`followThought`, D66), because a
// write that lands instantly is the jump that clock exists to remove.
//
// The flag lives on the box because each box has its own reader decision. The
// listeners are attached lazily on first use; the body is built fresh per card,
// so no box outlives the listeners it owns.
function growInner(box, mutate) {
  if (box._followTail === undefined) {
    box._followTail = true;
    box.addEventListener("wheel", (event) => {
      if (event.deltaY < 0) box._followTail = false;
    }, { passive: true });
    box.addEventListener("scroll", () => {
      box._followTail = atBottom(box);
    }, { passive: true });
  }
  const atEnd = box._followTail;
  const result = mutate();
  if (atEnd) box.scrollTop = box.scrollHeight;
  return result;
}

// The reader's intent, captured before the frame that would undo it. `wheel`
// rather than only `scroll` because a scroll event arrives after the default
// scroll is applied — a tick smaller than the slack could re-pin on the same
// gesture that unpinned (N141).
log.addEventListener("wheel", (event) => {
  if (mounting || !isLiveRender()) return;
  if (event.deltaY < 0) {
    followTail = false;
    if (pinned !== null) {
      pinned = null;
      setReserve(0);
    }
    paintJumpLive();
  }
}, { passive: true });

// Every other way back to the tail — dragging the scrollbar, Page Down, touch —
// arrives here, and arriving at the bottom resumes following. Programmatic
// follows land at the bottom too, so they confirm the state rather than clear
// it.
log.addEventListener("scroll", () => {
  // Moving a view's nodes in or out changes the scroll position without the
  // reader touching it; that is not an intent to stop following.
  if (mounting || !isLiveRender()) return;
  const top = log.scrollTop;
  const movedUp = top < lastScrollTop - 4;
  lastScrollTop = top;
  if (atBottom(log)) {
    followTail = true;
  } else if (movedUp) {
    // A real upward move — wheel, scrollbar, Page Up. Content that grew after
    // a programmatic follow is not a move and must not stop the following.
    followTail = false;
    if (pinned !== null) {
      pinned = null;
      setReserve(0);
    }
  }
  paintJumpLive();
}, { passive: true });

jumpLive.addEventListener("click", () => {
  if (!currentView) return;
  // The arrow is the way back to the tail, so it also ends a review: leaving
  // the message under review marked while the view returns to the draft would
  // be two states at once.
  markPromptCursor(null);
  followTail = true;
  followBottom();
  paintJumpLive();
});

// -- the prompt cursor -------------------------------------------------------
//
// Up from the composer walks back through the user's own messages and Down
// walks forward again; Down past the newest returns to the tail and the draft.
// It is the other end of the axis `jumpLive` is on: one leaves the bottom on
// purpose, the other comes back.
//
// Only the *first* Up is gated: the caret has to be at offset 0 with nothing
// selected, or a multi-line draft loses its own caret movement. Down from the
// draft is left to the browser — there is nothing newer than the draft to walk
// to. Once the cursor is active the arrows belong to it, the textarea is
// neither read nor written, and the draft survives the whole trip untouched.
let promptCursor = null;

function promptMessages() {
  // Queried fresh on every press: a message that arrives while the reader is
  // reviewing (a steer, another tab) is part of the walk from then on.
  return [...log.querySelectorAll(".turn.user")];
}

function markPromptCursor(node) {
  if (promptCursor === node) return;
  if (promptCursor) promptCursor.classList.remove("current");
  promptCursor = node;
  if (node) node.classList.add("current");
}

function showPrompt(node) {
  markPromptCursor(node);
  // A deliberate departure from the tail: without this the next streamed token
  // drags the reader back to the bottom mid-review.
  followTail = false;
  node.scrollIntoView({ block: "start" });
  paintJumpLive();
}

function endPromptCursor() {
  markPromptCursor(null);
  followTail = true;
  followBottom();
  paintJumpLive();
}

// Append one event to the timeline. Everything routed through here is
// conversation — prose, a note, an error — so it also ends the work run: a run
// is exactly the working *between* two pieces of talk, and a paragraph mid-turn
// splits it (D44). Steps never come through here; `appendWork` takes them.
//
// Prose is also what makes the run's last card go: the stage folds into the
// reserved blank before the bubble is appended, and the bubble is written into
// that blank. `flush` cancels the column gap for that one bubble: the gap is
// itself a height change, and the pin has no space in it yet when the empty
// bubble arrives.
function add(node, { flush = false } = {}) {
  const run = workRun;
  workRun = null;
  // Talk ends both open bubbles as well as the run: a thought that arrives
  // after prose belongs to a new block, not to the one above the prose.
  agentTurn = null;
  endThought();
  const folding = run && run._stage && !run._stage.classList.contains("collapsed");
  const foldMs = folding ? motionMs(run._stage) : 0;
  if (flush && folding) node.classList.add("flush");
  if (run) foldStageForOutput(run);
  // The first bubble of a folded run is written into the blank, not over the
  // folding card. Rendering its first token immediately would attach that line
  // to the stage's moving bottom edge and slide it up the card's whole height;
  // hold the render until the fold has landed. The wait is one fold (~160ms),
  // and only this bubble pays it.
  if (flush && folding) {
    node._foldHold = true;
    setTimeout(() => {
      node._foldHold = false;
      if (node.dataset.raw) renderMarkdown(node.dataset.raw, node);
      if (node.parentNode === log) grow(() => {});
    }, foldMs + 120);
  }
  return grow(() => {
    appendTranscript(node);
    return node;
  });
}

// The reply is re-rendered from source, because markdown cannot be appended to,
// but at most once per frame. A fast model delivers several tokens a frame, and
// rebuilding the bubble for each one is layout the eye never sees — each a
// small jump in the transcript's height for the glide to chase. Coalesced, a
// frame's tokens are one rebuild and one new target.
//
// The node is captured rather than read from `agentTurn`, which belongs to the
// view being drawn when the frame arrived; the render lands in whichever log
// holds the bubble, and only the visible one is followed. A bubble written into
// a folded card's blank waits for the fold to land before it first paints; see
// `add`, which renders it then.
function paintReply(node) {
  if (node._paint) return;
  node._paint = requestAnimationFrame(() => {
    node._paint = null;
    if (node._foldHold) return;
    const render = () => renderMarkdown(node.dataset.raw, node);
    if (node.parentNode === log && isLiveRender()) grow(render);
    else render();
  });
}

// Append a transcript node before the view's reserve. The reserve stays the last
// child: it is the space at the end, and appending after it would put the blank
// above the content that is supposed to fill it. A view with no reserve is the
// draft or the login surface.
function appendTranscript(node) {
  if (reserveEl && reserveEl.parentNode === log) {
    log.insertBefore(node, reserveEl);
  } else {
    log.appendChild(node);
    if (reserveEl && reserveEl.parentNode !== log) log.appendChild(reserveEl);
  }
}

// Append a step — a tool call or a reasoning block — to the turn's open run,
// opening one if this is the first. Keeping steps out of `add` is what stops a
// run from being cut by its own members.
function appendWork(node, { animate = true } = {}) {
  if (!workRun) {
    workRun = el("div", "work-run");
    grow(() => appendTranscript(workRun));
  }
  // Rows are the chain, so they go in front of the stage: the stage is the foot
  // of the run and the chain grows upward out of it. Replay opts out of the
  // arrival animation: a session opening is not a sequence of steps arriving,
  // and a hundred rows fading in at once is a flash.
  if (animate) node.classList.add("arrive");
  return grow(() => {
    if (workRun._stage) workRun.insertBefore(node, workRun._stage);
    else workRun.appendChild(node);
    return node;
  });
}

// A note is a line in a conversation, so there has to be a conversation to put
// it in. The draft and the login surface have none — `resetRender` clears
// `renderView` and points `log` at an empty `#log` — and in the draft that node
// is a zero-height row (`body.drafting`), so a note appended to it is not merely
// out of place: it is clipped to a sliver of text at the very top of the window,
// above the heading and the box. That is where "Using <model>." was landing
// every time a model was chosen before the first message.
//
// The predicate is `renderView`, not `drafting()`, because a background branch
// draws into its own holder while the reader sits in the draft: its
// "Disconnected." or failed-frame note is real, and would be lost with it.
//
// Feedback about something staged before the first message belongs to the
// control that did it instead: the models panel repaints from the state it has
// just read, `showWorkingDir` and `showAttachments` write the chips in the
// composer, and `saveWorkingDir` already returns without a note in the draft for
// the same reason.
function note(text) {
  if (!renderView) return null;
  return add(el("div", "turn note", text));
}

function previewLine(text) {
  return String(text || "").replace(/\s+/g, " ").trim().slice(0, 120);
}

// Markdown markers are syntax, not speech: the row's one-line preview should say
// what the thought is about, not show the `**` that would make it bold. This is
// a stripper, not a parser — the preview is 120 characters and the body it
// summarises is the rendered truth.
function plainPreview(text) {
  return String(text || "")
    // Fences first: a code block's contents are text, and its backticks are not.
    .replace(/^[ \t]*(?:```+|~~~+)[^\n]*$/gm, "")
    .replace(/`([^`\n]*)`/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    // A table's delimiter row is syntax, and its pipes are cell borders: a
    // preview that stops before the body should still read as words.
    .replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, "")
    .replace(/\|/g, " ")
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|(?:[-*+]|\d+[.)])\s+)/gm, "")
    .replace(/\*\*|__|~~|\*|_/g, "");
}

// One reasoning block: a compact row in the timeline and the full text as the
// detail the row opens. Both are built here and updated together by the
// `reasoning` frame, so the text lives in one place. The row says "thinking" and
// a one-line preview; while the block is the run's live step it is staged at the
// foot, and once it is history the same press opens it under the row. A reopened
// session draws the same row from the display projection's `reasoning` field
// (D46).
function reasoningBlock({ animate = true } = {}) {
  const row = el("div", "think-row");
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  row.appendChild(el("span", "label", "thinking"));
  const preview = el("span", "preview", "");
  row.appendChild(preview);

  const detail = el("div", "reason-detail step-detail collapsed");
  // The thought is markdown like the reply, so it carries the same `md` scope.
  // Its ink stays dim — the trace is still a step behind the answer — but it is
  // no longer slanted as a whole, because emphasis inside a block that is
  // already italic has nothing left to say.
  const body = el("div", "reasoning-body md");
  detail.appendChild(body);

  // The detail is detached until the row is mounted, and `reasoningTurn` is the
  // row, so the frame handler writes its raw text through `_body`.
  row._body = body;
  row._preview = preview;
  row._detail = detail;

  // The card follows its own tail while a thought streams into it. A wheel up,
  // or a real upward move, hands the scroll back to the reader; arriving at the
  // bottom hands it back to the card (N141). The upward move is compared against
  // the position this code last wrote, because an eased follow is never "at the
  // bottom" while it is moving.
  row._follow = true;
  row._lastTop = 0;
  row._shown = 0;
  body.addEventListener("wheel", (event) => {
    if (event.deltaY < 0) row._follow = false;
  }, { passive: true });
  body.addEventListener("scroll", () => {
    const top = body.scrollTop;
    const movedUp = top < row._lastTop - 4;
    row._lastTop = top;
    if (atBottom(body)) row._follow = true;
    else if (movedUp) row._follow = false;
  }, { passive: true });

  row.onclick = () => pressRow(row);
  row.onkeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pressRow(row);
    }
  };
  appendWork(row, { animate });
  return row;
}

// The thought is read, not watched (D66).
//
// Re-rendering it once per animation frame is the right rate for a crossfade
// and the wrong one for prose: sixty updates a second is more than the eye can
// parse, so every token is a small disturbance instead of a phrase. The body is
// fed on a *reading clock* instead — whole-word chunks, one every 200ms, each
// blooming in over 260ms — while the position is eased on the rendering clock
// underneath it.
//
// Appending is what makes the bloom possible: a renderer that rebuilds the
// block replays every animation on every update, and the card shimmers instead
// of settling. So this path appends and never rebuilds, and the settled card is
// drawn once by `settleThought` with the same renderer a reply uses.
const THOUGHT_CHUNK_CHARS = 60;   // one line at the card's measure
const THOUGHT_CHUNK_MS = 200;     // the reading clock
const THOUGHT_TAU_MS = 140;       // how fast the position closes the gap
const THOUGHT_BACKLOG = THOUGHT_CHUNK_CHARS * 4;

function paintThought(row) {
  if (row._loop) return;
  row._next = 0;
  row._reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const frame = (now) => {
    const raw = row._body.dataset.raw || "";
    if (raw.length !== row._lastRaw) {
      row._lastRaw = raw.length;
      row._idleAt = now;
    }
    if (now >= row._next) {
      releaseThought(row, raw);
      row._next = now + THOUGHT_CHUNK_MS;
    }
    followThought(row, now);
    row._preview.textContent = previewLine(plainPreview(raw));
    // Nothing arriving and nothing left to catch up on: the loop stops itself
    // rather than spinning on a thought whose socket died. A later frame
    // restarts it, because `paintThought` is the only way text reaches here.
    const gap = row._body.scrollHeight - row._body.clientHeight - row._body.scrollTop;
    if (raw.length === row._shown && gap < 0.5 && now - row._idleAt > 2000) {
      row._loop = null;
      return;
    }
    row._loop = requestAnimationFrame(frame);
  };
  row._loop = requestAnimationFrame(frame);
}

// One whole-word chunk, appended. A chunk that would end inside a word waits for
// the next space, so the card never shows half a word.
function releaseThought(row, raw) {
  let shown = row._shown || 0;
  if (shown >= raw.length) return;
  // Nobody reads faster than the clock, and a background tab or a fast model
  // can leave a long queue. The surplus is skipped rather than crawled through:
  // the reader did not see it either way, and the settled render still has every
  // character.
  if (raw.length - shown > THOUGHT_BACKLOG) {
    const skip = raw.length - THOUGHT_BACKLOG;
    const space = raw.indexOf(" ", skip);
    shown = space === -1 ? skip : space + 1;
  }
  let end = Math.min(raw.length, shown + THOUGHT_CHUNK_CHARS);
  if (end < raw.length) {
    const space = raw.lastIndexOf(" ", end);
    if (space > shown) end = space + 1;
  }
  appendThought(row, raw.slice(shown, end));
  row._shown = end;
}

// One paragraph per blank line, one fade per chunk. `inlineMarkdown` rather than
// `renderMarkdown` because this appends: a block renderer that clears its
// container is exactly what replays the fade on everything already read.
function appendThought(row, text) {
  for (const piece of text.split(/(\n{2,})/)) {
    if (!piece) continue;
    if (/^\n{2,}$/.test(piece)) {
      row._para = null;
      continue;
    }
    if (!row._para) {
      if (row._livePara) row._livePara.classList.remove("live");
      row._para = el("p", "md-p live");
      row._livePara = row._para;
      row._body.appendChild(row._para);
    }
    const chunk = el("span", "md-chunk");
    inlineMarkdown(piece, chunk);
    row._para.appendChild(chunk);
  }
}

// The band marks a body that scrolls; a short one is not faded for nothing.
// Cached on the element because the follow asks on every frame and the class
// write is the only part worth skipping.
function paintThoughtBand(body) {
  const overflows = body.scrollHeight > body.clientHeight + 1;
  if (body._banded === overflows) return;
  body._banded = overflows;
  body.classList.toggle("scrolls", overflows);
}

// The follow, one scale down from the transcript's: an exponential in time, so
// the motion is the same at any refresh rate, and a reader who wheels up inside
// the card keeps their place (N141).
function followThought(row, now) {
  const box = row._body;
  const dt = Math.max(1, Math.min(64, now - (row._at ?? now)));
  row._at = now;
  const tail = box.scrollHeight - box.clientHeight;
  paintThoughtBand(box);
  if (!row._follow) return;
  const gap = tail - box.scrollTop;
  if (Math.abs(gap) < 0.5) {
    if (gap) {
      box.scrollTop = tail;
      row._lastTop = box.scrollTop;
    }
    return;
  }
  box.scrollTop += row._reduce ? gap : gap * (1 - Math.exp(-dt / THOUGHT_TAU_MS));
  row._lastTop = box.scrollTop;
}

// The stream is over: the card is drawn once by the markdown renderer, so a
// list, a fence or a table the live path appended as prose becomes the block the
// model wrote. For prose nothing changed, so the swap is not visible.
function settleThought(row) {
  if (row._loop) {
    cancelAnimationFrame(row._loop);
    row._loop = null;
  }
  const raw = row._body.dataset.raw || "";
  if (!raw) return;
  renderMarkdown(raw, row._body);
  paintThoughtBand(row._body);
  row._shown = raw.length;
  row._para = null;
  row._livePara = null;
  if (row._follow) row._body.scrollTop = row._body.scrollHeight;
}

// Prose, a tool call, a compaction, a length cut or the end of the turn ends the
// thought: the stream is over, so the card is settled once with the real
// renderer.
function endThought() {
  const row = reasoningTurn;
  reasoningTurn = null;
  if (row) settleThought(row);
}

// -- one view per open session ------------------------------------------------
//
// The state a live turn writes into — the open agent bubble, the reasoning row,
// the work run, the tool cards, the reader's scroll — belongs to a session, not
// to the window. It is swapped into the module-level names for the frame being
// drawn and swapped back out, so every renderer below keeps its single-view
// shape while more than one session can have a turn in flight.

function makeView(session, branch) {
  const holder = document.createElement("div");
  holder.dataset.session = session.id;
  // The blank a folding stage holds at the tail. It is not in the tree until
  // the first content is appended, so a short transcript still bottom-aligns on
  // its first message (see `appendTranscript`).
  const reserve = document.createElement("div");
  reserve.className = "tail-reserve";
  reserve.setAttribute("aria-hidden", "true");
  const view = {
    id: branch.id,
    sessionId: session.id,
    branch: { ...branch },
    session: { ...session },
    // The transcript while the view is not on screen. `#log` holds the nodes of
    // the view that is, and they move between the two rather than being rebuilt.
    holder,
    socket: null,
    opening: null,
    settleOpen: null,
    reconnectDelay: 0,
    agentTurn: null,
    reasoningTurn: null,
    workRun: null,
    reserveEl: reserve,
    reserveH: 0,
    pinned: null,
    // The stage currently folding into the blank, while its height transition
    // runs. A fold in an overflowing transcript pre-funds the reserve and must
    // not follow or settle until the stage is out of the layout.
    foldingStage: null,
    followTail: true,
    promptCursor: null,
    toolCards: new Map(),
    toolRows: new Map(),
    scratchOrigins: new Map(),
    // Call ids whose card was drawn from the server's record of a prompt another
    // window is answering, rather than from this window's own frames, so the
    // buttons can be taken away by whoever gives the verdict (D63).
    remotePending: new Set(),
    running: false,
    busy: false,
    unread: false,
    state: "open",
    detail: "",
    touchedAt: Date.now(),
    workingDir: branch.working_dir ?? null,
    attachments: branch.attachments ?? [],
    toolSelection: branch.tool_selection ?? null,
    model: branch.model ?? null,
    reasoning: branch.reasoning ?? null,
    approval: branch.approval ?? null,
    firstMessage: branch.first_message ?? null,
    // Whether a turn is in progress, and which turn the next user message would
    // open. A replayed transcript seeds the count in `replay`.
    turnOpen: false,
    turnIndex: 0,
    // The inline editor open in this transcript, if any. It belongs to the
    // view, not the window: switching away leaves the bubble editable where it
    // was, and coming back returns to it.
    editing: null,
    usageFrame: null,
    scrollTop: 0,
    lastTool: null,
  };
  // The projection is replayed into the holder once, and is not needed again;
  // keeping it on the row object would hold a second copy of the conversation.
  delete view.session.messages;
  delete view.session.branch;
  return view;
}

function snapshotRender() {
  return {
    log,
    agentTurn,
    reasoningTurn,
    workRun,
    reserveEl,
    reserveH,
    pinned,
    followTail,
    promptCursor,
    toolCards,
    toolRows,
    scratchOrigins,
  };
}

function loadRender(view) {
  log = view === currentView ? $("log") : view.holder;
  renderView = view;
  agentTurn = view.agentTurn;
  reasoningTurn = view.reasoningTurn;
  workRun = view.workRun;
  reserveEl = view.reserveEl;
  reserveH = view.reserveH;
  pinned = view.pinned;
  followTail = view.followTail;
  promptCursor = view.promptCursor;
  toolCards = view.toolCards;
  toolRows = view.toolRows;
  scratchOrigins = view.scratchOrigins;
}

function saveRender(view) {
  view.agentTurn = agentTurn;
  view.reasoningTurn = reasoningTurn;
  view.workRun = workRun;
  view.reserveEl = reserveEl;
  view.reserveH = reserveH;
  view.pinned = pinned;
  view.followTail = followTail;
  view.promptCursor = promptCursor;
  view.toolCards = toolCards;
  view.toolRows = toolRows;
  view.scratchOrigins = scratchOrigins;
}

function restoreRender(snapshot) {
  log = snapshot.log;
  agentTurn = snapshot.agentTurn;
  reasoningTurn = snapshot.reasoningTurn;
  workRun = snapshot.workRun;
  reserveEl = snapshot.reserveEl;
  reserveH = snapshot.reserveH;
  pinned = snapshot.pinned;
  followTail = snapshot.followTail;
  promptCursor = snapshot.promptCursor;
  toolCards = snapshot.toolCards;
  toolRows = snapshot.toolRows;
  scratchOrigins = snapshot.scratchOrigins;
  renderView = currentView;
}

// Run a frame's renderer against a view that is not the one on screen. The
// transcript nodes are built in that view's holder, so the next switch has
// nothing to reconstruct.
function withView(view, fn) {
  if (view === currentView) return fn();
  const live = snapshotRender();
  loadRender(view);
  try {
    return fn();
  } finally {
    saveRender(view);
    restoreRender(live);
  }
}

// A render with no branch behind it: the draft and the login surface. The
// transcript is empty, and every name the renderers use is cleared so a note
// cannot be appended to the view that was open a moment ago.
function resetRender() {
  log = $("log");
  agentTurn = null;
  reasoningTurn = null;
  workRun = null;
  reserveEl = null;
  reserveH = 0;
  pinned = null;
  followTail = true;
  lastScrollTop = 0;
  promptCursor = null;
  toolCards = new Map();
  toolRows = new Map();
  scratchOrigins = new Map();
  renderView = null;
}

// Whether this window keeps a view's transcript and socket in memory. It is
// *not* what the rail draws as Active — that set is the account's, from the
// core (D63) — it is only what this document is willing to keep warm: the
// session on screen, one doing something, and anything looked at inside the
// account's window.
function keepViewWarm(view) {
  // On screen, or doing something: the three states that ask for attention
  // rather than merely being recent. None of them is a time since use, so a
  // clock cannot retire any of them.
  if (view === currentView || view.running || view.unread) return true;
  return warmWindowOpen(view);
}

// Whether the session is still inside the account's window, by when the reader
// last touched it here. One comparison, read by the predicate above and by
// `parkView`, so "0 keeps nothing" cannot mean one thing at a switch and another
// at the sweep.
function warmWindowOpen(view) {
  return Date.now() - view.touchedAt < warmWindowMs();
}

// How long this window keeps a view warm after it stops being live, from the
// account's setting. Read on every question rather than cached at sign-in: the
// control in Settings applies to views that are already open, and a value copied
// into a variable at boot would leave the setting true only for sessions opened
// after it changed.
function warmWindowMs() {
  return interfacePrefs.active_minutes * 60000;
}

// A turn that never left — the socket would not open, or the frame failed to
// send. The view goes back to idle, and a background view that was only being
// held for that turn goes away.
function markTurnFailed(view) {
  view.running = false;
  view.busy = false;
  // No turn is running on this view, so the next message opens one rather
  // than steering one the server does not have.
  view.turnOpen = false;
  view.state = "open";
  view.detail = "";
  if (view === currentView) setBusy(false);
  setViewPhase(view, "open", "");
  if (view !== currentView) dropView(view);
  paintGripBadge();
}

// A turn was sent from this view. The rail follows the phases from here, and the
// view's last-used stamp moves with it: the working set is ordered by that, and a
// turn in flight is the strongest claim a session has to being open.
function markTurnStarted(view) {
  view.running = true;
  view.busy = true;
  view.unread = false;
  view.touchedAt = Date.now();
  view.lastTool = null;
  setViewPhase(view, "thinking", "starting");
  if (view === currentView) setBusy(true);
  paintGripBadge();
}

// The phase is the coarse fact the rail can show — thinking, running a tool,
// waiting for a human, finished — and the detail is the one line under it.
function setViewPhase(view, phase, detail) {
  view.state = phase;
  view.detail = detail || "";
  // The row and its section summary are updated where they stand. Rebuilding
  // the list on every phase would reset the rail's scroll and close a row menu
  // while a background turn advanced — neither of which is the reader's doing.
  // A rebuild is only needed when the view has no row yet, which is the moment
  // it entered the working set.
  if (activeRowEls.has(view.id)) {
    paintActiveRow(view);
    paintActiveGroupNote();
  } else {
    renderSessions();
  }
  paintGripBadge();
}

// Fold one frame into the view's rail state. This runs for every frame, on the
// screen or not; `handle` below runs only when there is a transcript to draw
// into. The split is what lets a background turn keep a truthful state line
// without paying to render tokens nobody is looking at.
function trackViewFrame(view, frame) {
  switch (frame.type) {
    case "waiting":
      setViewPhase(view, "thinking", "waiting for the model");
      break;

    case "token":
      if (view.state !== "thinking" || view.detail !== "writing the reply") {
        setViewPhase(view, "thinking", "writing the reply");
      }
      break;

    case "reasoning":
      if (view.state !== "thinking" || view.detail !== "reasoning") {
        setViewPhase(view, "thinking", "reasoning");
      }
      break;

    case "truncated":
      setViewPhase(view, "thinking", "continuing after the length limit");
      break;

    case "compacted":
      setViewPhase(view, "thinking", "compacting the earlier conversation");
      break;

    case "user_message":
      view.running = true;
      view.busy = true;
      view.touchedAt = Date.now();
      setViewPhase(view, "thinking", view.detail || "starting");
      // The title is derived from the opening message server-side, so a fresh
      // session is still "untitled" in the list this painted before the turn
      // began. Ask once it has a message to derive from.
      if (!view.session.title) loadSessions();
      break;

    case "tool_proposal":
      view.lastTool = frame.name;
      setViewPhase(view, "tool", frame.name);
      break;

    case "tool_approval_required":
      setViewPhase(view, "approval", `approve ${view.lastTool || "a tool call"}`);
      break;

    case "tool_decision":
      if (view.state === "approval") {
        setViewPhase(view, "tool", view.lastTool || "running");
      }
      break;

    case "tool_result":
      if (view.state !== "thinking") setViewPhase(view, "thinking", "reading the result");
      break;

    case "usage":
      view.usageFrame = frame;
      if (view === currentView) paintUsage(view);
      break;

    case "done":
      view.running = false;
      view.busy = false;
      view.unread = view !== currentView;
      // A reply that landed while the reader was elsewhere is what the Active
      // row is for, so the view opens at that reply rather than at whatever
      // scroll position it was left on.
      if (view.unread) view.followTail = true;
      setViewPhase(view, "done", "");
      if (view === currentView) setBusy(false);
      else {
        // A background turn needs no socket once it is over. Closing it keeps
        // the existing rule that a vanished client cannot leave a turn burning
        // budget: there is no turn left to burn. The next message reconnects.
        closeViewSocket(view);
        loadSessions();
      }
      break;

    default:
      break;
  }
}

// Paint the composer's per-session context: the directory, the attachments, the
// tools this conversation may use, and what its last turn cost.
function paintViewChrome(view) {
  showWorkingDir(view.workingDir);
  showAttachments(view.attachments);
  refreshJobs();
  toolSelection = view.toolSelection;
  paintTools();
  paintUsage(view);
  setBusy(view.busy);
}

function paintUsage(view) {
  const frame = view && view.usageFrame;
  if (!frame) {
    clearUsage();
    $("context-bar").classList.add("hidden");
    return;
  }
  $("usage").textContent = usageLine(frame.usage, frame);
  $("usage").title = usageTitle(frame.usage);
  showContext(frame.usage, frame.context_tokens, frame.compacts_above);
}

// Move the live transcript into its holder, remembering where the reader was.
function stashViewDom(view) {
  if (!currentView || view !== currentView) return;
  const box = $("log");
  view.followTail = followTail;
  view.scrollTop = box.scrollTop;
  mounting = true;
  while (box.firstChild) view.holder.appendChild(box.firstChild);
  mounting = false;
  box.dataset.session = "";
}

// Put a view's transcript on screen. A running session is pinned to its live
// edge — the step it is on is the thing the switch was asking to see — while a
// session that is not running restores where the reader left it.
function attachView(view) {
  const box = $("log");
  box.dataset.session = view.id;
  const pin = view.running || view.unread;
  mounting = true;
  while (view.holder.firstChild) box.appendChild(view.holder.firstChild);
  followTail = pin ? true : view.followTail;
  // Force the new height before restoring the position; a scrollTop set before
  // layout is clamped to the old (empty) height and silently lands at zero.
  void box.scrollHeight;
  box.scrollTop = followTail ? box.scrollHeight : view.scrollTop;
  lastScrollTop = box.scrollTop;
  mounting = false;
  view.unread = false;
  // A fold may have finished while the view was in its holder. Settle the
  // reserve against the height the real layout now has, then hold the tail if
  // the view is live.
  if (pinned !== null) {
    settle();
    if (followTail) box.scrollTop = box.scrollHeight;
    lastScrollTop = box.scrollTop;
  }
  // A view that is not running cannot be waiting for a reply to fill a blank.
  // A release interrupted by a switch (or a turn that ended without `done`)
  // leaves only empty space at the foot.
  if (!pin && reserveH > 0.5) {
    pinned = null;
    setReserve(0);
  }
  paintJumpLive();
}

// Put a view on screen by moving its transcript into `#log`. Nothing is
// rebuilt: a running session resumes mid-thought, on the step it is on, with the
// history above it exactly where it was left.
function focusViewEntry(view) {
  // A bubble being edited is where the next keystroke belongs; the composer is
  // disabled, so focusing it would be focusing nothing.
  const ta = view && view.editing
    ? view.editing.node.querySelector("textarea")
    : null;
  if (ta) ta.focus({ preventScroll: true });
  else $("input").focus({ preventScroll: true });
}

function showView(view) {
  if (!views.has(view.id)) return;
  if (currentView === view) {
    focusViewEntry(view);
    return;
  }
  closeForkMenu();
  viewOpen++;

  const previous = currentView;
  if (previous) {
    saveRender(previous);
    stashViewDom(previous);
    // Left open rather than dropped: the working set is what the reader can come
    // back to, and the window in Settings is how long it lasts (D63).
    parkView(previous);
  }

  currentView = view;
  activeBranchId = view.id;
  renderView = view;
  loadRender(view);
  $("log").classList.remove("hidden");
  leaveDraft();
  attachView(view);
  view.touchedAt = Date.now();
  // On screen is read — by the account, which is what clears the badge in the
  // window that is not this one (D63).
  markRead(view.sessionId);
  paintViewChrome(view);
  applyBranchChoices(view);
  paintBranchChip();
  paintComposerMode();
  renderSessions();
  focusViewEntry(view);
}

function discardView(view) {
  closeViewSocket(view);
  views.delete(view.id);
  view.holder.replaceChildren();
}

// A view that has just stopped being on screen. It keeps its nodes and its place
// in the working set, but not its socket: an idle session has nothing to listen
// for, and the next message reopens one (`ensureSocket`).
//
// A running turn keeps its socket, because the socket is the turn (D63) — the
// server cancels it when the last client goes. An unread reply has already had
// its socket closed by `done`.
function parkView(view) {
  view.touchedAt = Date.now();
  if (view.running) return;
  closeViewSocket(view);
  // A window of 0 means keep nothing, and it has to mean it now rather than at
  // the next sweep: that is the smallest value's whole promise.
  if (!warmWindowOpen(view)) discardView(view);
}

// Drop a view that is neither current nor doing anything. Forced when the user
// removes a session, which is also the one case that cancels a running turn.
function dropView(view, { force = false } = {}) {
  if (!view || !views.has(view.id)) return;
  if (!force && (view.running || view.unread)) return;
  if (view === currentView) {
    closeForkMenu();
    saveRender(view);
    stashViewDom(view);
    currentView = null;
    activeBranchId = null;
    renderView = null;
    $("log").replaceChildren();
    $("log").dataset.session = "";
    resetRender();
  }
  closeViewSocket(view);
  views.delete(view.id);
  view.holder.replaceChildren();
  renderSessions();
}

function closeViewSocket(view) {
  // A caller may be waiting on this socket's handshake. Dropping the view has
  // to answer it, or a `submit` awaiting the connect would wait forever.
  if (view.settleOpen) {
    const settle = view.settleOpen;
    view.settleOpen = null;
    settle(false);
  }
  const sock = view.socket;
  if (!sock) return;
  view.socket = null;
  sock.onopen = null;
  sock.onmessage = null;
  sock.onclose = null;
  try {
    sock.close();
  } catch {
    // Already closed is the same outcome as closed.
  }
}

function reconnectView(view) {
  if (view.socket) return;
  view.reconnectDelay = view.reconnectDelay
    ? Math.min(view.reconnectDelay * 2, 10000)
    : 500;
  setTimeout(() => {
    if (!views.has(view.id) || view.socket) return;
    if (view !== currentView) return;
    connect(view);
  }, view.reconnectDelay);
}

// The socket a message is about to be sent on. A view opened from history has
// none until now — there is nothing to listen for in an idle session, and a
// socket per branch ever looked at is a connection nobody asked for.
function ensureSocket(view) {
  if (view.socket && view.socket.readyState === WebSocket.OPEN) return true;
  if (view.socket && view.socket.readyState === WebSocket.CONNECTING) {
    return view.opening;
  }
  return connect(view);
}

// -- session ----------------------------------------------------------------

function showLogin(message) {
  csrf = null;
  accountName = "";
  closeForkMenu();
  // The task view and its poll go with the account, like the working set's.
  // `tasks.js` loads after this file, so the first sign-in screen can run
  // before it exists.
  if (typeof tasksSignedOut === "function") tasksSignedOut();
  // The poll goes with the account: the rows it holds are one person's work, and
  // the next to sign in must not be shown them even for the four seconds before
  // their own answer arrives.
  stopActive();
  // Every open branch view's socket goes with the account, and every transcript
  // with it: the next person to sign in must not be shown the last one's work.
  viewOpen++;
  for (const view of views.values()) closeViewSocket(view);
  views.clear();
  currentView = null;
  activeBranchId = null;
  $("log").replaceChildren();
  $("log").dataset.session = "";
  resetRender();
  // Back to no session, and nothing staged for one. A directory chosen by the
  // account that just signed out must not be waiting for the next account's
  // first message — the server would refuse a path outside the new account's
  // reach, but offering it at all states one user's filesystem to another.
  draftWorkingDir = null;
  draftAttachments = [];
  // The catalog too, for the reason the staged path above is cleared: it is one
  // account's grant, and the next person to sign in must not be shown which
  // tools the last one had.
  toolCatalog = [];
  toolSelection = null;
  draftToolSelection = null;
  $("tools-chip").classList.add("hidden");
  // The login card has its own layout (`body:has(#login:not(.hidden))` opts out
  // of the grid entirely), so a draft class left on would apply its grid rules
  // to a body that is no longer a grid.
  leaveDraft();
  $("sessions").classList.add("hidden");
  // Settings too, and for a sharper reason than tidiness: it holds the composed
  // policy of the account that just signed out, and leaving it open would display
  // one account's reach over another's login form.
  $("settings").classList.add("hidden");
  // The scrim with it. It is a full-window overlay, so leaving it up would dim
  // the login form and swallow the first click on it.
  $("scrim").classList.add("hidden");
  $("models").classList.add("hidden");
  watchGpu(false);
  $("gpu-toggle").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("log").classList.add("hidden");
  paintGripBadge();
  // The container, not its children: hiding only the parts would leave `#context`
  // drawing its top border across the login screen with nothing underneath it.
  $("context").classList.add("hidden");
  $("cwd-menu").classList.add("hidden");
  $("attach-menu").classList.add("hidden");
  $("login-msg").textContent = message || "";
}

function applyAdmin(admin) {
  isAdmin = Boolean(admin);
  $("gpu-toggle").classList.toggle("hidden", !isAdmin);
  // The foot holds Tasks for everyone and Manage for operators. Hiding the
  // whole row for a non-operator would have hidden the one door they need.
  $("sessions-foot").classList.remove("hidden");
  $("admin-link").classList.toggle("hidden", !isAdmin);

  // Read once on sign-in, not only when the panel is open: the header chip and
  // the thinking control are drawn from the same state, and the panel is polled
  // only while visible.
  if (isAdmin) loadModelState().catch(() => {});

  // The dropdown stays visible for anyone who does not get the panel. Loading and
  // unloading is privileged — that is appliance state — but *choosing* among the
  // models policy already grants is not, and hiding both would have left a
  // non-operator no way to pick one at all.
  $("model").classList.toggle("hidden", isAdmin);
  $("gpu-model").classList.toggle("hidden", !isAdmin);
}

// What the appliance calls itself (D82). Four places name it — the sign-in
// heading, the rail's wordmark, the rail's way into the administration page and
// the window title — and an organisation that renamed its agent should not still
// be reading "Neuman" in any of them. Set from `/api/brand` before sign-in as
// well, so the door carries the name too.
function applyAgentName(name) {
  const label = (name || "").trim() || "Neuman";
  $("login-title").textContent = label;
  $("sessions-brand").textContent = label;
  $("admin-link-label").textContent = `Manage ${label}`;
  document.title = label;
}

function showChat(account) {
  accountName = account || "";
  $("login").classList.add("hidden");
  // The rail is an overlay at every width and starts closed: opening it covers
  // the transcript, so it must never be open before anyone asked. It is not
  // persisted, because navigation state is state.
  $("sessions").classList.remove("hidden");
  setRailCollapsed(true);
  $("log").classList.remove("hidden");
  $("context").classList.remove("hidden");
  // Not `#cwd-bar`: it is a status line that shows only when there is a
  // directory to state, and `showWorkingDir` owns that. Revealing it here would
  // put "no working directory" above every fresh composer again.
  $("composer").classList.remove("hidden");
  // The face of the account circle: initials, because the face has to stay one
  // size for every account name. `title` and `aria-label` carry the whole name, so
  // a screen reader announcing "SA" is told it is the account control.
  $("account-initials").textContent = initials(account);
  $("account-toggle").setAttribute("aria-label", `Account and settings: ${account}`);
  $("account-toggle").title = `Account and settings: ${account}`;
  // The whole name, in the dialog the chip opens. The circle can only carry two
  // letters, so this is the one place the account is actually named.
  $("settings-name").textContent = account;
  // The task list's poll, which keeps the rail's Tasks count current. If
  // `tasks.js` has not run yet it starts itself on seeing `csrf`.
  if (typeof tasksSignedIn === "function") tasksSignedIn();
}

// Up to two initials from an account name. A single token gives its first two
// letters ("sam" -> "SA"); several give the first and last ("Ada Lovelace" ->
// "AL"), which is the pair that distinguishes people rather than the pair that
// repeats a shared given name.
function initials(name) {
  const parts = String(name || "").trim().split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts.at(-1)[0]).toUpperCase();
}

// Every field the server composed, with where each came from. Provenance is
// the point: "why can this account do that" is answerable from a layered
// policy only if the layer is named, and working it out by hand across
// defaults, roles and an overlay is exactly the tedium this replaces.
// What this account may do, as a reading rather than a list of field names.
//
// It was a `dl` of every key in the composed policy, in policy's own spelling —
// `files_read`, `pids_max` — which answered an operator's question ("what is in
// effect?", N43) in the operator's vocabulary. The same facts, grouped by the
// question a *user* is asking: where can I work, what can I reach, and what
// stops a turn.
//
// Tools are deliberately absent. The composer's tool chip is where they are
// seen and chosen now (D50), and a second rendering of one grant is exactly the
// drift this file keeps warning about.
//
// Still a reading, not a control: the one policy field a user authors for
// themselves is `auto_accept`, and that is a chip in the composer beside the
// decision it governs.
function renderPermissions(body) {
  const pane = $("pane-permissions");
  const policy = body.policy || {};
  const provenance = body.provenance || {};

  pane.innerHTML = "";

  const section = (title) => {
    pane.appendChild(el("h3", null, title));
    const list = el("dl", "reading");
    pane.appendChild(list);
    return list;
  };

  // One row. `source` is the layer that set the value — the answer to "why can
  // I do this", which is a follow-up rather than the value itself, so it is dim
  // and parenthesised beside it.
  const row = (list, label, value, source) => {
    list.appendChild(el("dt", null, label));
    const dd = el("dd");
    dd.appendChild(value instanceof Node ? value : el("span", null, value));
    if (source) dd.appendChild(el("span", "src", ` (${source})`));
    list.appendChild(dd);
  };

  // Paths and hosts get a line each. A comma-joined run of four absolute paths
  // is unreadable, and "where exactly" is the whole question here.
  const lines = (values, empty) => {
    if (!values || !values.length) return el("span", "none", empty);
    const box = el("div", "paths");
    for (const value of values) box.appendChild(el("span", null, value));
    return box;
  };

  const where = section("Where you can work");
  // `readable`, not `policy.files_read`: a grant naming a home directory is not
  // itself reachable — it expands to the configured data subdirectories — so
  // showing the raw entry would name a path no tool can actually use. This is
  // the same expansion the system prompt states, for the same reason.
  row(where, "Readable", lines(body.readable, "nothing"), provenance.files_read);
  row(where, "Writable", lines(policy.files_write, "nothing"), provenance.files_write);

  const net = section("Network");
  // Not a host list: those tools run on this machine over its network, so the
  // only thing policy decides is whether they are offered at all (N146).
  row(net, "Web tools", el("span", "none", policy.network === "any"
      ? "`web_fetch` — any host this machine can reach"
      : "`web_fetch` is refused"),
      provenance.network);

  const limits = section("Limits on a turn");
  row(limits, "Daily tokens",
      policy.tokens_daily === null || policy.tokens_daily === undefined
        ? el("span", "none", "uncapped")
        : el("span", "mono", policy.tokens_daily.toLocaleString()),
      provenance.tokens_daily);
  row(limits, "Steps per turn", el("span", "mono", String(policy.max_iterations ?? "—")),
      provenance.max_iterations);
}

// -- settings: usage ----------------------------------------------------------

// Today's tokens against today's cap.
//
// Fetched when the tab is opened rather than at sign-in: it is the one reading
// here that changes while the window is shut, and a figure cached from an hour
// ago is worse than a figure that takes a moment to arrive.
async function renderUsage() {
  const pane = $("pane-usage");
  pane.innerHTML = "";
  pane.appendChild(el("p", "none", "Reading…"));

  let usage;
  try {
    usage = await api("GET", "/api/usage");
  } catch (error) {
    pane.innerHTML = "";
    pane.appendChild(el("p", "none", `Could not read usage: ${error.message}`));
    return;
  }

  pane.innerHTML = "";
  pane.appendChild(el("h3", null, "Today"));

  const cap = usage.tokens_daily;
  const used = usage.settled || 0;

  const figure = el("div");
  figure.appendChild(el("span", "big", used.toLocaleString()));
  // An uncapped account gets no denominator and no bar, because there is
  // nothing to be a fraction of. Saying "of ∞" or drawing an empty bar would
  // both imply a limit that does not exist.
  figure.appendChild(el("span", "of", cap ? ` of ${cap.toLocaleString()} tokens` : " tokens"));
  pane.appendChild(figure);

  if (cap) {
    const share = Math.min(1, used / cap);
    const meter = el("div", "meter");
    // The same thresholds the context bar uses, so "getting full" looks the
    // same wherever it is shown.
    if (share >= 0.9) meter.classList.add("bad");
    else if (share >= 0.7) meter.classList.add("warn");
    const fill = el("span");
    fill.style.width = `${(share * 100).toFixed(1)}%`;
    meter.appendChild(fill);
    pane.appendChild(meter);
  }

  const list = el("dl", "reading");
  // Reserved is held against turns still running, and it is *not* added to the
  // figure above: a reservation is released if the turn fails, so folding it in
  // would show a total that falls back down and look like broken accounting.
  if (usage.reserved) {
    list.appendChild(el("dt", null, "In flight"));
    const dd = el("dd");
    dd.appendChild(el("span", "mono", usage.reserved.toLocaleString()));
    dd.appendChild(el("span", "src", " reserved for running turns"));
    list.appendChild(dd);
  }
  list.appendChild(el("dt", null, "Resets"));
  list.appendChild(el("dd", null, "midnight UTC"));
  pane.appendChild(list);

  if (!cap) {
    pane.appendChild(el("p", "hint", "No daily cap is set for this account."));
  }
}

// -- settings: the dialog ------------------------------------------------------

// The interface preferences, which belong to the *account* rather than to this
// browser.
//
// This is a deliberate exception to the rule the preference beside it follows.
// The rail's width lives in `localStorage` because it describes one browser and
// syncing it would let one tab change another's reading. Neither clause is true here: someone signing in from a second machine
// wants their own theme rather than the default, and two tabs agreeing on the
// colour scheme is the correct outcome rather than a surprise. They are also the
// settings a future `customize-neuman` tool has to be able to write, and the
// server cannot reach `localStorage` at all (D53).
//
// Held in a variable and not re-read: the values arrive with the identity, in
// the `/api/login` and `/api/me` responses the client already waits for. A
// second request would mean the page painting in the default theme and then
// correcting itself, which is a visible flash of the wrong theme.
const PALETTES = [
  "dark",
  "light",
  "nord",
  "everforest-dark",
  "everforest-light",
  "gruvbox-dark",
  "gruvbox-light",
];

let interfacePrefs = { palette: "dark", collapse_reasoning: false, active_minutes: 30 };

// The longest window the Sessions pane offers, in minutes. The server's bound and
// the column's CHECK are the same number; this copy exists so the field can be
// clamped before it is sent rather than sending a value the route will refuse.
const MAX_ACTIVE_MINUTES = 1440;

// The account's own standing instructions (D65), the middle prompt tier between
// the operator's text in the administration page and a directory's `AGENTS.md`.
// Held like `interfacePrefs` and for the same reason: they arrive with the
// identity, so the dialog opens with the text already in it rather than
// flashing an empty box. `accountInstructions` is the *stored* value; the
// textarea holds the draft, and the two are what the counter and the Save
// button are drawn from.
//
// The ceiling is the server's, and this copy exists only to count characters
// beside the box; `maxlength` below is what stops an over-long paste. A test
// holds all four — this, the markup, the store and the migration — to one
// number.
const MAX_INSTRUCTIONS = 2000;
let accountInstructions = "";

function hideReasoning() {
  return interfacePrefs.collapse_reasoning;
}

// The theme is written as `data-palette` on the root element, which is what the
// stylesheet selects on (`:root[data-palette="…"]`); the attribute keeps the
// token set's name even though the setting is labelled Theme. `dark` is written
// out rather than left implicit even though the bare `:root` block already
// defines it: an attribute that is sometimes absent means the default theme with
// no marker is also the one that cannot be styled differently from "no
// preference loaded yet", and the settings control would have nothing to read
// back.
function applyPalette(name) {
  const palette = PALETTES.includes(name) ? name : "dark";
  document.documentElement.dataset.palette = palette;
}

// Every preference at once, from whichever identity response carried them.
// Tolerant of a field being absent: an older server, or a login that failed
// before the row was read, should land on the defaults rather than throwing
// inside the sign-in path.
function applyInterface(prefs) {
  const p = prefs || {};
  interfacePrefs = {
    palette: PALETTES.includes(p.palette) ? p.palette : "dark",
    collapse_reasoning: Boolean(p.collapse_reasoning),
    // Read the same tolerant way for the same reason: a server without the
    // field gets the 30-minute default rather than `undefined` minutes, which
    // would make every view expire on the next tick.
    active_minutes: Number.isInteger(p.active_minutes)
      ? Math.min(Math.max(p.active_minutes, 0), MAX_ACTIVE_MINUTES)
      : 30,
  };
  applyPalette(interfacePrefs.palette);
  paintInterfacePrefs();
}

// Write one preference, and let the server's reply be what updates the state.
//
// The theme is applied *before* the request and reverted if it fails. That is
// the one place in this client where an optimistic update is right: a theme is
// the single setting whose whole purpose is to be seen immediately, and a
// round trip of latency before the colours move reads as a broken control. The
// revert is what keeps it honest — a rejected write does not leave the page
// showing a theme the account does not have.
async function setInterface(change) {
  const before = { ...interfacePrefs };
  interfacePrefs = { ...interfacePrefs, ...change };
  if (change.palette !== undefined) applyPalette(interfacePrefs.palette);
  paintInterfacePrefs();

  try {
    // `api` throws on any non-2xx and already carries the CSRF header, so the
    // success path is just the parsed reply — which is also the authority on
    // what landed, rather than the optimistic guess above.
    applyInterface(await api("PATCH", "/api/me/interface", change));
  } catch {
    interfacePrefs = before;
    applyPalette(before.palette);
    paintInterfacePrefs();
  }
}

// The controls in the Interface pane, drawn from the state rather than toggled
// in place. One painter for both so a preference changed in another tab — or
// reverted by a failed write — cannot leave the checkbox and the theme
// disagreeing about what is in force.
function paintInterfacePrefs() {
  $("pref-hide-reasoning").checked = interfacePrefs.collapse_reasoning;
  $("pref-active-minutes").value = String(interfacePrefs.active_minutes);
  for (const button of $("pref-palette").querySelectorAll("button")) {
    button.setAttribute(
      "aria-pressed", String(button.dataset.palette === interfacePrefs.palette));
  }
}

// The model and reasoning a new branch starts with. They arrive with the
// identity like the interface preferences, because the composer needs them on
// the first paint: selecting a model after the page has settled is the visible
// correction this exists to remove.
function applyDefaults(payload) {
  const d = (payload && payload.defaults) || {};
  accountDefaults = { model: d.model || null, reasoning: d.reasoning || null };
  paintDefaults();
  applyCurrentChoices();
}

// The two selects in Settings › Assistant. Only a registered model is offered:
// a granted name with no catalog entry cannot run, and a default that cannot
// run is how a conversation starts in a state the composer has to correct.
// The reasoning list is the chosen model's own declaration, not a global list.
function paintDefaults() {
  const modelSelect = $("pref-default-model");
  const levelSelect = $("pref-default-reasoning");
  if (!modelSelect || !levelSelect) return;

  const chosen = modelSelect.value || accountDefaults.model || "";
  const wantedLevel = levelSelect.value || accountDefaults.reasoning || "";
  modelSelect.replaceChildren();
  const none = el("option", null, "(no default)");
  none.value = "";
  modelSelect.appendChild(none);
  for (const info of modelInfo.values()) {
    if (!info.registered) continue;
    const option = el("option", null, info.name);
    option.value = info.name;
    modelSelect.appendChild(option);
  }
  modelSelect.value = availableModel(chosen) ? chosen : "";

  const levels = defaultLevels(modelSelect.value);
  levelSelect.replaceChildren();
  const fallback = el("option", null, "Model's default");
  fallback.value = "";
  levelSelect.appendChild(fallback);
  for (const level of levels) {
    const option = el("option", null, level);
    option.value = level;
    levelSelect.appendChild(option);
  }
  levelSelect.value = levels.includes(wantedLevel) ? wantedLevel : "";
  levelSelect.disabled = !levels.length;
  const missing = accountDefaults.model && !availableModel(accountDefaults.model)
    ? accountDefaults.model
    : "";
  $("pref-default-note").textContent = modelSelect.value
    ? ""
    : missing
      ? `${missing} is no longer available. Choose another default, or none.`
      : "No default model. A new conversation starts with no model selected.";
}

function defaultLevels(name) {
  const declared = name && reasoningByModel[name];
  return (declared && declared.levels) || [];
}

async function saveDefaults() {
  const model = $("pref-default-model").value || null;
  const reasoning = model ? ($("pref-default-reasoning").value || null) : null;
  const note = $("pref-default-note");
  try {
    const body = await api("PATCH", "/api/me/defaults", { model, reasoning });
    applyDefaults(body);
    note.textContent = "Saved. It applies to new conversations.";
  } catch (error) {
    note.textContent = `Could not save: ${error.message}`;
    paintDefaults();
  }
}

// The stored instructions, from whichever identity payload carried them: the
// two identity responses and the save's own reply. Takes the *payload* rather
// than the text on purpose — every caller has an object, and reading the field
// here is what keeps the contract in one place. That is not a style choice: the
// first version took a string, the save passed its reply object, and a
// successful write cleared the box that had just stored it (N156).
function applyInstructions(payload) {
  const text = payload && typeof payload.instructions === "string"
    ? payload.instructions
    : "";
  accountInstructions = text;
  $("pref-instructions").value = text;
  paintInstructions();
}

// Counter and Save button, drawn from the draft rather than toggled in place.
// Save is enabled only when the textarea differs from what is stored, so the
// button says something true about whether there is anything to save.
function paintInstructions() {
  const box = $("pref-instructions");
  $("pref-instructions-count").textContent =
    `${box.value.length.toLocaleString()} / ${MAX_INSTRUCTIONS.toLocaleString()}`;
  $("pref-instructions-save").disabled = box.value === accountInstructions;
}

// A whole value, sent on the press rather than on every keystroke: this is a
// textarea, not a toggle, and a write per character would be a request per
// character. The server's reply is what updates the stored value — the same
// authority-over-optimism rule `setInterface` follows.
async function saveInstructions() {
  const box = $("pref-instructions");
  const note = $("pref-instructions-note");
  $("pref-instructions-save").disabled = true;
  try {
    applyInstructions(await api("PATCH", "/api/me/instructions",
      { instructions: box.value }));
    note.textContent = "Saved. It applies from your next message.";
  } catch (error) {
    // Re-enabled, because the draft is still in the box and throwing it away
    // on a failed write would be the worst of both outcomes.
    paintInstructions();
    note.textContent = `Could not save: ${error.message}`;
  }
}

const SETTINGS_TABS = [
  "interface",
  "account",
  "sessions",
  "assistant",
  "permissions",
  "usage",
];

function showSettingsTab(name) {
  for (const tab of SETTINGS_TABS) {
    const selected = tab === name;
    $(`tab-${tab}`).setAttribute("aria-selected", String(selected));
    $(`pane-${tab}`).classList.toggle("hidden", !selected);
  }
  // The heading names the pane, so the dialog says where you are without the
  // sidebar having to be read again.
  $("settings-title").textContent = name[0].toUpperCase() + name.slice(1);
  // Only usage needs a fetch, and only when it is actually looked at.
  if (name === "usage") renderUsage();
}

function openSettings() {
  paintInterfacePrefs();
  paintDefaults();
  // An unsaved draft is discarded on reopen: the dialog shows what is stored,
  // which is what the next turn would read. `accountInstructions` is that value.
  $("pref-instructions").value = accountInstructions;
  $("pref-instructions-note").textContent = "";
  paintInstructions();
  $("scrim").classList.remove("hidden");
  $("settings").classList.remove("hidden");
  showSettingsTab("interface");
  // Focus moves into the dialog, which is the rest of the keyboard's round
  // trip. The first tab rather than the dialog itself: this is a tablist now,
  // so there is a real control to land on.
  $("tab-interface").focus();
}

function closeSettings() {
  $("settings").classList.add("hidden");
  $("scrim").classList.add("hidden");
  // Focus back to what opened it, or the ring is left on something invisible.
  $("account-toggle").focus();
}

function settingsOpen() {
  return !$("settings").classList.contains("hidden");
}

// The colour is the glance; the mode name is the label, kept because
// red/amber/green is precisely the palette a colour-blind reader cannot
// separate, and because `never`/`safe`/`yolo` is what the audit chain,
// `neuman-admin` and the policy view all say. The sentence is not on the button:
// three descriptions were a wall, so one line below says what the mode *in
// force* means.
const APPROVAL_MODES = [
  {
    mode: "never",
    detail: "Nothing runs without approval, including reads.",
  },
  {
    mode: "safe",
    detail: "Reading and searching run freely; anything that writes asks.",
  },
  {
    mode: "yolo",
    detail: "Only critical actions ask. Deleting, renaming and admin reads still stop.",
  },
];

function renderApproval(policy) {
  const host = $("approval-choices");
  host.innerHTML = "";

  // The branch's own choice when it has one, otherwise the account's mode, then
  // clamped to the operator's ceiling. The server clamps again at the point of
  // consent; this copy is what the control can truthfully display.
  const choice = currentView ? branchApproval : draftApproval;
  const ceiling = policy.auto_accept_max || "yolo";
  const allowed = APPROVAL_MODES.findIndex((entry) => entry.mode === ceiling);
  const rank = (mode) => APPROVAL_MODES.findIndex((entry) => entry.mode === mode);
  const chosen = Math.max(0, Math.min(
    rank(choice || policy.auto_accept || "safe"),
    allowed < 0 ? APPROVAL_MODES.length - 1 : allowed,
  ));
  const current = APPROVAL_MODES[chosen].mode;

  for (const [index, entry] of APPROVAL_MODES.entries()) {
    const button = el("button", entry.mode);
    button.appendChild(el("span", "dot"));
    button.appendChild(el("span", "label", entry.mode));
    button.setAttribute("aria-pressed", String(entry.mode === current));
    button.title = `${entry.mode} — ${entry.detail}`;

    // Above the ceiling. Disabled rather than absent, so the limit is visible:
    // a missing option reads as a client that forgot it.
    if (allowed >= 0 && index > allowed) {
      button.disabled = true;
      button.title = `${entry.mode} — not permitted for this account; an operator sets the ceiling.`;
    } else {
      button.onclick = () => chooseApproval(entry.mode);
    }
    host.appendChild(button);
  }

  // One sentence for the mode in force, rather than three for the modes you are
  // not on. `never` is the one name that does not read as its behaviour (it
  // means "never auto-accept"), and this line is what disambiguates it.
  const selected = APPROVAL_MODES.find((entry) => entry.mode === current) || APPROVAL_MODES[1];
  $("approval-current").textContent = selected.detail;

  $("approval-ceiling").textContent =
    allowed >= 0 && allowed < APPROVAL_MODES.length - 1
      ? `An operator has capped this account at ${ceiling}.`
      : "";

  // The chip in the composer's footer, which is what this control looks like
  // when it is not open. Painted here rather than by its own reader, so the
  // mode on screen and the mode in the menu come from one statement of what the
  // server said — two renderers for one value is how they drift apart.
  const chip = $("approval-chip");
  $("approval-mode").textContent = current;
  // The mode as a class, so the dot takes its colour from the same rules the
  // menu's dots use. Cleared first: the class is the state, so a stale one left
  // behind would be a second state.
  chip.classList.remove(...APPROVAL_MODES.map((entry) => entry.mode));
  chip.classList.add(current);
  chip.title = `Approval: ${current} — ${selected.detail}`;
}

async function chooseApproval(mode) {
  // The menu is a short list of named acts, so picking one ends it. Closed
  // before the request rather than after: the repaint redraws the choices
  // inside it, and a menu that redraws itself under the pointer invites a
  // second click on a button that has moved.
  $("approval-menu").classList.add("hidden");
  try {
    await persistBranchChoice({ approval: mode });
    const inForce = currentView
      ? branchApproval || (policyState && policyState.policy.auto_accept) || "safe"
      : draftApproval || (policyState && policyState.policy.auto_accept) || "safe";
    note(`Approval mode for this branch is now ${inForce}.`);
  } catch (error) {
    note(`Could not change approval mode: ${error.message}`);
  }
}

async function loadPolicy() {
  const select = $("model");
  select.innerHTML = "";
  try {
    const body = await api("GET", "/api/policy");
    renderPermissions(body);
    renderApproval(body.policy || {});
    // The expanded paths, not `policy.files_read`: a grant naming a home
    // directory is not itself reachable, so offering it would offer a path the
    // server refuses.
    readablePaths = body.readable || [];
    reasoningByModel = body.reasoning || {};
    policyState = body;
    const info = body.model_info || [];
    modelInfo = new Map(info.map((entry) => [entry.name, entry]));
    select.innerHTML = "";
    // An explicit empty choice. The old code selected the first granted model by
    // accident, so opening the panel could change what the next turn used. Now
    // "no model" is a state the UI can be in, and it is the honest one when the
    // account's default is missing or not installed.
    const none = el("option", null, "(no model)");
    none.value = "";
    select.appendChild(none);
    for (const entry of info) {
      const option = el("option", null,
        entry.registered ? entry.name : `${entry.name} (unavailable)`);
      option.value = entry.name;
      option.disabled = !entry.registered;
      select.appendChild(option);
    }
    if (!info.length) {
      note("No models are granted to this account. An operator grants them with `neuman-admin`.");
      select.disabled = true;
    } else {
      select.disabled = !info.some((entry) => entry.registered);
    }
    applyCurrentChoices();
    paintDefaults();
    paintReasoning();
    // The granted tools, from the same sign-in and the same question: what may
    // this account do. Awaited inside `loadPolicy` rather than beside its three
    // call sites, so a fourth cannot forget it — and re-read whenever policy is,
    // which is what makes an operator's revocation show up in the chooser rather
    // than waiting for a reload.
    await loadTools();
  } catch (error) {
    note(`Could not read policy: ${error.message}`);
  }
}

// -- the working directory ---------------------------------------------------

// Where the work is. A hint, not a grant: the server refuses a path outside what
// policy grants for reading, so choosing one cannot widen what the account
// reaches.
//
// A chooser rather than a filesystem browser, because a browser is not available
// to a web page: the OS file dialog never reveals an absolute path, and the
// server runs as `neuman` so it cannot list a 0700 home to draw one itself. The
// chooser offers what policy *expands to* — `/api/policy` returns `readable`
// separately from `policy.files_read` for exactly this, since a grant naming a
// home directory is not itself reachable.
async function saveWorkingDir(next) {
  // Staged while there is no session to attach it to, and PATCHed by
  // `startSession` the moment one exists. Deliberately not validated here: the
  // PATCH route is where `within_reach` lives, and a client-side copy of the
  // "hint, not a grant" rule is a second opinion about policy that would drift
  // from the first. The chooser only offers paths policy reaches anyway, so the
  // staged value is one the server has already described as reachable.
  if (drafting()) {
    draftWorkingDir = next || null;
    showWorkingDir(draftWorkingDir);
    $("cwd-menu").classList.add("hidden");
    return;
  }

  // Captured before the round trip. A reader who switches session while this
  // is in flight is still changing the directory of the branch they were in,
  // not the one that happened to be open when the answer came back.
  const view = currentView;
  const id = activeBranchId;
  try {
    const saved = await api("PATCH", `/api/branches/${id}`, {
      working_dir: next || null,
    });
    if (view) view.workingDir = saved.working_dir ?? null;
    if (view === currentView) {
      showWorkingDir(saved.working_dir);
      note(saved.working_dir
        ? `Working directory set to ${saved.working_dir}.`
        : "Working directory cleared.");
    }
  } catch (error) {
    if (view === currentView) note(error.message);
  }
  $("cwd-menu").classList.add("hidden");
}

// How much of a path the footer shows. The status line has room for about 55
// characters beside the chips in a comfortable window; 40 leaves the line room
// to breathe and still names the last two segments of a deep path.
const CWD_MAX = 40;

// A path at a glance, shortened from the **left**, because the tail is the part
// that says where you are: every deep path in an organisation shares its head,
// so `/home/alice/projects/…` says nothing that `/home/` does not, while
// `…/web/src` is the answer to "where am I".
//
// This is why the cap is applied here rather than in CSS: `text-overflow:
// ellipsis` shortens from the other end, and it is kept only as a backstop for
// a window too narrow for `CWD_MAX`, where the tail does go — the title carries
// the whole path in both cases, and so does the folder button's.
function shortPath(path, max = CWD_MAX) {
  if (path.length <= max) return path;
  // One character of the budget is the ellipsis itself.
  const tail = path.slice(-(max - 1));
  // Land on a segment boundary when one is close to the cut, so this reads
  // `…/web/src` rather than `…ient/web/src`.
  const slash = tail.indexOf("/");
  return `…${slash > 0 && slash <= 8 ? tail.slice(slash) : tail}`;
}

function showWorkingDir(value) {
  currentWorkingDir = value || null;
  const label = $("cwd-current");
  // The path the branch runs from, shortened to fit the line. The title keeps
  // the whole of it, and `currentWorkingDir` above is untouched: this is the
  // display of the path, not the path.
  label.textContent = value ? shortPath(value) : "no working directory";
  label.title = value || "The agent runs from the server's own directory.";
  // Named in full for assistive tech, which does not announce the ellipsis: the
  // shortened text would otherwise be the whole of what the control is called,
  // and the head of the path — the part that was cut — is not on screen to read.
  label.setAttribute("aria-label", value || "no working directory");
  label.classList.toggle("unset", !value);
  // The whole label goes when there is nothing to state. It used to also hide
  // the clear button, which sat inside the bar; the clear action now lives in
  // the chooser, so it is told separately — same condition, one writer for each
  // element that has to obey it.
  $("cwd-bar").classList.toggle("hidden", !value);
  // There is nothing to clear when nothing is set, and a footer that says so
  // would be a dead control in the one state where the chooser is opened to
  // *set* a directory. `hidden`, not removed: the row is built once in
  // `index.html` and this is the only writer of whether it is on screen.
  $("cwd-clear").classList.toggle("hidden", !value);
  // The glyph alone; the title carries the verb. The label that was here
  // competed with the path beside it — the path is the thing being changed, and
  // a folder icon next to it already reads as "the folder control".
  const pick = $("cwd-pick");
  const verb = value ? "Change the working directory" : "Choose a working directory";
  pick.title = value ? `${verb} (${value})` : verb;
  pick.setAttribute("aria-label", verb);
  // Lit only when set, so the resting composer has one accent rather than two.
  pick.classList.toggle("set", Boolean(value));
  // The environment belongs to the directory, so a new directory invalidates
  // what was known about the old one before anything is drawn. Painting the
  // previous answer against a new path is the one wrong state available here.
  currentEnvironment = null;
  paintEnvironment();
  probeEnvironment(value);
}

// What each resolver state means on screen. Three outcomes, not seven: the
// states that differ in *cause* do not differ in what the user does about them.
//
// `ok` is the only lit one. The four rebuild cases are one warning, because
// "re-enter the directory" is the answer to all of them. `env_untrusted` is its
// own case rather than folded in, because a cache nix did not write is
// something to report rather than to rebuild, and the tool refuses to use it.
// Everything else — `no_environment` above all — draws nothing: most
// directories are not projects (N85), and a folder of data is not in a degraded
// state for failing to be a flake.
const ENVIRONMENT_STATES = {
  ok: { kind: "ready", text: "development environment active" },
  env_unrealised: { kind: "warn", text: "development environment not built" },
  env_stale: { kind: "warn", text: "development environment out of date" },
  env_collected: { kind: "warn", text: "development environment needs rebuilding" },
  env_untrusted: { kind: "untrusted", text: "development environment cache not written by nix" },
};

// The outline, and the words that carry the same fact.
//
// The words are not decoration. A coloured border is the whole signal otherwise,
// and that is unreadable to anyone who cannot distinguish the two hues or is
// using a screen reader — so the state goes in the `title` and into the
// accessible name, where `showWorkingDir` already writes both.
function paintEnvironment() {
  const bar = $("cwd-bar");
  const state = currentEnvironment ? ENVIRONMENT_STATES[currentEnvironment] : null;
  for (const kind of ["ready", "warn", "untrusted"]) {
    bar.classList.toggle(`env-${kind}`, state !== null && state.kind === kind);
  }
  if (!currentWorkingDir) return;
  const label = $("cwd-current");
  label.title = state ? `${currentWorkingDir} — ${state.text}` : currentWorkingDir;
  label.setAttribute("aria-label", state
    ? `${currentWorkingDir}, ${state.text}`
    : currentWorkingDir);
}

// Ask what the directory is. Fire-and-forget: the control is already drawn and
// correct without an answer, so this only ever adds to it.
//
// A dispatch to the user's own machine, not a read here — the `.direnv` cache
// lives in their home and the server runs as `neuman`. Same reason the chooser
// lists directories by dispatching `fs_list`.
async function probeEnvironment(path) {
  const generation = ++environmentProbe;
  if (!path) return;
  let answer;
  try {
    answer = await api("GET", `/api/environment?path=${encodeURIComponent(path)}`);
  } catch {
    // Silence. An unprobed directory and a directory with no environment look
    // the same on screen, which is right: neither is a state to act on, and a
    // chooser that reported its own plumbing would be noise in the one place
    // the user is trying to read a path.
    return;
  }
  // A newer choice has already been made, or the directory changed under us.
  if (generation !== environmentProbe || answer.path !== currentWorkingDir) return;
  currentEnvironment = answer.reason || null;
  paintEnvironment();
}

// -- the choosers -------------------------------------------------------------
//
// One widget, two menus. Three panels stand for the levels around the directory
// in the middle: the parent on the left, that directory in the centre, and the
// contents of the row highlighted there on the right. `readablePaths` are the
// granted roots, and everything below them is reached by listing, because policy
// grants a *subtree* and the roots alone are one level of a tree the user already
// has reach over.
//
// The rule both menus share, and the reason they are one implementation: a press
// **selects**, and one button commits. That is the only model that gives the two
// menus the same verb — a row in the directory menu used to commit it while the
// same-looking row in the attachment menu opened it, so the two menus disagreed
// about the meaning of a click without saying so anywhere.
//
// It is also the cheapest model to run. Three panels could mean three listings
// per step; a step costs one, because the new centre is either the panel you were
// already reading (cached) or the parent you came from (cached). The preview on
// the right is the one genuinely new request, and it waits for the selection to
// rest. `listDirectory` is the whole of that machinery.
const CHOOSER_ROWS = 200;

// Every open menu's state: where its centre panel is pointed, what is selected
// there, its filter, and the listings it has already paid for. Per menu because a
// directory created a moment ago has to appear the next time the menu opens, and
// because the filter in one must not be the filter in the other.
const chooserViews = new Map();

// Row ids are what `aria-activedescendant` points at; a counter is enough.
let chooserRowSeq = 0;

// Where a chooser opens: the directory the branch already works in, when policy
// still reaches it, else the granted roots. Reopening at the current directory is
// the point — nudging an existing choice should not mean walking the tree again —
// but a cwd a narrowed policy no longer reaches has to fall back rather than
// offer a path the server would refuse.
function reachableStart(path) {
  if (!path) return null;
  return readablePaths.some((root) => path === root || path.startsWith(`${root}/`))
    ? path
    : null;
}

// The parts of one chooser. Named here so nothing else has to know the id scheme.
function chooserParts(menuId) {
  const stem = menuId === "cwd-menu" ? "cwd" : "attach";
  const part = (suffix) => $(`${stem}-${suffix}`);
  return {
    field: part("filter"),
    crumbs: part("crumbs"),
    left: part("left"),
    centre: part("rows"),
    right: part("right"),
    heads: {
      left: part("left-head"),
      centre: part("centre-head"),
      right: part("right-head"),
    },
  };
}

// What an open chooser is pointed at. `centre` is a path, or null for the granted
// roots — the one level policy names outright, and the one with no parent to put
// on the left. `selected` is the row highlighted in the centre panel: the thing
// the button takes, and the thing the right panel previews.
function newChooserView(start, files) {
  return {
    centre: start,
    selected: null,
    filter: "",
    files,
    listings: new Map(),
    inflight: new Map(),
    tokens: { left: 0, centre: 0, right: 0 },
    previewTimer: 0,
    commitButton: null,
    history: [start],
    at: 0,
    hoverTimer: 0,
    hoverRow: null,
  };
}

// Focus goes to the *field*, and stays there.
//
// The field is the filter, the caret is what says typing works, and every key the
// menu owns is read from it. The selection is drawn rather than focused, so a
// press on a row cannot leave the keyboard pointed at nothing — which is what
// used to happen when the rows took focus and typing then had to be routed back
// into the field by hand.
function focusChooser(menuId) {
  const parts = chooserParts(menuId);
  const view = chooserViews.get(menuId);
  if (!view) return;
  parts.field.value = view.filter;
  parts.field.focus();
}

// Opening a chooser: fresh state, at the directory the branch already works in
// when policy still reaches it, else the granted roots.
//
// Reopening at the current directory is the point — nudging an existing choice
// should not mean walking the tree again — but a cwd a narrowed policy no longer
// reaches has to fall back rather than offer a path the server would refuse.
async function openChooser(menuId) {
  const other = menuId === "cwd-menu" ? "attach-menu" : "cwd-menu";
  // The two choosers occupy the same place above the context card, so only one
  // can be open: stacked, the second would simply hide the first and closing it
  // would reveal a menu nobody remembers opening.
  $(other).classList.add("hidden");
  $(menuId).classList.remove("hidden");
  chooserViews.set(menuId, newChooserView(
    reachableStart(currentWorkingDir),
    menuId === "attach-menu",
  ));
  // Not awaited: the field takes the caret now, not once the first listing lands.
  renderChooser(menuId, "none");
  focusChooser(menuId);
}

function openWorkingDirMenu() {
  return openChooser("cwd-menu");
}

// Settles when the centre panel has its rows, which is what a keyboard step waits
// for before it can put the selection on one of them.
function renderChooser(menuId, direction) {
  const parts = chooserParts(menuId);
  const view = chooserViews.get(menuId);
  if (!view) return Promise.resolve();

  const centre = view.centre;
  const parent = centre === null ? null : parentWithinReach(centre);
  // At a granted root there is no reachable parent, but there is still a level
  // above it: the other roots. Drawing them is what makes the left panel mean the
  // same thing at every depth — "the place this one sits in" — instead of going
  // blank exactly at the top, which is where the menu opens the first time.
  const above = centre === null
    ? { kind: "none" }
    : parent === null
      ? { kind: "roots" }
      : { kind: "dir", path: parent };

  paintCrumbs(menuId, parts, view);

  // Each panel fills on its own. The centre is what you are reading and the sides
  // are context, so nothing waits for all three before drawing: a cached listing
  // paints in the same frame, and one that has to be fetched says so where it
  // will appear.
  fillPanel(menuId, parts, view, "left", above);
  const filled = fillPanel(menuId, parts, view, "centre",
    centre === null ? { kind: "roots" } : { kind: "dir", path: centre });
  fillPanel(menuId, parts, view, "right", previewOf(view));

  if (direction && direction !== "none") arrive(parts, direction);
  return filled;
}

// The right-hand panel: the contents of the highlighted folder, a word about a
// highlighted file, or nothing while nothing is highlighted. A file has no
// contents, and an empty panel is the honest drawing of that.
function previewOf(view) {
  if (!view.selected) return { kind: "none" };
  return view.selected.kind === "d"
    ? { kind: "dir", path: view.selected.path }
    : { kind: "file" };
}

function panelList(parts, role) {
  return role === "left" ? parts.left : role === "right" ? parts.right : parts.centre;
}

function headOf(target) {
  if (target.kind === "roots") return "granted directories";
  if (target.kind === "dir") return basename(target.path) || target.path;
  return "";
}

async function fillPanel(menuId, parts, view, role, target) {
  const list = panelList(parts, role);
  // One token per panel: a later render or a later selection must not let an
  // older answer land in a panel that has moved on.
  const token = ++view.tokens[role];
  parts.heads[role].textContent = headOf(target);
  list.innerHTML = "";
  // A side panel with nothing to show is not drawn as a panel: an empty receded
  // card reads as one waiting to be filled. That is the left at the granted roots,
  // and the right with nothing highlighted or a folder with nothing to open.
  const panel = list.closest(".chooser-panel");
  const side = role !== "centre";
  panel.classList.toggle("empty", side && target.kind === "none");

  if (target.kind === "none") return;

  if (target.kind === "file") {
    list.appendChild(el("div", "none", "A file has no contents."));
    return;
  }

  // The roots, which need no listing: policy named them.
  if (target.kind === "roots") {
    if (!readablePaths.length) {
      list.appendChild(el("div", "none",
        "No readable paths are granted to this account. An administrator grants them in a role, on the Roles page."));
      return;
    }
    for (const path of readablePaths) {
      list.appendChild(chooserRow(menuId, role, { path, name: path, kind: "d" }));
    }
    return;
  }

  // A directory. A cache hit is the common case — the panel you stepped into was
  // the one on the right a moment ago, or the centre you came from — and then
  // this finishes before the frame does.
  if (!view.listings.has(target.path)) {
    list.appendChild(el("div", "none", "Listing…"));
  }

  let entries;
  try {
    entries = await listDirectory(view, target.path);
  } catch (error) {
    if (token !== view.tokens[role]) return;
    list.innerHTML = "";
    list.appendChild(el("div", "none", `Could not list this directory: ${error.message}`));
    return;
  }
  if (token !== view.tokens[role]) return;

  list.innerHTML = "";
  // The left panel is a map of the tree, so it shows directories even in the menu
  // that attaches files: a file in it would be a row with nothing to do. The
  // directory menu's own listings carry no files either, so its emptiness is
  // always "no subdirectories" — `Empty.` would claim the folder is empty when
  // the listing simply was never asked for files.
  const dirsOnly = role === "left" || !view.files;
  const all = dirsOnly ? entries.filter((entry) => entry.kind === "d") : entries;
  const shown = all.slice(0, CHOOSER_ROWS);
  if (!shown.length) {
    if (side) panel.classList.add("empty");
    else list.appendChild(el("div", "none", dirsOnly ? "No subdirectories." : "Empty."));
  }
  for (const entry of shown) {
    list.appendChild(chooserRow(menuId, role, {
      path: join(target.path, entry.name),
      name: entry.name,
      kind: entry.kind,
    }));
  }
  if (all.length > shown.length) {
    list.appendChild(el("div", "none", `…and ${all.length - shown.length} more.`));
  }
  if (role === "centre") list.appendChild(filterEmptyNote());
}

// Listing, cached and single-flight.
//
// `files` is a property of the menu rather than of the caller — the directory
// chooser wants directories, the attachment chooser wants both — which is what
// lets one cache entry serve every panel: the parent panel reuses the listing the
// centre already paid for, and walking back out costs nothing at all.
function listDirectory(view, path) {
  const known = view.listings.get(path);
  if (known) return Promise.resolve(known);
  const going = view.inflight.get(path);
  if (going) return going;

  const files = view.files ? "&files=1" : "";
  const request = api("GET", `/api/browse?path=${encodeURIComponent(path)}${files}`)
    .then((answer) => {
      view.inflight.delete(path);
      const entries = answer.entries || [];
      view.listings.set(path, entries);
      return entries;
    })
    .catch((error) => {
      // Not cached: a listing that failed is one a retry may fix, and a remembered
      // failure would outlive the reason for it.
      view.inflight.delete(path);
      throw error;
    });
  view.inflight.set(path, request);
  return request;
}

// Moving the centre panel, whichever way it moves. `direction` is only for the
// arrival animation — a step into a folder comes from the right, a step out of one
// from the left — because in a box this size the cheapest honest way to say "you
// moved" is one composited transform on one panel, and nothing at all when the
// reader has asked for reduced motion.
//
// `select` is for a step taken with the arrows: the path of the row to land on, or
// "first". The keyboard then stays in the list with a row already picked, so the
// next arrow walks on from there — sending the caret back to the field would make
// every step need a Down before the one after it could be taken.
async function descendTo(menuId, path, direction, { remember = true, select = null } = {}) {
  const parts = chooserParts(menuId);
  const view = chooserViews.get(menuId);
  if (!view) return;
  view.centre = path;
  view.selected = null;
  view.filter = "";
  parts.field.value = "";
  if (remember) rememberPlace(view, path);
  if (!select) {
    renderChooser(menuId, direction);
    parts.field.focus();
    return;
  }
  parts.centre.focus({ preventScroll: true });
  await renderChooser(menuId, direction);
  // Moved on while the listing was in flight: that step owns the selection now.
  if (chooserViews.get(menuId) !== view || view.centre !== path) return;
  const rows = [...parts.centre.querySelectorAll(".path")];
  const row = rows.find((r) => r.dataset.path === select) || rows[0];
  if (row) selectRow(menuId, row);
}

// Where this menu has been, so a mouse has a back and a forward like every other
// navigator. A step through history does not add to it — otherwise Back would
// push the place it just left and Forward would never become available.
function rememberPlace(view, path) {
  if (view.history[view.at] === path) return;
  view.history = view.history.slice(0, view.at + 1);
  view.history.push(path);
  view.at = view.history.length - 1;
}

function stepHistory(menuId, delta) {
  const view = chooserViews.get(menuId);
  if (!view) return;
  const next = view.at + delta;
  if (next < 0 || next >= view.history.length) return;
  view.at = next;
  return descendTo(menuId, view.history[next], delta < 0 ? "up" : "in", { remember: false });
}

// `keys` lands the selection on the directory just left, so Left then Right is a
// round trip.
function climb(menuId, keys = false) {
  const view = chooserViews.get(menuId);
  if (!view || view.centre === null) return;
  return descendTo(menuId, parentWithinReach(view.centre), "up",
    { select: keys ? view.centre : null });
}

function arrive(parts, direction) {
  const panel = parts.centre.closest(".chooser-panel");
  if (!panel) return;
  panel.classList.remove("arrive-left", "arrive-right");
  // Reading layout restarts the animation when the same direction is travelled
  // twice in a row; without it the class is already there and the second step
  // would not move.
  void panel.offsetWidth;
  panel.classList.add(direction === "up" ? "arrive-left" : "arrive-right");
  panel.addEventListener("animationend", () => {
    panel.classList.remove("arrive-left", "arrive-right");
  }, { once: true });
  // A backstop for the case where the animation never runs — a reader who has
  // asked for reduced motion gets `animation: none`, and `animationend` is then
  // never fired. One timer per navigation, and it only ever removes a class.
  setTimeout(() => panel.classList.remove("arrive-left", "arrive-right"), 400);
}

// -- shared chooser parts -----------------------------------------------------

// One row. `role` decides what a press means, and that is the whole of the
// interaction model:
//
//   centre   open — a folder becomes the centre panel, a file is attached
//   left     open — the parent is a place you can go, not a choice you can take
//   right    nothing — it is what you are looking at, not what you are pointing at
//
// The one control in either menu that *takes* something is the small chip in the
// row's right-hand column, and it exists only where there is something to take.
// The row carries no focus: the field keeps the caret, the highlight is a class,
// and `aria-selected` finally means what a listbox says it means — which row is
// picked, rather than which directory is currently in force.
function chooserRow(menuId, role, { path, name, kind }) {
  const row = el("div", "path");
  row.id = `chooser-row-${++chooserRowSeq}`;
  row.dataset.path = path;
  row.dataset.name = name;
  row.dataset.kind = kind;
  row.dataset.filterText = name.toLowerCase();
  row.setAttribute("role", "option");
  row.setAttribute("aria-selected", "false");
  const attached = kind === "f" && attachments.includes(path);
  if (path === currentWorkingDir || attached) {
    row.classList.add("current");
    if (path === currentWorkingDir) row.setAttribute("aria-current", "true");
  }
  // Every panel is a place to go — the left because it is where you came from,
  // the right because it is what you are looking at — so every row outside the
  // centre is pressable. Only the centre's rows select.
  if (role !== "centre") row.classList.add("pickable");

  row.appendChild(el("span", "path-icon", kind === "d" ? "🗀" : "🗋"));
  const label = el("span", "path-name", name);
  // The name is ellipsized; the tooltip is the only way to the full value.
  label.title = path;
  row.appendChild(label);

  const actions = el("span", "path-actions");
  if (path === currentWorkingDir || attached) actions.appendChild(el("span", "path-check", "✓"));
  // A folder in the directory menu can be taken as the working directory; a file
  // in the attachment menu can be attached. A folder in the attachment menu has
  // nothing to take, so it has no chip and the row is the whole of it.
  if (role === "centre" && (kind === "d" ? menuId === "cwd-menu" : menuId === "attach-menu")) {
    const pick = el("button", "path-pick", kind === "d" ? "use" : "attach");
    pick.type = "button";
    pick.tabIndex = -1;
    pick.title = kind === "d"
      ? `Set the working directory to ${path}`
      : `Attach ${path} to the message`;
    // The chip sits inside the row, and the row opens: a press here is not a press
    // there. `mousedown` as well, or focus would move out of the filter on the way
    // to the click that closes the menu.
    pick.onmousedown = (event) => event.stopPropagation();
    pick.onclick = (event) => {
      event.stopPropagation();
      chooseRow(menuId, path);
    };
    actions.appendChild(pick);
  } else if (role === "centre") {
    actions.appendChild(el("span", "path-pick-slot"));
  }
  row.appendChild(actions);

  if (role === "centre") {
    // A press must not take the caret out of the filter: the field is where typing
    // goes, and a row that steals focus silently turns the filter off.
    row.onmousedown = (event) => event.preventDefault();
    row.onclick = () => openRow(menuId, path, kind);
    // Pointing at a row previews it without taking anything.
    row.onmousemove = () => hoverRow(menuId, row);
    row.onmouseleave = () => leaveRow(menuId, row);
  } else if (role === "left" || role === "right") {
    // A side panel is a place to go, not a choice to take: a press makes that row
    // the centre panel, wherever it was drawn. The right panel used to be inert —
    // a preview you could read but not act on — which made it the one panel whose
    // rows looked like every other row and were the only ones that did nothing.
    row.onclick = () => openRow(menuId, path, kind);
  }
  return row;
}

// A press on a row: a folder becomes the centre panel, a file is attached. There
// is nothing for a press to select — selecting is what the chip and the pointer
// do, and neither of them navigates.
function openRow(menuId, path, kind) {
  if (kind === "d") return descendTo(menuId, path, "in");
  if (menuId === "attach-menu") return attachFile(path);
  return undefined;
}

// The chip: take this one, from wherever it is.
function chooseRow(menuId, path) {
  if (menuId === "attach-menu") attachFile(path);
  else saveWorkingDir(path);
}

// The pointer previews what it rests on.
//
// A press opens a folder, so a press can no longer be what fills the right-hand
// panel. The pointer is the other thing that can point at a row, and it does it
// without taking anything: after 200ms of resting — long enough that sweeping
// across a directory costs nothing, short enough to feel like looking — the row
// becomes the selection and its contents appear on the right. `mousemove` rather
// than `mouseover` because a listing that appears under a still pointer must not
// select itself and spend a listing nobody asked for.
function hoverRow(menuId, row) {
  const view = chooserViews.get(menuId);
  if (!view) return;
  if (view.selected && view.selected.path === row.dataset.path) return;
  // Already counting for this row: moving *within* it must not restart the clock,
  // or a pointer drifting slowly across one row would never settle anywhere.
  if (view.hoverRow === row && view.hoverTimer) return;
  clearTimeout(view.hoverTimer);
  view.hoverRow = row;
  view.hoverTimer = setTimeout(() => {
    view.hoverTimer = 0;
    view.hoverRow = null;
    if (!row.isConnected || row.classList.contains("hidden")) return;
    selectRow(menuId, row);
  }, 200);
}

function leaveRow(menuId, row) {
  const view = chooserViews.get(menuId);
  if (!view || view.hoverRow !== row) return;
  clearTimeout(view.hoverTimer);
  view.hoverTimer = 0;
  view.hoverRow = null;
}

// The selection is a class and a preview, not a focus ring.
function selectRow(menuId, row) {
  const parts = chooserParts(menuId);
  const view = chooserViews.get(menuId);
  if (!view) return;

  view.selected = row
    ? { path: row.dataset.path, name: row.dataset.name, kind: row.dataset.kind }
    : null;

  for (const other of parts.centre.querySelectorAll(".path")) {
    const on = other === row;
    other.classList.toggle("selected", on);
    other.setAttribute("aria-selected", on ? "true" : "false");
  }
  if (row) {
    parts.centre.setAttribute("aria-activedescendant", row.id);
    reveal(parts.centre, row);
  } else {
    parts.centre.removeAttribute("aria-activedescendant");
  }

  labelCommit(menuId, view);
  schedulePreview(menuId, view);
}

// Keep the selection on screen. The list scrolls and the keyboard does not, so
// arrowing past the panel's height used to walk the highlight off the edge and
// leave the reader unable to see what Enter would take. Measured against the two
// rects rather than `offsetTop`, which is relative to the menu, not to the list.
function reveal(list, row) {
  const box = list.getBoundingClientRect();
  const at = row.getBoundingClientRect();
  if (at.top < box.top) list.scrollTop += at.top - box.top;
  else if (at.bottom > box.bottom) list.scrollTop += at.bottom - box.bottom;
}

// The one request a selection can cost, and not one per keystroke. Holding Down
// through a directory moves the selection thirty times a second, so the preview
// waits for the selection to rest — 90ms, short enough that a click feels
// immediate — and a path listed already paints without a request at all.
function schedulePreview(menuId, view) {
  clearTimeout(view.previewTimer);
  view.previewTimer = setTimeout(() => {
    if (chooserViews.get(menuId) !== view) return;   // closed and reopened since
    fillPanel(menuId, chooserParts(menuId), view, "right", previewOf(view));
  }, 90);
}

// One of the small buttons at the head of the path: back, forward, up. A real
// `<button>`, because a `span` with a click handler is not reachable by keyboard
// and does not announce itself as a control.
function navButton(glyph, name, onPress, enabled, className = "crumb-nav") {
  const button = el("button", className, glyph);
  button.type = "button";
  button.title = name;
  button.setAttribute("aria-label", name);
  button.disabled = !enabled;
  button.onclick = onPress;
  return button;
}

// Where you are, the way back out, and the one button that takes what you have.
// The button is the menu's OK: it takes the highlighted row, or the directory the
// crumb names when nothing is highlighted. That is what lets a press on a row mean
// "go there" without also meaning "and choose it".
function paintCrumbs(menuId, parts, view) {
  const bar = parts.crumbs;
  const centre = view.centre;
  bar.innerHTML = "";

  // Back and forward, for the hand rather than the keyboard: every navigator has
  // them at the head of the path, and until now the only way back was `↰` — which
  // goes up, not back, and cannot return to the sibling you were just in.
  bar.appendChild(navButton("‹", "Back", () => stepHistory(menuId, -1), view.at > 0));
  bar.appendChild(navButton("›", "Forward", () => stepHistory(menuId, 1),
    view.at < view.history.length - 1));

  if (centre !== null) {
    const parent = parentWithinReach(centre);
    bar.appendChild(navButton("↰",
      parent === null ? "Up to the granted directories" : `Up to ${parent}`,
      () => descendTo(menuId, parent, "up"), true, "crumb-up"));
  }

  const path = el("span", "crumb-path");
  // A path reads left to right whatever the box does with it. `direction: rtl` is
  // how the head of a long path is clipped instead of the tail, but on a path
  // that *fits* the bidi algorithm moved the leading slash to the far end — the
  // crumb read `home/alice/projects/`. `<bdi dir="ltr">` keeps the text one
  // left-to-right run inside a box that still overflows to the left.
  const text = el("bdi", "", centre === null ? "granted directories" : centre);
  text.dir = "ltr";
  path.title = centre === null ? "The directories policy grants this account" : centre;
  path.appendChild(text);
  bar.appendChild(path);

  const use = el("button", "crumb-use");
  use.type = "button";
  use.onclick = () => commitChooser(menuId);
  view.commitButton = use;
  bar.appendChild(use);
  labelCommit(menuId, view);
}

// What the button takes: the highlighted row when there is one, else the directory
// you are standing in. Null when there is neither — the granted roots with nothing
// highlighted — because a control that would do nothing is not drawn.
function commitTarget(menuId, view) {
  const selected = view.selected;
  if (selected) {
    if (menuId === "attach-menu" && selected.kind !== "f") return null;
    return { path: selected.path, leaf: selected.name };
  }
  if (menuId === "attach-menu" || !view.centre) return null;
  return { path: view.centre, leaf: null };
}

// The button says what it will do, because there are two possible targets and
// only one of them is named on screen: "use this folder" is the directory in the
// crumb, "use contracts" is the row highlighted beside it.
function labelCommit(menuId, view) {
  const button = view.commitButton;
  if (!button) return;
  const target = commitTarget(menuId, view);
  button.classList.toggle("hidden", !target);
  if (!target) return;
  const attaching = menuId === "attach-menu";
  button.textContent = target.leaf
    ? `${attaching ? "attach" : "use"} ${target.leaf}`
    : "use this folder";
  button.title = attaching
    ? `Attach ${target.path} to the message`
    : `Set the working directory to ${target.path}`;
}

function commitChooser(menuId) {
  const view = chooserViews.get(menuId);
  const target = view ? commitTarget(menuId, view) : null;
  if (!target) return false;
  if (menuId === "attach-menu") attachFile(target.path);
  else saveWorkingDir(target.path);
  return true;
}

// Enter is the OK button, with the one exception the attachment menu forces: a
// folder cannot be attached, so Enter there opens it rather than doing nothing.
// That is also the only keystroke the two menus disagree about.
function confirmChooser(menuId) {
  const view = chooserViews.get(menuId);
  if (!view) return false;
  if (!view.selected) {
    // A filter that matched nothing is not a choice: Enter must not quietly take
    // the directory you were already in.
    return view.filter ? false : commitChooser(menuId);
  }
  if (menuId === "attach-menu" && view.selected.kind === "d") {
    descendTo(menuId, view.selected.path, "in");
    return true;
  }
  return commitChooser(menuId);
}

// Right arrow: open the selected row. For a file, opening it is attaching it.
function openSelected(menuId, keys = false) {
  const view = chooserViews.get(menuId);
  if (!view || !view.selected) return false;
  if (view.selected.kind === "d") {
    descendTo(menuId, view.selected.path, "in", { select: keys ? "first" : null });
    return true;
  }
  if (menuId === "attach-menu") {
    attachFile(view.selected.path);
    return true;
  }
  return false;
}

// Rendered alongside the rows and revealed only when a filter matches nothing,
// so an empty result explains itself instead of looking like a broken list.
function filterEmptyNote() {
  return el("div", "none filter-empty hidden", "No matches.");
}

// The centre list, narrowed. A typed path filters on its last segment: someone
// typing `/srv/eng/do` is heading somewhere, and matching whole rows against the
// full string would empty the list at the first slash — the one keystroke that
// proves they know exactly where they are going.
//
// The first survivor becomes the selection as you type. That is what makes "type
// three letters, Enter" a complete act, and it is why the preview exists: you see
// what Enter will take before it takes it.
function applyChooserFilter(menuId) {
  const parts = chooserParts(menuId);
  const view = chooserViews.get(menuId);
  if (!view) return;

  const needle = view.filter.includes("/")
    ? view.filter.slice(view.filter.lastIndexOf("/") + 1)
    : view.filter;

  let first = null;
  for (const row of parts.centre.querySelectorAll(".path")) {
    const match = row.dataset.filterText.includes(needle);
    row.classList.toggle("hidden", !match);
    if (match && !first) first = row;
  }
  const empty = parts.centre.querySelector(".filter-empty");
  if (empty) empty.classList.toggle("hidden", !(needle && !first));
  selectRow(menuId, first);
}

// Enter on a typed path, rather than on a highlighted row.
//
// This is the part that makes a deep tree bearable: `/srv/eng/docs` and Enter
// goes straight there instead of a click per level. A trailing slash means "go
// into this directory"; anything else is treated as a directory to open, and in
// the file chooser a full file path attaches it.
//
// Nothing is validated here. The path goes to the same places a clicked row
// goes — `/api/browse`, which lists what the account reaches, or the PATCH
// route, where `within_reach` lives. A client-side guess about reach would be a
// second opinion about policy, and the honest answer for a path outside it is
// the server's refusal rather than a row that silently never appears.
async function chooserSubmitTyped(menuId) {
  const view = chooserViews.get(menuId);
  if (!view) return false;
  const typed = view.filter.trim();
  if (!typed.startsWith("/")) return false;

  if (menuId === "cwd-menu") {
    // A path the chooser can list is one to *navigate* to; the crumb bar's button
    // is how it becomes the working directory. Enter navigating and Enter
    // committing cannot both be Enter.
    await descendTo(menuId, typed.length > 1 ? typed.replace(/\/$/, "") : "/", "in");
    return true;
  }

  // The file chooser: a trailing slash is unambiguous navigation, and anything
  // else is tried as a file first because attaching is what this menu is for.
  if (typed.endsWith("/")) {
    await descendTo(menuId, typed.length > 1 ? typed.replace(/\/$/, "") : "/", "in");
    return true;
  }
  await attachFile(typed);
  return true;
}

// Does this element take text of its own? A keystroke addressed to a field has
// to reach that field and not the composer — the login form, a rename box, a
// chooser's filter and the composer itself are the cases the type-anywhere
// listener below must leave alone. Named once because two document-level
// handlers ask the question and a second copy is how they come to disagree.
function takesText(node) {
  return node instanceof HTMLElement
    && (node.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName));
}

// Keyboard for whichever chooser is open. Up and Down move the selection, Right
// opens it, Left climbs, Enter takes it, Escape closes. A picker that needs a
// mouse is a picker half the people cannot use.
//
// Typing is not handled here. It goes into the chooser's own field, which is where
// the text is visible, and the field keeps the caret throughout — so the arrows
// and Enter below are read from a field that always has focus, and no state has to
// be routed back to it.
function chooserKeydown(event) {
  const menu = [$("cwd-menu"), $("attach-menu")].find((m) => !m.classList.contains("hidden"));
  if (!menu) return;

  const parts = chooserParts(menu.id);
  const view = chooserViews.get(menu.id);
  if (!view) return;
  const target = event.target;
  const inField = target === parts.field;

  // Any other field — the composer, a rename box — keeps its keys. A chooser can
  // be open with the composer focused (tab, not click), and swallowing there
  // would eat someone's typing.
  if (!inField && takesText(target)) return;

  const rows = [...parts.centre.querySelectorAll(".path")]
    .filter((row) => !row.classList.contains("hidden"));
  const index = rows.findIndex((row) => row.classList.contains("selected"));

  // Alt+Left and Alt+Right are the history pair everywhere else on a desktop,
  // and they are free here: the bare arrows move the selection and step out.
  if (event.altKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
    event.preventDefault();
    stepHistory(menu.id, event.key === "ArrowLeft" ? -1 : 1);
    return;
  }

  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    if (!rows.length) return;
    // The keyboard wins over a pointer that happens to be resting on a row.
    clearTimeout(view.hoverTimer);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    // From nothing selected, Down starts at the top and Up at the bottom.
    const next = index < 0
      ? (delta > 0 ? 0 : rows.length - 1)
      : (index + delta + rows.length) % rows.length;
    selectRow(menu.id, rows[next]);
    // The caret leaves the field the moment the arrows take over. Without this,
    // the text that got you here keeps its claim on Left and Right, so a filter
    // typed a second ago is the reason the hierarchy will not move — which is
    // backwards: typing narrows the list, then the arrows are for walking it.
    // Focus goes to the list, which is where `aria-activedescendant` was always
    // pointing anyway; a key typed from here is routed back into the field.
    parts.centre.focus({ preventScroll: true });
  } else if (event.key === "ArrowLeft") {
    // While the field is still being edited the arrows belong to the caret —
    // someone mid-path needs to move inside it. The moment a Down has moved the
    // selection, focus is on the list and these are the menu's again.
    if (inField && parts.field.value) return;
    climb(menu.id, true);
  } else if (event.key === "ArrowRight") {
    if (inField && parts.field.value) return;
    if (!openSelected(menu.id, true)) return;
  } else if (event.key === "Enter") {
    // A whole path typed into the field wins over the selection: `/srv/eng/docs`
    // and Enter is the fast way through a deep tree. Otherwise Enter is the OK
    // button, and takes whatever the button names.
    event.preventDefault();
    chooserSubmitTyped(menu.id).then((went) => {
      if (!went) confirmChooser(menu.id);
    });
    return;
  } else if (event.key === "Escape") {
    menu.classList.add("hidden");
    (menu.id === "cwd-menu" ? $("cwd-pick") : $("attach")).focus();
  } else if (!inField && event.key.length === 1
             && !event.ctrlKey && !event.metaKey && !event.altKey
             && !activates(target)) {
    // Typed at a row or at the button rather than the field: send it to the field
    // instead of dropping it, so starting to type from anywhere in the menu
    // works. Space over something that activates is still a press.
    view.filter = (parts.field.value + event.key).toLowerCase();
    parts.field.value = view.filter;
    applyChooserFilter(menu.id);
    parts.field.focus();
  } else {
    return;
  }
  event.preventDefault();
}

// The parent, or null to mean "back to the list of roots". Null rather than the
// parent string once `path` is itself a root, because a root's parent is outside
// policy.
function parentWithinReach(path) {
  if (readablePaths.includes(path)) return null;
  const cut = path.lastIndexOf("/");
  if (cut <= 0) return null;
  const parent = path.slice(0, cut);
  return readablePaths.some((root) => parent === root || parent.startsWith(`${root}/`))
    ? parent
    : null;
}

const join = (base, name) => (base.endsWith("/") ? `${base}${name}` : `${base}/${name}`);
// Defensive about a missing path: the tool card asks for the leaf of arguments
// that may be absent, and `undefined.slice` would blank the whole card.
const basename = (path) => {
  const s = String(path ?? "");
  return s.slice(s.lastIndexOf("/") + 1) || s;
};
// What a path is called on a scan line. A tool result past the preview is
// written to the dispatch scratch as `<32-hex digest>.stdout.txt` (D42), and the
// digest is the part the eye skips, so it is clipped to eight characters —
// enough to tell two of them apart. Only under `/scratch`, so a user's own
// hex-named file is left whole.
const subjectName = (path) => {
  const name = basename(path);
  const digest = /^[0-9a-f]{16,}/.exec(name);
  if (digest && /\/scratch\//.test(String(path))) {
    return `${digest[0].slice(0, 8)}…${name.slice(digest[0].length)}`;
  }
  return name;
};

// Record that a result wrote `result.persisted` — one `<dispatch>.<stream>.txt`
// per stream — so a later read of the path can say what produced it. The
// producing card carries its verb and subject (`card._origin`), so the label is
// the same words its own row used.
function rememberScratch(result, card) {
  if (!result.persisted || !card || !card._origin) return;
  for (const [stream, path] of Object.entries(result.persisted)) {
    if (path) scratchOrigins.set(path, { ...card._origin, stream });
  }
}

// `stdout of Run “pytest -q”` for a scratch path this branch wrote, or `""` for
// one it did not — a cross-session read, a `git` checkout in scratch, or a
// result from before the path was carried. The caller falls back to the file's
// name, which is what it showed before.
function scratchOrigin(path) {
  const origin = scratchOrigins.get(String(path ?? ""));
  if (!origin) return "";
  const stream = origin.stream === "stderr" ? "stderr" : "stdout";
  const act = origin.subject ? `${origin.verb} “${origin.subject}”` : origin.verb;
  return `${stream} of ${act}`;
}

// -- models and GPU ----------------------------------------------------------

// Admin-only. The server re-checks on every route, so this flag decides whether
// a control is *drawn*, never whether it is permitted — a forged `is_admin` gets
// a prettier button and the same 403.
let isAdmin = false;
// Bumped to stop the watch loop: a loop whose number is no longer current
// returns at its next turn instead of painting a closed panel.
let gpuWatch = 0;

// The panel's state, and the time of the GPU sample it was drawn from. With
// `after`, the server answers only once there is a newer sample (or after half
// a minute), which is how the bar follows a load without polling.
async function loadModelState(after = null) {
  let body;
  try {
    // Written out per branch, like the load and unload routes, so the path is
    // one the server can be checked to serve.
    const path = after === null
      ? "/api/models/state"
      : `/api/models/state?after=${encodeURIComponent(after)}`;
    body = await api("GET", path);
  } catch (error) {
    $("models").innerHTML = "";
    const wrap = el("div", "panel-body");
    wrap.appendChild(el("div", "none", `Could not read model state: ${error.message}`));
    $("models").appendChild(wrap);
    return null;
  }

  paintGpuChip(body.gpu);
  paintReasoning();
  renderModelPanel(body);
  const sampled = body.gpu && body.gpu.sampled_at;
  return typeof sampled === "number" ? sampled : null;
}

// The header chip: which model is in use.
//
// It used to carry a memory bar and a "18/24 GB" readout as well. The panel this
// button opens states both again, with the device name and the free figure
// besides — so the chip was a second copy of a number that is worth consulting
// when loading a model and not worth watching while writing a message.
//
// The reasoning level used to ride along (`qwen · high`) because it was decided in
// the panel, which may be closed. It now lives on the composer's status line,
// beside approval — the other choice about how the next turn runs — so the header
// no longer repeats it. What is left is the one thing with nowhere else to live:
// the model dropdown is hidden for an operator, so this is where the current model
// shows at a glance.
function paintGpuChip(gpu) {
  const model = $("model").value;
  $("gpu-model").textContent = model || "no model";

  // The GPU's own state reaches the chip only as a tooltip now. "no GPU" was
  // wrong on a machine with a 3090 in it — the sampler had a driver version
  // mismatch and could only report absence — so when there is a reason, the
  // reason is what the title says (N79).
  const device = (gpu && gpu.devices && gpu.devices[0]) || null;
  $("gpu-toggle").title = device
    ? "Models and GPU"
    : (gpu && gpu.unavailable_because) || "Models and GPU";
}

function renderModelPanel(body) {
  const panel = $("models");
  panel.innerHTML = "";

  const device = (body.gpu && body.gpu.devices && body.gpu.devices[0]) || null;
  panel.appendChild(panelHead(device, body.gpu));

  const wrap = el("div", "panel-body");
  const chat = body.chat || [];
  const models = body.models || [];

  // The chat models a person may pick, cloud and local in one list. The old panel
  // drew the choosable models and the loadable runtimes as two lists, so a local
  // model appeared twice and neither row said which control was the real one.
  if (chat.length) {
    for (const model of chat) wrap.appendChild(chatRow(model));
    // Declared on the appliance but not granted: kept in view so an operator can
    // still read the inventory, without letting it look selectable.
    const granted = new Set(chat.map((m) => m.name));
    const others = models.filter((m) => !granted.has(m.name));
    if (others.length) {
      wrap.appendChild(el("div", "also",
        `Also installed, not granted: ${others.map((m) => m.name).join(", ")}`));
    }
  } else {
    wrap.appendChild(el("div", "none",
      "No chat models are granted to this account. An administrator grants them in a role, on the Roles page."));
  }

  panel.appendChild(wrap);
}

// The card's header: what it is, and the capacity the local models compete for. The
// GPU figures live here as well as in the header chip, so "will this fit" is
// answerable from inside the window that loads it. There is no close button: the
// toggle that opened it closes it, and so does a click outside or Escape.
function panelHead(device, gpu) {
  const head = el("div", "panel-head");
  head.appendChild(el("h2", null, "Models"));
  head.appendChild(gpuSummary(device, gpu));
  return head;
}

function gpuSummary(device, gpu) {
  const box = el("span", "panel-gpu");
  if (!device) {
    box.appendChild(el("span", "dev", "no GPU"));
    const why = gpu && gpu.unavailable_because;
    if (why) box.title = why;
    return box;
  }
  box.appendChild(el("span", "dev", device.name));

  const pct = Math.round((device.memory_used_mb / device.memory_total_mb) * 100);
  const bar = el("span", "bar");
  const fill = el("span", "fill" + (pct >= 85 ? " full" : ""));
  fill.style.width = `${pct}%`;
  bar.appendChild(fill);
  box.appendChild(bar);

  box.appendChild(el("span", "gb",
    `${(device.memory_used_mb / 1024).toFixed(1)}/${(device.memory_total_mb / 1024).toFixed(0)} GB`));
  const free = (device.memory_total_mb - device.memory_used_mb) / 1024;
  box.title = `${device.name} — ${free.toFixed(1)} GB free`;
  return box;
}

// Close from the toggle that opened it, a click outside, or Escape. The watch
// stops with it: a request held for a window nobody has open is load for nothing.
function closeModelsPanel({ refocus = false } = {}) {
  $("models").classList.add("hidden");
  watchGpu(false);
  if (refocus) $("gpu-toggle").focus();
}

// One row of the model list. Cloud and local differ in what the meta says — a
// price per million tokens, or a size — and local rows carry the residency
// control the old "chat runtimes" section used to own, so the same model no
// longer appears twice.
function chatRow(model) {
  const current = model.name === $("model").value;
  const row = el("div", "model chat-model" + (current ? " chosen" : ""));

  row.appendChild(el("span", "dot " + (current ? "picked" : "idle")));
  row.appendChild(el("span", "model-name", model.name));
  // Local or cloud, derived from the endpoint by the server. This is the fact a user
  // most needs and the dropdown never showed, so it is a column rather than a note.
  row.appendChild(el("span", "badge " + (model.local ? "local" : "cloud"),
    model.local ? "local" : "cloud"));

  // Numbers only. A size answers "will it fit" and a price answers "what does a
  // turn cost"; state words beside them repeated what the buttons and the dot
  // already say, and were read as noise (the user removed them).
  const bits = [];
  if (model.vision) bits.push("vision");
  if (model.local) {
    if (model.loaded === true) {
      if (model.vram_mb != null)
        bits.push(`${(model.vram_mb / 1024).toFixed(1)} GB`);
    } else if (model.loaded === false && model.size_mb != null) {
      // The tilde marks an estimate, because it is the weights file size: the
      // resident figure depends on context size and KV cache.
      bits.push(`~${(model.size_mb / 1024).toFixed(1)} GB`);
    }
  } else if (model.input_cost_per_m || model.output_cost_per_m) {
    // Per million tokens, in and out. Zero for a local model, and the zero is the
    // point: it is what makes the comparison legible.
    bits.push(`$${model.input_cost_per_m}/$${model.output_cost_per_m} per M`);
  }
  // Why the last load failed, when the runtime saw why: a model that ran out of
  // GPU memory otherwise looks the same as one nobody has loaded yet.
  if (model.local && model.loaded !== true && model.error) bits.push(`could not load: ${model.error}`);
  row.appendChild(el("span", "model-meta", bits.join(" · ")));

  const actions = el("span", "model-actions");
  if (!model.registered) {
    // Granted but not in the catalog: selecting it would answer `model_error`, so it
    // is shown and not offered.
    actions.appendChild(el("span", "model-note", "unavailable"));
  } else if (model.local && model.loaded === true) {
    if (current) {
      actions.appendChild(el("span", "model-note in-use", "in use"));
    } else {
      const use = el("button", null, "Use");
      use.onclick = () => chooseModel(model);
      actions.appendChild(use);
    }
    // Unloadable whether or not it is the one in use, so VRAM can be freed without
    // switching models.
    const off = el("button", null, "Unload");
    off.onclick = () => actOnModel(model.name, "unload", off);
    actions.appendChild(off);
  } else {
    const use = el("button", null,
      current ? "in use" : model.local && model.loaded === false ? "Use & load" : "Use");
    use.disabled = current;
    if (!current) use.onclick = () => chooseModel(model);
    actions.appendChild(use);
  }
  row.appendChild(actions);

  return row;
}

// Selecting a model also loads it, because those are one intention. The hidden
// `#model` select remains the single source of truth for what a turn sends — moving
// that would mean two places deciding which model is in use.

// The level a turn will send for `name`, or null when the model declares no
// levels. Filled lazily from the declared default, so a model never touched in
// the panel sends nothing and the server applies its own default.
//
// Levels are validated against the declaration rather than trusted from the
// map, because a stale choice from a previous declaration would be refused by
// the server and end the turn in `reasoning_error`.
function chosenLevel(name) {
  const declared = reasoningByModel[name] || {};
  const levels = declared.levels || [];
  if (!levels.length) return null;
  if (levels.includes(reasoningChoice[name])) return reasoningChoice[name];
  const fallback = declared.default || levels[0];
  reasoningChoice[name] = fallback;
  return fallback;
}

// The level names that mean "do not think". The server spells them the same
// way (`_OFF_LEVELS` in `reasoning.py`), and that is the one thing about a
// level the client cannot read off the declaration: the list says which names
// exist, not which of them is the absence of the feature. A test asserts the
// two lists agree, because a name that means off to the server and a step of
// thinking to the meter is a mark that fills for a turn that will not think.
const OFF_LEVELS = new Set(["none", "off"]);

// Where `level` sits on the ramp: `at` bars filled out of `of`, the ramp being
// one bar per *live* level. `off` is the empty position rather than a step of
// its own — it is the absence of thinking, and a filled bar would read as
// effort spent.
//
// The rank is the level's position in the declared list, which is a reading of
// that list rather than a second opinion about what a level means: the catalogs
// order levels cheapest-first, and the meter draws the order the server already
// validates against. Nothing here knows which dialect is underneath.
function reasoningDepth(levels, level) {
  const live = levels.filter((name) => !OFF_LEVELS.has(name));
  const at = OFF_LEVELS.has(level) ? 0 : live.indexOf(level) + 1;
  return { at: Math.max(0, at), of: live.length };
}

// Draw `of` bars into `host`, the first `at` filled. Cleared first, so a
// repaint after a model change cannot leave the previous model's longer ramp
// behind — a stale bar is a wrong level, drawn confidently.
function fillBars(host, at, of) {
  host.replaceChildren();
  for (let i = 1; i <= of; i += 1) host.appendChild(el("i", i <= at ? "on" : null));
}

// The reasoning chip on the composer's status line: the level for the model in
// the dropdown. Rebuilt whenever the model changes, because the levels are the
// model's own declaration — qwen offers none/low/medium/xhigh, glm low/high/max,
// deepseek off/low/high/max — and hidden entirely for a model that declares
// none. The off switch is a level like any other here: which request shape it
// turns into is the server's business (`reasoning.py`), and a client that knew
// would be a second opinion about the dialect. A control offering a level
// the model does not take would send a value the server refuses, which ends the
// turn.
//
// It sits beside approval because they are the same kind of setting: how the next
// turn runs, not what it carries. This is now the only place a level is chosen or
// shown; the models panel no longer draws the chips and the header no longer
// repeats the level.
function paintReasoning() {
  const chip = $("reasoning-chip");
  const model = $("model").value;
  const levels = (reasoningByModel[model] || {}).levels || [];

  if (!model || !levels.length || $("model").disabled) {
    chip.classList.add("hidden");
    $("reasoning-menu").classList.add("hidden");
    return;
  }

  const chosen = chosenLevel(model);
  chip.classList.remove("hidden");
  $("reasoning-level").textContent = chosen;

  // The mark carries the level, and the word names it. Both, because they say
  // different things: bars are read at a glance and a word is read exactly, and
  // "3 of 4" is not something four bars can spell.
  const depth = reasoningDepth(levels, chosen);
  fillBars(chip.querySelector(".depth"), depth.at, depth.of);

  // The meter is `aria-hidden` and the button's only text is the level word, so
  // the level has to be named here or the control is announced as "medium" —
  // a bare adjective beside a model name, with nothing saying what it is a
  // level of.
  const steps = depth.of ? ` (${depth.at} of ${depth.of})` : "";
  chip.title = `Reasoning: ${chosen}${steps} for ${model}`;
  chip.setAttribute("aria-label", chip.title);

  const host = $("reasoning-choices");
  host.innerHTML = "";
  for (const level of levels) {
    const button = el("button", null, level);
    // The same meter on every row, filled to that row's depth: the menu is
    // where the ramp should be legible, since the list is otherwise a few bare
    // words whose order says nothing about which end is which.
    const meter = el("span", "depth");
    meter.setAttribute("aria-hidden", "true");
    const row = reasoningDepth(levels, level);
    fillBars(meter, row.at, row.of);
    button.prepend(meter);
    button.setAttribute("aria-pressed", String(level === chosen));
    button.onclick = () => chooseReasoning(level);
    host.appendChild(button);
  }
}

function chooseReasoning(level) {
  const model = $("model").value;
  if (!model) return;
  reasoningChoice[model] = level;
  // Closed before the repaint, like the approval picker: a menu that redraws
  // itself under the pointer invites a second click on a button that moved.
  $("reasoning-menu").classList.add("hidden");
  // Repaint through the chooser rather than marking the pressed chip by hand:
  // `chosenLevel` is what validates the choice against the declaration, and a
  // second place deciding what is in force is how the two drift.
  paintReasoning();
  persistBranchChoice({ reasoning: level }).catch((error) => {
    note(`Could not save the reasoning level: ${error.message}`);
  });
}

// -- per-branch choices ------------------------------------------------------
//
// Model, reasoning and approval are properties of the branch, not of the window.
// The old client kept the model in one `<select>` and the level in a map keyed
// by model, so switching sessions silently changed what the next turn would run
// with. Now each branch remembers its own, and the controls are repainted from
// it whenever a view comes on screen.

function availableModel(name) {
  const info = name && modelInfo.get(name);
  return Boolean(info && info.registered);
}

function effectiveModel(view) {
  if (view && availableModel(view.model)) return view.model;
  if (availableModel(accountDefaults.model)) return accountDefaults.model;
  return "";
}

function effectiveReasoning(view, model) {
  const declared = reasoningByModel[model] || {};
  const levels = declared.levels || [];
  if (view && view.reasoning && levels.includes(view.reasoning)) return view.reasoning;
  if (accountDefaults.reasoning && levels.includes(accountDefaults.reasoning)) {
    return accountDefaults.reasoning;
  }
  return null;
}

// Paint the controls from one branch. Called when a view is shown and after any
// choice is written, so the control on screen and the row the next turn reads
// are one statement.
function applyBranchChoices(view) {
  branchApproval = view.approval ?? null;
  const model = effectiveModel(view);
  $("model").value = model;
  if (model) {
    const wanted = effectiveReasoning(view, model);
    if (wanted) reasoningChoice[model] = wanted;
    else delete reasoningChoice[model];
  }
  paintReasoning();
  $("gpu-model").textContent = model || "no model";
  if (policyState) renderApproval(policyState.policy || {});
  paintBranchChip();
  if (view.model && !availableModel(view.model) && !view._missingNoted) {
    view._missingNoted = true;
    note(
      `${view.model} is no longer available to this account. ` +
        "Pick another model for this branch.",
    );
  }
}

function applyDraftChoices() {
  const model = (draftModel && availableModel(draftModel))
    ? draftModel
    : (availableModel(accountDefaults.model) ? accountDefaults.model : "");
  $("model").value = model;
  if (model) {
    const declared = reasoningByModel[model] || {};
    const levels = declared.levels || [];
    const wanted = draftReasoning && levels.includes(draftReasoning)
      ? draftReasoning
      : (accountDefaults.reasoning && levels.includes(accountDefaults.reasoning)
        ? accountDefaults.reasoning
        : null);
    if (wanted) reasoningChoice[model] = wanted;
    else delete reasoningChoice[model];
  }
  paintReasoning();
  $("gpu-model").textContent = model || "no model";
  if (policyState) renderApproval(policyState.policy || {});
}

function applyCurrentChoices() {
  if (currentView) applyBranchChoices(currentView);
  else applyDraftChoices();
}

// Write one choice where it belongs: the branch row when there is one, the
// draft otherwise. The server's reply is the authority, because it clamps
// approval to the ceiling and refuses a model that has gone away.
async function persistBranchChoice(change) {
  if (!currentView) {
    if ("model" in change) draftModel = change.model;
    if ("reasoning" in change) draftReasoning = change.reasoning;
    if ("approval" in change) draftApproval = change.approval;
    applyDraftChoices();
    return;
  }
  const saved = await api("PATCH", `/api/branches/${currentView.id}`, change);
  currentView.branch = { ...currentView.branch, ...saved };
  if ("model" in change) currentView.model = saved.model ?? null;
  if ("reasoning" in change) currentView.reasoning = saved.reasoning ?? null;
  if ("approval" in change) currentView.approval = saved.approval ?? null;
  const session = sessions.get(currentView.sessionId);
  if (session) {
    session.branches = (session.branches || []).map((branch) =>
      branch.id === currentView.id ? { ...branch, ...saved } : branch);
  }
  applyBranchChoices(currentView);
}

// The branch chip, and the menu it opens. Hidden for a session that has only
// its root branch: "main" on a conversation that has never forked is a label
// with nothing to choose.
function paintBranchChip() {
  const chip = $("branch-chip");
  const menu = $("branch-menu");
  if (!currentView) {
    chip.classList.add("hidden");
    menu.classList.add("hidden");
    return;
  }
  const session = sessions.get(currentView.sessionId) || currentView.session;
  const branches = (session && session.branches) || [currentView.branch];
  chip.classList.toggle("hidden", branches.length < 2);
  $("branch-label").textContent = branchLabel(currentView);
  chip.title = branches.length > 1
    ? `Branch ${branchLabel(currentView)} of ${branches.length}`
    : "The root branch of this session";
}

// A branch's name outside a view: `main` for the root, the first thing said in
// it for a fork, and a last resort for a fork that has not spoken yet. The
// current view's copy is preferred because a just-created branch has spoken
// before the branch list has been reloaded.
function fullBranchLabel(branch, view = null) {
  if (!branch || !branch.forked_from) return "main";
  const text = ((view && view.id === branch.id ? view.firstMessage : null)
    || branch.first_message || "").trim().replace(/\s+/g, " ");
  return text || "branch";
}

function branchRecordLabel(branch, view = null) {
  const text = fullBranchLabel(branch, view);
  return text === "main" || text === "branch" ? text : text.slice(0, 28);
}

// A fork at a branch's first message is recorded under that branch, but the
// turn before the boundary belongs to its parent: the two branches share every
// word above the fork. The tree shows it at the boundary it actually left, not
// one level below the branch it was made from. `inherited_event_count` is the
// test: the child copied the same prefix as the parent, so both start at the
// same turn.
function displayParentId(branch, byId) {
  let cursor = branch;
  const seen = new Set();
  while (cursor && cursor.forked_from && !seen.has(cursor.id)) {
    seen.add(cursor.id);
    const parent = byId.get(cursor.forked_from);
    if (!parent) return cursor.forked_from;
    if ((cursor.inherited_event_count || 0) === (parent.inherited_event_count || 0)) {
      cursor = parent;
      continue;
    }
    return parent.id;
  }
  return null;
}

function renderBranchMenu() {
  const host = $("branch-choices");
  host.innerHTML = "";
  if (!currentView) return;
  const session = sessions.get(currentView.sessionId) || currentView.session;
  const branches = (session && session.branches) || [currentView.branch];
  const byId = new Map(branches.map((branch) => [branch.id, branch]));
  const children = new Map();
  for (const branch of branches) {
    const parentId = displayParentId(branch, byId);
    if (!children.has(parentId)) children.set(parentId, []);
    children.get(parentId).push(branch);
  }

  const rows = [];
  const walk = (parentId, depth) => {
    for (const branch of children.get(parentId) || []) {
      rows.push({ branch, depth });
      walk(branch.id, depth + 1);
    }
  };
  walk(null, 0);
  // A branch whose recorded parent is missing is still a branch this account
  // can read. Show it at the top rather than dropping it from the only list of
  // branches in the window.
  for (const branch of branches) {
    if (!rows.some((row) => row.branch.id === branch.id)) rows.push({ branch, depth: 0 });
  }

  for (const { branch, depth } of rows) {
    const view = views.get(branch.id);
    const label = branch.id === currentView.id
      ? branchLabel(currentView)
      : branchRecordLabel(branch, view);
    const button = el("button", branch.id === currentView.id ? "current" : null, label);
    button.style.setProperty("--depth", depth);
    button.setAttribute("role", "menuitem");
    if (branch.id === currentView.id) button.setAttribute("aria-current", "true");
    const when = new Date(branch.created_at).toLocaleString();
    button.title = branch.forked_from
      ? `Forked from another branch · ${when}`
      : `The root branch · ${when}`;
    // The live state from the view, when this window has one; a branch that is
    // running behind the visible one is the reason the menu exists.
    if (view && view.running) button.appendChild(el("span", "branch-state", "running"));
    if (view && view.unread) button.appendChild(el("span", "branch-state", "finished"));
    button.onclick = () => switchBranch(branch.id);
    host.appendChild(button);
  }
}

// A pencil on a turn-opening user message. Editing it forks; the original is
// never rewritten. Only a prompt this branch actually wrote is offered: the
// inherited prefix belongs to another branch's history, and a fork made from a
// point inside it would have no turn of this branch's own to name.
function attachEditAction(node, view, seq, text) {
  if (!node || !view || !seq) return null;
  const button = el("button", "edit-action");
  button.type = "button";
  button.innerHTML = PENCIL;
  button.title = "Edit this message in a new branch. The original stays here.";
  button.setAttribute("aria-label", "Edit this message in a new branch");
  button.onclick = (event) => {
    event.stopPropagation();
    startEdit(node, view, seq, text);
  };
  node.appendChild(button);
  return button;
}

function startEdit(node, view, seq, text) {
  if (!node || !view) return;
  if (view.editing) {
    const open = view.editing.node.querySelector("textarea");
    if (open) open.focus({ preventScroll: true });
    return;
  }
  if (view !== currentView) return;
  markPromptCursor(null);
  view.editing = { node, seq, text };
  node.classList.add("editing");
  node.replaceChildren(editorEl(view));
  paintComposerMode();
  const ta = node.querySelector("textarea");
  if (ta) {
    autosize(ta);
    ta.focus({ preventScroll: true });
    ta.setSelectionRange(ta.value.length, ta.value.length);
    node.scrollIntoView({ block: "nearest" });
  }
}

function editorEl(view) {
  const edit = view.editing;
  const wrap = el("div", "editor");
  const ta = document.createElement("textarea");
  ta.rows = 1;
  ta.value = edit.text;
  ta.spellcheck = true;
  ta.setAttribute("aria-label", "Message text; sending starts a new branch");
  const status = el("div", "edit-status");
  status.setAttribute("role", "status");
  const actions = el("div", "edit-actions");
  const cue = el("span", "edit-cue");
  cue.title = "Sending here starts a new branch; the original and everything below stay as they are.";
  const glyph = el("span", "edit-glyph", "⑂");
  glyph.setAttribute("aria-hidden", "true");
  cue.appendChild(glyph);
  cue.appendChild(document.createTextNode("new branch"));
  const cancel = el("button", "edit-cancel", "Cancel");
  cancel.type = "button";
  cancel.title = "Cancel (Esc)";
  const send = el("button", "edit-send", "Send");
  send.type = "button";
  send.title = "Send in a new branch (Enter)";
  send.disabled = !edit.text.trim();
  actions.append(cue, cancel, send);
  wrap.append(ta, status, actions);
  wrap._editSend = send;
  wrap._editCancel = cancel;
  wrap._editStatus = status;

  const sync = () => {
    send.disabled = !ta.value.trim();
    autosize(ta);
  };
  ta.addEventListener("input", sync);
  ta.addEventListener("keydown", (event) => {
    if (event.isComposing || event.keyCode === 229) return;
    if (!view.editing || view.editing.busy) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      cancelEdit(view);
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      commitEdit(view);
    }
  });
  cancel.onclick = () => cancelEdit(view);
  send.onclick = () => commitEdit(view);
  return wrap;
}

function setEditStatus(edit, message) {
  const editor = edit && edit.node.querySelector(".editor");
  if (!editor || !editor._editStatus) return;
  editor._editStatus.textContent = message;
}

function cancelEdit(view) {
  const edit = view && view.editing;
  if (!edit || edit.busy) return;
  view.editing = null;
  edit.node.classList.remove("editing");
  edit.node.textContent = edit.text;
  attachEditAction(edit.node, view, edit.seq, edit.text);
  paintComposerMode();
  $("input").focus({ preventScroll: true });
}

async function commitEdit(view) {
  const edit = view && view.editing;
  if (!edit || edit.busy) return;
  const ta = edit.node.querySelector("textarea");
  const text = (ta ? ta.value : "").trim();
  if (!text) return;
  edit.busy = true;
  if (ta) ta.disabled = true;
  const editor = edit.node.querySelector(".editor");
  if (editor && editor._editSend) editor._editSend.disabled = true;
  if (editor && editor._editCancel) editor._editCancel.disabled = true;
  setEditStatus(edit, "");

  let created;
  try {
    created = await forkAtTurn(view, edit.seq);
  } catch (error) {
    edit.busy = false;
    if (ta) {
      ta.disabled = false;
      ta.focus({ preventScroll: true });
    }
    if (editor && editor._editSend) editor._editSend.disabled = false;
    if (editor && editor._editCancel) editor._editCancel.disabled = false;
    setEditStatus(edit, `Could not fork: ${error.message}`);
    return;
  }

  // The fork exists. Restore the source bubble before opening the new branch:
  // a source that is kept because its turn is running must not be left holding
  // an editor its view no longer claims.
  const source = views.get(view.id) || view;
  const sourceWasRunning = source.running;
  view.editing = null;
  edit.node.classList.remove("editing");
  edit.node.textContent = edit.text;
  attachEditAction(edit.node, view, edit.seq, edit.text);
  paintComposerMode();

  const session = sessions.get(view.sessionId);
  if (session) {
    session.branches = [...(session.branches || []), created.branch];
    session.open_branch_id = created.branch.id;
  }
  if (created.warnings && created.warnings.length) note(created.warnings.join(" "));

  await openSession(view.sessionId, created.branch.id);
  const opened = views.get(created.branch.id);
  if (!opened) {
    const box = $("input");
    box.value = text;
    autosize(box);
    paintComposerMode();
    note("The branch was created, but it could not be opened. Your message is in the box.");
    return;
  }

  // The source transcript has a fork node it has never drawn. Drop it so the
  // next look refetches the branch list; a running source is kept, because its
  // socket is the turn.
  if (!sourceWasRunning && source !== opened && source !== currentView) dropView(source);

  const sent = await sendEdited(opened, text);
  if (!sent) {
    const box = $("input");
    box.value = text;
    autosize(box);
    paintComposerMode();
    note("The branch was created, but the message did not send. It is in the box.");
  }
}

async function forkAtTurn(view, atSeq) {
  return api("POST", `/api/branches/${view.id}/fork`, {
    at_seq: atSeq,
    side: "before",
  });
}

// The message a turn will send. One reader of the model control, so a fork that
// sends the edited text and a typed message cannot disagree about the model or
// the level. The level is omitted unless this model declares it: sending one
// unasked-for is refused, and a choice left over from another model must not
// ride along. The view's copy is kept in step for the switch back.
function turnFrame(view, content) {
  const model = $("model").value;
  if (!model || $("model").disabled) {
    note("No model is available to send to.");
    return null;
  }
  const frame = { type: "message", content, model };
  const level = chosenLevel(model);
  if (level) frame.reasoning = level;
  if (view) {
    view.model = model;
    view.reasoning = level || null;
  }
  return frame;
}

async function sendEdited(view, content) {
  if (!view || view.running) return false;
  const frame = turnFrame(view, content);
  if (!frame) return false;
  markTurnStarted(view);
  if (!(await ensureSocket(view))) {
    markTurnFailed(view);
    return false;
  }
  if (!send(frame, view)) {
    markTurnFailed(view);
    return false;
  }
  withView(view, () => {
    agentTurn = null;
    endThought();
  });
  paintComposerMode();
  return true;
}

async function chooseModel(model) {
  $("model").value = model.name;
  // The level belongs to the model, so a model change resets it to that model's
  // declared default rather than carrying a name the new model does not have.
  const level = chosenLevel(model.name);
  if (level) reasoningChoice[model.name] = level;
  await persistBranchChoice({ model: model.name, reasoning: level });
  paintReasoning();
  if (model.local && model.loaded === false) {
    note(`Loading ${model.name}…`);
    await actOnModel(model.name, "load", el("button"));
    return;
  }
  note(`Using ${model.name}.`);
  await loadModelState();
}

async function actOnModel(name, action, button) {
  button.disabled = true;
  button.textContent = action === "load" ? "Loading…" : "Stopping…";
  try {
    // The verb is written out per branch rather than interpolated: `load` and
    // `unload` are two routes, not one route with a parameter, and a URL built
    // from a variable verb cannot be checked against the router.
    const path = action === "load"
      ? `/api/models/${encodeURIComponent(name)}/load`
      : `/api/models/${encodeURIComponent(name)}/unload`;
    await api("POST", path, {});
    // Loading returns as soon as the socket accepts, which is before the weights
    // are resident — so the panel is re-read rather than assuming the new state.
    // A first refresh is immediate for the button, then the timer catches up.
    await loadModelState();
  } catch (error) {
    note(`Could not ${action} ${name}: ${error.message}`);
    button.disabled = false;
  }
}

// Watched only while the panel is open. Each request is held by the server until
// there is a newer GPU sample — written by the runtime host as a model loads,
// sleeps or stops — so the bar follows within a second, and an idle open panel
// costs one request every half minute. Without a sample to wait on (no GPU, or
// the sampler not yet run) it falls back to reading every five seconds.
function watchGpu(on, after = null) {
  gpuWatch += 1;
  if (on) followGpu(gpuWatch, after);
}

async function followGpu(generation, after = null) {
  while (generation === gpuWatch) {
    const sampled = await loadModelState(after);
    if (generation !== gpuWatch) return;
    if (sampled === null) {
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
    after = sampled;
  }
}

// -- composer ----------------------------------------------------------------

function paintComposerMode() {
  // An edit owns the message being corrected, and the composer is the other
  // way to say something to the same branch. Two live inputs in one column is
  // a choice between them nobody asked for, so the box steps aside while the
  // bubble is editable. It is disabled rather than emptied: a draft already
  // in it is the reader's, and switching views must not destroy it.
  const editing = Boolean(currentView && currentView.editing);
  $("input").disabled = editing;
  $("input").placeholder = editing ? "Finish editing above…" : composerPlaceholder;
  $("composer").classList.toggle("editing", editing);
  $("send").classList.toggle("hidden", editing);
  // Full strength only when there is something to act on. Send is an icon
  // rather than a word, so "nothing typed yet" has no other way to show: the
  // label used to carry that by changing to "Steer", and a lone arrow at full
  // contrast over an empty box invites a click that does nothing.
  $("send").classList.toggle("ready", !editing && $("input").value.trim() !== "");
}

// -- attachments -------------------------------------------------------------

// An attachment is a path the agent reads, never bytes in the prompt: a file
// already inside policy, or one dropped on the window, which the daemon has
// written to its own `drops/` directory (D102). The server re-checks every path
// on every PATCH, so this list cannot widen what the agent may read.
//
// The attachment chooser: the same widget over the same place. A file being
// attached almost always lives under the directory the branch already works in,
// and `files=1` is what makes its listings carry files at all — the directory
// menu asks the same route for directories only.
let attachments = [];

function openAttachMenu() {
  return openChooser("attach-menu");
}

async function attachFile(path) {
  if (attachments.includes(path)) {
    $("attach-menu").classList.add("hidden");
    return;
  }
  await saveAttachments([...attachments, path]);
  $("attach-menu").classList.add("hidden");
}

// The whole list, every time. The server states the result, so the UI shows what
// was actually stored rather than what was requested — a 403 leaves the chips
// exactly as they were.
async function saveAttachments(next) {
  // Staged until there is a session, like the working directory, and sent
  // through the same PATCH — which is where every path is re-checked against
  // policy, including ones staged before the branch existed.
  if (drafting()) {
    draftAttachments = next;
    showAttachments(draftAttachments);
    return;
  }

  // The same capture as `saveWorkingDir`: an answer to a PATCH belongs to the
  // session that sent it, not to whichever one is on screen when it lands.
  const view = currentView;
  const id = activeBranchId;
  try {
    const saved = await api("PATCH", `/api/branches/${id}`, { attachments: next });
    if (view) view.attachments = saved.attachments ?? [];
    if (view === currentView) showAttachments(saved.attachments);
  } catch (error) {
    if (view === currentView) note(error.message);
  }
}

function showAttachments(list) {
  attachments = list || [];
  const bar = $("attachments");
  bar.innerHTML = "";
  bar.classList.toggle("hidden", !attachments.length);

  for (const path of attachments) {
    const chip = el("span", "chip");
    // The basename is what identifies a file to a person; the full path is in
    // the tooltip because two files can share a name.
    chip.appendChild(el("span", "chip-name", basename(path)));
    chip.title = path;

    const drop = el("button", "chip-x", "✕");
    drop.title = `Detach ${path}`;
    drop.onclick = () => saveAttachments(attachments.filter((p) => p !== path));
    chip.appendChild(drop);
    bar.appendChild(chip);
  }
}

// -- dropped files -----------------------------------------------------------
//
// A file dragged in from the desktop arrives as bytes and a bare name — the
// browser never says where it lives — so it is handed to the daemon serving this
// page, which writes it into its `drops/` directory on this machine and answers
// with that path (D102). From there it is an attachment like any other: the same
// PATCH, the same chip. The bytes never go to the core.
//
// The whole window is the target, because that is where a person lets go; the
// composer is what lights up, because that is where the file ends up.

// The daemon's own limit, repeated so an oversized file is refused before it is
// read rather than after it has been sent.
const DROP_LIMIT = 25 * 1024 * 1024;
let dragDepth = 0;

function carriesFiles(event) {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

function canDrop() {
  return !$("context").classList.contains("hidden");
}

function showDropTarget(on) {
  $("composer").classList.toggle("dropping", on);
}

window.addEventListener("dragenter", (event) => {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  showDropTarget(canDrop());
});

window.addEventListener("dragover", (event) => {
  if (!carriesFiles(event)) return;
  // Always, even where a drop is not accepted: without it Chromium opens the
  // file in this tab, replacing the conversation with a PDF viewer.
  event.preventDefault();
  event.dataTransfer.dropEffect = canDrop() ? "copy" : "none";
});

window.addEventListener("dragleave", (event) => {
  if (!carriesFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) showDropTarget(false);
});

window.addEventListener("drop", (event) => {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  showDropTarget(false);
  if (!canDrop()) return;
  // Read synchronously: the transfer is emptied once this handler returns.
  const items = Array.from(event.dataTransfer.items ?? []);
  const files = [];
  for (const [index, file] of Array.from(event.dataTransfer.files).entries()) {
    // A folder arrives as a zero-byte "file" that cannot be read. The chooser
    // is how a directory is named, as the working directory.
    const entry = items[index]?.webkitGetAsEntry?.();
    if (entry && entry.isDirectory) {
      note(`${file.name} is a folder. Choose it as the working directory instead.`);
      continue;
    }
    files.push(file);
  }
  attachDropped(files);
});

async function attachDropped(files) {
  const added = [];
  for (const file of files) {
    if (file.size > DROP_LIMIT) {
      note(`${file.name} is over the 25 MiB limit. Attach it with the + button instead.`);
      continue;
    }
    try {
      const response = await fetch(`/api/drops?name=${encodeURIComponent(file.name)}`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/octet-stream" },
        body: file,
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !payload?.path) {
        throw new Error(payload?.detail || payload?.error || `HTTP ${response.status}`);
      }
      added.push(payload.path);
    } catch (error) {
      note(`Could not attach ${file.name}: ${error.message}`);
    }
  }
  const next = [...attachments, ...added.filter((path) => !attachments.includes(path))];
  if (next.length !== attachments.length) await saveAttachments(next);
}

// -- background jobs ---------------------------------------------------------
//
// A command that outlived its wait is still running on this machine, under this
// account, and the person it belongs to has to be able to see it and stop it
// without asking the model to (D98). Those jobs live in the daemon that serves
// this page, so the row reads `/api/jobs` from the daemon directly — the core
// only hears how a job ended, and a round trip for state that is one process
// away could only be a slower copy of it.
//
// Read on an event and never on a timer: a `jobs` frame when a call detaches,
// the end of a turn, and the tab becoming visible again. The elapsed figure is
// redrawn from the rail's existing 15-second tick, because a clock is not a
// piece of state to poll for.

let jobs = [];

async function refreshJobs() {
  let body = null;
  try {
    body = await api("GET", "/api/jobs");
  } catch {
    // Signed out, or a daemon that is restarting: an empty row is the honest
    // reading, and the next event asks again.
    showJobs([]);
    return;
  }
  showJobs(body && body.jobs);
}

function showJobs(list) {
  jobs = Array.isArray(list) ? list : [];
  for (const job of jobs) {
    // The page's own clock, from an age the daemon measured: two machines'
    // clocks do not have to agree for an elapsed figure to be right.
    job.startedAt = Date.now() - (job.running_ms || 0);
  }

  const bar = $("jobs");
  bar.innerHTML = "";
  bar.classList.toggle("hidden", !jobs.length);

  for (const job of jobs) {
    const chip = el("span", "chip");
    // What identifies a job to a person is the command, which is why the
    // daemon sends the same label `job_output` lists. The tool is in the tip.
    chip.appendChild(el("span", "chip-name", job.label || job.tool));
    chip.appendChild(el("span", "job-time", jobDuration(job.running_ms)));
    chip.title = `${job.tool}: ${job.label || "a background job"}`;

    const stop = el("button", "chip-x", "✕");
    stop.title = "Stop this job";
    stop.setAttribute("aria-label", `Stop ${job.label || job.tool}`);
    stop.onclick = () => stopJob(job.job_id);
    chip.appendChild(stop);
    bar.appendChild(chip);
  }
  paintJobTimes();
}

// The elapsed figure only. Rebuilding the row on a tick would drop focus from a
// Stop button under the pointer.
function paintJobTimes() {
  const bar = $("jobs");
  const chips = [...bar.children];
  chips.forEach((chip, index) => {
    const job = jobs[index];
    const slot = chip.querySelector(".job-time");
    if (job && slot) slot.textContent = jobDuration(Date.now() - job.startedAt);
  });
}

async function stopJob(jobId) {
  try {
    await api("POST", `/api/jobs/${encodeURIComponent(jobId)}/stop`);
  } catch (error) {
    // Already over, or the daemon went away. Either way the row re-reads, and
    // the settlement reaches the conversation as a notice.
    note(error.message);
  }
  await refreshJobs();
}

// `45s`, `4m10s`, `1h02m` — the client daemon's spelling, so the row and what
// `job_output` tells the model agree about how long a build has been going.
function jobDuration(ms) {
  const seconds = Math.max(0, Math.floor((ms || 0) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m${String(seconds % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`;
}

// -- tool selection -----------------------------------------------------------

// Which tools this conversation may use.
//
// A *narrowing* and nothing else. Policy decides what this account may ever
// call, the server intersects this against that, and the capability gate
// re-checks every proposal — so a name here can only ever remove a tool from
// what the model is told exists, never add one (D50).
//
// Deselected means **absent**: no schema, no mention in the system prompt. The
// model is not told it has a tool it may not use, because the knowledge would
// cost context on every turn and the remedy is always the user's — which is
// also why the chip carries a count rather than hiding the state.
//
// `null`, not `[]`, is the default, and the distinction is load-bearing all the
// way down to the column. Null means "every granted tool" and keeps tracking
// the registry as an appliance installs more; an empty array is a user who
// switched everything off. A selection naming every tool currently installed
// would look identical to the default today and diverge from it tomorrow.
let toolCatalog = [];       // every tool, granted or not, from `/api/tools`
let toolSelection = null;   // null = all granted
let draftToolSelection = null;

// Loaded once per sign-in rather than per open: the registry is fixed for the
// life of the appliance's build, and the *grant* can change under it — which is
// what the PATCH re-check is for. A chooser that refetched on every open would
// pay a round trip to redraw a list that almost never changes.
async function loadTools() {
  try {
    toolCatalog = (await api("GET", "/api/tools")).tools || [];
  } catch (error) {
    // The *previous* catalog is kept rather than cleared, which matters on a
    // re-read: a narrowing already stored on the server stays in force whether
    // this request succeeded or not, and emptying the list here would hide the
    // chip and take away the only control that can undo it — leaving a
    // conversation quietly missing tools with nothing on screen to explain it.
    //
    // With nothing to keep (the first load failed) the chip stays hidden and
    // every granted tool is in play, which is this feature's own default and
    // the behaviour from before it existed. Said out loud only in that case,
    // because it is the one where the user loses a control they never had.
    if (!toolCatalog.length) {
      note(`Could not read the tool list: ${error.message}`);
    }
  }
  paintTools();
}

// The granted names, in registry order. The order matters: it is the order the
// server advertises in, so the panel reads the way the prompt does.
function grantedTools() {
  return toolCatalog.filter((tool) => tool.allowed).map((tool) => tool.name);
}

// What is actually in play right now, as a Set for the row checkboxes.
//
// Intersected with the grant rather than trusted, the same way the server
// intersects it: a selection can name a tool an operator has since revoked, and
// a checkbox ticked for something that cannot run is a lie the panel tells.
function activeTools() {
  const granted = grantedTools();
  if (currentSelection() === null) return new Set(granted);
  const wanted = new Set(currentSelection());
  return new Set(granted.filter((name) => wanted.has(name)));
}

// Staged before there is a session, like the working directory and attachments.
function currentSelection() {
  return drafting() ? draftToolSelection : toolSelection;
}

function paintTools() {
  const chip = $("tools-chip");
  const granted = grantedTools();

  // Nothing to choose between: an account granted one tool or none has no use
  // for a chooser, and a control that opens onto a single fixed row is noise on
  // a status line.
  if (granted.length < 2) {
    chip.classList.add("hidden");
    $("tools-menu").classList.add("hidden");
    return;
  }
  chip.classList.remove("hidden");

  const active = activeTools();
  const count = $("tools-count");
  // Silent at the default and loud when narrowed — the chip's whole job is to
  // explain, later, why the model did not reach for something.
  if (active.size === granted.length) {
    count.textContent = "";
    count.className = "";
    chip.title = `All ${granted.length} tools available to this conversation`;
  } else {
    count.textContent = `${active.size}/${granted.length}`;
    count.className = "narrowed";
    chip.title = active.size
      ? `${active.size} of ${granted.length} tools enabled for this conversation`
      : "No tools enabled for this conversation";
  }

  // The button says which way the press goes rather than naming the state it is
  // already in: at anything short of everything it selects all, and only at
  // exactly everything does it offer the opposite.
  const all = $("tools-all");
  all.textContent = active.size === granted.length ? "Select none" : "Select all";
  all.onclick = () =>
    saveToolSelection(active.size === granted.length ? [] : null);

  const host = $("tools-rows");
  host.innerHTML = "";
  for (const tool of toolCatalog) {
    host.appendChild(toolRow(tool, active));
  }

  const hint = $("tools-hint");
  hint.textContent = active.size
    ? "Unchecked tools are not offered to the model at all."
    : "The model will have no tools and must answer from the conversation.";
}

// One row: a checkbox, the name, and what the tool does.
//
// A `<label>` so the description is inside the hit area, and the checkbox is a
// real input so it arrives in the tab order and reads correctly to a screen
// reader without any aria of ours.
function toolRow(tool, active) {
  const row = el("label", "tool-row");
  const box = el("input");
  box.type = "checkbox";
  box.checked = active.has(tool.name);

  const text = el("span");
  text.appendChild(el("span", "tool-name", tool.name));
  // `mutating` is the one fact worth marking here: switching off something that
  // can change files is the main reason to open this panel.
  if (tool.allowed && tool.mutating) {
    text.appendChild(el("span", "tool-tag", "writes"));
  }
  // `/api/tools` omits diagnostics unless they are actually granted, so this
  // marks the unusual case rather than a whole category: a tool that exists to
  // exhaust a cgroup or escape a process group should not read as ordinary work.
  if (tool.diagnostic) {
    text.appendChild(el("span", "tool-tag diag", "diagnostic"));
  }

  if (!tool.allowed) {
    // Listed, not hidden, so the panel shows the whole surface — and why a tool
    // is missing, which "absent" cannot say. `reason` is the gate's own stable
    // code, translated here because the client is where a person reads it.
    row.classList.add("ungranted");
    box.disabled = true;
    box.checked = false;
    text.appendChild(el("span", "tool-what", ungrantedReason(tool)));
    row.append(box, text);
    return row;
  }

  text.appendChild(el("span", "tool-what", toolBlurb(tool)));
  // The model's full description on hover. The row shows one clipped line, and
  // this is where the detail stays reachable for someone deciding whether they
  // need the tool at all.
  row.title = (tool.description || "").replace(/\s+/g, " ").trim();
  // On `change` rather than on the label's click: a label forwards the click to
  // its input, so handling both would toggle twice and land back where it
  // started.
  box.onchange = () => toggleTool(tool.name, box.checked);
  row.append(box, text);
  return row;
}

// The one line a row shows under the tool's name.
//
// `summary` is the manifest field written for a person; `description` is written
// for the model and is a paragraph — every caveat about what the tool cannot do,
// which is exactly what it should be in a tool schema and exactly what nobody
// wants to read in a list of nineteen checkboxes.
//
// The fallback clips rather than wrapping the whole thing, so a tool that has
// not been given a summary yet degrades to a short label instead of flooding
// the panel. Cut at a sentence end where there is one early enough, because a
// first sentence is usually the summary somebody would have written.
function toolBlurb(tool) {
  if (tool.summary) return tool.summary;

  const text = (tool.description || "").replace(/\s+/g, " ").trim();
  if (text.length <= 72) return text;

  const stop = text.indexOf(". ");
  if (stop > 0 && stop <= 72) return text.slice(0, stop + 1);
  return `${text.slice(0, 69).trimEnd()}…`;
}

function ungrantedReason(tool) {
  if (tool.reason === "insufficient_reach") {
    // The needs are the actionable part: an operator grants reach, and naming
    // which kind is the difference between a complaint and a request.
    return `Not available: this account has no ${tool.needs.join(", ")} reach.`;
  }
  if (tool.reason === "unknown_tool") {
    return "Not installed on this appliance.";
  }
  return "Not granted to this account. An administrator grants tools in a role, on the Roles page.";
}

// Toggling one row writes the whole list, like `saveAttachments`: the panel
// holds what it just rendered, so there is no add/remove ordering to get out of
// step with the stored value.
function toggleTool(name, on) {
  const next = activeTools();
  if (on) next.add(name);
  else next.delete(name);

  // Back to the default when everything is on, rather than storing a list that
  // names every tool. The two look the same today and diverge the moment the
  // appliance installs another tool — one keeps meaning "all of them".
  const granted = grantedTools();
  const all = next.size === granted.length;
  saveToolSelection(all ? null : granted.filter((tool) => next.has(tool)));
}

// The server states the result, so the panel shows what was actually stored
// rather than what was asked for.
async function saveToolSelection(next) {
  if (drafting()) {
    draftToolSelection = next;
    paintTools();
    return;
  }

  // Captured for the same reason as the other two PATCHes above.
  const view = currentView;
  const id = activeBranchId;
  try {
    const saved = await api("PATCH", `/api/branches/${id}`, { tool_selection: next });
    if (view) view.toolSelection = saved.tool_selection ?? null;
    if (view === currentView) toolSelection = saved.tool_selection;
  } catch (error) {
    if (view === currentView) note(error.message);
  }
  // Repainted either way: on failure the stored value is unchanged, and the
  // checkbox the user just clicked has to go back to matching it.
  if (view === currentView) paintTools();
}

// -- panel widths -------------------------------------------------------------

// -- the rail's width and fold ------------------------------------------------

// The rail remembers its width, because a width you have to set on every visit is
// not a setting. Clamped on read as well as on write, because `localStorage` is
// editable by anyone with the console open and a stored 4000 would be a panel
// with no visible edge to drag back.
//
// The key is versioned because the values under the old one were not widths: the
// drag that wrote them stored `clientX`, so a jittery click ratcheted the rail
// down to the 180px floor and left it there in that browser alone (N136). A
// client that read them back would keep showing the bug on the machine the fix
// had reached, so the value is abandoned rather than migrated — there is nothing
// in it that was ever a width.
const RAIL = { min: 180, max: 560, storage: "neuman.sessions.width.v2" };

function clampRailWidth(px) {
  return Math.min(RAIL.max, Math.max(RAIL.min, px));
}

// A width, as the stylesheet wants it: the number, and the window as a ceiling.
// The default in CSS is `min(16rem, calc(100vw - var(--rail-strip)))` — a reading
// measure, with the window allowed to overrule it only when there is no room
// (N136). A stored width is a person's own, and it is applied through the same
// `min()`: clamping the *number* was never enough, because a stored 400 in a
// 300px window is a rail with no visible edge to drag back, which is the case
// the clamp reads as if it prevented.
function railWidthValue(px) {
  return `min(${Math.round(px)}px, calc(100vw - var(--rail-strip)))`;
}

const savedRail = Number(localStorage.getItem(RAIL.storage));
if (savedRail) {
  document.documentElement.style.setProperty(
    "--sessions-width", railWidthValue(clampRailWidth(savedRail)));
}

// The width on screen, folded or not. The grip sits outside the rail, so it can be
// dragged while the rail is `display: none` — and then `getBoundingClientRect` is
// 0 while the computed width still resolves the `min()`, which is the width a
// drag has to start from.
function visibleRailWidth() {
  const rail = $("sessions");
  return rail.getBoundingClientRect().width
    || parseFloat(getComputedStyle(rail).width)
    || RAIL.min;
}

// Fold the rail from one place, so the class the stylesheet reads and the grip's
// `aria-expanded` cannot drift.
function setRailCollapsed(collapsed) {
  document.body.classList.toggle("rail-collapsed", collapsed);
  if (collapsed) closeRowMenu();
  paintRailGrip();
}

function paintRailGrip() {
  const grip = $("sessions-grip");
  if (!grip) return;
  const collapsed = document.body.classList.contains("rail-collapsed");
  grip.setAttribute("aria-expanded", String(!collapsed));
  grip.title = collapsed ? "Show the session list" : "Hide the session list";
  grip.setAttribute("aria-label", collapsed ? "Show the session list" : "Hide the session list");
  // The only way a turn in flight can be visible while the list is folded.
  paintGripBadge();
}

// The grip drags to set the width and folds the rail on a click that does not
// move. One control for both, because the reopen control has to sit outside the
// pane it folds — a grip inside it would fold away with it and leave nothing to
// click. The whole right edge drags too, for the hand that reaches for an edge
// rather than a circle; it has no fold to mean, so its press only resizes.
const railGrip = $("sessions-grip");
const railEdge = $("sessions-resize");
let railDrag = null;

function startRailDrag(event, foldOnClick) {
  event.preventDefault();
  railDrag = {
    startX: event.clientX,
    startWidth: visibleRailWidth(),
    moved: false,
    foldOnClick,
  };
  if (foldOnClick) railGrip.classList.add("dragging");
  document.body.classList.add("resizing");
}

railGrip.addEventListener("pointerdown", (event) => startRailDrag(event, true));
railEdge.addEventListener("pointerdown", (event) => startRailDrag(event, false));
window.addEventListener("pointermove", (event) => {
  if (!railDrag) return;
  const { startX, startWidth } = railDrag;
  if (Math.abs(event.clientX - startX) > 3) railDrag.moved = true;
  if (!railDrag.moved) return;
  // A *delta* from the width the drag began at, not the pointer's own x. The
  // press lands on the grip, which straddles the rail's edge — so `clientX` at
  // pointerdown is already the width, and a click that jittered a few pixels
  // stored a rail narrower than the one it started from and kept it, in that
  // browser only (N136). Anchored to the left edge, which is where the rail is.
  document.documentElement.style.setProperty(
    "--sessions-width",
    railWidthValue(clampRailWidth(startWidth + (event.clientX - startX))));
});
// On `window`, not the handle: a fast drag outpaces the element and the pointer
// ends up over the transcript, where a listener bound to the handle would never
// see the move and the rail would stick mid-resize.
window.addEventListener("pointerup", () => {
  if (!railDrag) return;
  const { moved, startWidth, foldOnClick } = railDrag;
  const width = Math.round(visibleRailWidth());
  railDrag = null;
  railGrip.classList.remove("dragging");
  document.body.classList.remove("resizing");
  if (!moved) {
    // A press on the edge that did not move is not a fold: the edge has no fold
    // to mean, and only the grip's own press toggles.
    if (foldOnClick) {
      setRailCollapsed(!document.body.classList.contains("rail-collapsed"));
    }
    return;
  }
  // Stored from the width on screen rather than by re-parsing the variable: the
  // variable holds a `min()` now, and `parseInt` of that is `NaN`. And a drag
  // that ends within the threshold of where it began stores nothing at all — the
  // preference is an edge that moved, and a press that moved nothing is a press
  // that meant to fold.
  if (Math.abs(width - Math.round(startWidth)) > 3) {
    localStorage.setItem(RAIL.storage, width);
  }
});
// A keyboard activation (Enter or Space on the focused button) arrives as a
// `click` with no pointer sequence, so the mouse path never toggled. `detail` is
// the discriminator: a real pointer click carries a count, a keyboard one is 0.
railGrip.addEventListener("click", (event) => {
  if (event.detail === 0) {
    setRailCollapsed(!document.body.classList.contains("rail-collapsed"));
  }
});
paintRailGrip();

// -- past sessions -----------------------------------------------------------

// Pin a session above the date headings, or let it fall back into them.
//
// A field on `PATCH` rather than a route of its own, unlike removal: a pin has
// two values and the client states the one it wants, while removal has no
// second value to state. The fact lives on the server for the same reason
// `removed_at` does — a pin that holds on the laptop and not the phone is not a
// pin — and it has to reach the server anyway, because the ordering is applied
// there before the row cap (D61).
async function setPinned(session) {
  const pinned = !session.pinned_at;
  try {
    await api("PATCH", `/api/sessions/${session.id}`, { pinned });
  } catch (error) {
    note(`Could not ${pinned ? "pin" : "unpin"} that session: ${error.message}`);
    return;
  }
  // Repainted rather than moved in the DOM: the pinned section, the date groups
  // and the row's own date all change together, and the server is the side that
  // decides where the row now belongs.
  loadSessions();
}

// Reading is opening, on every device.
//
// The watermark is the account's, not this window's: a reply read on the phone
// stops being `finished` on the laptop on the next poll, because the flag was
// never in the browser to begin with (D63). One write when a session comes on
// screen, and one when a reply lands in the one already there — both are the
// same act, a person having it in front of them.
async function markRead(sessionId) {
  try {
    await api("POST", `/api/sessions/${sessionId}/read`, {});
  } catch {
    // A refused mark leaves the row unread, which is the safe direction: the
    // next open tries again, and nothing is hidden by a write that did not land.
  }
}

// Removal is the server's fact, not this browser's.
//
// This was a list of ids in `localStorage`, which made "removed" true of one
// browser: removed on a laptop, still there on the phone. Worse, the filtering
// happened *after* `GET /api/sessions` had applied its 50-row limit, so hiding
// ten sessions shortened the list by ten instead of revealing the ten behind
// the cap. `POST /api/sessions/{id}/remove` sets `removed_at`, the listing
// filters before its limit, and both problems are gone.
//
// One-way, deliberately: there is no un-remove. The "Show N removed" footer
// this replaces could only restore *everything* at once — it cleared the whole
// stored list rather than toggling a row — so it was an undo that did more than
// anyone asked.
//
// And no confirmation either. It was tried and it is the wrong trade: removing
// a session is a tidying gesture made often, the dialog interrupts every one of
// them, and what it protects against is losing a row from a list — not losing
// the conversation, which stays on the appliance either way. A prompt on every
// cheap action is how people learn to dismiss prompts.
//
// Still not a deletion: the session, its transcript and its audit chain stay on
// the appliance for an operator to purge or keep. And still not a *close* —
// `DELETE /api/sessions/{id}` sets `closed_at`, which is what makes a row
// eligible for scheduled retention, and tidying a list must not arm a deletion
// timer on a transcript the audit chain refers to.
async function removeSession(session) {
  try {
    await api("POST", `/api/sessions/${session.id}/remove`, {});
  } catch (error) {
    note(`Could not remove that session: ${error.message}`);
    return;
  }

  const branches = [...views.values()].filter((view) => view.sessionId === session.id);
  const wasCurrent = branches.some((view) => view === currentView);
  // Removing the session you are *in* has somewhere to land now: the draft.
  // This is why the hero exists — the old code could not hide the current row
  // at all, so it carried an exemption keeping the active session visible on a
  // list it had supposedly been removed from.
  if (wasCurrent) showDraft();
  // Removal is the one deliberate end of a running turn: the reader asked for
  // the session to stop being theirs, and a socket left open would keep a turn
  // running for a conversation no longer in the list.
  for (const view of branches) dropView(view, { force: true });
  sessions.delete(session.id);
  loadSessions();
}

// Which repaint is the current one. Two can overlap — the end of a turn
// repaints to pick up a derived title while a removal is still in flight — and
// without this the slower response could land last and draw a list the server
// has already moved past.
let sessionsPaint = 0;
// The last listing the server returned — already filtered, if a filter was in
// force. The filter is the server's (`?q=`, D52), so nothing here narrows these
// rows; `renderSessions` groups and draws exactly what arrived.
let sessionsRows = [];
// Kept so a repaint for another reason — a turn finishing and deriving a title —
// re-sends the query rather than silently widening the list back out.
let sessionsFilterText = "";

// Which date the list is ordered by: `used` is the last thing said in a session,
// `created` is when it began. The default is `used`, because the session you want
// is almost always the one you were last working in.
//
// Per browser rather than per account, like the rail's width: it says how this
// list is being read, not a fact about the person reading it. What the server is
// told is the order itself, because the listing is capped at fifty — sorting
// those fifty here would be a different fifty from the fifty most recently used,
// and would look right until an account outgrew the cap (D61).
const SESSION_SORTS = ["used", "created"];
const SORT_STORAGE = "neuman.sessions.sort";
const savedSort = localStorage.getItem(SORT_STORAGE);
let sessionsSort = SESSION_SORTS.includes(savedSort) ? savedSort : "used";

// The date a row is ordered, grouped and drawn by — one function, because the
// order is the server's and the heading above a row is the same fact: a row
// under "Yesterday" whose own date read "2h ago" would be two answers to one
// question.
function sessionDate(session) {
  return sessionsSort === "created" ? session.created_at : session.last_activity;
}

// What that date means, for the row's tooltip — the date moves when the sort
// changes, so the word has to move with it.
function sessionDateName() {
  return sessionsSort === "created" ? "Created" : "Last activity";
}

// Paint the chosen sort into the menu, and name it on the control. One writer,
// called when the menu opens, after a choice, and once at startup, so the dot,
// the words and the order on screen cannot disagree. The button's own name says
// which order is in force because the glyph cannot, and the menu is closed most
// of the time; the words are read off the menu item rather than repeated, so a
// rename happens in one place.
function paintSortMenu() {
  let chosen = null;
  for (const button of $("sessions-sort-menu").querySelectorAll("button[data-sort]")) {
    const mine = button.dataset.sort === sessionsSort;
    button.setAttribute("aria-checked", String(mine));
    if (mine) chosen = button.textContent.trim();
  }
  const label = `Sort sessions: ${chosen}`;
  $("sessions-sort").title = label;
  $("sessions-sort").setAttribute("aria-label", label);
}

// Choose an order. The list is fetched again rather than re-sorted in place:
// `?sort=` has to reach the server, which is where the row cap is applied.
function chooseSort(sort) {
  if (sort === sessionsSort || !SESSION_SORTS.includes(sort)) return;
  sessionsSort = sort;
  localStorage.setItem(SORT_STORAGE, sort);
  paintSortMenu();
  loadSessions();
}

// Which calendar day a session belongs to, for the group headings. Today and
// yesterday get words, because that is how a reader thinks about them; older
// days get a date, and a date from another year carries the year.
function sessionGroup(iso) {
  const day = new Date(iso);
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (day >= midnight) return "Today";
  if (day >= new Date(midnight.getTime() - 86400000)) return "Yesterday";
  return day.getFullYear() === now.getFullYear()
    ? day.toLocaleDateString(undefined, { month: "long", day: "numeric" })
    : day.toLocaleDateString(
      undefined, { year: "numeric", month: "long", day: "numeric" });
}

// One session row: title, meta, and a fixed actions column at the end.
// The matched fragment, with the search terms marked.
//
// `ts_headline` wraps hits in `<<`/`>>` rather than `<b>`, and this splits on
// those and builds elements — so the server never hands the client markup and
// the client never parses any. Conversation text is the most untrusted string
// in the appliance: it is whatever a person typed and whatever a model wrote
// back, and a snippet built with `innerHTML` would make a session title an
// injection site (D1).
function snippetLine(snippet) {
  const line = el("div", "snippet");
  // Split keeps the delimiters' contents as every other element, so the parity
  // of the index is what says whether a piece matched.
  for (const [index, piece] of snippet.split(/<<|>>/).entries()) {
    if (!piece) continue;
    line.appendChild(index % 2 ? el("mark", null, piece) : document.createTextNode(piece));
  }
  return line;
}

function sessionRow(session) {
  const row = el("div", "row" +
    (session.id === currentView?.sessionId ? " current" : ""));

  // A session gets its title from the conversation, so a fresh one has none.
  // Saying "untitled" is better than an empty row that looks like a bug.
  const title = el("div", "title" + (session.title ? "" : " untitled"),
    session.title || "untitled");
  row.appendChild(title);

  // Why this row is in a filtered list. Present only when the match was in the
  // conversation rather than in the title, so an unfiltered list is unchanged
  // and a title hit does not get a line restating the title.
  if (session.snippet) row.appendChild(snippetLine(session.snippet));

  const meta = el("div", "meta");
  if (session.closed_at) meta.appendChild(el("span", "closed", "closed"));
  if (session.branch_count > 1) {
    const badge = el("span", "branch-count", `⑂${session.branch_count}`);
    badge.title = `${session.branch_count} branches`;
    meta.appendChild(badge);
  }
  // The date the list is ordered by, not always the date it was created: the
  // sort moves it, and the tooltip says which one is on screen and spells the
  // relative form out. A row under a "Today" heading reads `4h ago`; the
  // absolute time is the thing a sidebar cannot show.
  const stamp = el("span", "when", when(sessionDate(session)));
  stamp.title = `${sessionDateName()}: ${new Date(sessionDate(session)).toLocaleString()}`;
  meta.appendChild(stamp);
  row.appendChild(meta);

  // A separate target, because double-click cannot work here: the *first* click
  // fires the row handler and loads the session, so you would be editing a title
  // while the transcript replaced itself underneath. `stopPropagation` on
  // `dblclick` is too late — the `click` has already bubbled.
  //
  // Hidden until the row is hovered, so a list of ten sessions is ten titles
  // rather than ten titles and ten buttons. Its own grid column puts it at the
  // row's end rather than wherever the meta beside it happens to stop.
  //
  // A menu rather than the rename pencil it replaces: two actions on a row
  // cannot both be one icon, and a second bare glyph beside the first would be
  // two unlabelled targets a pixel apart. The menu names them instead.
  const more = el("button", "more", "⋮");
  more.title = "Session actions";
  more.setAttribute("aria-label", "Session actions");
  more.setAttribute("aria-haspopup", "menu");
  more.onclick = (event) => {
    event.stopPropagation();
    openRowMenu(row, more, title, session);
  };
  row.appendChild(more);

  row.onclick = () => resumeSession(session.id);
  return row;
}

// -- the Active section -------------------------------------------------------
//
// A session is in Active while this window has something to show for it: it is
// on screen, it is running a turn (in this window or carrying one over from it),
// it finished a turn while the reader was elsewhere, or it was last used inside
// the window Settings › Sessions sets. That is the working set the rail exists
// to manage once more than one session can be live at a time.
//
// The last of those is why a view is parked rather than dropped when the reader
// looks away: a session you were just in is the one you are most likely to go
// back to, and reopening it used to cost a request and a rebuild. The window is
// how long that lasts, and the three live states are exempt from it — a turn
// running and a reply nobody has read are asking for attention, and time is not
// an answer to either.
//
// It sits above Pinned because it is not an arrangement of history — it is what
// the reader is working in, and a pinned session is still a session you have to
// go and open. The order is the last time the reader touched it, so the session
// they just opened is at the top and the row they left does not jump around as
// tokens arrive.

const activeRowEls = new Map();
// The summary at the right end of the Active heading, kept so a phase change can
// correct it without redrawing the rows around it.
let activeGroupNote = null;

// The Active section is **the account's working set**, and it is the same list in
// every window: what the core reports as being done, looked at or just opened
// (D63). This window's part in it is one word — `open` for the session it has on
// screen — and its own frames where it holds a view, which are finer than a
// four-second poll. Everything else, including the order, is the core's.
//
// It lists **sessions**, not branches. A session with a live branch and another
// branch on screen is one row, because a fork is a way of reading one
// conversation rather than a second conversation. The row's state is the most
// urgent of its branches; when that branch is not the one on screen the detail
// names it, so a background approval is never invisible.
function activeGroups() {
  const bySession = new Map();
  // The core's rows, in the core's order: newest activity first, where activity
  // is anything said or done in the conversation. Deliberately not the order of
  // opening — clicking a row must not lift it over the one being worked in.
  for (const row of activeRows) {
    bySession.set(row.id, {
      sessionId: row.id,
      session: sessions.get(row.id) || { id: row.id, title: row.title },
      views: [],
      branchId: row.branch_id || null,
      state: row.state,
      tool: row.tool,
      lastActivity: Date.parse(row.last_activity) || 0,
    });
  }
  // This window's views, folded into their session's row — or added, when the
  // poll has not seen it yet: a session created or opened seconds ago is active
  // here before the next tick says so.
  for (const view of views.values()) {
    let group = bySession.get(view.sessionId);
    if (!group) {
      if (!doesSomething(view)) continue;
      group = {
        sessionId: view.sessionId,
        session: view.session || { id: view.sessionId },
        views: [],
        branchId: view.id,
        state: null,
        tool: null,
        lastActivity:
          Date.parse((sessions.get(view.sessionId) || {}).last_activity) || view.touchedAt,
      };
      bySession.set(view.sessionId, group);
    }
    group.views.push(view);
    group.session = view.session || group.session;
  }
  const groups = [...bySession.values()];
  for (const group of groups) {
    group.views.sort((a, b) => b.touchedAt - a.touchedAt);
    group.stateView = stateViewOf(group);
    group.session = { ...group.session, ...(sessions.get(group.sessionId) || {}) };
    // What the state line says when the branch that needs attention is not the
    // one the reader is looking at.
    group.branchNote =
      group.views.length > 1 && group.stateView !== currentView
        ? branchLabel(group.stateView)
        : "";
  }
  return groups.sort((a, b) => b.lastActivity - a.lastActivity);
}

// A view that is worth a row on its own, before the poll agrees: the one on
// screen, one with a turn in it, or one holding a reply this window has not
// shown. The rest of the set is the core's, and a kept-but-idle view outside
// its window is not active — it is just still in memory.
function doesSomething(view) {
  return view === currentView || view.running || view.unread;
}

// Which state a row shows, in order of what a reader has to act on. A view this
// window holds wins where it is doing something, because its phase comes from
// the frames; below that the core's answer is the answer, which is what makes
// two windows agree. `open` outranks `finished` because a session on screen has
// just been read, whether or not the watermark has landed yet.
function stateViewOf(group) {
  const local = (predicate) => group.views.find(predicate);
  return (
    local((view) => view.state === "approval") ||
    local((view) => view.running) ||
    remoteStateView(group, "approval") ||
    remoteStateView(group, "running") ||
    local((view) => view.unread) ||
    local((view) => view === currentView) ||
    remoteStateView(group, "finished") ||
    remoteStateView(group, "idle") ||
    group.views[0]
  );
}

// The core's state for a group, shaped like a view so every renderer reads the
// same fields. Not in `views`: there is no transcript, no socket and nothing to
// drop on a sweep, which is the whole difference between a window that holds a
// session and one that can see it is active.
function remoteStateView(group, wanted) {
  if (group.state !== wanted) return null;
  return {
    id: group.branchId,
    sessionId: group.sessionId,
    running: wanted === "running" || wanted === "approval",
    unread: wanted === "finished",
    // The local vocabulary the renderer and the stylesheet already speak.
    state: { running: "tool", approval: "approval", finished: "done", idle: "open" }[wanted],
    detail:
      wanted === "approval"
        ? `${group.tool ? `approve ${group.tool}` : "approve a tool call"} · in another window`
        : wanted === "running"
          ? "in another window"
          : "",
    lastTool: group.tool,
    touchedAt: 0,
    branch: null,
    firstMessage: "",
  };
}

// A branch's name in the switcher: `main` for the root, the first thing said in
// it for a fork, and a last resort for a fork that has not spoken yet.
function branchLabel(view) {
  if (!view || !view.branch || !view.branch.forked_from) return "main";
  const text = (view.firstMessage || "").trim().replace(/\s+/g, " ");
  return text ? text.slice(0, 28) : "branch";
}

// The word is the fact; the dot is its colour. Both are drawn, because a hue is
// not readable to everyone and the rail is glanced at rather than read.
//
// No elapsed time. What a reader acts on is the word — `needs you` or `running`
// — and how long it has been going is answered by opening the session.
function viewStateInfo(view) {
  if (view.state === "approval") {
    return { word: "needs you", detail: view.detail || "approve a tool call" };
  }
  if (view.state === "tool") {
    return { word: "running", detail: view.detail };
  }
  if (view.state === "thinking") {
    return { word: "thinking", detail: view.detail };
  }
  if (view.state === "done" && view.unread) {
    return { word: "finished", detail: "reply ready" };
  }
  return {
    // The row is in the account's working set; `open` is this window's one claim
    // on it. Everything else is `idle` — active, and not on screen here (D63).
    word: view === currentView ? "open" : "idle",
    detail: "",
  };
}

function activeRowClass(group) {
  return [
    "row",
    "active",
    group.sessionId === currentView?.sessionId ? "current" : "",
    `state-${group.stateView.state}`,
  ].filter(Boolean).join(" ");
}

function activeRow(group) {
  const session = group.session || { id: group.sessionId };
  const row = el("div", activeRowClass(group));
  row.dataset.sessionId = group.sessionId;
  row.title = session.title || "untitled";

  const title = el("div", "title" + (session.title ? "" : " untitled"),
    session.title || "untitled");
  row.appendChild(title);

  const total = (sessions.get(group.sessionId) || group.session || {}).branch_count
    || group.views.length;
  if (total > 1) {
    const badge = el("span", "branch-count", `⑂${total}`);
    badge.title = `${total} branches`;
    row.appendChild(badge);
  }

  const more = el("button", "more", "⋮");
  more.title = "Session actions";
  more.setAttribute("aria-label", "Session actions");
  more.setAttribute("aria-haspopup", "menu");
  more.onclick = (event) => {
    event.stopPropagation();
    openRowMenu(row, more, title, session);
  };
  row.appendChild(more);

  const line = el("div", "state-line");
  line.appendChild(el("span", "live-dot"));
  line.appendChild(el("span", "state-word"));
  line.appendChild(el("span", "state-detail"));
  row.appendChild(line);

  row.onclick = () => {
    if (group.sessionId === currentView?.sessionId) {
      $("input").focus({ preventScroll: true });
      return;
    }
    openSession(group.sessionId, group.stateView.id);
  };
  activeRowEls.set(group.sessionId, row);
  paintActiveRow(group.sessionId);
  return row;
}

function paintActiveRow(sessionId) {
  const row = activeRowEls.get(sessionId);
  if (!row) return;
  const group = activeGroups().find((item) => item.sessionId === sessionId);
  if (!group) return;
  // A row whose menu is open keeps the class that holds its button visible.
  const menuOpen = row.classList.contains("menu-open");
  row.className = activeRowClass(group);
  if (menuOpen) row.classList.add("menu-open");

  const info = viewStateInfo(group.stateView);
  const word = row.querySelector(".state-word");
  const detail = row.querySelector(".state-detail");
  if (!word || !detail) return;

  word.textContent = info.word;
  const suffix = group.branchNote ? ` · ${group.branchNote}` : "";
  detail.textContent = (info.detail || "") + suffix;
  detail.hidden = !detail.textContent;
  row.setAttribute(
    "aria-label",
    `${group.session.title || "untitled"}. ${info.word}` +
      (detail.textContent ? `, ${detail.textContent}` : ""),
  );
}

function groupHead(label, opts = {}) {
  const head = el("div", "group " +
    (label === "Active" ? "active" : label === "Pinned" ? "pinned" : ""));
  head.appendChild(el("span", null, label));
  if (opts.note) {
    head.appendChild(el("span", "group-note" + (opts.warn ? " warn" : ""), opts.note));
  }
  return head;
}

// The one sentence the Active heading carries, in the order of what a reader
// has to do about it: a blocked turn first, then work in flight, then a reply
// waiting to be read, then the quiet case.
function activeSummary(active) {
  const needs = active.filter((group) => group.stateView.state === "approval").length;
  // Read off the state view rather than the group's views: a session running in
  // another window has no view here, and the count is about the account's work,
  // not this document's.
  const running = active.filter((group) => group.stateView.running).length;
  const unread = active.filter((group) => group.stateView.unread).length;
  if (needs) {
    return { note: `${needs} ${needs === 1 ? "needs" : "need"} you`, warn: true };
  }
  if (running) return { note: `${running} running`, warn: false };
  if (unread) return { note: `${unread} finished`, warn: false };
  return { note: `${active.length} active`, warn: false };
}

function paintActiveGroupNote() {
  if (!activeGroupNote) return;
  const active = activeGroups();
  if (!active.length) return;
  const summary = activeSummary(active);
  activeGroupNote.textContent = summary.note;
  activeGroupNote.classList.toggle("warn", summary.warn);
}

// The count on the grip, for the reader who has folded the rail. It is the one
// way a background turn can ask for attention while the list is out of sight;
// an approval wins the badge because that is the state that is actually blocked.
function paintGripBadge() {
  const badge = $("sessions-grip-badge");
  if (!badge) return;
  const collapsed = document.body.classList.contains("rail-collapsed");
  if (!collapsed) {
    badge.classList.add("hidden");
    return;
  }
  const groups = activeGroups();
  const running = groups.filter((group) => group.stateView.running);
  const unread = groups.filter((group) => group.stateView.unread);
  const needs = groups.filter((group) =>
    group.stateView.state === "approval").length;
  const total = running.length + unread.length;
  const show = total > 0;
  badge.classList.toggle("hidden", !show);
  if (!show) {
    // The visual badge is gone; the grip's name has to stop claiming a count.
    const grip = $("sessions-grip");
    if (grip) grip.setAttribute("aria-label", "Show the session list");
    return;
  }
  badge.textContent = needs ? `${needs}!` : String(total);
  // Amber for a blocked turn, accent while anything is still working, green for
  // a reply that landed while the rail was folded. The same three states the
  // rows carry, so the badge cannot say something the list would contradict.
  badge.style.background = needs
    ? "var(--warn)"
    : running.length
      ? "var(--accent)"
      : "var(--ok)";
  badge.style.color = "var(--ink-on-accent)";

  // The badge is a visual count, so it is hidden from assistive technology; the
  // grip itself carries the same fact in its name, including the state the
  // colour is carrying for everyone else.
  const grip = $("sessions-grip");
  if (grip) {
    const state = needs
      ? `${needs} waiting for you`
      : running.length
        ? `${total} active`
        : `${unread.length} finished`;
    grip.setAttribute("aria-label", `Show the session list, ${state}`);
  }
}

// -- the account's working set -----------------------------------------------
//
// Which sessions are active is a fact about the account, not about this window
// (D63): the core knows what is running (`Services.turns`), what was opened
// recently (`read_at`), what was last said in it and whether the reply has been
// seen. So the rail asks it, and two windows — and two devices — draw the same
// list. This window adds one word to it, `open`.
//
// A poll rather than the held request the GPU bar uses: the client's `/api`
// proxy forwards each call on a worker thread (`ui.py`), so a request held open
// would pin one thread per window for as long as the window exists — and more
// windows is exactly what this feature is for.
//
// `activeSignature` is the last answer folded into a string, so a poll that
// changes nothing redraws nothing: a rail rebuilt every four seconds closes an
// open row menu and moves the reader's scroll for no reason. `null` is "never
// asked", which is not the same answer as "nothing is active".
const ACTIVE_POLL_MS = 4000;

async function loadActive() {
  let rows;
  try {
    rows = (await api("GET", "/api/active")).sessions || [];
  } catch {
    // An unreachable core, or a session that ended (which `api` has already put
    // on screen). An unconfirmed row is not drawn; the history this window
    // already knows stays where it is.
    if (activeRows.length) {
      activeRows = [];
      activeSignature = null;
      renderSessions();
    }
    return;
  }

  const before = new Map(activeRows.map((row) => [row.id, row]));
  activeRows = rows;

  // A call that starts waiting, or stops, while this window has its session
  // open. The card arrives by itself, or `needs you` is a row that cannot be
  // answered from the window looking at it; and a verdict given elsewhere takes
  // the buttons away here, so this window never offers a choice already made.
  for (const row of rows) {
    const held = views.get(row.branch_id);
    if (!held) continue;
    if (row.state === "approval" && before.get(row.id)?.state !== "approval") {
      loadPending(held);
    }
  }
  for (const was of before.values()) {
    if (was.state !== "approval") continue;
    if (rows.find((row) => row.id === was.id)?.state === "approval") continue;
    const held = views.get(was.branch_id);
    if (held) settlePending(held);
  }

  const signature = rows.map((row) => `${row.id}:${row.state}:${row.tool || ""}`).join("|");
  if (signature === activeSignature) return;
  const first = activeSignature === null;
  activeSignature = signature;
  // A session joining or leaving the set moves the history too — a derived
  // title, a new `last_activity`, a row that belongs under another date — so
  // the listing is read again rather than only repainted. The first answer is
  // not a change: sign-in has just fetched the same list.
  if (first) renderSessions();
  else loadSessions();
}

// Whether a branch is waiting on a verdict, per the last poll. Used when a
// session is opened mid-approval: the card should come with it rather than wait
// for the next tick.
function awaitingVerdict(branchId) {
  return activeRows.some((row) => row.branch_id === branchId && row.state === "approval");
}

function scheduleActive() {
  if (activeTimer) clearTimeout(activeTimer);
  activeTimer = null;
  // Nothing to ask about while the page is behind another, and a hidden window
  // is the one case where this poll is pure cost.
  if (!csrf || document.hidden) return;
  activeTimer = setTimeout(async () => {
    activeTimer = null;
    await loadActive();
    scheduleActive();
  }, ACTIVE_POLL_MS);
}

function startActive() {
  activeSignature = null;
  loadActive();
  scheduleActive();
}

function stopActive() {
  if (activeTimer) clearTimeout(activeTimer);
  activeTimer = null;
  activeRows = [];
  activeSignature = null;
}

// A window that was behind another missed whatever happened while it was hidden,
// and that is not only the working set: a pin, a removal or a derived title made
// elsewhere landed too. One read of each on the way back.
document.addEventListener("visibilitychange", () => {
  if (!csrf) return;
  if (document.hidden) {
    if (activeTimer) clearTimeout(activeTimer);
    activeTimer = null;
    return;
  }
  loadSessions();
  loadActive();
  scheduleActive();
  // A job that ended while the tab was hidden changed nothing on screen; this
  // is the read that catches the row up.
  refreshJobs();
});

// Draw the listing: the working set, then pinned rows, then the dates.
//
// The filter and the order are both over the rows the server returned — it
// searched them (`?q=`) and sorted them (`?sort=`) before applying its cap.
// Nothing here filters or re-sorts the history; the Active rows are a separate
// fact (what this window has open) and are shown even when a filter is narrowing
// the past sessions below them.
function renderSessions() {
  const panel = $("sessions-list");
  const scroll = panel.scrollTop;
  panel.innerHTML = "";
  activeRowEls.clear();
  activeGroupNote = null;
  closeRowMenu();

  const active = activeGroups();
  if (active.length) {
    const head = groupHead("Active", activeSummary(active));
    activeGroupNote = head.querySelector(".group-note");
    panel.appendChild(head);
    for (const group of active) panel.appendChild(activeRow(group));
  }

  const rows = sessionsRows;
  const activeIds = new Set(active.map((group) => group.sessionId));
  if (!rows.length) {
    // The working set above is not what the filter searched, so this sentence
    // has to be about the history rather than about the whole rail.
    panel.appendChild(el("div", "empty",
      sessionsFilterText.trim()
        ? "No past sessions match that filter."
        : "No past sessions."));
    paintGripBadge();
    return;
  }

  // Pinned rows are their own section rather than interleaved with the dates.
  // A date heading cannot say "these are first because you pinned them", and a
  // pinned row drawn inside the date groups would either split a day in two or
  // sit under a heading that contradicts why it is at the top.
  const pinned = rows.filter(
    (session) => session.pinned_at && !activeIds.has(session.id));
  if (pinned.length) {
    panel.appendChild(groupHead("Pinned"));
    for (const session of pinned) panel.appendChild(sessionRow(session));
  }

  // Grouped in place, not sorted into buckets: the rows arrive in the order the
  // server chose, so a heading is emitted whenever the day changes and the
  // order is preserved.
  let group = null;
  for (const session of rows) {
    if (session.pinned_at || activeIds.has(session.id)) continue;
    const label = sessionGroup(sessionDate(session));
    if (label !== group) {
      group = label;
      panel.appendChild(groupHead(label));
    }
    panel.appendChild(sessionRow(session));
  }
  // A repaint for a state change must not throw the reader's place away; this
  // list is long enough to scroll and it is still the same list.
  panel.scrollTop = scroll;
  paintGripBadge();
}

// The working set's clock. Nothing on a row changes second to second any more —
// `viewStateInfo` has no elapsed time — so this tick exists for expiry alone,
// and it is the only thing that brings a view back out of the working set. 15
// seconds is well inside a setting whose unit is a minute.
//
// A dropped view is still a session: it moves from Active back to its date group,
// which is why the rail is redrawn rather than only the expired rows removed.
setInterval(() => {
  let dropped = false;
  for (const view of [...views.values()]) {
    if (keepViewWarm(view)) continue;
    discardView(view);
    dropped = true;
  }
  if (dropped) renderSessions();
  // The same tick ages the running-job chips. It redraws a figure from
  // `Date.now()`; nothing is fetched, so this stays a clock rather than the
  // polling loop D14 rules out.
  if (jobs.length) paintJobTimes();
}, 15000);

// Rendered from `GET /api/sessions`, which has existed since M7 with nothing
// calling it. The order is the server's, because that is where the cap is: ask
// for the fifty most recently used, not the fifty newest reordered.
async function loadSessions() {
  const paint = ++sessionsPaint;
  // Any open row menu belongs to a row that is about to be replaced, and it is
  // positioned from that row's old rect — so it would float over the new list
  // pointing at nothing, still holding the id of a row it no longer anchors to.
  closeRowMenu();

  let rows;
  try {
    // The filter goes to the server (`?q=`), which searches what was *said* in
    // each session — the user's messages and the model's replies — through the
    // full-text index. It was a substring match over titles here, which could
    // only ever find a session whose derived title happened to contain the
    // word: the conversation itself was unsearchable, and a title is the first
    // few words of the first message.
    //
    // Not filtered in the browser, because that needs every session's text in
    // the page: it grows without bound and puts whole conversations in memory
    // to answer a question about which one to open.
    const query = sessionsFilterText.trim();
    const order = `sort=${sessionsSort}`;
    const path = query
      ? `/api/sessions?q=${encodeURIComponent(query)}&${order}`
      : `/api/sessions?${order}`;
    rows = (await api("GET", path)).sessions || [];
  } catch (error) {
    if (paint !== sessionsPaint) return;
    // The history could not be read, but the working set is local and must not
    // disappear because a list request failed.
    sessionsRows = [];
    renderSessions();
    $("sessions-list").prepend(
      el("div", "empty", `Could not list sessions: ${error.message}`));
    return;
  }

  // A newer repaint started while this one was waiting, so this answer is
  // already stale — the list it would draw is the one the newer call is about
  // to replace.
  if (paint !== sessionsPaint) return;

  // The server returns exactly the sessions this account can still see, in the
  // order it was asked for — filtered and sorted before the cap, not after it.
  // Nothing is filtered or re-ordered here.
  //
  // The list is the server's rows; the session map keeps their branches, which
  // a listing does not carry. Fold the fresh row over what a view already knew
  // so a derived or renamed title reaches the Active row without a re-open.
  for (const session of rows) {
    const known = sessions.get(session.id);
    sessions.set(session.id, {
      ...(known || {}),
      ...session,
      branches: (known && known.branches) || [],
    });
  }
  for (const view of views.values()) {
    const row = rows.find((session) => session.id === view.sessionId);
    if (row) view.session = { ...view.session, ...row };
  }
  sessionsRows = rows;
  renderSessions();
}

// The open row menu, so a second click elsewhere closes the first.
//
// One at a time and tracked at module scope, because the menu is built per row
// rather than being a single element in the markup: there is nothing with a
// stable id for the document-level dismissal to look up.
let rowMenu = null;

function closeRowMenu() {
  if (!rowMenu) return;
  // The row keeps its button visible while its menu is open, so the class has
  // to come off with the menu — including when the menu is removed by a
  // repaint rather than by a click.
  rowMenu.anchorRow?.classList.remove("menu-open");
  rowMenu.remove();
  rowMenu = null;
}

// The row's actions — rename, pin, remove — anchored to its row.
//
// Positioned `fixed` from the button's own rect rather than appended inside the
// row: `#sessions-list` scrolls and clips, so a menu parented to a row near the
// bottom would be cut off by the panel edge rather than overlapping it.
function openRowMenu(row, anchor, title, session) {
  // A second click on the same button closes it, matching every other control
  // on this page.
  if (rowMenu && rowMenu.dataset.session === session.id) {
    closeRowMenu();
    return;
  }
  closeRowMenu();

  const menu = el("div", "row-menu");
  menu.dataset.session = session.id;
  menu.setAttribute("role", "menu");

  const item = (label, danger, onPick) => {
    const button = el("button", danger ? "danger" : null, label);
    button.setAttribute("role", "menuitem");
    button.onclick = (event) => {
      event.stopPropagation();
      closeRowMenu();
      onPick();
    };
    return button;
  };

  menu.appendChild(item("Edit name", false, () => beginRename(title, session)));

  // The second act on a row, and the one that says where it belongs. The label
  // states the effect rather than the state — `Pin to top` then `Unpin` — so it
  // is not a checkbox whose current value has to be inferred from the menu's
  // own wording. Not a danger colour: a pin costs nothing, and the item that
  // replaces it is the undo.
  const pin = item(session.pinned_at ? "Unpin" : "Pin to top", false, () => setPinned(session));
  pin.title = session.pinned_at
    ? "Back into the date order, where its own date puts it."
    : "Above the date headings, on every device.";
  menu.appendChild(pin);

  // "Remove" rather than "Delete", because it is not one. The title says what
  // actually happens and who can destroy it for real — the distinction matters
  // most to the person who just clicked it expecting the stronger thing.
  const remove = item("Remove from list", true, () => removeSession(session));
  remove.title =
    "Takes it off your list on every device, for good. The session and its audit trail stay on the appliance until an operator purges them.";
  menu.appendChild(remove);

  document.body.appendChild(menu);

  // Flipped up when there is no room below, so the last row in a long list gets
  // a usable menu rather than one off the bottom of the window.
  const at = anchor.getBoundingClientRect();
  const height = menu.offsetHeight;
  const below = window.innerHeight - at.bottom;
  menu.style.left = `${Math.min(at.left, window.innerWidth - menu.offsetWidth - 8)}px`;
  menu.style.top = below < height + 8 ? `${at.top - height}px` : `${at.bottom}px`;

  menu.anchorRow = row;
  row.classList.add("menu-open");
  rowMenu = menu;
  menu.querySelector("button").focus();
}

// Rename in place, triggered by the row's menu. Enter commits, Escape
// reverts, blur commits — because
// clicking away from a field you have just typed into means you meant it, and
// losing the edit would be the one behaviour nobody expects.
function beginRename(node, session) {
  const original = session.title || "";
  node.contentEditable = "plaintext-only";
  node.classList.remove("untitled");
  node.textContent = original;
  node.focus();

  // Select the whole thing, so typing replaces rather than appends.
  const range = document.createRange();
  range.selectNodeContents(node);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);

  let settled = false;

  const finish = async (commit) => {
    if (settled) return;
    settled = true;
    node.contentEditable = "false";

    const next = node.textContent.trim().replace(/\s+/g, " ");
    if (!commit || !next || next === original) {
      // Nothing to save, or the user changed their mind. Repaint from the list
      // rather than trusting local state to be right.
      loadSessions();
      return;
    }

    try {
      const saved = await api("PATCH", `/api/sessions/${session.id}`, { title: next });
      session.title = saved.title;
    } catch (error) {
      note(`Could not rename: ${error.message}`);
    }
    loadSessions();
  };

  node.onkeydown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    }
  };
  node.onblur = () => finish(true);
}

// Relative for anything recent, absolute once "3 days ago" stops being useful.
function when(iso) {
  const then = new Date(iso);
  const minutes = Math.round((Date.now() - then.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)}h ago`;
  if (minutes < 60 * 24 * 7) return `${Math.round(minutes / 1440)}d ago`;
  return then.toLocaleDateString();
}

// Open a session, on a branch. One session may have several; the branch with the
// newest activity is the default, and the switcher names the rest.
//
// `GET /api/sessions/{id}` returns the *projection* — what the model saw — plus
// the branch list. That is the right transcript: the governance records
// (proposals, decisions) are in the audit chain, and a reader wants the
// conversation.
async function openSession(id, branchId = null) {
  // A click on another row supersedes a GET that is still in flight; without
  // this, a slow answer could put a session on screen that was not the last one
  // chosen.
  const seq = ++viewOpen;
  const wanted = branchId || sessions.get(id)?.open_branch_id || null;
  const existing = wanted ? views.get(wanted) : null;
  if (existing) {
    showView(existing);
    loadSessions();
    return;
  }

  try {
    const query = branchId ? `?branch=${encodeURIComponent(branchId)}` : "";
    const answer = await api("GET", `/api/sessions/${id}` + query);
    if (seq !== viewOpen) return;

    const { messages: _messages, branch: _branch, ...meta } = answer;
    sessions.set(id, {
      ...meta,
      branch_count: (answer.branches || []).length,
      branches: answer.branches || [],
    });
    const view = makeView(answer, answer.branch);
    views.set(view.id, view);
    // The projection is a transcript rather than a set of frames. It is drawn
    // once into the view's holder, before the view is mounted, so opening a
    // session costs one build and switching back to it costs a move.
    withView(view, () => replay(answer.messages || []));
    view.followTail = true;

    // A session opened while its turn is blocked on a person has to show the
    // prompt, not just the history: the rail says `needs you`, and this is where
    // that becomes answerable. Asked only for a branch the poll reports blocked,
    // so opening an idle session costs no request — and the poll's transition
    // catches a prompt that starts a moment later.
    if (awaitingVerdict(view.id)) loadPending(view);

    // Anything staged in the draft was context for a session that was never
    // started, and opening a past one abandons it.
    draftWorkingDir = null;
    draftAttachments = [];
    draftToolSelection = null;
    draftModel = null;
    draftReasoning = null;
    draftApproval = null;

    showView(view);
    loadSessions();          // repaint, so the current row moves
  } catch (error) {
    if (seq !== viewOpen) return;
    note(`Could not open that session: ${error.message}`);
  }
}

// Kept as the rail's entry point: a row opens the branch the server says has the
// newest activity unless this window already knows a better one.
function resumeSession(id) {
  const session = sessions.get(id);
  return openSession(id, session && session.open_branch_id);
}

// Switch branches without leaving the session. A branch with a live view is
// shown by moving its nodes; one that is not is opened with the ordinary GET.
async function switchBranch(branchId) {
  closeForkMenu();
  $("branch-menu").classList.add("hidden");
  $("branch-chip").setAttribute("aria-expanded", "false");
  const view = views.get(branchId);
  if (view) {
    showView(view);
    return;
  }
  const sessionId = currentView && currentView.sessionId;
  if (!sessionId) return;
  await openSession(sessionId, branchId);
}

// The branch whose own turn ends at `s`, where `s` is a seq in this transcript.
// A non-root branch's copied rows are seqs 2..inherited_event_count and its own
// events begin at inherited_event_count + 1; a root starts at 1 on screen and
// at 2 inside a descendant's copy (the copy's fork marker is seq 1).
function ownerAtBoundary(chain, current, s) {
  for (let i = 0; i < chain.length; i += 1) {
    const branch = chain[i];
    const start = i === 0
      ? (branch.id === current.id ? 1 : 2)
      : (branch.inherited_event_count || 0) + 1;
    if (s < start) return i > 0 ? chain[i - 1] : null;
    const next = chain[i + 1];
    if (!next) return branch;
    if (s <= (next.inherited_event_count || 0)) return branch;
  }
  return chain[chain.length - 1] || null;
}

// Every fork visible in this transcript, grouped by the boundary it left. The
// boundary seq is a turn_complete in this branch's log; the display parent is
// the branch that owns the turn ending there. Two branches recorded under
// different parents but leaving the same boundary are therefore one node — the
// case a fork made at a branch's first message produces.
function branchForkLayout(view) {
  const session = sessions.get(view.sessionId) || view.session;
  const branches = (session && session.branches) || [];
  const byId = new Map(branches.map((branch) => [branch.id, branch]));
  if (!byId.has(view.id)) byId.set(view.id, view.branch);
  const current = byId.get(view.id);
  if (!current) return { groups: [] };

  const chain = [];
  const seen = new Set();
  let cursor = current;
  while (cursor && !seen.has(cursor.id)) {
    chain.unshift(cursor);
    seen.add(cursor.id);
    cursor = cursor.forked_from ? byId.get(cursor.forked_from) : null;
  }
  const chainIds = new Set(chain.map((branch) => branch.id));

  const grouped = new Map();
  for (const child of byId.values()) {
    if (!child.forked_from || !chainIds.has(child.forked_from)) continue;
    const parent = byId.get(child.forked_from);
    if (!parent) continue;
    let s;
    if (parent.id === current.id) {
      s = child.forked_from_seq;
    } else {
      const index = chain.indexOf(parent);
      const next = index >= 0 ? chain[index + 1] : null;
      if (!next) continue;
      s = child.inherited_event_count;
      if (s === null || s === undefined) continue;
      if (s > (next.inherited_event_count || 0)) continue;
    }
    if (s === null || s === undefined) continue;
    if (!grouped.has(s)) grouped.set(s, new Set());
    grouped.get(s).add(child.id);
  }

  const groups = [];
  for (const s of [...grouped.keys()].sort((a, b) => a - b)) {
    const parent = ownerAtBoundary(chain, current, s);
    if (!parent) continue;
    const members = [];
    for (const id of new Set([parent.id, ...grouped.get(s)])) {
      const branch = byId.get(id);
      if (branch) members.push(branch);
    }
    const path = chain
      .filter((branch) => members.some((member) => member.id === branch.id))
      .pop() || parent;
    groups.push({
      s,
      parentId: parent.id,
      memberIds: members.map((branch) => branch.id),
      pathId: path.id,
      isOrigin: members.some((branch) => branch.id === current.id)
        && s === (current.inherited_event_count || 0),
    });
  }
  return { groups };
}

function forkMarkerEl(group) {
  const marker = el("div", "fork-marker");
  marker.dataset.fork = `${group.parentId}:${group.s}`;
  const before = el("span", "fork-line");
  before.setAttribute("aria-hidden", "true");
  const button = el("button", "fork-node");
  button.type = "button";
  button.setAttribute("aria-haspopup", "menu");
  button.setAttribute("aria-expanded", "false");
  const glyph = el("span", "fork-glyph", "⑂");
  glyph.setAttribute("aria-hidden", "true");
  const count = el("span", "fork-count", String(group.memberIds.length));
  count.setAttribute("aria-hidden", "true");
  button.append(glyph, count);
  const n = group.memberIds.length;
  button.title = group.isOrigin
    ? `This branch began here · ${n} ${n === 1 ? "branch" : "branches"}`
    : `${n} ${n === 1 ? "branch" : "branches"} from here`;
  button.setAttribute(
    "aria-label",
    `${group.isOrigin ? "This branch began here. " : ""}` +
      `${n} ${n === 1 ? "branch" : "branches"} from here. Open the branch switcher.`,
  );
  if (group.isOrigin) button.classList.add("origin");
  button.onclick = (event) => {
    event.stopPropagation();
    toggleForkMenu(button, group);
  };
  const after = el("span", "fork-line");
  after.setAttribute("aria-hidden", "true");
  marker.append(before, button, after);
  return marker;
}

function toggleForkMenu(node, group) {
  if (forkMenuOpen && forkMenuOpen.node === node) {
    closeForkMenu();
    return;
  }
  openForkMenu(node, group);
}

function forkMenuRow(branch, extra, group, isParent) {
  const row = el("button", `fork-row ${extra}`);
  row.type = "button";
  row.setAttribute("role", "menuitem");
  const dot = el("span", "fork-dot");
  dot.setAttribute("aria-hidden", "true");
  const label = branchRecordLabel(branch, views.get(branch.id));
  const name = el("span", "fork-name", label);
  const meta = el("span", "fork-meta");
  row.append(dot, name, meta);
  if (branch.id === currentView.id) {
    row.classList.add("current");
    row.setAttribute("aria-current", "true");
    meta.textContent = "here";
    row.title = "You are reading this branch";
    row.setAttribute("aria-label", `${label}, current branch`);
  } else if (branch.id === group.pathId) {
    row.classList.add("path");
    meta.textContent = "on your path";
    row.title = "Your branch reads through this one";
    row.setAttribute("aria-label", `${label}, the branch you are reading through`);
  } else if (isParent) {
    meta.textContent = "source";
    row.title = "The branch this fork came from";
    row.setAttribute("aria-label", `Switch to ${label}, the branch this fork came from`);
  } else {
    row.title = `Switch to ${fullBranchLabel(branch)}`;
    row.setAttribute("aria-label", `Switch to ${label}`);
  }
  row.onclick = () => {
    closeForkMenu();
    switchBranch(branch.id);
  };
  return row;
}

function openForkMenu(node, group) {
  if (!currentView) return;
  const session = sessions.get(currentView.sessionId) || currentView.session;
  const branches = (session && session.branches) || [];
  const byId = new Map(branches.map((branch) => [branch.id, branch]));
  const parent = byId.get(group.parentId);
  const members = group.memberIds.map((id) => byId.get(id)).filter(Boolean);
  if (!parent || !members.length) return;

  const menu = $("fork-menu");
  const title = group.parentId === currentView.id
    ? "Branches from this point"
    : `Branched from ${branchRecordLabel(parent, views.get(parent.id))}`;
  menu.innerHTML = `
    <div class="fork-menu-head">
      <span class="fork-menu-title"></span>
      <span class="fork-menu-count"></span>
    </div>
    <div class="fork-tree"></div>`;
  menu.querySelector(".fork-menu-title").textContent = title;
  menu.querySelector(".fork-menu-count").textContent =
    `${members.length} ${members.length === 1 ? "branch" : "branches"}`;

  const tree = menu.querySelector(".fork-tree");
  tree.appendChild(forkMenuRow(parent, "fork-parent", group, true));
  const children = members.filter((branch) => branch.id !== parent.id);
  if (children.length) {
    const rail = el("div", "fork-children");
    for (const branch of children) {
      rail.appendChild(forkMenuRow(branch, "fork-child", group, false));
    }
    tree.appendChild(rail);
  }

  $("branch-menu").classList.add("hidden");
  $("branch-chip").setAttribute("aria-expanded", "false");
  if (forkMenuOpen) forkMenuOpen.node.setAttribute("aria-expanded", "false");
  menu.classList.remove("hidden");
  forkMenuOpen = { node, group };
  node.setAttribute("aria-expanded", "true");
  positionForkMenu(node);
  const focusRow = tree.querySelector(".fork-row.current")
    || tree.querySelector(".fork-row.path")
    || tree.querySelector(".fork-row");
  if (focusRow) focusRow.focus({ preventScroll: true });
}

function positionForkMenu(node) {
  const menu = $("fork-menu");
  const rect = node.getBoundingClientRect();
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const gap = 10;
  const margin = 8;
  const left = Math.min(
    Math.max(rect.left + rect.width / 2 - width / 2, margin),
    window.innerWidth - width - margin,
  );
  const limit = Math.min(
    window.innerHeight - margin,
    $("composer").getBoundingClientRect().top - margin,
  );
  let top = rect.bottom + gap;
  if (top + height > limit) {
    const above = rect.top - gap - height;
    top = above >= margin ? above : Math.max(margin, limit - height);
  }
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

function closeForkMenu() {
  if (!forkMenuOpen) return;
  $("fork-menu").classList.add("hidden");
  forkMenuOpen.node.setAttribute("aria-expanded", "false");
  forkMenuOpen = null;
}

// The transcript, from projected messages. Deliberately not a perfect
// reconstruction of the live view: tool *proposals* are governance rather than
// conversation, and a governance record is absent from the display projection.
// What a reader wants is what was said, what was thought, and what ran — the
// display projection carries the thinking under `reasoning` (D46).
//
// A projected `user` turn is a string in every case a reader sees now. The
// guard is for the one shape it used to be: an image turn, whose content is a
// provider content-part list. There is no text in a picture and the picture
// belongs on the tool card, so only the text parts would be shown — but
// `textContent = [object Object]` was the failure this replaces.
function messageText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((part) => part && part.type === "text")
      .map((part) => part.text || "")
      .join("\n");
  }
  return "";
}

// A reopened session starts collapsed: this builds each row and its `_detail`
// but never stages one, so every row is there to click and none is expanded. A
// reader who asked to review a session has not asked to look at its last step.
function replay(messages) {
  followTail = true;
  promptCursor = null;
  // The projection says which user rows open a turn (`opens_turn`): a steering
  // row is also a `user` message, and it follows a reply as often as an opening
  // does. The client cannot infer it from the previous role; only the branch's
  // `turn_complete` rows know, and the projection is where they were read.
  let turnIndex = 0;
  // Forks are placed at the completed turn they left. A projected transcript has
  // no row for that boundary, so the node is inserted before the first row with
  // a later seq; the layout says which branch owns the turn that ended and which
  // branch begins after it.
  const layout = branchForkLayout(renderView);
  let forkIndex = 0;
  const flushForks = (beforeSeq) => {
    while (forkIndex < layout.groups.length && layout.groups[forkIndex].s < beforeSeq) {
      const group = layout.groups[forkIndex];
      forkIndex += 1;
      appendTranscript(forkMarkerEl(group));
    }
  };
  const ownStart = (renderView.branch.inherited_event_count || 0) + 1;
  for (const message of messages) {
    // The boundary is a `turn_complete` row, which the display projection does
    // not draw. The first projected row after it is the earliest thing that can
    // follow the fork node, so every message flushes by seq: a compaction note
    // can sit between the boundary and the next prompt, and it belongs *after*
    // the fork, not above it.
    if (message.seq !== undefined && message.seq !== null) flushForks(message.seq);
    if (message.role === "user") {
      const opensTurn = message.opens_turn === true;
      const node = add(el("div", "turn user", messageText(message.content)));
      if (opensTurn) turnIndex += 1;
      // An edit forks before this turn. It is offered only on a prompt this
      // branch wrote: the inherited prefix is another branch's history, and a
      // fork made inside it would have no turn of this branch's own to name.
      if (message.seq && opensTurn && turnIndex > 1 && message.seq >= ownStart) {
        attachEditAction(node, renderView, message.seq, messageText(message.content));
      }
    } else if (message.role === "note") {
      // A line from Neuman rather than a person: the marker for a compaction —
      // the model's context was shortened here, though the reader still gets
      // every message, the same line the live `compacted` frame draws — or the
      // notice that a background job ended while nothing was watching it (D98).
      add(el("div", "turn note", message.content));
    } else if (message.role === "assistant") {
      // The display projection carries the chain of thought as a reader-only
      // field, because a live turn shows a thinking row and a reopened one
      // should read the same. It comes first, the order it streamed in.
      if (message.reasoning) {
        const row = reasoningBlock({ animate: false });
        row._preview.textContent = previewLine(plainPreview(message.reasoning));
        // The body is the expensive half and the detail starts closed, so it is
        // filled when the row is opened. The preview above is the one line the
        // collapsed row shows either way, and it renders the same markdown the
        // live path streamed.
        row._detail._reveal = () => {
          row._body.dataset.raw = message.reasoning;
          renderMarkdown(message.reasoning, row._body);
          paintThoughtBand(row._body);
        };
      }
      if (message.content) {
        const bubble = add(el("div", "turn agent md"));
        bubble.dataset.raw = message.content;
        renderMarkdown(message.content, bubble);
      }
      for (const call of message.tool_calls || []) {
        const fn = call.function || {};
        // `toolCard` rather than a hand-built card, so a replayed call gets the
        // same summary line as a live one. Arguments arrive as a JSON *string*
        // here and as an object live; parse so both reach the summariser in the
        // shape it expects.
        let args = {};
        try {
          args = fn.arguments ? JSON.parse(fn.arguments) : {};
        } catch {
          args = {};
        }
        const name = fn.name || "tool";
        // Deferred: the head and the timeline row are the whole glance, and the
        // arguments and result are built only if the row is opened. No
        // approve/deny on history either — the verdict was given long ago, and
        // buttons that do nothing are worse than none (N89's lesson).
        const card = toolCard(
          { tool_call_id: call.id, name, arguments: args },
          { deferred: true, animate: false },
        );
        card._reveal = () => {
          // The output is inside the card's scrolling body; insert the arguments
          // in front of it there, so one scrollport holds both halves.
          const output = card.querySelector(".tool-output");
          output.before(toolInput(name, args));
        };
      }
    } else if (message.role === "tool") {
      // Attach to its call when we have the card, so a result sits under the
      // arguments that produced it rather than floating below everything.
      const card = toolCards.get(message.tool_call_id);
      if (card) {
        // The result half of the reveal, chained onto the arguments half built
        // above. Nothing here touches the DOM: a write's diff and the result
        // body are what make opening a long session slow, and both are built
        // when the card is opened rather than when the session is.
        //
        // `rememberScratch` stays eager: a later `fs_read` of a scratch path
        // names the call that wrote it, and that lookup must be complete for
        // the whole transcript before any card is opened.
        rememberScratch(message, card);
        const build = card._reveal;
        card._reveal = () => {
          if (build) build();
          // A write's diff survives a reload: the server sends `structured` in
          // the display projection, so a reopened session shows the change
          // rather than the one-line summary the model saw.
          if (message.structured && message.structured.diff) {
            const args = card.querySelector(".args");
            if (args) args.replaceWith(diffView(message.structured));
          }
          renderResult(card, message);
        };
        // The outcome, where a live card says `failed`. `wall_ms` is not in the
        // display projection, and the exit code is not shown either: the result
        // body carries the failure in red, and `exit 0`/`exit 2` is a number most
        // readers cannot read. The row's right edge instead says what came back —
        // the result's size — and its node says how it ended, so a reopened
        // session reads the same as the live one it replaced. A call whose result
        // never arrived keeps that initial `proposed`.
        const hasExit = message.exit_code !== undefined && message.exit_code !== null;
        const failed = hasExit && message.exit_code !== 0;
        setToolState(
          message.tool_call_id,
          failed ? "failed" : "",
          failed || !message.content ? "" : humanBytes(message.content),
          failed ? "failed" : "done",
        );
      } else {
        add(el("div", "turn note", unwrapUntrusted(message.content) || ""));
      }
    }
  }
  // Groups after the last message still belong on the transcript: a branch with
  // no own turn yet has its origin node at the end.
  flushForks(Infinity);
  // The count belongs to the view, not to this replay: the next message typed
  // into a reopened session opens turn N+1, and its edit point exists unless it
  // is the first turn (N174).
  renderView.turnIndex = turnIndex;
  log.scrollTop = log.scrollHeight;
  paintJumpLive();
}

// -- the draft ---------------------------------------------------------------
//
// `activeBranchId === null` is the draft: signed in, nothing started, no row on the
// server and no socket. The first message is what makes a session exist.
//
// This replaces a boot path that POSTed `/api/sessions` on every page load.
// Nothing ever cleaned those up — the only DELETE in the store needs
// `closed_at`, which an abandoned session never gets — so every reload left an
// immortal untitled row, and fifty of them pushed real conversations out of a
// list that shows fifty. The hero is the fix for that as much as it is a nicer
// way in: a session you never spoke to now leaves nothing behind.
//
// It also gives removing the active session somewhere to land.

function drafting() {
  return activeBranchId === null;
}

// Back to the hero: no session on screen, no draft socket, nothing staged.
//
// The views that are still doing something stay. Clicking "New session" is not
// a request to stop the turns in flight — that is the whole point of the Active
// section — so a running or unread transcript moves into its holder and waits.
function showDraft() {
  viewOpen++;
  closeForkMenu();
  if (currentView) {
    saveRender(currentView);
    stashViewDom(currentView);
    // Kept, not discarded: going back to the hero is not a reason to close a
    // session the reader may return to. The window in Settings decides when it
    // closes, and the sweep does the closing.
    parkView(currentView);
    currentView = null;
    activeBranchId = null;
    renderView = null;
  }
  resetRender();
  $("log").replaceChildren();
  $("log").dataset.session = "";
  jumpLive.classList.add("hidden");
  setBusy(false);

  draftWorkingDir = null;
  draftAttachments = [];
  // Back to the defaults for a new conversation: every granted tool, and the
  // account's own model and reasoning. A narrowing chosen for one conversation
  // is not a preference stated for the next.
  draftToolSelection = null;
  draftModel = null;
  draftReasoning = null;
  draftApproval = null;
  toolSelection = null;
  showWorkingDir(null);
  showAttachments([]);
  paintTools();
  branchApproval = null;
  applyDraftChoices();
  paintBranchChip();

  followTail = true;
  promptCursor = null;
  clearUsage();
  $("context-bar").classList.add("hidden");
  paintHero();
  renderSessions();
  // No `paintComposerMode` here: `setBusy` above already calls it.
}

// The hero sits inside `#log` rather than beside it: the transcript's grid area
// is already the empty space being filled, and it already centres its contents
// to a readable measure. A second element in the grid would mean two things
// competing for one row and a layout that has to switch between them.
function paintHero() {
  if (!drafting()) return;
  const hero = el("div", "hero");
  hero.appendChild(el("h2", null, "What are we working on?"));
  // Mounted on the composer, not appended to the transcript.
  //
  // It used to be a row in `#log`, which meant it occupied a grid track above
  // the message box — and a track with height pushes the box off centre by half
  // of it. The box is what the draft is centred on, so the heading hangs from
  // its top edge instead (`.hero` under `body.drafting`), out of the flow and
  // anchored to the thing it captions.
  //
  // The cost of moving it: clearing `#log` no longer removes it, so every exit
  // from the draft has to call `clearHero` explicitly.
  clearHero();
  $("context").appendChild(hero);
  // The box lifts to the middle of the window while this is set, instead of
  // sitting at the bottom of an empty one. A class on `body` rather than inline
  // styles, because what moves is the *grid*, which is a layout decision and
  // belongs in the stylesheet with the grid it changes.
  document.body.classList.add("drafting");
  // A *move*, not a size change: the observer below sees boxes, and the box is
  // the same at the floor of the window and at the middle of it. A stale
  // `--headroom` caps every upward popover to the room the previous layout had.
  measureHeadroom();
}

// The hero is a child of the composer now, so clearing `#log` no longer removes
// it. One function takes it out, and one function leaves the draft — every exit
// goes through `leaveDraft` rather than dropping the class itself, because the
// two things now have to happen together and a fourth exit that only did one
// would leave "What are we working on?" floating over a real conversation.
function clearHero() {
  const existing = $("context").querySelector(".hero");
  if (existing) existing.remove();
}

function leaveDraft() {
  document.body.classList.remove("drafting");
  clearHero();
  measureHeadroom();
}

// Create the session the draft describes, then hand back its view.
//
// Ordered deliberately: create, PATCH what was staged, then mount the view and
// connect. The socket's first frame is the message, and the turn reads the
// working directory and attachments from the row when it starts — so staging
// has to be on the row before the frame goes out, or the first turn runs
// without them.
async function startSession() {
  // A history row's GET may still be in flight from before the first message.
  // Creating a session supersedes that choice, so the late answer must not
  // mount a conversation the reader has already left the draft to start.
  viewOpen++;
  const created = await api("POST", "/api/sessions", {});
  const { branch, ...session } = created;
  sessions.set(session.id, { ...session, branches: [branch] });
  const view = makeView(session, {
    ...branch,
    working_dir: draftWorkingDir,
    attachments: draftAttachments,
    tool_selection: draftToolSelection,
    model: draftModel,
    reasoning: draftReasoning,
    approval: draftApproval,
  });
  views.set(view.id, view);

  // `!== null` for the selection rather than a length test: `[]` is a real
  // staged choice — a conversation the user asked to run with no tools — and
  // treating it as nothing staged would hand the first turn every tool instead.
  const body = {};
  if (draftWorkingDir !== null) body.working_dir = draftWorkingDir;
  if (draftAttachments.length) body.attachments = draftAttachments;
  if (draftToolSelection !== null) body.tool_selection = draftToolSelection;
  if (draftModel !== null) body.model = draftModel;
  if (draftReasoning !== null) body.reasoning = draftReasoning;
  if (draftApproval !== null) body.approval = draftApproval;

  let warning = null;
  if (Object.keys(body).length) {
    try {
      const saved = await api("PATCH", `/api/branches/${view.id}`, body);
      view.branch = { ...view.branch, ...saved };
      view.workingDir = saved.working_dir ?? null;
      view.attachments = saved.attachments ?? [];
      view.toolSelection = saved.tool_selection ?? null;
      view.model = saved.model ?? null;
      view.reasoning = saved.reasoning ?? null;
      view.approval = saved.approval ?? null;
      view.session.branches = [saved];
      sessions.get(view.sessionId).branches = [saved];
    } catch (error) {
      // The session exists and the staged context does not. The view falls back
      // to the branch's defaults rather than showing a directory the first turn
      // is not going to run in; the sentence below is what stops that looking
      // like the agent's mistake.
      view.workingDir = null;
      view.attachments = [];
      view.toolSelection = null;
      warning = `Session started, but its context did not save: ${error.message}`;
    }
  }

  draftWorkingDir = null;
  draftAttachments = [];
  draftToolSelection = null;
  draftModel = null;
  draftReasoning = null;
  draftApproval = null;

  // The composer drops back to the bottom of the window the moment there is a
  // transcript for it to sit under.
  showView(view);
  if (warning) note(warning);
  return view;
}

// One socket per view. A background view's frames are rendered into its own
// holder, so the connection staying open is what keeps the turn alive: the
// server cancels a turn when its last client vanishes, and switching sessions
// no longer vanishes.
function connect(view) {
  // The session may have been removed while a create or a switch was opening
  // its socket. A view that is no longer in the map has nothing to connect to.
  if (!views.has(view.id)) {
    const opened = Promise.resolve(false);
    view.opening = opened;
    return opened;
  }
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  // No token in the URL: the WebSocket authenticates on upgrade from the same
  // HttpOnly cookie, so a URL that leaks into a log or a Referer carries
  // nothing useful.
  const sock = new WebSocket(`${scheme}//${location.host}/ws/branches/${view.id}`);
  view.socket = sock;

  // Resolved when this socket is usable, so the first message of a new session
  // can wait for the handshake instead of racing it. Every other caller ignores
  // the promise and keeps the fire-and-forget behaviour it had.
  //
  // Resolves rather than rejects on failure, with whether it opened: a reject
  // from a promise most callers drop is an unhandled rejection, and the one
  // caller that waits already reports the failure itself.
  let settle;
  const opened = new Promise((resolve) => { settle = resolve; });
  view.opening = opened;
  view.settleOpen = settle;

  sock.onopen = () => {
    view.reconnectDelay = 0;
    view.settleOpen = null;
    if (view === currentView) note("Connected.");
    settle(true);
  };
  sock.onmessage = (event) => {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch {
      if (view === currentView) note("Received a frame that was not JSON.");
      return;
    }

    // The rail's state is a fact about the frame whether or not anybody is
    // looking at the transcript, so it is folded in first. `handle` is what
    // needs a surface; it draws into the view's holder when the view is hidden.
    trackViewFrame(view, frame);

    // Rendering a frame must not be able to strand the session.
    //
    // `handle` used to be called bare. A throw inside it escaped the listener
    // into the console, and because `setBusy(false)` lives in exactly two
    // places — a socket close and the `done` case — a render error inside
    // `done` left the composer saying "Steer" with Stop live, forever: the
    // socket was healthy, so the `onclose` fallback never ran either. A
    // finished turn presented as a frozen app, and the only evidence was an
    // exception in a console nobody had open (N99).
    //
    // So the failure is contained and *shown*. The transcript is where a reader
    // is already looking, and a visible "this frame did not render" beats a
    // silent one every time — it names the frame, so the bug is reportable
    // rather than just felt.
    try {
      if (view === currentView) handle(frame);
      else withView(view, () => handle(frame));
    } catch (error) {
      const report = () => note(
        `Could not render a "${frame && frame.type}" frame: ${error && error.message}`);
      if (view === currentView) report();
      else withView(view, report);
      // Rethrown on a macrotask so the console still gets the real stack with
      // its original trace, without that throw skipping the recovery below.
      setTimeout(() => {
        throw error;
      });
    } finally {
      // The turn's end is a fact about the session, not about whether its last
      // frame drew. `done` means no turn is running, so the controls return to
      // idle even if rendering it failed — which is precisely the case that
      // used to wedge.
      if (frame && frame.type === "done" && view === currentView) {
        setBusy(false);
        // A job that ended while the model was not waiting on it sends no
        // frame (there is no turn to send on); the turn's end is where the row
        // finds out it is gone.
        refreshJobs();
      }
    }
  };
  sock.onclose = (event) => {
    if (view.socket !== sock) return;
    view.socket = null;
    view.settleOpen = null;
    settle(false);

    // 4401/4403/4404 are the server's own codes for unauthenticated, wrong
    // origin, and no such session — all of which mean "do not reconnect".
    if (event.code === 4401 || event.code === 4403) {
      showLogin("Session ended. Sign in again.");
      return;
    }
    if (event.code === 4404) {
      // Also what a session removed on another device answers now, since the
      // upgrade refuses a removed one. This view is not reconnectable; if it is
      // the one on screen, the draft is the state that is always valid.
      const wasCurrent = view === currentView;
      if (wasCurrent) note("That session is no longer available.");
      dropView(view, { force: true });
      if (wasCurrent) showDraft();
      return;
    }

    // A dropped connection is a cancelled turn server-side — the socket's own
    // close handler is what stops work that nobody can watch. So the rail stops
    // saying it is thinking, and a reply that was in flight is not presented as
    // one that landed.
    const wasRunning = view.running;
    if (wasRunning) {
      view.running = false;
      view.busy = false;
      view.unread = view !== currentView;
      view.state = "done";
      view.detail = "";
      // The server cancels a turn when its socket goes, so no `done` is coming
      // and the next message opens a turn rather than steering one.
      view.turnOpen = false;
      if (view.unread) view.followTail = true;
    }
    if (view === currentView) {
      setBusy(false);
      note(`Disconnected${event.code ? ` (${event.code})` : ""}. Reconnecting…`);
    } else if (wasRunning) {
      withView(view, () => note(`Disconnected${event.code ? ` (${event.code})` : ""}.`));
    }
    renderSessions();

    // A background turn is already over as far as the server is concerned; a
    // new socket would be for a message nobody has sent yet. The view on screen
    // reconnects, so an approval card's buttons are not left dead.
    if (view === currentView) reconnectView(view);
  };

  return opened;
}

function setBusy(busy) {
  // The flag belongs to the session on screen, so a background frame that ends
  // its turn cannot make this composer look idle while this view is still busy.
  if (currentView) currentView.busy = busy;
  // Not disabled while busy: the same box steers mid-turn, so the button stays
  // live and changes what it *means*.
  //
  // The name moves rather than the glyph. This was `textContent = "Steer"`,
  // which would now delete the icon and leave a bare word in its place — the
  // button's only child is an `<svg>`. The arrow is right for both acts anyway:
  // sending and steering are the same gesture into the same inbox, and D12 is
  // the reason there is one button at all.
  const label = busy ? "Steer this turn" : "Send";
  $("send").title = label;
  $("send").setAttribute("aria-label", label);
  // Stop is shown only while there is a turn to stop. Moved out of the header
  // into the composer: it controls *this* turn, which is what the box beside it
  // does, and an idle header should not carry a disabled button for a state
  // nobody is in.
  $("interrupt").classList.toggle("hidden", !busy);
  $("interrupt").disabled = !busy;
  paintComposerMode();
}

// -- the protocol -----------------------------------------------------------
//
// One case per `type` in ws.schema.json. Unknown types are *shown*, not
// dropped: a client that silently ignores what it does not understand hides a
// protocol change behind a blank screen.

function handle(frame) {
  // The branch this frame arrived on. A background branch's frames are rendered
  // through `withView`, which swaps the module names these cases address, so
  // the view here is the render context and not necessarily the one on screen
  // (D63). Naming it once keeps a case from reaching for a `view` parameter
  // that does not exist (N174).
  const view = renderView;
  switch (frame.type) {
    case "waiting":
      // A state, not an event. The rail's phase already says the model is
      // silent (`trackViewFrame`), and drawing a line in the transcript moved
      // the page when it arrived and again when it went. Nothing is drawn here;
      // the composer's Stop button and the rail carry the wait.
      break;

    case "truncated":
      // The reply hit the token cap mid-thought and the turn is carrying on
      // from it. Shown because the alternative was indistinguishable from a
      // finished answer: reasoning, then silence, and no way to tell "done"
      // from "cut off" (N90).
      //
      // The bubble is closed first, so what follows starts a new one rather
      // than appearing to continue the same sentence across the break.
      agentTurn = null;
      endThought();
      add(el("div", "turn note", "…reply hit the length limit; continuing"));
      break;

    case "jobs":
      // A call detached, or the set changed: the row lives in the daemon, so
      // the frame is a nudge to re-read it rather than a second copy of it.
      refreshJobs();
      break;

    case "compacted": {
      // The capacity bar is about to fall by most of its width, so the drop
      // gets a line rather than being left to look like a defect — which is
      // what it looked like when pruning re-ran unrecorded every step and the
      // reported size alternated between two values (N119).
      //
      // Deliberately plain about the cost: the summary is lossy, and a user who
      // later finds the agent has forgotten an early detail should be able to
      // scroll back and see where it went.
      const n = frame.summarized_messages || 0;
      agentTurn = null;
      endThought();
      add(
        el(
          "div",
          "turn note",
          n > 0
            ? `…context filled; summarized the earlier ${n.toLocaleString()} messages and continuing`
            : "…context filled; summarized the earlier conversation and continuing",
        ),
      );
      break;
    }

    case "token":
      if (!agentTurn) {
        // The flush class cancels the column gap for the bubble written into a
        // card's reserved blank: the gap is a height change the reserve has not
        // funded yet. `add` folds the stage and pins the blank before this
        // bubble is appended.
        agentTurn = add(el("div", "turn agent md"), { flush: true });
        // The raw text, kept because markdown cannot be appended to: `**bo` is
        // not bold until its closing `**` arrives, so each token re-renders the
        // whole reply from source rather than mutating what is on screen.
        agentTurn.dataset.raw = "";
      }
      agentTurn.dataset.raw += frame.text;
      paintReply(agentTurn);
      break;

    case "reasoning":
      if (!reasoningTurn) {
        reasoningTurn = reasoningBlock();
        // The chain of thought is the thing worth watching, so its row stages as
        // it starts streaming — at the foot of the run, where it is read, rather
        // than into a pane. The stage appears once and the tail follows it.
        //
        // Unless the reader said otherwise in Settings. The row is still built
        // and still fills, so the thinking is there to open and is recorded
        // either way — the preference governs whether it *stages itself*,
        // which is the part that reserves the card for the turn. A preference
        // that suppressed the row would hide the fact that the model thought at
        // all.
        if (!hideReasoning()) grow(() => followStep(reasoningTurn));
      }
      // The stage is one fixed height, so the log has nothing to chase: the
      // thought follows its own tail inside the body, and the card does not
      // grow by a line as it streams. The text is released on a reading clock;
      // see `paintThought` (D66).
      grow(() => {
        reasoningTurn._body.dataset.raw =
          (reasoningTurn._body.dataset.raw || "") + frame.text;
        paintThought(reasoningTurn);
      });
      break;

    case "user_message":
      // The server echoes every user message — its own input *and* steering —
      // so this is the only place a user bubble is drawn. Rendering
      // optimistically on send as well would double every line in this tab,
      // and the first version of this file did exactly that behind a
      // `frame.echo` check for a field the contract does not have
      // (`additionalProperties: false`, so it never could).
      //
      // Waiting for the echo also makes a second tab consistent, and means a
      // message that the server rejected never appears as though it were sent.
      //
      // The first user turn of a turn is the opening prompt an edit forks
      // before. A steering message is not — the boundary resolves to the
      // previous turn, which would silently fork more than the reader asked for.
      const opensTurn = !view.turnOpen;
      view.turnOpen = true;
      if (opensTurn && view.branch && view.branch.forked_from && !view.firstMessage) {
        view.firstMessage = frame.content;
        const known = (sessions.get(view.sessionId) || {}).branches || [];
        const entry = known.find((branch) => branch.id === view.id);
        if (entry) entry.first_message = frame.content;
        paintBranchChip();
      }
      const node = add(el("div", "turn user", frame.content));
      if (opensTurn) view.turnIndex += 1;
      const ownStart = (view.branch.inherited_event_count || 0) + 1;
      if (opensTurn && view.turnIndex > 1 && frame.seq && frame.seq >= ownStart) {
        attachEditAction(node, view, frame.seq, frame.content);
      }
      break;

    case "tool_proposal":
      // A tool call closes whatever bubbles are open, so what comes *after* it starts
      // fresh below the card.
      //
      // Without this, `reasoningTurn` and `agentTurn` were nulled only at `done` — so
      // post-tool reasoning appended to the bubble created *before* the tool and
      // appeared above it. A turn with three tool calls read as one wall of thinking
      // at the top followed by three cards, rather than
      // reason -> call -> reason -> call, which is the order it happened in (N78).
      agentTurn = null;
      endThought();
      toolCard(frame);
      // The step the turn is on stages as it starts, so the chain shows the call
      // in flight rather than a row that never unfolds. This is also what
      // crossfades the thinking block the call ended into the tool's card.
      grow(() => followStep(toolRows.get(frame.tool_call_id)));
      break;

    case "tool_approval_required": {
      const card = toolCards.get(frame.tool_call_id);
      if (!card) break;
      // An approval is the one thing worth opening without being asked: a
      // blocked turn whose buttons are out of view is the N89 shape of bug, so
      // the row stages its card. `grow` for the same reason the decision handler
      // uses it: staging can create the stage, which is a height change, not
      // only an append.
      grow(() => {
        followStep(toolRows.get(frame.tool_call_id), { intoView: !followTail });
        // A write replaces its arguments with its diff. Only here, not on the
        // proposal: the preview arrives with this frame because resolving it is
        // a dispatch the server makes only when someone is actually being asked.
        if (frame.preview) {
          const args = card.querySelector(".args");
          if (args) args.replaceWith(diffView(frame.preview));
        }
        const ask = card.querySelector(".ask");
        if (ask) ask.classList.remove("hidden");
        setToolState(frame.tool_call_id, "waiting for you", undefined, "waiting");
      });
      break;
    }

    case "tool_decision": {
      const card = toolCards.get(frame.tool_call_id);
      if (!card) break;
      grow(() => {
        const ask = card.querySelector(".ask");
        if (ask) ask.remove();
        // Who decided, in words. This printed the raw boolean — "running ·
        // false" — which is a field name's value rather than a sentence, and
        // the reading most people land on ("running: false"?) is the opposite
        // of what it means.
        const who = frame.consent ? "you" : "auto";
        setToolState(
          frame.tool_call_id,
          frame.accepted ? `running · ${who}` : `refused · ${who}`,
          undefined,
          frame.accepted ? "running" : "refused",
        );
      });
      break;
    }

    case "tool_result": {
      const card = toolCards.get(frame.tool_call_id) || toolCard({
        tool_call_id: frame.tool_call_id,
        name: frame.name,
        arguments: {},
      });
      // What the card's head says about the outcome. Not the exit code: `exit 2`
      // is a number most readers cannot interpret at a glance, and the failure is
      // already in the result body, in red. A finished success says nothing on the
      // card — the result is the state — while the row spends its right edge on
      // what came back, because the node already carries *how* it ended (D44).
      let outcome = "";
      let kind = "done";
      if (frame.timed_out) { outcome = "timed out"; kind = "failed"; }
      else if (frame.cancelled) { outcome = "cancelled"; kind = "refused"; }
      else if (frame.exit_code) { outcome = "failed"; kind = "failed"; }
      const summary = outcome || !frame.content ? "" : humanBytes(frame.content);
      const body = card.querySelector(".tool-body");
      grow(() => {
        setToolState(frame.tool_call_id, outcome, summary, kind);
        // The card's height is fixed (D62), so the transcript has nothing to
        // follow here: the result arrives under the arguments, inside the
        // card's own scrollport, and follows that tail unless the reader has
        // scrolled up inside the card.
        growInner(body, () => renderResult(card, frame));
      });
      break;
    }

    case "usage":
      // Folded into the view by `trackViewFrame` before this runs, and painted
      // there only when that view is the one on screen. A background branch's
      // figures are kept for the moment it is opened; they are not this
      // composer's figures.
      break;

    case "error":
      add(el("div", "turn error", frame.message ? `${frame.code}: ${frame.message}` : frame.code));
      // A message refused because this branch already has a turn running did
      // not start one here. `submit` marks the view running before the socket
      // carries the frame, and no `done` is coming on this connection — the
      // turn belongs to another socket — so leaving it would show Steer over a
      // turn this view never started (N179).
      if (frame.code === "turn_in_flight") markTurnFailed(view);
      break;

    case "done": {
      // The turn is over, so the next user message opens a new turn. Its
      // `turn_complete` seq is the boundary an edit uses; the projection places
      // the fork node before the next opening prompt.
      view.turnOpen = false;
      // A turn ended, so the next token starts a new bubble, and the work run
      // ends too — the next turn's first call must not join this one's run.
      agentTurn = null;
      endThought();
      workRun = null;
      // Prose has already folded the run's stage, by way of `add`. A run that
      // ended without prose leaves its stage in place as the turn's artifact;
      // `releaseReserve` does nothing there. If the reply was shorter than the
      // blank, the blank closes here rather than ending the transcript in a
      // void.
      releaseReserve(180);
      // The composer and the rail belong to the session on screen.
      // A background turn has already been folded into its view by
      // `trackViewFrame`, including the list repaint its new title needs.
      if (isLiveRender()) {
        setBusy(false);
        // It landed in the window that is watching it, so it has been read. The
        // watermark is the account's: without this, the row would say `finished`
        // until the next open, in this window as well as the others.
        markRead(view.sessionId);
        // The first turn of a session is also where it gets its name, derived
        // from the opening message by the server. The list was painted when the
        // session was created — a moment too early to have seen that title — so
        // a fresh session sat there as "untitled" until something else repainted.
        loadSessions();
      }
      // A reason worth acting on gets a sentence; the rest get their name.
      // `truncated` and `max_iterations` both mean "there is more to do", and
      // the next thing to type is the same in both cases — so say it, rather
      // than leaving the reader to infer it from a word.
      if (frame.reason === "truncated" || frame.reason === "max_iterations") {
        note(`Ended: ${frame.reason} — say "continue" to pick up where it stopped.`);
      } else if (frame.reason && frame.reason !== "stop") {
        note(`Ended: ${frame.reason}`);
      }
      break;
    }

    default:
      note(`Unhandled frame: ${JSON.stringify(frame)}`);
  }
}

// Two thresholds, as fractions of the compaction point. Below `WARN` the bar is
// quiet; past `BAD` the next summary is close. Chosen so the colour arrives with
// a turn or two still in hand.
//
// They mean something milder than they used to. While the denominator was the
// model's window, `BAD` meant "a turn is close to not fitting" — a failure.
// Measured against the compaction point it means "the oldest span is about to be
// summarized", which is designed behaviour and recoverable: the turn continues,
// lossily. The colours are kept because the *user* may still want to act —
// finishing a thought before the summary lands is a reasonable thing to want —
// but a full bar is no longer a warning of breakage (N120).
const CONTEXT_WARN = 0.7;
const CONTEXT_BAD = 0.9;

function showContext(usage, capacity, compactsAbove) {
  const bar = $("context-bar");
  const used = usage && usage.prompt_tokens;

  // **The denominator is where compaction happens, not the model's window.**
  //
  // Those are different numbers, and only one of them is a thing the user is
  // approaching. The window reserves room for the reply, a margin and the
  // compaction reserve before the conversation gets any — on a 65,536-token
  // model that leaves 30,311 — so a bar against the window sat a little under
  // half full at the moment history was summarized, which reads as a defect
  // rather than as the designed behaviour (N119, N120).
  //
  // Measuring against the window instead was also the mistake N116 and N117
  // were both about: believing the conversation may use all of it.
  //
  // Falls back to the window when the compaction point is unknown, which keeps
  // a bar for a model whose budget cannot be computed rather than hiding one.
  const ceiling = compactsAbove && compactsAbove > 0 ? compactsAbove : capacity;

  // Both halves or nothing. A window the operator never recorded means there is
  // no denominator, and a bar against an invented one is precisely the failure
  // this exists to surface — budgeting against an unrecorded window is what
  // ended two sessions (N86). So it stays hidden rather than reassuring.
  if (!ceiling || ceiling <= 0 || used == null) {
    bar.classList.add("hidden");
    return;
  }

  const fraction = Math.min(used / ceiling, 1);
  bar.classList.remove("hidden");
  // Capped at 100%: history can exceed the compaction point — that is what a
  // step which has just grown past it looks like, for the one step before the
  // summary lands — and a bar wider than its track would overflow the status
  // line rather than reporting it.
  $("context-fill").style.width = `${(fraction * 100).toFixed(1)}%`;
  bar.classList.toggle("warn", fraction >= CONTEXT_WARN && fraction < CONTEXT_BAD);
  bar.classList.toggle("bad", fraction >= CONTEXT_BAD);

  // The exact figures, on hover. `prompt_tokens` is the provider's own count for
  // the last request, so this is what the context actually held rather than an
  // estimate — and it describes the turn that just happened, not the one being
  // typed.
  //
  // Both numbers are named, because "48,000 of 30,311" would be nonsense
  // without saying which ceiling is which: the model's window is the fact
  // people compare models on, and the compaction point is what this bar is
  // actually measuring.
  const percent = Math.round(fraction * 100);
  const window_ =
    compactsAbove && compactsAbove > 0 && capacity && capacity > compactsAbove
      ? `; model window ${capacity.toLocaleString()}`
      : "";
  bar.title =
    `context: ${used.toLocaleString()} of ${ceiling.toLocaleString()} tokens ` +
    `before summarizing (${percent}%)${window_}`;
}

// The caption above the context bar: the one figure the bar cannot show.
//
// It was `↑92k ↓2.4k · $0.0142` first, and that was wrong twice. It is 120px of
// text over an 80px gauge, so the caption dominated the thing it captions; and
// `↑` is the bar's own numerator — the fill *is* `prompt_tokens` over the
// compaction point — so the caption repeated in characters what the bar already
// says in length.
//
// What is left is the figure with no other home: the spend, because that is the
// number worth the space (it is why the total came off this line in the first
// place).
//
// **A model with no price gets no caption at all.** `↓2.4k` stood here for a
// while where there was no rate to price the turn, on the argument that output
// tokens are the only cost a free model has. It answered a question nobody
// asked: the gauge is the reading, and a lone count above it — sharing its line
// with no other figure — read as another control on the status line rather than
// as the bar's caption. What the bar measures is in the bar's own title.
//
// Absence of a cost is how a local model is recognised here, and it is not a
// proxy: `session_cost_usd` is sent only when `turn_cost` could price the step,
// and it declines to price one with no declared rates, which is what a model on
// this machine is. Localness itself lives in `/api/models/state`, which is
// operator-only appliance topology and never reaches a plain account's client.
//
// Where there *is* a caption, the whole `in · out · total` reading is one hover
// away in `usageTitle`.
function usageLine(usage, frame) {
  if (!usage) return "";
  return money(frame && frame.session_cost_usd, frame && frame.session_cost_estimated);
}

// The exact counts, on hover and for a screen reader. The caption carries the
// session's spend, so these are the figures it is *not* showing rather than a
// sentence repeated — and they stay one property read away rather than deleted.
// Set on the caption, so on a local model, where there is no caption, there is
// nothing to hover; the bar's own title is the reading there.
function usageTitle(usage) {
  if (!usage) return "";
  const parts = [];
  if (usage.prompt_tokens != null) parts.push(`${usage.prompt_tokens.toLocaleString()} in`);
  if (usage.completion_tokens != null) parts.push(`${usage.completion_tokens.toLocaleString()} out`);
  if (usage.total_tokens != null) parts.push(`${usage.total_tokens.toLocaleString()} total`);
  return parts.length ? `this turn: ${parts.join(" · ")} tokens` : "";
}

// The one writer of the status line's token text, so the value cleared on a new
// session cannot leave a stale title behind.
function clearUsage() {
  $("usage").textContent = "";
  $("usage").title = "";
}

// A running total in dollars, or "" when there is nothing to say.
//
// Cumulative over the session rather than per turn, which is what makes it
// worth a status line at all: one turn's cost is noise, and the question a
// user actually has is what this conversation has run to. A local model has no
// rate, and the server sends no figure rather than a zero: "free" and "not
// priced" are different claims and `$0.00` is the one that gets believed —
// which is why an unpriced turn puts nothing above the bar rather than a dollar
// amount this would have had to invent. `~` marks a figure
// we computed from declared rates rather than one the provider billed — the
// distinction the `estimated` flag exists to carry, and the reason this does
// not simply print a number.
function money(usd, estimated) {
  if (typeof usd !== "number" || !isFinite(usd) || usd <= 0) return "";
  // Sub-cent totals are the common case for a few turns on a cheap model, and
  // rounding them to $0.00 would read as free. Four decimals down there, two
  // once there is a cent to show.
  const shown = usd < 0.01 ? usd.toFixed(4) : usd.toFixed(2);
  return `${estimated ? "~" : ""}$${shown}`;
}

// Markdown, as much of it as a chat window needs.
//
// Models write `**bold**`, `` `code` ``, bullet lists and fenced blocks whether
// or not anything renders them, so the choice is between showing the marks and
// showing what they mean. This renders the subset that actually appears in
// replies: fenced code, headings, bullet and numbered lists, blockquotes, bold,
// italic, inline code, and links.
//
// **Nodes, never `innerHTML`.** Model output is untrusted (§3) and tool output
// quoted inside it doubly so. Every piece of text here becomes a text node, so
// a reply containing `<img onerror=...>` renders those characters rather than
// an element — there is no parse step for an attacker to reach. That is also
// why there is no markdown library: a dependency here would be a parser with
// `innerHTML` inside it, in the one tier the design calls untrusted, and the
// client is four files with no build step on purpose (N46).
//
// Links get `rel="noopener noreferrer"` and no `target`, so a reply cannot
// reach back into this page through `window.opener`.

// Inline spans: code first, because backticks suppress everything inside them.
function inlineMarkdown(text, parent) {
  // The emphasis rules carry a flanking condition, and it is not cosmetic.
  //
  // `return b * h → return 0.5 * b * h` has two multiplication signs, and a rule
  // that only asks for a `*` on each side read everything between them as
  // italic — deleting the markers and rendering `return b  b * h`. Text
  // disappeared from a reply, which is worse than showing the raw mark.
  //
  // So emphasis must open on a non-space and close on a non-space, as markdown
  // itself requires: `*word*` is emphasis, `a * b` is arithmetic. The same
  // applies to `**` and to the underscore forms. Code spans still come first,
  // because a backtick suppresses everything inside it.
  // Named groups, because the alternation is long enough that counting
  // positional ones is how a lookahead gets added and the wrong capture gets
  // read. The closing marker carries the "preceded by non-space" condition as a
  // lookbehind rather than inside the body, so the body stays a plain lazy run
  // and cannot match the empty string.
  const pattern = new RegExp(
    [
      "(?<ticks>`+)(?<code>[\\s\\S]*?)\\k<ticks>",
      "\\*\\*(?![\\s*])(?<b1>[\\s\\S]*?)(?<=[^\\s*])\\*\\*",
      "__(?![\\s_])(?<b2>[\\s\\S]*?)(?<=[^\\s_])__",
      "(?<![\\w*])\\*(?![\\s*])(?<i1>[^*\\n]*?)(?<=[^\\s*])\\*(?![\\w*])",
      "(?<![\\w_])_(?![\\s_])(?<i2>[^_\\n]*?)(?<=[^\\s_])_(?![\\w_])",
      "\\[(?<text>[^\\]]+)\\]\\((?<href>[^)\\s]+)\\)",
    ].join("|"),
    "g",
  );

  let last = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      parent.appendChild(document.createTextNode(text.slice(last, match.index)));
    }
    const g = match.groups;
    if (g.code !== undefined) {
      parent.appendChild(el("code", null, g.code));
    } else if (g.b1 !== undefined || g.b2 !== undefined) {
      parent.appendChild(el("strong", null, g.b1 ?? g.b2));
    } else if (g.i1 !== undefined || g.i2 !== undefined) {
      parent.appendChild(el("em", null, g.i1 ?? g.i2));
    } else if (g.text !== undefined) {
      const href = g.href;
      // Only schemes that cannot execute. `javascript:` in a model-written link
      // is the obvious attack and the one worth naming.
      const safe = /^(https?:|mailto:)/i.test(href);
      if (safe) {
        const a = el("a", null, g.text);
        a.href = href;
        a.rel = "noopener noreferrer";
        parent.appendChild(a);
      } else {
        parent.appendChild(document.createTextNode(match[0]));
      }
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parent.appendChild(document.createTextNode(text.slice(last)));
  }
}

// The fence languages this client's small highlighter actually knows. A fence
// that names one of these gets the four token classes; anything else — `diff`,
// `text`, a language nobody has written a regex for — stays plain, because a
// grammar this client does not have is better uncoloured than confidently
// wrong. The same judgement `CODE_ARGS` makes when it gives a tool no language.
const HIGHLIGHT_LANGS = new Set([
  "python", "py", "javascript", "js", "typescript", "ts", "rust", "rs",
  "shell", "bash", "sh", "zsh", "json", "yaml", "yml", "toml",
]);

// A markdown table's cells: the leading and trailing pipes are optional, `\|` is
// a literal pipe inside a cell, and every cell is trimmed. The header defines
// the table, so a short row is padded and a long one's excess is ignored — the
// same rule GFM gives, and the only one that reads the same at any width.
function tableCells(line) {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
  const cells = [];
  let cell = "";
  for (let k = 0; k < text.length; k++) {
    if (text[k] === "\\" && text[k + 1] === "|") {
      cell += "|";
      k++;
    } else if (text[k] === "|") {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += text[k];
    }
  }
  cells.push(cell.trim());
  return cells;
}

// The alignment a delimiter cell asks for, as a class rather than an inline
// style: the CSP permits no inline style attribute.
function tableAlign(cell) {
  const left = cell.startsWith(":");
  const right = cell.endsWith(":");
  return left && right ? "md-center" : right ? "md-right" : "";
}

// Block structure. Walks lines once; a fence swallows everything to its close.
function renderMarkdown(text, into) {
  // Re-rendering replaces every node. The container may be a scrollport of its
  // own — a thought streaming into a fixed stage — and clearing it would clamp
  // a reader who had scrolled up back to the top. The pixel offset is right to
  // restore because streaming only appends at the end; `growInner` decides
  // separately whether the tail should be followed instead.
  const keepTop = into.scrollTop;
  into.textContent = "";
  const lines = String(text ?? "").split("\n");
  let i = 0;
  let paragraph = null;

  const flush = () => {
    paragraph = null;
  };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code. An unterminated fence still renders as a block rather than
    // leaking backticks into the prose — a reply cut off mid-block is common.
    const fence = line.match(/^\s*(```+|~~~+)(.*)$/);
    if (fence) {
      flush();
      const close = fence[1][0].repeat(3);
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(close)) {
        body.push(lines[i]);
        i++;
      }
      i++; // the closing fence, or the end
      const pre = el("pre", "md-code");
      const info = fence[2].trim();
      if (info) pre.dataset.lang = info;
      const code = body.join("\n");
      const language = info.split(/\s+/)[0].toLowerCase();
      // Highlight only a language this four-class highlighter knows; see
      // `HIGHLIGHT_LANGS`. The newline is its own text node so a highlighted
      // line and the next stay separate lines under `white-space: pre`.
      if (HIGHLIGHT_LANGS.has(language)) {
        for (const [at, lineText] of code.split("\n").entries()) {
          if (at) pre.appendChild(document.createTextNode("\n"));
          highlight(lineText, pre);
        }
      } else {
        pre.appendChild(document.createTextNode(code));
      }
      into.appendChild(pre);
      continue;
    }

    // A table is a header row, a delimiter row, then body rows until a blank
    // line or a line with no pipe. The delimiter is what tells a table from a
    // paragraph that happens to contain a `|`; without it the lines fall
    // through to prose and keep their pipes, which is the failure this fixes.
    if (line.includes("|") && i + 1 < lines.length) {
      const header = tableCells(line);
      const marks = tableCells(lines[i + 1]);
      const ruled =
        marks.length === header.length &&
        marks.every((cell) => /^:?-+:?$/.test(cell)) &&
        lines[i + 1].includes("|");
      if (ruled) {
        flush();
        // The wrapper, not the table, is the sideways scrollport: the document
        // must not widen (N132), and inside a card the table scrolls itself.
        const wrap = el("div", "md-table-wrap");
        const table = el("table", "md-table");
        const head = el("thead");
        const headRow = el("tr");
        for (const [k, cell] of header.entries()) {
          const th = el("th", tableAlign(marks[k]));
          th.scope = "col";
          inlineMarkdown(cell, th);
          headRow.appendChild(th);
        }
        head.appendChild(headRow);
        table.appendChild(head);
        i += 2;
        const bodyRows = el("tbody");
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          const cells = tableCells(lines[i]);
          const row = el("tr");
          for (let k = 0; k < header.length; k++) {
            const td = el("td", tableAlign(marks[k]));
            inlineMarkdown(cells[k] || "", td);
            row.appendChild(td);
          }
          bodyRows.appendChild(row);
          i++;
        }
        table.appendChild(bodyRows);
        wrap.appendChild(table);
        into.appendChild(wrap);
        continue;
      }
    }

    if (!line.trim()) {
      flush();
      i++;
      continue;
    }

    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (heading) {
      flush();
      const h = el(`h${Math.min(heading[1].length + 2, 6)}`, "md-h");
      inlineMarkdown(heading[2], h);
      into.appendChild(h);
      i++;
      continue;
    }

    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flush();
      const block = el("blockquote", "md-quote");
      const parts = [quote[1]];
      i++;
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        parts.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      inlineMarkdown(parts.join("\n"), block);
      into.appendChild(block);
      continue;
    }

    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flush();
      const ordered = Boolean(numbered);
      const list = el(ordered ? "ol" : "ul", "md-list");
      if (ordered) list.start = Number(numbered[1]);
      while (i < lines.length) {
        const b = lines[i].match(/^\s*[-*+]\s+(.*)$/);
        const n = lines[i].match(/^\s*\d+[.)]\s+(.*)$/);
        if (ordered ? !n : !b) break;
        const item = el("li");
        inlineMarkdown((ordered ? n[1] : b[1]), item);
        list.appendChild(item);
        i++;
      }
      into.appendChild(list);
      continue;
    }

    // Prose. Consecutive lines join into one paragraph, as markdown means.
    if (!paragraph) {
      paragraph = el("p", "md-p");
      into.appendChild(paragraph);
    } else {
      paragraph.appendChild(document.createTextNode("\n"));
    }
    inlineMarkdown(line, paragraph);
    i++;
  }
  into.scrollTop = keepTop;
}

// The envelope the model sees, removed from what a person sees.
//
// `wrap_untrusted` fences tool output so an injected "SYSTEM:" line cannot be
// read as the harness talking (D23). That fence is addressed to the model and
// means nothing to a reader — it is a delimiter, not content. Stripping it here
// is safe because the server already removes any occurrence from the output
// itself before wrapping, so output cannot forge a marker to hide behind.
const UNTRUSTED_OPEN = "<<<UNTRUSTED_TOOL_OUTPUT>>>";
const UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_TOOL_OUTPUT>>>";

function unwrapUntrusted(text) {
  if (typeof text !== "string") return "";
  let body = text;
  const open = body.indexOf(UNTRUSTED_OPEN);
  if (open !== -1) body = body.slice(open + UNTRUSTED_OPEN.length);
  const close = body.lastIndexOf(UNTRUSTED_CLOSE);
  if (close !== -1) body = body.slice(0, close);
  // Only the newlines the envelope itself added.
  return body.replace(/^\n/, "").replace(/\n$/, "");
}

// What a call is *about*, in one line, instead of a JSON object to decode.
//
// Every tool here takes a path, a command, or a query — so the card can say
// "shapes.py" where it used to print `{ "path": "/home/..." }`. The full
// arguments stay available below for anything this cannot summarise, because a
// summary that quietly drops a parameter above an Approve button would be worse
// than the JSON.
function callSummary(name, args) {
  if (!args || typeof args !== "object") return "";
  const path = args.path || args.src || args.file;
  if (typeof path === "string" && path) {
    const tail = path.split("/").filter(Boolean).pop() || path;
    const extra = [];
    if (args.dst) extra.push(`→ ${String(args.dst).split("/").filter(Boolean).pop()}`);
    if (typeof args.offset === "number") extra.push(`from line ${args.offset}`);
    if (Array.isArray(args.edits)) {
      extra.push(`${args.edits.length} edit${args.edits.length === 1 ? "" : "s"}`);
    }
    return extra.length ? `${tail} · ${extra.join(" · ")}` : tail;
  }
  if (typeof args.command === "string") return args.command;
  if (typeof args.code === "string") return args.code.split("\n")[0].slice(0, 80);
  if (typeof args.query === "string") return args.query;
  if (typeof args.pattern === "string") return args.pattern;
  if (typeof args.url === "string") return args.url;
  return "";
}

// Which argument of a call *is* the code, and what language it is in.
//
// A call that carries a program should show the program. `python` with a
// twelve-line script summarised to its first line and a JSON blob underneath
// made a reader reconstruct the source from an escaped string — `\n` and `\"`
// and all — to answer "what is it about to run", which is the one question the
// card exists to answer.
//
// Keyed off the tool name rather than sniffed from the arguments, because the
// language decides the colouring and only the tool knows it. `null` means
// nothing is highlighted and the text is shown plain: a write's content has no
// language until its extension says one, and a command run through the Python
// keyword list lights up `in`, `is` and `not` as keywords, which is confident
// and wrong. `shell` is not here at all — it draws its own terminal view, in
// which the command is the prompt line rather than a file.
const CODE_ARGS = {
  python: { key: "code", language: "python" },
  blender: { key: "code", language: "python" },
  // A write's `content` is the file it would become, so it belongs in the file
  // view for the same reason a program does. Language is left unpicked: the
  // extension decides it, and guessing from the tool name would light up
  // arbitrary text with a grammar it is not.
  fs_write: { key: "content", language: null },
};

// A one-line command is not a listing. `ls -la /tmp` in a numbered, scrolling
// box is a gutter and a border around nine characters — the summary line it
// already had reads better, so only something with real structure gets the
// file treatment. The same judgement `renderResult` makes about output.
function codeArgument(name, args) {
  const spec = CODE_ARGS[name];
  if (!spec || !args || typeof args !== "object") return null;
  const text = args[spec.key];
  if (typeof text !== "string" || !text.trim()) return null;
  if (text.trimEnd().split("\n").length < 2) return null;
  return { ...spec, text };
}

// Syntax highlighting, deliberately small.
//
// Four token classes — comment, string, number, keyword — matched in one pass so
// a keyword inside a string is not re-matched. This is decoration for reading a
// file in a chat window, not a parser: it handles the common shapes of the
// languages this appliance is used on and leaves anything else uncoloured, which
// is the right failure. A real grammar is a dependency, and the client is three
// files with no build step on purpose (N46).
const KEYWORDS = new RegExp(
  // `\b` is the wrong boundary here. In `__main__` the character before `main`
  // is `_`, which is a word character — so `\bmain\b` finds no boundary there,
  // but `\b` *is* satisfied at other dunder edges and the middle of the
  // identifier lit up. Requiring a non-word character on each side instead
  // keeps a keyword from matching inside a longer name.
  "(?<![\\w$])(?:" +
    "def|class|return|import|from|if|elif|else|for|while|try|except|finally|with|as|" +
    "pass|raise|yield|lambda|async|await|in|not|and|or|is|None|True|False|self|" +
    "const|let|var|function|new|typeof|instanceof|null|undefined|true|false|this|" +
    "fn|let|mut|pub|impl|struct|enum|match|use|mod|crate|Some|Ok|Err" +
    ")(?![\\w$])",
  "g",
);

function highlight(line, node) {
  // One regex, alternating over the classes, so the first match wins and a
  // keyword inside a comment or string stays inside it.
  const pattern = new RegExp(
    [
      "(#[^\\n]*|//[^\\n]*)",
      // Triple quotes before single ones, or `"""a docstring"""` matches as an
      // empty string followed by uncoloured text — which is exactly how every
      // Python module's first line rendered.
      "(\"\"\"[\\s\\S]*?\"\"\"|'''[\\s\\S]*?'''" +
        "|\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*')",
      "(\\b\\d+(?:\\.\\d+)?\\b)",
      KEYWORDS.source,
    ].join("|"),
    "g",
  );

  let last = 0;
  let match;
  while ((match = pattern.exec(line)) !== null) {
    if (match.index > last) {
      node.appendChild(document.createTextNode(line.slice(last, match.index)));
    }
    const cls = match[1] ? "c" : match[2] ? "s" : match[3] ? "n" : "k";
    node.appendChild(el("span", cls, match[0]));
    last = match.index + match[0].length;
  }
  if (last < line.length) node.appendChild(document.createTextNode(line.slice(last)));
}

// How many lines get the numbered treatment before the rest becomes plain text.
//
// `python` and `shell` may each return a megabyte, and a megabyte of short
// lines is ~52,000 rows at three elements each — about 157,000 nodes, every
// one of them a flex container the browser must style and lay out. The card's
// body is about 380px tall, so roughly forty of those rows are ever on screen:
// the other fifty-two thousand are laid out to be scrolled past.
//
// Past this bound the remainder is appended as one `pre` instead. Nothing is
// hidden — the text is all there, selectable and searchable — it simply stops
// paying for a gutter nobody reads that far down.
const FILE_VIEW_MAX_LINES = 2000;

// A file, numbered and coloured, in the card's scrollport.
//
// Numbers so "the bug is on line 11" is checkable, and small and dim so they
// stay a gutter rather than competing with the code.
function fileView(text, { highlightCode = true } = {}) {
  const wrap = el("div", "file");
  const lines = text.split("\n");
  // Trailing newline produces an empty final line that is an artefact of the
  // split, not a line of the file.
  if (lines.length && lines[lines.length - 1] === "") lines.pop();

  const numbered = Math.min(lines.length, FILE_VIEW_MAX_LINES);
  const width = String(lines.length).length;
  for (let i = 0; i < numbered; i++) {
    const row = el("div", "fl");
    const num = el("span", "ln", String(i + 1).padStart(width, " "));
    row.appendChild(num);
    const code = el("span", "tx");
    if (highlightCode) highlight(lines[i], code);
    else code.textContent = lines[i];
    row.appendChild(code);
    wrap.appendChild(row);
  }

  // The tail, as text. Said out loud first, because a reader who scrolls this
  // far needs to know the numbering stopped rather than that the file did.
  if (lines.length > numbered) {
    const rest = lines.length - numbered;
    wrap.appendChild(
      el("div", "file-more", `… ${rest.toLocaleString()} more line${rest === 1 ? "" : "s"}, unnumbered:`),
    );
    wrap.appendChild(el("pre", "file-tail", lines.slice(numbered).join("\n")));
  }
  return wrap;
}

// A write, drawn as a diff rather than as the arguments that asked for it.
//
// The arguments are what the *model* said; a diff is what would happen to the
// file. Above an Approve button only the second is worth reading — "replace this
// 400-character blob with that one" is not a thing anyone can check, and this
// control is what authorises the write, so it has to be legible.
//
// `rows` is computed server-side (`edit_plan.number_diff`) because a unified
// patch carries its line numbers only in hunk headers, and every client would
// otherwise reimplement the same two-counter walk. The raw `diff` travels beside
// it for anything that wants a patch.
function diffView(preview) {
  const wrap = el("div", "diff");

  const head = el("div", "diff-head");
  head.appendChild(el("span", "diff-path", preview.path));
  const delta = preview.new_bytes - preview.old_bytes;
  const sign = delta >= 0 ? "+" : "";
  head.appendChild(
    el(
      "span",
      "diff-meta",
      preview.created
        ? `new file · ${preview.new_bytes} bytes`
        : `${preview.new_bytes} bytes · ${sign}${delta}`,
    ),
  );
  // Nothing below the header for a file that does not exist yet: a diff against
  // an empty string is the whole file marked `+`, which is its contents rather
  // than a change, and the byte count in the header already says how much.
  wrap.appendChild(head);

  // A created file has no diff to show — `unified_diff` against an empty string
  // would be the whole file marked `+`, which is the content, not a change.
  if (preview.created) {
    wrap.appendChild(
      el("div", "diff-note", "New file — nothing to compare against."),
    );
    return wrap;
  }

  const rows = Array.isArray(preview.rows) ? preview.rows : [];
  if (!rows.length) {
    // No rows but a diff string: render the patch as-is rather than nothing.
    wrap.appendChild(el("pre", "diff-raw", preview.diff || ""));
    return wrap;
  }

  const table = el("div", "diff-body");
  let first = true;
  for (const row of rows) {
    // `@@ -8,11 +8,11 @@` is machine syntax: the line numbers it encodes are
    // already in the gutter, so for a reader it is noise that breaks the eye's
    // scan down the column. Between hunks it still has a job — saying "there is
    // a gap here" — so it becomes a rule rather than a line of text.
    if (row.kind === "hunk") {
      if (!first) table.appendChild(el("div", "dl gap"));
      first = false;
      continue;
    }
    first = false;
    const line = el("div", `dl ${row.kind}`);
    line.appendChild(el("span", "ln", row.old == null ? "" : String(row.old)));
    line.appendChild(el("span", "ln", row.new == null ? "" : String(row.new)));
    const mark = { add: "+", remove: "-", hunk: "@", context: " " }[row.kind] || " ";
    line.appendChild(el("span", "mk", mark));
    // textContent, never innerHTML: a diff is file contents, which is untrusted
    // data in the same sense tool output is (§3).
    line.appendChild(el("span", "tx", row.text));
    table.appendChild(line);
  }
  wrap.appendChild(table);
  return wrap;
}

// -- per-tool presentation --------------------------------------------------
//
// One line a reader scans, and one shape per output. The generic treatment — a
// JSON blob of the arguments above a dump of the output — is honest and
// unreadable: a person parses `{"path": "/srv/..."}` to learn a filename, and
// counts a directory by eye. Each tool knows what it produces, so each tool
// says so. A tool with no entry still works, which is the point of
// `tools/README`: adding an executable must not require touching the client.

function firstLine(text) {
  return String(text || "").split("\n")[0].slice(0, 100);
}

// The host and path, no scheme. The host alone rendered every arXiv paper as
// `arxiv.org`, so two fetches of the same site were indistinguishable; the path
// is what tells them apart.
function webTarget(url) {
  try {
    const u = new URL(url);
    return (u.host + u.pathname).replace(/\/$/, "");
  } catch {
    return String(url || "");
  }
}

function humanBytes(text) {
  // `Blob` measures UTF-8 the way the tool does; `String.length` counts UTF-16
  // units, which is the wrong number for anything non-ASCII.
  const n = new Blob([String(text ?? "")]).size;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// What each tool is called, and what one line says it is about. `subject` is
// the key information — the file, the query, the command — not the audit name.
// A `verb` may be a function when one tool is several acts: `browse` searches,
// opens and reads, and with the audit name gone from the row the verb is the
// only thing that can say which.
const BROWSE_VERB = {
  search: "Search",
  open: "Open",
  read: "Read",
  shot: "Screenshot",
  eval: "Eval",
};

const BLENDER_VERB = {
  exec: "Blender",
  new: "New scene",
  open: "Open in Blender",
  save: "Save",
  export: "Export",
  render: "Render",
};

const TOOL_VIEW = {
  fs_read: {
    verb: "Read",
    subject: (a) => {
      const base = scratchOrigin(a.path) || subjectName(a.path);
      return base + (a.offset ? ` · from line ${a.offset}` : "");
    },
  },
  fs_list: { verb: "List", subject: (a) => a.path || "" },
  fs_search: {
    verb: "Search",
    subject: (a) => {
      const where = scratchOrigin(a.path) || subjectName(a.path);
      return `“${a.pattern}”` + (a.path ? ` in ${where}` : "");
    },
  },
  fs_image: { verb: "Look at", subject: (a) => subjectName(a.path) },
  fs_write: {
    verb: "Write",
    subject: (a) =>
      subjectName(a.path) +
      (typeof a.content === "string" ? ` · ${humanBytes(a.content)}` : ""),
  },
  fs_edit: {
    verb: "Edit",
    subject: (a) =>
      subjectName(a.path) +
      (Array.isArray(a.edits)
        ? ` · ${a.edits.length} edit${a.edits.length === 1 ? "" : "s"}`
        : ""),
  },
  shell: { verb: "Run", subject: (a) => firstLine(a.command) },
  python: {
    verb: "Python",
    subject: (a) => [a.action, a.code && firstLine(a.code)].filter(Boolean).join(" · "),
  },
  web_fetch: {
    verb: "Fetch",
    subject: (a) => webTarget(a.url),
    href: (a) => a.url,
  },
  browse: {
    verb: (a) => BROWSE_VERB[a.action] || "Browse",
    subject: (a) => {
      if (a.query) return `“${a.query}”`;
      if (a.url) return webTarget(a.url);
      // `read`, `shot` and `eval` may act on the tab Neuman already has open,
      // in which case there is no URL to name — but the act is still one line.
      return a.action === "open" ? "" : "the open page";
    },
    href: (a) => a.url,
  },
  trash: { verb: "Trash", subject: (a) => subjectName(a.path) },
  move: { verb: "Move", subject: (a) => `${subjectName(a.src)} → ${subjectName(a.dst)}` },
  blender: {
    verb: (a) => BLENDER_VERB[a.action] || "Blender",
    subject: (a) => (a.path ? subjectName(a.path) : a.code ? firstLine(a.code) : ""),
  },
  blender_scene: { verb: "Describe", subject: (a) => a.object || "the scene" },
  blender_view: {
    verb: "Look at",
    subject: (a) =>
      [a.object || "the scene", a.view || "iso", a.shading].filter(Boolean).join(" · "),
  },
  session_query: {
    verb: "Query",
    subject: (a) => [a.action, a.session_id].filter(Boolean).join(" · "),
  },
};

function toolVerb(name, args) {
  const view = TOOL_VIEW[name];
  if (!view) return name;
  return typeof view.verb === "function" ? view.verb(args || {}) : view.verb;
}

// The URL a call is about, when it has one, so the card's head and its `url`
// field can be links. Only `http`/`https` — anything else is not a page a
// browser should be pointed at.
function toolHref(name, args) {
  const view = TOOL_VIEW[name];
  const url = view && view.href ? view.href(args || {}) : "";
  return /^https?:\/\//.test(url) ? url : "";
}

function toolSubject(name, args) {
  const view = TOOL_VIEW[name];
  const subject = view && view.subject ? view.subject(args || {}) : "";
  // `callSummary` is the generic fallback for a tool with no entry, and the
  // safety net for one whose entry produces nothing (an argument we cannot
  // name): a summary that quietly became blank would be worse than the JSON.
  return subject || callSummary(name, args);
}

// The arguments that are not already the code or the subject, as key/value.
// Seeing `path /srv/eng/shapes.py` is faster than parsing a JSON object, and an
// argument this does not know how to name still shows its own key rather than
// disappearing.
function argumentFields(name, args) {
  const skip = new Set(["preview"]);
  const code = CODE_ARGS[name];
  if (code) skip.add(code.key);
  // A URL the card already shows as its subject link (`web_fetch`, `browse`) is
  // not repeated in the fields; the raw string was the one line that read as
  // machine output under a link saying the same thing.
  if (toolHref(name, args)) skip.add("url");
  const rows = [];
  for (const [key, value] of Object.entries(args || {})) {
    if (skip.has(key) || value === undefined || value === null || value === "") continue;
    let shown;
    if (Array.isArray(value)) shown = `${value.length} item${value.length === 1 ? "" : "s"}`;
    else if (typeof value === "object") shown = JSON.stringify(value);
    else shown = String(value);
    rows.push([key, shown]);
  }
  return rows;
}

function fieldList(rows) {
  const dl = el("dl", "fields");
  for (const [key, value] of rows) {
    dl.appendChild(el("dt", null, key));
    const dd = el("dd");
    // A URL argument is a link, so the page a call fetched is one click away
    // without copying it out of a monospace blob. Anything else stays text.
    if (/^https?:\/\//.test(value)) dd.appendChild(externalLink(null, value, value));
    else dd.textContent = value;
    dl.appendChild(dd);
  }
  return dl;
}

// The directory a shell command starts in: the call's own `cwd` when it named
// one, otherwise the session's working directory, which is where the dispatch
// starts. A card rebuilt from a result with no proposal has the second only.
function shellDirectory(args) {
  if (typeof args.cwd === "string" && args.cwd) return args.cwd;
  const view = renderView || currentView;
  return (view && view.workingDir) || "";
}

// The shell, drawn as a shell.
//
// A `shell` call used to show nothing at all until its result arrived: the
// command is the call's code, so the argument fields dropped it, and a one-line
// command was not a file either, leaving the card empty between its head and its
// output. The card now draws the call as the terminal it is — the prompt it runs
// at, the command on its own line (with a `>` continuation for each further
// one), and a block caret where the output will land. The prompt names the
// account, the directory the dispatch starts in, and `neuman` where a hostname
// belongs: the browser cannot know the machine's own hostname, and the appliance
// has a name it can say instead.
function shellInput(args) {
  const command = typeof args.command === "string" ? args.command.replace(/\r\n?/g, "\n") : "";
  if (!command.trim()) return null;
  const box = el("div", "shell");

  const prompt = el("span", "ps1");
  prompt.appendChild(el("span", "ps1-user", accountName || "you"));
  prompt.appendChild(el("span", "ps1-at", "@"));
  prompt.appendChild(el("span", "ps1-host", "neuman"));
  const cwd = shellDirectory(args);
  if (cwd) {
    prompt.appendChild(el("span", "ps1-sep", ":"));
    prompt.appendChild(el("span", "ps1-path", shortPath(cwd, 40)));
  }
  prompt.appendChild(el("span", "ps1-mark", "$"));

  const lines = command.replace(/\n+$/, "").split("\n");
  for (const [index, line] of lines.entries()) {
    const row = el("div", "shell-line");
    row.appendChild(index === 0 ? prompt : el("span", "ps1-cont", ">"));
    row.appendChild(el("span", "shell-cmd", line));
    box.appendChild(row);
  }

  // The wait, as a terminal draws it: a block caret blinking on the line where
  // the output will land. It is decoration for the eye — the head's state word
  // says `running` in words — and `renderResult` removes it with the first
  // result, so it can never outlive the wait it describes.
  const pending = el("div", "shell-pending");
  pending.title = "output will appear here";
  pending.setAttribute("aria-hidden", "true");
  pending.appendChild(el("span", "shell-caret"));
  box.appendChild(pending);
  return box;
}

// The input half of a card: the code if the tool runs one, and the named
// arguments. There used to be a `raw arguments` disclosure holding the exact
// JSON, which for every tool only restated what the fields and the code already
// showed — a second copy of the same facts, collapsed by default and opened by
// nobody. The one thing it could show and the fields could not, an array's
// contents, is either the code (a write's `content`) or arrives as a diff when
// the call is approved, so nothing approval needs is lost.
function toolInput(name, args) {
  const box = el("div", "args");
  // A shell call is its terminal, not its arguments: the command is the code
  // and the prompt says where it runs. `shellInput` is null when the command is
  // missing — a card rebuilt from a result with no proposal — and the generic
  // fields are the right fallback there.
  if (name === "shell") {
    const terminal = shellInput(args || {});
    if (terminal) {
      box.classList.add("shell-args");
      box.appendChild(terminal);
      return box;
    }
  }
  const code = codeArgument(name, args);
  if (code) box.appendChild(fileView(code.text, { highlightCode: code.language !== null }));
  const rows = argumentFields(name, args);
  if (rows.length) box.appendChild(fieldList(rows));
  return box;
}

const WRITE_TOOLS = new Set(["fs_write", "fs_edit"]);

function toolImageUrl(callId) {
  // The id comes from the log being drawn into rather than the session on
  // screen: a background view's image is fetched when its card is built, and
  // that card may belong to a session the reader is not looking at yet.
  const id = log && log.dataset ? log.dataset.session : "";
  if (!id) return "";
  return `/api/branches/${encodeURIComponent(id)}/tools/${encodeURIComponent(callId)}/image`;
}

// -- output shapes ----------------------------------------------------------
//
// Keyed by name; a renderer returns true when it drew something and false to
// fall through to the generic text view. Falling through is what keeps an
// unexpected output — a summarised listing, a call that failed — readable
// rather than blank.

function imageOutput(into, result, body) {
  const url = result.has_image && toolImageUrl(result.tool_call_id);
  if (!url) return false;
  // The card is one fixed box (D62, D64) and the picture is the result, so it
  // gets the height the arguments and the caption leave rather than a pixel
  // cap: the whole image is visible without scrolling the card. The price is
  // scale — a large picture is drawn smaller than its pixels.
  const scroll = into.closest(".tool-body");
  if (scroll) scroll.classList.add("shot-body");
  const frame = el("div", "shot-frame");
  const img = el("img", "shot");
  img.alt = `image returned by ${result.name}`;
  // Lazy: a transcript can hold many screenshots and only the visible one need
  // hit the route.
  img.loading = "lazy";
  img.src = url;
  // The bytes are fetched on demand and can age out of the log (a purge), so a
  // failed load says so rather than showing a broken-image glyph. The caption
  // below is the tool's own line and stays either way, so the failure note must
  // not repeat it. The frame goes too: an empty box where the picture would be
  // is not something the note needs around it.
  img.onerror = () => frame.replaceWith(el("div", "muted", "(image unavailable)"));
  frame.appendChild(img);
  into.appendChild(frame);
  if (body) into.appendChild(el("div", "muted", body));
  return true;
}

function listOutput(into, result, body) {
  if (!body) return false;
  const entries = [];
  const notes = [];
  for (const line of body.split("\n")) {
    if (!line) continue;
    if (line.startsWith("(")) {
      notes.push(line);
      continue;
    }
    const parts = line.split("\t");
    const kind = parts.shift();
    // Not the shape `fs_list` emits (a plain listing from some other tool): hand
    // it back to the text view rather than mislabeling every line as a file.
    if (kind !== "d" && kind !== "f") return false;
    entries.push({ dir: kind === "d", name: parts.join(" ") });
  }
  if (!entries.length && !notes.length) return false;
  const list = el("ul", "listing");
  for (const entry of entries) {
    const li = el("li", entry.dir ? "dir" : "file");
    li.appendChild(el("span", "kind", entry.dir ? "dir" : "file"));
    li.appendChild(el("span", "entry", entry.name));
    list.appendChild(li);
  }
  into.appendChild(list);
  for (const line of notes) into.appendChild(el("div", "muted", line));
  return true;
}

function searchOutput(into, result, body) {
  if (!body) return false;
  const groups = new Map();
  const notes = [];
  for (const line of body.split("\n")) {
    if (!line) continue;
    if (line.startsWith("(")) {
      notes.push(line);
      continue;
    }
    const match = line.match(/^(.*?):(\d+):(.*)$/);
    const path = match ? match[1] : line;
    if (!groups.has(path)) groups.set(path, []);
    groups.get(path).push(
      match ? { line: Number(match[2]), text: match[3] } : { line: null, text: "" },
    );
  }
  if (!groups.size && !notes.length) return false;
  const wrap = el("div", "results");
  for (const [path, items] of groups) {
    const group = el("div", "result-group");
    group.appendChild(el("div", "result-path", path));
    for (const item of items) {
      const row = el("div", "result-row");
      if (item.line !== null) row.appendChild(el("span", "result-line", String(item.line)));
      row.appendChild(el("span", "result-text", item.text));
      group.appendChild(row);
    }
    wrap.appendChild(group);
  }
  into.appendChild(wrap);
  for (const line of notes) into.appendChild(el("div", "muted", line));
  return true;
}

// A `browse search` result is a list of hits, not a page of text: a title, an
// indented URL and a snippet, blocks separated by blank lines. Parsed as the
// shape rather than keyed on the action, so `open`, `read` and `eval` fall
// through to the text view — and the discriminator is the *indent* on the URL
// line, because `open` and `read` also begin with a title and a URL and only the
// search action indents it.
function searchHits(body) {
  const text = String(body || "").trim();
  if (!text) return null;
  const hits = [];
  for (const block of text.split(/\n{2,}/)) {
    const lines = block.split("\n");
    const at = lines.findIndex((line) => /^\s+https?:\/\//.test(line));
    if (at < 0) return null;
    const title = lines.slice(0, at).join(" ").trim();
    if (!title) return null;
    hits.push({
      title,
      url: lines[at].trim(),
      snippet: lines.slice(at + 1).join(" ").trim(),
    });
  }
  return hits.length ? hits : null;
}

// One numbered hit per block, the title and the URL both real links. Not the
// file view: that numbered every line and set a list of sites in monospace.
function browseOutput(into, result, body) {
  const hits = searchHits(body);
  if (hits) {
    const list = el("ol", "hits");
    for (const [index, hit] of hits.entries()) {
      const li = el("li", "hit");
      li.appendChild(el("span", "n", `${index + 1}.`));
      const wrap = el("div", "hit-body");
      // Both the title and the URL are the link, so a reader can click whichever
      // they reach for. A new tab keeps the conversation on screen — this page is
      // very likely the one they are reading Neuman in.
      for (const [cls, label] of [["hit-title", hit.title], ["hit-url", hit.url]]) {
        wrap.appendChild(externalLink(cls, label, hit.url));
      }
      if (hit.snippet) wrap.appendChild(el("div", "hit-snippet", hit.snippet));
      li.appendChild(wrap);
      list.appendChild(li);
    }
    into.appendChild(list);
    return true;
  }
  const page = readPage(body);
  if (page) {
    readOutput(into, page);
    return true;
  }
  return false; // another browse action: let the text view draw it
}

// A `browse read` result: `_read` prints a `# title` line, the URL, then the
// extracted page text. Parsed by that shape — the `# ` heading is what the
// `open` action's bare `title\nurl` does not have — and rendered as a page
// rather than the file view, which numbered every line and set a web page in
// monospace. Not the site's own HTML: that would mean an iframe and a
// sanitiser, and this is the text the tool already extracted.
function readPage(body) {
  const lines = String(body || "").split("\n");
  if (!/^#\s+/.test(lines[0] || "")) return null;
  const url = (lines[1] || "").trim();
  if (!/^https?:\/\//.test(url)) return null;
  return {
    title: lines[0].replace(/^#\s+/, "").trim(),
    url,
    text: lines.slice(2).join("\n").trim(),
  };
}

function readOutput(into, page) {
  const head = el("div", "read-head");
  for (const [cls, label] of [["read-title", page.title || page.url], ["read-url", page.url]]) {
    head.appendChild(externalLink(cls, label, page.url));
  }
  into.appendChild(head);

  // One paragraph per blank-line-separated block, kept as `pre-wrap` so the
  // single newlines `innerText` leaves between list items and inside a block
  // survive rather than collapsing into one run-on paragraph.
  const article = el("div", "read-body");
  for (const block of page.text.split(/\n{2,}/)) {
    const text = block.trim();
    if (text) article.appendChild(el("p", null, text));
  }
  into.appendChild(article);
}

// A `web_fetch` result that is HTML: draw the page it stands for, not the
// markup. The response is untrusted (§3, D23), so nothing parsed is attached to
// the live document: `DOMParser` builds an inert tree (no scripts run, no
// resources load), the noise elements are skipped, and only text is read out and
// re-emitted as our own nodes. No URL from the page becomes a link — the only
// links are the call's own URL. This is the one parse in a client whose rule is
// "nodes, never `innerHTML`", and it is safe for the same reason (D45).
//
// Bounded, because a transcript keeps every card and a page can be a megabyte:
// the text stops at a block and character budget, and the full response stays one
// click away under `raw HTML`, so the budget is a view rather than a loss.
const PAGE_BLOCKS = 240;
const PAGE_CHARS = 20000;

// The tags whose text is never the page a reader wants.
const HTML_SKIP = new Set([
  "SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "IFRAME", "CANVAS",
  "FORM", "INPUT", "BUTTON", "SELECT", "TEXTAREA", "NAV", "HEADER", "FOOTER",
  "ASIDE",
]);
// What counts as its own line of text.
const HTML_BLOCK = new Set([
  "P", "DIV", "SECTION", "ARTICLE", "MAIN", "UL", "OL", "LI", "H1", "H2", "H3",
  "H4", "H5", "H6", "PRE", "BLOCKQUOTE", "TABLE", "TR", "TD", "TH", "DT", "DD",
  "FIGCAPTION", "FIGURE", "ADDRESS",
]);

function looksLikeHtml(text) {
  return /<!doctype html|<html[\s>]|<head[\s>]|<body[\s>]/i.test(
    String(text || "").slice(0, 2048),
  );
}

// The readable blocks of a parsed page, one per leaf block element. A block that
// contains other blocks is descended into instead of emitted, which is what
// keeps a wrapper `<div>` from repeating everything its children say; a block
// with only inline content becomes one line.
function pageBlocks(root, limit) {
  const blocks = [];
  const walk = (node) => {
    for (const child of node.children) {
      if (blocks.length >= limit) return;
      if (HTML_SKIP.has(child.tagName) || child.hasAttribute("hidden")) continue;
      if (!HTML_BLOCK.has(child.tagName)) {
        walk(child);
        continue;
      }
      if ([...child.children].some((c) => HTML_BLOCK.has(c.tagName))) {
        walk(child);
        continue;
      }
      const text = (
        child.tagName === "PRE" ? child.textContent : child.textContent.replace(/\s+/g, " ")
      ).trim();
      if (text) blocks.push({ tag: child.tagName, text });
    }
  };
  walk(root);
  return blocks;
}

function blockClass(tag) {
  if (/^H[1-6]$/.test(tag)) return "read-h";
  if (tag === "LI") return "read-li";
  if (tag === "PRE") return "read-pre";
  return "";
}

function webFetchOutput(into, result, body) {
  if (!looksLikeHtml(body)) return false;
  const doc = new DOMParser().parseFromString(body, "text/html");
  const card = into.closest(".tool");
  const url = (card && card._url) || "";
  const title = (doc.title || "").trim();

  // The same head `browse read` uses, so a fetched page and a read page look
  // like the same kind of object.
  const head = el("div", "read-head");
  const label = title || url || "fetched page";
  if (url) {
    head.appendChild(externalLink("read-title", label, url));
    head.appendChild(externalLink("read-url", url, url));
  } else {
    head.appendChild(el("span", "read-title", label));
  }
  into.appendChild(head);

  // `limit + 1` so a full batch means there was more, without walking the rest.
  const blocks = pageBlocks(doc.body, PAGE_BLOCKS + 1);
  const article = el("div", "read-body");
  let used = 0;
  let truncated = blocks.length > PAGE_BLOCKS;
  for (const block of blocks.slice(0, PAGE_BLOCKS)) {
    if (used >= PAGE_CHARS) {
      truncated = true;
      break;
    }
    used += block.text.length;
    article.appendChild(el("p", blockClass(block.tag), block.text));
  }
  if (!article.children.length) article.appendChild(el("p", null, "(no readable text)"));
  into.appendChild(article);
  if (truncated) {
    into.appendChild(el("div", "muted", "page text truncated here; the full response is below"));
  }

  // The whole response, one click away and one text node rather than one per
  // line: nothing is discarded, and nothing pays for a page nobody opened.
  const raw = el("details", "raw");
  raw.appendChild(el("summary", null, "raw HTML"));
  raw.appendChild(el("pre", null, body));
  into.appendChild(raw);
  return true;
}

// A shell's result is the terminal's output, not a file: no line gutter, no
// numbering, and wrapping at the card's edge the way a terminal wraps at its
// window's. The `shell-output` class is what keeps it on the same inset surface
// as the prompt above it, with no rule between the command and what it printed.
function shellOutput(into, result, body) {
  if (!body) {
    into.appendChild(el("div", "muted", "(no output)"));
    return true;
  }
  into.appendChild(el("pre", "shell-out", body));
  return true;
}

const TOOL_OUTPUT = {
  fs_list: listOutput,
  fs_search: searchOutput,
  browse: browseOutput,
  web_fetch: webFetchOutput,
  shell: shellOutput,
};

// A write's diff replaces its arguments, the way it does when the approval frame
// arrives — one diff, in the one place the card has always shown it. Under
// `yolo` there is no approval frame, so nothing replaced the arguments, and the
// result is where the `structured` half arrives; doing it here as well is what
// shows the change instead of a one-line summary above a JSON blob. Skipped
// when `.args` is already gone, so an approved (or replayed) write does not end
// up with the same diff twice.
function writeOutput(card, result) {
  const structured = result.structured;
  if (!structured || !(structured.rows || structured.diff)) return;
  const args = card.querySelector(".args");
  if (args) args.replaceWith(diffView(structured));
}

// A finished tool result, drawn into its card.
//
// Shared by the live `tool_result` frame and by `replay`, because they are the
// same thing arriving at different times — and when they were two functions,
// reopening a session silently reverted every improvement the live path had
// grown. A reader should not be able to tell whether a card was drawn as it
// happened or rebuilt from the log.
function renderResult(card, result) {
  const into = card.querySelector(".tool-output") || card;
  const body = unwrapUntrusted(result.content);
  // Every shell result — output, empty or failed — lands on the terminal's own
  // surface, with no rule between the command and what it printed.
  if (result.name === "shell") into.classList.add("shell-output");
  // The terminal's caret is the wait, not the result: the first thing that
  // comes back replaces it, whatever the command's exit code was.
  const pending = card.querySelector(".shell-pending");
  if (pending) pending.remove();
  // A persisted result names the scratch file it wrote; record which call that
  // was, so a later `fs_read` of the path can say what produced it.
  rememberScratch(result, card);
  const failed =
    result.exit_code !== undefined && result.exit_code !== null && result.exit_code !== 0;
  // The head's state, when it says `failed`, is coloured like the body below it:
  // the word is the glance and the red is the confirmation. A success clears it,
  // so the colour and the word cannot disagree.
  const state = card.querySelector(".state");
  if (state) state.classList.toggle("bad", failed);
  // A per-tool shape, but never for a failure: an error is a message to read,
  // not a listing to browse, and running its text through a parser would drop
  // the part that explains what went wrong.
  if (!failed && WRITE_TOOLS.has(result.name)) writeOutput(card, result);
  // A picture is drawn for any tool that returned one, whatever its name: the
  // manifest says which tools do (`returns_image`), and a second list here
  // would be the one that forgot `blender_view`.
  const render = !failed && (result.has_image ? imageOutput : TOOL_OUTPUT[result.name]);
  if (render && render(into, result, body)) return;
  if (!body) {
    into.appendChild(el("pre", null, "(no output)"));
    return;
  }
  if (failed) {
    // A failed shell command is still a terminal: the same surface, in the
    // error colour, rather than the generic red `pre` the other tools use.
    if (result.name === "shell") {
      into.appendChild(el("pre", "shell-out failed", body));
      return;
    }
    const output = el("pre", null, body);
    output.style.color = "var(--bad)";
    into.appendChild(output);
    return;
  }
  // A successful read is a file. Anything else is output, and numbering the
  // lines of a one-line "wrote 46 bytes" would be noise.
  const looksLikeFile = result.name === "fs_read" || body.split("\n").length > 3;
  into.appendChild(
    looksLikeFile
      ? fileView(body, { highlightCode: result.name === "fs_read" })
      : el("pre", null, body),
  );
}

function toolCard(proposal, { deferred = false, animate = true } = {}) {
  const args = proposal.arguments || {};
  const verb = toolVerb(proposal.name, args);
  const subject = toolSubject(proposal.name, args);
  const title = [verb, subject].filter(Boolean).join(" ");

  // The detail: the full card, shown in the run's live stage or opened under
  // the row once it is history. Built once and moved, never rendered twice
  // (D32). Its head carries
  // the verb and subject at full size, and the audit name beside them: the row
  // drops the name to keep the scan line about content, but the card is where
  // the identifier the audit chain and the manifest use belongs.
  const card = el("div", "tool step-detail collapsed");
  card.dataset.tool = proposal.name;
  // Kept for the result renderer: a `web_fetch` page view needs the URL the call
  // asked for, and by render time only the card is still around.
  const href = toolHref(proposal.name, args);
  if (href) card._url = href;
  // The words its own row uses, kept for anything that needs to name the call
  // later — a scratch file it wrote is titled with them (`rememberScratch`).
  card._origin = { verb, subject };
  const head = el("div", "head");
  head.appendChild(el("span", "verb", verb));
  head.appendChild(el("span", "name", proposal.name));
  if (subject) {
    head.appendChild(
      href ? externalLink("subject", subject, href) : el("span", "subject", subject),
    );
  }
  head.appendChild(el("span", "state", "proposed"));
  card.appendChild(head);

  // The card's scrolling half. The head stays above it and the approve/deny row
  // stays below it, so the name and the buttons are reachable however much the
  // result weighs. This is the one scrollport: every output shape flows into it
  // rather than opening a second scrollbar inside the first.
  const body = el("div", "tool-body");

  // Where the result lands, so a per-tool renderer and the generic one append
  // to the same place and `renderResult` need not know the card's shape. Built
  // before the deferred path so that path has an anchor to insert its arguments
  // in front of it.
  const output = el("div", "tool-output");

  // The detail half — the arguments and the approve/deny row — is what a
  // reopened session defers. `replay` passes `deferred` and installs a
  // `_reveal` that builds it when the row is first opened, so a transcript with
  // a hundred calls pays for the one card the reader asked to see. A live call
  // builds it now, because the frame that fills it may arrive at any moment.
  let ask = null;
  if (!deferred) {
    // The arguments. Classed `args` because a mutating write replaces this with
    // its diff when the approval frame arrives: the arguments are what the model
    // asked for, and a diff is what the file would become.
    body.appendChild(toolInput(proposal.name, args));

    // The approve/deny row, hidden until the server says a human is being asked.
    //
    // Built either way rather than on demand, because the `tool_decision` frame
    // that reveals it is also what removes it — one element with one lifecycle is
    // easier to be sure about than two code paths that construct a card
    // differently. Hidden rather than absent so revealing it is a class toggle
    // rather than a second construction, and the body gives up the row's height
    // without the card changing size.
    //
    // It used to be shown immediately: under `safe` a read-only call flashed
    // buttons for the few milliseconds before its decision arrived, and under
    // `yolo` — where nothing emitted a decision at all — they simply stayed
    // (N89).
    ask = el("div", "ask hidden");
    // No label. "Approve this call?" above an Approve and a Deny button is the
    // buttons restated — the question is already in them, and the row reads
    // faster without it. The spacer keeps the buttons right-aligned.
    ask.appendChild(el("span", null, ""));
    const approve = el("button", "primary", "Approve");
    const deny = el("button", "danger", "Deny");
    approve.onclick = () => decide(proposal.tool_call_id, true);
    deny.onclick = () => decide(proposal.tool_call_id, false);
    ask.appendChild(deny);
    ask.appendChild(approve);
  }

  body.appendChild(output);
  card.appendChild(body);
  // Outside the scrolling body: a button row that scrolls away under a long
  // diff is the N89 bug in a different place.
  if (ask) card.appendChild(ask);

  // The row: the compact line in the timeline that discloses the detail. No
  // audit name — with the verb and the subject already saying what the act is
  // and what it is about, the name was a third label for the same fact.
  // `data-tool` keeps it on the element for anything that needs the row by tool.
  const row = el("div", "tool-row");
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  row.dataset.tool = proposal.name;
  row._label = title;
  row.title = title;
  row.classList.add("outcome-running");
  row.appendChild(el("span", "verb", verb));
  if (subject) row.appendChild(el("span", "subject", subject));
  row.appendChild(el("span", "state", "proposed"));
  row._detail = card;
  row.onclick = () => pressRow(row);
  row.onkeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pressRow(row);
    }
  };

  toolCards.set(proposal.tool_call_id, card);
  toolRows.set(proposal.tool_call_id, row);

  // A step joins the turn's open run, which `appendWork` opens if this is the
  // turn's first. Reasoning blocks take the same path, so a thought between two
  // calls stays inside the run rather than splitting it (D44).
  appendWork(row, { animate });
  return card;
}

async function decide(toolCallId, approved) {
  // Named before the wait: the socket may have to be opened, and the reader can
  // switch sessions while that happens — the verdict belongs to the card it was
  // pressed on, not to whatever is on screen when the connection settles.
  const view = currentView;
  const card = toolCards.get(toolCallId);
  // The verdict travels on the branch's socket, and a window that did not start
  // the turn has none until it needs one: an approval is the one frame a session
  // opened from the projection can send (D63). Opening it here rather than
  // refusing without one keeps the card answerable after a reconnect too.
  if (!view || !(await ensureSocket(view))) return;
  // Only mark the card once the verdict is actually on the wire. `send` returns
  // false when the socket is closed, and removing the buttons anyway left a card
  // reading "approved" whose verdict the server never received — the turn then
  // waited out the full approval timeout and the UI looked frozen with no way to
  // retry. The buttons staying put is what makes a dropped connection
  // recoverable.
  if (!send({ type: "approval", tool_call_id: toolCallId, approved }, view)) return;

  view.remotePending.delete(toolCallId);
  if (card) {
    grow(() => {
      const ask = card.querySelector(".ask");
      if (ask) ask.remove();
      setToolState(
        toolCallId,
        approved ? "approved" : "denied",
        undefined,
        approved ? "running" : "refused",
      );
    });
  }
}

// -- answering a call another window is blocked on ---------------------------
//
// A turn belongs to its socket (D63), so a second window sees no frames from it
// — but the *verdict* is the account's, and the branch's inbox is where it goes.
// What the second window lacked was not authority but the prompt: the card, with
// what the model asked for and the diff the write would make. The server carries
// both on the branch's pending read, so this is the same card by another road.

// Draw one waiting call into the view being rendered. The preview is the one the
// server carried, never a second dispatch: that would be a different tool run,
// and it could describe a different write than the verdict applies to.
function stagePending(call) {
  if (toolCards.has(call.tool_call_id)) return;
  const card = toolCard(
    { tool_call_id: call.tool_call_id, name: call.tool, arguments: call.arguments },
    { animate: false },
  );
  grow(() => followStep(toolRows.get(call.tool_call_id), { intoView: false }));
  if (call.preview) {
    const args = card.querySelector(".args");
    if (args) args.replaceWith(diffView(call.preview));
  }
  const ask = card.querySelector(".ask");
  if (ask) ask.classList.remove("hidden");
  setToolState(call.tool_call_id, "waiting for you", undefined, "waiting");
  renderView.remotePending.add(call.tool_call_id);
}

// The prompts this branch is waiting on, for a view this window holds.
async function loadPending(view) {
  let calls;
  try {
    calls = (await api("GET", `/api/branches/${view.id}/pending`)).pending || [];
  } catch {
    // The card is an extra on top of a turn this window cannot see; a refused
    // read leaves the turn exactly as blocked as it was.
    return;
  }
  if (!views.has(view.id)) return;
  withView(view, () => {
    for (const call of calls) stagePending(call);
  });
}

// The verdict landed elsewhere, so the buttons go. A card still offering
// Approve and Deny for a call that has already been decided is the shape N89
// fixed inside one window; a second window would have brought it back.
function settlePending(view) {
  if (!view.remotePending.size) return;
  withView(view, () => {
    for (const callId of view.remotePending) {
      const card = toolCards.get(callId);
      const ask = card && card.querySelector(".ask");
      if (!card || !ask) continue;
      grow(() => {
        ask.remove();
        // Not "approved" or "denied": this window did not see which, and the
        // result arrives when the session is reopened.
        setToolState(callId, "answered in another window", undefined, "running");
      });
    }
    view.remotePending.clear();
  });
}

// The socket is named rather than read off the module. A message typed in one
// session can be waiting on a connection while the reader opens another, and the
// frame still belongs to the session it was typed in.
function send(frame, view = currentView) {
  const sock = view && view.socket;
  if (!sock || sock.readyState !== WebSocket.OPEN) {
    if (view === currentView) note("Not connected.");
    return false;
  }
  sock.send(JSON.stringify(frame));
  return true;
}

// -- events -----------------------------------------------------------------

async function signIn() {
  $("login-msg").textContent = "";
  try {
    const body = await api("POST", "/api/login", {
      username: $("username").value,
      password: $("password").value,
    });
    csrf = body.csrf_token;
    $("password").value = "";
    showChat(body.account);
    applyAdmin(body.is_admin);
    applyInterface(body.interface);
    applyInstructions(body);
    applyDefaults(body);
    if (body.must_change_password) {
      openSettings();
      showSettingsTab("account");
      $("pw-note").textContent =
        "Your password is temporary. Change it before continuing.";
      return;
    }
    await loadPolicy();
    // The draft, not a session. Signing in is not starting a conversation, and
    // treating it as one is what left a row behind for every visit.
    showDraft();
    loadSessions();
    startActive();
  } catch (error) {
    // The server does not distinguish an unknown account from a wrong
    // password, and neither does this: anything else is an enumeration oracle.
    $("login-msg").textContent = error.message === "unauthenticated"
      ? "Incorrect username or password."
      : error.message;
  }
}

$("login-go").onclick = signIn;

// Enter from either field, since there is no form to submit. Explicit rather
// than inherited: what the browser would have done for free is exactly what
// leaked the password into the URL.
for (const id of ["username", "password"]) {
  $(id).onkeydown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      signIn();
    }
  };
}

// The composer is one box across every session, so a send that had to wait for
// a socket can come back to find the reader typing in another one. Only the text
// that was actually sent is cleared; anything typed since stays.
function clearSent(box, content) {
  if (box.value.trim() !== content) return;
  box.value = "";
  box.style.height = "auto";
}

async function submit() {
  if (currentView && currentView.editing) {
    focusViewEntry(currentView);
    return;
  }
  const box = $("input");
  const content = box.value.trim();
  if (!content) return;
  // Sending ends a review, and the button path never sees a keydown.
  markPromptCursor(null);

  // Mid-turn, the same box steers instead of starting a new turn (D12: the
  // WebSocket posts into the session's inbox and the harness drains it). One
  // input for both is the whole point — an agent you can correct while it
  // works does not need to be stopped and restarted.
  if (currentView && currentView.running) {
    const steering = currentView;
    if (!(await ensureSocket(steering))) return;
    if (!send({ type: "steer", content }, steering)) return;
    clearSent(box, content);
    paintComposerMode();
    return;
  }

  if (!$("model").value || $("model").disabled) {
    note("No model is available to send to.");
    return;
  }
  // From the draft, this message is what creates the session. Checked *after*
  // the model check: a draft that cannot be sent to should not leave a session
  // row behind, which is the garbage the hero exists to stop generating.
  let view = currentView;
  if (drafting()) {
    // Cleared before the await, not after. The box is the only thing holding
    // this text and the create round-trip is a window in which Enter can fire
    // again — twice, and the second send would create a second session with
    // the same message in it.
    box.value = "";
    box.style.height = "auto";
    setBusy(true);               // the composer is not idle during the create

    let ready = false;
    try {
      view = await startSession();
      // Marked before the handshake, so the session cannot be discarded as
      // idle while the socket is opening.
      markTurnStarted(view);
      ready = await connect(view);
    } catch (error) {
      note(`Could not start a session: ${error.message}`);
    }

    if (!ready || !view) {
      // `connect`'s own handlers have already said why, and `showDraft` puts
      // the text back nowhere — so the message is restored here rather than
      // lost to a failed handshake.
      if (view) markTurnFailed(view);
      box.value = content;
      autosize(box);
      showDraft();
      return;
    }
    loadSessions();              // the new row, and it is the current one
  }

  if (!view) return;
  // The turn is marked before a socket is waited for. If the reader switches
  // sessions during that wait, this view is still running — so it is kept, and
  // the frame below goes to this view's socket rather than to whichever one is
  // on screen by then. A new session's view was already marked before its
  // handshake, so its clock starts at the message rather than at the connect.
  if (!view.running) markTurnStarted(view);
  if (!(await ensureSocket(view))) {
    markTurnFailed(view);
    return;
  }

  // Omitted unless *this* model declares the level, which the server reads as
  // "the model's own default" — and which it refuses outright for a model that
  // takes no level, so sending one unasked-for fails the turn. `turnFrame` is
  // the one place that rule, and the view's model copy, are applied.
  const frame = turnFrame(view, content);
  if (!frame) {
    markTurnFailed(view);
    return;
  }

  if (!send(frame, view)) {
    // A send that never left must not leave Stop live over a turn that is not
    // running, and the view it was for must not sit in Active claiming one is.
    markTurnFailed(view);
    return;
  }
  // No optimistic bubble: the server echoes this back as `user_message`, and
  // drawing it here as well would show it twice.
  clearSent(box, content);
  paintComposerMode();
  // Against the view that received the message, not whichever is on screen by
  // now: a switch during the handshake must not clear the new view's bubbles.
  withView(view, () => {
    agentTurn = null;
    endThought();
  });
}

$("send").onclick = submit;

$("input").onkeydown = (event) => {
  // A keystroke that is composing belongs to the IME, Enter included: it
  // confirms the composition rather than sending it.
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    markPromptCursor(null);
    submit();
    return;
  }
  if (event.key === "Escape" && promptCursor) {
    // The review is the thing on top while the composer has the keyboard, so
    // Escape takes it and not the rail or a card behind it.
    event.preventDefault();
    event.stopPropagation();
    endPromptCursor();
    return;
  }
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    const box = event.target;
    if (promptCursor) {
      // The cursor owns the arrows while it is active: they move it, and the
      // draft's text and caret are untouched.
      event.preventDefault();
      const messages = promptMessages();
      const at = messages.indexOf(promptCursor);
      if (event.key === "ArrowUp") {
        if (at > 0) showPrompt(messages[at - 1]);
      } else if (at >= 0 && at < messages.length - 1) {
        showPrompt(messages[at + 1]);
      } else {
        endPromptCursor();
      }
      return;
    }
    // First press: only from the edge the arrow points at. Down is absent —
    // from the draft there is nothing newer than the draft to walk to.
    if (event.key === "ArrowUp"
        && box.selectionStart === 0 && box.selectionEnd === 0) {
      const messages = promptMessages();
      if (!messages.length) return;
      event.preventDefault();
      showPrompt(messages[messages.length - 1]);
    }
  }
};

// The review only exists while the composer has the keyboard: losing focus —
// to a chooser, the transcript, the Send button — ends it rather than leaving a
// ring on a message the arrows can no longer reach.
$("input").addEventListener("blur", () => markPromptCursor(null));

// Grow with the text, to a point.
$("input").oninput = (event) => {
  // Typing is an edit, so a review is over — without this the highlight would
  // outlive the arrows that put it there.
  markPromptCursor(null);
  autosize(event.target);
  // The first keystroke is what brings Send to full strength.
  paintComposerMode();
};

// The type-ahead itself. One handler shape for both choosers: the field is the
// filter, and `applyChooserFilter` decides what a typed path means for matching.
for (const menuId of ["cwd-menu", "attach-menu"]) {
  const { field } = chooserParts(menuId);
  field.oninput = () => {
    const view = chooserViews.get(menuId);
    if (!view) return;
    view.filter = field.value.toLowerCase();
    applyChooserFilter(menuId);
  };
}

// A side panel's head is the way into that panel: the left one climbs to the
// parent, the right one opens the highlighted folder. The rows did this one at a
// time; the head is a target that is there whatever the rows hold.
for (const menuId of ["cwd-menu", "attach-menu"]) {
  const { heads } = chooserParts(menuId);
  // Kept off the field's caret, as a press on a row is.
  heads.left.onmousedown = heads.right.onmousedown = (event) => event.preventDefault();
  heads.left.onclick = () => climb(menuId);
  heads.right.onclick = () => openSelected(menuId);
}

// The two openers — the folder button in the composer and the path on the status
// line — do the same thing, so they share one handler rather than two that must
// be kept saying the same thing.
async function toggleWorkingDirMenu() {
  const menu = $("cwd-menu");
  if (menu.classList.contains("hidden")) {
    // Reopens at the session's current directory, not the roots: the directory
    // being changed is nearly always the one in effect or a neighbour, and
    // walking the tree again just to nudge it is the part that read as work.
    await openWorkingDirMenu();
  } else {
    menu.classList.add("hidden");
  }
}

$("cwd-pick").onclick = toggleWorkingDirMenu;
$("cwd-current").onclick = toggleWorkingDirMenu;

// The chooser's footer: the one act here that is not a choice of directory.
// `saveWorkingDir` stages it before a session exists and PATCHes it after, so
// this is the same path a chosen directory takes, with null for the value.
$("cwd-clear").onclick = () => saveWorkingDir(null);

$("attach").onclick = async () => {
  const menu = $("attach-menu");
  if (menu.classList.contains("hidden")) {
    await openAttachMenu();
  } else {
    menu.classList.add("hidden");
  }
};

// Every card a control in this client opens, as [panel, the controls that open
// it] — and, for the ones whose opener is not in the bar, the element a press
// inside which must count as a press inside the panel.
//
// A list of openers, not one: the working directory is stated on the status line
// and changed from the folder button in the composer, and both presses must count
// as presses on the card — otherwise click-away hides the menu on the way down
// and the second opener's own handler reopens it, so the control never closes.
//
// One list, because three things need to agree about it: click-away dismissal,
// Escape, and `aria-expanded`. They had a copy each, which is the shape of every
// stale-value bug this client has found — a panel closes and the attribute that
// describes it does not, or a new panel is added to one loop and not the other.
//
// `CARDS`, not `RAIL`: these are the floating cards. The rail's width is a
// separate concern, because a column is not a popover.
//
// The rail is deliberately absent. It is an overlay driven by `rail-collapsed`
// rather than by the `.hidden` class these cards use, so its dismissal lives with
// the rail (`setRailCollapsed`) rather than in this loop.
const CARDS = [
  ["cwd-menu", ["cwd-pick", "cwd-current"]],
  ["attach-menu", ["attach"]],
  ["models", ["gpu-toggle"]],
  ["approval-menu", ["approval-chip"]],
  ["reasoning-menu", ["reasoning-chip"]],
  ["branch-menu", ["branch-chip"]],
  ["tools-menu", ["tools-chip"]],
  // The rail's own popover. It is inside the panel it orders rather than
  // parented to `body` like the row menu, so it scrolls with nothing and needs
  // no rect arithmetic — but it is still a card a control opens, so it belongs
  // in the one list that decides dismissal and `aria-expanded`.
  ["sessions-sort-menu", ["sessions-sort"]],
  // Settings is deliberately absent, and so is the account menu that used to
  // open it. Settings is a *modal* now: it has a scrim, and the scrim is what
  // dismisses it — a press outside has a real target rather than being inferred
  // from "not inside any listed card". Folding it into this loop would also
  // leave the scrim up, since the loop only knows how to hide one element.
  // `closeSettings` hides both, and Escape routes to it below.
];

// A press anywhere outside the bubble being edited abandons the edit. Capture,
// because the target may be a control that switches the view on pointerdown;
// by the time a bubble handler runs the editor could belong to another view.
// A press inside the bubble — the textarea, Send or Cancel — is the edit itself
// and must reach its own handler.
document.addEventListener("pointerdown", (event) => {
  const edit = currentView && currentView.editing;
  if (!edit || edit.busy) return;
  if (event.target instanceof Node && edit.node.contains(event.target)) return;
  cancelEdit(currentView);
}, true);

// Click away to dismiss. A popover floating over the transcript has to close on
// the next click somewhere else, or it sits on top of what you were trying to
// read until you remember which button opened it. The rail gets the same
// dismissal: it is opened by a button, so the next click on the transcript is
// the natural way to put it away. Settings is the exception and has its own
// scrim — see `CARDS`.
//
// On `pointerdown` rather than `click`, so the menu is gone by the time the
// press completes — a `click` listener fires after the pointer has already
// landed, which looks like a frame of lag on every dismissal.
//
// The toggle buttons are excluded because they already toggle: closing here on
// the way down would hide the menu, and then the button's own handler would see
// it hidden and reopen it, so the control would never close.
document.addEventListener("pointerdown", (event) => {
  // Settings is modal, so a press lands either in the dialog or on its scrim
  // and nothing behind it is reachable. Returning here is what keeps one press
  // from doing two things: the scrim's own handler closes the dialog, and
  // without this the same press would also collapse the rail underneath it.
  if (settingsOpen()) return;

  // The per-row session menu, which has no id to list below: it is built on
  // demand and lives on `body`. Its own buttons stop propagation, so reaching
  // here with the menu open means the press landed somewhere else.
  if (rowMenu && !rowMenu.contains(event.target)) closeRowMenu();
  const onForkNode = event.target instanceof Element
    && event.target.closest(".fork-node") !== null;
  if (forkMenuOpen
      && !onForkNode
      && !$("fork-menu").contains(event.target)) {
    closeForkMenu();
  }

  for (const [menuId, openers] of CARDS) {
    const menu = $(menuId);
    if (menu.classList.contains("hidden")) continue;
    if (menu.contains(event.target)
        || openers.some((id) => $(id).contains(event.target))) continue;
    // The Models window owns a poll timer, so it has its own close rather than
    // just `classList.add("hidden")`.
    if (menuId === "models") { closeModelsPanel(); continue; }
    menu.classList.add("hidden");
  }

  // The rail overlays the transcript at every width, so a press outside it puts
  // it away — the same dismissal every popover here has, for the same reason. The
  // row menu is parented to `body` rather than to the rail, so a press inside it
  // counts as inside — the same exclusion that keeps removing a session from
  // shutting the list it came from.
  if (!document.body.classList.contains("rail-collapsed")
      && !$("sessions").contains(event.target)
      && !$("sessions-grip").contains(event.target)
      && !$("sessions-resize").contains(event.target)
      && !(rowMenu && rowMenu.contains(event.target))) {
    setRailCollapsed(true);
  }
});

// `aria-expanded`, kept in step with those panels by *observing* them rather than
// by writing the attribute wherever one of them is opened or hidden. There are a
// dozen such places — both sign-in paths, every toggle, the click-away loop, the
// shared dismissals — and the attribute set at the call site is the one that goes
// missing when a panel is added later, which is the same failure as a disclosure
// that never says it is open: to anyone not using a mouse, the panel appears from
// nowhere and Escape appears to do nothing.
//
// Settings is included now even though it is not in `CARDS`: it *is* toggled by
// a control of its own (the account chip, whose `aria-haspopup` is `dialog`), so
// the chip owns the disclosure. It is absent from `CARDS` only because its
// dismissal is the scrim's rather than the click-away loop's — two different
// questions about the same panel, and conflating them is what left the chip
// silent about a dialog it opens.
const EXPANDING = [...CARDS, ["settings", ["account-toggle"]]];
const panelObserver = new MutationObserver(() => {
  for (const [panelId, openers] of EXPANDING) {
    for (const id of openers) {
      $(id).setAttribute(
        "aria-expanded", String(!$(panelId).classList.contains("hidden")));
    }
  }
});
for (const [panelId] of EXPANDING) {
  panelObserver.observe($(panelId), { attributes: true, attributeFilter: ["class"] });
}

// How much room there is above the composer, published for the popovers that
// open upward into it (`--headroom` in the stylesheet, where the geometry is).
//
// Measured rather than assumed, because the composer is not always at the bottom
// of the window: in the draft it is centred, which leaves about half as much room
// as the `vh` caps those panels used to carry. The tool chooser was the visible
// casualty — allowed 60vh above a box at the middle of the window, it grew past
// the top edge, and because the panel is the scrolling element its first rows
// landed somewhere no scroll or click could reach.
//
// A `ResizeObserver` rather than a window `resize` listener, because the card's
// own height is half the question and it changes without the window changing at
// all: every line typed into the field, every attachment chip, and the switch out
// of `drafting` on the first message move its top edge.
//
// It watches `body` as well as the card, and that second target is not
// redundant. In the draft the card is `align-self: center`, so shortening the
// window moves its top edge while leaving its own box exactly as it was — the
// one case the card alone cannot see. `body` is the grid it is centred in, so
// `body`'s height changing is the signal.
//
// A `ResizeObserver` sees size and not position, so the observer alone cannot
// see `drafting` being toggled: the card's box is the same at the floor of the
// window and at the middle of it. `measureHeadroom` is named for that case, and
// the two places that toggle the class (`paintHero`, `leaveDraft`) call it
// directly. Without that the tools panel stayed capped to the room a *session*
// had, and in a draft it opened past the top edge — where its own scrollbar
// could not reach the first rows (N138).
function measureHeadroom() {
  const top = $("context").getBoundingClientRect().top;
  // Clamped at zero because a composer taller than the window puts its top edge
  // above the viewport, and "headroom" is a distance, not a signed coordinate.
  // What keeps a panel openable at that size is the floor on `--headroom-cap`.
  document.documentElement.style.setProperty("--headroom", `${Math.max(0, top)}px`);
}

const headroom = new ResizeObserver(measureHeadroom);
headroom.observe($("context"));
headroom.observe(document.body);

// The chooser keyboard, for whichever menu is open. On `document` rather than
// the menu so opening one need not steal focus from wherever it was.
document.addEventListener("keydown", chooserKeydown);

// Escape dismisses whatever a control opened, top-most first.
//
// It used to close the Models window and nothing else, which left the panels and
// both menus dismissable by clicking the transcript and by no key at all — the
// one dismissal a keyboard user has.
//
// Separate from `chooserKeydown` because these are panels, not lists of rows.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  // The choosers own their Escape, and they float above the panels: one press
  // closing the chooser *and* the panel behind it would take two things at once.
  if (!$("cwd-menu").classList.contains("hidden")
      || !$("attach-menu").classList.contains("hidden")) return;
  // The row menu first and instead: it floats above every panel, so Escape should
  // take the thing on top rather than both at once.
  if (rowMenu) {
    closeRowMenu();
    return;
  }
  // A fork node's local branch switcher is a card floating over the transcript,
  // so Escape takes it before the panels behind it and puts focus back on the
  // node that opened it.
  if (forkMenuOpen) {
    const node = forkMenuOpen.node;
    closeForkMenu();
    node.focus({ preventScroll: true });
    return;
  }
  // Settings before the rail and the cards: it is modal, so nothing behind it
  // is actually reachable and Escape can only sensibly mean this.
  if (settingsOpen()) {
    closeSettings();
    return;
  }
  // The rail's own sort menu, before the rail itself. It lives *inside* the
  // panel, so the branch below would fold the whole rail instead of closing the
  // one thing on top of it — and the reader would lose the list they were
  // rearranging.
  if (!$("sessions-sort-menu").classList.contains("hidden")) {
    $("sessions-sort-menu").classList.add("hidden");
    $("sessions-sort").focus();
    return;
  }
  // The rail's overlay, at every width — Escape takes the glance you opened
  // before it reaches the cards behind it.
  if (!document.body.classList.contains("rail-collapsed")) {
    setRailCollapsed(true);
    $("sessions-grip").focus();
    return;
  }
  for (const [panelId, openers] of CARDS) {
    if (panelId === "cwd-menu" || panelId === "attach-menu") continue;
    if ($(panelId).classList.contains("hidden")) continue;
    if (panelId === "models") closeModelsPanel({ refocus: true });
    else {
      $(panelId).classList.add("hidden");
      $(openers[0]).focus();
    }
    return;
  }
});

// The local fork menu's rows form one focus group: arrow keys move between
// them, as they do in every other menu here. Enter and Space are the button's
// own activation and need no handler.
document.addEventListener("keydown", (event) => {
  if (!forkMenuOpen) return;
  const menu = $("fork-menu");
  if (!menu.contains(event.target)) return;
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const rows = [...menu.querySelectorAll(".fork-row")];
  if (!rows.length) return;
  const index = rows.indexOf(document.activeElement);
  let next;
  if (event.key === "Home") next = 0;
  else if (event.key === "End") next = rows.length - 1;
  else if (index < 0) next = event.key === "ArrowUp" ? rows.length - 1 : 0;
  else if (event.key === "ArrowDown") next = (index + 1) % rows.length;
  else next = (index - 1 + rows.length) % rows.length;
  rows[next].focus();
});

// The transcript moving under a fixed menu leaves it pointing at a node that is
// no longer there; the next frame of a running turn is enough to do it.
$("log").addEventListener("scroll", closeForkMenu, { passive: true });
window.addEventListener("resize", closeForkMenu);

// Type anywhere, and it lands in the composer.
//
// The box is not a thing to be clicked into; while this window has focus it is
// where typing goes, the way a terminal's prompt is. Clicking and highlighting the
// transcript, scrolling it, opening a tool card's disclosure — every one of those
// moves focus off the field, and every one used to mean a second click before the
// next sentence. What this removes is that click.
//
// The keystroke is not consumed: focus moves to the textarea and the browser's own
// default action lands the character there, at whatever caret the box was last
// left at. Inserting the text here by hand would be a second text editor, and it is
// the version that drops a key, an IME composition, or a paste.
//
// A keystroke belongs somewhere else, and is left there, when:
//   - focus is already in a field that takes text (the composer, a rename box, a
//     chooser filter, the login form) — the case this must never break;
//   - a modifier is held, because then it is a command rather than a character —
//     except Cmd/Ctrl+V, which is input and not an act on the page;
//   - the browser is mid-IME-composition, where the keystroke belongs to the IME
//     and moving focus would strand it;
//   - it is Space over something that activates — a menu button, a disclosure's
//     summary, a list row — because Space already means "press this" to a keyboard
//     user, and no letter competes with that meaning;
//   - a chooser is open, because that menu owns its keyboard and already turns
//     printable keys into its own filter (see `chooserKeydown`);
//   - the settings dialog is open, because it is modal: the composer is behind a
//     scrim, so focusing it would put the cursor somewhere invisible;
//   - a bubble is being edited, in which case the bubble is the composer — the
//     box itself is disabled, and focusing it would drop the keystroke;
//   - the composer is not on screen, which is the login surface.
//
// `defaultPrevented` is checked first so a handler that has already claimed the
// key wins, whatever order this one is registered in.
function activates(node) {
  return node instanceof Element
    && node.closest("button, a[href], summary, [role='button'], [role='menuitem'], [role='option']") !== null;
}

document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented) return;
  if (event.isComposing || event.keyCode === 229) return;

  // Paste is the one modified keystroke that is input rather than an act on the
  // page: someone who has clicked into the transcript and presses Cmd/Ctrl+V means
  // "put this in the message", the same as typing it. Every other modifier is a
  // command, and is left to the browser.
  const paste = (event.ctrlKey || event.metaKey) && !event.altKey
    && event.key.toLowerCase() === "v";
  if (!paste && (event.ctrlKey || event.metaKey || event.altKey)) return;
  // A character, not a control key. Enter sends, the arrows move, Tab leaves and
  // Escape dismisses; each already means something where it lands. Backspace and
  // Delete are edits rather than typing, but they are the edits someone reaches
  // for first, and the same trip back to the box should carry them.
  if (!paste && event.key.length !== 1
      && event.key !== "Backspace" && event.key !== "Delete") return;
  if (takesText(event.target)) return;
  if (event.key === " " && activates(event.target)) return;
  if (!$("cwd-menu").classList.contains("hidden")
      || !$("attach-menu").classList.contains("hidden")) return;
  // A modal is open, so the composer is behind a scrim and unreachable. Without
  // this, typing a letter in Settings moved focus to a field the reader cannot
  // see and dropped the keystroke into the next message.
  if (settingsOpen()) return;
  if ($("composer").classList.contains("hidden")) return;
  if (currentView && currentView.editing) {
    const ta = currentView.editing.node.querySelector("textarea");
    if (ta) {
      ta.focus();
      return;
    }
  }
  $("input").focus();
});

// The row menu is positioned `fixed` from a row's rect, so anything that moves
// the row underneath it leaves it pointing at the wrong one. Scrolling the list
// and hiding the panel both do; closing is better than recomputing, because a
// menu that follows a scroll is a menu you have to chase.
$("sessions-list").addEventListener("scroll", closeRowMenu);

// The type-ahead, debounced because each keystroke is now a query.
//
// The filter searches server-side (see `loadSessions`), so typing "retention"
// would otherwise be eight requests of which seven are already stale. 160ms is
// below the point a pause between keystrokes reads as lag and above the gap
// inside a typed word, so a burst collapses to one request and a pause issues
// it immediately.
//
// `loadSessions` already guards against an out-of-order answer with
// `sessionsPaint`, so a slow response from an earlier prefix cannot overwrite a
// later one — the debounce is about not *sending* them, not about correctness.
let filterTimer = null;

$("sessions-filter").addEventListener("input", (event) => {
  // One search box for whichever list the rail is showing (D99).
  if (tasksIsOpen()) {
    tasksFilter(event.target.value);
    return;
  }
  sessionsFilterText = event.target.value;
  clearTimeout(filterTimer);
  filterTimer = setTimeout(loadSessions, 160);
});

// The sort control: a menu of the two dates, opened from the glyph beside the
// search field. It is repainted on open rather than only on change, so the dot
// reflects a choice made in another tab in the same browser, and focus moves
// into it the way the row menu's does — a menu that opens without taking the
// keyboard is one a keyboard user has to Tab into before it can be used.
$("sessions-sort").onclick = () => {
  const menu = $("sessions-sort-menu");
  if (menu.classList.contains("hidden")) {
    paintSortMenu();
    menu.classList.remove("hidden");
    menu.querySelector("button").focus();
    return;
  }
  menu.classList.add("hidden");
};

$("sessions-sort-menu").onclick = (event) => {
  const button = event.target.closest("button[data-sort]");
  if (!button) return;
  // Closed before the request rather than when it lands, like the approval
  // picker's menu: `loadSessions` repaints the list underneath, and a menu that
  // redraws under the pointer invites a second click on an item that has moved.
  $("sessions-sort-menu").classList.add("hidden");
  $("sessions-sort").focus();
  chooseSort(button.dataset.sort);
};

// Once at startup, so the control names the stored order before its menu has
// ever been opened. The markup's own label is the no-script fallback.
paintSortMenu();

$("gpu-toggle").onclick = async () => {
  const panel = $("models");
  if (!panel.classList.contains("hidden")) {
    closeModelsPanel();
    return;
  }
  // The card floats over the status line it opened from, and the click-away loop
  // has already put away any other card on the way down — so there is no corner
  // to share and no exclusion list to keep.
  panel.classList.remove("hidden");
  // The first read answers at once; the watch then waits for anything newer.
  watchGpu(true, await loadModelState());
};

// The approval menu. It opens *upward* from the footer — see the CSS — because
// the chip sits at the bottom of the card, and a menu below it would be off the
// bottom of the window. The same shape the path choosers use for the same reason.
$("approval-chip").onclick = () => {
  $("approval-menu").classList.toggle("hidden");
};

// The reasoning menu, the same upward popover as approval. A non-operator changes
// the model from the dropdown, which fires `change`; `paintReasoning` rebuilds
// the levels for the new model and hides the chip when it declares none.
$("reasoning-chip").onclick = () => {
  $("reasoning-menu").classList.toggle("hidden");
};

// Which branch the next message runs in. The menu is rebuilt on open because a
// fork changes the list, and a stale branch is one the reader could switch to
// and not find.
$("branch-chip").onclick = () => {
  const menu = $("branch-menu");
  const opening = menu.classList.contains("hidden");
  if (opening) {
    closeForkMenu();
    renderBranchMenu();
  }
  menu.classList.toggle("hidden");
};

// The tool chooser. Repainted on open rather than only on change, because the
// grant can have been narrowed by an operator since the panel was last drawn
// and a stale tick is the one thing this panel must not show.
$("tools-chip").onclick = () => {
  const menu = $("tools-menu");
  const opening = menu.classList.contains("hidden");
  if (opening) paintTools();
  menu.classList.toggle("hidden");
};
$("model").addEventListener("change", () => {
  const name = $("model").value;
  if (!name) {
    paintReasoning();
    return;
  }
  const level = chosenLevel(name);
  if (level) reasoningChoice[name] = level;
  paintReasoning();
  persistBranchChoice({ model: name, reasoning: level }).catch((error) => {
    note(`Could not save the model: ${error.message}`);
  });
});

// The account chip opens Settings directly. It used to open a two-item menu —
// Settings and Sign out — which put the thing people came for behind two
// clicks; Sign out is in the dialog's sidebar now and the menu is gone.
$("account-toggle").onclick = () => {
  if (settingsOpen()) closeSettings();
  else openSettings();
};

$("settings-close").onclick = closeSettings;
// The scrim is the "click anywhere else" target. A real element rather than a
// document-level listener inferring "not inside the dialog": it also blocks the
// press from reaching whatever is underneath, which is what makes the dialog
// modal rather than merely on top.
$("scrim").onclick = closeSettings;

for (const tab of SETTINGS_TABS) {
  $(`tab-${tab}`).onclick = () => showSettingsTab(tab);
}

// Written on change rather than on close, so the preference is saved even if the
// dialog is dismissed by Escape or the scrim.
$("pref-hide-reasoning").onchange = (event) => {
  setInterface({ collapse_reasoning: event.target.checked });
};

// Written on change rather than on every keystroke, like the instructions box:
// half-typed numbers are not settings. A cleared or unreadable box goes back to
// what is in force rather than becoming 0 — 0 is a real answer (`keep nothing`),
// not the absence of one — and the field is clamped to the bound the route
// accepts before it is sent, so a typed 99999 lands as the maximum rather than as
// a refusal.
$("pref-active-minutes").onchange = (event) => {
  const typed = event.target.value.trim();
  const asked = typed === "" ? NaN : Number(typed);
  const minutes = Number.isFinite(asked)
    ? Math.min(MAX_ACTIVE_MINUTES, Math.max(0, Math.round(asked)))
    : interfacePrefs.active_minutes;
  event.target.value = String(minutes);
  if (minutes !== interfacePrefs.active_minutes) {
    setInterface({ active_minutes: minutes });
  }
};

// One listener on the group rather than one per swatch: the buttons are static
// markup, and delegation means a theme added to the stylesheet and the HTML
// needs no new handler to go with it.
$("pref-palette").onclick = (event) => {
  const button = event.target.closest("button[data-palette]");
  if (!button) return;
  // No-op on the theme already in force. Not a correctness guard — the server
  // would accept it — but it keeps a double-click from spending a request to
  // change nothing.
  if (button.dataset.palette === interfacePrefs.palette) return;
  setInterface({ palette: button.dataset.palette });
};

// The instructions box is written on an explicit press, unlike the two
// preferences above: a textarea in progress is not a preference until the
// person says it is. Typing clears the last "Saved" note, because it is no
// longer true of what is in the box.
$("pref-instructions").oninput = () => {
  $("pref-instructions-note").textContent = "";
  paintInstructions();
};
$("pref-instructions-save").onclick = saveInstructions;
$("pref-default-model").onchange = () => {
  paintDefaults();
  saveDefaults();
};
$("pref-default-reasoning").onchange = saveDefaults;

// The one credential this person owns. The server refuses every other route
// until a temporary password is replaced, so this is not a convenience.
$("pw-save").onclick = async () => {
  const note = $("pw-note");
  const current = $("pw-current").value;
  const next = $("pw-new").value;
  const repeated = $("pw-repeat").value;
  note.textContent = "";
  if (next.length < 12) {
    note.textContent = "The new password must be at least 12 characters.";
    return;
  }
  if (next !== repeated) {
    note.textContent = "The new passwords do not match.";
    return;
  }
  try {
    await api("PATCH", "/api/me/password", {
      current_password: current,
      new_password: next,
    });
    $("pw-current").value = "";
    $("pw-new").value = "";
    $("pw-repeat").value = "";
    note.textContent = "Password changed.";
  } catch (error) {
    note.textContent = `Could not change it: ${error.message}`;
  }
};

$("interrupt").onclick = () => send({ type: "interrupt" });

// "New session" is now free and instant: it returns to the draft rather than
// creating a row. Clicking it ten times costs nothing, which is the point —
// every one of those clicks used to leave an untitled session behind.
$("new").onclick = () => {
  // The rail's primary act is the list's: in the task list it is New task.
  if (tasksIsOpen()) {
    tasksNew();
    return;
  }
  showDraft();
  // Repaint, so the row that was current stops being drawn as current.
  loadSessions();
};

$("logout").onclick = async () => {
  try {
    await api("POST", "/api/logout", {});
  } catch {
    // Already gone is the same outcome as logged out.
  }
  showLogin("Signed out.");
};

// -- start ------------------------------------------------------------------
//
// `/api/me` returns the CSRF token for an existing session, so a reload does
// not require signing in again — the cookie survives, and the token it needs
// comes back with the identity.
(async () => {
  try {
    // Before the identity, so the sign-in screen is branded even when there is
    // no session to read a name from. A name is cosmetic: failing to read one
    // must not stop somebody signing in.
    try {
      applyAgentName((await api("GET", "/api/brand")).name);
    } catch {
      // Left at the built-in name, which is what the markup already says.
    }
    const me = await api("GET", "/api/me");
    csrf = me.csrf_token;
    applyAgentName(me.agent_name);
    showChat(me.account);
    applyAdmin(me.is_admin);
    applyInterface(me.interface);
    applyInstructions(me);
    applyDefaults(me);
    if (me.must_change_password) {
      openSettings();
      showSettingsTab("account");
      $("pw-note").textContent =
        "Your password is temporary. Change it before continuing.";
      return;
    }
    await loadPolicy();
    // A reload lands on the draft, not on a new session and not on the last
    // one. This is the line that used to POST `/api/sessions`, so every reload
    // minted a row nothing would ever delete.
    //
    // Landing on the *last* session instead — what ChatGPT and Claude do —
    // would mean remembering an id across reloads, which is navigation state in
    // `localStorage` and a session that reopens itself without being asked. The
    // hero is the honest default: the list is one click away and nothing is
    // resumed by surprise.
    showDraft();
    loadSessions();
    startActive();
  } catch {
    showLogin();
  }
})();
