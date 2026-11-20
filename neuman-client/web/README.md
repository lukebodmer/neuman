# neuman-client/web

The chat window: four files, no build step, no dependencies.

## Public surface

- **`neuman-client/default.nix`** builds it with `pkgs.callPackage ./web { }`,
  the client's wrapper points `NEUMAN_CLIENT_WEB` at the store path, and the
  client's UI server serves it on `127.0.0.1:8642` with the core's API proxied
  beside it. Nothing else consumes it, which is why it lives here rather than at
  the root.

## Private surface

Everything inside the file. There is no module boundary to respect because
there are no modules.

## Why one file

Because the point is iteration. The chat window is the part of Neuman most
likely to be rewritten, thrown away, or replaced by a native app — the slot this
directory reserves — and a toolchain would make every experiment cost a build.
Editing a file and reloading a tab is the whole loop.

It is also the untrusted tier (§3). A dependency tree on the browser surface is
attack surface that has to earn its place, and a chat window does not need one.
When this grows past what one file can hold, the thing to add is a second file,
then a bundler — in that order, and only when the pain is real — and the palette
split below is that first step and no further.

**`palette.css` is the one stylesheet both documents load.** `index.html` and
`admin.html` are separate views — a transcript and a table are different
layouts — but they must agree on colour, so the tokens and the two base rules
(`border-box`, the scrollbar) live in one file and each page loads only its own
layout after it. The administration page used to load `neuman.css` for the tokens
it needs and inherit the chat window's layout with them: its `#sessions`
host became the chat window's session rail, fixed to the left edge of the window
over everything else (N158).

Anything dropped in `src/` is served: the derivation copies the directory whole,
so the favicon needs no packaging change and no CSP change (`img-src 'self'`
already covers same-origin images). The icon is the one asset with a source —
`favicon.svg` is the drawing, and the PNGs, the `.ico` and the touch icon were
rendered from it, the touch icon with the tile's corners squared because iOS
masks that one itself. One catch worth knowing — the flake builds from a **git tree**,
so an unstaged file is invisible to the build and the build succeeds without it.
A missing asset looks exactly like a browser cache miss (N103).

## What it must get right

Three things, because the core refuses the client if it gets them wrong, and
because a rewrite would drop them first:

- **The session token is in an HttpOnly cookie the page cannot read.** It never
  touches JavaScript. Nothing in this file stores it, and nothing can.
- **Every unsafe request carries `x-csrf-token`.** The token comes from the
  login response, lives in a variable, and is re-checked by the server per
  request — a valid session is not the same thing as an intended request.
- **The WebSocket authenticates on upgrade, from the same cookie.** No second
  login, and no token in the URL, so a URL that lands in a log or a `Referer`
  carries nothing.

`localStorage` holds none of *it* — no token, no CSRF value — deliberately:
anything there is readable by anything that achieves script execution, which is
what the CSRF token exists to survive. It still holds a few per-browser
ergonomics (whether replies are spoken, the rail's width); the preferences that
describe a *person* rather than a browser are on the account instead (D53).

## The shell

One column and a composer: the transcript, with the composer beneath it so the
box you write into and the prose it writes into share a measure. There is no
second pane — the live step's detail is in the run's foot card and history opens under its row (see below) — so
nothing competes with the reading measure for width. A conversation shorter than
the window sits at the foot of the transcript, beside the composer: the first
child's `margin-top: auto` takes the free space, so a new chat starts where the
message box is and the work stage is at the bottom from its first step. Once the
log overflows the auto margin is zero and the scroll range is the content's own.

**The rail is an overlay, not a column.** It was a grid track once, and a track
displaces: opening a glance beside the transcript squeezed the transcript, and
dragging the rail wide squeezed it harder. It is `position: fixed` with a shadow
at every width now, so the list goes *over* what you are reading and leaves the
transcript and the composer exactly where they were. A fixed grip on its edge
folds it, and the whole right edge drags to resize it: the grip is a fold button
and a resize handle, the edge is a resize handle only. Because it floats it
dismisses the way the popovers do — a press outside it, or Escape.

**Its width is a constant, with the window as a ceiling.** `min(16rem,
calc(100vw - var(--rail-strip)))`: the reading measure the rail carries, and only
where the window cannot afford it does the rail give way — down to the width that
still holds the two controls pinned to that edge, the grip's 14px of overhang and
the account circle at `right: 16px`. A `min()` rather than a `vw` scale, because
a width that scales with the viewport is a width that differs between two
browsers whose windows are not the same size, and reads as too narrow on exactly
the windows where a sidebar matters most (N136). A width you dragged is stored
per browser and applied through the same ceiling, so a window narrowed later can
still be escaped from. The key is versioned (`neuman.sessions.width.v2`) because
the values the old drag ratcheted down were never widths, and reading them back
would keep the fault alive in the one browser that held one (N136).

**The document never scrolls sideways.** `html` is `overflow-x: clip` and takes
its height from `100%`, not `100vh`, and the transcript clips its own width. A
wide row in flow otherwise gave the page a horizontal scrollbar; the scrollbar
made `100vh` taller than the visible area, so a second scrollbar appeared and
clipped the fixed controls on the right edge (N132). Wide content — code, diffs,
file views, listings — scrolls inside its own card instead.

**There is no header bar.** It carried a session toggle, a wordmark, a model
dropdown, a GPU chip and an account chip — three disclosures pointing in three
directions, held apart by an auto-margin adjacency hack, with `--bar-height`
measured at runtime from a bar whose height depended on the account name's font.
Each had been placed by local convenience because none of them had a region to
belong to, and the bar was where unowned controls went. The rule now is that
**chrome is emitted by the region it governs**: the rail carries the wordmark,
the New-session button, the filter and sort, and an administrator's way into
the administration page; the composer's status line
carries the model control beside approval and reasoning. The account is the
deliberate exception — it belongs to no region — and is a
circle in the top-right corner (see below). Delete the bar and there is nothing
left for it to hold (D49).

**The New-session button is the rail's primary act, and it is not filled.** It
was a neutral button between a wordmark and a search field, so the one control
that starts something looked like the two that find something; the accent fill
that answered that then made it the loudest thing in the panel, with every other
control in the head a hairline beside a slab. It is a tinted surface with accent
ink — `--tint-accent` and an accent edge, which is the palette's own vocabulary
for a quiet semantic surface — so it keeps a box, and one hue, without the block
of it. `--accent` on `--tint-accent` and on `--tint-accent-hover` are in the
contrast test's pair table because this rule renders both pairs. The hover
deepens the tint by `--tint-hover`, a palette value rather than one fixed step:
how far a hue can deepen before its own ink loses contrast is a property of the
palette. The original three take 16%; gruvbox dark takes 11%, where the pair
measures 4.62:1, and 12% would measure 4.47. The
plus is a drawn path — a typed `+` is the same fault as every other typed glyph
here, at a smaller size — and the sort control beside the search field is drawn
the same way. `button.primary` keeps a hover of its own for the login button:
`button:hover` only moves a border, and on a filled button that border is already
the accent.

**The model control moved down to the status line.** It is a per-turn scope
choice like approval, and reasoning levels are the model's own declaration, so
the model chip and the reasoning chip belong adjacent. `applyAdmin` swaps which
shape is drawn — an operator's chip that opens the models card, or a plain
`<select>` for everyone else, whose only chooser it is because
`/api/models/state` is operator-only (D32).

**The reasoning mark is a meter, not a badge.** The chip carried a sparkle that
never changed, so the word beside it carried the whole state — a mark worth
nobody's glance. It is now one bar per *live* level, filled to the one in
force, with an off level (`none`, `off`) drawn empty: depth is the only axis
the levels have, and a count expresses it without a valence. Deliberately not
the approval dot's three colours. That mapping works because red/amber/green
*is* a risk ordering — `never` stops everything, `yolo` stops nothing — and a
reasoning level is not good or bad, so a hue here would invent a judgement and
spend the three semantic colours where they must keep meaning danger. The same
meter is drawn on every row of the popover, because the list of level names is
otherwise a set of bare words whose order says nothing. `paintReasoning` reads
the ramp off the declared list, which the catalogs order cheapest-first; the
off spellings are the one thing it cannot read there, so the client keeps them
in `OFF_LEVELS` and a test holds that list to the server's.

**The tool chip narrows what the model is told it has.** The third per-turn
scope choice on the status line, and the last mark on it: it sits at the very
right, past the context gauge, because the gauge is a reading and the chip is a
control. What the chip protects is the same budget the gauge measures — every
advertised tool is schema in the window whether it is called or not. It shares a
right-hand group with the token count, and the group carries the auto margin
rather than either child: the chip is hidden for an account granted fewer than
two tools, permanently rather than until a request lands, so a margin on the chip
would leave the count unpinned for that whole account. Its panel lists the
registry from `/api/tools` — granted tools as
checkboxes, ungranted ones struck through with the reason, because a tool that
is simply missing reads as a broken feature rather than an ungranted one.

Unchecking means the tool is **absent** from the next turn: no schema, and no
mention in the system prompt. It is not advertised-and-forbidden, because that
spends context on tools that cannot run and makes a scope the user set depend on
the model obeying a sentence (D50). The selection cannot widen anything — the
server intersects it with what policy grants, and the capability gate re-checks
every call regardless — so this is a view control with no authority in it.

Two states the client must keep apart: `null` is Select all and keeps tracking
the registry as the appliance installs more tools, while `[]` is a user who
switched everything off. Ticking the last tool back on stores `null` rather than
a list naming everything, because those are identical today and diverge
tomorrow. It is per branch and persists until changed, like the working
directory.

## The timeline and its stage

The transcript is a **timeline**: the flow you scroll — user bubbles and
assistant prose — with each reasoning block and each tool call reduced to
**one line**. A run has two kinds of disclosure. The step the turn is *on* is
shown in the run's one **stage** at its foot, so a step change is a crossfade in
a fixed box rather than a card opening and another closing (D62). Every row that
is history — an earlier step, or any row in a reopened session — opens its own
detail **under itself**, at the place the reader is looking (D64).

The reason for the split is that a turn has two readings. *Following the
conversation* wants the prose in a scroller; *watching the work* wants the
current step large, without hunting for it in a wall of output. A second pane
served that badly: the detail lived in a column, competed with the reading
measure for width, and the same node had to move between the column and the row
at a breakpoint. The stage is one home inside the timeline for the live step; it
is not the only home for a card. A detail is in exactly one place — `_mounted`
is `"stage"`, `"inline"` or `null` — and `pressRow` is the one dispatcher: the
live step's row toggles the stage, and every other row opens or closes its
detail under itself.

**The live stage follows the newest step by crossfading it.** A reasoning block
stages itself as it streams and a tool card as it starts, so the thing worth
watching is always the one on screen; the next step fades in over the one before
it, and a turn with three calls *advances* rather than stacking three cards over
the reply. A blocked call stages its card whether or not it is newest — a turn
whose approval buttons are out of view is the N89 shape of bug. **A reopened
session starts collapsed**: `replay` builds every row and its detail's shell but
stages none, and the body is built on first open, so a past session lands with
nothing expanded and pays only for the cards the reader asks to see. Rows built
from history do not play the live arrival animation.

**The stage is one fixed height; a step change does not touch it.** It is
`--step-h` (`min(40vh, 420px)`), the run's live card, and both a thought and a
call's output fill it: the body scrolls — except a picture, which shrinks to the
box (see `fs_image` below) — and a tool card's head and approve/deny row sit
outside the scrollport (D62). A thought that streams for a minute and a
call that returns a page of output are the same box; advancing the run inserts a
one-line row above the stage and moves nothing below it. The step changes by two
140ms opacity transitions in that box. A manual open of the live row toggles the
stage; a manual close folds it over 160ms and follows the tail. A history row's
detail is a disclosure under its row: it opens with the same fixed height, and
the reader's viewport does not move to it — the log's `overflow-anchor: none`
is what stops the browser from moving it instead.

**Prose ends the run, and the stage folds into a reserved blank.** When a token
starts a reply after a run, folding the stage immediately would shrink the
content under the reader. Instead the fold reserves the card's height at the
tail: the blank's height plus the content above it is held constant, the reply
is written into the blank, and the tail follows again only when the reply has
filled it (D64). A short transcript holds its top with a frame-by-frame reserve;
an overflowing one funds the reserve before the card shrinks, because otherwise
the browser clamps the scroll position by the height the card has already lost.
The reply's first bubble is held until the fold lands and is flush with the run,
because the 10px column gap is a height the reserve has not funded yet. `done`
closes a blank the reply did not fill; the turn's last card is otherwise gone,
which is the point — the old stage stayed open as an artifact and the answer
began below it. `prefers-reduced-motion` makes the crossfade, the fold and the
release instant.

The run closes on prose, a note, a user message or the end of the turn, which is
what keeps a paragraph in the middle of a turn from being drawn as part of the
work beside it. **The wait is not in the transcript at all.** `waiting` is a
state, not an event: the rail's phase already says "waiting for the model", and
the composer's Stop button carries the clock. Drawing it as a line made the page
move when it arrived and again when it went (N151). Reasoning no longer cuts the
rail: the acts stay in the order they happened, and the hollow node is what
tells a thought from a call (D44). The rail is centred on the nodes rather than
near them: a node is 8px at the run's left edge, so it centres on 4px, and the
2px bar starts at 3px — the spine meets every dot through its middle. The run is
also the row's grid: every row takes its three columns from the run, so verbs
sit in one gutter, subjects start at one x and outcomes end at one x, and the
list reads down a column instead of across a ragged line. It is set in from the
prose on both sides, and its subjects use `--work` — a neutral set a step back
from the prose's `--text` — so the two registers do not scan alike: the answer
is what you read, the run is what happened.

## Tool cards

A tool call is two elements from one source: a **row** in the timeline and the
**detail** the row opens. The row is the one line a reader scans — the verb a
person calls the act (`Read`, `Search`, `Trash`), the subject (the file, the
query, the command) and the outcome. The audit name (`fs_read`) is **not on the
row**: with the verb and the subject already saying what the act is and what it
is about, the name was a third label for the same fact and it pushed every
subject to a different x. It stays on the element (`data-tool`) and in the
opened card's head — the detail is where the identifier the audit chain and the
manifest use belongs. A `browse` call, whose verb is a function of its
action, reads `Search`, `Open`, `Read`, `Screenshot` or `Eval`: with the name
gone, the verb is the only thing that can say which act it was.

The row's right edge carries the **result**, not the status: the size of what
came back, or nothing while the call is still running. **The node on the rail is
the status** — accent while running, amber while a human is being asked, red on
failure, dim when refused, and a receded blue once it is done — and every one of
those is repeated as a word on the card, so the colour is never the only
carrier. A thinking row puts the word `thinking` in the verb gutter, in the same
column as the verbs, so the one row that is not a call still lines up with the
ones that are — and the verbs wear the accent while that label is dim, so a call
and a thought are told apart by colour as well as by word and node.

The card is the detail; the stage owns its chrome while it is live, and the
inline `.step-detail` box owns it when the card is history: verb, audit name
and subject at full size, then the arguments and the output. The body between the head and
the approve/deny row is the one scrollport, and a result that arrives while the
card is open follows the body's own tail (`growInner`) rather than the
transcript's — the stage's height does not change when it lands. Both row and
card read one state string from **one writer** (`setToolState`), and the
mutations still sit inside the `grow()` every frame handler uses, so a row
written after mount follows the transcript the way any other outline change
does.

**The arguments are named values, not a JSON blob.** `path /srv/eng/x.py` as a
definition list is faster to read than `{"path": "/srv/eng/x.py"}`, and an
argument the client cannot name still shows its own key rather than
disappearing. The field list is the whole of the arguments: the `raw arguments`
disclosure that used to sit under it only restated what the fields and the code
already showed, and the one thing it could show that the fields could not — an
array's contents — is either the code (a write's `content`) or arrives as the
diff when the call is approved.

**Each tool draws its own output.** `fs_list` is a listing, `fs_search` is hits
grouped by the file they are in, a write is its diff, `fs_image` is the picture,
and `browse` draws a page or a list of sites. A tool with no renderer falls
through to the generic view — a numbered file for `fs_read`, plain text
otherwise, in the error colour on a non-zero exit — and falling through is what
keeps an unexpected output (a summarised listing, a failed call) readable rather
than blank. A write's diff comes from the tool's own `structured` half, so it is
the change that happened rather than one re-derived in the browser.

**A `shell` call is drawn as the terminal it is.** Its command used to vanish:
the command is the call's code, so the argument fields dropped it, and a
one-line command was not a file either, leaving a blank card between the head
and the result. The card now prints the prompt the dispatch runs at — account,
`neuman` where a hostname belongs, and the branch's directory, shortened from
the left like the composer's — then the command, with a `>` continuation for
each further line. `neuman` is the appliance's name, not a claim about the
machine's hostname: the browser cannot know that, and a made-up host would be a
worse invention than the name the product already has. A block caret blinks on
the line where the output will land; it is decoration for the eye (the head's
state word says `running` in words) and `renderResult` removes it with the
first result, so a wait cannot outlive itself. The result is printed on the same
inset surface with no gutter or numbering — a terminal's output wraps at the
card's edge, which is the one thing a file view must not do — and a failed
command keeps the surface in the error colour rather than falling back to a
plain red `pre`.

**`fs_image` shows the image, fetched on demand.** Neither the WebSocket frame
nor the reopen projection carries the bytes: a screenshot is megabytes of
base64, and neither every frame nor every reload should pay for one. Both say a
picture exists — `has_image` on the frame and on the display projection's tool
message — and the card points an `<img>` at
`GET /api/branches/{id}/tools/{call_id}/image`, which reads it from the event
log. The route is scoped by `visible_to`, so another account's picture is a 404,
and the model's history still carries the bytes on its synthetic `user` turn,
because a provider accepts `image_url` nowhere else. **The picture takes the
card's remaining height.** The card is one fixed box (D62, D64), so the image is
bounded by the height the arguments and the caption leave rather than by a pixel
cap: the whole of it is on screen without scrolling the card. It is contained in
that space — a small picture is not blown up — and it never widens the
transcript. The cost is scale: a large screenshot is drawn smaller than its
pixels.

**A `web_fetch` response that is HTML is drawn as a page, not as markup.** The
markup used to land in the generic file view, which numbered every line and set
the tags in monospace, with the article itself nowhere readable. It is parsed
with `DOMParser` — an inert tree that is never attached to this page, so no
script runs and no resource loads — walked for its leaf block elements, and
re-emitted as the client's own paragraphs, headings and list items. **No URL
from inside the page becomes a link**; the only links are the call's own, and
`http(s)` only (D45). The extraction is bounded by a block and a character
budget, and the full response stays one click away under `raw HTML`, so the
bound is a view rather than a loss. A response that is not HTML — a plain-text
file, a JSON API — falls through to the text view as before.

**A URL a person might follow is a link.** The `url` field of the arguments and
the card's subject (for `web_fetch` and `browse`) open in a new tab with
`rel="noopener noreferrer"`. The timeline row's subject is deliberately *not* a
link: the row is itself the focus target, and a link inside a button is two
things to click.

**A successful `fs_read` renders as a file**: numbered lines in a dim gutter and
four token classes of highlighting, scrolling inside the card. A failure renders
as plain text in the error colour — a stack trace is a message to read, not a
file to browse.

**A read of a scratch file is titled with the call that wrote it.** Output that
outgrows a tool's preview is persisted to `scratch/<dispatch_id>.stdout.txt`
(D42), and the dispatch id is minted per dispatch — *not* the tool-call id — so
the name says nothing about what the model is reading. The result that wrote the
file carries the paths as `persisted` (D47); the client records each against the
call's own verb and subject, and a later `fs_read`/`fs_search` of the path draws
`stdout of Run “pytest -q”` instead of `6fddbbd2….stdout.txt`. An unknown path —
another session's scratch, a `git` checkout, a result from before the field —
falls back to the filename, which is what it showed before.

**The untrusted envelope is stripped for display.** `wrap_untrusted` fences tool
output so an injected "SYSTEM:" line cannot be read as the harness talking
(D23); that fence is addressed to the model and is noise to a person. Stripping
it here is safe because the server removes any occurrence from the output itself
before wrapping, so output cannot forge a marker to hide behind.

**The approve/deny row is hidden until the server says a human is being asked.**
It appears on `tool_approval_required` and is removed by `tool_decision`.
Drawing it on `tool_proposal` instead meant it appeared for calls nobody was
asked about: under `safe` a read-only call flashed buttons, and under `yolo`
they never went away, because that path emitted no decision at all (N89). The
schema says a proposal "must never be rendered as one", and buttons are exactly
that.

The state line names who decided — `running · you` or `running · auto` — rather
than printing the `consent` boolean, which rendered as `running · false` and
reads as the opposite of what it means.

## Markdown

A reply and a chain of thought are both markdown, and the same renderer draws
both: paragraphs, headings, emphasis, lists, blockquotes, fenced code, links and
tables. **Nodes, never `innerHTML`.** Model output is untrusted (§3) and tool
output quoted inside it doubly so, so every piece of text becomes a text node:
a reply containing `<img onerror=…>` renders those characters rather than an
element. That is also why there is no markdown library — a dependency here would
be a parser with `innerHTML` inside it, in the one tier the design calls
untrusted, and the client is four files with no build step on purpose (N46).

**A table is a block.** The delimiter row is what distinguishes a table from a
paragraph that happens to contain a `|`; the header defines the columns, a short
row is padded and a long one's excess is ignored (the GFM rule), and alignment
comes from the delimiter row as a class rather than an inline style, because the
CSP permits no inline style attribute. The table sits in a wrapper that scrolls
sideways: the document never scrolls sideways (N132), so wide content scrolls
inside its own box, and a table is wide content.

**Fenced code is highlighted only when the fence names a language the small
highlighter knows.** The four token classes are the ones a file view has used
since it was written — comment, string, number, keyword — and a fence naming
`diff`, `text` or any language not on that list stays plain rather than being
confidently wrong: the highlighter is a regex over the common shapes of the
languages here, not a grammar. `HIGHLIGHT_LANGS` is the list of languages it can
speak, and a fence that names `python` gets the same colouring a `python` tool's
source gets.

**The thought is markdown in dim ink, not an italic block.** It renders through
the same path as a reply, which is what makes `**bold**`, lists and tables in a
chain of thought mean something; the trace keeps `--dim` so it stays a step
behind the answer, but it cannot be italic as a whole, because emphasis inside an
already-italic block has nothing left to say.

**A live thought arrives on a reading clock, not a rendering one** (D66).
Re-rendering the block once per animation frame is the right rate for a
crossfade and the wrong one for prose: sixty updates a second is more than the
eye can parse, so every token is a small disturbance instead of a phrase. The
body is fed in whole-word chunks, one every 200ms, and each chunk blooms in over
260ms; the scroll position is eased underneath it on the rendering clock, so the
content moves on one clock and the card's position on the other.

That is why the live path *appends* and never rebuilds. A renderer that clears
its container replays every animation on every update, so a bloom on a rebuilt
block is a card that shimmers instead of settling. So the live path appends
`inlineMarkdown` a chunk at a time, and `settleThought` draws the finished
thought once with `renderMarkdown` — which is where a list, a fence or a table
the live path showed as prose becomes the block the model wrote. For prose
nothing changes at settle, so the swap is not visible. A reopened session fills
the body the first time the row is opened, the same way it defers every other
history detail.

The card itself is read through three choices that only make sense together
with that clock: a 60ch measure, because a line running the card's full width is
a long return sweep on text that is moving; a fade at both edges, marking the
band where a line is both complete and current; and full ink on the paragraph
being written, so the live edge is legible without anything moving. The band is
paint only and waits for the body to overflow, so a short thought is not faded
for nothing.

## The account, a circle in the corner

A round avatar in the window's top-right corner carries the signed-in initials
and opens **Settings**. It replaced a gear glyph, and it is the one piece of
chrome that is not emitted by a region.

It used to open a two-item menu — Settings and Sign out — which put the thing
people came for behind two clicks for no gain. The chip opens the dialog
directly now, Sign out is the last item in the dialog's sidebar, and one
floating surface is gone (D51).

The gear was a *typed pictograph* while everything around it is drawn — the rail's
grip is a path, the composer's icons are inline SVG — and
U+2699 is a character whose weight, baseline and colour the appliance does not
decide: a font stack is free to substitute an emoji face for it, which is the
failure the composer's icons were moved to SVG to avoid. And it named neither of
the two things it led to. A gear is a picture of "settings"; what has a name, a
policy and a session to end is *the account*.

**That rule is written down in more than one comment and broken in several
places** — see "Glyphs" below. The gear was one instance of the general problem,
not the problem.

The circle is the one control with **no region to belong to**: nothing in the
transcript, the rail or the composer owns "who am I". Every other control moved
out of the header to the region it governs; this one has none, so it keeps a
corner, and the shape does the explaining — a circle carrying initials reads as
the signed-in person, where a rounded rectangle beside the name read as one more
button. The face shows initials rather than the name because the face has to stay
one size for every account; the whole name is in `title` and `aria-label` — and
spelled out in full inside the dialog, which is the one place it is actually
written.

## Settings, a centred modal

Five sections — **Interface**, **Sessions**, **Assistant**, **Permissions**,
**Usage** — chosen from a column of tabs on the left, with the account's name
above them and Sign out pinned below.

**It is not a corner card, and not a column.** It was both, in that order. A
resizable full-height column was more furniture than a reading needed; a 360px
card anchored under the chip was right while the content *was* one read-only
policy list. Several sections need a sidebar and a body, and a 640px dialog hung
off a corner control falls off a narrow window. So it is centred — by `inset: 0`
plus `margin: auto` rather than a translate, which would land a focused control
inside it on a half-pixel and blur it — and bounded with `min()` against the
viewport so it shrinks instead of overflowing.

It deliberately does **not** fill the window. This is opened mid-conversation,
and the transcript staying visible around it is how you keep your place.

**A scrim makes it modal rather than merely on top.** The generic click-away
loop below infers dismissal from "the press was not inside any listed card"; a
real element is better here on both counts — the press has a target instead of
being inferred, and the scrim blocks it from reaching what is underneath. Two
other handlers had to learn that, and both were real bugs: type-anywhere would
focus the composer *behind* the scrim, putting the caret somewhere invisible;
and the click-away loop would collapse the rail on the same press that dismissed
the dialog. Both return early while it is open — one press, one dismissal.

**Interface** holds the theme and the reasoning default. Both belong to the
*account*, not the browser: they are PATCHed to `/api/me/interface` and ride
back with the identity on every sign-in (D53). That is deliberately the opposite
of where the rail's width lives. That is `localStorage` because it describes
one browser and syncing it would let one tab change another's reading; neither clause is true of a theme, and the server has to be
able to write these for a future `customize-neuman` tool to exist at all. The
administration page has no picker of its own and applies the same theme from the
identity it signs in with.

The theme picker is a row of swatch buttons rather than a dropdown — a colour
scheme is a thing you look at, and a `<select>` hides every option but one behind
a click. Each swatch paints from the tokens of the theme it *names*: every
`:root[data-palette="…"]` block also matches `.swatch[data-palette="…"]`, so a
sample cannot drift from the scheme it samples. The change is applied
optimistically and reverted if the write fails, which is the one place in this
client that is right — a theme's whole purpose is to be seen immediately, and a
round trip before the colours move reads as a broken control.

The reasoning preference governs *staging only* — the row is still built and
still filled, because a preference that suppressed it would hide the fact that
the model thought at all, and a history row still opens when pressed.

**Sessions** holds one number: **Keep sessions active for** — how long a session
stays in the working set after it was last opened or had anything said in it
(D63). Its own section rather than a row in Interface, because it is about which
sessions are still around rather than about how the client looks, and because
the answer is a number with a unit rather than a switch. It belongs to the
*account* for the same reason the theme does: it says how this person works, so
it follows them to another machine — and it decides the same list there, because
the rail's membership is the core's. Locally it also decides how long this
window keeps a transcript and its socket warm, which is memory rather than the
list. The field writes on change rather than per keystroke, because half-typed
numbers are not settings; a cleared box returns to what is in force rather than
becoming 0,
which is a real value ("keep nothing") and not the absence of one. 0 is also the
one value the client acts on immediately rather than at the next sweep: the
window is the whole feature, so its smallest value has to close a session as the
reader leaves it.

**Assistant** holds the account's own standing instructions for the model — the
middle prompt tier between the operator's text in the administration page and a
directory's `AGENTS.md` (D65). They are text, not a control, so they are
written on an explicit **Save** rather than on every keystroke, and the button
is disabled until the box differs from what is stored. The counter beside it and
`maxlength` on the box are the same 2,000 the server refuses beyond; four
places name that number and a test holds them together. The pane is additive:
the text becomes one more section of the system prompt and cannot replace it,
so it cannot drop the identity or the untrusted-output rule, and it grants
nothing.

**Permissions** is a reading, never a control. Tools are deliberately absent:
the composer's tool chip is where they are seen and chosen (D50), and the old
policy list rendered the same grant twice. What is left is reach in a user's
terms rather than policy's field names — where you can read and write, which
hosts `web_fetch` reaches, what bounds a turn — with the layer that granted each
value beside it, because "why can I do this" is the follow-up question. The one
policy field a user authors for themselves is `auto_accept`, and that is a chip
in the composer beside the decision it governs.

**Usage** is today only, from `GET /api/usage`. The cap resets daily, so a week
of totals would be a different question with a different denominator. Settled
and reserved are shown apart and never summed: a reservation is released when a
turn fails, so one combined figure would visibly fall back down and read as
broken accounting. An uncapped account gets no bar rather than one against
infinity.

### Dismissal, and saying so

Three things have to agree about every card a control opens, and they now read
from one list, `CARDS`, rather than from a copy each:

- **A press anywhere else closes it**, and a press on the control does not — the
  control already toggles, so closing on the way down would make the next press
  reopen what it just closed.
- **Escape closes it.** It used to close the Models window and nothing else, so
  the sessions list, Settings and both path choosers could be dismissed by
  clicking the transcript and by no key at all, which is the one dismissal a
  keyboard user has. Escape yields to the choosers, which own their own, and takes
  the row menu first, because that floats above everything.
- **`aria-expanded` says whether it is open**, on the control. It is written by a
  `MutationObserver` over the cards rather than at each place one is opened or
  hidden, because there are a dozen such places — both sign-in paths, every
  toggle, the click-away loop — and the attribute written at the call site is the
  one a card added later will not get. Settings is in the observer's list but
  deliberately **not** in `CARDS`: the chip toggles it, so the chip owns the
  disclosure, while its dismissal is the scrim's — that loop can only hide one
  element and would leave the scrim up, swallowing every click afterwards.

The rail is **not** in `CARDS`. It is driven by `rail-collapsed` rather than by
the `.hidden` class the cards use, so a press away or Escape puts it away through
`setRailCollapsed`, next to the rail, rather than through this list.

## The palette

**The setting is called Theme; a theme is a palette.** The picker says *theme*
because that is what a person chooses, and the stylesheet says *palette* because
that is what a theme is: a `:root[data-palette="…"]` block of semantic tokens.
The attribute, the API field and the column all keep the second word, which is
why this section does too. The tokens are `palette.css` — the one file both
documents load, and the only file allowed to carry a colour literal. `neuman.css`
is the chat window's layout and `admin.css` the administration page's (N158).

Every colour in either layout stylesheet comes from a semantic token, and a test
(`test_no_rule_in_the_stylesheet_carries_a_colour_literal`) fails the build if a
rule in `neuman.css` or `admin.css` carries a literal. That rule is the whole
system; the rest follows from it.

**Tokens say what a colour is for, not what it is.** `--surface-raised`,
`--ink-on-accent`, `--tint-bad`. The alternative is the base16 shape — 16 or 24
indexed slots that elements get assigned to — which exists because a terminal
has no semantics, only ANSI indices. It cannot express the thing that matters
here: contrast is a property of a *pair*, ink against a surface. Named roles let
one table of pairs check every palette at once; indices would make each new
theme re-derive which combinations are load-bearing, which in practice means not
checking them (D53).

**A theme sets the roots; everything else is derived.** Hovers, tracks, the
four tinted surfaces and the window-chrome washes are `color-mix` over those
roots. Adding a theme is filling in a form, not redesigning. Two derivation
controls carry the per-palette direction:

- `--lift` is the colour a surface moves *toward* to read as hovered: white on a
  dark palette, black on a light one. Flipping that one token reverses every
  hover in the file — there is no `@media` branch and no second set of rules.
- `--shadow` is the channel triplet a shadow is drawn in, as bare `R G B` so
  each use sets its own alpha. Pure black is right on dark and reads as dirt on
  white.

Two values constrain a derivation rather than describe a colour: `--tint` is how
much of a semantic hue a resting surface takes, and `--tint-hover` how much it
deepens under the pointer. Both are held where the hue stays legible as ink on
its own tint, and that ceiling is not the same in every scheme — gruvbox dark
reaches it at 8% and 11% where the original dark theme can afford 11% and 16%.

**What the literals used to cost.** This file had 35 hex literals in rules plus
11 hardcoded black shadows. `#22262e` appeared seventeen times as the hover
surface the palette had no name for; `#253048`, `#2a1d20`, `#2a2419` and
`#4a4030` were hand-mixed tints duplicating what `--wash`/`--edge` already
expressed declaratively. Every one was correct in the palette it was sampled
from and wrong in any other — invisibly, until someone switched theme.

**Seven themes.** Dark is the default. Light is not an inversion: the hues come
down in lightness to stay legible on white, and the surface *order* is preserved
rather than flipped, because the order states depth rather than brightness. Nord
is a mapping rather than a reproduction — its published `nord11` red measures
3.05:1 on `nord0`, and `--bad` carries error messages, so the red and magenta
are lightened and the surfaces sit a step below the official Polar Night.
Everforest and Gruvbox are mappings for the same reason. Everforest is the
**hard** background variant in both modes; its light `fg` measures 5.40:1 on its
own bg0 and its light accents between 1.9 and 3.3:1 on the card, so those inks
are darkened while the hues are kept. Gruvbox's bright red measures 3.37:1 on
dark1 and its bright blue 4.31:1, so both are lightened; its light yellow and
green measure 3.4:1 and 4.3:1 on the card and are darkened. An unreadable error
message is a bug, not a theme.

**Contrast is asserted, not eyeballed.** `test_every_palette_is_legible` resolves
each token (following `var()` and `color-mix`) and checks the full table of
ink/surface pairs per palette at WCAG AA: 7:1 for prose, 4.5:1 for other text,
low floors for borders and inert dots, which must be *visible* rather than
readable. A pair that
the stylesheet does not actually render is deliberately not constrained — the
cost of an invented constraint is a palette distorted to satisfy it. If the
resolver meets a form it cannot evaluate it fails loudly rather than skipping,
because a test that silently checks less is worse than no test.

**Adding a theme** means five places, and a test asserts they agree:
`:root[data-palette="…"]` in `palette.css` (plus naming `.swatch[…]` in the same
selector, so the picker's sample comes free), `PALETTES` in the client, a button
in the settings markup, `PALETTES` on `AccountStore`, and the CHECK constraint in
a migration. A theme added to four of the five is a button that 400s, or a theme
nobody can reach.

## Glyphs

This client has a rule, stated in more than one comment: **an icon is drawn, not
typed.** The composer's icons are inline SVG, the rail's fold grip is a drawn
path, and the reason given is that a typed character renders at a different
weight, baseline and colour in every font the appliance
might fall back to — and on some platforms arrives in full colour whatever the
theme. (The header's CSS-bars hamburger was once the other example; the header is
gone and the rail's grip is an SVG.)

Two of the characters those comments name are **no longer in the code at all**:
the paperclip was replaced by a plus, for a reason that was never about rendering
("a picture of a physical act nobody performs"), and Stop became a drawn rect. Both
now survive only as prose, in `index.html` and in this README, which is how the
rule stopped being enforced — the examples that justified it had gone, so nothing
pointed at what it forbade.

Eight typed pictographs are live, in seven places:

| Where | Character | What it is |
|---|---|---|
| `chooserRow`, `.path-icon` | `U+1F5C0` | the directory row icon, both choosers |
| `chooserRow`, `.path-icon` | `U+1F5CB` | the file row icon |
| `chooserRow`, `.path-check` | `U+2713` | the mark on the chosen path |
| the attachment chip, `#settings-close` | `U+2715` | removing an attachment, closing Settings |
| the session row's `.more` | `U+22EE` | the row's action menu |
| `paintCrumbs` | `U+2039`, `U+203A` | back and forward in the chooser's crumb bar |
| `paintCrumbs` | `U+21B0` | step out of a directory |

The table names the function or element rather than a line number, because the
line numbers it used to carry had drifted twice — a reference that points at the
wrong line is worse than one that names the function it lives in.

The folder is the one that matters. `#cwd-pick` in the composer is a **drawn SVG
folder**, and the chooser rows carry the same glyph as a pictograph a few hundred
pixels away in the same view — so the client draws a folder and types a folder, and
a reader comparing them is looking at the rule being broken on one side of it.

What is *not* established here is how any of the eight actually render on the
appliance's font stack. That has not been measured, and the case for changing them
is consistency with the rule rather than a screenshot of them misbehaving. The fix
is small: each becomes a path, and `#cwd-pick` already has the folder's.

## The approval picker

A **chip in the composer's footer** — a coloured dot and the mode name — opening
a menu of three buttons: red `never`, amber `safe`, green `yolo`. Colours
*beside* the mode names, not instead of them: the colour is the faster read, but
those three words are what the audit chain, `neuman-admin policy show` and the
policy view all say, so dropping them would make all three unsearchable from the
UI. The dot reads as "how much runs without asking" rather than as good/bad —
`never` stops everything, `yolo` stops almost nothing.

It lived in Settings, which became two clicks away when the gear turned into the
account chip. That left the one setting about a person's own attention as the
hardest thing in the UI to find, and invisible at the moment it matters: you
want to know you are on `never` *before* sending, not when the first card
appears. The footer is where the decisions are made.

**Not in the Models card**, though that was the first instinct. That card is
operator-only (`#gpu-toggle` is hidden unless `is_operator`), and approval is a
per-user preference — `PUT /api/policy/auto-accept` needs only an authenticated
session. Moving it there would have removed the control for every non-operator.

One renderer paints both the chip and the menu, so the mode on screen and the
mode in the list cannot disagree. Picking one closes the menu before the request
rather than after: `loadPolicy` repaints the choices inside it, and a menu that
redraws under the pointer invites a second click on a button that has moved.

This is the one policy field a user may author for themselves, and what makes
that safe is `auto_accept_max` — the operator's ceiling. A mode above it is
drawn *disabled* rather than hidden, so the limit is visible rather than
mysterious, and the server refuses it independently: the picker is a
convenience, not the check.

`PUT /api/policy/auto-accept` goes through `decision.policy.ops` like every
other policy mutation, so it is validated, audited and generation-bumped by the
same code — a user lowering their own guardrail is exactly the sort of decision
the chain exists to record.

**It is not in the Models window**, which is worth stating because the two look
like neighbours: both are "how the agent behaves". It was proposed for the top
of that card and three things rule it out, the first two structural rather than
aesthetic.

- The Models card is **operator-only** — `#gpu-toggle` is hidden for everyone
  else, because GPU capacity and the model inventory are appliance topology —
  while `auto_accept` is the one policy field a *user* authors for themselves.
  Routing it through an operator is what made the appliance tiring in a way policy
  did not require, so putting the control behind an operator-only surface would
  undo that for every ordinary account.
- The card is **polled and rebuilt**. `watchGpu` reads `/api/models/state` every
  five seconds while it is open, and `renderModelPanel` clears its body on each
  read. A preference drawn inside it would be destroyed and redrawn under the
  pointer, and the only thing that could put it back is `loadPolicy` — two places
  deciding one control, which is the disagreement D32 exists to remove.
- The card answers a **per-model** question. Reasoning level is in it because a
  level is declared by the model that is being chosen; `auto_accept` belongs to
  the account and is capped by the account's policy, so no row of that list could
  own it.

## The context bar

Below the composer, under the token count: a fill bar showing how much of the
model's context the last turn used. `prompt_tokens` over `context_tokens`, both
from the `usage` frame.

**One short figure sits above the bar, a size down — and none where there is no
price.** What is there is the one number the gauge cannot show itself: the
branch's spend, where the model has rates. A model with none — a local one —
gets no caption, and the gauge stands alone. The reply's token count (`↓2.4k`)
was the caption in that gap for a while, on the argument that output tokens are
the only cost a free model has. It answered a question nobody asked: the bar is
the reading, and a lone count above it, sharing its line with no other figure,
reads as another control on the status line rather than as the bar's caption.
Whether a model is local lives in `/api/models/state`, which is operator-only
appliance topology, but the client does not need it — `session_cost_usd` is sent
only when the step could be priced, so an empty caption *is* the local case. It
is centred on the bar rather than flushed to its right end: the block is exactly
as wide as the gauge, so centring the text in the block centres it over the
fill, which reads as a heading over the gauge rather than as a label hanging off
one corner of it.

The first shape of this was `↑92k ↓2.4k · $0.0142` above the bar, and it was
wrong twice. It is 120px of text over an 80px gauge, so the caption was wider
than the thing it captions and won the eye against it. And `↑` is the bar's own
numerator — the fill *is* `prompt_tokens` over the compaction point — so the
caption repeated in characters what the bar says in length. The whole
`23,600 in · 2,400 out · 26,000 total` reading is on the caption's `title`, one
hover away, where the exact figures have always been. On a local model there is
no caption to hover: the bar's own title carries the context figures, and the
turn's reply count is not on screen at all.

It is deliberately small, and it is diagnostic rather than decorative. Two
sessions ended mid-task on a context the server had no number for — the manager
trimmed history against a default six times the real window, so compaction
never fired and the provider ended the turn (N86). A count with no denominator
could not have shown that coming: 12,000 tokens is comfortable on one model and
fatal on another.

**No window, no bar.** When the operator never recorded a context size the
frame omits it and the bar stays hidden, rather than drawing against a guessed
ceiling — which is the same mistake in a more reassuring form. Two thresholds
(70%, 90%) rather than a gradient, because "fine", "filling" and "about to
fail" are the states worth telling apart.

The figure describes the turn that just finished, not the one being typed: it
is the provider's own count for the last request, which is the real number
rather than an estimate.

**What a session has cost** is the caption when the model has rates: a running
USD total for the conversation, from `session_cost_usd` on the
`usage` frame. A `~` prefix means the figure was computed from the operator's
declared rates rather than billed by the provider — OpenRouter reports what it
charged and is shown exactly, DeepSeek reports none and is priced from its
cache-hit/miss split (D37). A local model with no rates shows nothing at all,
because "free" and "not priced" are different claims and `$0.00` is the one
that gets believed. Sub-cent totals keep four decimals for the same reason.

## The protocol

One `case` per message type in `contracts/schemas/ws.schema.json`. The client
sends `message`, `steer`, `approval`, `interrupt`; the server sends `token`,
`reasoning`, `user_message`, `tool_proposal`, `tool_decision`, `tool_result`,
`tool_approval_required`, `usage`, `waiting`, `truncated`, `compacted`, `error`,
`done`.

The `usage` frame carries `context_tokens` alongside the counts when the
resolved model's window is known, which is what the context bar is sized
against.

Two choices worth knowing:

- **An unknown type is rendered, not dropped.** A client that silently ignores
  what it does not understand hides a protocol change behind a blank screen.
- **User messages are drawn from the server's echo, never optimistically.** The
  server echoes every one, so drawing on send as well doubles each line — which
  the first version of this file did, behind a check for a field the contract
  does not have.

The same input box steers mid-turn and sends between turns (D12: the WebSocket
posts into the session inbox and the harness drains it). One box for both is
the point — an agent you can correct while it works does not need stopping.

## The composer

**One drawn surface, not a row of widgets.** The border, the fill and the focus
ring belong to `#composer`; the textarea inside is transparent and borderless
and the buttons have no box of their own. `:focus-within` is what moves the ring
— the textarea has the focus, the field shows it, which is what anyone looking
at it believes is happening anyway.

The icons are **inline SVG**, for the reason every icon here is drawn rather than
typed: 📎 and ⏹ render at a different weight, baseline and colour on every
platform, and one of them arrives in full colour whatever the theme.
`currentColor` then lets one icon carry each button's state — dim at rest, the
attach hue, red while stopping — with no second asset.

Attach is a **plus**, not a paperclip: it means "add something to this message",
and the clip was a picture of a physical act nobody performs. It keeps the warm
hue that maps it to the menu it opens (see the window-hue system below) but
loses its border, because a bordered chip inside the field is the boxed-widget
look this layout exists to remove.

The Steer label moved from the glyph to the *name*, and it was a latent bug:
`setBusy` used to write `textContent = "Steer"` on Send. With an icon inside, that
deletes the icon and leaves a bare word. The arrow is right for both acts anyway
— sending and steering are one gesture into one inbox — so the `title` and
`aria-label` change and the glyph does not.

Send dims (`.ready`) until there is something to send. The word used to carry
that by changing; a lone arrow at full contrast over an empty box invites a
click that does nothing.

The **folder** sits beside the plus, because both say what this message carries
with it. The working directory used to have its own strip above the composer,
which separated "the file I am attaching" from "the directory I am working in"
by a horizontal rule neither of them asks for. What remains above the field is a
*status line* — the path, which is itself a control opening the same chooser as
the folder button — and it is hidden entirely when nothing is set, rather than
stating a default nobody chose. Clearing the directory is the chooser's footer,
not a ✕ beside the path: see **The working directory** below.
The folder wears its hue only once a directory exists, so a resting composer has
one accent (Send) rather than two.

## Typing goes to the composer

**The box is not a thing to be clicked into.** While the window has focus it is
where typing goes, the way a terminal's prompt is: click and highlight the
transcript, scroll it, open a tool card's disclosure, and the next sentence still
starts where you left off. Every one of those gestures moves focus off the field,
and every one of them used to cost a second click before anyone could keep
writing.

The keystroke is **not consumed**. The listener moves focus to the textarea and
lets the browser's own default action land the character there, at whatever caret
the box was last left at. Appending it by hand would be a second text editor
living beside the real one, and it is the version that drops a key, an IME
composition, or a paste.

The whole of the feature is therefore its exclusions, because a listener that
claims a key from the wrong place is worse than none:

- **A field that takes text keeps its keystrokes** — the composer itself, a
  rename box, a chooser's filter, the login form. `takesText` names that set once,
  because `chooserKeydown` asks the same question about the same elements and two
  copies are how they come to disagree.
- **A modifier makes it a command**, not a character, so `Ctrl`, `Cmd` and `Alt`
  keystrokes are left to the browser — with one exception. **`Cmd`/`Ctrl+V` is
  input, not an act on the page.** Someone who has clicked into the transcript and
  pastes means "put this in the message", the same as typing it, and a paste that
  vanished because focus had moved would be the feature failing exactly where it is
  most wanted. Every other modified keystroke has a meaning the page does not own.
- **An IME mid-composition is left to the IME** (`isComposing`, `keyCode` 229);
  moving focus would strand the text being composed.
- **Space over something that activates** — a menu button, a disclosure's
  summary, a list row — still means "press this". It is the one printable key with
  a default action on a control, and no letter competes with that meaning. Enter,
  the arrows, Tab and Escape are not characters and already mean something where
  they land, so they are left by shape rather than by name.
- **An open chooser owns its keyboard.** It already turns printable keys into its
  own filter; jumping to the composer under it would be two things on screen
  answering one key.
- **A hidden composer takes nothing.** That is the login surface, where the box is
  not on screen and a character would land nowhere a person could see.

`Backspace` and `Delete` come along. They are edits rather than typing, but they
are the edits someone reaches for first, and the same trip back to the box should
carry them.

**Up and Down walk the user's own messages.** Up from the box brings the last
user message to the top of the transcript, further Ups walk back, Down walks
forward, and Down past the newest returns to the tail — the other end of the axis
the `jump-live` arrow is on. Only the first Up is gated — caret at offset 0,
nothing selected — so a multi-line draft keeps its own caret movement; once the
cursor is active the arrows belong to it, the textarea is neither read nor
written, and the draft survives the trip. Down from the draft is left alone,
because there is nothing newer than the draft to walk to. Clearing `followTail`
is what keeps a streaming reply from dragging the reader back to the bottom
mid-review, a `current` ring marks the message the cursor is on, and an edit, a
blur, or Escape ends the review — Escape returning to the tail.

## The choosers

Neither picker opens the OS file dialog, and that is **not** a UI choice
(gotchas N57). A browser never hands a page an absolute path — `<input
type="file">` gives bytes and a bare filename — while an attachment here is a
**path** and a working directory *is* an absolute path the server stores. An
upload would also have nowhere to land: `~/.local/share/neuman/` is outside
`home_data_dirs` so `within_reach` is false, the server runs as `neuman` and
cannot write into a `0700` home, and no tool accepts bytes. The chooser produces
real paths because the listing is a dispatch to the client, which runs as the
user on their own machine.

Each chooser is a **type-ahead field over three panels**. The middle panel is the
directory you are looking at, the left is where it sits, the right is the
contents of the row you have selected there. The panels are the answer to the
complaint the old single list earned: entering a folder was a 22×18 chevron at
the far right of a 750px row, and pressing the row's *name* committed it as the
working directory and closed the menu instead. Two menus that looked alike also
behaved oppositely — the attachment chooser's folder rows opened on a press
while the directory chooser's committed — because each had grown its own answer
to "what does a click mean here".

**A press opens; the chip on the row you are on takes.** A press on a folder
makes it the centre panel — the thing a file manager's single click has always
done — and the small `use` chip where the old chevron sat is the one control that
*takes* a row, as the working directory. It is drawn only for the row the pointer
is over or the keyboard has selected: a `use` button on every line competes with
the press that already opens the folder, and the reason to take a row is exactly
the moment you are looking at it. The space is reserved whether or not it is
visible, so revealing it cannot reflow the name beside it (N53's trade, again).
In the attachment menu the same slot reads `attach` on a file and holds a blank
spacer on a folder, because a folder is not something a session can attach —
and the spacer keeps one right edge across a listing of both kinds. Enter takes
the highlighted row, and so does the button in the crumb bar, whose label names
its target: `use this folder` for the directory the crumb names, `use contracts`
for the row highlighted beside it.

**The card is one height.** It used to be `max-height`, so it grew and shrank
with whichever panel held the most rows: a folder of two entries next to a
preview of a ten-entry one dragged the whole box — and the path you were reading
— up and down under the pointer. The height is fixed at `min(340px, 38vh,
--headroom-cap)` at every depth and every selection, and each panel scrolls its
own rows inside it. The cost is empty space in a short directory, which is much
cheaper than a window that moves while you aim at it.

**The pointer is what previews.** With a press committed to opening, something
else has to fill the right-hand panel or the third column is decoration for mouse
users. Pointing at a row is the only gesture left that can mean "tell me about
this without doing anything", so it does: a row rested on for 200ms becomes the
selection, its contents appear on the right, and the crumb button starts naming
it. Movement arms the timer and *rest* is what fires it, so sweeping across a
directory dispatches nothing; `mousemove` rather than `mouseover`, so a listing
that appears under a still pointer does not select itself and spend a listing
nobody asked for. The keyboard drives the same selection, and `selectRow` brings
it into view — the panel scrolls and the keyboard does not, so arrowing past the
panel's height used to walk the highlight out of sight.

**Up is not back.** `↰` steps out of a directory; it cannot return to the sibling
you were just in. So the head of the path carries the pair every navigator has —
back and forward, per open menu, with Alt+Left and Alt+Right for the keyboard. A
step taken *through* the history is not recorded, or Back would push the place it
just left and Forward would never become available.

**At a granted root, the left panel shows the roots.** A root has no parent in
policy, so the panel above it used to go blank at exactly the depth a first visit
starts. The level above a root is the other granted directories, which is also
what `↰` returns to.

**The keyboard takes the caret with it.** A filter typed a moment ago keeps its
claim on Left and Right until the first Up or Down: that keystroke moves focus
out of the field and onto the list, so the arrows are the menu's again — Left
climbs, Right opens — and a key typed from there is routed back into the field.
Without it, narrowing a list was the one thing that stopped you walking out of
it: the text that got you somewhere held the arrow keys that would take you on.

**Every panel is a place to go.** The right panel's rows are pressable like the
left's — a press makes that row the centre panel, with the folder you were
previewing becoming the context on the left. It had been inert: a preview you
could read but not act on, and the only rows in the menu that did nothing when
pressed.

**It costs one listing per new directory.** That is the whole reason this shape
was affordable. A listing is a dispatch to the acting user's client, not a local
read (`/api/browse` → `fs_list`, and the same reason again as N57), so three
panels could have meant three dispatches per step. They do not, because the new
centre is always the panel that was already on screen: stepping into a row
listed on the right costs nothing, and stepping back out costs nothing, because
the parent was the centre a moment ago. `listDirectory` keeps one map per open
menu and one request in flight per path, and the roots level costs nothing at all
because policy named it. The previews are the only new request a visit can add:
the pointer's waits for a 200ms rest and the keyboard's for 90ms, and a directory
listed once is never fetched again while that menu is open. The cache is dropped
when the menu opens: a directory created a moment ago has to appear next time,
and a picker that lies about the disk is worse than one that re-lists.

**The motion is one transform.** The centre panel arrives from the side you came
from — 12px and a fade, 140ms, `transform` and `opacity` only, and nothing at all
under `prefers-reduced-motion`. The panels do not rotate and the surface does not
blur: `backdrop-filter` over a live transcript re-blurs on every frame of the
glide, and `rotateY` foreshortens the column you are stepping back into by about
a tenth, which is exactly enough to truncate the name you are trying to read.
The sides recede through opacity and a 0.94 scale, which costs nothing and dims
nothing that is needed.

- The field replaces an invisible filter. Printable keys were already captured
  and matched against rows, with nothing on screen showing what had been typed —
  the list just got shorter and Backspace was the only way to find out why.
- It takes a **whole path**. `/srv/eng/docs` and Enter goes straight there,
  instead of a click per level, which is what makes a deep tree bearable. In the
  file chooser a trailing slash navigates and anything else attaches.
- A typed path filters on its **last segment**. Matching whole rows against the
  full string would empty the list at the first slash — the one keystroke that
  proves the person knows exactly where they are going. The first survivor
  becomes the selection as you type, so three letters and Enter is a complete
  act, and the right panel shows what Enter will take before it takes it.
- Nothing is validated client-side. A typed path goes where a clicked row goes:
  `/api/browse`, or the PATCH route where `within_reach` lives. A guess about
  reach here would be a second opinion about policy.

The field is a **sibling** of the panels, not inside them. The panels clear with
`innerHTML = ""` on every navigation, so a field in that subtree would be
destroyed mid-keystroke — losing focus, the caret, and the text, on the very
keystroke that caused the navigation. For the same reason each panel's rows
scroll and the menu does not: a filter that scrolls off the top of the list it
filters is worse than none.

The field also **keeps the caret**, and the selection is drawn rather than
focused. Rows no longer take focus, so a press cannot leave the keyboard pointed
at nothing, and typing after a press still lands in the filter — which is what
used to need a document-level handler to route the keystroke back. `aria-selected`
marks the row the button would take, which is what a listbox means by it; it used
to mark "the directory currently in force", which was a different fact wearing
the same attribute. The crumb's path is wrapped in `<bdi dir="ltr">`:
`direction: rtl` is what clips a long path's head rather than its tail, but on a
path that fits the bidi algorithm moved the leading slash to the far end, so the
crumb read `home/alice/projects/` (N153).

## The draft

Signing in lands on a **draft**, not a session: a heading with the message box
directly under it, centred, and no row on the server. `sessionId === null` is
that state. The first message is what creates the session — `POST /api/sessions`,
then a `PATCH` for anything staged, then the socket, then the frame.

The heading and the box read as one object, the way they do in ChatGPT and
Claude: an invitation and the thing you answer it with. A box pinned to the
bottom of an empty window reads as neither. `body.drafting` is what moves it —
the log row collapses to `0`, the composer takes the whole window and centres in
it with `align-self: center`, and the heading hangs above the card out of the
flow so that captioning the box does not shift the centring. The composer drops
back to the floor the moment there is a transcript. Nothing about the card's own
styling changes between the two states: it moves, it does not become a different
thing.

**The box is what gets centred**, not the pair. While the heading occupied a
real row it pushed the flexible track down by its own height, so the box settled
about 27px below true centre — enough to look like a mistake rather than a
choice. `align-self` rather than a `vh` margin for a second reason: a margin
fixes the *bottom* edge, so every new line typed would push the box upward
instead of expanding it around its middle.

Centring the composer is also what made the upward popovers wrong, and the fix
belongs to this state rather than to them. Each had a `vh` cap, which bounds the
room above the composer only if the composer is at the bottom of the window;
here it is at the middle, and the tool chooser ran off the top with its first
rows unreachable (N134). A `ResizeObserver` now publishes `--headroom` — the
measured distance from the top of the viewport to the top of `#context` — and
every panel anchored to that edge caps itself against it. The observer watches
`body` as well as the card, because a shorter window moves a centred card's top
edge without changing the card's own box. It cannot see the *mode* change,
though — entering the draft moves the card with every box the same size — so the
two places that toggle `drafting` re-measure directly, or the cap stays at the
room a session had and the tools panel opens past the top (N138).

Spanning the composer across both grid rows was the first attempt and does not
work — `grid-row: log / composer` ends at the composer row's *start* line, so
that track collapses to 0px and the card bottom-aligns into the log row on top
of the heading.

This replaced a boot path that POSTed `/api/sessions` on load. Nothing ever
deleted those: the only `DELETE` in the store requires `closed_at`, which an
abandoned session never gets, so **every page reload left an immortal untitled
row** and fifty reloads pushed real conversations out of a list that shows
fifty. The hero is as much a fix for that as it is a nicer way in.

It also gives removing the active session somewhere to land, which is what the
old "the current session is never hidden from itself" exemption existed to
paper over. Remove the session you are in and you are back at the draft.

A working directory or an attachment chosen *before* the first message is staged
client-side and PATCHed on immediately after creation — through the same route a
live session uses, because that route is where `within_reach` lives and a
second copy of the "hint, not a grant" check is how it drifts.

**Nothing is written into the transcript before the first message.** The log row
is zero-height in this state, so text appended to it is not merely out of place:
it is clipped to a sliver at the very top of the window, above the heading —
which is where "Using …" landed every time a model was chosen from the panel.
`note` therefore draws nothing when there is no view to draw into, and feedback
about something staged belongs to the control that did it: the models panel
repaints from the state it has just read, and the path and attachment chips are
written by `showWorkingDir` and `showAttachments`. `saveWorkingDir` already
returned without a note in the draft for the same reason; this is that rule at
the one place every other note goes through. The guard is `renderView` rather
than `drafting()` because a background branch draws into its own holder while
the draft is open, and its "Disconnected." still has a transcript to land in.

Reloading returns to the draft rather than the last conversation. Landing on the
last one would mean remembering an id across reloads — navigation state in
`localStorage`, and a session that reopens itself without being asked. The list
is one click away.

## Active sessions

**A branch with a turn in flight keeps its socket when you look elsewhere.** The
client opens one WebSocket per branch view it has open, not one for the window.
Clicking another row used to close the old socket, and the server cancels a turn
in the socket's own `finally` — so a switch was a Stop with nothing on screen to
say so. The socket now stays open and the frames keep arriving; each view owns
the transcript it is drawing, and a hidden one draws into a detached holder.

**Nothing is replayed when you come back.** A running view is pinned to its live
edge: the tool card, thinking card or answer that is happening now, with the
history above it. An idle view restores the scroll position it was left at. The
nodes are moved into `#log` rather than rebuilt, so the live stage, an open
inline detail, the tail reserve and the streaming text survive the switch — a
replay would flash the last complete reply or lose the reader's place.

**Active sits above Pinned, and it is the account's list.** A session is in it
while a turn is running in it, while a reply is newer than the last time
somebody looked at it, or while it was opened or last had anything said or done
in it inside the window the account set — **Keep sessions active for**, 30
minutes by default, 0 for keep nothing, changed in Settings › Sessions. The core
answers it (`GET /api/active`), so every window and every device draw the same
rows in the same order; the only thing this window adds is the word `open` for
the session it has on screen, and `idle` for the rest. That is the working set
rather than an arrangement of history, so it comes first. **The order is the
conversation's**: the row sits where the last thing said or done in it puts it,
so clicking an old session adds it to the list without lifting it over the one
you are working in. The row carries a second line under the title: a dot, a word
and what the turn is on. There is no clock: the row says what is happening, and
how long it has been happening is not something a reader acts on from a list —
it was a counter ticking at a row nobody was reading. `thinking` and `running`
pulse in the accent, `needs you` is amber and names what it is waiting for, and
`finished` is green and says `reply ready` until somebody reads it; those three
states stay until they are dealt with, whatever the window says. The heading says
whether anything in the section needs a human before it counts what is running,
and the grip carries the same count when the rail is folded — the one surface
left when the list is out of sight.

**Reading is opening, on every device.** `finished` is not a flag in this
browser: the server compares the newest reply in the log against the session's
read watermark, and opening the session moves the watermark — in any window. So
a reply read on the phone stops being `finished` on the laptop on the next poll,
and a reply that lands in the session already on screen marks itself read,
because somebody is looking at it.

**A view is parked, not dropped, when the reader looks away.** It keeps its
nodes and its socket closes, because an idle session has nothing to listen for
and the next message opens one again. A running view keeps its socket, because
that socket is the turn. A turn that finishes in the background closes its own
and keeps its transcript; its row goes to `finished` until it is read. Clicking
New session parks the session it left rather than closing it, and removing a
session is the one deliberate cancellation. A 15-second sweep retires the views
whose window has closed — that is memory, not membership: the rail's list is the
core's, so a session the core has aged out is gone from Active in every window
whatever this one still holds — and a window of 0 means keep nothing, which
takes effect as the reader leaves rather than at the next sweep, because that is
the whole promise of the smallest value.

**The working set is the account's; this window holds transcripts.** Its views,
sockets, transcripts, scroll and composer are its own, but membership, order and
unread are the core's, so every window and every device draws the same list.
`GET /api/active` is asked every four seconds while the page is in front and not
at all while it is hidden. A session this window holds no view of is a row all
the same: `running · in another window`, or `needs you · approve shell` when a
call is waiting on a person — and clicking it opens that branch from the
projection, the log so far, because the live step and the streaming text belong
to the socket that owns the turn. **The prompt does not.** A blocked call's card
is drawn from `GET /api/branches/{id}/pending`, which serves the tool, the
arguments and the preview the write resolved to, so `needs you` can be answered
in the window the person is actually looking at. The preview is carried and
never recomputed: it is a dispatch to this machine made for that call, and a
second one could describe a different write than the verdict applies to.
Approving opens the branch's socket if this window has none — an approval is the
one frame a session opened from the projection can send — which also makes a
card answerable after a reconnect, and when the verdict lands elsewhere the
buttons are taken away, because a card still offering Approve and Deny for a
decided call is the bug N89 fixed, in a second window. A session this window
holds is folded into its row rather than drawn beside it, and its phase comes
from its frames, which are finer than a four-second snapshot; two answers to one
row is how a rail starts to flicker. Coming back to a hidden window reads both
lists again, so a pin, a removal or a title derived elsewhere lands then.

It is a poll rather than the held request the GPU bar uses because the client's
`/api` proxy forwards each call on a worker thread — a request left open would
pin one thread per window for as long as the window exists, and more windows is
what this feature is for.

The composer's directory, attachments, tool selection and usage are per view,
and a send names the view it belongs to, so switching mid-handshake cannot
deliver the message to the wrong session. A view this window already holds is
not dropped when its session is removed from another window: the listing loses
the row and the transcript on screen stays until the window closes, because a
kept view is the only thing that never asks the server again.

## Past sessions

**The rail** lists the working set first, then the past sessions grouped by the
date it is ordered by. Clicking one opens its transcript — moving it on screen
if it is already open and running — and a session that is not open opens from the
projection. Pinned sessions sit below Active and above the dates — see below.

Grouping is why the list reads as a few short lists rather than a wall: the
server returns up to 50 rows, and a heading whenever the calendar day changes
turns that into Today, Yesterday, and a date each.

**The order is the server's, and the reader chooses the date.** Two are offered
— when the session began, and when it was last active — and the second is the
default, because the session you want is almost always the one you were last
working in. A drawn glyph beside the search field opens the choice; the choice
is remembered per browser, like the rail's width, because it says how this list
is being read rather than a fact about the person reading it. The order is
applied in the query and **before the row cap**, for the same reason the filter
is: sorting the newest fifty in the browser and calling it "recently used" is a
different fifty from the fifty most recently used, and it looks right until an
account outgrows the cap (D61).

"Last activity" is exactly what its name says — the newest of the session's
creation, its newest event, and its closing — and it is the expression the
retention clock already reads (`purgeable`), so "old" and "recently used"
cannot drift apart. It is deliberately **not** "last opened": opening a session
to read it writes nothing, and a list that reordered itself because you looked at
something would move under the pointer. The cost is that reopening a session
does not lift it, while sending a message does.

**A pinned session sits below Active and above the dates.** `Pin to top` is the third item in the
row's `⋮` menu, and `Unpin` replaces it, so the item states the effect and the
current state never has to be inferred from the wording. Pinned rows are drawn as
their own section rather than interleaved with the dates: a date heading cannot
say "first because you pinned it", and a pinned row inside the date groups would
either split a day in two or sit under a heading that contradicts why it is at
the top. The pin is a column (`pinned_at`), not a list in `localStorage`, for the
reason removal is: a pin that holds on the laptop and not the phone is not a pin
— and the order has to reach the server anyway, because the server is where the
cap is. Pinning a session twice keeps the moment it was first pinned, and it is
not a `close` and not a `remove`: the conversation runs on, and the row stays.

**The filter searches what was said, server-side.** It was a substring match
over titles in the browser — and a title is the first few words of the first
message, so a conversation was findable only by how it opened. It also searched
only the newest 50 rows, the same shape of limitation the remove path was
rebuilt to avoid. `?q=` now goes to the server, which matches the user's
messages and the model's replies through a full-text index over the whole
history (D52).

It is **full-text, not fuzzy**: word and prefix matching in any order, not
`fzf`'s scattered subsequence. That was asked for and declined, because no index
can answer a subsequence match — it degrades to scanning every message in every
session on every keystroke. This is what ChatGPT and Claude do too.

Reasoning and tool output are deliberately **not** searched. A tool result is a
file's contents or a directory listing, so indexing it would mean typing a
filename matched every session that had ever listed the directory containing it.

Each hit carries a snippet showing why it matched, with the terms wrapped in
`<<`/`>>` rather than markup — `snippetLine` splits on those and builds
elements, because conversation text is the most untrusted string in the
appliance and a snippet built with `innerHTML` would make a session title an
injection site. Typing is debounced at 160ms so a burst of keystrokes is one
request, and `sessionsPaint` discards an answer a newer request has superseded.

The replay is **the conversation as it happened**, not the model's trimmed
context. It is built from the same `project()` the model path uses, but with
`for_display`: reader-only fields are added (a tool's name, its exit status, the
diff, the scratch paths, the chain of thought) and a compaction is **not**
applied. Compaction replaces the model's history with a summary and a tail, but
the event log keeps every original, so a reopened session shows every prompt and
every result, with a note where the model's view was shortened. Applying the
replacement to the reader was the bug: a reopened session showed only the tail,
the prompts it had folded were gone, and the `system` summary was never drawn at
all (N129). Governance records (tool proposals, approval decisions) are still
deliberately absent — they live in the audit chain, and `neuman-admin audit
search` is where the decisions are.

An assistant turn's chain of thought is carried on the display projection under a
reader-only `reasoning` field — never the provider's `reasoning_content` — and
`replay` draws it as the same thinking row a live turn did. It is reader-only for
the reason the diff is: a session being read back is bound for no provider, and
the model gets its thinking back only in the dialect that asks for it.

A reopened card's **body is deferred**: `replay` builds the row, its `thinking`
preview or tool head, and its outcome, but the arguments, the result, a write's
diff and the full reasoning text are built by `revealDetail` when the row is
first opened — staged live or inline (D60). `rememberScratch` is the one
result-side effect that stays eager, so a later `fs_read` of a scratch path can
still name the call that wrote it even
when that call's card was never opened (D47).

A session is named from its opening message — a whitespace-collapsed truncation
to 60 characters, broken on a word boundary. Derived rather than model-generated:
asking for a title costs a request per session, fails when the gateway is down,
and on a local model pays a cold VRAM load before the first real answer, all to
guess at what you meant. A truncation of what you actually said is honest, free,
and works offline. Hover a row and click `⋮` for **Edit name**, **Pin to top**
(or **Unpin**) and **Remove from list**. Renaming happens in place: Enter
commits, Escape reverts, and blur commits — clicking away from a field you have
just typed into means you meant it, and losing the edit is the one behaviour
nobody expects.

That menu exists because double-click cannot work here: the row opens a session
on `click`, so the first half of a double-click loads the transcript while the
title is being edited, and `stopPropagation` on `dblclick` is too late to stop it
(N53). A row has several actions and one target for them, so the menu names each
— it replaced a bare pencil once there was a second action, because two
unlabelled glyphs a pixel apart are worse than one menu that names both.

**Remove takes a session off your list for good, everywhere.** It calls
`POST /api/sessions/{id}/remove`, which sets `removed_at` and nothing else. The
session, its transcript and its audit chain stay on the appliance for an
operator to purge or keep.

Three properties follow from that being a server fact rather than a browser one:

- **It holds on every device.** The id used to go into `localStorage`, which
  made "removed" true of one browser — removed on the laptop, still there on the
  phone.
- **It frees a slot.** `list_for_account` filters before its `LIMIT`, so
  removing a session reveals the one behind the 50-row cap. Filtering in the
  browser happened *after* the limit, so the list just got shorter.
- **There is no un-remove, and no confirmation.** Deliberate on both counts: a
  user who removes a session is done with it, and the "Show N removed" footer
  this replaces could only restore *everything* at once — it cleared the whole
  stored list rather than toggling. A confirm dialog was tried in between and
  removed: this is a tidying gesture made often, and what the prompt guarded
  was the loss of a *row from a list*, not of the conversation, which stays on
  the appliance either way. A prompt on every cheap action teaches people to
  dismiss prompts.

Removing is **not** closing. `DELETE /api/sessions/{id}` sets `closed_at`, which
means the conversation is over and is what makes a row eligible for scheduled
retention. Tidying a sidebar must not arm a deletion timer on a transcript the
audit chain refers to, so the two stay separate — a session can be removed while
it is still live. Reconnecting to a removed session is refused (4404) so a stale
tab on another device cannot resurrect it.

The menu is positioned `fixed` from the row's own rect and parented to `body`,
because `#sessions-list` scrolls and clips — anchored inside a row it would be
cut off by the panel edge instead of overlapping it. That parenting is also why
the rail's dismissal has to treat a press inside it as a press inside the rail:
it runs on `pointerdown`, so without the exemption, removing a session would
close the overlay you were removing it from before the menu item's own `click`
ever fired.

A hand-chosen name is never overwritten by the derived one. That asymmetry is why
the store has two methods — `set_title` replaces, `title_if_unnamed` only fills a
blank — and it has a test, because the symptom of getting it wrong ("my rename
keeps vanishing") points nowhere near the cause.

The list is fetched on load and repainted after every turn and removal, not
cached across a long life. A second tab may have started a session, and a stale
list is worse than a brief wait.

## Branches

A session may hold several **branches**. A branch is one transcript with its
own working directory, attachments, tool selection, model, reasoning level and
approval mode; switching between them is moving between transcripts, not
opening another rail row. The row counts them (`⑂3`) and the status line's
branch chip names the one on screen and opens the switcher.

A fork is an **edit of a prompt**, made in place. Hovering a turn-opening user
message shows a pencil on the bubble's corner; pressing it turns that bubble
into a textarea. **Send** forks *before* that turn on the server and sends the
edited text into the new branch. The source branch is never rewritten — everything below the fork is
still there when you switch back — and the edited message is the new branch's
first new turn. **Cancel** (or Escape, or a press anywhere outside the bubble)
restores the original bubble. The action
is offered only on a prompt this branch wrote: the inherited prefix belongs to
another branch's history, a steering row does not open a turn, and the first
turn has no completed turn before it to fork at.

A fork point is drawn in the transcript: a centred node on a hairline at the
boundary. For the branch that begins there, its first new prompt sits immediately
below the node, so the transcript does not repeat that prompt as a second lane
title. Pressing the node opens the local tree for that boundary — the parent (the
branch whose turn ended there) and the branches that continue from it, with the
current branch and the path through it marked. The footer branch chip's menu is
the whole session's tree, nested the same way. A child forked at a branch's first
prompt is drawn as a sibling at the branch's own origin, because the two share
every word above it. The footer branch menu's hint names the files and messages
that already happened; the UI does not call a fork a rewind. The branch the rail
opens is the one with the newest activity; there is no branch pointer to go stale
(D83).

The rail still lists **sessions**, because that is the thing a person means by
"my conversations". Removing and closing are session-level; a branch has no
close of its own.

## The working directory

A field above the transcript, per branch. It tells the agent where the work is —
and it is a **hint, not a grant**: the server refuses a path outside what policy
already grants for reading, so typing one cannot widen what the account
reaches. The refusal
is named (`outside_policy`, 403) rather than a generic 400, because the user
picked a real directory and the fix is an operator changing configuration.

Setting it also gives the model something it never had: the *list of paths it may
read at all*. Before this the system prompt said who the agent acts for and which
tools exist, but never where it was — so it had to guess a path or spend a turn
asking, and a wrong guess reads as a broken appliance rather than a missing
sentence.

Read per turn rather than cached, so changing it mid-session takes effect on the
next message — the same way policy does.

It is a **chip in the footer**, shaped like the approval and reasoning chips
beside it: quiet until hovered, and a press on the path opens the same chooser
the folder button in the field opens, so the directory can be changed from where
it is stated. It is hidden entirely when nothing is set, rather than saying "no
working directory" on every new conversation about a default nobody chose.

A deep path is shortened **from the left** before it is drawn, to about 40
characters (`…/web/src`), because the tail is the part that says where you are
and `text-overflow: ellipsis` hides exactly that. The stylesheet keeps the
ellipsis as a backstop for a window narrower than the cap, and the whole path is
on the chip's `title` in both cases. Without the cap a 73-character path took
497px, wrapped the token count onto a second row and doubled the footer's height.

**Clearing it is the chooser's footer, not a ✕ beside the path.** The ✕ was a
destructive target one stray click from the path it destroys, for an act that
belongs where you have already gone on purpose, and it was the only thing on the
status line that was shaped like a second control on one setting. So the path is
now the chip's whole surface, and `Clear working directory` sits at the foot of
the chooser — full-bleed under the rows, behind a hairline, dim at rest and ink
on hover rather than accented, since the chip's accent already means "a
directory is set" and this is the act of unsetting it. It is hidden while
nothing is set, because a control that clears nothing is a dead one.

A footer rather than a row, for two reasons. A row among directories reads as
another directory, and it would have to live inside `#cwd-rows` — a
`role="listbox"` whose children are all `role="option"`, where a button is not
allowed. And it has to be in the same place in both states of the menu, while
the crumb bar's button appears only when there is something to take — the
granted roots have no directory of their own to stand in, and nothing is
highlighted when the menu opens.

That button is the way to take the directory you are standing in without
climbing out to its parent, and since the panels arrived it also takes the row
you have highlighted — the label names which, `use this folder` or `use
contracts`, so the two targets never look alike.

### The environment outline

The chip carries a **border** when the directory has a development environment,
so "my project's own toolchain is live here" is readable without asking. Three
states, from `/api/environment`:

| Border | When | What it means |
|---|---|---|
| `--ok` | the environment resolved | `python` and `shell` run inside it |
| `--edge-warn` | unbuilt, stale, or garbage-collected | a project whose environment needs the user to re-enter the directory |
| `--bad` | the cache was not written by nix | reported rather than used |

A directory with **no** environment gets no border at all. Most directories are
not projects — they hold the data a script is pointed at — and a folder of DICOM
files is not in a degraded state for failing to be a flake (N85). Marking every
one of them would make the outline mean nothing by being everywhere.

Four resolver states collapse into one amber because they differ in cause and
not in remedy: `env_unrealised`, `env_stale` and `env_collected` are all fixed by
the human re-entering the directory, and distinguishing them on screen would be
three colours for one action. `env_untrusted` is separate because it is not a
rebuild — the tool refuses that cache, and the answer is to report it.

**The border, not the text.** The path is eleven-pixel monospace and has to stay
readable as a path; recolouring it to carry a status is how a path stops being
one. And the colour is never the only carrier — `paintEnvironment` writes the
same sentence into the chip's `title` and its accessible name, because a hue is
not available to everyone reading it.

No sixth hue was added. The palette documents five and says a sixth would need a
sixth meaning; these three are success, "careful and reversible", and failure,
which are three of the five.

**It is never stored.** The state is a fact about the user's disk at this moment
— a `nix-collect-garbage` turns a resolved environment into `env_collected`
without the directory moving — so nothing persists it and the browser re-probes
on every change, guarded by a generation counter so two quick choices cannot let
the first answer land last. A probe that fails draws nothing: an unprobed
directory and one with no environment look the same, which is right, because
neither is a state to act on.

Behind it is a dispatch, not a read. A `.direnv` cache lives in the user's own
home, so the server cannot see it — the same reason the chooser lists
directories by dispatching `fs_list` (D33).

## The context card

Three layers, which is the whole of "what will this turn carry":

- **The attached files**, as chips *inside* the field and above the text they
  qualify. They were a bar of their own with the panel's background, which said
  the files belonged to the window; a chip on the field says they belong to the
  message.
- **The message**, with the two controls that add context to it — attach and the
  working directory — on the same field.
- **The footer**: what is standing on the left (approval, working directory) and
  what the last turn cost on the right. Two kinds of fact, two ends of one line,
  with the right-hand group — the token count and its gauge, then the tools chip
  — taking an auto margin rather than a spacer element between two groups.

The footer paints no background of its own. `--panel` there over the card's own
`--panel` was invisible until the composer's darker field landed between them —
then the two equal panels read as a separate band below the box rather than as
the bottom of one card.

## Tool approval

A proposal renders the tool's name and a one-line summary of the call above
Approve and Deny, with the arguments as named fields — and a write's content as
code — on the same card. The summary is a convenience and never the only thing
available: what a person approves must be inspectable in full, which is why the
whole call is on the card. The raw JSON disclosure that used to sit under the
fields restated what they already showed and was removed; the diff arrives in
its place when a write is approved.

**A mutating write shows its diff instead**, replacing the arguments when the
approval frame arrives. The server resolves the write read-only first and sends
the patch with the frame, so what is on screen is the change that will happen
rather than the request that asked for it (N97).

The same card is drawn by a window that did not start the turn: the diff and the
arguments are served from the waiting call itself, and Approve/Deny there send
the verdict into the branch's inbox on a socket this window opens for it (D63).
The preview is never re-derived — a second dispatch would describe a write made
at a different moment.

There is no "Approve this call?" label. Two buttons reading Approve and Deny
already ask the question, and the row reads faster without the restatement.

Whether a proposal appears at all is policy, not client behaviour —
`auto_accept` decides, and `never` is what the demonstration image sets so the
approval is visible.

## Tests

`checks.vm-iso` covers what matters here: that the client is served, that it
does **not** shadow the API — a mount at `/` matches
everything, so serving HTML in response to `GET /api/me` is a total API failure
that looks like a working web server), and that the login handshake works —
including that a cookie without a CSRF token is refused.

There is no browser test of the JavaScript. What would be worth asserting
behaviourally — the handshake and the protocol mapping — is better checked
against the real server than against a mock of it; `checks.vm-iso` covers the
served page and the login handshake.

What *is* checked is the client's own structure, from the source, in
`neuman-client/tests/test_web.py` — because a rewrite is the expected fate of
this file and the properties a rewrite drops are the ones nothing else notices:
that no `$("id")` names an element that does not exist, that every top-level
function has a unique name (the file has one global scope, and a redeclared
function wins silently), that every card a control opens is in `CARDS` (so
Escape and `aria-expanded` reach it) while the settings modal is dismissed by
its scrim instead, that Settings has exactly one opener, that typing is routed
to the composer without stealing a field's keys, a keyboard command, or a
modal's, and that the approval picker offers exactly the modes the server has.
They are source assertions rather than browser tests, which is a real
limitation — none of them can see what the page looks like.
